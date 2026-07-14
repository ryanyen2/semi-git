# VS Code extension

The extension shows which feature owns each line of code, draws the feature tree as a rail
alongside a commit timeline, and lets you merge, split, rename, move, or revert a feature from the
editor. It never edits your code. It calls `sgt`'s read views and its feature and kernel verbs.

## Install from source

```bash
cd editor/vscode
npm install
npm run compile        # type-check and bundle to dist/extension.js
```

Press `F5` in VS Code to launch an Extension Development Host, or package it with `npx vsce
package` and install the `.vsix`. The extension activates in any workspace that has
`.sgt/local/ideal.json`, which `sgt init` writes. It calls the `sgt` on your `PATH`, which you can
override with the `sgt.path` setting.

## What you get

| Surface | What it shows |
| --- | --- |
| Feature blame | A colored tint per line, by the feature that owns it (`blame_view`) |
| Feature map | A rail view of the feature tree: the hierarchy and a commit timeline, with a hover preview on every action that changes state |
| Plan CodeLens and status bar | Lines that match or drift from the active `sgt plan` session (U14) |
| Revert preview | A read-only diff of what reverting a feature would change, before you commit |

### Feature blame

Each line is tinted in its owning feature's color, on the background, the left border, and the
overview ruler. Hovering shows the feature's label and id. Toggle it with **semi-git: Toggle
Feature Blame** (`sgt.blame.enabled`). Colors come from the feature id through a deterministic
hash in `src/color.ts`, so the same feature always gets the same color in the gutter and in the
feature map, and the colors adjust to the theme for contrast.

### Feature map

**semi-git: Show Feature Map** (`sgt.showFeatureMap`) opens a webview panel with a rail view. The
layout follows the pattern in `experiments/patch_clustering/out/rail2.html`.

- The left column is the feature tree from `map_view`. Subsystems and features are listed in DFS
  order and indented by depth, each one collapsible, with a colored dot, a label, and a size bar.
- The right column is a shared commit-index axis from `history_view`. Every mined commit appears
  in order. Each feature has a lifebar from its first op to its last, and a glyph for each op at
  its commit index. The glyphs use the kernel's op-kind names: `◆` add, `+` extend, `~` rework,
  `−` prune, `⋔` move, `⋈` merge, and `·` touched.
- The connectors between features are cross-feature dependencies from `map_view`'s `edges`, which
  come from the coupling graph rolled up to feature pairs. Each node shows up to a threshold of
  them, and the overflow count is reported rather than dropped.

Hovering a row or an edge dims everything else and highlights the hovered node and its dependency
neighbors. Color always means identity. Status is shown by a glyph or a stroke, never by a second
color. Clicking a feature opens a detail panel with its label, rationale, and size, and an action
bar with Rename, Merge into, Split, Move ops, and Revert.

Hovering Split or Revert runs the real `plan_split` or `plan_revert_feature` preview live and
paints the features it would affect. For Revert this can cover more than the one feature you
named, because it is the real closure of the kernel edit rather than a guess. Merge into and Move
ops arm a mode where you pick a target. Hovering a candidate feature previews the merge or move
against it, and clicking confirms and applies it. Every preview is read-only, through `sgt preview <verb> ...
--json`. Only a click on Split or Revert, or a confirmed Merge or Move target, writes anything,
through `sgt merge`, `split --apply`, `rename`, `move`, or `revert`.

### Plan CodeLens and status bar

When a plan session is active, from `sgt plan intake`, matched and drifted lines get a one-line
CodeLens (`✦ matches plan step N` or `◇ drift`) that opens a diff of the step's intent against the
real edit. A status bar item shows step progress (`○` pending, `●` matched). Toggle it with
`sgt.plan.enabled`. Both are hidden when no session is active.

### Revert preview

From the feature map's action bar, or **semi-git: Preview Revert Feature**, open a read-only diff
of the current files against the predicted files after a revert. It runs `sgt revert <feature>
--emit` and writes nothing. If the revert is refused, for example because of a fork, it shows the
reason instead of a diff. **semi-git: Revert Feature** applies it for real after a confirmation,
then rebuilds the files and commits.

## Settings

| Setting | Default | Description |
| --- | --- | --- |
| `sgt.path` | `sgt` | Path to the `sgt` executable. |
| `sgt.blame.enabled` | `true` | Tint each line by the feature that owns it. |
| `sgt.plan.enabled` | `true` | Show a CodeLens on lines that match or drift from the active plan session. |

## How it talks to sgt

Every read runs `sgt <verb> --json` in the workspace root, using the JSON views in `sgt/api.py`.
Results are cached in `src/store.ts` and refreshed when a file under `.sgt/` changes or you save a
Python file. Writes call the same verbs the CLI calls. The feature map's hover preview and the
CLI's `sgt preview <verb> ...` read the same `feature_verb_preview_view` projection, so the
extension, the CLI, and MCP all read one schema.
