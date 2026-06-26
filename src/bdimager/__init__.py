"""bdimager — rootless block-device image capture / build / write.

A standalone Python tool that images block devices (typically SD
cards) without root for the image-editing path:

- capture: read a source block device into a *mother* image,
- build: turn a mother image into a distributable image (shrink /
  compress, plus generic file/dir injection), rootless,
- write: write a distributable image to a (possibly larger)
  target device, expanding the rootfs to fill it.

The rootless image edits use `debugfs` (ext) and `mtools` (FAT) on
the image file directly — no loop-mount, no root; only the
physical-device `dd` of capture / write needs sudo.

The version-of-record lives in `bd-config.toml` `[project].version`.
"""
