# ugh

`ugh` runs on top of an ordinary git repo and tracks your code function by function instead of file
by file. Because it follows each function and class separately, you can remove one function's
history, or bring it back, without disturbing the rest of the file.

`ugh` never writes your code. You and your coding agent edit files the same way you always have, and
`ugh` keeps track of which of those edits are currently in.

## Install

You need [uv](https://docs.astral.sh/uv/) and Python 3.10 or newer.

```bash
uv tool install ugh
```

The `ugh` command is now on your PATH. If your shell can't find it, run `uv tool update-shell` and
open a new terminal.

Then go into a git repo and read its history in. You only do this once per repo.

```bash
cd your-project
ugh init
```

From then on, edit files however you normally do, and record the edits when you're ready.

```bash
ugh save -m "add input validation"
```

`ugh save` tells you which feature each edit landed in.

## Set up your coding agent and editor

If you use Claude Code or the VS Code extension, run this in the repo:

```bash
ugh init --agent
```

It writes four things:

- `.mcp.json`, so Claude Code offers the `ugh` tools when you start it in this repo.
- `.claude/settings.json`, which pre-approves that server so you don't get asked.
- `.claude/skills/`, which holds three skills that teach an agent how to use `ugh`.
- `.vscode/settings.json`, which points the VS Code extension at your `ugh` install.

Every path it writes is absolute, because a program started from your Dock or Applications folder
doesn't inherit the same PATH your terminal has.

The three skills are `ugh-agent`, which covers how to work in an ugh repo and which command answers
which question, `ugh-plan`, which covers recording intent before you build and reconciling it
afterward, and `ugh-workflow`, which covers choosing between commands that look alike.

For an agent that only has a shell, there's nothing to wire up. Every read command takes `--json`,
and `ugh help` lists the full surface.

Your agent can record its own work, and it should. `ugh save` asks the agent for its own words, and
those words become the name of any feature born from the work. `ugh undo` reverses a save.

Two commands change state other people can see, and they stay with you rather than your agent.
They are `ugh sync` and `ugh resolve`. Each one asks you to confirm, and no single command undoes
them.

## Install the VS Code extension

Download `ugh-0.1.0.vsix` from the
[latest release](https://github.com/ryanyen2/ugh/releases/latest). In VS Code, open the
Extensions view, click the `...` menu, and choose "Install from VSIX".

Run `ugh init --agent` in your repo so the extension knows where `ugh` is.

## Setting an API key

The daily loop needs no API key. A few commands call a language model, e.g., `ugh plan intake`. Put
your key in a `.env` file at the root of your repo.

```
OPENAI_API_KEY=sk-...
```

You can point `ugh` at any OpenAI-compatible endpoint by setting `OPENAI_BASE_URL`, and you can pick
the model with `UGH_MODEL`. The default model is `gpt-5.4-mini`.

## The problem it solves

Say a coding agent runs for an hour, touches a dozen files, and adds rate limiting, a caching layer,
and a retry policy in one pass. The caching layer turns out to be wrong.

In plain git you have two options, and both risk the work you want to keep. You can find every line
the caching code touched and revert those lines by hand. Or you can cherry-pick around a commit that
also holds the two features you want.

`ugh` already tracked each function's edits separately, so removing one is a single command. Here is
a run against a scratch repo.

```
$ ugh revert cache.py::get_cached
 ▸ rewind  cache.py::get_cached

 removes 1 edit(s) across 1 symbol(s) · 1 file(s): cache.py
  ✓ revert applied — 1 edit(s) removed, 0 added. (`ugh undo` reverses this.)
```

`get_cached` is gone from `cache.py`. The other function in that file, `set_cached`, is untouched,
and every symbol in `rate_limit.py` and `retry.py` is byte for byte what it was before. `ugh restore
cache.py::get_cached` puts it back.

The same command works on a whole feature with `ugh revert <feature>`, once the edits have been
grouped that way.
[docs/guide/workflows.md](docs/guide/workflows.md) covers when that grouping is reliable and when it
isn't.

## Daily commands

A target for `revert` or `restore` can be a `file::symbol` name, an op id, a feature, or, if you set
an API key, a plain English phrase.

| Command | What it's for |
| --- | --- |
| `ugh init` | Read your existing git history into `ugh`. Run once per repo. |
| `ugh save -m "..."` | Record the edits you just made, and name the feature they landed in. |
| `ugh log` | What you did, newest first. `--map` shows features over time, `--tree` shows the feature tree, and `--summary` shows what needs attention. |
| `ugh status` | What needs attention right now. The same view as `ugh log --summary`. |
| `ugh show <target>` | What an id, feature, or symbol is, and what removing it would cost. Add `--at <point>` to read a file as it was then. Nothing is checked out. |
| `ugh find <words>` | Find a feature, save, or symbol you can describe but not name. |
| `ugh undo` | Reverse your last `ugh` command as a new change, rather than by rewriting history. It shows what it will do first. |
| `ugh revert <target>` | Remove one symbol, feature, or checkpoint's work, along with anything built on it. |
| `ugh restore <target>` | Bring a removed thing back, along with anything it needs. |
| `ugh resolve <symbol>` | Walk through reconciling a symbol that was edited two different ways at once. |
| `ugh plan ...` | State a plan up front so later saves can be matched against it. |
| `ugh feature rename` | Rename a feature. Labels only, and it never touches your code. |
| `ugh check` | Verify the op store. `--repair` re-derives the record after a git history rewrite. |
| `ugh sync` | Fetch a teammate's work and merge it, flagging any real conflict. |
| `ugh push` | Push. If the push is rejected, it points you at `ugh sync`. |
| `ugh mcp` | Run an MCP server so a coding agent can call `ugh` directly. |

Run `ugh help` for the full list.

## How it works

`ugh` reads each commit and breaks it into per-symbol edits. Your codebase at any moment is the set
of edits that are currently in, and `ugh` can rebuild your files from that set exactly. Run it and
you get back, byte for byte, what is checked out.

Removing an edit also removes anything built on top of it, so whatever is left still rebuilds into
working files. Two versions of the same function can never both be in at once. When that happens,
because two people edited it in parallel, `ugh` calls it a fork and asks you to reconcile it rather
than picking a side. `ugh check` checks that the current state is still valid, and `ugh check
--tree` that the files it builds match what git has.

[docs/guide/the-semantic-tree.md](docs/guide/the-semantic-tree.md) has the formal version.

For the curious, `ugh` calls one per-symbol edit an op, the current set of edits an ideal, and
rebuilding your files from that set the fold.

## Working with other people

Conflicts don't go away, but they get smaller. If two people edit the same function at the same
time, that's a real conflict, and `ugh` isolates it to that one function. Everything else merges
right away with no conflict markers to resolve.

You reconcile the conflict with `ugh resolve <symbol>`. It won't let the conflict close until your
build and test checks pass, so a conflict is never closed by code nobody verified.

[docs/guide/workflows.md](docs/guide/workflows.md) walks through this end to end, along with the
points where a person still has to step in.

## Docs

Start with [docs/guide/](docs/guide/). It covers how `ugh` models your code, a getting started
walk-through, the VS Code extension, and a tour by use case in
[workflows.md](docs/guide/workflows.md) that also lists today's limits.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for how to set up a development checkout, run the tests, and
cut a release.

## License

MIT. See [LICENSE](LICENSE).
