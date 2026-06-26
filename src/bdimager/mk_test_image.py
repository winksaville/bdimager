"""Build / verify a small synthetic Pi-like `.img` for testing the
builder's offline inject + finalize — rootless, stdlib + imglib only.

A real mother image is multi-GB and only exists after a card capture.
To exercise the inject + finalize passes on their own, this helper makes
a ~160 MB image with the same shape as a Pi card — a FAT boot partition
plus an ext4 root — seeded with a few generic files: a survivor that
must persist across build, and a stale file an inject can overwrite.
`verify` reports the finalize outcome (e2fsck clean, shrunk, xz valid);
inject-specific assertions live in the test that knows what it injected.

Like the build itself, it needs no root: `sfdisk` writes the table,
`mke2fs` + `debugfs` build and seed a standalone ext4 root that is then
spliced into the image, and `mtools` formats + seeds the FAT boot via
its `image@@offset` syntax.

    python3 -m bdimager.mk_test_image build images/mother.img
    bd-build --mother images/mother.img --output images/deploy.img \\
        --shrink --compress
    python3 -m bdimager.mk_test_image verify images/deploy.img
"""

import argparse
import subprocess
import tempfile
from pathlib import Path

from . import imglib

TOTAL_BYTES = 160 * 1024 * 1024  # 160 MB: FAT32 boot (~47 MB) + ext4 root

# DOS table: ~47 MB FAT boot (type c) then the rest ext4 (type 83),
# matching a Pi card's p1/p2 layout.
SFDISK_SCRIPT = """label: dos
unit: sectors
start=2048, size=96256, type=c
type=83
"""

# Generic files seeded on the ext4 root. Paths are absolute within the
# filesystem.
#
# - the survivor must persist unchanged across a build (a sanity anchor
#   that build never clobbers untouched files),
# - the stale file gives an inject test an overwrite target — its content
#   must change when `--add <host>:/opt/app/config.txt` runs.
SURVIVOR_PATH = "/etc/hostname"
SURVIVOR_CONTENT = "bdimager-test\n"
STALE_PATH = "/opt/app/config.txt"
STALE_CONTENT = "OLD STALE CONFIG\n"

SEED_ROOT = {
    SURVIVOR_PATH: SURVIVOR_CONTENT,
    STALE_PATH: STALE_CONTENT,
    "/etc/os-release": 'NAME="bdimager-test"\nID=test\n',
    # Some bulk content so the pre-shrink rootfs is non-trivial and the
    # finalize shrink visibly reclaims space.
    "/var/lib/data/blob.txt": "filler line for the test rootfs\n" * 200,
}

# Token files written to the FAT boot partition.
SEED_BOOT = {
    "config.txt": "dtoverlay=uart0-pi5\n",
    "cmdline.txt": "console=serial0,115200 root=/dev/mmcblk0p2\n",
}


def pick_boot_root(img: Path) -> tuple[dict, dict]:
    """Return (boot, root) partition dicts by MBR type (FAT 'c', ext '83').

    Used at build time, before any filesystem exists to probe — so it
    keys off the partition table type, not a filesystem magic.
    """
    fat = {"1", "4", "6", "b", "c", "e", "ef"}
    parts = imglib.partitions(img)
    boot = next(p for p in parts if p["type"].lower().lstrip("0") in fat)
    root = next(p for p in parts if p["type"] == "83")
    return boot, root


def seed_root(root_fs: Path) -> None:
    """mke2fs has run on `root_fs`; seed it with the SEED_ROOT files.

    Stages each file's content to a host temp file, then drives one
    `debugfs -w` batch of mkdir + write commands. debugfs mkdir is not
    recursive, so every ancestor directory is created, shallowest first.
    """
    dirs: set[str] = set()
    for path in SEED_ROOT:
        parent = Path(path).parent
        while str(parent) != "/":
            dirs.add(str(parent))
            parent = parent.parent
    ordered = sorted(dirs, key=lambda d: d.count("/"))

    with tempfile.TemporaryDirectory(prefix="mk-seed-") as tmp:
        cmds = [f"mkdir {d}" for d in ordered]
        for i, (path, content) in enumerate(SEED_ROOT.items()):
            host = Path(tmp) / f"seed{i}"
            host.write_text(content)
            cmds.append(f"write {host} {path}")
        imglib.debugfs_batch(root_fs, cmds)


def build(img: Path) -> int:
    """Create a seeded synthetic Pi-like image at `img` (overwrites)."""
    img.parent.mkdir(parents=True, exist_ok=True)
    with open(img, "wb") as f:
        f.truncate(TOTAL_BYTES)
    imglib.run(["sfdisk", str(img)], stdin=SFDISK_SCRIPT, capture=True)

    boot, root = pick_boot_root(img)

    # Build + seed a standalone ext4 root, then splice it into the image.
    root_fs = Path(tempfile.mkstemp(prefix="mk-root-", suffix=".ext4")[1])
    try:
        with open(root_fs, "wb") as f:
            f.truncate(root["length"])
        imglib.run(["mke2fs", "-F", "-q", "-t", "ext4", str(root_fs)], capture=True)
        seed_root(root_fs)
        imglib.splice_region(img, root["off"], root_fs)
    finally:
        root_fs.unlink(missing_ok=True)

    # Format + seed the FAT boot partition in place via mtools @@offset.
    selector = imglib.fat_at(img, boot["off"])
    imglib.run(["mformat", "-i", selector, "-F", "::"], capture=True)
    with tempfile.TemporaryDirectory(prefix="mk-boot-") as tmp:
        for name, content in SEED_BOOT.items():
            host = Path(tmp) / name
            host.write_text(content)
            imglib.run(
                ["mcopy", "-i", selector, "-o", str(host), f"::{name}"], capture=True
            )

    print(
        f"mk_test_image: built seeded image {img} ({TOTAL_BYTES // (1024 * 1024)} MB)"
    )
    return 0


