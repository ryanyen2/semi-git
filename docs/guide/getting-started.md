# Getting started

This page takes you from an empty repo to removing and re-adding a single function. Read
[The model](the-semantic-tree.md) first for how `sgt` represents your code.

## Install

You need [`uv`](https://docs.astral.sh/uv/). Two extras matter. Mining symbols needs the
tree-sitter grammars in the `entities` extra, and the feature tree needs the clustering libraries
in the `lens` extra. Install both for the full tool.

```bash
uv venv --python 3.12
uv pip install -e ".[entities,lens]"
```

The core loop needs no API key. One optional command, `sgt plan intake`, uses an OpenAI key to
turn a written intent into a set of predicted ops. Set `OPENAI_API_KEY` (and optionally
`OPENAI_MODEL`) in a `.env` file if you want it. Everything on this page works without a key.

## Your first commit through sgt

Run this inside a git repo.

```bash
sgt init                    # read existing git history into the op store under .sgt/

# edit files with your editor or agent, the same as always

sgt save -m "add email validation"   # read your edits into ops and commit a record
sgt status                            # files, symbols, features, coverage, and any drift
sgt map                               # the feature tree
sgt blame app.py                      # which feature owns each symbol in a file
```

`sgt save` is the commit step of the daily loop. It reads what you changed on disk, records the
new ops, and commits. `sgt status` shows whether the working tree matches the recorded ideal or
has drifted ahead of it.

## Remove and re-add a symbol

You name a target as `file::symbol`, an op id, or a feature id or label. `--emit` previews the
change and writes nothing.

```bash
sgt revert app.py::validate_email --emit   # preview the removal
sgt revert app.py::validate_email          # remove it and everything built on it, then commit
sgt restore app.py::validate_email         # add it back, along with anything it needs
```

After the revert, `validate_email` is gone from `app.py` and every other symbol is byte for byte
the same. `sgt undo` inverts your last change if you want to step back.

## Where things live

| Path | What it holds |
| --- | --- |
| `.sgt/ops/` | the op store, committed to git with your code |
| `.sgt/tree/` | the feature tree from `sgt map`, committed |
| `.sgt/local/` | working state for this checkout, ignored by git (the current ideal, drafts, staged rewrites, sessions) |
| `.git/` | ordinary git, where `sgt` commits the folded tree |

## For coding agents

`sgt mcp` runs a stdio MCP server so an agent can call `sgt` directly instead of shelling out. It
exposes 11 tools today: `sgt_init`, `sgt_log`, `sgt_state`, `sgt_diff`, `sgt_fsck`, `sgt_revert`,
`sgt_restore`, `sgt_oracle_run`, `sgt_plan_intake`, `sgt_checkpoint`, and `sgt_drift`. An agent on
MCP can inspect state and do symbol-level revert and restore. The collaboration verbs (`sync`,
`land`, `merge-op`, `session`, `propose`) have no MCP tool yet, so those still run from the
terminal. See [User workflows](workflows.md) for the full picture.
