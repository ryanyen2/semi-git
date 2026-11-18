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
out (`ugh revert`) is a new commit whose ops un-apply that work, and its `Ugh-Out:` trailer records
which steps it took out, so what is out is read from git history like everything else.

## The fold

`ugh` turns an ideal back into files by stitching each live symbol's current code together in
order, with the surrounding text between symbols kept exactly as it was. The result is byte for
byte what ends up checked out. There is no separate copy of your files that can drift out of sync
with the ops. The files you see are just this ideal turned back into code.

`ugh check` checks that the current ideal is valid, and `ugh check --tree` that the files it
produces match what git actually has.

## The feature tree

Ops are small, one per symbol edit. The feature tree groups them into features, so you can think
and work at a larger scale than one function at a time. `ugh log --refresh` builds this tree by clustering
symbols that tend to change together and that reference each other across your history. Each
feature gets an id like `F3` and a label, and the ids stay the same across rebuilds, so a feature
keeps its identity as your code grows.

This automatic grouping needs history to learn from. On a brand-new repo there is not enough
signal yet to split features apart, so `ugh log` lists one feature for everything. As real
commits build up, the seams start to appear. You can name a feature in your own words at any time
with `ugh feature rename`. It only changes the label and never touches your code.

## The one rule: ugh never writes your code

You or your coding agent write the code, in your editor, however you like. `ugh` reads those
edits, models them as ops, and rebuilds your working tree from the current ideal. The test for
whether something is `ugh`'s job is whether it would have to invent logic that was not already
there. If it would, that is the coding agent's job, not `ugh`'s.

One thing follows from this. Your working tree is always just the current ideal turned back into
files. Reverting a feature drops that feature's ops and rebuilds the files from what is left. That
is why a removal still looks clean months later.

## Drift

When you edit files directly, your working tree moves ahead of the ideal `ugh` has recorded. `ugh`
calls that gap drift. It detects drift and refuses to change state on top of edits it has not
recorded yet, so it never overwrites your work. Run `ugh save` to record the edits as new ops, and
the drift clears.

## How the visual tools use this model

The [VS Code extension](vscode-extension.md) reads this same model through `ugh <command>
--json`.

- Feature blame shows which feature owns each symbol in a file. It is the per-feature version of
  `git blame`.
- The feature map shows the tree itself, with each feature in its own color and a timeline of its
  ops across commits.
- The revert preview runs `ugh revert <feature> --emit` and shows what a removal would change,
  before you commit to it.
