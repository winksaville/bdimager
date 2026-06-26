"""Build a deployable image from a mother image — the build app.

`build` is one of the three image-builder apps (`build`, `capture`,
`write`) and the fully *rootless* one (inject + finalize): it imports
only `imglib` (the rootless storage toolkit) and `imgcfg` (shared
config), never the device layer — so "build never touches a physical
device" holds structurally, not just by convention.

Given a mother image (produced by `capture`, or any existing `.img`),
`build` copies it to the output and edits the output's ext4 root in the
file with `debugfs` — no loop-mount, no root:

- transform: copy mother -> output (the mother stays pristine),
- inject: copy caller-specified host files / directories into the
  image rootfs (see Injection below),
- finalize: shrink the rootfs to its minimum, then xz the image.

Settings come from `bd-config.toml`'s `[image]` (shared artifact paths)
and `[build]` (shrink / compress); see `imgcfg` for the config / env /
CLI precedence. `--dry-run` resolves and prints the plan, touching
nothing.

Injection (`--add`):

- each `--add SRC<sep>DEST` copies host path `SRC` (a file or a
  directory, recursively) to absolute `DEST` inside the image rootfs,
- the separator is `:` or `->`; per entry pick whichever your paths
  don't contain (`./a.conf:/etc/a.conf`, or `'./a->/etc/wei:rd'` when
  the dest carries a colon). `DEST` must be absolute; an entry that
  parses validly under *both* separators is a hard error,
- a `DEST` that already exists is overwritten (its mode / uid / gid are
  preserved); overwrites are listed in the plan,
- the same `--add` arguments can live in a `@file` (one `--add SRC:DEST`
  per line, `#` comments allowed); `build @adds.txt`.

    # show the resolved plan, change nothing
    bd-build --dry-run

    # build a deployable image, injecting a config and a directory
    bd-build --mother images/mother.img --output images/deploy.img \\
        --add ./app.conf:/etc/app.conf --add ./assets:/opt/app/assets
"""

import argparse
import os
import shlex
import shutil
import tempfile
from pathlib import Path

from . import imgcfg, imglib

# bd-config.toml tables this app reads: shared artifact paths + the
# build-specific knobs.
SECTIONS = ("image", "build")

# Built-in fallback used only when bd-config.toml omits a key, so the
# tool still resolves a complete plan against a stripped config. These
# mirror the committed [image] + [build] defaults.
DEFAULTS: dict[str, object] = {
    "images_dir": "images",
    "mother": "mother.img",
    "output": "deploy.img",
    "shrink": True,
    "compress": True,
}

# Path-valued settings (free-form strings) vs. on/off toggles. The split
# drives both the argparse wiring and the BD_* env coercion. Injection
# (`--add`) is a repeatable list, handled outside this config machinery.
STR_KEYS = ("images_dir", "mother", "output")
BOOL_KEYS = ("shrink", "compress")


class _ArgParser(argparse.ArgumentParser):
    """ArgumentParser whose `@file` lines may carry a full `--flag value`.

    argparse's default reads one argument per `@file` line; overriding
    `convert_arg_line_to_args` lets a line read `--add SRC:DEST`
    (shlex-split, so quoted paths survive) and lets `#` comments and
    blank lines be skipped.
    """

    def convert_arg_line_to_args(self, line: str):
        """Split one `@file` line into args; drop blanks and `#` comments."""
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            return []
        return shlex.split(stripped)


def parse_add(entry: str) -> tuple[Path, str]:
    """Parse one `--add` value into (host_src, image_dest).

    The separator is `:` or `->`; `DEST` must be absolute. Disambiguation
    keys off that absolute-dest rule: an entry is split by each separator
    it contains, the result kept only if `DEST` is absolute and both
    sides are non-empty. Exactly one valid split wins (so a colon inside a
    `->`-separated dest is fine); zero is a format error; two distinct
    valid splits are ambiguous and rejected.
    """
    candidates: list[tuple[Path, str]] = []
    seen: set[tuple[str, str]] = set()
    for sep in ("->", ":"):
        if sep not in entry:
            continue
        src, dest = entry.split(sep, 1)
        if src and dest and dest.startswith("/") and (src, dest) not in seen:
            seen.add((src, dest))
            candidates.append((Path(src), dest))
    if not candidates:
        raise SystemExit(
            f"build: --add {entry!r} is not SRC:DEST or SRC->DEST with an "
            "absolute DEST")
    if len(candidates) > 1:
        raise SystemExit(
            f"build: --add {entry!r} is ambiguous — it parses under both "
            "':' and '->'; use the separator your paths don't contain")
    return candidates[0]


