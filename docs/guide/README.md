# ugh user guide

`ugh` tracks your codebase at the level of individual functions and classes, on top of git, so you
can name steps, take them out and bring them back on their own.

Read these in order.

1. [The model](the-semantic-tree.md) explains how `ugh` represents your code. It covers the
   per-symbol edits, the current state, how that state turns back into files, and names and
   addresses. Start here.
2. [Getting started](getting-started.md) covers installing `ugh`, your first commit through it,
   removing and bringing back a symbol, and where its files live on disk.
3. [User workflows](workflows.md) walks through the solo loop, working with other people, agent
   sessions, and review, with real commands and real output. It also says where each one is
   reliable today and where it still has limits.

The VS Code extension still reads the feature lanes and checkpoints this version removed; its guide
is in `docs/archive/guide/vscode-extension.md` until the extension is rewritten.

The other directories under `docs/` are historical design records rather than material for someone
new, e.g., `docs/design/` and `docs/plans/`. Read this guide first, and go to those only when you
want the reasoning behind a decision.
