# Todo

This file uses [Prose form](../AGENTS.md#prose-form). It
contains near term tasks with a short description and
uses links or reference links for more details.

## In Progress

_No cycle currently in progress._

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

- Port fc's image builder into bdimager (generalized) [[1]]

# References

[1]: chores/chores-01.md#chore-close-out-030-image-builder-port
