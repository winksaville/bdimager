"""Rootless host-side helpers for the block-device image builder — edit
the partitions of a `.img` without losetup / mount / sudo.

Mounting a `.img` needs root (loop device + mount are privileged), so
the builder edits filesystems *in the file* instead:

- partitions are located by probing the filesystem magic at each
  partition's byte offset (read from the MBR via `sfdisk -J`), not by
  trusting the MBR type code,
- the ext4 root is edited with `debugfs`, which has no offset option —
  so the root partition is extracted to a standalone temp file (a
  filesystem at offset 0), edited, and spliced back byte-for-byte,
- the FAT boot partition is edited with `mtools`, which addresses a
  filesystem inside an image natively via its `image@@offset` syntax.

None of this needs root; only the physical-device `dd` of capture/write
does.
"""

import json
import os
import subprocess
import tempfile
from pathlib import Path

# ext4 superblock magic (0xEF53, little-endian) sits 0x438 bytes into a
# partition; a FAT boot sector ends with 0x55AA at offset 0x1FE. Reading
# these two locations identifies a partition's filesystem without root.
_EXT_MAGIC_OFF = 0x438
_EXT_MAGIC = b"\x53\xef"
_FAT_SIG_OFF = 0x1FE
_FAT_SIG = b"\x55\xaa"

_CHUNK = 1 << 20  # 1 MiB copy buffer for extract/splice


def run(cmd: list, *, stdin: str | None = None, capture: bool = False,
        echo: bool = True, check: bool = True) -> subprocess.CompletedProcess:
    """Run a command; echo it and raise on failure by default.

    - `stdin` feeds text to the process (used to script `sfdisk`),
    - `capture` returns stdout/stderr as text for callers that parse it,
    - `echo=False` silences the `$ cmd` line (read-only queries — lsblk,
      mdir, e2fsck — that shouldn't clutter a build log),
    - `check=False` returns a non-zero exit instead of raising (tools like
      e2fsck whose exit 1 means "fixed", not "failed").
    """
    if echo:
        print("  $", " ".join(str(c) for c in cmd))
    return subprocess.run(cmd, input=stdin, check=check, text=True, capture_output=capture)


def partitions(img: Path) -> list[dict]:
    """Return the image's partitions as dicts with byte offset and length.

    - reads the MBR/GPT table via `sfdisk -J` (rootless),
    - each dict carries node, raw type, byte `off`, and byte `length`.
    """
    data = json.loads(run(["sfdisk", "-J", str(img)], capture=True).stdout)
    table = data["partitiontable"]
    sector = table.get("sectorsize", 512)
    out = []
    for p in table.get("partitions", []):
        out.append({
            "node": p.get("node"),
            "type": str(p.get("type", "")),
            "off": p["start"] * sector,
            "length": p["size"] * sector,
        })
    return out


def probe_fstype(img: Path, off: int) -> str | None:
    """Identify the filesystem at byte `off` as 'ext', 'vfat', or None."""
    with open(img, "rb") as f:
        f.seek(off + _EXT_MAGIC_OFF)
        if f.read(2) == _EXT_MAGIC:
            return "ext"
        f.seek(off + _FAT_SIG_OFF)
        if f.read(2) == _FAT_SIG:
            return "vfat"
    return None


def find_boot_root(img: Path) -> tuple[dict | None, dict | None, list[dict]]:
    """Return (boot_part, root_part, all_parts), each found by fs probe.

    - boot is the first FAT partition, root the first ext partition,
    - each returned partition dict gains a `fstype` key.
    """
    parts = partitions(img)
    boot = root = None
    for p in parts:
        p["fstype"] = probe_fstype(img, p["off"])
        if p["fstype"] == "ext" and root is None:
            root = p
        elif p["fstype"] == "vfat" and boot is None:
            boot = p
    return boot, root, parts