def _entry(root_fs: Path, path: str) -> dict | None:
    """Return the directory entry for `path`, or None if it's absent."""
    parent, _, name = path.rpartition("/")
    for e in imglib.fs_list(root_fs, parent or "/"):
        if e["name"] == name:
            return e
    return None


def verify(img: Path) -> int:
    """Extract `img`'s root and report finalize outcome; 0 if all clean.

    Checks the generic build outcome — survivor preserved, rootfs clean,
    shrunk smaller than the synthetic mother, and an xz that passes its
    integrity check. Inject-specific assertions are the caller's job
    (it knows what it added), via imglib.fs_read / fs_exists.
    """
    _boot, root, _parts = imglib.find_boot_root(img)
    if root is None:
        raise SystemExit(f"mk_test_image: no ext root partition in {img}")

    root_fs = imglib.extract_region(img, root["off"], root["length"])
    failures = []
    try:
        rc = imglib.e2fsck(root_fs, fix=False)
        print(f"e2fsck: exit {rc} ({'clean' if rc == 0 else 'errors'})")
        if rc != 0:
            failures.append("e2fsck")

        print("\nverify build:")
        # Survivor preserved unchanged.
        survivor = imglib.fs_read(root_fs, SURVIVOR_PATH)
        ok = survivor == SURVIVOR_CONTENT
        print(f"  {'PASS' if ok else 'FAIL'} survivor {SURVIVOR_PATH}")
        if not ok:
            failures.append(SURVIVOR_PATH)

        print("\nverify finalization:")
        # shrink: the deploy image is smaller than the synthetic mother.
        img_size = img.stat().st_size
        ok = img_size < TOTAL_BYTES
        print(
            f"  {'PASS' if ok else 'FAIL'} shrink image {img_size} B < mother {TOTAL_BYTES} B"
        )
        if not ok:
            failures.append("shrink")
        # xz: the compressed image exists and passes its integrity check.
        xz = Path(f"{img}.xz")
        ok = (
            xz.exists()
            and subprocess.run(["xz", "-t", str(xz)], capture_output=True).returncode
            == 0
        )
        print(
            f"  {'PASS' if ok else 'FAIL'} xz     {xz} ({'ok' if xz.exists() else 'missing'})"
        )
        if not ok:
            failures.append(str(xz))
    finally:
        root_fs.unlink(missing_ok=True)

    if failures:
        print(f"\nmk_test_image: {len(failures)} check(s) FAILED")
        return 1
    print("\nmk_test_image: all checks passed")
    return 0


def verify_expanded(img: Path) -> int:
    """Check an expanded card: root partition + rootfs grew to fill it.

    Asserts the write app's expand worked — the last partition reaches
    (within a sector of) the end of the card, the ext rootfs fills that
    partition, e2fsck is clean, and the survivor file lived through the
    grow.
    """
    _boot, root, _parts = imglib.find_boot_root(img)
    if root is None:
        raise SystemExit(f"mk_test_image: no ext root partition in {img}")
    img_size = img.stat().st_size
    sector = imglib.sector_size(img)
    failures = []

    print("verify expand:")
    root_end = root["off"] + root["length"]
    ok = root_end >= img_size - sector
    print(f"  {'PASS' if ok else 'FAIL'} partition fills card "
          f"(root end {root_end} B vs card {img_size} B)")
    if not ok:
        failures.append("partition-fill")

    root_fs = imglib.extract_region(img, root["off"], root["length"])
    try:
        rc = imglib.e2fsck(root_fs, fix=False)
        ok = rc == 0
        print(f"  {'PASS' if ok else 'FAIL'} e2fsck clean (exit {rc})")
        if not ok:
            failures.append("e2fsck")
        fs_size = imglib.ext_size(root_fs)
        ok = fs_size >= root["length"] - 1024 * 1024
        print(f"  {'PASS' if ok else 'FAIL'} rootfs fills partition "
              f"(fs {fs_size} B vs part {root['length']} B)")
        if not ok:
            failures.append("fs-fill")
        ok = imglib.fs_read(root_fs, SURVIVOR_PATH) == SURVIVOR_CONTENT
        print(f"  {'PASS' if ok else 'FAIL'} survivor survived ({SURVIVOR_PATH})")
        if not ok:
            failures.append("survivor")
    finally:
        root_fs.unlink(missing_ok=True)

    if failures:
        print(f"\nmk_test_image: expand verify — {len(failures)} check(s) FAILED")
        return 1
    print("\nmk_test_image: expand verify all passed")
    return 0


def main() -> int:
    """Parse the build/verify/verify-expanded subcommand and dispatch (rootless)."""
    p = argparse.ArgumentParser(
        prog="mk_test_image", description=__doc__.splitlines()[0]
    )
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("build", "verify", "verify-expanded"):
        sub.add_parser(name).add_argument("img", type=Path)
    args = p.parse_args()
    if args.cmd == "build":
        return build(args.img)
    if args.cmd == "verify":
        return verify(args.img)
    return verify_expanded(args.img)


if __name__ == "__main__":
    raise SystemExit(main())
