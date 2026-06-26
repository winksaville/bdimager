# Architecture

This file uses [Prose form](AGENTS.md#prose-form). It describes
how the `bdimager` package is structured.

## Pipeline

Three standalone apps move an image through two artifacts — a
*mother* image and a *deployable* image:

- **capture** (`bd-capture`) reads a source block device into the
  mother image.
- **build** (`bd-build`) transforms the mother into the deployable
  image — inject files/dirs, shrink the rootfs, xz it.
- **write** (`bd-write`) writes the deployable image to a target
  device and grows the rootfs to fill it.

`build` is fully rootless; only `capture` / `write` touch a
physical device (the `dd`, under `sudo`).

## Modules (`src/bdimager/`)

- **`imglib.py`** — rootless in-file storage toolkit. Locates
  partitions with `sfdisk -J`, edits the ext root with `debugfs`
  (extract the partition to a temp file at offset 0, edit, splice
  it back), edits the FAT boot with `mtools` (`image@@offset`),
  and resizes with `resize2fs`. No loop-mount, no root.
- **`imgcfg.py`** — shared config plumbing. Loads `bd-config.toml`
  tables, merges file < `BD_*` env < CLI flag into a resolved
  plan, generates the per-key flags, and prints the plan.
- **`devsafe.py`** — physical-device targeting + safety (two-key
  match, system-disk refusal, content preview, confirm) and the
  privileged `dd` capture/write plus expand. The one module that
  touches a device.
- **`build.py`** — the rootless build app: transform copy, inject
  (`--add` / `@file`), finalize (shrink + xz). Imports only
  `imglib` + `imgcfg`, never `devsafe`, so "build is rootless"
  holds structurally.
- **`capture.py`** / **`write.py`** — the device apps, over
  `devsafe` + `imgcfg`.
- **`mk_test_image.py`** — builds / verifies a small synthetic
  Pi-like image so the build + device paths self-test rootlessly
  (no card, no root); used by the `tests/`.

## Dependency direction

`imglib` and `imgcfg` are leaves. `devsafe` → `imglib`. `build` →
`imglib` + `imgcfg`. `capture` / `write` → `devsafe` + `imgcfg`.
`mk_test_image` → `imglib`.

## See also

- [`README.md`](README.md) — user-facing overview and
  per-subcommand usage.
- [`AGENTS.md`](AGENTS.md) — bot workflow, versioning,
  commit/push conventions, code conventions.
- [`notes/todo.md`](notes/todo.md) — live task list
- [`notes/chores/`](notes/chores) — chores-*.md files contain
  discussion and notes on various chores in github compatible
  markdown.
