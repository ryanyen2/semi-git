# semi-git (`sgt`)

`sgt` sits on top of an ordinary git repo and tracks your code function by function, not just file
by file. Because it follows each function and class on its own, you can pull out — or bring back —
exactly one function's or one feature's worth of history, without disturbing anything else. `sgt`
never writes your code: you or your coding agent edit files the same way you always have, and `sgt`
just keeps track of which of those edits are currently in.

## Install and first run

You need [`uv`](https://docs.astral.sh/uv/).

```bash
uv venv --python 3.12
uv pip install -e ".[entities,lens]"
```

Two extras matter: `entities` lets `sgt` read your code into individual symbols, and `lens` lets it
build the feature tree. Add `dev` (`".[entities,lens,dev]"`) if you also want to run the tests.

The daily loop needs no API key. A few optional steps call an LLM — the feature labeler, `sgt plan
intake`, `sgt intent build`, and the plain-English forms of `revert`/`restore`. Set
`OPENAI_API_KEY` for those. The endpoint is env-driven, so you are not tied to OpenAI: point
`OPENAI_BASE_URL` at any OpenAI-compatible gateway (a litellm proxy serving Claude models, say) and
pick the model with `SGT_MODEL` (default `gpt-5.4-mini`).

Then, inside a git repo:

```bash
sgt init                              # read your existing git history in, once per repo
# edit files with your editor or agent, exactly as always
sgt save -m "add input validation"    # record those edits, and see which feature(s) they landed in
```

## The problem it solves

Say a coding agent runs for an hour, touches a dozen files, and lands rate limiting, a caching
layer, and a retry policy in one pass. The caching layer turns out to be wrong. In plain git,
removing only that work means finding every line the caching code touched and reverting those lines
by hand, or cherry-picking around a commit that also holds the two features you want to keep. Either
way you risk taking the other two features down with it.

`sgt` already tracked each function's edits on their own, so removing one is a single command. This
ran against a scratch repo while writing this doc:

```
$ sgt revert cache.py::get_cached
 ▸ rewind  cache.py::get_cached

 removes 1 edit(s) across 1 symbol(s) · 1 file(s): cache.py
  ✓ revert applied — 1 edit(s) removed, 0 added. (`sgt undo` reverses this.)
```

`get_cached` is gone from `cache.py`. `set_cached`, the other function in the same file, is
untouched, and every symbol in `rate_limit.py` and `retry.py` is byte for byte what it was before.
`sgt restore cache.py::get_cached` puts it back. The same command works on a whole feature (`sgt
revert <feature>`) or a whole agent session (`sgt revert --session <name>`) once those edits have
been grouped that way. See [`docs/guide/workflows.md`](docs/guide/workflows.md) for when that
grouping is reliable and when it is not.

## Daily commands

A target for `revert`/`restore` can be a `file::symbol` name, an op id, a feature, or, with a key
set, a plain-English phrase.

| Command | What it's for |
| --- | --- |
| `sgt init` | Read your existing git history into `sgt`. Run once per repo. |
| `sgt save -m "..."` | Record the edits you just made, and name the feature(s) they landed in. |
| `sgt now` | Where am I? What you asked for, what's unsaved, what needs you, and the one next thing to do. |
| `sgt log` | What you did, newest first. `--map` (features over time), `--tree` (the feature tree), `--summary` (what needs attention). |
| `sgt status` | What needs attention right now. The same view as `sgt log --summary`. |
| `sgt show <spec> [<file>]` | Read a file as it was at a past point, or list what existed there. Nothing is checked out. |
| `sgt why <target>` | Why this code exists: the prompt or plan step behind a commit, op, or symbol. |
| `sgt undo` | Step back: reverse your last `sgt` command, as a new change rather than by rewriting history. It shows what it will do before doing it. |
| `sgt revert <target>` | Remove one symbol, feature, or session's worth of work, plus anything built on it. |
| `sgt restore <target>` | Bring a removed thing back, along with anything it needs. |
| `sgt resolve <symbol>` | Walk through reconciling a symbol that ended up edited two different ways at once. |
| `sgt switch <branch>` | Switch branches and rebuild that branch's files. |
| `sgt diff <a> <b>` | Show which symbol-level edits differ between two states. |
| `sgt intent ...` | Browse the "why" behind a feature — the segments of its history, spanning features. |
| `sgt plan ...` | State a plan up front so later saves can match your work against it. |
| `sgt feature ...` | Re-group or rename features. Labels only — never touches your code. |
| `sgt advanced ...` | Rare and maintenance commands: fork surgery, `advanced fsck`, and the raw plumbing. |
| `sgt sync` | Fetch a teammate's work and merge it, flagging any real conflict. |
| `sgt land <branch>` | Advance a shared branch — one writer at a time, and only once your checks pass. |
| `sgt push` | Push; if it's rejected, it points you at `sgt sync`. |
| `sgt propose ...` | Open a review object (like a PR) a reviewer can accept feature by feature. |
| `sgt session ...` | Run an agent in its own scratch worktree, then land its work. |
| `sgt mcp` | Run an MCP server so a coding agent can call `sgt` directly. |

Run `sgt help` for the full list, including the rare verbs under `sgt advanced` and `sgt feature`.

## How it works

`sgt` reads each commit and breaks it into per-symbol edits. Your codebase at any moment is just the
set of edits that are currently in, and `sgt` can rebuild your files from that set exactly — run it
and you get back, byte for byte, what is checked out. Removing an edit also removes anything built on
top of it, so whatever is left still rebuilds into working files. Two versions of the same function
can never both be in at once; when that happens — say two people edit it in parallel — `sgt` calls
it a fork and asks you to reconcile it, rather than picking a side. `sgt advanced fsck` checks that
the current state is still valid and that the files it builds match what git has.

[`docs/guide/the-semantic-tree.md`](docs/guide/the-semantic-tree.md) has the formal version.

> For the curious: `sgt` calls one per-symbol edit an *op*, the current set of edits an *ideal*, and
> rebuilding your files from it the *fold*.

## Working with other people

Conflicts do not go away, but they get smaller. If two people edit the same function at the same
time, that is a real conflict; `sgt` isolates it to that one function — a fork — and merges
everything else right away, with no conflict markers to resolve. You reconcile the fork with `sgt
resolve <symbol>`, which will not let it close until your build and test checks pass, so a conflict
is never closed by code nobody verified. `sgt land` advances a shared branch one writer at a time,
and only once those checks are green. [`docs/guide/workflows.md`](docs/guide/workflows.md) walks
through this end to end, along with parallel agent sessions and the points where a person still has
to step in.

## Docs

[`docs/guide/`](docs/guide/) is the place to start: how `sgt` models your code, a getting-started
walk-through, the VS Code extension, and a tour by use case ([`workflows.md`](docs/guide/workflows.md))
that also lists today's limits.

## Development

```bash
uv venv --python 3.12
uv pip install -e ".[entities,lens,dev]"
uv run pytest
```
