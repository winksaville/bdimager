"""Host block-device targeting + safety for the image builder's capture
and write — the only steps that touch a physical device (root, and
destructive on the wrong one).

The targeting + safety checks are read-only and rootless (lsblk / stat
/ glob over /sys and /dev); the privileged `dd` writes (`dd_capture`,
`dd_write`) also live here — devsafe is the one module that touches a
physical device. The guards catch a wrong-device target before any
write runs:

- two-key targeting — the caller names the device twice (`/dev/xx` and a
  `/dev/disk/by-id/yy`); both must resolve to the same major:minor, so a
  stale `/dev/sdX` enumeration can't slip through,
- system-disk refusal — refuse any target whose lsblk subtree backs a
  mounted filesystem, walking the dm / LVM / md stack (root-on-LVM-on-
  RAID-on-NVMe, not just a direct mount),
- content preview — list each partition's root rootlessly (debugfs for
  ext, mtools for FAT) so the human sees what they'd read or destroy,
- confirmation — interactive, bypassable only via bd-config.toml.
"""

import json
import os
import shutil
import stat as statmod
from pathlib import Path

from . import imglib

BY_ID = Path("/dev/disk/by-id")

# Top-level directory names that mark a partition as a real OS / data
# filesystem; surfaced as an extra warning in the confirm summary.
_SYSTEM_MARKERS = {"etc", "boot", "home", "bin", "usr", "var", "root", "lib"}


def _lsblk(cols: str) -> list[dict]:
    """Return `lsblk -J` (tree) for the given columns, parsed to blockdevices.

    NAME is always requested even if the caller doesn't use it: lsblk only
    nests `children` when NAME is among the output columns, and the
    safety walks (subtree mount checks, partition listing) depend on that
    tree — without it the list is flat and the walks silently see nothing.
    """
    cp = imglib.run(["lsblk", "-J", "-o", f"NAME,{cols}"], capture=True, echo=False)
    return json.loads(cp.stdout).get("blockdevices", [])


def majmin(path: str) -> str:
    """Return 'major:minor' of the block device at `path` (follows symlinks)."""
    st = os.stat(path)
    if not statmod.S_ISBLK(st.st_mode):
        raise SystemExit(f"devsafe: {path} is not a block device")
    return f"{os.major(st.st_rdev)}:{os.minor(st.st_rdev)}"


def verify_two_key(dev_path: str, byid_path: str) -> str:
    """Confirm both targets name the same device; return the resolved /dev path.

    - `byid_path` must live under /dev/disk/by-id/ (the stable, hardware
      handle); a by-uuid or bare name is rejected,
    - both must resolve to the same major:minor, else abort.
    """
    if BY_ID not in Path(byid_path).parents:
        raise SystemExit(
            f"devsafe: second target must be a {BY_ID}/ path, got {byid_path}")
    a, b = majmin(dev_path), majmin(byid_path)
    if a != b:
        raise SystemExit(
            f"devsafe: targets disagree — {dev_path}={a} vs {byid_path}={b}; aborting")
    return os.path.realpath(dev_path)


def resolve_device(path: str, *, test_mode: bool = False) -> str:
    """Resolve a single-device handle to its canonical path.

    - accepts either form — a `/dev/xx` node or a `/dev/disk/by-id/yy`
      symlink — and returns `os.path.realpath(path)`,
    - asserts the result is a block device, except in `test_mode` where a
      regular file stands in for a card (so capture is self-testable with
      no root / hardware).

    This is the single-key resolution capture uses (a read needs no
    cross-check); `verify_two_key` layers the two-key major:minor match
    on top for the destructive write.
    """
    real = os.path.realpath(path)
    if test_mode:
        return real
    if not Path(real).exists():
        raise SystemExit(f"devsafe: {path} does not exist")
    if not statmod.S_ISBLK(os.stat(real).st_mode):
        raise SystemExit(f"devsafe: {path} ({real}) is not a block device")
    return real


def dd_capture(src_dev: str, dest_img: Path, *, test_mode: bool = False) -> None:
    """Copy a source block device into the mother image — the capture write.

    - real mode: `sudo dd if=<src_dev> of=<dest_img>` — root, since
      reading a block device needs it; `conv=fsync` flushes before return,
    - `test_mode`: a rootless file copy, so the capture path runs against
      a synthetic mk_test_image `.img` standing in for a device,
    - the destination's parent is created and any existing dest removed
      first, so a smaller capture never leaves a stale tail.
    """
    dest_img = Path(dest_img)
    dest_img.parent.mkdir(parents=True, exist_ok=True)
    dest_img.unlink(missing_ok=True)
    if test_mode:
        shutil.copyfile(src_dev, dest_img)
        print(f"  capture: copied {src_dev} -> {dest_img} (test-mode)")
        return
    imglib.run(["sudo", "dd", f"if={src_dev}", f"of={dest_img}",
                "bs=4M", "status=progress", "conv=fsync"])


