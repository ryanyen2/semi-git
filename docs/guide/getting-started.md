# Getting started

This page takes you from an empty repo to removing and bringing back a single function. Read
[The model](the-semantic-tree.md) first to understand how `sgt` represents your code.

## Install

You need [`uv`](https://docs.astral.sh/uv/). Two extras matter. Reading symbols out of your code
needs the tree-sitter grammars in the `entities` extra, and building the feature tree needs the
clustering libraries in the `lens` extra. Install both for the full tool.

```bash
uv venv --python 3.12
uv pip install -e ".[entities,lens]"
```

The core loop needs no API key. One command, `sgt plan intake`, is optional and uses an OpenAI key
to turn a written intent into a set of predicted ops. Set `OPENAI_API_KEY` (and, if you want,
`OPENAI_MODEL`) in a `.env` file if you plan to use it. Everything else on this page works without
a key.

## Your first commit through sgt

Run this inside a git repo.

```bash
sgt init                    # read your existing git history into the op store under .sgt/

# edit files with your editor or agent, the same as always

sgt save -m "add email validation"   # read your edits into ops and commit a record of them
sgt status                            # files, symbols, features, coverage, and any drift
sgt map                               # the feature tree
sgt blame app.py                      # which feature owns each symbol in this file
```

`sgt save` is the commit step of your daily loop. It reads what you changed on disk, records the
new ops, and commits. `sgt status` tells you whether your working tree matches the state `sgt` has
recorded, or has drifted ahead of it.

## Remove and bring back a symbol

You name a target as `file::symbol`, an op id, or a feature id or label. `--emit` previews the
change without writing anything.

```bash
sgt revert app.py::validate_email --emit   # preview the removal
sgt revert app.py::validate_email          # remove it and everything built on it, then commit
sgt restore app.py::validate_email         # add it back, along with anything it needs
```

After the revert, `validate_email` is gone from `app.py`, and every other symbol is byte for byte
what it was before. `sgt undo` steps back if you want to undo your last change.

If you do not know the exact name, `sgt revert "the email validation logic"` asks an LLM to
propose candidates and previews each one before applying anything. See
[`workflows.md`](workflows.md#2-remove-one-thing-from-a-big-tangled-edit) for how that works.

## Where things live

| Path | What it holds |
| --- | --- |
| `.sgt/ops/` | the op store, committed to git along with your code |
| `.sgt/tree/` | the feature tree from `sgt map`, also committed |
| `.sgt/local/` | working state for this checkout only, not committed (the current state, drafts, staged rewrites, sessions) |
| `.git/` | ordinary git, where `sgt` commits the files it builds from that state |

## For coding agents

`sgt mcp` runs a stdio MCP server so an agent can call `sgt` directly instead of running it as a
shell command. It exposes 11 tools today: `sgt_init`, `sgt_log`, `sgt_state`, `sgt_diff`,
`sgt_fsck`, `sgt_revert`, `sgt_restore`, `sgt_oracle_run`, `sgt_plan_intake`, `sgt_checkpoint`, and
`sgt_drift`. An agent using MCP can inspect state and do symbol-level revert and restore. The
commands for working with other people (`sync`, `land`, `merge-op`, `session`, `propose`) have no
MCP tool yet, so those still need to run from the terminal. See [User workflows](workflows.md) for
the full picture.
