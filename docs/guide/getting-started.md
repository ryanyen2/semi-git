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
ops, and commits. It prints each step it recorded with its address, such as
`app.py::validate_email@3f2a1c0`. Add `--as <name>` to give the save's steps a name, such as `ugh
save -m "add email validation" --as signup`.

Here are the commands you'll use to see where you are.

```bash
ugh log                      # the saves, newest first, with the names on their steps
ugh status                   # files, symbols, coverage, drift, untracked files, the plan
ugh show signup              # the steps named signup and what taking them out would take
ugh find email               # addresses whose names, subjects or definitions mention email
```

`ugh status` tells you whether your working tree matches the state `ugh` has recorded or has drifted
ahead of it.

## Remove a symbol and bring it back

You name the steps with an address: a name, `name@<rev>`, a file, `file::symbol`,
`file::symbol@<rev>` (one step), a save's commit sha, or a range `<rev>..<rev>`. In a terminal,
`revert` and `restore` first show you what would go, what is held and why, and which saves stand on
it. They wait for you to confirm before writing anything, so the preview is the default
rather than a flag.

```bash
ugh revert app.py::validate_email
ugh restore app.py::validate_email
```

After the revert, `validate_email` is gone from `app.py`, and every other symbol is byte for byte
what it was before. `ugh undo` steps back if you change your mind.

If you don't know the address, `ugh find email validation` lists the addresses whose names, save
subjects, definition names or captured asks contain those words. `revert` itself takes only an
address, and a phrase is refused with the nearest `find` hits.

## Where things live

| Path | What it holds |
| --- | --- |
| `.sgt/ops/` | The op store, mined from history; every clone mines its own. |
| `.sgt/names.json` | The names you and your agents gave to steps. They travel on `refs/ugh/state`, alongside your branches. |
| `.sgt/local/` | Working state for this checkout only, and not committed. Holds the current state, the undo journal, the plan, the captured asks, and caches. |
| `.git/` | Ordinary git, where `ugh` commits the files it builds from that state. |

The directory is `.sgt/` because the tool used to be called sgt.

## For coding agents

Run this once in your repo:

```bash
ugh init --agent
```

It writes `.mcp.json` so Claude Code offers the `ugh` tools, pre-approves that server in
`.claude/settings.json`, installs three skills into `.claude/skills/`, and points the VS Code
extension at your `ugh` install through `.vscode/settings.json`.

Behind `.mcp.json` is `ugh mcp`, a stdio MCP server that lets an agent call `ugh` directly instead
of shelling out. It exposes 10 tools: `ugh_init`, `ugh_log`, `ugh_check`, `ugh_save` (with `as`),
`ugh_name`, `ugh_revert`, `ugh_restore`, `ugh_show`, `ugh_find`, and `ugh_plan`.

An agent with those tools can declare a plan, save its own work under the names it declared, find an
address it can describe, and revert or restore steps by address. The commands
for working with other people have no MCP tool, so `ugh sync` and `ugh resolve` run from the
terminal. See
[user workflows](workflows.md) for the full picture.
