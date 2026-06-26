"""Tests for the rootless storage + config core (0.3.0-1).

- imgcfg: config load / schema rejection / precedence — pure, no tools,
- imglib + mk_test_image: a rootless round-trip over a synthetic image
  (sfdisk / mke2fs / debugfs / mtools) proving the in-file edit path.
"""

import argparse

import pytest

from bdimager import imgcfg, imglib, mk_test_image

# --- imgcfg ---------------------------------------------------------------

_DEFAULTS = {"images_dir": "images", "mother": "mother.img", "shrink": True}


def test_load_config_missing_returns_defaults(tmp_path):
    """A missing config file falls back to the built-in defaults entirely."""
    cfg = imgcfg.load_config(tmp_path / "absent.toml", ("image", "build"), _DEFAULTS)
    assert cfg == _DEFAULTS


def test_load_config_overlays_known_keys(tmp_path):
    """Known keys in a read section overlay the defaults."""
    p = tmp_path / "bd-config.toml"
    p.write_text('[image]\nimages_dir = "imgs"\n[build]\nshrink = false\n')
    cfg = imgcfg.load_config(p, ("image", "build"), _DEFAULTS)
    assert cfg["images_dir"] == "imgs"
    assert cfg["shrink"] is False
    assert cfg["mother"] == "mother.img"  # untouched default


def test_load_config_rejects_unknown_key(tmp_path):
    """An unknown key in a read section is a hard error (a typo)."""
    p = tmp_path / "bd-config.toml"
    p.write_text('[build]\nshrnk = true\n')  # typo
    with pytest.raises(SystemExit):
        imgcfg.load_config(p, ("build",), _DEFAULTS)


def test_resolve_precedence(monkeypatch):
    """Precedence is file < BD_* env < CLI flag, recorded in the source."""
    file_cfg = {"images_dir": "images", "shrink": True}
    # env overrides file
    monkeypatch.setenv("BD_IMAGES_DIR", "from-env")
    # flag overrides env; absent flag (None) falls through
    args = argparse.Namespace(images_dir="from-flag", shrink=None)
    plan = imgcfg.resolve(args, file_cfg, ("images_dir",), ("shrink",))
    assert plan["images_dir"] == ("from-flag", "flag")
    assert plan["shrink"] == (True, "config")


def test_resolve_env_only(monkeypatch):
    """With no flag, a BD_* env var wins over the file value."""
    file_cfg = {"shrink": True}
    monkeypatch.setenv("BD_SHRINK", "off")
    args = argparse.Namespace(shrink=None)
    plan = imgcfg.resolve(args, file_cfg, (), ("shrink",))
    assert plan["shrink"] == (False, "env")


def test_parse_bool_rejects_garbage():
    """A non-boolean BD_* value fails loud rather than silently flipping."""
    assert imgcfg.parse_bool("yes", "BD_SHRINK") is True
    with pytest.raises(SystemExit):
        imgcfg.parse_bool("maybe", "BD_SHRINK")


# --- imglib round-trip ----------------------------------------------------


def test_synthetic_image_roundtrip(tmp_path):
    """Build a synthetic image, then edit its ext root + read its FAT boot.

    Exercises the whole rootless toolkit: sfdisk table, mke2fs root,
    debugfs read/write, splice extract/back, mtools FAT listing.
    """
    img = tmp_path / "mother.img"
    assert mk_test_image.build(img) == 0

    boot, root, parts = imglib.find_boot_root(img)
    assert boot is not None and root is not None
    assert len(parts) == 2

    # ext root: clean fsck + seeded survivor reads back.
    root_fs = imglib.extract_region(img, root["off"], root["length"])
    try:
        assert imglib.e2fsck(root_fs, fix=False) == 0
        assert imglib.fs_read(root_fs, mk_test_image.SURVIVOR_PATH) == \
            mk_test_image.SURVIVOR_CONTENT
        assert imglib.fs_exists(root_fs, mk_test_image.STALE_PATH)

        # Inject a new file, splice back, and confirm it survives a re-read.
        marker = tmp_path / "marker.txt"
        marker.write_text("injected\n")
        imglib.debugfs_batch(root_fs, [f"write {marker} /marker.txt"])
        imglib.splice_region(img, root["off"], root_fs)
    finally:
        root_fs.unlink(missing_ok=True)

    reread = imglib.extract_region(img, root["off"], root["length"])
    try:
        assert imglib.fs_read(reread, "/marker.txt") == "injected\n"
    finally:
        reread.unlink(missing_ok=True)

    # FAT boot: the seeded boot tokens list back via mtools.
    names = imglib.fat_list(imglib.fat_at(img, boot["off"]))
    assert "config.txt" in names
    assert "cmdline.txt" in names
