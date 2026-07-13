# User workflows

A tour of what you can actually do with `sgt` today, one use case at a time. Each section names
the commands, walks a concrete example, and says plainly where the feature is solid versus where
it's still being hardened — see [`FINDINGS.md`](../../FINDINGS.md) for the exhaustive version of
the latter.

If you haven't yet, read [the semantic tree](the-semantic-tree.md) first for the mental model.
The one-sentence version: history is a DAG of symbol-level edits ("ops"); the current codebase
state is a subset of that DAG ("the ideal"); folding the ideal back together reproduces exactly
what's on disk. `sgt` never writes your code — you or your agent do that; `sgt` tracks which
edits are "in" and lets you add or remove them precisely.

Every example below either ran for real against a scratch repo while writing this doc, or is
drawn directly from the project's own test suite (cited by file). Nothing here is invented
terminal output.

---

## 1. The solo daily loop

This is the part that should feel unsurprising — it's `git`'s daily loop with sgt riding along:

```bash
sgt init                    # once, per repo: mine existing history into the op store
# ...edit files with your editor or agent, same as always...
sgt save -m "add input validation"   # mines your edit, commits a witness — the "commit" step
sgt status                  # files/symbols/features tracked, coverage, any open forks or drift
sgt undo                    # made a mistake? invert your last save/revert/restore (forward-only)
```

`sgt switch <branch>` is the sgt-aware equivalent of `git switch` — it also rematerializes that
branch's ideal, so file content and sgt's tracked state move together. This layer buys you
nothing exciting on its own; it's what makes the next sections possible without you doing
anything differently day to day.

---

## 2. The flagship case: revert one thing out of a big, tangled edit

This is the reason to reach for `sgt` instead of just `git`.

**The setup.** An agent (or you, moving fast) lands three unrelated changes in one sweep — rate
limiting, a caching layer, and a retry policy — spread across a handful of files, in one commit.
The caching layer turns out to be wrong. In plain git, undoing *just* that means finding every
line the caching work touched and reverting it by hand (or bisecting/cherry-picking around a
commit that also contains the other two features you want to keep), hoping the surgery doesn't
nick something else.

**What `sgt` gives you instead.** Because every edit was already mined into per-symbol ops, you
can name the exact symbol (or feature, or agent session — more below) and remove precisely that,
plus anything that was built on top of it, and nothing else. This is real output from a scratch
repo built for this doc — three files, one function each, committed together exactly like an
agent's single-pass edit:

```
$ sgt revert cache.py::get_cached
✓ [revert] cache.py::get_cached
    removed 1 op(s): a776620b9b56
    affected: cache.py::get_cached
```

After that command: `get_cached` is gone from `cache.py`. `set_cached` — the *other* function in
the same file — is untouched. `rate_limit.py` and `retry.py` are byte-identical to before the
command ran. `sgt restore cache.py::get_cached` puts it straight back:

```
$ sgt restore cache.py::get_cached
✓ [restore] cache.py::get_cached
    added 1 op(s): a776620b9b56
    affected: cache.py::get_cached
```

`<ref>` here was a `file::symbol` name, but it can just as well be an op-id, or — once there's
enough signal to group by — a **feature** or an **agent session**:

```bash
sgt revert <feature-id-or-label>     # every op sgt's map grouped under that feature
sgt revert --session <session-name>  # every op attributed to one agent session's landing
```

**The candid part.** Symbol-level revert (what you just saw) always works — it needs nothing but
the op DAG. Feature-level and session-level revert need the *grouping* to actually exist first,
and that grouping comes from two different places with different reliability:

- **`sgt map`'s automatic clustering** groups symbols into features using co-change and
  structural signal across the repo's *history*. It needs enough commits and enough churn to
  find real seams — on a brand-new repo (like the three-file scratch example above), it
  correctly refuses to invent a split and reports one feature for everything, because there's no
  signal yet to split on. `sgt merge`/`split`/`rename`/`move` let you correct or seed the
  grouping by hand when the automatic pass doesn't have enough to go on.
- **Session attribution** (`sgt revert --session <name>`) doesn't depend on clustering quality
  at all — it's exact, structured provenance stamped on every op a named `sgt session` (§4)
  landed, so "revert everything this agent run did" works from the first run, before there's any
  history to cluster over.

So: reach for symbol-level revert (or `sgt session` + `--session` revert) as the reliable tool
today; feature-level revert is genuinely powerful once `sgt map` (or your own `move`/`merge`
corrections) has had enough real history to work with, and `sgt blame <file>` tells you whether
that grouping currently covers the file you care about before you trust it.

---

## 3. Multi-user collaboration: sync, forks, and resolving them

Two people editing the same function independently is still a conflict — `sgt` doesn't make that
go away. What changes is the *blast radius*: the conflict is isolated to the one symbol both of
you touched, and everything else merges immediately, automatically, with no markers to resolve.

This example is grounded directly in the project's own test suite
(`tests/core/test_sync.py::test_sync_records_a_fork_and_lands_the_forked_symbol_at_the_common_ancestor`).

Alice and Bob both have `main.py` cloned, with `foo()` returning `1`.

**Alice** fixes a bug in `foo()`, making it return `999`:
```bash
sgt save -m "fix: foo off-by-one"
git push
```
That goes through cleanly — she pushed first.

**Bob**, unaware, independently "fixes" the same function to return `42`:
```bash
sgt save -m "fix: foo edge case"
git push        # rejected — main has moved
sgt sync origin main
```
`sync` fetches Alice's commit and unions the op sets. It finds both Alice's and Bob's edits claim
the same starting version of `foo` — a fork. It does **not** stop there: it commits a
reconciling state where `foo()` sits at the pre-fork content (neither `999` nor `42` — never a
silent pick of one side), records the fork as durable shared state in `.sgt/forks.json`, and
tells Bob what to do next:

