# VS Code extension

A GitLens-style lens onto the operation-ideal kernel and its feature tree. It shows which
**feature** owns each line, visualizes the feature tree as a rail alongside a shared commit-index
timeline, and lets you merge/split/rename/move features or revert one — in-situ, with color and
glyph doing the work instead of labels. It never edits your code directly; it drives `sgt`'s read
views and feature/kernel verbs.

## Install (from source)

```bash
cd editor/vscode
npm install
npm run compile        # type-check + bundle to dist/extension.js
```

Then press `F5` in VS Code to launch an Extension Development Host, or package it with
`npx vsce package` and install the `.vsix`. The extension activates automatically in any workspace
containing `.sgt/local/ideal.json` (written by `sgt init`). It calls the `sgt` on your `PATH` —
override with the `sgt.path` setting.

## What you get

| Surface | What it shows |
| --- | --- |
| Semantic blame | A colored gutter/border tint per line, by the feature that owns it (`blame_view`) |
| **Feature Map** | A rail visualization of the feature tree: hierarchy + a commit-index timeline, with hover-preview on every mutating action |
| Plan CodeLens + status bar | Lines matched or drifted from the active `sgt plan` session (U14) |
| Preview revert | A read-only diff of what reverting a feature would change, before you commit to it |

### Semantic blame

Each line is tinted (background + left border + overview-ruler mark) in its owning feature's
identity color; hovering shows the feature's label and id. Toggle with **semi-git: Toggle Feature
Blame** (`sgt.blame.enabled`). Colors are generated deterministically from the feature id via an
OKLCH golden-angle hash (`src/color.ts`), theme-aware for contrast — the same hue appears in the
gutter and in the Feature Map.

### Feature Map

**semi-git: Show Feature Map** (`sgt.showFeatureMap`) opens a webview panel with a rail
visualization, redesigned around the pattern explored in
`experiments/patch_clustering/out/rail2.html`:

- **Left column** — the feature tree (`map_view`'s nodes): subsystems and features, DFS-ordered
  and depth-indented, each collapsible; a feature's identity-colored dot, its label, and a size
  bar.
- **Right column** — a shared commit-index axis (`history_view`): every mined commit in order,
  with each feature's lifebar (its first→last op on that axis) and a glyph per op at its
  commit-index, using the kernel's real op-kind vocabulary: `◆` add, `+` extend, `~` rework,
  `−` prune, `⋔` move, `⋈` merge, `·` touched.
- **Edges** — cross-feature structural dependency connectors (`map_view`'s `edges`, the fused
  structural/co-change/scope coupling graph rolled up to feature pairs), thresholded per node with
  the overflow reported rather than silently dropped.

**Interaction.** Hovering a row or edge dims everything else and lights the hovered node plus its
dependency neighbors (color still only ever means identity; status is always a glyph or a stroke
treatment, never a second hue). Clicking a feature opens a detail panel with its label, rationale,
size, and an action bar: **Rename, Merge into…, Split, Move ops…, Revert**. Hovering **Split** or
**Revert** runs the real `plan_split`/`plan_revert_feature` preview live and paints the actual
affected features as a blast-radius ghost — for Revert this can genuinely span more than the one
feature named, since it is the real upset-closure of the kernel edit, not a guessed dependency
edge. **Merge into…**/**Move ops…** arm a "pick target" mode: hovering a candidate feature
live-previews the merge/move against it; clicking confirms and applies. Every preview is
side-effect-free (`sgt preview <verb> ... --json`); only a click on Split/Revert or a confirmed
Merge/Move target actually writes (`sgt merge`/`split --apply`/`rename`/`move`/`revert`).

### Plan CodeLens + status bar

When a plan session is active (`sgt plan intake`), matched and drifted lines get a one-line
CodeLens (`✦ matches plan step N` / `◇ drift`) that opens a diff of the step's intent against the
real edit, and a status bar item shows step progress (`○` pending / `●` matched). Toggle with
`sgt.plan.enabled`. Both are invisible with no active session.

### Preview revert

From the Feature Map's action bar (or **semi-git: Preview Revert Feature**), open a read-only
diff (current vs. predicted) of exactly what reverting a feature would change — computed by
`sgt revert <feature> --emit` without writing anything. A refusal (e.g. a chain fork) shows the
reason instead of a diff. **semi-git: Revert Feature** applies for real, after a confirmation, and
re-materializes + commits.

## Settings

| Setting | Default | Description |
| --- | --- | --- |
| `sgt.path` | `sgt` | Path to the `sgt` executable. |
| `sgt.blame.enabled` | `true` | Tint whole files by the feature that owns each span. |
| `sgt.plan.enabled` | `true` | Show a CodeLens above lines matched or drifted from the active plan session. |

## How it talks to sgt

Every read shells out to `sgt <verb> --json` (the canonical JSON projection in `sgt/api.py`) in
the workspace root; results are cached in `src/store.ts` and refreshed when `.sgt/**/*.json`
changes or you save a Python file. Mutations call the same verbs the CLI does — the Feature Map's
hover-preview and the CLI's `sgt preview <verb> ...` read the identical `feature_verb_preview_view`
projection, so there is no separate state: the extension, the TUI, the CLI, and MCP all read one
schema.
