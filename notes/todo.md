# Todo

This file uses [Prose form](../AGENTS.md#prose-form). It
contains near term tasks with a short description and
uses links or reference links for more details.

## In Progress

**Port fc's image builder into bdimager (generalized)**

bdimager is an empty scaffold. fc has a working rootless SD-card
image builder (capture → build → write, ~2,000 lines across 7
modules) that maps onto bdimager's purpose. Port it in as a
proper installable Python package, stripped of all fc-isms,
replacing fc's app-baking with a generic file/dir injection
feature.

Decisions:

- Generalize, no fc-isms: `bd-config.toml`, `BD_*` env prefix,
  `bd-capture` / `bd-build` / `bd-write` console scripts.
- Proper installable package: `src/bdimager/` + `pyproject.toml`.
- `build` injection: explicit `src`→`dest`, accepting **both**
  `:` and `->` (mutual collision escape hatch); an entry
  ambiguous in *both* separators is a hard error;
  dest-already-exists → overwrite, surfaced in `--dry-run`;
  supplied via `--add` on the CLI or via a `@file` (argparse
  fromfile).
- Scope KISS: file/dir copy only, on fc's rootless
  `debugfs` / `mtools` path (no mount). Real package
  installation is deferred to a todo (tier-two: needs
  mount + chroot + qemu-user, not the rootless debugfs path).

Ladder:

- 0.3.0-0 chore: scaffold package + open cycle (done)
  - `pyproject.toml` (version-of-record `0.3.0+0`, PEP 440
    `-`→`+`; hatchling; the three console scripts),
    `src/bdimager/` skeleton, uv / `.python-version`.
  - Move this item into `## In Progress`; open the chores-01
    section (empty `Commits:`).
- 0.3.0-1 feat: rootless storage + config core (done)
  - Port `imglib`, `imgcfg`, `devsafe`, `mk_test_image` into the
    package, de-fc'd.
  - `bd-config.toml` schema: drop fc build keys (`fc_path`,
    `fc_config_target`, `autologin_tty`, `scrub_*`), keep the
    generic `image` / `capture` / `write` / `build` keys, add
    the inject key; `BD_*` env.
  - Validate: `debugfs` / `mtools` round-trip smoke via
    `mk_test_image`.
- 0.3.0-2 feat: build app + generic file/dir injection (done)
  - Port `build` (keep shrink / compress / finalize on the
    rootless path), remove the baking / scrub logic.
  - Add `--add` / `@file` `src:dest` / `src->dest` injection
    with the collision rules above.
  - Validate: `image-test` self-test green, plus an inject case.
- 0.3.0-3 feat: capture + write device apps (done)
  - Port `capture` + `write` de-fc'd, `--test-mode` preserved,
    `BD_*` config.
  - Validate: `capture-test` + `write-test` self-tests green.
- 0.3.0-4 docs: README + ARCHITECTURE + deferred todo (done)
  - README per-subcommand usage (incl. the `--add` / `@file`
    syntax + collision rules + rootless note), ARCHITECTURE
    module map.
  - Add the installable-packages todo (tier-two).
- 0.3.0-5 chore: add justfile (done)
  - `just` recipes wrapping the apps (build / capture / write),
    `test` (pytest), `mk-test-image`, and the rootless
    image-test / capture-test / write-test smokes over the
    installed `bd-*` console scripts.
  - README pointer to `just --list`.
- 0.3.0 close-out: chore: close out 0.3.0 image-builder port
  - Full validation (all three self-tests; console scripts
    install + run).
  - Bookkeeping: move this block → chores-01 + an As-built
    ladder, `## Done` entry, ref prune, update
    `notes/README.md` if needed.
  - Decide push shape (squash / merge non-ff / keep) at push
    time.

## Todo

 Entries are in **strict priority rank** — #1 highest,
 descending. Reprioritize by moving an entry, then
 `vc-x1 fix-todo --no-dry-run notes/todo.md` to renumber.
 The numbers are positional rank, not stable IDs — to refer
 to a Todo, name it by its **title** (a greppable mention;
 a numbered list item has no anchor to link to), not its
 number. Long-tail entries
 live in [todo-backlog.md](todo-backlog.md). Use the
 [Prose Form in AGENTS.md](../AGENTS.md#prose-form); deeper
 detail goes in `notes/chores/chores-NN.md` design
 subsections (link via `[N]` ref).

1. **Handle installable packages in build inject.** Today `bd-build
   --add` only file-copies into the rootfs (the rootless `debugfs`
   path). A second tier would recognize an installable package
   (`.deb` / Alpine `apk` / Python wheel) and install it *properly*
   (dependencies, post-install hooks) instead of copying files.
   - This needs loop-mount + `chroot` + `qemu-user` / `binfmt_misc`
     (and root) to run the distro's installer in a foreign-arch
     rootfs — out of scope for the rootless KISS path, so it is its
     own cycle.
   - The `--add` fallback (user supplies the explicit destination)
     stays the path for anything not recognized.

## Done

Completed tasks are moved from `## Todo` to here, `## Done`, as they are completed
and older `## Done` sections are moved to [done.md](done.md) to keep this file small.

# References
