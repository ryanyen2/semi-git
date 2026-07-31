# Workflow audit and hardening plan (2026-07-31)

Status: proposed. Every finding below was **reproduced empirically** against a scratch repo
(baseline: 3 files / 6 symbols / 4 commits, `sgt init` + 3 saves; each scenario a fresh copy),
then root-caused in the code. Nothing here is inferred from docs — several findings directly
contradict what `README.md` and `docs/guide/workflows.md` claim is supported.

Build audited: `feat/temporal-prior-clustering` @ c9f96e5.

---

## 1. The workflow taxonomy

How people actually build a Python codebase with sgt underneath. ✅ = works as documented,
⚠️ = works with traps, ❌ = broken or destructive.

### W1. Solo daily loop (the advertised spine)
| Step | Status |
|---|---|
| `sgt init` on existing repo | ✅ |
| edit → `sgt save -m` | ✅ |
| `sgt save --as "<label>"` | ❌ dead on a fresh repo (F13); mislabels the mega-feature after refresh (F14) |
| `sgt log` / `--map` / `--tree` / `--summary` | ⚠️ "✓ in sync" lies after undo/merge drift (F3, F7) |
| `sgt revert <symbol>` | ✅ (clean tree) / ❌ crash on any dirty file (F4, F5) |
| `sgt restore <symbol>` | ❌ README's flagship inverse is unresolvable (F2) |
| `sgt restore <op-id>` | ✅ incl. revert → unrelated save → restore |
| `sgt undo` | ❌ absolute-snapshot semantics destroys intervening work (F3); executes journal entries from *failed* verbs and bypasses the dirty guard (F6) |
| `sgt switch <branch>` | ❌ intermittently aborts on sgt's own dirty `tree.json` (F1) |
| `sgt revert <feature>@<n>` | ⚠️ checkpoint's op-set exceeds its label (F16) |

### W2. Curation: labels and regrouping
`feature rename` ✅ · rename-then-revert-by-old-label fails safe ✅ · `regroup split` ❌ "not a
leaf feature" on the only feature that exists (F15) · `regroup move --to <new>` ❌ can't create a
target feature (F15) · young-repo clustering = one mega-feature, so `revert <label> --yes`
removes the entire codebase (F14).

### W3. Raw git interleaved with sgt (users *will* do this)
| Operation | Status |
|---|---|
| raw `git commit` (no save) | ⚠️ absorbed on next refresh, but invisible in `sgt log` rows and dropped by a later `undo` (F3) |
| `git commit --amend` | ✅ (confusing double message, end state correct) |
| `git reset --hard` | ✅ genuinely handled (no resurrection) |
| `git merge` (conflicted *or clean auto-merge*) | ❌ landmine: forked chains silently excluded from the ideal; next materializing verb rolls unrelated files back, destroying committed merge work (F7, F8) |
| evil merge (resolution ≠ either parent) | ❌ same landmine; the resolution itself is what gets destroyed (F7) |
| `git cherry-pick` | ❌ same landmine with zero prior warning — summary says "✓ in sync" (F9) |
| `git rebase` | ❌ conflicts on committed `.sgt/*` metadata; after `--abort`, "invalid ideal" that the advertised recovery does not fix (F10, F11) |
| raw `git checkout <branch>` | ⚠️ works, but F1 makes it silently fail mid-script |
| `git stash` around verbs | ⚠️ stash → verb → pop works only because verbs refuse all dirty trees (F5) |

### W4. Agentic loop
`plan intake` ✅ offline (1 undecomposed step) · save auto-confirm untested at depth ·
`sgt session start/status/land/gc`: ❌ every CLI-started session is instantly "DEAD (gc will
reap)" — `session gc` would reap live work (F19) · phantom "new ops"/overlap warnings from
resurrection-by-fresh-ref bookkeeping (F20) · `session land` reads oracle config from the
session worktree so the refusal's advice is self-defeating (F21) · landing left main's tree
dirty and the landed edit not materialized (F22) · fork refusal points at a `forks` surface
that shows nothing (F23).

