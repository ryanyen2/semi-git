# semi-git user guide

`sgt` tracks your codebase as a set of symbol-level edits on top of git, so you can remove or
re-add one feature's worth of history on its own. Start here.

1. [The model](the-semantic-tree.md) explains how `sgt` represents your code: ops, the ideal,
   the fold back to files, and the feature tree. Read this first.
2. [Getting started](getting-started.md) covers install, your first commit through `sgt`,
   removing and re-adding a symbol, and where files live on disk.
3. [User workflows](workflows.md) walks through the solo loop, working with other people, agent
   sessions, and review, with real commands and output. It also says where each feature is
   reliable today and where it is not.
4. [VS Code extension](vscode-extension.md) covers the in-editor feature blame, the feature map,
   and the revert preview.

Reference material sits alongside this guide. `docs/brainstorms/` and `docs/ideation/` hold early
thinking, `docs/design/` holds design decisions, and `docs/plans/` holds the implementation
plans.
