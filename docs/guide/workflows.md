# User workflows

This is a tour of what `sgt` does today, one use case at a time. Each section names the commands,
walks a real example, and says where the feature is solid and where it still has limits.
[`FINDINGS.md`](../../FINDINGS.md) has the exhaustive version of the limits.

Read [The model](the-semantic-tree.md) first. The short version: history is a set of
symbol-level edits called ops, the current state of your codebase is a subset of those ops called
the ideal, and folding the ideal back together reproduces exactly what is on disk. `sgt` never
writes your code. You or your agent do that, and `sgt` tracks which edits are in.

Every example below ran against a scratch repo while writing this doc, or comes from the
project's own test suite, cited by file. None of the terminal output is invented.

## 1. The solo daily loop

This is git's daily loop with `sgt` reading along.

```bash
sgt init                    # once per repo: read existing history into the op store
# edit files with your editor or agent, the same as always
sgt save -m "add input validation"   # read your edits into ops and commit a record
sgt status                  # files, symbols, features, coverage, and any open forks or drift
sgt undo                    # step back: invert your last save, revert, or restore
```

`sgt switch <branch>` is the `sgt` version of `git switch`. It also rebuilds that branch's files
from its ideal, so the file content and the tracked state move together. This layer does little
on its own. It is what lets the later sections work without changing how you work day to day.

`sgt undo` moves forward only. It records an inverting change rather than rewinding history, so
you never lose the record of what happened.

## 2. Remove one thing from a big, tangled edit

This is the main reason to use `sgt` instead of plain git.

An agent, or you moving fast, lands three unrelated changes in one sweep: rate limiting, a
caching layer, and a retry policy, spread across several files, in one commit. The caching layer
is wrong. In plain git, undoing only that means finding every line the caching work touched and
reverting those lines by hand, or cherry-picking around a commit that also holds the two features
you want to keep, and hoping the surgery does not nick anything else.

`sgt` mined every edit into per-symbol ops already, so you can name the exact symbol and remove
it plus anything built on top of it. This is real output from a scratch repo built for this doc:
three files, one function each, committed together like an agent's single-pass edit.

```
$ sgt revert cache.py::get_cached
✓ [revert] cache.py::get_cached
    removed 1 op(s): a776620b9b56
    affected: cache.py::get_cached
```

After that command, `get_cached` is gone from `cache.py`. `set_cached`, the other function in the
same file, is untouched. `rate_limit.py` and `retry.py` are byte for byte what they were before.
`sgt restore cache.py::get_cached` puts it back:

```
$ sgt restore cache.py::get_cached
✓ [restore] cache.py::get_cached
    added 1 op(s): a776620b9b56
    affected: cache.py::get_cached
```

The target here was a `file::symbol` name. It can also be an op id, a feature, or an agent
session:

```bash
sgt revert <feature-id-or-label>     # every op the feature tree grouped under that feature
sgt revert --session <session-name>  # every op attributed to one agent session's landing
```

How reliable each of these is depends on where the grouping comes from.

- Symbol-level revert always works. It needs only the op set, nothing else.
- Feature-level revert needs the feature tree to have grouped the ops first. `sgt map` builds
  that grouping from how symbols change together across your history, so it needs enough commits
  and churn to find real seams. On a brand-new repo it reports one feature for everything,
  because there is no signal to split on yet. Use `sgt merge`, `sgt split`, `sgt rename`, and
  `sgt move` to correct or seed the grouping by hand.
- Session-level revert does not depend on the grouping at all. `sgt` stamps structured
  provenance on every op a named session lands, so `sgt revert --session <name>` is exact from
  the first run, before there is any history to cluster.

So symbol-level revert and session-level revert are the reliable tools today. Feature-level
revert works well once `sgt map`, or your own corrections, has had real history to work with. Run
`sgt blame <file>` to see whether the current grouping covers the file you care about before you
trust it.

### When you do not know the exact name

