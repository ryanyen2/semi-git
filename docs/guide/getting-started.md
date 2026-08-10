# Getting started

This page takes you from a fresh repo to removing a single function and bringing it back. If you
want to know how `sgt` represents your code before you start, read [the model](the-semantic-tree.md)
first.

## Install

You need [uv](https://docs.astral.sh/uv/) and Python 3.10 or newer.

```bash
uv tool install semi-git
```

Check that it worked.

```bash
sgt --version
```

If your shell can't find the command, run `uv tool update-shell` and open a new terminal.

## Your first commit through sgt

Run `sgt init` inside a git repo. It reads your existing history into the op store under `.sgt/`,
and you only need to run it once per repo.

```bash
cd your-project
sgt init
```

Now edit files the way you normally do. When you want to record what you changed, run `sgt save`.

```bash
sgt save -m "add email validation"
```

`sgt save` is the commit step of your daily loop. It reads what changed on disk, records the new
ops, and commits. It also tells you which feature your edits landed in.

Here are the commands you'll use to see where you are.

```bash
sgt now                      # what you asked for, what's unsaved, and the next thing to do
sgt log                      # what you did, one row per save
sgt status                   # files, symbols, features, coverage, and any drift
sgt show app.py --at 12      # read a file as it was at save 12, without checking anything out
sgt log --tree               # the feature tree
sgt advanced blame app.py    # which feature owns each symbol in this file
```

`sgt status` tells you whether your working tree matches the state `sgt` has recorded or has drifted
ahead of it.

`sgt now` leads with what you asked for, in your own words. If you use Claude Code, the prompt hook
that `sgt init` installs records each prompt as you type it, so `sgt now` can say what you're
working on without you declaring a plan.

## Remove a symbol and bring it back

You name a target as `file::symbol`, an op id, or a feature id or label. In a terminal, `revert` and
`restore` first show you the consequence, meaning what the edit removes and which dependents it
lands on. They wait for you to confirm before writing anything, so the preview is the default rather
than a flag.

```bash
sgt revert app.py::validate_email
sgt restore app.py::validate_email
```

After the revert, `validate_email` is gone from `app.py`, and every other symbol is byte for byte
what it was before. `sgt undo` steps back if you change your mind.

If you don't know the exact name, you can describe it instead, e.g., `sgt revert "the email
validation logic"`. That form asks a language model to propose candidates and previews each one
before applying anything, so it needs an API key. See
[workflows.md](workflows.md#2-remove-one-thing-from-a-big-tangled-edit) for how it works.

## Setting an API key

The daily loop needs no API key. A few steps call a language model, and they're all optional:

- the feature labeler in `sgt log --tree`
- `sgt plan intake`
- the intent clustering in `sgt intent build`
- the plain English forms of `sgt revert` and `sgt restore`

Put your key in a `.env` file at the root of your repo.

```
OPENAI_API_KEY=sk-...
```

You aren't tied to OpenAI. Point `OPENAI_BASE_URL` at any OpenAI-compatible endpoint, e.g., a
litellm proxy serving Claude models, and pick the model with `SGT_MODEL`. The default model is
`gpt-5.4-mini`.

## Where things live

| Path | What it holds |
| --- | --- |
| `.sgt/ops/` | The op store, committed to git along with your code. |
| `.sgt/tree/` | The feature tree from `sgt log --tree`, also committed. |
| `.sgt/local/` | Working state for this checkout only, and not committed. Holds the current state, drafts, staged rewrites, and sessions. |
| `.git/` | Ordinary git, where `sgt` commits the files it builds from that state. |

## For coding agents

Run this once in your repo:

```bash
sgt init --agent
```

It writes `.mcp.json` so Claude Code offers the `sgt` tools, pre-approves that server in
`.claude/settings.json`, installs three skills into `.claude/skills/`, and points the VS Code
extension at your `sgt` install through `.vscode/settings.json`.

Behind `.mcp.json` is `sgt mcp`, a stdio MCP server that lets an agent call `sgt` directly instead
of shelling out. It exposes 17 tools today: `sgt_init`, `sgt_now`, `sgt_log`, `sgt_status`,
`sgt_show`, `sgt_diff`, `sgt_save`, `sgt_advanced_fsck`, `sgt_revert`, `sgt_restore`,
`sgt_advanced_oracle_run`, `sgt_plan_intake`, `sgt_checkpoint`, `sgt_recall`, `sgt_drift`,
`sgt_plan_done`, and `sgt_plan_adopt`.

An agent with those tools can orient itself, inspect state, recall why existing code is the way it
is, run the plan and checkpoint and drift loop, save its own work, and revert or restore individual
symbols. The commands for working with other people have no MCP tool yet, so `sgt sync`, `sgt land`,
`sgt advanced merge-op`, `sgt session`, and `sgt propose` still run from the terminal. See
[user workflows](workflows.md) for the full picture.
