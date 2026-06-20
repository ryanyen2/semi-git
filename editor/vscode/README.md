# semi-git for VS Code

Read your code by **feature**, not by diff. This extension is a GitLens-style lens onto the
`sgt` semantic graph: it shows which feature owns each line, lets you see the whole feature
DAG, and lets you plug features in and out — all in-situ, with color and shape doing the work
instead of labels.

It never edits your code. `sgt` records and reorganizes the *semantic graph*; your coding
agent (or you) writes the code, then checkpoints it. This extension visualizes that graph and
drives `sgt`'s read and graph-op verbs.

## Features

- **Semantic blame** — the current line shows the feature that owns it (end-of-line annotation
  + status bar). Hover any line for the feature's intent, dependencies, dependents, and any
  conflict, plus one-click *preview suspend / preview revert*.
- **Feature heatmap** (`semi-git: Toggle Feature Heatmap`) — a per-feature gutter band and
  overview-ruler color across the whole file. Each feature has a stable, theme-aware **OKLCH**
  color (golden-angle hash of its id), identical in the editor, the graph, and the terminal UI.
- **Feature CodeLens** — a lens above each block naming the feature that owns it and how many
  features depend on it.
- **Feature DAG** — a sidebar tree and a full graph webview (`semi-git: Open Feature Graph`)
  with crossing-reduced dependency layers and routed edges. Hue is identity; **status is a glyph**
  (`●` active, `○` planned, `◐` suspended, `⚠` conflict), with a legend in the toolbar. Scroll to
  zoom, drag to pan, **Fit** to frame; arrow keys + `Enter` navigate; click to inspect. Nodes
  animate to new positions on `plan`/`reconcile` (respecting reduced-motion).
- **Revision navigation** — *preview revert* / *preview suspend* open a read-only diff of what
  the change would do to the working tree, computed by `sgt emit` without writing anything.

## Requirements

- The [`sgt`](../../README.md) CLI on your `PATH` (or set `sgt.path`).
- A workspace initialized with `sgt init` (the extension activates when it finds `.sgt/graph.json`).

## Settings

| Setting | Default | Description |
| --- | --- | --- |
| `sgt.path` | `sgt` | Path to the `sgt` executable. |
| `sgt.blame.enabled` | `true` | Current-line semantic blame annotation. |
| `sgt.heatmap.enabled` | `false` | Whole-file per-feature gutter + ruler heatmap. |
| `sgt.codeLens.enabled` | `true` | CodeLens naming the feature above each block. |

## Develop

```bash
npm install
npm run compile      # type-check + bundle to dist/extension.js
npm run watch        # rebuild on change
```

Press `F5` in VS Code to launch an Extension Development Host against this folder.