If none of op-id, `file::symbol`, or feature label matches, `sgt revert`/`sgt restore` fall to
one more rung: a natural-language target.

```
$ sgt revert "the caching layer"
? [revert] 'the caching layer' did not resolve; did you mean:
  1. cache.py::get_cached (symbol) — the only caching-related function in the diff
     would remove 1 op(s), add 0 op(s)
     re-invoke: sgt revert cache.py::get_cached
```

An LLM proposes ranked candidate refs grounded in your repo's own op ids, symbols, and feature
labels — it never invents one. Each candidate is re-planned for real before it is shown, so a
hallucinated or no-longer-live ref never makes it into the list. Nothing is applied by default;
re-run with the printed concrete ref to apply deterministically, or pass `--yes` to apply the
top candidate directly. Needs `OPENAI_API_KEY`; with no key set, the command fails with a clear
message instead of guessing.

This is the least reliable rung, by design: it is a proposal, always previewed, and every
*applied* edit is still the same exact, deterministic op-id/symbol/feature revert as above —
just addressed by an LLM's guess at what you meant, not by a fuzzy match on the code itself.

## 3. Working with other people: sync and forks

Two people editing the same function at the same time is a real conflict, and `sgt` does not make
it go away. What it changes is the size of the conflict. `sgt` isolates it to the one symbol both
people touched, which it calls a fork, and merges everything else right away with no markers to
resolve.

This example comes from the project's own test suite
(`tests/core/test_sync.py::test_sync_records_a_fork_and_lands_the_forked_symbol_at_the_common_ancestor`).

Alice and Bob both cloned `main.py`, where `foo()` returns `1`.

Alice fixes a bug in `foo()` so it returns `999`:

```bash
sgt save -m "fix: foo off-by-one"
git push
```

That goes through cleanly, because she pushed first.

Bob, not knowing about Alice's fix, changes the same function to return `42`:

```bash
sgt save -m "fix: foo edge case"
git push        # rejected, because main has moved
sgt sync origin main
```

`sync` fetches Alice's commit and merges the op sets. It finds that both edits claim the same
starting version of `foo`, which is a fork. It commits a state where `foo()` sits at the pre-fork
content, so it never silently picks one side over the other. It records the fork as durable
shared state in `.sgt/forks.json` and tells Bob what to do next:

```bash
sgt forks
# main.py::foo — tips <alice's op> / <bob's op> — run `sgt merge-op` to reconcile
```

Bob resolves it:

```bash
sgt merge-op <alice_op> <bob_op> --intent "reconcile foo fix"
# drafts a placeholder op chained onto Alice's tip, needing real content
# edit main.py by hand or with an agent to the real reconciled foo()
sgt fulfill <draft-id> --from-tree
sgt commit
```

`commit` runs your build and test oracle against the reconciled version and refuses to commit it
unless the oracle passes, or you record a human override with a reason. A fork cannot be closed
by a version nobody verified. Once it lands, `main.py::foo` is one continuous chain again and the
fork record closes.

When nothing forks, which is the common case because most of a sync touches separate files, `sgt
sync` merges and reports `merged=True` and you see none of the above.

For a shared branch that several people, or CI, or several agents might advance at once, `sgt
land <branch>` uses a git-ref compare-and-swap. One lander wins, and the others re-merge against
the new tip and retry on their own.

## 4. Parallel agent sessions

`sgt session` exists for longer isolated agent work.

```bash
sgt session start caching-refactor --base main   # a real git worktree, own branch, own directory
# point an agent or yourself at that worktree, and edit and save there normally
sgt session status --watch      # warns early if two sessions' footprints start to overlap
sgt session land caching-refactor    # lands its ops onto main and stamps session attribution
```

The attribution that `land` stamps is what makes `sgt revert --session caching-refactor` work
later. It is permanent structured provenance rather than the session record itself, so it still
resolves long after the session has landed and its scratch worktree is gone. `sgt session gc`
reaps sessions whose owning process has died and leaves live ones alone.