def dd_write(src_img: Path, dest_dev: str, *, test_mode: bool = False) -> None:
    """Write the deployable image to a target device — the write app's dd.

    - real mode: `sudo dd if=<src_img> of=<dest_dev>` — root, since
      writing a block device needs it; `conv=fsync` flushes before return,
    - `test_mode`: write the image bytes into `dest_dev` as a file *without
      truncating*, so a pre-sized "card" file keeps its (larger) size for
      the expand step to grow into; an absent dest is created at image
      size,
    - unlike a device, a file's tail past the image is preserved — that's
      deliberate, the expand step fills it.
    """
    if test_mode:
        data = Path(src_img).read_bytes()
        mode = "r+b" if Path(dest_dev).exists() else "wb"
        with open(dest_dev, mode) as f:
            f.write(data)
        print(f"  write: wrote {src_img} -> {dest_dev} (test-mode)")
        return
    imglib.run(["sudo", "dd", f"if={src_img}", f"of={dest_dev}",
                "bs=4M", "status=progress", "conv=fsync"])


def expand_to_device(dev: str, *, test_mode: bool = False) -> None:
    """Grow the root partition + filesystem to fill the device — write's expand.

    After the (shrunk) image is written, the device is larger than it, so
    the last partition and its ext rootfs are grown to reclaim the free
    space:

    - real mode: `sudo sfdisk` extends the last partition, then `sudo
      resize2fs` grows the live filesystem to fill it,
    - `test_mode`: the device is a file, so the rootless imglib path —
      grow the partition-table entry, then extract -> `resize2fs` ->
      splice the root region (finalize's shrink, in reverse).
    """
    if test_mode:
        _expand_file(Path(dev))
    else:
        _expand_device(dev)


def _expand_file(card: Path) -> None:
    """Rootless expand of a file: grow the root partition + rootfs to fill it."""
    _boot, root, parts = imglib.find_boot_root(card)
    if root is None:
        raise SystemExit(f"devsafe: no ext root partition in {card}")
    partnum = parts.index(root) + 1
    sector = imglib.sector_size(card)
    card_size = card.stat().st_size
    # Sector-align the new size so the partition entry and the extracted
    # region agree — otherwise the grown fs could spill past the partition.
    new_sectors = (card_size - root["off"]) // sector
    part_bytes = new_sectors * sector
    if part_bytes <= root["length"]:
        print(f"  expand: root already fills {card}; nothing to do")
        return
    print(f"\nexpand: root partition #{partnum} -> fill {card_size} B (test-mode)")
    # 1. grow the partition-table entry to span to the end of the card
    imglib.set_part_size(card, partnum, new_sectors)
    # 2. grow the rootfs to fill the enlarged partition (extract the now
    #    larger region — old fs + zero pad — resize2fs to fill, splice back)
    root_fs = imglib.extract_region(card, root["off"], part_bytes)
    try:
        grown = imglib.ext_grow(root_fs)
        imglib.splice_region(card, root["off"], root_fs)
    finally:
        root_fs.unlink(missing_ok=True)
    print(f"  expand: root partition -> {part_bytes} B, rootfs -> {grown} B")


def _expand_device(dev: str) -> None:
    """Privileged expand of a block device: sudo sfdisk grow + sudo resize2fs.

    Untested in CI (needs a real card + root). The partition node is
    `<dev>p<n>` for nvme/mmc (the device name ends in a digit), else
    `<dev><n>`.
    """
    _boot, root, parts = imglib.find_boot_root(Path(dev))
    if root is None:
        raise SystemExit(f"devsafe: no ext root partition on {dev}")
    partnum = parts.index(root) + 1
    part_node = f"{dev}{'p' if dev[-1].isdigit() else ''}{partnum}"
    print(f"\nexpand: grow {dev} partition #{partnum} to fill, then resize2fs")
    # 1. extend the last partition to fill the device (",+" keeps the
    #    start, size = all remaining space), then re-read the table
    imglib.run(["sudo", "sfdisk", "--no-reread", "-N", str(partnum), dev],
               stdin=",+\n")
    imglib.run(["sudo", "partprobe", dev])
    # 2. grow the live filesystem to fill the partition
    imglib.run(["sudo", "e2fsck", "-fy", part_node], check=False)
    imglib.run(["sudo", "resize2fs", part_node])


def _aliases(directory: Path) -> dict:
    """Map each block device's kernel name -> sorted alias names under `directory`."""
    out: dict[str, list[str]] = {}
    if not directory.is_dir():
        return out
    for link in directory.iterdir():
        try:
            kname = os.path.basename(os.path.realpath(link))
        except OSError:
            continue
        out.setdefault(kname, []).append(link.name)
    for names in out.values():
        names.sort()
    return out


