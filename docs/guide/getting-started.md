# Getting started

This page takes you from a fresh repo to removing a single function and bringing it back. If you
want to know how `ugh` represents your code before you start, read [the model](the-semantic-tree.md)
first.

## Install

You need [uv](https://docs.astral.sh/uv/) and Python 3.10 or newer.

```bash
uv tool install ugh
```

Check that it worked.

```bash
ugh --version
```

If your shell can't find the command, run `uv tool update-shell` and open a new terminal.

## Your first commit through ugh

Run `ugh init` inside a git repo. It reads your existing history into the op store under `.sgt/`,
and you only need to run it once per repo.

```bash
cd your-project
ugh init
```

Now edit files the way you normally do. When you want to record what you changed, run `ugh save`.

```bash
ugh save -m "add email validation"
```

`ugh save` is the commit step of your daily loop. It reads what changed on disk, records the new
ops, and commits. It also tells you which feature your edits landed in.

Here are the commands you'll use to see where you are.

```bash
ugh log                      # what you did, one row per save
ugh status                   # files, symbols, features, coverage, and any drift
ugh show app.py --at 12      # read a file as it was at save 12, without checking anything out
ugh log --tree               # the feature tree
```

`ugh status` tells you whether your working tree matches the state `ugh` has recorded or has drifted
ahead of it.

`ugh log --rail` leads with what you asked for, in your own words. If you use Claude Code, the prompt
hook that `ugh init` installs records each prompt as you type it, so ugh can say what you're working
on without you declaring a plan.

## Remove a symbol and bring it back

You name a target as `file::symbol`, an op id, or a feature id or label. In a terminal, `revert` and
`restore` first show you the consequence, meaning what the edit removes and which dependents it
lands on. They wait for you to confirm before writing anything, so the preview is the default rather
than a flag.

```bash
ugh revert app.py::validate_email
ugh restore app.py::validate_email
```

After the revert, `validate_email` is gone from `app.py`, and every other symbol is byte for byte
what it was before. `ugh undo` steps back if you change your mind.

If you don't know the exact name, you can describe it instead, e.g., `ugh revert "the email
validation logic"`. That form asks a language model to propose candidates and previews each one
before applying anything, so it needs an API key. See
[workflows.md](workflows.md#2-remove-one-thing-from-a-big-tangled-edit) for how it works.

## Setting an API key

The daily loop needs no API key. A few steps call a language model, and they're all optional:

- the feature labeler in `ugh log --tree`
- `ugh plan intake`
- the checkpoint naming in `ugh log --refresh`
- the plain English forms of `ugh revert` and `ugh restore`

Put your key in a `.env` file at the root of your repo.

```
OPENAI_API_KEY=sk-...
```

You aren't tied to OpenAI. Point `OPENAI_BASE_URL` at any OpenAI-compatible endpoint, e.g., a
litellm proxy serving Claude models, and pick the model with `UGH_MODEL`. The default model is
`gpt-5.4-mini`.

## Where things live

| Path | What it holds |
| --- | --- |
| `.sgt/ops/` | The op store, committed to git along with your code. |
| `.sgt/tree/` | The feature tree from `ugh log --tree`, also committed. |
| `.sgt/local/` | Working state for this checkout only, and not committed. Holds the current state, the undo journal, plans, and caches. |
| `.git/` | Ordinary git, where `ugh` commits the files it builds from that state. |

The directory is `.sgt/` because the tool used to be called sgt. Every op store ever committed sits
at that path, and readers have to keep parsing the ones already in your history, so the name stayed
when the tool's did not. The `Sgt-Op:` trailers on commits `ugh` writes are the same story.

## For coding agents

Run this once in your repo:

```bash
ugh init --agent
```

It writes `.mcp.json` so Claude Code offers the `ugh` tools, pre-approves that server in
`.claude/settings.json`, installs three skills into `.claude/skills/`, and points the VS Code
extension at your `ugh` install through `.vscode/settings.json`.

Behind `.mcp.json` is `ugh mcp`, a stdio MCP server that lets an agent call `ugh` directly instead
of shelling out. It exposes 13 tools: `ugh_init`, `ugh_log`, `ugh_show`, `ugh_find`, `ugh_save`,
`ugh_advanced_fsck`, `ugh_revert`, `ugh_restore`, `ugh_plan_intake`, `ugh_checkpoint`, `ugh_drift`,
`ugh_plan_done`, and `ugh_plan_adopt`.

An agent with those tools can inspect state, find a thing it can describe, run the plan and
checkpoint and drift loop, save its own work, and revert or restore individual symbols. The commands
for working with other people have no MCP tool, so `ugh sync` and `ugh resolve` run from the
terminal. See
[user workflows](workflows.md) for the full picture.