### W5. Collaboration (two clones, shared remote)
The **designed** path — concurrent same-symbol edits → `sync` fork detection → park at ancestor
→ `resolve` → `--apply --override` → land → propagate — **works end-to-end** ✅, with traps:
gate-refused `--apply` consumes the draft (F17) · `push` succeeds mid-open-fork ⚠️ · fork
visibility diverges between replicas (F18) · a teammate's revert closure-removes your fresh
dependent work with zero notice (F12) · `sync`/`land`/`propose land` self-block on sgt's own
state-file dirt with advice naming a nonexistent verb `sgt put` (F1-family) · `propose land
--subset` is hollow while everything is one feature (F14-family).

### W6. Recovery
`advanced fsck` ✅ for op-store invariants, ❌ blind to ideal-vs-disk drift it later acts on ·
"re-mine (`sgt log --refresh`)" advice after rebase-abort **does not work**; the only working
recovery is undocumented `rm -rf .sgt/local` + refresh, which also discards local ideal edits
(F11) · `advanced reindex`/`migrate` no-ops for these cases.

---

## 2. Findings, ranked

Severity: 🔴 destroys user work · 🟠 corrupts/wedges sgt state · 🟡 blocks a documented workflow
· ⚪ UX/paper-cut.

| # | Sev | Finding | Root cause |
|---|---|---|---|
| F7 | 🔴 | After any local git merge of two sgt-saved branches (evil OR clean auto-merge), an unrelated `revert`/`restore` silently rewrites other files back to pre-merge content. Preview says "1 file"; commit shows 2+. | Mining diffs a merge commit against **first parent only** (`gitbind.py:534-553`, `mine.py:423,728-734` — the `gitbind.py:541` docstring claiming otherwise is backwards), re-attributing the second parent's whole delta as one cumulative op whose `before_version` = the merge-base version → forks with the branch's own chain whenever that side edited a symbol ≥2 times, *even conflict-free*. `fork_free` then drops **both** tips and re-grounds, cascading away the rest of the chain (`order.py:221-244`); `put()` regenerates every covered path from the stale ideal and deletes tracked paths not in it (`lens.py:895-934`), and `_dirty_conflicts` can't fire on committed bytes (`lens.py:772`). |
| F9 | 🔴 | Cherry-pick → same landmine, and `log --summary` says "✓ in sync" beforehand. | Same, plus drift check missed the cherry-picked commit. |
| F3 | 🔴 | `revert` → raw `git commit` → `undo`: the manual commit's content vanishes from the tree; never re-mined (witness advanced); `--summary` reports "✓ in sync"; not visible in `sgt log`. | Journal stores an **absolute pre-edit op-set snapshot** (`lens.py:708-719`), and `_apply_ideal_edit_inverse` re-materializes it wholesale (`lens.py:738-750`); the dirty guard's precondition `on_disk != committed` (`lens.py:772`) can't fire on committed content. No redo (`oplog.py:185` drops the event, journals nothing). |
| F6 | 🔴 | Failed (crashed) revert + reflexive `undo` wipes the uncommitted WIP the dirty-guard had just protected. | `record_ideal` journals before the edit lands; `oplog.undo` runs `get()` first (`oplog.py:165`), which folds the dirty edit into the current ideal so `_dirty_conflicts` structurally cannot fire, then `put(prev)` overwrites it. |
| F12 | 🔴 | `sync` closure-removes a teammate's *fresh dependent work* reporting only "✓ merged". Algebra correct (anti-resurrection + closure), zero communication, no restore hint. | Sync result reporting omits closure casualties. |
| F10 | 🟠 | Rebase/merge between sgt branches conflicts on `.sgt/ideal.json`, `.sgt/ops/*`, `.sgt/intent/*`; git text-merges sgt's JSON when it can. | Op store + ideal + tree + pins committed **into the user's working tree/merge surface**. |
| F11 | 🟠 | After `git rebase --abort`: "invalid ideal for <ref>: names an op the store can't produce — re-mine (`sgt log --refresh`)". Refresh does NOT repair; `reindex` doesn't; only undocumented `rm -rf .sgt/local` does. | After first contact `_sync` treats the persisted set as base and only **unions** new provenance (`lens.py:527-537`) — it never re-derives from ancestry, and the sync-cache gate can short-circuit entirely (`lens.py:400-421`). Meanwhile the rewind physically deleted committed op files (see F-STORE below), and `reduce_to_ideal` silently drops ids with no op present (`order.py:275-276`). The natural reconciliation hook — `is_ancestor(prev_head, head)` at the witness move (`gitbind.py:408-416`) — exists but is unused for this. |
| F-STORE | 🟠 | The committed op store is **non-monotone under git history rewrites**: `git add -A` in `put()` commits `.sgt/ops/*` into each witness commit (`lens.py:667`, `gitbind.py:728-745`; only `.sgt/local/` is gitignored, `store.py:178-182`), so `reset --hard`/rebase physically delete op files the local ideal still references. Repos that gitignore all of `.sgt/` (like this dev repo) don't hit it — behavior forks on a gitignore decision the docs never mention. | Store layout. |
| F1 | 🟠 | `sgt save` leaves tracked `.sgt/tree/tree.json` (and pins) modified post-commit → next `sgt switch`/`git checkout`/`sync`/`land`/`propose land` aborts. Scripts silently stay on the wrong branch (this bit the audit harness itself). | `put()` commits at `porcelain.py:153`, then the ownership cascade runs *after* at `porcelain.py:165`, writing committed artifacts `pins` (`ledger.py:296`), `authored` (`:297`), `tree.json` (`:302`) — the witness commit captures the pre-cascade blobs. |
| F2 | 🟡 | `sgt restore <file::symbol>` of a reverted symbol: "could not resolve … set OPENAI_API_KEY". README flagship. | `plan_restore`'s ghost-widening deliberately excludes `::` targets (`core/verbs.py:158`); symbol resolution = live frontier only (`core/verbs.py:72-76`). |
| F22 | 🟠 | `session land` advanced the record "+27 op(s)" (1-edit session), left main's `util.py` modified-uncommitted, landed edit not visible in tree. Contradicts workflows.md "fully succeeds or fully fails". | Land runs in the *scratch* worktree; `checked_out=False` branch (`land.py:277-278`) CASes `refs/heads/main` forward and restores only the scratch tree — the main worktree's ref moves underneath it, never re-materialized. "+27" = union delta vs main-tip ideal (`land.py:288`), not the session's edit count (`session.py:151-155`). |
| F23 | 🟡 | `session land` refuses "open fork(s) — run `sgt merge-op`" while `advanced forks` = "no open forks". Dead end. | Land computes forks on the fly and refuses **without persisting** (`land.py:246-247`); `forks`/`resolve` read committed `.sgt/forks.json`, written only by sync's materialize (`materialize.py:151`). |
| F19 | 🟡 | All CLI sessions instantly "DEAD (gc will reap)"; `session gc` would reap live worktrees; `gc --force` discards uncommitted scratch edits with no confirm (`session.py:220-226`). | Liveness = `os.getppid()` of the transient `session start` process (`session.py:138`, `is_alive` `:89-96`). |
| F20 | 🟡 | Fresh session shows phantom "3 new op(s)" = previously **reverted** ops; phantom overlap warnings between untouched sessions. | New ref's op-baseline re-seeded from git ancestry, resurrecting explicit exclusions (bookkeeping only; the fold is correct). |
| F21 | 🟡 | `session land` refusal "add `.sgt/oracle.json` and re-run" is self-defeating: config is read from the session worktree forked before the file existed. | Config loaded per-worktree. |
| F13 | 🟡 | `save --as` fails on every save until the first `log --refresh` ("no feature attribution yet"); labels silently dropped. README/workflows.md sell it as save-time naming. | Attribution lazily built at refresh; `assign_at_save` has no bootstrap for the empty tree. |
| F14 | 🟡 | Young repo = one mega-feature; `--as` renames the mega-feature (all history mislabeled); `revert <label> --yes` would remove the entire codebase; `propose --subset` hollow. | Clustering-only feature genesis; label granularity ≠ op-set granularity. |
| F15 | 🟡 | `regroup split` → "not a leaf feature" on the only (flat) feature, by label and short id; `regroup move --to <new>` cannot create a feature. No escape from the mega-feature. workflows.md explicitly recommends these verbs for exactly this case. | `_leaf` is exact-full-key only (`lens/verbs.py:72-75`); split (`:413-423`) and move (`:259-272`) lack the `resolve_feature` fallback that merge/rename have — the short handles and labels the UI prints never resolve; move has no create path. |
| F16 | 🟡 | 4 saves fold to 3 checkpoints; `revert <feature>@2` ("add caching layer") also removed save c3's request-logging work. | Segment folding keeps first save's label; op-set spans both. Additionally `@n` indices are explicitly reassigned on every rebuild (`intent/segment.py:346`) — only `:slug` handles are stable, but the UI leads with `@n`. |
| F17 | 🟡 | Oracle-gate-refused `resolve --apply` consumes the draft ("no drafted resolution yet" on retry). Also: no-oracle repos refuse with no override syntax shown (inconsistent with other verbs' "unconfigured = warn"). | Draft lifecycle + gate policy. |
| F18 | 🟡 | Fork state diverges between replicas: one clone "no open forks", the other 1 open, same branch. | `.sgt/forks.json` is rebuilt and **overwritten** per sync (`save_json`, not a union/CRDT); no shared fork record. |
| F4/F5 | 🟡 | Any dirty file (even unrelated, even `__pycache__/*.pyc` from the oracle's own compile run) blocks every materializing verb — as a raw Python traceback, with partial `.sgt` writes left behind. | `put()` folds whole tree; guard message raised as unhandled exception; no ignore-rules awareness. |
| F24 | ⚪ | Refusal advice names nonexistent verbs/flows: "`sgt put` or commit first", "re-run" flows that can't work; fsck green while acting on a stale ideal. | Messages never tested end-to-end. |
| F25 | 🔴 | **Second resurrection path (code-trace + isolated repro, not yet e2e):** teammate `land`s a revert and `push`es (ships `refs/sgt/log/<branch>`); your `sync` recovers their tip via the D1 land-log (`"log"` recovery) and **skips the three-way base-subtraction → the reverted op comes back silently**, and your next push propagates the un-revert. Defeats the headline 2026-07-12 "sync no longer resurrects reverts" fix on the land-log path. | `sync/resolve.py:82-85` subtracts only when `theirs_recovery in ("trailers","ideal-record")`; `"log"` is checked first and is a full ideal too. One-line fix (add `"log"`), zero test coverage (`test_sync_stages.py:339-350` hard-defaults `trailers`). Repro: scratchpad `repro_log_gap.py`. |
| F26 | 🟠 | `sgt save` during an in-progress `git merge` (MERGE_HEAD present) commits conflict-marker bytes as a normal save and finalizes the merge blind. | `porcelain.py:120-206` has no MERGE_HEAD check; `commit_all` is plain `git commit`. |
| F27 | 🟠 | Two `sgt land` in one checkout can interleave: they share the git index between `stage_candidate` (`land.py:252`) and `write_tree` (`land.py:264`), so the CAS winner can commit a tree that is not the one its oracle gated. | `.sgt/local/lock` is per-mutation, not per-verb; no worktree-level lock; ref CAS is the only arbiter. |
| F28 | 🟡 | `propose land` unions the accepted Δ onto the proposal's **create-time** base ideal, not the live base (fork-state is checked, disjoint advance is not re-validated). | `propose.py:127-130, 219-224`. Subset **closure validation does exist** (`propose.py:219-221`) — good. |
| F29 | 🟠 | `sgt undo` can roll back **shared** advances: a checked-out `sgt land` journals an ordinary undoable `ideal_edit` (`land.py:275`, `journal=checked_out`) — despite the oplog docstring (`oplog.py:24-26`) claiming lands are always refused — and every `sync` is journaled too (`materialize.py:183`). Combined with F3's snapshot semantics, an undo after land/push locally rewinds a ref the team already sees. | Journal admission policy inconsistent with the "shared-out" rule. |
| F30 | ⚪ | Reverting an op a plan step already auto-confirmed leaves a dangling `plan_matches.json` record and a `completed` step that never re-opens; the re-done work won't match the plan again. | `match.py:277-309`; `compute_checkpoint` excludes matched ops (`match.py:224`). |
| F31 | ⚪ | Detached-HEAD sessions key all per-ref tables on the raw HEAD sha, so every new commit orphans the previous key's witness/ideal rows. | `lens.py:218-221, 459-463`. |

Docs-vs-reality: `CLAUDE.md` references `CONCEPTS.md` and `docs/solutions/` — neither exists.
workflows.md §7's limit list contains **none** of the above classes.

---

## 3. Why these keep happening (architecture, not incident list)

Five structural decisions generate nearly every finding:

1. **Three sources of truth for "what is the current state".** Git ancestry (provenance scan),
   the committed `.sgt/ideal.json`, and the local-authoritative `.sgt/local/ideal.json` — with
   no reconciliation protocol and no validation stamp. Any raw-git operation moves ancestry and
   the committed half underneath the local half. → F7, F9, F10, F11, F20.
2. **sgt state lives inside the user's merge surface.** `.sgt/ops`, `ideal.json`, `tree/`,
   `pins/`, `intent/` are tracked files in the working tree, written partly *after* the commits
   they belong to. Every merge/rebase becomes a metadata conflict; every save dirties the tree.
   → F1, F10, and every "requires a clean working tree" self-block.
3. **Materialization writes the whole world.** `put()` = fold(entire ideal) → disk, so a
   one-symbol verb can rewrite any file whose recorded state is stale, and its guard protects
   only *uncommitted* bytes. → F7/F9's destruction step, F5, F4.
4. **The fork machinery only exists on the sync path.** Mining a local merge silently drops
   forked chains; land detects forks privately; `forks` shows only sync's. → F7, F23, F18.
5. **Attribution is a lazy batch process bolted beside save**, and undo is a snapshot, not an
   inverse. → F13–F16, F3, F6.

---

## 4. The plan

Ordered so that data-loss stops first, then the state model is fixed once (not patched
per-symptom), then semantics, then polish. Each phase has an acceptance gate.

### Phase 0 — Stop the bleeding (days)
*No architecture changes; pure guards. Ship immediately.*

- **0.1 Delta-scoped materialization guard.** Before `put()`, compute the file set touched by
  `before_ideal Δ after_ideal`. If fold(current ideal) ≠ disk for any file *outside* that set,
  refuse with a drift report naming those files — never rewrite them. Kills the F7/F9 landmine's
  destruction step even before ingest is fixed.
- **0.2 Undo hardening.** (a) Mark journal entries applied only after a successful `apply`;
  `undo` skips unapplied entries. (b) Route undo's materialization through the same dirty guard
  as every other verb. (c) Print what undo will drop/re-add and require confirm when the delta
  touches ops mined *after* the journal entry (the F3 case).
- **0.3 Refusals, not tracebacks.** Catch `DirtyWorkingTreeError` at the CLI boundary
  (`cli/ideal_edit.py`, `cli/session.py`, `cli/sync.py`); print the file list + the *actual*
  remedy. Fix advice strings: no `sgt put`, no "refresh" where refresh doesn't heal.
- **0.4 `session gc` safety.** Refuse to reap a session whose worktree has uncommitted changes
  or unlanded commits without `--force`.
- **0.5 Restore-by-symbol.** Extend `plan_restore`'s ghost-widening to `::` targets: resolve a
  non-live symbol over the whole store by footprint (newest ghost tip), reusing
  `_live_and_ghosts` (`cli/ideal_edit.py:341`). `_validated` already refuses illegal re-entries.
- **0.6 Truthful sync report.** When merge/closure removes ops from the local tree (F12), list
  the removed symbols + `sgt restore <op>` hints, and repeat open-fork warnings on *every* sync
  while a fork is open. (Closure removal is `order.upset_in_many` doing its job — only the
  reporting is missing; today only `recovery=="none"` paths get banners, `cli/sync.py:217-222`.)
- **0.7 The land-log resurrection one-liner (F25).** Add `"log"` to the recovery tuple at
  `sync/resolve.py:83`, plus a test that drives `theirs_recovery="log"` through `resolve()`
  (today's tests hard-default `"trailers"`). Highest value-to-diff ratio in this whole plan.
- **0.8 Persist land-time forks (F23).** When `land` refuses on `res.forks`, write the same
  fork records sync's materialize writes (`materialize.py:100-108,151`) so `forks`/`resolve`
  can see what land is talking about.
- **0.9 `save` refuses mid-merge (F26).** If `MERGE_HEAD` exists, refuse with "finish or abort
  the git merge first" instead of committing conflict markers.

Gate: the phase-A/B/C sandbox scripts (see §5) run with **zero silent file rewrites outside the
previewed set and zero tracebacks**.

### Phase 1 — One source of truth for the ideal (the core fix)
*This is the architectural decision. Everything in F7/F9/F10/F11/F20 follows from it.*

- **1.1 Demote both persisted ideals to caches.** Authoritative state becomes a pure function:
  `ideal(ref) = reduce(ops-with-provenance-in-ancestry(ref) minus exclusions(ref))`, where
  **exclusions** (explicit reverts/pins — the one thing ancestry can't express) are an
  append-only, content-addressed log. Persisted `ideal.json` gains a validation stamp
  (`HEAD` sha + exclusion-log head); any mismatch → rebuild, never trust. Rebase, amend,
  cherry-pick, reset, new worktrees, new clones all become *automatically* consistent, because
  membership is recomputed from the moved ancestry. (Amend/reset already work precisely because
  they stay on the recompute path — this generalizes the part of the design that empirically
  held up.)
- **1.2 Move sgt state out of the merge surface.** Store ops + exclusion log + feature tree
  under a dedicated ref (`refs/sgt/state`, git-notes-style, fetched/pushed alongside branches),
  not as working-tree files. Consequences: saves stop dirtying the tree (F1 dies), merges/rebases
  stop conflicting on metadata (F10 dies), `git checkout` interop is clean, and clones/sessions
  share state without copying files. Migration: `sgt migrate` moves `.sgt/{ops,ideal,tree,pins,
  intent}` into the ref; `.sgt/` keeps only `oracle.json` + `identity_constraints.json` (genuinely
  team-editable config) and gitignored `local/`.
- **1.3 Merge-aware mining.** A merge commit must not be mined as one cumulative first-parent
  diff (that is what mints the forking `v_base→v_tip` op). Options, in preference order:
  (a) skip the merge commit's non-conflict content entirely — both sides' ops are already mined
  from the branches themselves, and content-addressing unions them; mine only the paths where
  the merge blob differs from *both* parents (the conflict resolutions / evil hunks), minting
  each as a reconciliation op chained onto the first-parent tip with the second tip recorded as
  superseded; or (b) full both-parent mining. (a) is cheaper and matches the existing
  `merge-op` semantics (`rewrite.py` chain-extension + advisory provenance).
- **1.4 Fork-aware rebuild.** When the rebuild still encounters a forked chain, do exactly what
  sync does: park the symbol at the common ancestor, **record the fork in the one shared fork
  store**, and say so — never silently exclude (`fork_free`'s both-tips drop at
  `order.py:221-244` becomes park-and-report).

Gate: B3/B4/B5/B6 sandbox scenarios end with: correct bytes on disk, fork surfaced where real,
`fsck` green, and no recovery incantations needed.

### Phase 2 — Undo as algebra, transactions as protocol
- Journal schema: `(verb, target, removed_ids, added_ids, applied_at)`. `undo` = apply the
  *inverse delta relative to the current ideal* (`restore removed` ∪ `revert added`), validated
  through `Ideal.from_ops` like any verb — refuses instead of clobbering; `redo` = re-apply.
  Ops mined after the entry are untouched by construction (F3 impossible).
- Journal admission policy made consistent (F29): anything that advanced or ingested a shared
  ref (`land` checked-out or not, `sync`) is not silently undoable — undo offers the *local
  inverse* with an explicit "this diverges from the shared record" confirmation, or refuses
  with the forward-fix command (`revert` + `land`).
- Every mutating verb runs as: plan → validate → journal(intent) → apply → journal(done);
  crash between the two journal marks = `fsck` flags + auto-rollforward/rollback.

### Phase 3 — Attribution lifecycle owned by save
- `assign_at_save` bootstraps: empty tree + `--as` ⇒ create the feature, assign exactly the
  save's new symbols to it (never rename the shared cluster).
- Fix `regroup split`'s leaf-check (a flat root with checkpoints must be splittable);
  `regroup move --to <new-label>` creates the target.
- Checkpoint labels: a segment spanning multiple saves is labeled as a range ("add caching
  layer … fetch logs requests, 2 saves"), and `revert @n` previews list per-save messages.
- The clustering prior stays, but as a *suggestion* overlay; durable identity comes from
  save-time assignment (this completes the 2026-07-23 ownership-ledger direction).

### Phase 4 — Collaboration semantics
- **Fork store is shared state** (rides `refs/sgt/state`): open forks, who parked what, the
  reconciliation draft id. `advanced forks`, land's check, and session status all read it
  (F18/F23 die). `push` warns on open forks touching pushed refs.
- **Sessions**: liveness = lease file refreshed by any sgt command in that worktree (TTL, not
  pid); `session land` materializes atomically (tree clean after, edit present) or aborts
  whole; oracle config resolved from the *target* branch at land time.
- **Oracle policy, one rule**: `unconfigured` ⇒ warn-and-proceed locally, refuse only on
  *shared* advances (`land`, `propose land`, `resolve --apply`) with the exact `--override
  pass --reason "..."` syntax printed in the refusal. Gate refusals never consume drafts.

### Phase 5 — Truthful surfaces
- `fsck` gains the fidelity check its epilog already claims: fold(ideal(HEAD)) vs disk, and
  validation-stamp checks for every cached projection.
- `log --summary` never prints "✓ in sync" while any drift/fork/unapplied-journal exists.
- Every refusal's advice string is covered by a test that *executes the advice* and asserts it
  resolves the refusal (kills the F24 class permanently).
- Doc refresh (both directions): workflows.md:284 keep-dependents "one hop" is stale (U7
  shipped transitive); FINDINGS v1-limitations line ~1792 still cites the pre-U9 ~20% loss and
  calls rebirth chaining deferred; oplog.py:24-26 docstring contradicts `land.py:275`'s
  undoable checked-out land; document that a `regroup merge`-absorbed label stops resolving
  (revert via survivor); recommend `:slug` over unstable `@n` in every checkpoint hint
  (`segment.py:346`); CLAUDE.md references `CONCEPTS.md` and `docs/solutions/` which don't
  exist. And the `.sgt`-gitignore fork (F-STORE): pick ONE supported layout and document it.

### §5. The regression net (how we keep this fixed)
Turn the audit sandbox into `tests/laws/test_workflows.py`:
- **The no-silent-loss law** (umbrella property): after any sequence of {save, revert, restore,
  undo, switch, sync, land, resolve} × {raw commit, amend, rebase(+abort), merge (clean/
  conflicted/evil), cherry-pick, reset --hard, stash}, every byte the user committed is either
  present in the working tree or named in that command's output. Run as a randomized
  sequence-property test over a small op corpus, plus the ~20 deterministic scenarios from this
  audit as named regression cases.
- **The advice law**: every distinct refusal message's suggested command, executed verbatim,
  clears the refusal.
- CI job runs the suite against a repo *without* `.sgt/oracle.json` and *with* `__pycache__`
  present — the "user didn't follow the docs" environment is the default test environment.

---

## 5. What was verified as genuinely working (keep, don't regress)
- Kernel round-trip: revert → unrelated save → restore-by-op-id is byte-perfect; fsck-clean.
- `git commit --amend` and `git reset --hard` reconciled correctly in the sandbox — with the
  caveat that reset's success partly rides on `reduce_to_ideal`'s silent present-filter
  (`order.py:275-276`) absorbing the deleted op files; Phase 1 must preserve the *outcome*
  while making the mechanism explicit.
- The full designed collab loop: sync fork detection → park → resolve draft → override land →
  propagate. Anti-resurrection three-way merge holds **on the trailers/ideal-record recovery
  paths** (the `"log"` path is F25); dependency closure never leaves a dangling caller (result
  parses and runs).
- `propose --subset` closure validation exists and rejects non-downward-closed subsets
  (`propose.py:219-221`).
- The land CAS + oracle gate is genuinely atomic per the 2026-07-12 fix (`land.py:236-297`):
  gated tree == committed tree, losers re-ingest and retry (single-checkout index sharing,
  F27, is the remaining hole).
- Dirty-tree *detection* itself (the guard fires reliably; it's the scope + surfacing that's wrong).
- Session worktree materialization honors exclusions (no on-disk resurrection — the phantom
  "3 new ops" is bookkeeping only).
- Docs also *under*-claim in one place: workflows.md still says `revert --keep-dependents` is
  one-hop; FINDINGS U7 shipped transitive continuation. Doc refresh should fix both directions.
