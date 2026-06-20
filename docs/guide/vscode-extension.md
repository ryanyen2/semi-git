# VS Code extension

A GitLens-style lens onto the semantic tree. It shows which **feature** owns each line, draws
the feature DAG, and lets you plug features in and out — in-situ, with color and shape doing the
work instead of labels. It never edits your code; it drives `sgt`'s read and graph-op verbs.

## Install (from source)

```bash
cd editor/vscode
npm install
npm run compile        # type-check + bundle to dist/extension.js
```

Then press `F5` in VS Code to launch an Extension Development Host, or package it with
`npx vsce package` and install the `.vsix`. The extension activates automatically in any
workspace that contains a `.sgt/graph.json`. It calls the `sgt` on your `PATH` — override with
the `sgt.path` setting.

## What you get

| GitLens concept | semi-git equivalent |
| --- | --- |
| Inline / status-bar blame | **Semantic blame** — the current line's owning feature, end-of-line + status bar |
| Git CodeLens | **Feature CodeLens** — the feature above each block + its dependent count |
| Rich hovers | **Feature hover** — intent, deps, dependents, conflict, and *preview suspend/revert* links |
| File heatmap | **Feature heatmap** — per-feature gutter band + overview-ruler color across the file |
| Commit graph | **Feature DAG** — sidebar tree + a full graph webview |
| Revision navigation | **Preview revert / suspend** — a read-only diff of what the op would do |

### Semantic blame

The active line shows a quiet `◆ <feature intent>` annotation, and the status bar shows the
owner. Hover any line for the full detail and one-click previews. Toggle with **semi-git: Toggle
Line Blame**. Attribution is exact down to the statement — an edited line belongs to the fix
node that changed it, not the function's original author.

### Feature heatmap

**semi-git: Toggle Feature Heatmap** tints the whole file: a colored gutter band per contiguous
feature and a matching band on the overview ruler, so you see the distribution of features at a
glance. Each feature's color is a stable hash of its id — the same hue in the editor, the graph,
and everywhere else.

### Feature DAG

The **semi-git** activity-bar icon opens the *Feature DAG* sidebar (a tree rooted on the features
nothing depends on, conflicts and planned nodes surfaced first). **semi-git: Open Feature Graph**
opens the full graph webview: dependency layers with crossing-reduced ordering, long edges routed
around intervening nodes, and a stable identity hue per feature.

- **Status** is a glyph, never the hue (hue is identity): `●` active, `○` planned, `◐` suspended
  (dimmed), `⚠` conflict (red). A legend sits in the toolbar.
- **Navigate** by scroll-wheel zoom, drag to pan, and **Fit** to frame the whole graph. The graph
  also animates nodes to their new positions when you `plan`/`reconcile`, so you can see what moved.
- **Keyboard:** `Tab`/arrow keys move between nodes, `Enter`/`Space` inspects the focused one.
- **Click** a node to inspect it (the inspector offers preview-revert / preview-suspend). **Filter**
  by name in the toolbar — non-matches dim in place so the layout stays put.

### Revision navigation

From a hover, the node inspector, the graph, or the sidebar context menu, choose **Preview
revert** or **Preview suspend** to open a read-only diff (current vs. predicted) of exactly what
the op would change — computed by `sgt emit` without writing anything. A refusal (e.g. a
dependent still needs the feature) shows the reason instead of a diff. **Revert** / **Suspend**
/ **Restore** apply for real, after a confirmation, and re-materialize + commit.

## Settings

| Setting | Default | Description |
| --- | --- | --- |
| `sgt.path` | `sgt` | Path to the `sgt` executable. |
| `sgt.blame.enabled` | `true` | Current-line semantic blame annotation. |
| `sgt.heatmap.enabled` | `false` | Whole-file per-feature gutter + ruler heatmap. |
| `sgt.codeLens.enabled` | `true` | CodeLens naming the feature above each block. |

## How it talks to sgt

Every read shells out to `sgt <verb> --json` (the [canonical JSON projection](the-semantic-tree.md))
in the workspace root; results are cached and refreshed when `.sgt/*.json` changes or you save a
Python file. Mutations call the same verbs the CLI does. There is no separate state — the
extension, the TUI, the CLI, and MCP all read one schema.
