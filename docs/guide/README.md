# semi-git user guide

`sgt` versions your codebase by its **features**, not its diffs. Start here.

1. **[The semantic tree](the-semantic-tree.md)** — the mental model. What's in the tree, the one
   rule (sgt never writes your code), the `plan → checkpoint → revert/switch/reconcile` workflow,
   and how the visual tools map onto it. **Read this first.**
2. **[Getting started](getting-started.md)** — install, your first feature, plugging features in
   and out, and where things live on disk.
3. **[VS Code extension](vscode-extension.md)** — semantic blame, feature heatmap, CodeLens, the
   feature DAG, and diff-based revision navigation, in-editor.
4. **[Terminal UI](tui.md)** — browse the graph, inspect, preview, and apply ops from the shell.
5. **[User workflows](workflows.md)** — solo, collaborative, and agent-session use cases with
   concrete commands and real output, including what's still being hardened.

Reference material lives alongside: `docs/brainstorms/` (what it is), `docs/plans/` (how it's
built), and `docs/design/` (design decisions).
