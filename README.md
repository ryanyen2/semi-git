# semi-git (`sgt`)

`sgt` versions a codebase by its **features and symbols**, not its line diffs.

Git operates at the code-artifact level: a commit is a set of line changes, and that's the
entire vocabulary. It has no notion of "this hunk belongs to feature X" or "this function is
still the same logical thing it was three commits ago, even though it moved and got renamed
along the way." `sgt` sits on top of an ordinary git repo and works one level up: it mines every
commit down to symbol-level edits ("ops"), tracks each symbol's identity as it changes, and
groups the live op graph into a **feature tree** — a map from "what changed" to "why" — while
staying exactly faithful to the bytes on disk (folding the current op-set back together
reproduces exactly what's checked out).

**`sgt` never authors code.** You, or your coding agent (Claude Code or anything else), write
and edit files exactly as you always have. `sgt` only observes, models, and — when you ask —
surgically edits *which parts of that history are currently "in."*

## Why this matters

A coding agent runs for an hour, touches a dozen files, and lands rate limiting, a caching
layer, and a retry policy in one pass. The caching layer turns out to be wrong. In git, pulling
just that back out means bisecting scattered commits or hand-editing a diff, hoping you don't
drag the other two features down with it.

In `sgt`, each symbol's edits are already tracked individually, so removing exactly one of them
is a single command — verified live on a scratch repo while writing this doc:

```
$ sgt revert cache.py::get_cached
✓ [revert] cache.py::get_cached
    removed 1 op(s): a776620b9b56
    affected: cache.py::get_cached
```

`get_cached` is gone from `cache.py`; `set_cached` in the same file, and every symbol in
`rate_limit.py` and `retry.py`, are untouched — byte for byte, not just "no visible diff."
`sgt restore cache.py::get_cached` puts it straight back. The same edit works scoped to a whole
**feature** (`sgt revert <feature>`) or a whole **agent session** (`sgt revert --session
<name>`) once the map has grouped the ops that way — see
[`docs/guide/workflows.md`](docs/guide/workflows.md) for what that grouping needs to actually
kick in, candidly, rather than overclaiming it.

## The model, briefly

History = a content-addressed DAG of ops, one per symbol-level edit. A codebase's current state
= an *ideal* — a downward-closed, fork-free subset of that DAG. `code(ideal)` is a deterministic
byte-fold back into real files. Every mutating verb preserves that invariant; `sgt fsck` checks
it.

## Usage

```bash
sgt init                            # mine existing git history into the op store

# daily loop
sgt save -m "..."                   # mine your edits + commit a witness (the "commit" step)
sgt switch <branch>                 # sgt-aware `git switch`
sgt undo                            # invert your last ideal edit (forward-only, never rewinds)

# inspect
sgt status / sgt map / sgt blame <file>    # coverage, the feature tree, per-line attribution
sgt log / sgt state / sgt diff <a> <b>     # the op DAG, current ideal, semantic diff
sgt history                         # mined commits + every op's kind/feature/commit-index

# surgical edits (ideal algebra)
sgt revert [--emit] <ref>           # remove a symbol/op/feature + everything built on it
sgt revert --session <name>         # ...or everything one agent session landed
sgt restore [--emit] <ref>          # the inverse: re-add an op and its prerequisites

# regroup the feature tree itself (metadata-only, instant, content untouched)
sgt merge <survivor> <absorbed> / sgt split <feature> / sgt rename <feature> "..." / sgt move

# where the ideal algebra can't express an edit exactly (a same-symbol fork, a two-concern op):
sgt merge-op <a> <b> / sgt split-op <op> / sgt transplant <op>... --onto <ref>
sgt fulfill <draft-id> --from-tree  # supply the reconciled content sgt drafted a hollow for
sgt land                            # commit a staged rewrite, gated on the build/test oracle

# collaboration
sgt sync [remote] [branch]          # fetch + union a teammate's work; surfaces same-symbol forks
sgt forks                           # open forks + their `sgt merge-op` remedies
sgt push [remote] [branch]          # non-forcing push; a rejection routes you to `sgt sync`
sgt land <branch>                   # CAS-advance a shared branch to a verified op-set

# agent sessions
sgt session start <name>            # a scratch git worktree on its own branch, for one agent
sgt session status / land / gc      # early-fork warnings, CAS-land, reap dead sessions

# review + publish
sgt propose create/status/land/render/publish   # base+delta review object, partial-accept by
                                                  # feature, GitHub PR create/update via `gh`

sgt oracle run                      # build/test tiers against the current op-set
sgt tui / sgt mcp                   # terminal UI / stdio MCP server for coding-agent clients
```

`merge`/`split`/`rename`/`move` relabel the feature tree; `merge-op`/`split-op`/`transplant` fix
the op *chain* itself (a same-symbol fork, a op that mixes two concerns) — easy to conflate by
name, distinct in purpose. Full verb reference: `sgt help`.

## Multi-user collaboration

Conflicts don't disappear — they change shape. Two people editing the same function
independently still produces a genuine conflict, but `sgt sync` isolates it to exactly that
symbol (a "fork") and merges everything else automatically and immediately; resolving it is
`sgt merge-op` + `sgt fulfill` + `sgt land`, gated on your configured build/test oracle before it
commits. [`docs/guide/workflows.md`](docs/guide/workflows.md) walks through this end to end,
plus parallel agent sessions and where a human still has to step in today.

## Docs

- [`docs/guide/`](docs/guide/) — the mental model, getting started, VS Code extension, TUI, and
  [`workflows.md`](docs/guide/workflows.md) for a use-case-by-use-case tour, including what's
  still being hardened.
- [`FINDINGS.md`](FINDINGS.md) — what's verified, and the known v1 limitations.
- [`docs/plans/`](docs/plans/) — active and historical implementation plans.

## Development

Requires [`uv`](https://docs.astral.sh/uv/).

```bash
uv venv --python 3.12
uv pip install -e ".[dev]"
uv run pytest
```

## Status

The current implementation is the **operation-ideal kernel** (mined op DAG → order-ideal state →
deterministic fold) with a **feature lens** on top (the hierarchical map, `merge`/`split`/
`rename`/`move`, session/provenance attribution, sync/land/propose collaboration). A 2026-07-12
multi-agent review found real gaps in the kernel's invariant — ordinary git history (a file
deleted and re-added) could violate it and previously caused silent file deletion; `land`
persisted state before its gate; sync could resurrect a teammate's revert — and a
[fix plan](docs/plans/2026-07-12-001-fix-kernel-invariants-and-sync-plan.md) is actively landing
against it. See `FINDINGS.md` for the full, current disposition.
