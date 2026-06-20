# semi-git (`sgt`)

Version a codebase by its **features and concepts**, not its diffs.

`sgt` is **git for semantics**: it maintains a living semantic DAG (`.sgt/`) over an ordinary
git repo, and the coding agent (or a human) operates it the way it already operates `git`.
**sgt never authors code** — your coding agent writes it; sgt plans, records, and reorganizes
the *semantic graph* and reconstructs the tree from it. Every mutation runs through the **EICO
confluence gate** so nothing lands unless it commutes and preserves the codebase's invariants.

The loop: **`plan`** an intent into reviewable nodes → implement with your own tools →
**`checkpoint`** to record what you built (distilled into typed effects) → **`revert`/`switch`/
`reconcile`** to plug features in and out. The LLM is used only to reason about the graph
(decompose a plan, label a checkpoint), never to produce code.

**New to it? Start with the [user guide](docs/guide/README.md)** — the mental model, a
getting-started walkthrough, and the VS Code extension + terminal UI.

See:
- `docs/guide/` — user-facing guide (mental model, getting started, the two UIs)
- `docs/ideation/2026-06-17-semi-git-ideation.md` — where the idea came from
- `docs/brainstorms/2026-06-17-semi-git-requirements.md` — what it is (requirements)
- `docs/plans/2026-06-17-001-feat-semi-git-core-plan.md` — how it's built (plan)

## Development

Requires [`uv`](https://docs.astral.sh/uv/).

```bash
uv venv --python 3.12
uv pip install -e ".[dev]"
uv run pytest
```

## Usage

```bash
sgt init                                  # bind .sgt + git
sgt plan "validate + normalize an email"  # decompose an intent into reviewable PLANNED nodes
sgt "validate + normalize an email"       # shorthand for `sgt plan`
# ...implement a planned node with your own editor / coding agent...
sgt checkpoint --fulfills <node> --intent "..."   # record your edits under that node (-> ACTIVE)
sgt checkpoint                            # or: record ad-hoc edits as a new node
sgt graph                                 # the semantic DAG
sgt revert <feature>                      # plug a feature out (by dependency closure)
sgt revert <feature> --emit               # dry-run: preview the change, write nothing
sgt switch <feature> off|on               # suspend / restore
sgt reconcile [<ref>]                     # re-gate held quarantines; resolve any that now commute
sgt blame <file>                          # which feature owns each line (semantic blame)
sgt graph --json / sgt export             # machine-readable projection for tools/UIs
sgt tui                                    # terminal UI (needs `semi-git[tui]`)
sgt mcp                                   # stdio MCP server so a coding agent can drive sgt
```

A **VS Code extension** (`editor/vscode/`) and a **terminal UI** (`sgt tui`) sit on top of the
same `sgt … --json` surface: semantic blame, a feature DAG, a per-feature heatmap, and diff
previews of plug-outs. See the [user guide](docs/guide/README.md).

The graph ops (`revert`/`switch`/`reconcile`/`checkpoint --fulfills`/`checkpoint --intent`) need
no API key. `plan` uses the OpenAI key (read from `.env`: `OPENAI_API_KEY`, optional
`OPENAI_MODEL`) for **graph-level reasoning only** — decomposition, never code. A bare,
no-intent `checkpoint` *prefers* the LLM to label the distilled change but **degrades to
deterministic grouping offline**, so the whole loop works with no key.

## Status

Graph-only pivot complete: sgt no longer authors code (the OpenAI coding backend is removed).
The spine is `plan` → implement (your agent) → `checkpoint`/`--fulfills` → `revert`/`switch`/
`reconcile`, all gated by the confluence check, with an `--emit` dry-run. Verified by the test
suite + a live walkthrough (`scripts/e2e_plan_checkpoint.py`). See `FINDINGS.md` for what's
verified and the deferred items, and `docs/design/2026-06-19-graph-only-agent-driven-sgt.md`
for the design.

**Visual surfaces (2026-06-20).** A `sgt.api` JSON projection + line-level **semantic blame**
(`sgt/effects/attribute.py`) feed a **VS Code extension** and a **terminal UI** — one schema, no
drift. A feature's hue is its identity (same OKLCH color in every surface); status is a glyph, not
a hue. See the [user guide](docs/guide/README.md) and `FINDINGS.md`.
