# semi-git (`sgt`)

`sgt` sits on top of an ordinary git repo and tracks your code at the level of individual
functions and classes, not just files and lines. It reads every commit, breaks each one down into
per-symbol changes it calls "ops", and follows each symbol as it moves between files or gets
renamed. It groups the live ops into a feature tree, so you can remove or bring back exactly one
function's worth of history without touching anything else.

`sgt` never writes your code. You, or your coding agent, edit files the same way you always have.
`sgt` reads those edits, keeps a model of them, and when you ask, changes which parts of the
history are in your working tree.

## The problem it solves

Say a coding agent runs for an hour, touches a dozen files, and lands rate limiting, a caching
layer, and a retry policy in one pass. The caching layer turns out to be wrong. In plain git,
removing only that work means finding every line the caching code touched and reverting those
lines by hand, or cherry-picking around a commit that also holds the two features you want to
keep. Either way you risk taking the other two features down with it.

`sgt` already tracked each symbol's edits on its own, so removing one of them is a single command.
This ran against a scratch repo while writing this doc:

```
$ sgt revert cache.py::get_cached
✓ [revert] cache.py::get_cached
    removed 1 op(s): a776620b9b56
    affected: cache.py::get_cached
```

`get_cached` is gone from `cache.py`. `set_cached`, the other function in the same file, is
untouched, and every symbol in `rate_limit.py` and `retry.py` is byte for byte what it was before.
`sgt restore cache.py::get_cached` puts it back. The same command works on a whole feature (`sgt
revert <feature>`) or a whole agent session (`sgt revert --session <name>`) once the feature tree
has grouped the ops that way. See [`docs/guide/workflows.md`](docs/guide/workflows.md) for when
that grouping is reliable and when it is not.

## How it thinks about your code

History is a set of ops, one per symbol-level edit. The current state of your codebase is a
subset of that set, and it has to follow two rules to be valid:

- If an op is in, everything it was built on has to be in too.
- At most one version of each symbol is in at a time. Two competing versions of the same function
  is called a fork, and a valid state never holds one.

`sgt` calls a set that follows both rules an ideal, and it can turn any ideal back into real files
by stitching each live symbol's current code together in order. That fold is exact: run it and you
get back byte for byte what is checked out. Every command that changes state keeps the result a
valid ideal, and `sgt fsck` checks that it still is.

## Commands

```bash
sgt init                            # read your existing git history into the op store

# daily loop
sgt save -m "..."                   # read your edits and commit a record of them
sgt switch <branch>                 # git switch, and rebuild that branch's files from its ideal
sgt undo                            # invert your last mutating command (moves forward, never rewinds history)

# inspect — sgt log is the one surface, its modes are the old views
sgt log                             # the lane×commit grid (the default)
sgt log --tree                      # the feature tree
sgt log --rail                      # episode rail (vertical git-log): what I did, in order
sgt log --summary                   # files, symbols, features, coverage, oracle status, drift
sgt log --ops                       # the raw op DAG
sgt diff <a> <b>                    # a symbol-level diff of two ideals
sgt advanced blame <file>           # which feature owns each symbol in a file
sgt advanced state / history        # the current state; mined commits and each op's kind + feature
sgt intent list/show/build          # intent-clustering overlay: themes that span features

# add or remove ops (each shows the consequence and asks before writing)
sgt revert <sel>                    # remove a symbol, op, or feature, and anything built on it
sgt revert --session <name>         # remove everything one agent session landed
sgt revert <feature>@<n>            # rewind one checkpoint (intent segment) of a feature
sgt restore <sel>                   # add an op back, along with everything it needs
sgt revert "<intent>" / sgt restore "<intent>"  # no exact name to give it? an LLM proposes
                                                 # candidates, previews each, and asks before applying
sgt resolve <symbol>                # guided same-symbol fork resolution (merge-op → fulfill → land)

# regroup the feature tree (labels only, instant, never touches your code)
sgt feature regroup merge <survivor> <absorbed>   # fold one feature into another
sgt feature regroup split <feature>               # cut one feature into two
sgt feature rename <feature> "..."                # change a feature's label
sgt feature regroup move <op>... --to <feature>   # move ops to another feature
sgt feature select/why <ref>                      # explain a feature's closure / an op's attribution

# for edits the ideal can't represent on its own (two competing versions, or one op mixing
# two unrelated changes) — the rare kernel verbs live under `sgt advanced`
sgt advanced merge-op <a> <b>       # draft a placeholder op that reconciles a fork
sgt advanced split-op <op>          # draft an intermediate cut of an op that mixes two changes
sgt advanced transplant <op>... --onto <ref>   # draft ops carried over onto another chain
sgt advanced fulfill <draft-id> --from-tree    # supply the real content for a drafted placeholder
sgt advanced commit                 # commit a staged rewrite, once your build and test checks pass

# the agentic loop: state a plan, then let save match the work against it
sgt plan intake "<text>"            # decompose a stated plan into predicted hollow ops
sgt plan status / done / abandon    # a plan's match state; close it; drop it
sgt save --resolve-plan             # settle an ambiguous plan-step match a save couldn't auto-confirm

# collaboration
sgt sync [remote] [branch]          # fetch a teammate's work, merge the op sets, and report any fork
sgt advanced forks                  # open forks, and the merge-op that resolves each one
sgt push [remote] [branch]          # push, and tell you to run sgt sync if it gets rejected
sgt land <branch>                   # advance a shared branch to a verified op set

# agent sessions
sgt session start <name>            # a scratch git worktree on its own branch, for one agent
sgt session status / land / gc      # fork warnings, land onto main, clean up dead sessions

# review and publish
sgt propose create/status/land/render/publish   # a review object with a base and a set of
                                                 # changes on top, acceptable feature by feature,
                                                 # opened or updated as a GitHub PR through gh

sgt advanced oracle run             # run your build and test checks against the current op set
sgt mcp                             # a stdio MCP server for coding-agent clients
```

