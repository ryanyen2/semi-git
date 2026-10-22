# ugh for VS Code

Read your code by **feature**, not by diff. This extension is a full ugh workbench: it shows which
feature owns each line, draws the feature tree as a rail alongside a commit timeline with a
draggable playhead, lets you revert or restore a feature or one of its checkpoints from the
editor, and drives ugh's git bridge (`switch`/`save`/`undo`/`sync`/`push`/`land`) so day-to-day git
work never has to leave VS Code.

It never edits your code directly. `ugh` records and reorganizes the *semantic op graph*; your
coding agent (or you) writes the code, then checkpoints it. This extension visualizes that graph
and drives `ugh`'s read views and its feature, kernel, and git-bridge verbs.

## Features

- **Feature blame** — a colored tint per line, on the background, left border, and overview ruler,
  by the feature that owns it. Hover for the feature's label and id. Colors are a deterministic
  OKLCH hash of the feature id, identical across the editor, the workbench, and the terminal UI.
- **Composition Workbench** (what the ugh Workbench panel opens on; `ugh: Open Composition
  Workbench`) — the timeline: one lane per feature in its own hue, its checkpoints as blocks on a
  dated axis, counts in changes (one function in one save), functions and saves, never in ops.
  Selecting a feature or a checkpoint shows what it changed, down to the lines; taking work out
  or putting it back is previewed in place and confirmed from a bar docked under the rows. The
  playhead has mass: drag it and it rests on saves, lingering where a checkpoint begins or ends;
  flick it and it coasts to a save; the arrow keys step one save, Shift one checkpoint, Esc back to
  now. Right of the now line, the forecast band names who has work coming, one column each: your
  unsaved edits, every live `ugh session` (breathing while it runs, amber where two touched the
  same function), and every agent's plan with its progress; click a column to follow that actor.
- **Ledger** (the workbench's Ledger button; `ugh: Open Ledger`) — the same history as a
  document: one line per checkpoint, newest first, the feature lanes in the gutter. Open a row for
  the words that asked for it, the functions it touched, its saves and the exact lines; take a
  checkpoint out or put it back from a margin note that reads the consequence first; ⌘-click rows
  to take several out as one gesture. Its Timeline button swaps the panel back.
- **Activity bar** (`ugh`) — three tree views: **Now** (what is unsaved, what needs you,
  what you just did), **Features** (the feature/subsystem tree), **Changes** (drift, unmanaged
  paths, the trust queue).
- **Fork resolution** — an N-column view of a fork's tip images, plus the `merge-op` → hand-edit →
  `fulfill` → `land` wizard, entirely through real kernel verbs.
- **Hovers, diagnostics, inlay hints** — a symbol hover with label, rationale, and cross-feature
  coupling; a Hint on drifted spans (with a "Save to clear" quick-fix) and a Warning on forked
  ones; an opt-in `‹feature ·N ops›` inlay hint at each definition.
- **Plan CodeLens and status bar** — lines that match or drift from an active `ugh plan` session.
- **Revert preview** — a read-only diff of what reverting a feature would change, via `ugh revert
  --emit`, before you commit to it.
- **Git bridge** — palette commands and an always-visible status-bar oracle chip for
  `switch`/`save`/`undo`/`sync`/`push`/`land`, so `git checkout`/`stash`/`reset`/`pull`/`push`
  never have to run directly against a `.sgt`-tracked repo.

## Using it

In short: code as usual, and glance at the `ugh` icon in the activity bar whenever you want
to see what's going on. Colored stripes in the gutter show which feature owns each line. Open
**ugh: Open Composition Workbench** for the full picture (feature tree + timeline +
inspector), drag its playhead to see the code at any past point, and use the six palette commands
(Switch/Save/Undo/Sync/Push/Land) instead of typing git commands directly. A plain,
step-by-step walkthrough of a real session — first open, everyday use, resolving a conflict, and
so on — is in `docs/guide/vscode-extension.md` in the main repo.

## Requirements

Install the `ugh` command, then point this extension at it.

```bash
uv tool install ugh
cd your-project
ugh init --agent
```

`ugh init --agent` writes `.vscode/settings.json` with an absolute `ugh.path`. The absolute path
matters, because a VS Code started from your Dock or Applications folder inherits the login shell's
PATH rather than your terminal's, and `ugh` usually lives in a directory only your terminal knows
about. If the extension can't run `ugh`, it tells you and offers to open the setting.

A workspace with no `.sgt` store yet still activates the extension. The Features view shows an
**Initialize ugh** welcome action instead of an error.

## Settings

| Setting | Default | Description |
| --- | --- | --- |
| `ugh.path` | `ugh` | Path to the `ugh` executable. |
| `ugh.blame.enabled` | `true` | Tint each line by the feature that owns it. |
| `ugh.plan.enabled` | `true` | Show a CodeLens on lines that match or drift from the active plan session. |
| `ugh.inlayHints.enabled` | `false` | Show a `‹feature-label ·N ops›` inlay hint at each symbol's definition line. |
| `ugh.diagnostics.drift` | `true` | Show a Hint diagnostic (with a "Save to clear" quick-fix) on drifted spans. |
| `ugh.diagnostics.forks` | `true` | Show a Warning diagnostic on symbols with an open fork. |

See `docs/guide/vscode-extension.md` in the main repo for the full surface-by-surface reference.

## Develop

```bash
npm install
npm run compile      # type-check + bundle to dist/extension.js
npm run watch        # rebuild on change
```

Press `F5` in VS Code to launch an Extension Development Host against this folder.
