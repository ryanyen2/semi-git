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

`ugh save` commits the working tree as git would and prints each step it recorded with its
address, for example `cart.py::tax@3f2a1c0`: one save's change to one definition. Add `--as checkout` to give those steps a name. `ugh` never names
anything itself, so a name is always one you or your agent gave.

When the project configures a check (the tiers in `.sgt/oracle.json`), `ugh save` also runs it on
the commit it just made and records what the check observed there: its exit code, its output, and,
when the check runs Python 3.12 or later, which lines of your files it executed. A save then takes as
long as the check, and in return the observation comes from your real environment. The save prints
one line with the check's verdict, how many lines of its output changed against the parent's
observation, and how many of the save's steps the check ran. Observations stay on your machine
(`.sgt/local/observations/`); a clone has them only for the saves it made. `ugh status` says what
the check observed on HEAD.

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

Your agent can record its own work, and it should. `ugh save` takes the agent's own words as the
message and a name for the steps with `--as`. `ugh undo` reverses a save.

Two commands change state other people can see, and they stay with you rather than your agent.
They are `ugh sync` and `ugh resolve`, and no single command undoes them.

## Install the VS Code extension

Download `ugh-0.1.0.vsix` from the
[latest release](https://github.com/ryanyen2/ugh/releases/latest). In VS Code, open the
Extensions view, click the `...` menu, and choose "Install from VSIX".

Run `ugh init --agent` in your repo so the extension knows where `ugh` is.

## The problem it solves

Say a coding agent runs for an hour, touches a dozen files, and adds rate limiting, a caching layer,
and a retry policy in one pass. The caching layer turns out to be wrong.

In plain git you have two options, and both risk the work you want to keep. You can find every line
the caching code touched and revert those lines by hand. Or you can cherry-pick around a commit that
also holds the two features you want.

`ugh` already tracked each function's edits separately, so removing one is a single command. Here is
a run against a scratch repo.

```
$ ugh revert cache.py::get_cached --yes
  revert cache.py::get_cached
  goes: 1 step(s) (787d66b "agent pass: rate limit, caching")
  files: cache.py
  check: not run yet
  HEAD: not observed
  ✓ revert applied — 1 step(s) undone, 0 held, the parser passes. (`ugh undo` reverses this.)
```

`get_cached` is gone from `cache.py`. The other function in that file, `set_cached`, is untouched,
and every symbol in `rate_limit.py` and `retry.py` is byte for byte what it was before. The take-out
is a new commit, and the project's check (or, with none configured, the parser) ran on the result
before it was made. The commit's `Ugh-Out:` trailer names the ops of the steps it took out, so a
clone or a rebase still knows they are out. `ugh restore cache.py::get_cached` puts it back.

This scratch repo has no check, so HEAD was not observed and the answer says nothing about
behaviour. With a check, the preview lists the steps the check never ran on HEAD, and the applied
take-out says how many lines of the check's output changed. When that change is not empty and is
exactly the reverse of what the saves taken out changed in the output, the answer says "gone, as the
check sees it"; nothing else is called gone. A step the check never ran is listed as gone from the code, with
behaviour the check cannot confirm.

A step that other remaining work stands on is held, with the reason (other code uses it, a later
save changed it, a later save rewrote the same lines) and the saves that hold it. `--whole` takes
the work out together with everything that stands on it. The same command takes a name, a save (its
commit sha), a range of saves, a file, or one step.
[docs/guide/workflows.md](docs/guide/workflows.md) covers the details.

## Daily commands

Every command that acts on steps takes an address:

| Address | The steps it names |
| --- | --- |
| `checkout` | the steps named `checkout` |
| `checkout@3f2a1c0` | those steps in one save |
| `cart.py` | the steps on one file |
| `cart.py::tax` | the steps on one top-level definition |
| `cart.py::tax@3f2a1c0` | one step |
| `3f2a1c0` | one save's steps (anything `git rev-parse` takes) |
| `3f2a1c0..9b1e004`, `3f2a1c0..` | the saves in that range (`..` alone runs through HEAD) |

An address reaches steps that are out as well as steps that are in. A word that names two of these
(a name that is also a branch) is refused, naming both, and a word that names none is refused with
the nearest `ugh find` hits.

| Command | What it's for |
| --- | --- |
| `ugh init` | Read your existing git history into `ugh`. Run once per repo. |
| `ugh save -m "..." [--as <name>]` | Record the edits you just made; `--as` names this save's steps. |
| `ugh name <address> <name>` | Give a name to the steps at an address. This is also how you merge, split or move them. |
| `ugh rename <name> <new>` | Rename every step with a name; an existing name absorbs them. |
| `ugh log` | The saves, newest first: each with the names on its steps, how many are unnamed, and how many are out. |
| `ugh status` | What needs attention right now: two-version choices a sync left, edits not saved yet, and the plan. |
| `ugh show <address>` | Its steps, the ask behind each save, and what taking them out would take. |
| `ugh find <words>` | Addresses whose names, save subjects, definitions or captured asks contain the words. |
| `ugh undo` | Reverse your last `ugh` command as a new change, rather than by rewriting history. It shows what it will do first. |
| `ugh revert <address>` | Take steps out as a new edit; what other work stands on is held, with the reason. `--whole` takes what stands on them too. |
| `ugh restore <address>` | Bring back steps a revert took out, combined with edits made since. |
| `ugh resolve <address>` | Show both versions of a definition a sync left as a choice; `--theirs`, `--ours` or `--apply` (your hand edit) settles it. |
| `ugh plan [<name>...] [--done]` | Declare names before you build; `ugh plan` reports which have saves and which are missing. |
| `ugh check` | Verify the op store. `--repair` re-derives the record after a git history rewrite. |
| `ugh sync` | Fetch a teammate's work and merge it definition by definition. Names merge per step. |
| `ugh push` | Push. If the push is rejected, it points you at `ugh sync`. |
| `ugh mcp` | Run an MCP server so a coding agent can call `ugh` directly. |

Run `ugh help` for the full list.

## How it works

`ugh` reads each commit and breaks it into per-symbol edits. A save is a plain git commit, and
every clone reads the same edits out of the same history, so nothing but the names you gave
travels beside your branches. Your codebase at any moment is the set of edits in its history, and
`ugh` can rebuild your files from that set exactly. Run it and you get back, byte for byte, what is
checked out.

Taking a step out is itself a new edit, and a step that other remaining work stands on is held, so
whatever is left still rebuilds into working files. Where two people changed the same function and
the history merged them, the merge decides what the function is. `ugh check` checks that the
current state is still valid, and `ugh check --tree` that the files it builds match what git has.

[docs/design/2026-11-16-removal-sets.md](docs/design/2026-11-16-removal-sets.md) has the model.

For the curious, `ugh` calls one per-symbol edit an op, the current set of edits an ideal, and
rebuilding your files from that set the fold.

## Working with other people

`ugh sync` merges a teammate's branch one definition at a time. A definition only one side changed
takes that side; one both sides changed on different lines combines both; one both sides changed
on the same lines keeps yours in the file, with no conflict markers, and becomes a two-version
choice. `ugh save` waits until you settle each choice with `ugh resolve <address>`, which shows both
versions side by side. Because the merge works by definition rather than by line across the file,
two people adding different functions at the same place merge cleanly where git would conflict,
yours first. If you merge with plain git instead, you get git's result and `ugh` accepts it.

[docs/guide/workflows.md](docs/guide/workflows.md) walks through this end to end, along with the
points where a person still has to step in.

## Docs

Start with [docs/guide/](docs/guide/). It covers a getting started walk-through and a tour by use
case in [workflows.md](docs/guide/workflows.md) that also lists today's limits.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for how to set up a development checkout, run the tests, and
cut a release.

## License

MIT. See [LICENSE](LICENSE).
