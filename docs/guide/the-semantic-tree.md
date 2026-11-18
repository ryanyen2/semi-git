# The model

Git tracks your codebase as text: lines, diffs, and commits. `ugh` tracks it as edits to
individual symbols, such as functions and classes, and rebuilds your files from those edits. This
page explains how that works. If you read one page in this guide, read this one.

## Ops

`ugh` reads each git commit, its own commits included, and breaks it into ops. An op is one change
to one definition (with the comments that follow it), the head of a file, or a whole file, for
example adding a function, editing its body, or removing it. Each op records which symbols it
touched, the version it started from, the version it produced, and any other ops it depends on. A
version is a hash of the symbol, the version it came from, and its content, so the ops on one
symbol form a simple chain: content that comes back later is a new version on the same chain. A
rename or a move to another file is one op that removes the old name and creates the new one.

Every op gets an id based on its content, so the same edit from the same starting point always
gets the same id: mining the history again gives the ids the store already holds, and a rebase that
keeps each symbol's sequence of contents keeps its ids. The ids depend on where mining started, so
the same commits mined from two different `ugh init --horizon` points give different ids. The full set of ops lives in `.sgt/ops/` and is committed to git
along with your code.

## The ideal

The current state of your codebase is a subset of all the ops, and `ugh` calls a valid subset an
ideal. To be valid, it has to follow two rules:

- If an op is included, every op it was built on has to be included too.
- For any one symbol, at most one version can be included at a time. Two competing versions of the
  same function is called a fork, and a valid ideal never contains one.

A branch's ideal is every op mined from its history. Nothing is ever subtracted from it: taking work
out (`ugh revert`) is a new commit whose ops un-apply that work, and its `Ugh-Out:` trailer names
the ops of the steps it took out, so what is out is read from git history like everything else.

## The fold

`ugh` turns an ideal back into files by stitching each live symbol's current code together in
order, with the surrounding text between symbols kept exactly as it was. The result is byte for
byte what ends up checked out. There is no separate copy of your files that can drift out of sync
with the ops. The files you see are just this ideal turned back into code.

`ugh check` checks that the current ideal is valid, and `ugh check --tree` that the files it
produces match what git actually has.

## Steps, names and addresses

`ugh` shows the ops as steps. A step is one save's change to one unit: a top-level definition with
the blank lines before it, a file's head, or a whole file. A save has a step on a unit only where
the unit's text changed, and the step's ops are that save's ops whose change shows there; a change
to the blank lines between two definitions belongs to the one after them. Every step prints with its
address, `path::def@<short sha>`, or `path@<short sha>` for a step on a whole file, a file's head or
an import.

A name is a label a person or an agent gave to some steps, with `ugh save --as <name>`, `ugh name
<address> <name>` or `ugh rename <name> <new>`. A step has one name or none. `ugh` never names
anything itself, and a name never grows by itself: a later edit to a named definition carries no
name unless it was saved with `--as` or named again. The names live in `.sgt/names.json` and
travel with your ops; `ugh sync` merges them per step, and when both sides renamed the same step
differently it keeps yours and prints both.

What stands on a name is not stored. It is the planner's answer to "what would it take to take this
out": the steps that would go, the steps held because other work stands on them (with the reason),
and the removal set, the smallest set of steps around the start that nothing outside stands on.
`ugh show <address>` prints that answer.

Every command that acts on steps takes an address: a name, `name@<rev>`, a path, `path::def`,
`path::def@<rev>` (one step), a `<rev>` (one save), or `<rev>..<rev>`. An address reaches steps that
are out as well as steps that are in, so `restore` takes the same addresses as `revert`.

## The one rule: ugh never writes your code

You or your coding agent write the code, in your editor, however you like. `ugh` reads those
edits, models them as ops, and rebuilds your working tree from the current ideal. The test for
whether something is `ugh`'s job is whether it would have to invent logic that was not already
there. If it would, that is the coding agent's job, not `ugh`'s.

One thing follows from this. Your working tree is always just the current ideal turned back into
files, and taking steps out is a new edit to the definitions they touched. That is why a removal
still looks clean months later.

## Drift

When you edit files directly, your working tree moves ahead of the ideal `ugh` has recorded. `ugh`
calls that gap drift. It detects drift and refuses to change state on top of edits it has not
recorded yet, so it never overwrites your work. Run `ugh save` to record the edits as new ops, and
the drift clears.
