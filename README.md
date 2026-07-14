# semi-git (`sgt`)

`sgt` tracks your codebase as a set of symbol-level edits on top of an ordinary git repo. It
reads every commit, breaks each one down into per-symbol changes (it calls these "ops"), follows
each symbol as it moves and gets renamed, and groups the live ops into a feature tree. You can
then remove or re-add exactly one function's worth of history without touching anything else.

`sgt` never writes your code. You, or your coding agent, edit files the same way you always have.
`sgt` reads those edits, models them, and when you ask, changes which parts of the history are
currently in the working tree.

## The problem it solves

A coding agent runs for an hour, touches a dozen files, and lands rate limiting, a caching layer,
and a retry policy in one pass. The caching layer is wrong. In plain git, removing only the
caching work means finding every line it touched and reverting those lines by hand, or
cherry-picking around a commit that also holds the two features you want to keep. Either way you
risk dragging the other two down with it.

`sgt` already tracks each symbol's edits on their own, so removing one of them is a single
command. This ran against a scratch repo while writing this doc:

```
$ sgt revert cache.py::get_cached
✓ [revert] cache.py::get_cached
    removed 1 op(s): a776620b9b56
    affected: cache.py::get_cached
```

`get_cached` is gone from `cache.py`. `set_cached` in the same file is untouched, and every
symbol in `rate_limit.py` and `retry.py` is byte for byte the same as before. `sgt restore
cache.py::get_cached` puts it back. The same command works on a whole feature (`sgt revert
<feature>`) or a whole agent session (`sgt revert --session <name>`) once the feature tree has
grouped the ops that way. See [`docs/guide/workflows.md`](docs/guide/workflows.md) for when that
grouping is reliable.

## The model

History is a content-addressed set of ops, one per symbol-level edit. The current state of your
codebase is an ideal, which is a subset of those ops that is closed downward (if an op is in, so
is everything it was built on) and fork-free (no two competing versions of the same symbol).
`code(ideal)` folds that set back into real files, and the result is byte-for-byte what is
checked out. Every command that changes state keeps the ideal valid, and `sgt fsck` checks it.

## Commands

```bash
sgt init                            # read existing git history into the op store

# daily loop
sgt save -m "..."                   # read your edits and commit a record of them
sgt switch <branch>                 # git switch, and rebuild that branch's files from its ideal
sgt undo                            # invert your last change (it moves forward, it never rewinds)

# inspect
sgt status                          # files, symbols, features, coverage, oracle status, drift
sgt map                             # the feature tree
sgt blame <file>                    # which feature owns each symbol in a file
sgt log / sgt state / sgt diff <a> <b>   # the op set, the current ideal, a semantic diff
sgt history                         # mined commits, and each op's kind, feature, and commit index

# add or remove ops
sgt revert [--emit] <ref>           # remove a symbol, op, or feature, and anything built on it
sgt revert --session <name>         # remove everything one agent session landed
sgt restore [--emit] <ref>          # re-add an op and everything it needs

# regroup the feature tree (metadata only, instant, does not touch your code)
sgt merge <survivor> <absorbed>     # fold one feature into another
sgt split <feature>                 # cut one feature into two
sgt rename <feature> "..."          # change a feature's label
sgt move <op>... --to <feature>     # move ops to another feature

# when the ideal math cannot express an edit exactly (two competing versions, or one op that
# mixes two concerns)
sgt merge-op <a> <b>                # draft a placeholder op that reconciles a fork
sgt split-op <op>                   # draft an intermediate cut of a two-concern op
sgt transplant <op>... --onto <ref> # draft ops backported onto another chain
sgt fulfill <draft-id> --from-tree  # supply the real content for a drafted placeholder
sgt land                            # commit a staged rewrite, once the build and test oracle passes

# collaboration
sgt sync [remote] [branch]          # fetch and merge a teammate's work, and report same-symbol forks
sgt forks                           # open forks and the merge-op that resolves each one
sgt push [remote] [branch]          # push, and route you to sgt sync if it is rejected
sgt land <branch>                   # advance a shared branch to a verified op set

# agent sessions
sgt session start <name>            # a scratch git worktree on its own branch for one agent
sgt session status / land / gc      # fork warnings, land onto main, reap dead sessions

# review and publish
sgt propose create/status/land/render/publish   # a base-plus-delta review object, accept by
                                                 # feature, open or update a GitHub PR through gh

sgt oracle run                      # run the build and test tiers against the current op set
sgt mcp                             # a stdio MCP server for coding-agent clients
```

`merge`, `split`, `rename`, and `move` change the feature tree's labels. `merge-op`, `split-op`,
and `transplant` change the op chain itself. The names look similar and the jobs are different.
Run `sgt help` for the full list.

## Working with other people

Conflicts do not go away. Two people editing the same function at the same time is a real
conflict. What `sgt` changes is the size of it. `sgt sync` isolates the conflict to that one
symbol, which it calls a fork, and merges everything else right away. You resolve the fork with
`sgt merge-op`, then `sgt fulfill`, then `sgt land`. `land` runs your build and test oracle first
and refuses to commit a version that has not passed. [`docs/guide/workflows.md`](docs/guide/workflows.md)
walks through this end to end, along with parallel agent sessions and the cases where a person
still has to step in.

## Docs

- [`docs/guide/`](docs/guide/) has the model, a getting-started page, the VS Code extension, and
  [`workflows.md`](docs/guide/workflows.md) for a tour by use case, including the current limits.
- [`FINDINGS.md`](FINDINGS.md) records what is verified and the known v1 limitations.
- [`docs/plans/`](docs/plans/) holds the active and past implementation plans.

## Development

You need [`uv`](https://docs.astral.sh/uv/). Mining symbols needs the tree-sitter grammars in the
`entities` extra, and the feature tree needs the clustering libraries in the `lens` extra.

```bash
uv venv --python 3.12
uv pip install -e ".[entities,lens,dev]"
uv run pytest
```

## Status

The implementation is the operation-ideal kernel: it mines an op set, holds the current state as
an ideal, and folds it back to files deterministically. On top sits the feature tree, the
`merge`/`split`/`rename`/`move` verbs, session and provenance attribution, and the
sync/land/propose collaboration path.

A 2026-07-12 review found four ways ordinary git history could break the kernel's invariant, and
all four are fixed. A file that was deleted and re-added no longer disappears from the working
tree and no longer causes a silent delete. `land` no longer writes state before its build and
test gate runs. `sync` no longer resurrects a teammate's revert. `FINDINGS.md` has the full
current disposition and the limitations that remain, including that `sgt status` is slow on a
large op store.
