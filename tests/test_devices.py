"""Tests for the device apps (0.3.0-3): capture and write driven in
--test-mode (regular files standing in for block devices), so the full
capture -> build -> write -> expand chain runs rootless, no hardware.
"""

from bdimager import build, capture, imglib, mk_test_image, write


def _build_plan(mother, output, *, shrink=True, compress=False):
    return {
        "images_dir": ("images", "config"),
        "mother": (str(mother), "flag"),
        "output": (str(output), "flag"),
        "shrink": (shrink, "flag"),
        "compress": (compress, "flag"),
    }


def test_capture_then_build(tmp_path):
    """capture --test-mode copies a synthetic 'card' into the mother, then build."""
    card = tmp_path / "card.img"
    mother = tmp_path / "mother.img"
    deploy = tmp_path / "deploy.img"
    assert mk_test_image.build(card) == 0

    plan = {
        "images_dir": ("images", "config"),
        "mother": (str(mother), "flag"),
        "source": (str(card), "flag"),
    }
    assert capture.run_capture(plan, test_mode=True) == 0
    assert mother.exists()
    assert mother.read_bytes() == card.read_bytes()

    # The captured mother builds into a valid deployable image.
    assert build.run_build(_build_plan(mother, deploy, compress=True), []) == 0
    assert mk_test_image.verify(deploy) == 0


def test_write_then_expand(tmp_path):
    """write --test-mode writes a deploy image into a larger 'card', then expands."""
    mother = tmp_path / "mother.img"
    deploy = tmp_path / "deploy.img"
    card = tmp_path / "card.img"
    assert mk_test_image.build(mother) == 0
    # Shrink so the deploy is smaller than the card, leaving room to expand.
    assert build.run_build(_build_plan(mother, deploy, shrink=True), []) == 0

    # Pre-size the card larger than the deploy image (a real card would be).
    with open(card, "wb") as f:
        f.truncate(mk_test_image.TOTAL_BYTES)

    plan = {
        "images_dir": ("images", "config"),
        "output": (str(deploy), "flag"),
        "target": (str(card), "flag"),
        "target_byid": ("", "config"),
        "expand": (True, "flag"),
        "allow_yes": (False, "config"),
    }
    assert write.run_write(plan, allow_yes=False, yes_flag=False, test_mode=True) == 0

    # The root partition + rootfs grew to fill the card, survivor intact.
    assert mk_test_image.verify_expanded(card) == 0
    _boot, root, _parts = imglib.find_boot_root(card)
    root_fs = imglib.extract_region(card, root["off"], root["length"])
    try:
        assert imglib.fs_read(root_fs, mk_test_image.SURVIVOR_PATH) == \
            mk_test_image.SURVIVOR_CONTENT
    finally:
        root_fs.unlink(missing_ok=True)
