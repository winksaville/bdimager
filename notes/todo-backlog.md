# Todo Backlog

This file uses [Prose form](../AGENTS.md#prose-form). It holds
lower-priority `## Todo` entries — the long tail. When an
entry becomes a priority, move it (and any refs it cites)
into `notes/todo.md > ## Todo` at its priority rank (the
list is strict-ranked, #1 highest), then `fix-todo` to
renumber.

Same formatting rules as `notes/todo.md > ## Todo` — see
[Todo format](../AGENTS.md#todo-format). Run
`vc-x1 fix-todo --no-dry-run notes/todo-backlog.md` to
renumber.

## Todo

1. Rename the protocol from "cycle" to **dev-protocol** and
   collapse the vocabulary onto the ladder recursion. "cycle"
   implies a loop; a ladder is linear, and "sub-cycle" is just
   a step that expands into its own ladder. The rename is
   pervasive (doc title, `AGENTS.md`, `versioning.md`,
   `todo.md`, `vc-x1`), so it is deferred. Key moves:
   - define "a ladder is a series of steps" once at the top
   - drop "sub-cycle"
   - keep the phase roles (Preparation `-0` / Work `-N` /
     Close-out bare) as a ladder's *anatomy*
   - split structural recursion (universal) from
     publishing / bookkeeping (top-level only)
   - disambiguate the overloaded "cycle" (published unit vs
     per-commit validation vs informal dev loop)
