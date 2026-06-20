---
date: 2026-06-18
topic: merge algorithm — edge-case & user-flow analysis (T0)
status: design / ADR
origin: docs/plans/2026-06-18-003-feat-merge-algorithm-and-policy-plan.md
---

# Merge (T0) — edge-case & user-flow analysis

This is the hardest, most load-bearing part, so the design is written as a list of explicit
questions — *does the approach hold for edge case X / user flow Y?* — each with a decision and
the test that pins it. Anything not provably handled is marked **DEFERRED** with a reason, so
no edge case is silently assumed away.

## Correction from implementation (2026-06-18): two checks, not one

A first design re-gated each node with the confluence gate (`can_land`, pairwise-commute over
the batch). Implementation analysis shows that is **wrong** on two edge cases:

> **Q: can a node's own effects be gated by pairwise commutation?**
> **No.** A node accumulates effects over time — e.g. `add_def f` then a later `replace_def f`
> (a modify). Those target the same unit and do **not** commute. Commutation is a property of
> *concurrent* effects (different causal lineages), not of a single node's *sequential* history.
> Requiring intra-node commutation would quarantine every modified node.

> **Q: will a materialize-based gate surface a same-statement conflict (EC6)?**
> **No.** `StatementSeq.replace` resolves same-slot edits by LWW, so materialization *always
> succeeds* and silently picks a winner. A gate that only asks "does this materialize validly?"
> never sees the conflict.

So T0 uses **two checks per node**, processing nodes in total-order:
1. **Concurrent-conflict (structural, apply-free):** does this node have an effect that is
   *concurrent* (vv-incomparable) with an already-active node's effect and does not commute
   (`static_commute is False`)? Catches the LWW-hidden statement races (EC6/EC7) and
   statement-vs-def-rewrite (EC8-shape) that materialization would hide.
2. **Sequential-applicability (materialize-try):** do the node's effects, appended to the
   active set, materialize without a precondition failure and stay invariant-valid? Catches
   def-level name collisions (EC3), joint-invalidity (EC8), and tombstone-orphaned edits (EC9).

A node is **active** iff it passes both against the accumulated active set; otherwise it is a
durable conflict. Both checks are needed — neither alone covers all the edge cases below.

## The T0 algorithm (the thing being stress-tested)

`merge(local, delta)`:
1. **Union by eid.** Add the delta's log entries to the local log, deduped by `eid`. Add
   incoming node metadata for node ids not present locally. Union tombstones.
2. **Partition.** `common` = entries whose `eid` both sides already had; `incoming-new` =
   the rest. (Equivalently: entries not in the pre-merge local log.)
3. **Causal-greedy gate.** Sort all live effects by the total order `(vv.rank, author,
   counter)`. Greedily admit the maximal confluent prefix (the existing
   `max_coordination_free_batch_explained`, history-seeded). Admitted effects materialize;
   each held effect becomes a durable conflict (reusing `QUARANTINED` + witness), with the
   witness naming the admitted counterpart it lost to.
4. **Re-derive edges** across the union (`_infer_dependencies`) so cross-replica
   dependencies exist for revert-closure.

Two invariants the whole thing must preserve: **(I1) the materialized tree is always
invariant-valid** (never a broken merge) and **(I2) `merge(A,B)` ≡ `merge(B,A)`** (order
independence / SEC).

## Why causal-greedy, and the winner rule (an edge case that fixes the policy)

The plan said "LWW (last writer wins)". Implementation analysis overturns that:

> **Q: can we admit conflicting effects in *descending* order so the most recent wins (LWW)?**
> **No.** A dependent effect (e.g. `add_call(caller, base)`) does **not** commute with its
> dependency (`add_def base`) — applied in the wrong order the precondition fails. Descending
> order would consider the high-rank dependent first, fail its precondition against the base,
> and wrongly hold it. So admission **must** be ascending (causal) order. The consequence:
> among genuinely *concurrent* conflicting effects, the **lower `(vv.rank, author, counter)`
> wins** — "oldest/first writer wins", not LWW.

This is sound (deterministic, valid, lossless — the loser is held, never dropped) and is the
*natural* outcome of causal admission. We adopt **first-writer-wins by total order** for T0
and document it; the user resolves the surfaced conflict regardless, so the auto-pick is only
a default. (LWW is recoverable later by making resolution author a higher-rank effect.)

## Edge cases — identity & union

- **EC1 — Re-pull / idempotency.** Pulling the same delta twice. *Decision:* union by `eid`
  dedups; a re-pull is a no-op. *Holds because* materialization dedups by eid and tombstones
  are a set. **Test.**
