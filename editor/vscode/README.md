# semi-git for VS Code

Read your code by **feature**, not by diff. This extension is a full sgt workbench: it shows which
feature owns each line, draws the feature tree as a rail alongside a commit timeline with a
draggable playhead, lets you merge/split/rename/move/revert a feature or resolve a fork from the
editor, and drives sgt's git bridge (`switch`/`save`/`undo`/`sync`/`push`/`land`) so day-to-day git
work never has to leave VS Code.

It never edits your code directly. `sgt` records and reorganizes the *semantic op graph*; your
coding agent (or you) writes the code, then checkpoints it. This extension visualizes that graph
and drives `sgt`'s read views and its feature, kernel, and git-bridge verbs.

## Features

- **Feature blame** — a colored tint per line, on the background, left border, and overview ruler,
  by the feature that owns it. Hover for the feature's label and id. Colors are a deterministic
  OKLCH hash of the feature id, identical across the editor, the workbench, and the terminal UI.
- **Composition Workbench** (`semi-git: Open Composition Workbench`) — a full-window rail (feature
  tree + commit timeline) and inspector, grounded in a GitKraken/GitHub-Graph-style layout. A
  draggable playhead scrubs any commit-index frontier and re-folds `code(I)` + the oracle verdict
  live, without materializing the working tree. Hovering an action paints its blast/foundation
  effect before you commit to it.
- **Activity bar** (`semi-git`) — four tree views: **Features** (the feature/subsystem tree),
  **Forks** (the conflict inbox, badge = open count), **Changes** (drift, unmanaged paths, the
  trust queue), **Compositions** (sessions and proposals — the switch/land/publish surface).
- **Fork resolution** — an N-column view of a fork's tip images, plus the `merge-op` → hand-edit →
  `fulfill` → `land` wizard, entirely through real kernel verbs.
- **Hovers, diagnostics, inlay hints** — a symbol hover with label, rationale, and cross-feature
  coupling; a Hint on drifted spans (with a "Save to clear" quick-fix) and a Warning on forked
  ones; an opt-in `‹feature ·N ops›` inlay hint at each definition.
- **Plan CodeLens and status bar** — lines that match or drift from an active `sgt plan` session.
- **Revert preview** — a read-only diff of what reverting a feature would change, via `sgt revert
  --emit`, before you commit to it.
- **Git bridge** — palette commands and an always-visible status-bar oracle chip for
  `switch`/`save`/`undo`/`sync`/`push`/`land`, so `git checkout`/`stash`/`reset`/`pull`/`push`
  never have to run directly against a `.sgt`-tracked repo.

## Using it

In short: code as usual, and glance at the `semi-git` icon in the activity bar whenever you want
to see what's going on. Colored stripes in the gutter show which feature owns each line. Open
**semi-git: Open Composition Workbench** for the full picture (feature tree + timeline +
inspector), drag its playhead to see the code at any past point, and use the six palette commands
(Switch/Save/Undo/Sync/Push/Land) instead of typing git commands directly. A plain,
step-by-step walkthrough of a real session — first open, everyday use, resolving a conflict, and
so on — is in `docs/guide/vscode-extension.md` in the main repo.

## Requirements

- The [`sgt`](../../README.md) CLI on your `PATH` (or set `sgt.path`).
- A workspace initialized with `sgt init`. A workspace with no `.sgt` store yet still activates the
  extension — the Features view shows an **Initialize semi-git** welcome action instead of an
  error.

## Settings

| Setting | Default | Description |
| --- | --- | --- |
| `sgt.path` | `sgt` | Path to the `sgt` executable. |
| `sgt.blame.enabled` | `true` | Tint each line by the feature that owns it. |
| `sgt.plan.enabled` | `true` | Show a CodeLens on lines that match or drift from the active plan session. |
| `sgt.inlayHints.enabled` | `false` | Show a `‹feature-label ·N ops›` inlay hint at each symbol's definition line. |
| `sgt.diagnostics.drift` | `true` | Show a Hint diagnostic (with a "Save to clear" quick-fix) on drifted spans. |
| `sgt.diagnostics.forks` | `true` | Show a Warning diagnostic on symbols with an open fork. |

See `docs/guide/vscode-extension.md` in the main repo for the full surface-by-surface reference.

## Develop

```bash
npm install
npm run compile      # type-check + bundle to dist/extension.js
npm run watch        # rebuild on change
```

Press `F5` in VS Code to launch an Extension Development Host against this folder.
