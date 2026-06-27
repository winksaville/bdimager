# Chores-01

Chores-XX files use [Prose form](../../AGENTS.md#prose-form). They
contain discussions and notes on various chores in github compatible
markdown. There is also a [todo.md](../todo.md) file that tracks
tasks and in general there should be a chore section for each task
with the why and how this task will be completed.

## chore: scaffold package + open cycle

Commits: [[1]]

Stood up bdimager as an installable package — uv + hatchling, the
version-of-record in `bd-config.toml` `[project].version` (PEP 440
`+` form of the cycle version), and `bd-capture` / `bd-build` /
`bd-write` console scripts. Opened the 0.3.0 cycle that ports fc's
SD-card image builder into bdimager, generalized.

## feat: rootless storage + config core

Commits: [[2]]

Ported fc's rootless storage toolkit and config plumbing,
de-fc'd:

- `imglib` — locate partitions with `sfdisk`, edit the ext root
  with `debugfs` (extract / splice), the FAT boot with `mtools`,
  resize with `resize2fs`; no loop-mount, no root.
- `imgcfg` — load `bd-config.toml` tables and merge
  file < `BD_*` env < CLI into a resolved plan. The `[build]`
  schema was cut to the generic `shrink` / `compress`; fc's
  `fc_path` / `autologin` / `scrub_*` keys are gone.
- `devsafe` and `mk_test_image` ported alongside, the latter
  reseeded with generic anchors (a survivor file + a stale
  overwrite target) in place of fc's scrub/bake fixtures.

## feat: build app + generic file/dir injection

Commits: [[3]]

The build app, generalized. fc's secret-scrub and app-baking
(config / inittab / .profile / local.d) were dropped; the
rootless transform -> inject -> finalize pipeline now injects
caller-specified files and directories instead. The `[build]`
config schema is just `shrink` / `compress`.

### Injection design

- `--add SRC<sep>DEST` copies a host file or directory tree to an
  absolute image path; repeatable, and the same lines work from a
  `@file` (argparse fromfile, shlex-split, `#` comments).
- The separator is `:` or `->`. Per entry an unambiguous split is
  chosen by the absolute-DEST rule: split by each separator the
  entry contains, keep a result only if DEST is absolute and both
  sides are non-empty. Exactly one valid split wins (so a colon
  inside a `->` dest is fine); zero is a format error; two
  distinct valid splits are ambiguous and rejected. The two
  separators are thus mutual escape hatches for a path that
  contains the other.
- An existing DEST is overwritten with its mode / uid / gid
  preserved; a fresh file takes the source mode and root:root.
  Overwrites are listed in the plan.

## feat: capture + write device apps

Commits: [[4]]

Ported the two device apps over `devsafe`:

- `capture` — a single-source, non-destructive read of a block
  device into the mother (no two-key / confirm; that guards the
  write).
- `write` — the full gate (two-key targeting, system-disk
  refusal, two-factor confirm), then expand the rootfs to fill
  the target.
- both take `--test-mode` (a regular file stands in for a device),
  so the capture -> build -> write -> expand chain self-tests
  rootless.

## docs: README + ARCHITECTURE + deferred todo

Commits: [[5]]

README rewritten to the implemented three-command tool (usage,
the `--add` / `@file` syntax, the rootless note, the `bd-write`
safety gate); ARCHITECTURE filled with the pipeline + module map;
the deferred installable-packages work captured as a Todo
(tier-two: real package installation needs mount + chroot +
qemu-user, not the rootless debugfs path).

## chore: add justfile

Commits: [[6]]

Added a `justfile` wrapping the apps, `pytest`, and the rootless
image-test / capture-test / write-test smokes over the installed
`bd-*` console scripts — to cut the bare `uv run ...` friction
seen during the cycle.

## chore: close out 0.3.0 image-builder port

Commits:

Close-out of the 0.3.0 cycle — bookkeeping only: move the In
Progress block here, record the Done entry, and backfill the
`Commits:` refs above (the prior commits are now permanent on
`main`).

### Cycle decisions

- Generalize, no fc-isms: `bd-config.toml`, `BD_*` env,
  `bd-*` console scripts; fc's secret-scrub and app-baking
  dropped.
- A proper installable package (`src/bdimager/`, pyproject +
  hatchling).
- build injection is caller-driven — see the
  [Injection design](#injection-design) above.
- KISS scope: file/dir copy on the rootless `debugfs` path only;
  proper package *installation* is deferred to a Todo.

### As-built ladder

- 0.3.0-0 chore: scaffold package + open cycle [[1]]
- 0.3.0-1 feat: rootless storage + config core [[2]]
- 0.3.0-2 feat: build app + generic file/dir injection [[3]]
- 0.3.0-3 feat: capture + write device apps [[4]]
- 0.3.0-4 docs: README + ARCHITECTURE + deferred todo [[5]]
- 0.3.0-5 chore: add justfile [[6]]
- 0.3.0 close-out (this commit)

### Outcome

18 pytest tests pass rootless (debugfs / mtools / sfdisk under
the hood); the full capture -> build -> write -> expand chain
self-tests with no root or hardware, and the `just` smokes run
the same flow through the installed console scripts. The
real-device write path (sudo `dd` + sfdisk/resize2fs on hardware)
is not exercised in CI.

# References

[1]: https://github.com/winksaville/bdimager/commit/1c3e9a51b3b7 "1c3e9a51b3b768e13f9da04101c55902b4c8bb24"
[2]: https://github.com/winksaville/bdimager/commit/dc5adc0f4a42 "dc5adc0f4a425916fd599411a2b674713362e019"
[3]: https://github.com/winksaville/bdimager/commit/a6d6806d5345 "a6d6806d5345740d64465fdd77434ce62e715b4e"
[4]: https://github.com/winksaville/bdimager/commit/5b8e2a2299b8 "5b8e2a2299b807dbf97d3ab23405e6e4acf8db54"
[5]: https://github.com/winksaville/bdimager/commit/060bdca2248e "060bdca2248e512e0309ab76122ae7079397687e"
[6]: https://github.com/winksaville/bdimager/commit/9cdf1ead6f0e "9cdf1ead6f0efde9e58dcb9b62c1034bf4492904"