def _subtree_mounted(node: dict) -> bool:
    """True if `node` or any descendant has a non-empty mountpoint."""
    if any(m for m in (node.get("mountpoints") or [])):
        return True
    return any(_subtree_mounted(c) for c in node.get("children", []))


def protected_knames() -> set:
    """Whole-disk kernel names that back any mounted filesystem.

    A disk is protected if any node in its lsblk subtree (its partitions,
    and any dm / LVM / md / swap layered on them) is mounted — so a root
    on LVM-on-RAID-on-NVMe protects every underlying disk, not just a
    device with a direct mountpoint.
    """
    return {d["kname"] for d in _lsblk("KNAME,TYPE,MOUNTPOINTS")
            if d.get("type") == "disk" and _subtree_mounted(d)}


def assert_safe_target(device: str) -> None:
    """Abort if `device` (resolved whole disk) backs a mounted filesystem.

    The decisive guard — refuse the system / in-use disk. There is no
    override: a mounted-stack disk is never a capture/write target.
    """
    kname = os.path.basename(os.path.realpath(device))
    if kname in protected_knames():
        raise SystemExit(
            f"devsafe: REFUSING {device} ({kname}) — it backs a mounted "
            "filesystem (system / in-use disk), never a valid target.")


def _device_partitions(device: str) -> list[dict]:
    """Return the partition child nodes (kname + fstype) of `device`."""
    kname = os.path.basename(os.path.realpath(device))
    for d in _lsblk("KNAME,FSTYPE,TYPE"):
        if d["kname"] == kname:
            return [c for c in d.get("children", []) if c.get("type") == "part"]
    return []


def preview_partitions(device: str) -> list[dict]:
    """List each partition of `device` with its fs type and root entries.

    Rootless content peek (debugfs for ext, mtools for FAT) so the human
    sees what's on the device before a destructive write; each entry
    flags top-level system markers (etc/, boot/, home/, ...).
    """
    out = []
    for p in _device_partitions(device):
        node = f"/dev/{p['kname']}"
        fstype = (p.get("fstype") or "").lower()
        if fstype.startswith("ext"):
            names = [e["name"] for e in imglib.fs_list(Path(node), "/")]
        elif fstype in ("vfat", "fat", "msdos"):
            names = imglib.fat_list(node)
        else:
            names = []
        system = sorted({n.lower() for n in names} & _SYSTEM_MARKERS)
        out.append({"node": node, "fstype": fstype or "?",
                    "entries": names, "system": system})
    return out


def confirm(device: str, action: str, *, allow_yes: bool, yes_flag: bool) -> None:
    """Show the target summary and require confirmation for a device write.

    - prints the device and each partition's root preview, flagging any
      that look like a system / data filesystem,
    - the prompt is skipped only when BOTH `allow_yes` (bd-config.toml,
      the persistent permission) and `yes_flag` (the per-run `--yes`) are
      set — neither alone suffices,
    - `--yes` without `allow_yes` is a hard error (checked first), so a
      misconfigured skip fails loud instead of silently dropping to a
      prompt that might be answered on autopilot.
    """
    if yes_flag and not allow_yes:
        raise SystemExit(
            "devsafe: --yes requires [write].allow_yes = true in bd-config.toml")
    print(f"\n=== {action}: {device} ===")
    for p in preview_partitions(device):
        warn = f"   [SYSTEM: {', '.join(p['system'])}]" if p["system"] else ""
        listing = ", ".join(p["entries"]) or "(empty)"
        print(f"  {p['node']} ({p['fstype']}): {listing}{warn}")
    if allow_yes and yes_flag:
        print("devsafe: allow_yes + --yes set — skipping prompt.")
        return
    reply = input(f"\nProceed to {action} {device}? type 'yes' to confirm: ").strip()
    if reply != "yes":
        raise SystemExit("devsafe: not confirmed; aborting.")


def list_devices() -> int:
    """Print whole-disk block devices with size / bus / model / by-id / mounts.

    The helper for picking a capture/write target: shows each disk's
    by-id alias (the handle to pass as the second target) and flags any
    that back a mount (the system disk, which would be refused).
    """
    byid = _aliases(BY_ID)
    protected = protected_knames()
    print("block devices (whole disks):\n")
    for d in _lsblk("KNAME,SIZE,TRAN,MODEL,TYPE"):
        if d.get("type") != "disk":
            continue
        kname = d["kname"]
        flag = "   [SYSTEM/MOUNTED — would be refused]" if kname in protected else ""
        print(f"  /dev/{kname}  {d.get('size', '?'):>9}  "
              f"{(d.get('tran') or '-'):<5}  {d.get('model') or '-'}{flag}")
        aliases = byid.get(kname)
        if aliases:
            for alias in aliases:
                print(f"      by-id: /dev/disk/by-id/{alias}")
        else:
            print("      by-id: (none — udev/mdev may not cover this device)")
    return 0
