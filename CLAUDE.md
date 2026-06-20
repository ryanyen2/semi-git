# CLAUDE.md — working in semi-git (`sgt`)

`sgt` is **git for semantics**: a semantic DAG over a git repo. Read `README.md` for the pitch and
`FINDINGS.md` for what's verified. This file is the orientation for an agent editing the codebase.

## The one rule

**sgt never authors code.** It plans, records (distills edits into typed effects), and reorganizes
the *semantic graph*, then reconstructs the tree from it. The coding agent (or a human) writes the
code; `sgt` versions it by feature. The LLM is used only for graph reasoning (decompose a plan,
label a checkpoint) and degrades to deterministic behavior with no API key. Don't reintroduce a
code-authoring path. See `docs/design/2026-06-19-graph-only-agent-driven-sgt.md`.

## Build & test

```bash
uv venv --python 3.12 && uv pip install -e ".[dev]"   # core + dev
uv pip install -e ".[tui]"                            # + Textual, for `sgt tui`
uv run pytest -q                                      # full suite (251 tests)
uv run python scripts/e2e_plan_checkpoint.py          # live graph-only walkthrough
uv run python scripts/e2e_ui_surfaces.py              # live UI-surface walkthrough (offline)
```

The VS Code extension lives in `editor/vscode/` (TypeScript): `npm install && npm run compile`
(type-checks with `tsc --noEmit`, then bundles with esbuild). There is no VS Code CI host — the
gate is type-check + bundle + manual `F5` smoke.

Use `uv run python …` (not bare `python`).

## Where things live

- `sgt/store/` — the graph + append-only effect log. `sgt/effects/` — the effect model,
  `materialize()`, `build_statement_seq()`, the reverse differ (`diff.py`), and **`attribute.py`**
  (line→node semantic blame).
- `sgt/orchestrate/` — the verbs: `plan`, `checkpoint`/`--fulfills`, `revert`, `switch`,
  `reconcile`, and `emit_payload` (dry-run before/after for UIs). All mutations go through the
  drift guard + EICO confluence gate — don't bypass it.
- `sgt/api.py` — **the canonical JSON projection.** `sgt/cli.py` (`--json`), `sgt/mcp/server.py`,
  the VS Code extension, and `sgt/tui/` all read it. Change a shape here, not per-surface.
- `sgt/tui/` — Textual TUI (optional, lazy-imported). `editor/vscode/` — the extension.
- `docs/guide/` — user docs. `docs/plans/`, `docs/design/`, `docs/brainstorms/` — the paper trail.

## Invariants that are easy to break

- **One projection, many clients.** Never let a surface invent its own shape — extend `sgt.api`
  and let MCP/CLI/extension/TUI consume it. This is the single highest-leverage anti-drift move.
- **Color contract.** A feature's **hue is its identity**, generated in **OKLCH** by one
  golden-angle hash + Ottosson OKLCH→sRGB converter duplicated *identically* in three places —
  `editor/vscode/src/color.ts`, `editor/vscode/media/graph.js` (can't import across the webview
  bundle boundary), and `sgt/tui/color.py`. Keep them byte-identical (a test compares JS vs
  Python). **Status is never hue** — it's a glyph (`● ○ ◐ ⚠`) + dim, on every surface.
- **Blame is recovered from the log, never inferred from a text diff,** and is computed against the
  same `materialize()`/`build_statement_seq` path the tree uses, so they can't disagree.
- **Reads are offline; the graph-only boundary holds in every UI** — surfaces visualize and drive
  verbs, they never write code.

## Conventions

- Match the surrounding code's style; comments explain *why*, not *what*. New feature-bearing code
  gets tests under `tests/` mirroring the package path.
- File references in plans/docs use repo-relative paths.
- Commit only when asked; if on the default branch, branch first.
