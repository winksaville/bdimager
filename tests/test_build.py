"""Tests for the build app (0.3.0-2): --add parsing and an end-to-end
rootless build (transform + inject + finalize) over a synthetic image.
"""

import sys

import pytest

from bdimager import build, imglib, mk_test_image


# --- --add separator parsing ---------------------------------------------


def test_parse_add_colon():
    """A colon separates SRC from an absolute DEST."""
    assert build.parse_add("./a.conf:/etc/a.conf") == (__import__("pathlib").Path("./a.conf"), "/etc/a.conf")


def test_parse_add_arrow():
    """An arrow separates SRC from an absolute DEST."""
    src, dest = build.parse_add("./a.conf->/etc/a.conf")
    assert str(src) == "a.conf" and dest == "/etc/a.conf"


def test_parse_add_arrow_escapes_colon_in_dest():
    """A colon inside a `->`-separated dest is fine — only arrow split is valid."""
    src, dest = build.parse_add("./a->/etc/wei:rd")
    assert str(src) == "a" and dest == "/etc/wei:rd"


def test_parse_add_ambiguous_is_error():
    """An entry that parses validly under both separators is rejected."""
    with pytest.raises(SystemExit):
        build.parse_add("/a:/b->/c")


def test_parse_add_requires_absolute_dest():
    """A relative DEST has no valid split and is an error."""
    with pytest.raises(SystemExit):
        build.parse_add("./a:etc/a")


def test_parse_add_requires_separator():
    """An entry with neither separator is an error."""
    with pytest.raises(SystemExit):
        build.parse_add("just-a-path")


# --- end-to-end build -----------------------------------------------------


def _plan(mother, output, *, shrink=True, compress=True):
    """A resolved-plan dict (the {key: (value, source)} shape) for run_build."""
    return {
        "images_dir": ("images", "config"),
        "mother": (str(mother), "flag"),
        "output": (str(output), "flag"),
        "shrink": (shrink, "flag"),
        "compress": (compress, "flag"),
    }


def test_build_injects_and_finalizes(tmp_path):
    """Build injects a file, a directory tree, and an overwrite; then shrinks + xz."""
    mother = tmp_path / "mother.img"
    output = tmp_path / "deploy.img"
    assert mk_test_image.build(mother) == 0

    # Host sources: a standalone config, a directory tree, and a file that
    # overwrites the synthetic image's stale config.
    conf = tmp_path / "app.conf"
    conf.write_text("key = value\n")
    assets = tmp_path / "assets"
    (assets / "sub").mkdir(parents=True)
    (assets / "top.txt").write_text("top\n")
    (assets / "sub" / "b.txt").write_text("nested\n")
    newcfg = tmp_path / "newcfg"
    newcfg.write_text("FRESH CONFIG\n")

    adds = [
        build.parse_add(f"{conf}:/etc/app.conf"),
        build.parse_add(f"{assets}:/opt/app/assets"),
        build.parse_add(f"{newcfg}:{mk_test_image.STALE_PATH}"),
    ]
    assert build.run_build(_plan(mother, output), adds) == 0

    # Finalize produced both the shrunk .img and a valid .xz, survivor intact.
    assert output.exists()
    assert (tmp_path / "deploy.img.xz").exists()
    assert mk_test_image.verify(output) == 0

    # Injected content landed; the stale file was overwritten.
    _boot, root, _parts = imglib.find_boot_root(output)
    root_fs = imglib.extract_region(output, root["off"], root["length"])
    try:
        assert imglib.fs_read(root_fs, "/etc/app.conf") == "key = value\n"
        assert imglib.fs_read(root_fs, "/opt/app/assets/top.txt") == "top\n"
        assert imglib.fs_read(root_fs, "/opt/app/assets/sub/b.txt") == "nested\n"
        assert imglib.fs_read(root_fs, mk_test_image.STALE_PATH) == "FRESH CONFIG\n"
    finally:
        root_fs.unlink(missing_ok=True)


def test_add_via_atfile(tmp_path, monkeypatch, capsys):
    """`@file` supplies --add lines (with comments) just like the CLI."""
    mother = tmp_path / "mother.img"
    output = tmp_path / "deploy.img"
    mk_test_image.build(mother)
    addsfile = tmp_path / "adds.txt"
    addsfile.write_text(
        "# injections for this build\n"
        "--add ./app.conf:/etc/app.conf\n"
        "\n"
        "--add './a->/etc/wei:rd'\n"
    )
    monkeypatch.setattr(sys, "argv", [
        "bd-build", "--dry-run",
        "--mother", str(mother), "--output", str(output),
        f"@{addsfile}",
    ])
    assert build.main() == 0
    out = capsys.readouterr().out
    assert "/etc/app.conf" in out
    assert "/etc/wei:rd" in out


def test_dry_run_mutates_nothing(tmp_path, monkeypatch, capsys):
    """--dry-run resolves and prints the plan but writes no output image."""
    mother = tmp_path / "mother.img"
    output = tmp_path / "deploy.img"
    mk_test_image.build(mother)
    monkeypatch.setattr(sys, "argv", [
        "bd-build", "--dry-run",
        "--mother", str(mother), "--output", str(output),
        "--add", f"{tmp_path / 'x'}:/etc/x",
    ])
    assert build.main() == 0
    assert not output.exists()
    assert "stages:" in capsys.readouterr().out