- **EC2 — Diamond / re-convergence.** A merges B; later B pulls A (which now contains B's own
  effects + A's). *Decision:* B dedups its own effects by eid; only A's net-new effects and
  any conflict records are new. *Holds because* eid is globally unique and stable. **Test.**
- **EC3 — Logical duplicate (same feature built twice).** Both replicas independently
  `add_def shorten` in `app.py` with different node ids. *Decision:* the second `add_def`'s
  precondition (name unused) fails → non-commuting → conflict surfaced, **not** silently
  merged. *Holds via* the gate's precondition + `_hold_reason`. **Test.**
- **EC4 — order_key collision.** *Decision:* `eid = author:counter` is globally unique, so
  `(vv.rank, author, counter)` is a strict total order; no ties. *Holds by construction.*

## Edge cases — concurrency & conflict shape

- **EC5 — Concurrent edits to different statements of one function.** *Decision:* commute →
  both land, zero conflict (the granularity payoff). *Holds via* `static_commute` distinct-pos
  rule + history-seeded gate. **Test (also exercised at gate level).**
- **EC6 — Concurrent edits to the same statement.** *Decision:* don't commute → one lands
  (first-writer by total order), the other is a durable conflict with a witness. **Test.**
- **EC7 — N-way concurrent conflict (3+ replicas edit the same statement).** *Decision:* the
  greedy admits one; the rest are each held against the admitted one. The conflict record must
  carry **all** losing sides, not just one. **Test.**
- **EC8 — Mutual-validity-but-joint-invalidity.** R1 adds a caller of `X`; R2 removes `X`;
  each valid alone, invalid together. *Decision:* the batch invariant check (`codebase_valid`
  on the merged result) fails, so the gate holds whichever effect is later in total order;
  the materialized tree stays valid (I1). Either resolution (keep X / drop caller) is valid;
  determinism is what matters. **Test.**

## Edge cases — lifecycle interplay (user flows)

- **EC9 — Revert here, edit there.** R1 reverts node N (tombstones its effects); R2
  concurrently edits a function N defined. *Decision:* tombstones **must** travel in the delta
  (else R2's merge wouldn't know N is gone). After union, R2's edit targets a function whose
  defining effect is tombstoned → precondition fails → conflict ("edit of reverted feature").
  *Requires:* delta carries tombstones. **Test.**
- **EC10 — Suspend/status divergence.** R1 suspends node N (`switch off`), R2 extends N.
  *Decision:* **DEFERRED.** Node *status* lives in `graph.json`, not the log, so it has no
  causal stamp and no merge rule yet. T0 merges *effects*; node-metadata (status/intent/kind)
  reconciliation is a separate unit. For T0, status changes are not synced — flagged loudly,
  not silently mis-merged. *Why safe to defer:* effect-level merge is correct regardless; a
  stale status only affects whether a node's (already-merged) effects are active locally.
- **EC11 — Pull onto a drifted tree.** User has uncommitted hand edits and pulls. *Decision:*
  the existing pre-flight drift guard applies; `pull` first distills/`sync`s local drift into
  the log (so it has eids/causality), **then** merges the delta. Drift + incoming conflict
  compose: local distilled effects and incoming effects both go through the same gate.
  *Owned by* the transport plan (#002) calling `sync` then `merge`. **Test at transport level.**

## Edge cases — re-derivation & closure

- **EC12 — Cross-replica dependency.** R2's node uses a name R1's node defines. *Decision:*
  re-infer edges over the union so the `DEPENDS_ON` edge exists; guard with
  `would_create_cycle`. *Needed for* correct revert-closure post-merge. **Test.**
- **EC13 — Conflict closure.** A held (conflicting) node depends on the admitted node it lost
  to; reverting the admitted node should GC the held conflict. *Decision:* reuse the existing
  quarantine anchoring (`against` edges) so closure already covers it. **Test.**

## Edge cases — materialization

- **EC14 — Order independence (I2).** *Decision:* union is commutative; materialization sorts
  by total order; the conflict winner is chosen by total order (not merge order). So
  `merge(A,B)` ≡ `merge(B,A)` in both materialized tree and conflict set. **Test (both
  directions + a permutation).**
- **EC15 — Empties.** Removing all statements / reverting to nothing renders `pass` / drops the
  file. *Holds via* `StatementSeq.render` + `write_working_tree`'s managed-file deletion.
  **Covered by existing tests.**

## Further edge cases found while implementing (honest scoping)

- **EC9b — concurrent extend of a reverted node.** R1 reverts node N (tombstones it); R2
  concurrently `extend`s **the same node N** (adds effects under `node_id = N`). *Current
  behavior:* the tombstone hides **all** of N's entries, including R2's concurrent extend, so
  R2's work is silently dropped rather than surfaced. *Status:* **DEFERRED** — detecting this
  needs the tombstone to be vv-stamped (so "extend concurrent-with revert" is distinguishable
  from "extend the receiver already saw"), which is the same versioned-lifecycle work as EC10.
  *Mitigation today:* an edit that lands on a **separate** node (the common flow — EC9) is
  caught correctly; only same-node extend-vs-revert is lost. Flagged, not silent in the design.
- **EC-atomic — node-atomic conflict granularity.** A node bundling one conflicting effect and
  one independent effect is quarantined **as a whole**, so the independent effect is held too.
  *Status:* accepted for T0 (conservative, never wrong — just coarse). Finer per-effect
  splitting is a later refinement; it composes with statement granularity (most nodes touching
  one function are already fine-grained).

## Explicitly DEFERRED (with reason)

- **T1 multiverse / T2 resynthesis** (resolution that authors superseding effects) — Phase E.
  T0 only opens conflicts; it does not resolve them beyond the deterministic default winner.
- **EC10 node-metadata merge** — needs a versioned-metadata or status-as-log-event design.
- **Resolved-conflict supersession on re-merge** — depends on T1/T2 authoring; once a
  resolution effect (higher rank, observed both sides) exists it dominates, but marking the
  conflict closed so it doesn't re-open is a Phase E concern.
- **Log compaction / GC** — the log grows unbounded; out of T0 scope.

## Scope of the T0 implementation this enables

`Delta` (entries + tombstones + node metadata), `merge(local, delta)`, a `Conflict`
projection (`base`, `sides`) over the durable quarantine records, and tests EC1–EC9, EC12–EC14.
Everything else above is deferred with a stated reason.
