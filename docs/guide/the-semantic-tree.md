# The model

Git tracks your codebase as text: lines, diffs, and commits. `sgt` tracks it as edits to
individual symbols, such as functions and classes, and rebuilds your files from those edits. This
page explains how that works. If you read one page in this guide, read this one.

## Ops

`sgt` reads each git commit and breaks it into ops. An op is one change to one symbol, for example
adding a function, editing its body, or removing it. Each op records which symbol it touched, the
version it started from, the version it produced, and any other ops it depends on. `sgt` follows a
symbol as it moves between files or gets renamed, so edits to the same function stay on one chain
even after the text and the file location change.

Every op gets an id based on its content, so the same edit always gets the same id, and two
different edits never collide. The full set of ops lives in `.sgt/ops/` and is committed to git
along with your code.

## The ideal

The current state of your codebase is a subset of all the ops, and `sgt` calls a valid subset an
ideal. To be valid, it has to follow two rules:

- If an op is included, every op it was built on has to be included too.
- For any one symbol, at most one version can be included at a time. Two competing versions of the
  same function is called a fork, and a valid ideal never contains one.

These two rules are what make removal clean. When you remove an op, `sgt` also removes everything
that was built on it, so what is left is still a valid ideal, and it still turns back into real
files without any gaps.

## The fold

`sgt` turns an ideal back into files by stitching each live symbol's current code together in
order, with the surrounding text between symbols kept exactly as it was. The result is byte for
byte what ends up checked out. There is no separate copy of your files that can drift out of sync
with the ops. The files you see are just this ideal turned back into code.

`sgt fsck` checks that the current ideal is valid and that the files it produces match what git
actually has.

## The feature tree

Ops are small, one per symbol edit. The feature tree groups them into features, so you can think
and work at a larger scale than one function at a time. `sgt map` builds this tree by clustering
symbols that tend to change together and that reference each other across your history. Each
feature gets an id like `F3` and a label, and the ids stay the same across rebuilds, so a feature
keeps its identity as your code grows.

This automatic grouping needs history to learn from. On a brand-new repo there is not enough
signal yet to split features apart, so `sgt map` reports one feature for everything. As real
commits build up, the seams start to appear. You can correct or seed the grouping by hand at any
time with `sgt merge`, `sgt split`, `sgt rename`, and `sgt move`. These only change labels and
grouping. They never touch your code.

## The one rule: sgt never writes your code

You or your coding agent write the code, in your editor, however you like. `sgt` reads those
edits, models them as ops, and rebuilds your working tree from the current ideal. The test for
whether something is `sgt`'s job is whether it would have to invent logic that was not already
there. If it would, that is the coding agent's job, not `sgt`'s.

One thing follows from this. Your working tree is always just the current ideal turned back into
files. Reverting a feature drops that feature's ops and rebuilds the files from what is left. That
is why a removal still looks clean months later.

## Drift

When you edit files directly, your working tree moves ahead of the ideal `sgt` has recorded. `sgt`
calls that gap drift. It detects drift and refuses to change state on top of edits it has not
recorded yet, so it never overwrites your work. Run `sgt save` to record the edits as new ops, and
the drift clears.

## How the visual tools use this model

The [VS Code extension](vscode-extension.md) reads this same model through `sgt <command>
--json`.

- Feature blame shows which feature owns each symbol in a file. It is the per-feature version of
  `git blame`.
- The feature map shows the tree itself, with each feature in its own color and a timeline of its
  ops across commits.
- The revert preview runs `sgt revert <feature> --emit` and shows what a removal would change,
  before you commit to it.
