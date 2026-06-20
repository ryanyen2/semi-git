# Terminal UI

A compact terminal app for browsing the semantic tree, inspecting a feature, previewing a
plug-out, and applying graph ops — without leaving the shell.

## Install & run

```bash
uv pip install -e ".[tui]"     # pulls in Textual
sgt tui                        # run against the current repo
```

If Textual isn't installed, `sgt tui` tells you how to add it.

## Layout

```
 ┌ semi-git ─────────────────────────────────────────────────────────┐
 │ 5 features · 3 files · 18 effects            ✓ in sync             │
 ├──────────────────────────────────┬────────────────────────────────┤
 │ ● cap   normalize   lowercase…    │  ◆ lowercase + strip the domain │
 │ ● cap   validate    reject bad…   │  capability · active · a1b2     │
 │ ○ cap   ratelimit   throttle…     │                                 │
 │ ⚠ fix   retry       hold: …       │  depends on: validate           │
 │ …                                 │  effects: add_def normalize (…) │
 ├──────────────────────────────────┴────────────────────────────────┤
 │ F5 Refresh  / Filter  r Preview revert  s Preview suspend  X Revert │
 └─────────────────────────────────────────────────────────────────────┘
```

- **Left:** a filter box and the feature list. Status is the **glyph's shape** — `●` active,
  `○` planned, `◐` suspended (dimmed), `⚠` quarantined (red); the **hue is the feature's
  identity**, the same color it has in the editor gutter and the graph webview. No status-word
  column.
- **Right:** the selected feature's detail — intent, kind/status, dependencies, dependents,
  provenance, conflict witness, and effects. On a narrow terminal (< 100 cols) this pane folds
  away and `Enter` opens the detail as a full-screen modal instead.
- **Top:** counts, drift (`✓ in sync` or `⚠ drift: …`), and the filtered count when filtering.

## Keys

| Key | Action |
| --- | --- |
| `F5` | Refresh from disk |
| `/` | Focus the filter box |
| `r` | **Preview revert** (dry-run — nothing written) |
| `s` | **Preview suspend** (dry-run) |
| `X` | Revert the selected feature (confirm, then commit) |
| `O` / `U` | Suspend / restore the selected feature (confirm) |
| `q` | Quit |

The mutating ops are **uppercase** (`X`/`O`/`U`) to set them apart from the safe lowercase
previews (`r`/`s`) — a deliberate guardrail so a stray keystroke can't plug a feature out.

Previews show the per-file line delta as a toast and write nothing. Applying a graph op asks for
confirmation, then re-materializes and commits — the same path the CLI takes, including the
drift guard and the confluence gate.

## When to reach for which surface

- **TUI** — fast, keyboard-driven triage of the whole graph from a terminal; great over SSH.
- **[VS Code extension](vscode-extension.md)** — in-editor blame, heatmap, and diff previews
  while you're writing code.
- **[CLI](getting-started.md)** — scripting and the canonical surface; everything else is built
  on `sgt … --json`.