`sgt log` is the single inspection surface — the old `sgt map`/`graph`/`episodes`/`status` are now
its `--tree`/(default grid)/`--rail`/`--summary` modes. The daily spine stays top-level; rare and
maintenance verbs live under `sgt advanced` and `sgt feature`. `sgt feature regroup merge/split/move`
and `sgt feature rename` only change labels in the feature tree. `sgt advanced merge-op`, `split-op`,
and `transplant` change the actual chain of ops. The names look similar but the jobs are different.
Run `sgt help` for the full list.

`sgt revert` and `sgt restore` also take a plain-English target, e.g. `sgt revert "the caching
layer"`, when no op id, symbol name, or feature label matches exactly. An LLM proposes ranked
candidates grounded in your repo's own ops and features. It only ever picks what to point the
command at. It never writes code, and it never applies anything without your confirmation, either
`--yes` for the top candidate or a re-run with the exact name it printed. This needs
`OPENAI_API_KEY`. With no key set, the command fails with a clear message instead of guessing.

## Working with other people

Conflicts do not go away. If two people edit the same function at the same time, that is a real
conflict. What `sgt` changes is the size of it. `sgt sync` isolates the conflict to that one
symbol, which it calls a fork, and merges everything else right away. You resolve the fork with
`sgt resolve <symbol>`, which sequences the merge-op → fulfill → oracle → land steps for you (the
raw kernel verbs are still there as `sgt advanced merge-op` / `advanced fulfill` / `advanced
commit`). The oracle gate runs your build and test checks first and refuses to close a fork with
code that has not passed them, so a fork can't be closed by code nobody verified. [`docs/guide/workflows.md`](docs/guide/workflows.md) walks through this case end
to end, along with parallel agent sessions and the points where a person still has to step in.

## Docs

- [`docs/guide/`](docs/guide/) covers how `sgt` represents your code, a getting-started walk-through,
  the VS Code extension, and [`workflows.md`](docs/guide/workflows.md), a tour by use case that
  also lists today's limits.
- [`FINDINGS.md`](FINDINGS.md) records what is verified and what is still limited in this version.
- [`docs/plans/`](docs/plans/) holds the active and past implementation plans.

## Development

You need [`uv`](https://docs.astral.sh/uv/). Reading symbols out of your code needs the
tree-sitter grammars in the `entities` extra, and building the feature tree needs the clustering
libraries in the `lens` extra.

```bash
uv venv --python 3.12
uv pip install -e ".[entities,lens,dev]"
uv run pytest
```

## Status

`sgt` is built around an operation-ideal kernel: it reads a set of ops from your history, holds
the current state as an ideal, and turns that back into files exactly. On top of that sits the
feature tree, the `sgt feature regroup`/`rename` commands, tracking of who or what session made
each change, and the sync, land, and propose commands for working with other people.

A review on 2026-07-12 found four ways that ordinary git history could break the kernel's rules,
and all four are now fixed. A file that was deleted and then re-added no longer disappears from
your working tree. `sgt land` no longer writes state before its build and test check runs, so a
failed or interrupted land can't leave things half-changed. `sgt sync` no longer brings back a
change a teammate had deliberately reverted. See [`FINDINGS.md`](FINDINGS.md) for the full
picture, including that `sgt log --summary` is currently slow on a large op store.
