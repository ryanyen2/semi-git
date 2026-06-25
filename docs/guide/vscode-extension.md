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
| Commit graph | **Feature Graph** — a row-based swim-lane graph in the bottom panel (+ a quick-nav sidebar tree) |
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

### Feature Graph

Modeled on GitLens's Commit Graph, but mapped from commits to **semantic nodes**. It lives in the
**bottom panel** (run **semi-git: Open Feature Graph**, or open the *Feature Graph* panel view).
The lightweight *Features* tree in the activity bar is the quick-nav companion.

Each feature is a **row** (most-derived on top), laid out like a git graph:

- **KIND column** — a colored ref-pill: the feature's identity hue (left accent) + its kind, with a
  status glyph. Status is a glyph, never the hue: `●` active, `○` planned, `◐` suspended (dimmed),
  `⚠` conflict (red). A legend sits in the toolbar.
- **GRAPH column** — git-style **swim lanes**: each feature is a node circle in its identity color,
  connected by colored bezier edges down to the features it depends on. Planned nodes render hollow;
  conflicts get a red ring.
- **FEATURE column** — the decision's **slug**: a short ~5-word human title (authored by `sgt plan`
  or distilled for landed work); the full decision sentence drops to the sub-line and detail pane.
- **Minimap** — a canvas activity ribbon (effects per feature) with status-colored markers; click
  to jump to a feature.
- **Header** — feature count, a drift chip (`✓ in sync` / `⚠ drifted`), and **live agent presence**.

**Live presence.** When your coding agent edits files, the affected features light up in
near-real-time — a `✎ editing` badge, a pulsing node halo, and a header indicator (`✎ agent editing
N: …`) — so you can watch the graph being worked, multiplayer-style. A just-checkpointed feature
flashes as it lands.

**Inspect in-situ — no popups.** Selecting a row (click, arrow keys + `Enter`, or a hover/tree
"Inspect") opens a **detail pane** beside the graph (never a modal). It carries, top to bottom:

- **Rationale (ADR)** — the decision's **Context / Decision / Consequence**. For a planned node these
  come from `sgt plan`; for landed work, a one-click **✦ Distill** button reconstructs them via the
  LLM (no-op offline). Status shows as a glyph + label (`○ Planned`, `● In force`, `● Landed`).
- **Structure** — a *deterministic*, analysis-derived description read straight from the entity call
  graph: what this decision **defines**, what those defs **use**, and what **uses** them. Unlike the
  ADR prose this can't drift or hallucinate — it's recomputed from code (or, for a planned node, its
  declared `provides`/`needs`). Distillation is fed this block as ground truth so the prose stays
  anchored to real structure.
- **Alternatives weighed**, the **git transaction** (commit chips), and the **footprint** (clickable
  entity chips that reveal the def), then the **actions**.

**Preview revert/suspend** opens a read-only diff; **Revert/Suspend** apply with a two-click inline
confirm (no modal) since they're reversible. **Search** dims non-matches in place.

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
