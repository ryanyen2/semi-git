# Getting started

`sgt` is **git for semantics**: it keeps a living graph of your codebase's *features* and
rebuilds the working tree from it. This page gets you from zero to a feature you can plug in and
out. For the why and the mental model, read [The semantic tree](the-semantic-tree.md) first.

## Install

```bash
uv venv --python 3.12
uv pip install -e .            # core CLI (`sgt`)
uv pip install -e ".[tui]"     # + the terminal UI
```

The graph ops need no API key. `sgt plan` uses an OpenAI key (from `.env`: `OPENAI_API_KEY`,
optional `OPENAI_MODEL`) for **graph-level reasoning only** — decomposing an intent, never
writing code. A bare `checkpoint` prefers the key to label a change but degrades to deterministic
grouping offline, so the whole loop works with no key.

## Your first feature

```bash
sgt init                                   # bind .sgt/ + git in the current repo

# 1. plan — decompose an intent into reviewable PLANNED nodes (no code yet)
sgt plan "validate and normalize an email address"
sgt graph                                  # see the planned nodes and their dependencies

# 2. implement one of them with your own editor / coding agent
#    (sgt never writes code — you do)

# 3. checkpoint — record your edits under the planned node, flipping it ACTIVE
sgt checkpoint --fulfills "normalize" --intent "lowercase + strip the domain"

# 4. inspect
sgt status                                 # nodes, files, effects, and any drift
sgt show normalize                         # one node: effects, deps, dependents
sgt blame app.py                           # which feature owns each line
```

Refs resolve fuzzily: `sgt show "email"`, `sgt revert normalize`, or a node id all work.

## Plug a feature in and out

```bash
sgt revert normalize --emit                # dry-run: preview the change, write nothing
sgt revert normalize                       # plug it out (by dependency closure) + commit
sgt switch normalize off                   # suspend instead (keeps history); `on` restores
```

If a change is held back because it doesn't yet fit (`quarantined`), resolve it once its rival
is gone:

```bash
sgt reconcile                              # re-gate every pending quarantine
```

## See it visually

- **Terminal UI:** `sgt tui` — browse the DAG, inspect a node, preview a plug-out, apply ops.
  See [the TUI guide](tui.md).
- **VS Code:** install the extension in `editor/vscode/` for semantic blame, a feature heatmap,
  CodeLens, a graph view, and diff previews. See [the extension guide](vscode-extension.md).

## For coding agents

`sgt mcp` runs a stdio MCP server exposing the same surface as tools (`sgt_graph`, `sgt_show`,
`sgt_status`, `sgt_conflicts`, `sgt_blame`, `sgt_plan`, `sgt_checkpoint`, `sgt_revert`,
`sgt_switch`, `sgt_reconcile`). Point your agent at it and it drives the same loop you do.

## Where things live

| Path | What |
| --- | --- |
| `.sgt/graph.json` | the semantic DAG (nodes + edges) |
| `.sgt/effects.json` | the append-only effect log + per-node bundles |
| `.git/` | ordinary git — `sgt` commits the materialized tree here |
