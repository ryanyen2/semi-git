# semi-git user guide

`sgt` tracks your codebase at the level of individual functions and classes, on top of git, so you
can remove or bring back one feature's worth of history on its own.

Read these in order.

1. [The model](the-semantic-tree.md) explains how `sgt` represents your code. It covers the
   per-symbol edits, the current state, how that state turns back into files, and the feature tree.
   Start here.
2. [Getting started](getting-started.md) covers installing `sgt`, your first commit through it,
   removing and bringing back a symbol, and where its files live on disk.
3. [User workflows](workflows.md) walks through the solo loop, working with other people, agent
   sessions, and review, with real commands and real output. It also says where each feature is
   reliable today and where it still has limits.
4. [VS Code extension](vscode-extension.md) covers the in-editor feature blame, the feature map, and
   the revert preview.

The other directories under `docs/` are historical design records rather than material for someone
new, e.g., `docs/design/` and `docs/plans/`. Read this guide first, and go to those only when you
want the reasoning behind a decision.