## 5. Proposing and publishing work for review

For a review flow close to a pull request, but with per-feature granularity:

```bash
sgt propose create --base main --title "Add rate limiting + caching"
sgt propose status <id>                       # current, needs re-merge, or forked, against base
sgt propose land <id> --subset rate-limiting  # advance main by just that feature's ops
sgt propose render <id> --github              # a suggested branch and PR body in plain markdown
sgt propose publish <id> --remote origin      # push, and create or update the GitHub PR through gh
```

`--subset` is the point. A reviewer can accept the features that are ready without waiting on the
rest. `land` refuses if the accepted subset is missing something it structurally depends on, and
it names what is missing rather than applying a partial state.

One limit to know. If two features sit directly next to each other in the same file, meaning two
functions back to back that share the whitespace between them, accepting only one of them with
`--subset` can produce a file that is missing the separator between them. This is a known v1
limitation, recorded in the U32 entry of `FINDINGS.md`. Features in separate files have no such
coupling.

## 6. Using this with Claude Code or any MCP client

`sgt mcp` runs a stdio MCP server so an agent can call `sgt` directly instead of shelling out. It
exposes 11 tools today, not the full CLI: `sgt_init`, `sgt_log`, `sgt_state`, `sgt_diff`,
`sgt_fsck`, `sgt_revert`, `sgt_restore`, `sgt_oracle_run`, `sgt_plan_intake`, `sgt_checkpoint`,
and `sgt_drift`.

So an agent driving `sgt` over MCP can inspect state and do symbol-level revert and restore,
which covers section 1 and part of section 2. The verbs `sync`, `land`, `merge-op`, `session`,
and `propose` have no MCP tool yet. When a sync produces a fork, or you want a named agent
session, a person has to run those verbs in the terminal. This gap is tracked as its own
follow-up plan and is not part of the current kernel-correctness work.

## 7. What is fixed, and what still has limits

A 2026-07-12 review found four ways ordinary git history could break the kernel. All four are
fixed now.

- A file or symbol that was deleted and re-added no longer disappears from the working tree, and
  the case that used to cause a silent delete now keeps the file and reports it in `sgt status`
  and `sgt fsck`. Re-adding a symbol now chains onto its earlier history, which recovered most of
  the ops that the old model dropped. One edge remains. If a symbol is deleted and re-added with
  byte-identical content, the two versions still register as a fork on the content.
- Symlinks are now treated as unmanaged. `sgt` never writes or deletes through a symlink, at the
  file or any parent directory.
- `sgt land` is now transactional. A red build, an interrupted run, or a lost race leaves the
  working tree and `.sgt/` state exactly as it found them, rather than writing state before the
  build and test gate runs.
- `sgt sync` now does a three-way merge against the common ancestor, so a teammate's deliberate
  revert travels instead of coming back.

These limits remain by design or are not yet built:

- `sgt` decomposes Python and TypeScript or TSX into real symbols through tree-sitter. Any other
  language materializes as exact, faithful whole-file content. It is never mis-parsed, it is just
  not revertable symbol by symbol.
- Some TypeScript constructs, such as enums, `type` aliases, and namespaces, are kept as exact
  file content rather than extracted as individual symbols, so they are not independently
  revertable.
- `revert --keep-dependents` reaches one hop. Only direct dependents of the reverted symbol get a
  continuation placeholder. Anything further downstream drops like a plain revert.
- Imports are ordinary text, not a symbol with its own verb. No command warns that a revert left
  an unused import or offers to prune one.
- `sgt status` is slow on a large op store. On this project's own store of about 7,840 ops it
  takes on the order of a minute, because reducing the full op set to a valid ideal is expensive
  and `status` does it more than once. This is a known performance limit, not a correctness one.

Run `sgt fsck` when you are unsure about the state of a repo. It checks that the ideal is valid
and that the folded tree matches git.
