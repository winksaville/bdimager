# bdimager

A Python tool to image block devices (typically SD cards) through
three commands:

- `bd-capture` — copy a source block device to a file *mother* image.
- `bd-build` — turn the mother into a distributable image: inject
  caller-specified files/directories, then shrink the rootfs and xz
  it.
- `bd-write` — write the distributable image to a target block
  device, growing the rootfs to fill a target larger than the image.

The image-editing path is **rootless**: `bd-build` edits the
filesystems *inside* the `.img` (`debugfs` for ext, `mtools` for
FAT) rather than loop-mounting, so no `sudo` is needed. Only the
physical-device `dd` of capture/write needs root.

Eventually I'd like to convert this to Rust.

## Requirements

- Python ≥ 3.14 and [`uv`](https://docs.astral.sh/uv/).
- System tools on `PATH`: `sfdisk` / `lsblk` (util-linux),
  `debugfs` / `mke2fs` / `resize2fs` / `dumpe2fs` / `e2fsck`
  (e2fsprogs), `mformat` / `mcopy` / `mdir` (mtools), and `xz`.

## Usage

Settings come from a `bd-config.toml` in the current directory
(`[image]` shared paths plus `[build]` / `[capture]` / `[write]`
knobs). Precedence is lowest to highest: config file < `BD_<KEY>`
env var < CLI flag. Every command takes `--dry-run` to resolve and
print the plan without touching anything, and `--config` to point
at another file.

### bd-capture (device → mother)

A non-destructive read; needs `sudo` for the real `dd`.

```
bd-capture --list-devices                  # pick a source (shows by-id)
bd-capture --source /dev/sdb               # capture into the mother
bd-capture --test-mode --source card.img --mother mother.img  # rootless
```

### bd-build (mother → deployable image)

Fully rootless. Injection places host files/directories into the
image rootfs:

- `--add SRC:DEST` or `--add SRC->DEST` — copy host `SRC` (a file
  or a directory tree) to absolute image path `DEST`. Repeatable.
- the separator is `:` or `->`; per entry pick whichever your
  paths don't contain — e.g. use `->` when the destination has a
  colon (`'./a->/etc/wei:rd'`). `DEST` must be absolute; an entry
  that parses validly under *both* separators is a hard error.
- an existing `DEST` is overwritten (its mode/uid/gid preserved);
  overwrites are listed in the plan.
- the same arguments may live in a `@file` — one `--add SRC:DEST`
  per line, `#` comments allowed — passed as `bd-build @adds.txt`.

```
bd-build --mother mother.img --output deploy.img \
    --add ./app.conf:/etc/app.conf --add ./assets:/opt/app/assets
bd-build --no-shrink --no-compress         # skip the finalize steps
```

### bd-write (image → device)

Destructive, so it runs the full safety gate: two-key targeting
(`--target` plus the `--target-byid` `/dev/disk/by-id` handle, which
must resolve to the same device), refusal of any disk backing a
mounted filesystem, and an interactive confirm. The confirm is
skippable only with both `[write].allow_yes = true` and `--yes`.
After writing, the rootfs is grown to fill the target unless
`--no-expand`.

```
bd-write --list-devices
bd-write --target /dev/sdb --target-byid /dev/disk/by-id/usb-...
bd-write --test-mode --output deploy.img --target card.img  # rootless
```

## Repo

This is the main repo of a dual-repo convention for using
a bot to help develop a project — code, but equally prose, an
image, a song, a screenplay, anything the bot generates from a
conversation. The goal is that this main repo contains the
"what" (the artifact), while the partner bot repo contains the
"why" and "how" (the conversation). The key to the convention is
each change is cross-referenced to the other. Thus there is a
coherent story of the development of the project across time.

The beginnings of that tool is [vc-x1](https://github.com/winksaville/vc-x1)
which currently does achieve this goal, but is being used as a
first test bed.

### Assumptions

- **The [`vc-x1`](https://github.com/winksaville/vc-x1) companion tool**
  will be used to help manage the repo the underlying jj steps are
  documented so the flow also works without it.

### Cloning

`vc-x1 clone ..` can be used to clone

## Releasing

TBD

## jj Tips for Git Users

This project uses [Jujutsu (jj)](https://docs.jj-vcs.dev/latest/)
alongside git. New to jj? See
[Steve Klabnik](https://github.com/steveklabnik)'s
[Jujutsu tutorial](https://steveklabnik.github.io/jujutsu-tutorial).

Repo-specific how-tos — initial commit, pushing, modifying and
force-pushing a commit, revsets, and a useful-commands reference —
live in [notes/jj-tips.md](notes/jj-tips.md).

## Cross-repo Linking with Git Trailers

Commits in each repo use [git trailers](https://git-scm.com/docs/git-interpret-trailers)
to cross-reference their counterpart in the other repo via an
`ochid` (Other Change ID) trailer — the defining mechanism of the
dual-repo convention. For the full definition — trailer syntax,
the example shape, per-commit mechanics, and `.vc-config.toml` —
see
[Cross-repo linking (ochid trailers)](AGENTS.md#cross-repo-linking-ochid-trailers).

## Contributing

Bot-following workflow, commit conventions, and code style are
canonical in [AGENTS.md](AGENTS.md) (which `CLAUDE.md` imports)
and [notes/cycle-protocol.md](notes/cycle-protocol.md)

- [Cycle numbering](notes/cycle-protocol.md#numbering) — the
  `X.Y.Z-N` phase-suffix convention (Preparation / Work /
  Close-out).
- [Commit description](notes/cycle-protocol.md#commit-description)
  — Conventional Commits + `(version)` suffix and per-repo body
  shape.

Task tracking and release details live under [notes/](notes/):
near-term tasks in [notes/todo.md](notes/todo.md), per-release
details in `notes/chores/chores-*.md`, and notes-specific
formatting rules in [notes/README.md](notes/README.md).

## License

Licensed under either of

- Apache License, Version 2.0 ([LICENSE-APACHE](LICENSE-APACHE) or http://apache.org/licenses/LICENSE-2.0)
- MIT license ([LICENSE-MIT](LICENSE-MIT) or http://opensource.org/licenses/MIT)

### Contribution

Unless you explicitly state otherwise, any contribution intentionally submitted
for inclusion in the work by you, as defined in the Apache-2.0 license, shall
be dual licensed as above, without any additional terms or conditions.
