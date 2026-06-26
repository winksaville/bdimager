"""Write a deployable image to a target device — the write app.

`write` is one of the three image-builder apps (`build`, `capture`,
`write`) and the destructive one: it writes `[image].output` to a
physical block device. Writing a device needs root, so the `dd` runs
under `sudo`; the surrounding targeting + inspection stays rootless.

`write` is the read/write counterpart of `capture` — capture reads a
device into an image, write writes an image to a device. Because a wrong
target is unrecoverable, write runs the full `devsafe` gate before any
write:

- two-key targeting — the caller names the device twice, `--target`
  (`/dev/xx`) and `--target-byid` (`/dev/disk/by-id/yy`); they must
  resolve to the same major:minor or it aborts,
- system-disk refusal — any target backing a mounted filesystem is
  refused outright,
- two-factor confirm — an interactive confirm, skippable only with BOTH
  `[write].allow_yes` (config) and `--yes` (per run).

Unless `[write].expand` is off, after the write the root partition and
its filesystem are grown to fill the target (the shrunk image leaves the
rest of the device unused). `--test-mode` writes to a regular file and
skips the device gate, so the write + expand path is exercisable against
a built image with no root or hardware. `--dry-run` resolves and prints
the plan, touching nothing.

    # list candidate devices (with by-id), then exit
    bd-write --list-devices

    # write the deploy image to a device (needs sudo)
    bd-write --target /dev/sdb --target-byid /dev/disk/by-id/usb-...

    # exercise the write path against a file, no root
    bd-write --test-mode \\
        --output images/deploy.img --target images/_test-card.img
"""

import argparse
from pathlib import Path

from . import devsafe, imgcfg

# bd-config.toml tables this app reads: shared artifact paths + write's
# own knobs.
SECTIONS = ("image", "write")

# Built-in fallback mirroring the committed [image] + [write] defaults.
# `allow_yes` is here but absent from STR_KEYS / BOOL_KEYS, so argparse
# generates no `--allow-yes` — the confirm-bypass permission is grantable
# only in the config, and skipping also needs `--yes` (two-factor).
DEFAULTS: dict[str, object] = {
    "images_dir": "images",
    "output": "deploy.img",
    "target": "",
    "target_byid": "",
    "expand": True,
    "allow_yes": False,
}

STR_KEYS = ("images_dir", "output", "target", "target_byid")
BOOL_KEYS = ("expand",)


def print_plan(config_path: Path, plan: dict) -> None:
    """Print the resolved settings and the write stage."""
    imgcfg.print_settings(config_path, plan, STR_KEYS, BOOL_KEYS, "write")
    output = imgcfg.img_path(plan, "output")
    target = plan["target"][0]
    print("")
    print("stages:")
    if target:
        print(f"  1. write    dd if={output} of={target}")
        if plan["expand"][0]:
            print("  2. expand   grow root partition + resize2fs to fill the device")
    else:
        print("  1. write    (no target set — nothing to write)")


def run_write(plan: dict, *, allow_yes: bool, yes_flag: bool, test_mode: bool) -> int:
    """Write the output image to the target device behind the safety gate.

    - real mode: two-key targeting + system-disk refusal + interactive
      confirm, then `sudo dd` the output image to the device,
    - `test_mode`: skip the device gate (it needs a real block device,
      validated separately) and write to a file, so the path is
      self-testable,
    - then, unless `[write].expand` is off, grow the root partition +
      filesystem to fill the device.
    """
    output = imgcfg.img_path(plan, "output")
    target = plan["target"][0]
    byid = plan["target_byid"][0]
    if not target:
        raise SystemExit("write: no --target device given (nothing to write)")
    if not output.exists():
        raise SystemExit(f"write: image not found: {output} (run bd-build first)")

    if test_mode:
        dev = devsafe.resolve_device(target, test_mode=True)
    else:
        if not byid:
            raise SystemExit(
                "write: --target requires --target-byid (the /dev/disk/by-id handle)")
        dev = devsafe.verify_two_key(target, byid)
        devsafe.assert_safe_target(dev)
        devsafe.confirm(dev, "write", allow_yes=allow_yes, yes_flag=yes_flag)

    print(f"\nwrite: {output} -> {dev}{' (test-mode)' if test_mode else ''}")
    devsafe.dd_write(output, dev, test_mode=test_mode)
    if plan["expand"][0]:
        devsafe.expand_to_device(dev, test_mode=test_mode)
    print("\nwrite: image -> device complete.")
    return 0


def parse_args() -> argparse.Namespace:
    """Build the parser: control flags + one flag per [image]/[write] key."""
    p = argparse.ArgumentParser(
        prog="bd-write",
        description=__doc__.splitlines()[0],
    )
    p.add_argument(
        "--config", type=Path, default=imgcfg.DEFAULT_CONFIG,
        help=f"bd-config.toml to read [image]/[write] from (default: {imgcfg.DEFAULT_CONFIG})",
    )
    p.add_argument(
        "--dry-run", action="store_true", dest="dry_run",
        help="resolve and print the plan, mutate nothing",
    )
    p.add_argument(
        "--list-devices", action="store_true", dest="list_devices",
        help="list block devices (with by-id) for picking a target, then exit",
    )
    p.add_argument(
        "--test-mode", action="store_true", dest="test_mode",
        help="write a regular file and skip the device gate (rootless self-test)",
    )
    p.add_argument(
        "--yes", action="store_true", dest="yes",
        help="skip the write confirm — only with [write].allow_yes=true (else errors)",
    )
    imgcfg.add_arguments(p, STR_KEYS, BOOL_KEYS)
    return p.parse_args()


def main() -> int:
    """Resolve the plan, print it, then write unless --dry-run."""
    args = parse_args()
    if args.list_devices:
        return devsafe.list_devices()
    file_cfg = imgcfg.load_config(args.config, SECTIONS, DEFAULTS)
    plan = imgcfg.resolve(args, file_cfg, STR_KEYS, BOOL_KEYS)
    print_plan(args.config, plan)

    if args.dry_run:
        return 0
    return run_write(plan, allow_yes=bool(file_cfg["allow_yes"]),
                     yes_flag=args.yes, test_mode=args.test_mode)


if __name__ == "__main__":
    raise SystemExit(main())