def _file_injections(src: Path, dest: str) -> list[tuple[Path, str]]:
    """Expand one (src, dest) into concrete (host_file, image_path) pairs.

    - a file maps to `dest` directly,
    - a directory maps each regular file under it to `dest/<relpath>`,
      mirroring the tree.
    """
    if src.is_file():
        return [(src, dest)]
    if src.is_dir():
        out = []
        base = dest.rstrip("/")
        for f in sorted(src.rglob("*")):
            if f.is_file():
                rel = f.relative_to(src).as_posix()
                out.append((f, f"{base}/{rel}"))
        return out
    raise SystemExit(f"build: --add source not found: {src}")


def _entry(root_fs: Path, path: str) -> dict | None:
    """Return the directory entry (mode/uid/gid/...) for `path`, or None."""
    parent, _, name = path.rpartition("/")
    for e in imglib.fs_list(root_fs, parent or "/"):
        if e["name"] == name:
            return e
    return None


def _ancestors(path: str) -> list[str]:
    """Return `path`'s ancestor directories, shallowest first (excl. '/')."""
    parts = path.strip("/").split("/")[:-1]  # drop the filename
    out, cur = [], ""
    for p in parts:
        cur = f"{cur}/{p}"
        out.append(cur)
    return out


def plan_injections(root_fs: Path, adds: list[tuple[Path, str]]) -> tuple[list[str], list[str]]:
    """Build the debugfs commands for the injections; return (cmds, overwrites).

    - missing ancestor directories are created (shallowest first),
    - each file is written and re-stamped: an overwrite preserves the
      target's mode / uid / gid; a fresh file takes the source's mode and
      root:root ownership,
    - `overwrites` lists the image paths that already existed (surfaced in
      the plan).
    """
    pairs: list[tuple[Path, str]] = []
    for src, dest in adds:
        pairs += _file_injections(src, dest)

    need_dirs: list[str] = []
    seen_dirs: set[str] = set()
    for _host, image_path in pairs:
        for d in _ancestors(image_path):
            if d not in seen_dirs:
                seen_dirs.add(d)
                need_dirs.append(d)

    cmds: list[str] = []
    for d in need_dirs:
        if not imglib.fs_exists(root_fs, d):
            cmds.append(f"mkdir {d}")

    overwrites: list[str] = []
    for host, image_path in pairs:
        entry = _entry(root_fs, image_path)
        if entry is not None:
            overwrites.append(image_path)
            mode, uid, gid = entry["mode"], entry["uid"], entry["gid"]
            cmds.append(f"rm {image_path}")
        else:
            mode = host.stat().st_mode & 0o7777 | 0o100000
            uid = gid = 0
        cmds.append(f"write {host} {image_path}")
        cmds.append(f"sif {image_path} mode 0{mode:o}")
        cmds.append(f"sif {image_path} uid {uid}")
        cmds.append(f"sif {image_path} gid {gid}")
    return cmds, overwrites


def print_plan(config_path: Path, plan: dict, adds: list[tuple[Path, str]]) -> None:
    """Print the resolved settings and the stages the build would run."""
    imgcfg.print_settings(config_path, plan, STR_KEYS, BOOL_KEYS, "build")

    def val(key: str):
        return plan[key][0]

    mother, output = imgcfg.img_path(plan, "mother"), imgcfg.img_path(plan, "output")
    print("")
    print("stages:")
    print(f"  1. transform copy {mother} -> {output}")
    if adds:
        print("  2. inject")
        for src, dest in adds:
            print(f"       {src} -> {dest}")
    else:
        print("  2. inject    (none)")
    finalize_steps = []
    if val("shrink"):
        finalize_steps.append("shrink rootfs")
    if val("compress"):
        finalize_steps.append(f"xz -> {output}.xz")
    print(f"  3. finalize  {', '.join(finalize_steps) if finalize_steps else '(none)'}")


def inject_root(root_fs: Path, adds: list[tuple[Path, str]]) -> None:
    """Copy the caller's host files / directories into the extracted root.

    - `adds` is the parsed `--add` list; an empty list is a no-op,
    - `root_fs` is a standalone partition image (offset 0), so debugfs
      addresses it directly.
    """
    if not adds:
        return
    cmds, overwrites = plan_injections(root_fs, adds)
    imglib.debugfs_batch(root_fs, cmds)
    print(f"  inject: {len(adds)} source(s); {len(overwrites)} overwrite(s)")
    for path in overwrites:
        print(f"    overwrote {path}")