def extract_region(img: Path, off: int, length: int) -> Path:
    """Copy `length` bytes at `off` out to a temp file; return its path.

    The result is a standalone filesystem image (offset 0) that `debugfs`
    can open directly. Caller is responsible for deleting it.
    """
    fd, name = tempfile.mkstemp(prefix="bd-part-", suffix=".fs")
    os.close(fd)
    dst_path = Path(name)
    with open(img, "rb") as src, open(dst_path, "wb") as dst:
        src.seek(off)
        remaining = length
        while remaining:
            chunk = src.read(min(_CHUNK, remaining))
            if not chunk:
                break
            dst.write(chunk)
            remaining -= len(chunk)
    return dst_path


def splice_region(img: Path, off: int, src: Path) -> None:
    """Write `src` back into `img` starting at byte `off` (in place).

    The source is an extracted partition edited in place, so its length
    is unchanged and this overwrites exactly the original region.
    """
    with open(src, "rb") as s, open(img, "r+b") as d:
        d.seek(off)
        while True:
            chunk = s.read(_CHUNK)
            if not chunk:
                break
            d.write(chunk)


def debugfs_batch(fs_file: Path, commands: list[str]) -> None:
    """Run a list of debugfs commands against `fs_file` in one -w session.

    - no-op for an empty list,
    - commands run in order, so a `rm` then `write` then `sif` sequence
      (the truncate-in-place idiom) behaves as written.
    """
    if not commands:
        return
    fd, cmdfile = tempfile.mkstemp(prefix="bd-debugfs-", suffix=".txt")
    try:
        with os.fdopen(fd, "w") as f:
            f.write("\n".join(commands) + "\n")
        run(["debugfs", "-w", "-f", cmdfile, str(fs_file)], capture=True)
    finally:
        os.unlink(cmdfile)


def fs_list(fs_file: Path, path: str) -> list[dict]:
    """List one ext directory via `debugfs ls -l -p`; [] if absent.

    Each entry dict carries name, inode, mode (int), uid, gid, size, and
    the full child path. `.` / `..` are dropped.
    """
    cp = subprocess.run(
        ["debugfs", "-R", f"ls -l -p {path}", str(fs_file)],
        capture_output=True, text=True,
    )
    entries = []
    for line in cp.stdout.splitlines():
        fields = line.split("/")
        # Parseable rows look like: /inode/mode/uid/gid/name/size/
        if len(fields) < 7:
            continue
        name = fields[5]
        if name in ("", ".", ".."):
            continue
        entries.append({
            "name": name,
            "inode": int(fields[1]),
            "mode": int(fields[2], 8) if fields[2] else 0,
            "uid": int(fields[3]) if fields[3] else 0,
            "gid": int(fields[4]) if fields[4] else 0,
            "size": int(fields[6]) if fields[6] else 0,
            "path": path.rstrip("/") + "/" + name,
        })
    return entries


def fs_walk_files(fs_file: Path, start: str):
    """Yield every regular-file entry under `start` (recursive).

    - descends into subdirectories; symlinks and special files are
      skipped so a truncate never follows a link out of the tree.
    """
    stack = [start]
    while stack:
        current = stack.pop()
        for entry in fs_list(fs_file, current):
            kind = entry["mode"] & 0o170000
            if kind == 0o040000:
                stack.append(entry["path"])
            elif kind == 0o100000:
                yield entry


def fs_exists(fs_file: Path, path: str) -> bool:
    """Return True if `path` exists in the ext filesystem `fs_file`."""
    cp = subprocess.run(
        ["debugfs", "-R", f"stat {path}", str(fs_file)],
        capture_output=True, text=True,
    )
    return "File not found" not in (cp.stdout + cp.stderr)


def fs_read(fs_file: Path, path: str) -> str | None:
    """Return the text contents of `path` in ext fs `fs_file`, or None.

    - dumps the file out with `debugfs dump` (rootless) and reads it back;
      None means the file is absent, distinct from an empty file (""),
    - used for the read-modify-write of text content an edit changes in
      place rather than overwriting.
    """
    fd, tmp = tempfile.mkstemp(prefix="bd-read-", suffix=".dump")
    os.close(fd)
    try:
        cp = subprocess.run(
            ["debugfs", "-R", f"dump {path} {tmp}", str(fs_file)],
            capture_output=True, text=True,
        )
        if "File not found" in (cp.stdout + cp.stderr):
            return None
        return Path(tmp).read_text()
    finally:
        os.unlink(tmp)


