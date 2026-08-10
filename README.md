# semi-git (`sgt`)

`sgt` runs on top of an ordinary git repo and tracks your code function by function instead of file
by file. Because it follows each function and class separately, you can remove one function's
history, or bring it back, without disturbing the rest of the file.

`sgt` never writes your code. You and your coding agent edit files the same way you always have, and
`sgt` keeps track of which of those edits are currently in.

## Install

You need [uv](https://docs.astral.sh/uv/) and Python 3.10 or newer.

```bash
uv tool install semi-git
```

The `sgt` command is now on your PATH. If your shell can't find it, run `uv tool update-shell` and
open a new terminal.

Then go into a git repo and read its history in. You only do this once per repo.

```bash
cd your-project
sgt init
```

From then on, edit files however you normally do, and record the edits when you're ready.

```bash
sgt save -m "add input validation"
```

`sgt save` tells you which feature each edit landed in.

## Set up your coding agent and editor

If you use Claude Code or the VS Code extension, run this in the repo:

```bash
sgt init --agent
```

It writes four things:

- `.mcp.json`, so Claude Code offers the `sgt` tools when you start it in this repo.
- `.claude/settings.json`, which pre-approves that server so you don't get asked.
- `.claude/skills/`, which holds three skills that teach an agent how to use `sgt`.
- `.vscode/settings.json`, which points the VS Code extension at your `sgt` install.

Every path it writes is absolute, because a program started from your Dock or Applications folder
doesn't inherit the same PATH your terminal has.

The three skills are `sgt-agent`, which covers how to work in an sgt repo and which command answers
which question, `sgt-plan`, which covers recording intent before you build and reconciling it
afterward, and `sgt-workflow`, which covers choosing between commands that look alike.

For an agent that only has a shell, there's nothing to wire up. Every read command takes `--json`,
and `sgt help` lists the full surface.

Your agent can record its own work, and it should. `sgt save` asks the agent for its own words, and
those words become the name of any feature born from the work. `sgt undo` reverses a save.

Four commands change state other people can see, and they stay with you rather than your agent.
They are `sgt land`, `sgt sync`, `sgt propose land`, and `sgt resolve`. Each one asks you to confirm,
and no single command undoes them.

## Install the VS Code extension

Download `semi-git-0.1.0.vsix` from the
[latest release](https://github.com/ryanyen2/semi-git/releases/latest). In VS Code, open the
Extensions view, click the `...` menu, and choose "Install from VSIX".

Run `sgt init --agent` in your repo so the extension knows where `sgt` is.

## Setting an API key

The daily loop needs no API key. A few commands call a language model, e.g., `sgt plan intake`. Put
your key in a `.env` file at the root of your repo.

```
OPENAI_API_KEY=sk-...
```

You can point `sgt` at any OpenAI-compatible endpoint by setting `OPENAI_BASE_URL`, and you can pick
the model with `SGT_MODEL`. The default model is `gpt-5.4-mini`.

## The problem it solves

Say a coding agent runs for an hour, touches a dozen files, and adds rate limiting, a caching layer,
and a retry policy in one pass. The caching layer turns out to be wrong.

In plain git you have two options, and both risk the work you want to keep. You can find every line
the caching code touched and revert those lines by hand. Or you can cherry-pick around a commit that
also holds the two features you want.

`sgt` already tracked each function's edits separately, so removing one is a single command. Here is
a run against a scratch repo.

```
$ sgt revert cache.py::get_cached
 ▸ rewind  cache.py::get_cached

 removes 1 edit(s) across 1 symbol(s) · 1 file(s): cache.py
  ✓ revert applied — 1 edit(s) removed, 0 added. (`sgt undo` reverses this.)
```

`get_cached` is gone from `cache.py`. The other function in that file, `set_cached`, is untouched,
and every symbol in `rate_limit.py` and `retry.py` is byte for byte what it was before. `sgt restore
cache.py::get_cached` puts it back.

The same command works on a whole feature with `sgt revert <feature>`, or on a whole agent session
with `sgt revert --session <name>`, once the edits have been grouped that way.
[docs/guide/workflows.md](docs/guide/workflows.md) covers when that grouping is reliable and when it
isn't.

## Daily commands

A target for `revert` or `restore` can be a `file::symbol` name, an op id, a feature, or, if you set
an API key, a plain English phrase.

| Command | What it's for |
| --- | --- |
| `sgt init` | Read your existing git history into `sgt`. Run once per repo. |
| `sgt save -m "..."` | Record the edits you just made, and name the feature they landed in. |
| `sgt now` | Where am I? What you asked for, what's unsaved, what needs you, and the next thing to do. |
| `sgt log` | What you did, newest first. `--map` shows features over time, `--tree` shows the feature tree, and `--summary` shows what needs attention. |
| `sgt status` | What needs attention right now. The same view as `sgt log --summary`. |
| `sgt show <target>` | What an id, feature, or symbol is, and what removing it would cost. Add `--at <point>` to read a file as it was then. Nothing is checked out. |
| `sgt why <target>` | Why this code exists, meaning the prompt or plan step behind a commit, op, or symbol. |
| `sgt undo` | Reverse your last `sgt` command as a new change, rather than by rewriting history. It shows what it will do first. |
| `sgt revert <target>` | Remove one symbol, feature, or session's work, along with anything built on it. |
| `sgt restore <target>` | Bring a removed thing back, along with anything it needs. |
| `sgt resolve <symbol>` | Walk through reconciling a symbol that was edited two different ways at once. |
| `sgt switch <branch>` | Switch branches and rebuild that branch's files. |
| `sgt diff <a> <b>` | Which symbol-level edits differ between two states. |
| `sgt intent ...` | Browse the reason behind a feature. |
| `sgt plan ...` | State a plan up front so later saves can be matched against it. |
| `sgt feature ...` | Re-group or rename features. Labels only, and it never touches your code. |
| `sgt advanced ...` | Rare and maintenance commands, e.g., `sgt advanced fsck`. |
| `sgt sync` | Fetch a teammate's work and merge it, flagging any real conflict. |
| `sgt land <branch>` | Advance a shared branch, one writer at a time, once your checks pass. |
| `sgt push` | Push. If the push is rejected, it points you at `sgt sync`. |
| `sgt propose ...` | Open a review object, like a pull request, that a reviewer can accept feature by feature. |
| `sgt session ...` | Run an agent in its own scratch worktree, then land its work. |
| `sgt mcp` | Run an MCP server so a coding agent can call `sgt` directly. |

Run `sgt help` for the full list.

## How it works

`sgt` reads each commit and breaks it into per-symbol edits. Your codebase at any moment is the set
of edits that are currently in, and `sgt` can rebuild your files from that set exactly. Run it and
you get back, byte for byte, what is checked out.

Removing an edit also removes anything built on top of it, so whatever is left still rebuilds into
working files. Two versions of the same function can never both be in at once. When that happens,
because two people edited it in parallel, `sgt` calls it a fork and asks you to reconcile it rather
than picking a side. `sgt advanced fsck` checks that the current state is still valid and that the
files it builds match what git has.

[docs/guide/the-semantic-tree.md](docs/guide/the-semantic-tree.md) has the formal version.

For the curious, `sgt` calls one per-symbol edit an op, the current set of edits an ideal, and
rebuilding your files from that set the fold.

## Working with other people

Conflicts don't go away, but they get smaller. If two people edit the same function at the same
time, that's a real conflict, and `sgt` isolates it to that one function. Everything else merges
right away with no conflict markers to resolve.

You reconcile the conflict with `sgt resolve <symbol>`. It won't let the conflict close until your
build and test checks pass, so a conflict is never closed by code nobody verified. `sgt land`
advances a shared branch one writer at a time, and only once those checks are green.

[docs/guide/workflows.md](docs/guide/workflows.md) walks through this end to end, along with
parallel agent sessions and the points where a person still has to step in.

## Docs

Start with [docs/guide/](docs/guide/). It covers how `sgt` models your code, a getting started
walk-through, the VS Code extension, and a tour by use case in
[workflows.md](docs/guide/workflows.md) that also lists today's limits.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for how to set up a development checkout, run the tests, and
cut a release.

## License

MIT. See [LICENSE](LICENSE).
