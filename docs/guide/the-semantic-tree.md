# The model

Git tracks your codebase as text: lines, diffs, and commits. `sgt` tracks it as symbol-level
edits and rebuilds the files from them. This page explains how that works. If you read one page,
read this one.

## Ops

`sgt` reads each git commit and breaks it into ops. An op is one change to one symbol, for
example adding a function, editing its body, or removing it. Each op records which symbol it
touched, the version it started from, the version it produced, and any other ops it depends on.
`sgt` follows a symbol as it moves between files or gets renamed, so edits to the same logical
function stay on one chain even when the text and location change.

Every op has an id derived from its content, so the same edit always gets the same id, and two
different edits never collide. The whole set of ops lives in `.sgt/ops/` and is committed to git
along with your code.

## The ideal

The current state of your codebase is an ideal. An ideal is a subset of all the ops, with two
rules:

- It is closed downward. If an op is in the ideal, then every op it was built on is also in.
- It is fork-free. For any one symbol, at most one version is in the ideal at a time. Two
  competing versions of the same function is a fork, and an ideal never contains one.

These two rules are what make removal clean. When you remove an op, `sgt` also removes everything
that was built on it, so the result is still a valid ideal and still folds back to real files.

## The fold

`code(ideal)` folds the ideal back into files. It stitches each live symbol's bytes together in
order, with the surrounding text between symbols preserved exactly. The output is byte for byte
what is checked out. There is no separate copy of your files that can drift from the ops. The
files are the fold of the current ideal.

`sgt fsck` checks that the ideal is valid and that the fold matches what git has.

## The feature tree

Ops are fine-grained. A feature tree groups them into features so you can work at the level you
think in. `sgt map` builds the tree by clustering symbols that change together and that reference
each other across your history. Each feature gets an id like `F3` and a label, and the ids stay
stable across rebuilds so a feature keeps its identity as the code grows.

The automatic grouping needs history to work from. On a brand-new repo there is not enough signal
to split features apart, so `sgt map` reports one feature for everything. As real commits
accumulate, the seams appear. You can correct or seed the grouping by hand at any time with `sgt
merge`, `sgt split`, `sgt rename`, and `sgt move`. These change labels and grouping only. They
never touch your code.

## The one rule: sgt never writes your code

You or your coding agent write the code, in your editor, however you like. `sgt` reads those
edits, models them as ops, and rebuilds the working tree from the ideal. The test for whether a
job belongs to `sgt` is whether it would invent logic that was not there. If it would, that is
the coding agent's job.

One thing follows from this. The working tree is the fold of the ideal. Reverting a feature drops
that feature's ops and folds the ideal again. That is why a removal stays clean months later.

## Drift

When you edit files directly, the working tree moves ahead of the recorded ideal. `sgt` calls the
difference drift. It detects drift and refuses to change state on top of unrecorded edits, so it
never overwrites your work. Run `sgt save` to record the edits as new ops, and the drift clears.

## How the visual tools map to this

The [VS Code extension](vscode-extension.md) reads the same model through `sgt <verb> --json`.

- Feature blame shows which feature owns each symbol in a file. It is the per-feature version of
  `git blame`.
- The feature map shows the tree itself, with each feature in its own color and a timeline of its
  ops across commits.
- The revert preview runs `sgt revert <feature> --emit` and shows what a removal would change,
  before you commit to it.