def sector_size(img: Path) -> int:
    """Return the image's logical sector size in bytes (from `sfdisk -J`)."""
    data = json.loads(run(["sfdisk", "-J", str(img)], capture=True).stdout)
    return data["partitiontable"].get("sectorsize", 512)


def set_part_size(img: Path, partnum: int, size_sectors: int) -> None:
    """Resize partition `partnum` (1-based) to `size_sectors`, keeping its start.

    Rewrites only that table entry via `sfdisk -N`; the leading comma in the
    spec keeps the existing start (and type), so only the size changes.
    """
    run(["sfdisk", "--no-reread", "-N", str(partnum), str(img)],
        stdin=f",{size_sectors}\n", capture=True)


def ext_size(fs_file: Path) -> int:
    """Return the ext filesystem's size in bytes (block count * block size)."""
    out = subprocess.run(["dumpe2fs", "-h", str(fs_file)],
                         capture_output=True, text=True).stdout
    count = block = None
    for line in out.splitlines():
        if line.startswith("Block count:"):
            count = int(line.split(":", 1)[1])
        elif line.startswith("Block size:"):
            block = int(line.split(":", 1)[1])
    if count is None or block is None:
        raise SystemExit(f"imglib: could not read ext size from {fs_file}")
    return count * block


def e2fsck(fs_file: Path, *, fix: bool) -> int:
    """Run e2fsck on `fs_file`, returning its exit code (not raising).

    - `fix=True` -> `-fy` (auto-fix, e.g. before a resize); `fix=False`
      -> `-fn` (check-only, e.g. a verify),
    - returns the code so callers can treat 0 (clean) / 1 (fixed) as ok
      and >=2 as a real failure.
    """
    flag = "-fy" if fix else "-fn"
    return run(["e2fsck", flag, str(fs_file)], capture=True, echo=False, check=False).returncode


def ext_min_resize(fs_file: Path) -> int:
    """Shrink ext `fs_file` to its minimum with `resize2fs -M`; return new bytes.

    - resize2fs requires a clean fs, so `e2fsck` runs first (exit 0 =
      clean, 1 = fixed, both fine; >=2 is a real error),
    - the new size is read back from dumpe2fs after the resize.
    """
    rc = e2fsck(fs_file, fix=True)
    if rc >= 2:
        raise SystemExit(f"imglib: e2fsck failed on {fs_file} (exit {rc})")
    run(["resize2fs", "-M", str(fs_file)], capture=True)
    return ext_size(fs_file)


def ext_grow(fs_file: Path) -> int:
    """Grow ext `fs_file` to fill its container with `resize2fs`; return new bytes.

    The inverse of `ext_min_resize`: `resize2fs` with no size argument
    expands the filesystem to fill the file (or partition). Used by the
    write app's expand — after the partition is grown, the rootfs is
    grown to fill it.

    - resize2fs requires a clean fs, so `e2fsck` runs first (exit 0 =
      clean, 1 = fixed, both fine; >=2 is a real error).
    """
    rc = e2fsck(fs_file, fix=True)
    if rc >= 2:
        raise SystemExit(f"imglib: e2fsck failed on {fs_file} (exit {rc})")
    run(["resize2fs", str(fs_file)], capture=True)
    return ext_size(fs_file)


def fat_at(img: Path, off: int) -> str:
    """Return the mtools `image@@offset` selector for the FAT at `off`."""
    return f"{img}@@{off}"


def fat_list(node) -> list[str]:
    """Root-directory entry names of a FAT filesystem via mtools (rootless).

    - `node` is an mtools selector — a device path (`/dev/sdb1`) or an
      `image@@offset` from fat_at,
    - the FAT counterpart of fs_list (ext via debugfs); `mdir -b` prints
      full paths, of which we return the basenames.
    """
    cp = run(["mdir", "-i", str(node), "-b", "::/"], capture=True, echo=False, check=False)
    return [os.path.basename(line.rstrip("/"))
            for line in cp.stdout.splitlines() if line.strip()]
