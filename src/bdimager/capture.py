"""Capture a source block device into the mother image — the capture app.

`capture` is one of the three image-builder apps (`build`, `capture`,
`write`). It reads a source block device — an SD card, USB stick, NVMe,
or disk — into `[image].mother` (via the device layer, `devsafe`) so a
subsequent `build` can transform it. Reading a block device needs root,
so the `dd` runs under `sudo`; the surrounding inspection (device
resolution, `--list-devices`) stays rootless.

capture is a *non-destructive read*, so it takes a single source device
(`--source`, a `/dev/xx` node or a `/dev/disk/by-id/yy` handle) — no
two-key targeting and no confirm (that safety guards the destructive
write, not this). `--test-mode` reads a regular file instead of a block
device, so the capture path is exercisable against a synthetic
mk_test_image `.img` with no root or hardware.

Settings come from `bd-config.toml`'s `[image]` (shared artifact paths)
and `[capture]` (this app's `source`); see `imgcfg` for the config / env
/ CLI precedence. `--dry-run` resolves and prints the plan, touching
nothing.

    # list candidate devices (with by-id), then exit
    bd-capture --list-devices

    # capture a device into the mother (needs sudo)
    bd-capture --source /dev/sdb

    # exercise the capture path against a synthetic file, no root
    bd-capture --test-mode \\
        --source images/_test-card.img --mother images/_test-mother.img
"""

import argparse
from pathlib import Path

from . import devsafe, imgcfg

# bd-config.toml tables this app reads: shared artifact paths + capture's
# own knobs.
SECTIONS = ("image", "capture")

# Built-in fallback mirroring the committed [image] + [capture] defaults,
# so the tool resolves a complete plan against a stripped config.
DEFAULTS: dict[str, object] = {
    "images_dir": "images",
    "mother": "mother.img",
    "source": "",
}

STR_KEYS = ("images_dir", "mother", "source")
BOOL_KEYS = ()


def print_plan(config_path: Path, plan: dict) -> None:
    """Print the resolved settings and the capture stage."""
    imgcfg.print_settings(config_path, plan, STR_KEYS, BOOL_KEYS, "capture")
    source = plan["source"][0]
    mother = imgcfg.img_path(plan, "mother")
    print("")
    print("stages:")
    if source:
        print(f"  1. capture   dd if={source} of={mother}")
    else:
        print("  1. capture   (no source set — nothing to capture)")


def run_capture(plan: dict, *, test_mode: bool) -> int:
    """Capture the source device into the mother image.

    - resolves the single `--source` handle (`/dev/xx` or by-id) to a
      device, then copies it into `[image].mother` via devsafe,
    - `test_mode` reads a regular file instead of a block device so the
      path is self-testable; otherwise the read runs under `sudo dd`.
    """
    source = plan["source"][0]
    if not source:
        raise SystemExit("capture: no --source device given (nothing to capture)")
    mother = imgcfg.img_path(plan, "mother")
    dev = devsafe.resolve_device(source, test_mode=test_mode)
    print(f"\ncapture: {dev} -> {mother}{' (test-mode)' if test_mode else ''}")
    devsafe.dd_capture(dev, mother, test_mode=test_mode)
    print("\ncapture: device -> mother complete.")
    return 0


def parse_args() -> argparse.Namespace:
    """Build the parser: control flags + one flag per [image]/[capture] key."""
    p = argparse.ArgumentParser(
        prog="bd-capture",
        description=__doc__.splitlines()[0],
    )
    p.add_argument(
        "--config", type=Path, default=imgcfg.DEFAULT_CONFIG,
        help=f"bd-config.toml to read [image]/[capture] from (default: {imgcfg.DEFAULT_CONFIG})",
    )
    p.add_argument(
        "--dry-run", action="store_true", dest="dry_run",
        help="resolve and print the plan, mutate nothing",
    )
    p.add_argument(
        "--list-devices", action="store_true", dest="list_devices",
        help="list block devices (with by-id) for picking a source, then exit",
    )
    p.add_argument(
        "--test-mode", action="store_true", dest="test_mode",
        help="read a regular file instead of a block device (rootless self-test)",
    )
    imgcfg.add_arguments(p, STR_KEYS, BOOL_KEYS)
    return p.parse_args()


def main() -> int:
    """Resolve the plan, print it, then capture unless --dry-run."""
    args = parse_args()
    if args.list_devices:
        return devsafe.list_devices()
    file_cfg = imgcfg.load_config(args.config, SECTIONS, DEFAULTS)
    plan = imgcfg.resolve(args, file_cfg, STR_KEYS, BOOL_KEYS)
    print_plan(args.config, plan)

    if args.dry_run:
        return 0
    return run_capture(plan, test_mode=args.test_mode)


if __name__ == "__main__":
    raise SystemExit(main())