```bash
sgt forks
# main.py::foo — tips <alice's op> / <bob's op> — run `sgt merge-op` to reconcile
```

Bob resolves it:
```bash
sgt merge-op <alice_op> <bob_op> --intent "reconcile foo fix"
# drafts a hollow op — a placeholder needing real content, chained onto Alice's tip
# ...edit main.py by hand (or with an agent) to the real reconciled foo()...
sgt fulfill <draft-id> --from-tree
sgt land
```
`land` refuses to commit the reconciled version unless your configured build/test oracle passes
against it (or you record an explicit human override with a reason) — the fork can't be closed
by a version nobody verified. Once it lands, `main.py::foo` is one continuous chain again going
forward, and the fork record closes.

If nothing forked — the far more common case, since most of a sync usually touches disjoint
files — `sgt sync` just merges and reports `merged=True`; you never see any of the above.

For a shared branch that multiple people (or CI, or multiple agents) might advance at once,
`sgt land <branch>` uses a git-ref compare-and-swap: one lander wins, the other automatically
re-unions against the new tip and retries. No manual step needed for that race.

---

## 4. Parallel agent sessions

For longer-lived, isolated agent work — the thing `sgt session` exists for:

```bash
sgt session start caching-refactor --base main   # a real git worktree, own branch, own dir
# ...point an agent (or yourself) at that worktree; it edits and saves normally there...
sgt session status --watch      # early-fork warning if two sessions' footprints start to overlap
sgt session land caching-refactor    # CAS-lands its ops onto `main`, stamps session attribution
```

The attribution stamped by `land` is what makes `sgt revert --session caching-refactor` work
later (§2) — it's permanent structured provenance, not the session record itself, so it still
resolves correctly long after the session has landed and its scratch worktree is gone.
`sgt session gc` reaps sessions whose owning process has died, without touching live ones.

---

## 5. Proposing and publishing work for review

For a review flow closer to a pull request, but with per-feature granularity:

```bash
sgt propose create --base main --title "Add rate limiting + caching"
sgt propose status <id>                       # current / needs re-union / forked, vs. base
sgt propose land <id> --subset rate-limiting  # advance `main` by just that feature's ops
sgt propose render <id> --github              # a suggested branch + PR body, plain markdown
sgt propose publish <id> --remote origin      # push it, create or update the GitHub PR via `gh`
```

`--subset` is the point: a reviewer can accept part of a proposal — the features that are ready
— without waiting on the rest, and `land` refuses if the accepted subset is missing something it
structurally depends on (named explicitly, not a silent partial apply).

**One sharp edge, documented candidly:** if two features sit directly adjacent in the same file
(two functions back to back, sharing the whitespace/residue between them), accepting only one of
them via `--subset` can currently produce a file missing the separator between them — a known
v1 limitation (see `FINDINGS.md`'s U32 entry). Features in separate files have no such coupling.

---

## 6. Using this with Claude Code (or any MCP client)

`sgt mcp` runs a stdio MCP server, so an agent can call sgt directly instead of shelling out.
**Today it exposes 11 tools, not the full CLI surface:** `sgt_init`, `sgt_log`, `sgt_state`,
`sgt_diff`, `sgt_fsck`, `sgt_revert`, `sgt_restore`, `sgt_oracle_run`, `sgt_plan_intake`,
`sgt_checkpoint`, `sgt_drift`.

In practice, that means an agent driving sgt purely over MCP can inspect state and do
symbol-level revert/restore — the core of §1 and half of §2 — but **`sync`, `land`, `merge-op`,
`session`, and `propose` have no MCP surface yet.** The moment a sync produces a fork, or you
want a named agent session, a human has to drop into the terminal and run those CLI verbs by
hand. This is a known, tracked gap, not an oversight — closing it is scoped as its own follow-up
plan, not bundled into current kernel-correctness work.

---

## 7. What's genuinely still being hardened

Worth knowing before you trust this on something you can't afford to lose, in order of how much
it can bite you:

- **Delete-then-recreate a symbol (or a whole file) can currently drop it from the materialized
  tree.** Add → delete → re-add the same symbol mines as two disconnected histories today
  instead of one continuous chain, and reducing that to a valid state can silently exclude the
  re-added file. On this project's own ~7,000-op history, that's measured at roughly 20% of ops.
  This is the headline item in the active
  [fix plan](../plans/2026-07-12-001-fix-kernel-invariants-and-sync-plan.md); the immediate
  mitigation already landed is that a would-be deletion is now surfaced as a reported,
  recoverable state rather than silently applied.
- **Symlinks aren't modeled** — mining and materialization don't yet treat them specially. The
  same fix plan covers making them safely "unmanaged" rather than written or deleted through.
- **`sgt land`'s gate ordering** — the same plan covers making a red or interrupted `land` leave
  the tree and `.sgt/` state exactly as it found them, rather than persisting before the
  build/test gate runs.
- **`sgt sync`'s merge base** — today it's closer to a blind union than a verified three-way
  merge; a teammate's deliberate revert can, in some histories, come back. Also covered by the
  same plan.
- **Two languages** — Python and TypeScript/TSX are decomposed into real symbols via
  tree-sitter; anything else materializes as exact, faithful whole-file content, just not
  independently revertable symbol by symbol.
- **`revert --keep-dependents` is one hop** — only direct dependents of the reverted symbol get a
  continuation placeholder; anything further downstream drops like a plain revert would.

None of these are silent anymore where it matters most (materialization) — they're either fixed,
surfaced-and-recoverable, or named here. `sgt fsck` is the command to run when in doubt.