def edit_root(img: Path, adds: list[tuple[Path, str]]) -> None:
    """Extract `img`'s ext4 root, inject into it, splice it back.

    - the root partition is located by filesystem probe, copied out to a
      standalone temp file so `debugfs` can edit it, then written back at
      the same offset,
    - the temp file is always removed, even on failure.
    """
    boot, root, _parts = imglib.find_boot_root(img)
    if root is None:
        raise SystemExit(f"build: no ext root partition found in {img}")
    print(f"\nedit: root@{root['off']} ({root['length']} B), "
          f"boot={'@' + str(boot['off']) if boot else '(none)'}")

    root_fs = imglib.extract_region(img, root["off"], root["length"])
    try:
        inject_root(root_fs, adds)
        imglib.splice_region(img, root["off"], root_fs)
    finally:
        root_fs.unlink(missing_ok=True)


def finalize(img: Path, plan: dict) -> None:
    """Shrink the root partition to minimum and/or xz-compress the output.

    - shrink (rootless): re-extract the ext root, `resize2fs -M` it, splice the
      smaller fs back, shrink its partition-table entry, and truncate the image
      so the deploy `.img` is no longer the mother's full size,
    - compress: `xz` the (shrunk) image to `<output>.xz`, keeping the `.img` so
      it stays directly writable,
    - the shrunk rootfs sits at its minimum (no free space) by design; growing
      it to the target device is the write app's job.
    """
    def val(key: str):
        return plan[key][0]

    if val("shrink"):
        _boot, root, parts = imglib.find_boot_root(img)
        if root is None:
            raise SystemExit(f"build: no ext root partition in {img}")
        partnum = parts.index(root) + 1
        print(f"\nshrink: root partition #{partnum} @ {root['off']}")
        root_fs = imglib.extract_region(img, root["off"], root["length"])
        try:
            new_size = imglib.ext_min_resize(root_fs)
            os.truncate(root_fs, new_size)
            imglib.splice_region(img, root["off"], root_fs)
        finally:
            root_fs.unlink(missing_ok=True)
        imglib.set_part_size(img, partnum, new_size // imglib.sector_size(img))
        os.truncate(img, root["off"] + new_size)
        print(f"  shrink: root -> {new_size} B; image -> {root['off'] + new_size} B")

    if val("compress"):
        print(f"\ncompress: xz -k {img} -> {img}.xz")
        imglib.run(["xz", "-k", "-T0", "-f", str(img)], capture=True)


def run_build(plan: dict, adds: list[tuple[Path, str]]) -> int:
    """Execute the build stages: transform + inject + finalize.

    - copies the mother to the output (relative paths sit under
      `images_dir`) so the mother stays pristine, then edits the copy's
      ext4 root in place, then finalize (shrink + xz),
    - capture (device -> mother) and write (output -> device) are separate
      apps; build assumes the mother already exists.
    """
    mother = imgcfg.img_path(plan, "mother")
    output = imgcfg.img_path(plan, "output")
    if not mother.exists():
        raise SystemExit(
            f"build: mother image not found: {mother} "
            "(run bd-capture or point --mother at an existing .img)")

    print(f"\ntransform: copy {mother} -> {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(mother, output)
    edit_root(output, adds)
    finalize(output, plan)

    print("\nbuild: transform + inject + finalize complete.")
    return 0


def parse_args() -> argparse.Namespace:
    """Build the parser: --config/--dry-run/--add plus a flag per config key."""
    p = _ArgParser(
        prog="bd-build",
        description=__doc__.splitlines()[0],
        fromfile_prefix_chars="@",
    )
    p.add_argument(
        "--config", type=Path, default=imgcfg.DEFAULT_CONFIG,
        help=f"bd-config.toml to read [image]/[build] from (default: {imgcfg.DEFAULT_CONFIG})",
    )
    p.add_argument(
        "--dry-run", action="store_true", dest="dry_run",
        help="resolve and print the plan, mutate nothing",
    )
    p.add_argument(
        "--add", action="append", default=None, metavar="SRC:DEST",
        help="inject host SRC (file/dir) to absolute image DEST "
             "(separator ':' or '->'); repeatable; also via @file",
    )
    imgcfg.add_arguments(p, STR_KEYS, BOOL_KEYS)
    return p.parse_args()


def main() -> int:
    """Resolve the plan, print it, then run the build unless --dry-run."""
    args = parse_args()
    file_cfg = imgcfg.load_config(args.config, SECTIONS, DEFAULTS)
    plan = imgcfg.resolve(args, file_cfg, STR_KEYS, BOOL_KEYS)
    adds = [parse_add(e) for e in (args.add or [])]
    print_plan(args.config, plan, adds)

    if args.dry_run:
        return 0
    return run_build(plan, adds)


if __name__ == "__main__":
    raise SystemExit(main())
