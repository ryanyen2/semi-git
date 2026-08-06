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

The core loop needs no API key. A few graph-reasoning steps are optional and call an LLM: the
feature labeler (`sgt log --tree`), `sgt plan intake`, the intent-clustering pass (`sgt intent build`),
and the natural-language forms of `revert`/`restore`. Set `OPENAI_API_KEY` in a `.env` file at the
repo root if you plan to use them; everything else on this page works without a key. The endpoint
is env-driven, so you are not tied to OpenAI: point `OPENAI_BASE_URL` at any OpenAI-compatible
gateway (for example a litellm proxy serving Claude models) and pick the model with `SGT_MODEL`
(or `OPENAI_MODEL`). The default is `gpt-5.4-mini`.

## Your first commit through sgt

Run this inside a git repo.

```bash
sgt init                    # read your existing git history into the op store under .sgt/

# edit files with your editor or agent, the same as always

sgt save -m "add email validation"   # record your edits; sgt names the feature(s) they landed in
sgt now                              # where am I: what you asked for, what's unsaved, what's next
sgt log                              # what you did: one row per save, feature chips per row
sgt status                           # files, symbols, features, coverage, and any drift
sgt show 12 app.py                   # read a file as it was at save 12 (nothing is checked out)
sgt log --tree                       # the feature tree
sgt advanced blame app.py            # which feature owns each symbol in this file
```

`sgt save` is the commit step of your daily loop. It reads what you changed on disk, records the
new ops, and commits. `sgt status` tells you whether your working tree matches the state `sgt` has
recorded, or has drifted ahead of it.

`sgt now` leads with what you asked for, in your own words. If you use Claude Code, the prompt hook
installed by `sgt init` records each prompt as you type it, so the surface can say what you are
working on without you telling it twice — you do not have to declare a plan for this to work.

## Remove and bring back a symbol

You name a target as `file::symbol`, an op id, or a feature id or label. On a terminal, `revert`
and `restore` first draw the consequence — what the edit removes and which dependents it lands on —
and wait for you to confirm before writing anything, so the preview is the default, not a flag.

```bash
sgt revert app.py::validate_email          # show the consequence, confirm, then remove it and commit
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
| `.sgt/tree/` | the feature tree from `sgt log --tree`, also committed |
| `.sgt/local/` | working state for this checkout only, not committed (the current state, drafts, staged rewrites, sessions) |
| `.git/` | ordinary git, where `sgt` commits the files it builds from that state |

## For coding agents

`sgt mcp` runs a stdio MCP server so an agent can call `sgt` directly instead of running it as a
shell command. It exposes 14 tools today: `sgt_init`, `sgt_log`, `sgt_grid`, `sgt_status`,
`sgt_diff`, `sgt_advanced_fsck`, `sgt_revert`, `sgt_restore`, `sgt_advanced_oracle_run`,
`sgt_plan_intake`, `sgt_checkpoint`, `sgt_recall`, `sgt_drift`, and `sgt_plan_done`. An agent using
MCP can inspect state, recall why existing code is the way it is, run the plan → checkpoint → drift
loop, and do symbol-level revert and restore. The
commands for working with other people (`sync`, `land`, `merge-op`, `session`, `propose`) have no
MCP tool yet, so those still need to run from the terminal. See [User workflows](workflows.md) for
the full picture.
