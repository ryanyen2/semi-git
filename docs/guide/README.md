# semi-git user guide

`sgt` tracks your codebase at the level of individual functions and classes, on top of git, so you
can remove or bring back one feature's worth of history on its own. Start here.

1. [The model](the-semantic-tree.md) explains how `sgt` represents your code: ops, the current
   state it calls the ideal, how that turns back into files, and the feature tree. Read this
   first.
2. [Getting started](getting-started.md) covers installing `sgt`, your first commit through it,
   removing and bringing back a symbol, and where its files live on disk.
3. [User workflows](workflows.md) walks through the solo loop, working with other people, agent
   sessions, and review, with real commands and real output. It also says where each feature is
   reliable today and where it still has limits.
4. [VS Code extension](vscode-extension.md) covers the in-editor feature blame, the feature map,
   and the revert preview.

Other material sits alongside this guide. `docs/brainstorms/` and `docs/ideation/` hold early
thinking, `docs/design/` holds design decisions, and `docs/plans/` holds the implementation plans.
