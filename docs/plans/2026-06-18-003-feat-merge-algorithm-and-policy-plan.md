---
title: "feat: semantic merge algorithm & conflict policy (T0→T1→T2) on the effect-log-primary core"
type: feat
status: draft
date: 2026-06-18
origin: docs/design/2026-06-18-effect-log-primary-redesign.md
depends-on:
  - docs/plans/2026-06-18-002-feat-local-first-collaboration-plan.md
---

# feat: semantic merge algorithm & conflict policy (T0→T1→T2)

## Summary

This plan implements the **semantic merge** at the heart of "replace git": reconcile two
replicas' divergent effect logs into one convergent state, with a conflict policy that
escalates from **T0 (deterministic LWW quarantine)** through **T1 (multiverse /
superposition)** to **T2 (intent-aware re-synthesis)** — the resolution git cannot do.

It is built on the **effect-log-primary redesign** (origin doc): the log is the only
structure that merges; order, node grouping, and the dependency DAG are projections;
effects are **statement-granular** so same-function edits commute; commutation is a
**static algebra** so merge is near-linear; and conflicts are **first-class objects** with
a single `resolve()` operation. This plan sequences the foundational refactor (Phases A–C)
and then the policy tiers (Phases D–F).

This plan **supersedes the collaboration plan's Phases A–B** (causal foundation +
deterministic clustering are absorbed into the log-primary refactor) and **builds on its
Phases C–D** (two-replica merge entry point + git transport).

---

## Problem Frame

`max_coordination_free_batch_explained` already lands the maximal confluent set and holds
the rest — the merge *engine* exists. What is missing is (a) a core that lets merge be a
clean log-union + re-project instead of a four-structure reconciliation, (b) statement
granularity so the common case (two people, same function, different lines) does not
needlessly conflict, (c) a near-linear commutation check for large divergent suffixes, and
(d) a conflict representation rich enough to hold "both sides" and "merged by intent."
Without the redesign, every one of these would be a patch on structures shaped for a
single writer. With it, the policy tiers are small, well-isolated additions.

---

## Key Technical Decisions

- **KTD1. Merge is `union(logA, logB)` → re-project.** Given two logs, compute the common
  causal prefix and the concurrent suffixes, union them, and run the deterministic
  projection (order → gate → cluster → edges). No edge-merging, no order-merging — those
  are derived. (origin thesis; revises fanout KTD1.)
- **KTD2. The commutation algebra decides most pairs without applying.** Conflicts on
  same-target `replace`/`remove` are declared structurally; statement ops on distinct
  stable ids commute; only ambiguous pairs fall to apply-and-compare (with an AST cache).
  (origin §3.)
- **KTD3. A `Conflict` is a first-class, durable object** holding `base`, `sides`, and a
  `resolution` ∈ {Unresolved, Collapsed(side), Resynthesized(effects)}. `QUARANTINED`
  becomes "node backing an `Unresolved` conflict." (origin §4; revises fanout KTD3.)
- **KTD4. One `resolve(conflict, strategy)` op authors new effects.** `auto`
  (rewrite-to-commute), `collapse(side)` (T1), `resynthesize` (T2) all emit ordinary
  `LogEntry`s that supersede the conflict — keeping the projection deterministic while the
  LLM acts only inside resolution. (origin §5; revises fanout KTD7.)
- **KTD5. T0 is the always-correct floor and ships before T1/T2.** An `Unresolved`
  conflict materializes the deterministic LWW winner and holds the rest; convergence and
  "never a broken tree" hold without any human or LLM step. T1/T2 are strictly better UX
  on top, never a correctness dependency.
- **KTD6. Statement identity uses tree-CRDT positional ids**, prototyped in isolation
  before wiring into effects (origin R1). Def-level ops are unchanged and remain valid.

---

## High-Level Technical Design

```
sgt pull  (or local fan-out — same engine)
  → union(local_log, incoming_delta)                      # KTD1
  → derive materialization order (topo + total order)     # collab KTD5, now sole authority
  → for each concurrent group, gate via commutation algebra + invariant check   # KTD2
        commute        → land (projected into nodes via canonical_cluster)
        do-not-commute → open a Conflict{base, sides}      # KTD3
  → resolve(conflict, auto)  bounded rewrite-to-commute    # KTD4 (auto)
  → unresolved conflicts materialize LWW winner (T0)       # KTD5
  → report landed nodes + open conflicts (with witnesses + both sides)
```

T1 adds `resolve(conflict, collapse(side))`; T2 adds `resolve(conflict, resynthesize)`
that synthesizes a unit from base + all sides + all intents and re-gates it.

---

## Output Structure

New files:
- `sgt/effects/stmt.py` — statement-granular addressing + tree-CRDT positional ids.
- `sgt/engine/algebra.py` — static commutation table + AST-cache-backed fallback.
- `sgt/conflict/model.py` — `Conflict`, `Side`, `Resolution` types (projection of the log).
- `sgt/conflict/resolve.py` — the unified `resolve(conflict, strategy)` operation.
- `scripts/e2e_merge.py` — live divergence → T0/T1/T2 resolution verification.
- Tests mirroring each (`tests/effects/test_stmt.py`, `tests/engine/test_algebra.py`,
  `tests/conflict/test_model.py`, `tests/conflict/test_resolve.py`).

Modified files:
- `sgt/effects/model.py` — `insert_stmt` / `replace_stmt` / `remove_stmt` ops.
- `sgt/store/oplog.py` (from collab plan) — log is primary; conflicts project from it.
- `sgt/sync/merge.py` (from collab plan) — re-expressed as union → re-project + conflicts.
- `sgt/project.py` — order/bundles/edges become caches of log projections.
- `sgt/cli.py` — `sgt resolve <conflict> [--collapse <side> | --synthesize | --auto]`.

---

## Requirements

- **M1.** Merge is union-of-logs → deterministic re-projection (no separately-merged state).
- **M2.** Commutation is decided by a static algebra for all non-ambiguous pairs; the
  semantic fallback parses each file at most once per batch.
- **M3.** Statement-granular edits to the same unit commute when they touch distinct
  statements (stable ids survive concurrent insertion).
- **M4.** A conflict is a durable first-class object with base, sides, and resolution.
- **M5.** `resolve()` with `auto`/`collapse`/`resynthesize` all author new superseding effects.
- **M6.** T0 (LWW quarantine) converges and never materializes a broken tree, with no
  human/LLM step.
- **M7.** T1 lets a user collapse a conflict to a chosen side deterministically.
- **M8.** T2 synthesizes a merged unit from base + sides + intents and re-gates it; a
  failed synthesis falls back to an open conflict, never a silent bad merge.
- **M9.** `merge(L,R)` and `merge(R,L)` converge to normalized-AST-identical state and the
  same conflict set (SEC under merge).

---

## Implementation Units

### Phase A — Log-primary foundation (refactor, behavior-identical)

### A1. Effect log as sole authored state; order/bundles/edges become projections
- **Goal:** Collapse `order` + `bundles` + edge-set into one log; derive the rest.
- **Requirements:** M1
- **Dependencies:** collab-plan A1–A4 (replica id, version vector, log) — absorbed here
- **Files:** `sgt/store/oplog.py`, `sgt/project.py`, `tests/test_project.py`
- **Approach:** Make `EffectLog` the authored structure (`(eid, node_id, effect, author, vv)`).
  Re-express `active_effects`/`bundles`/`self.order` as derived views over the log; keep them
  as cached properties for callers. Add a one-way migration from the existing
  `effects.json` (`order`+`bundles`+`witnesses`) into the log on first `open`. Prove
  `materialize()` is normalized-identical to today across the existing corpus.
- **Patterns to follow:** `Project.open/save` round-trip (`project.py:77-109`);
  `active_effects` (`project.py:112`).
- **Test scenarios:**
  - Happy: existing serial/fan-out scenarios materialize identically through the log.
  - Migration: a pre-redesign `.sgt` opens, migrates, and round-trips.
  - Edge: revert/closure still works with bundles/edges derived, not stored.

### A2. Derived materialization order (topological + total order)
- **Goal:** Make replay order a pure function of the log, not insertion order.
- **Requirements:** M1, M9
- **Dependencies:** A1
- **Files:** `sgt/project.py`, `tests/test_project.py`
- **Approach:** Replace `self.order` iteration in `materialize` with: derive effect-level
  dependencies (origin §1), topologically sort, break ties by `(vv.rank, replica_id,
  counter)`. Add the SEC property test: permuting authoring order of causally-independent
  effects yields normalized-identical output. (Supersedes collab-plan A4/KTD5.)
- **Patterns to follow:** `SemanticGraph.topo_order` (`graph.py:182`); `normalize`
  (`invariants.py`).
- **Test scenarios:**
  - Happy: dependency-respecting order is produced regardless of append order.
  - `Covers M9.` two permutations of the same independent effects → identical materialization.
  - Edge: a dependency cycle in derived deps is surfaced, not silently dropped.

### Phase B — Commutation algebra (optimization + better witnesses)

### B1. Static commutation table with AST-cached semantic fallback
- **Goal:** Decide commutation without apply for non-ambiguous pairs; parse once per file.
- **Requirements:** M2
- **Dependencies:** A1
- **Files:** `sgt/engine/algebra.py`, `sgt/engine/confluence.py`, `tests/engine/test_algebra.py`
- **Approach:** `commute_static(e1, e2) -> bool | None` returning a verdict for known pairs
  (monotone+disjoint → True; same-target replace/remove → False; distinct-stmt-id ops →
  True; …) and `None` for ambiguous pairs. `commute()` consults the table first and only
  falls back to apply-and-compare (`confluence.py:37`) when `None`, using a per-batch parsed
  AST cache. No change to *which* batches are confluent on the current op set (regression).
- **Patterns to follow:** `commute` / `_disjoint_paths` (`confluence.py:24-55`).
- **Test scenarios:**
  - Happy: monotone disjoint pair decided statically (no apply call — assert via spy).
  - Happy: same-target `replace_def` pair declared conflicting statically.
  - Edge: an ambiguous rename pair falls back and matches the old apply-and-compare verdict.
  - Perf: a batch of N effects on one file parses that file ≤ once in the fallback path.

### Phase C — Statement granularity (the merge-quality lever)

### C1. Tree-CRDT statement identity (isolated prototype)
- **Goal:** Stable ids for statements in a function body under concurrent insertion.
- **Requirements:** M3
- **Dependencies:** none (prototype before wiring — origin R1)
- **Files:** `sgt/effects/stmt.py`, `tests/effects/test_stmt.py`
- **Approach:** Assign each body slot a fractional-index / RGA-style positional id so two
  concurrent inserts at "the same place" get distinct, totally-ordered ids without
  coordination. Pure data + algorithm; no effects yet. Define `between(a, b) -> id` and a
  total order over ids.
- **Patterns to follow:** value-type + JSON round-trip style in `graph.py`.
- **Test scenarios:**
  - Happy: `between` always yields an id strictly ordered between its neighbors.
  - `Covers M3 seed.` two concurrent `between(a,b)` ids are distinct and deterministically ordered.
  - Edge: dense repeated insertion between two ids does not collide or run out of space.

### C2. Statement-granular effect ops + structural materialization
- **Goal:** Express body edits at statement granularity, with statement identity that is
  **log-resident** (never recomputed from text).
- **Requirements:** M3
- **Dependencies:** C1, B1
- **Files:** `sgt/effects/model.py`, `sgt/effects/body.py`, `sgt/engine/commute.py`,
  `tests/effects/test_model.py`
- **CONSTRAINT discovered in implementation (2026-06-18):** `apply_effect` is text→text, and
  a statement's `PosId` is *not* in the source text. A structural materialize that re-seeds
  positions from the body text on each pass **loses stable identity**: after a commit an
  inserted statement is indistinguishable from an original, so positions shift and concurrent
  edits stop aligning — defeating the whole point. Therefore positions MUST come from the log,
  not from re-parsing text. The proven body model (`sgt/effects/body.py`, `StatementSeq`)
  assumes exactly this (slots keyed by `PosId`).
- **Chosen design (replaces the original sketch):** a function becomes *statement-managed* by
  **decomposing its body into statement effects** so its slot positions live in the log:
  - `add_def` of a statement-managed function emits one `insert_stmt` per body statement, each
    `PosId` derived deterministically from the `add_def` eid + statement index (reproducible by
    any replica from the log alone);
  - `replace_stmt(file, func, pos, source)` / `remove_stmt(file, func, pos)` /
    `insert_stmt(file, func, after, before, source)` carry the **resolved `PosId`** (from C1),
    never a text-relative index (indices are unsafe under concurrency);
  - **materialization is structural**: for each statement-managed function, replay *its*
    statement effects (from the log, in total order) into a `StatementSeq` and render —
    positions are never recomputed from text. Non-statement effects keep the existing
    text-`apply_effect` fast path; the two compose (defs/imports via text, managed bodies via
    `StatementSeq`).
- **Commutation (static, no apply):** two statement ops on the same func commute iff they
  target distinct `PosId`s; same-`PosId` replace/remove do not (→ Phase D conflict). Decided in
  `commute.py` (renamed from the plan's `algebra.py` — `sgt/lifecycle/algebra.py` already
  exists) **without** applying, so the gate need not run structural apply pairwise; only the
  batch invariant check materializes structurally.
- **Patterns to follow:** existing op constructors + `apply_effect` (`model.py:49-75`,
  `239-289`); the proven `StatementSeq` in `sgt/effects/body.py`.
- **Test scenarios:**
  - Happy: insert/replace/remove materializes correctly through the structural path.
  - `Covers M3.` two edits to *different* statements of the same function commute and both land
    (no conflict) — through the existing confluence gate.
  - Identity: an inserted statement survives a commit + re-materialize with the *same* `PosId`
    (the regression that the text-reseed approach fails).
  - Edge: two edits to the *same* statement conflict (→ Phase D conflict object).
  - Back-compat: a pre-existing `replace_def`-only history materializes unchanged.

### Phase D — Conflict object & T0 policy (always-correct floor)

### D1. First-class `Conflict` projected from the log
- **Goal:** A durable object holding base + competing sides + resolution.
- **Requirements:** M4, M6
- **Dependencies:** A1
- **Files:** `sgt/conflict/model.py`, `sgt/project.py`, `tests/conflict/test_model.py`
- **Approach:** `Conflict(id, base_ref, sides: list[Side], resolution)` where `Side =
  (author, intent, effects)`. It is a projection over log entries tagged as conflicting.
  An `Unresolved` conflict materializes the **deterministic LWW winner** (total order from
  A2) and holds the other sides — exactly today's quarantine behavior, re-expressed.
  Reframe `NodeStatus.QUARANTINED` as "node backing an `Unresolved` conflict"; migrate the
  existing `witnesses` map into conflict objects.
- **Patterns to follow:** `quarantine`/`resolve_quarantine` (`project.py:169-207`).
- **Test scenarios:**
  - Happy: a conflict persists/reloads with base + both sides + witness.
  - `Covers M6.` an unresolved conflict materializes the LWW winner; the tree stays valid.
  - Migration: an existing quarantined node becomes an `Unresolved` conflict.

### D2. Merge as union → re-project → conflicts (T0)
- **Goal:** The two-replica merge, re-expressed on the redesigned core.
- **Requirements:** M1, M6, M9
- **Dependencies:** A2, B1, C2, D1; reuses collab-plan C1u (delta) + transport
- **Files:** `sgt/sync/merge.py`, `tests/sync/test_merge.py`
- **Approach:** Re-express collab-plan C2u: `merge(local, incoming_delta)` = union logs,
  re-derive order, gate concurrent groups via the algebra, open `Conflict`s for
  non-commuting same-target groups (LWW winner materialized), then `resolve(auto)`.
  Order-independent by construction (union is commutative; projection is deterministic).
- **Patterns to follow:** `_run_plan` land/hold loop (`loop.py`); collab-plan C2u.
- **Test scenarios:**
  - Happy: disjoint edits merge with zero conflicts.
  - `Covers M3 end-to-end.` same-function/different-statement edits both land (no conflict).
  - `Covers M6/M9.` same-statement edits open one conflict; `merge(L,R)`≡`merge(R,L)`.

### Phase E — T1 multiverse & T2 intent-aware merge (the differentiator)

### E1. Unified `resolve()` op: auto + collapse (T1)
- **Goal:** One operation that authors superseding effects; ship `auto` and `collapse`.
- **Requirements:** M5, M7
- **Dependencies:** D1
- **Files:** `sgt/conflict/resolve.py`, `sgt/cli.py`, `tests/conflict/test_resolve.py`
- **Approach:** `resolve(conflict, strategy)`: `auto` = bounded `rewrite_to_commute`
  (fold in fan-out U7); `collapse(side_id)` = pick a side, author its effects as the
  resolution, supersede the conflict. Output is ordinary `LogEntry`s (new eid/vv). CLI:
  `sgt resolve <conflict> --collapse <side>` / `--auto`.
- **Patterns to follow:** `rewrite_to_commute` (fan-out U7); `resolve_quarantine`
  (`project.py:193`).
- **Test scenarios:**
  - Happy: `collapse(side)` supersedes the conflict; chosen side materializes; tree valid.
  - Happy: `auto` resolves a rewrite-fixable conflict; an unfixable one stays open.
  - `Covers M5.` resolution effects appear in the log with fresh ids and re-derive cleanly.

### E2. `resolve(resynthesize)` — intent-aware merge (T2)
- **Goal:** Synthesize a merged unit from base + all sides + all intents; re-gate it.
- **Requirements:** M5, M8
- **Dependencies:** E1
- **Files:** `sgt/conflict/resolve.py`, `sgt/agents/`, `tests/conflict/test_resolve.py`
- **Approach:** `resynthesize` hands an agent the base unit, every side's effects, and every
  side's **intent**, asks for a single unit satisfying all intents, parses it into effects,
  and re-gates through the confluence check. On gate failure, leave the conflict open with a
  witness (never land an ungated synthesis). HITL: the act is non-deterministic, but its
  *output* is logged effects, preserving SEC. CLI: `sgt resolve <conflict> --synthesize`.
- **Patterns to follow:** structured-output agents (`sgt/agents/classifier.py`,
  `sgt/adapter/openai_agent.py`); the confluence gate (`confluence.py`).
- **Test scenarios:**
  - Happy (mocked agent): a synthesized unit satisfying both intents lands after re-gating.
  - `Covers M8.` a synthesis that fails the gate leaves the conflict open (no silent merge).
  - Edge: synthesized effects carry fresh ids and re-derive deterministically.
  - Test expectation: agent mocked; no live network in unit tests.

### Phase F — Verification

### F1. Live end-to-end merge & resolution
- **Goal:** Prove T0/T1/T2 and SEC-under-merge on the real backend.
- **Requirements:** M6, M7, M8, M9
- **Dependencies:** E2
- **Files:** `scripts/e2e_merge.py`
- **Approach:** Two clones. (1) Same-function/different-statement edits → both land, zero
  conflict (proves the granularity payoff). (2) Same-statement edits → one conflict; resolve
  via collapse and via resynthesize; assert valid, deterministic outcomes. (3) Assert
  `merge(L,R)`≡`merge(R,L)` materialization + conflict set. Mirror `e2e_sync.py`/`e2e_fanout.py`.
- **Patterns to follow:** `scripts/e2e_sync.py`, `scripts/e2e_fanout.py`,
  `scripts/e2e_collab.py` (collab-plan D2).
- **Test scenarios:** the three above; run live as the verification artifact.

---

## Scope Boundaries

### Deferred to follow-up work
- **N-way conflicts (>2 sides) UX** — the model supports N sides; the resolution UX is
  tuned for 2 first.
- **Conflict-aware semantic blame/history surface** — richer than `push`/`pull` print.
- **Automatic T2 (no human trigger)** — T2 stays human-invoked; auto-resynthesis-on-merge
  is a later policy once trust is established.

### Deferred for later (origin)
- Real-time co-editing, confluence corpus (learned commute priors — would feed the algebra),
  RL-trained resolution policy, additional backends.

---

## Risks & Dependencies

- **R1. Statement identity (C1) is the research risk** — prototype in isolation; if it
  slips, T0 at def-granularity still ships and converges (just with more conflicts).
- **R2. The refactor must stay behavior-identical through Phase D** — guarded by the
  existing ~140 tests; the *only* intended behavior change is the new merge/resolution.
- **R3. T2 non-determinism** is contained to the resolution act; the SEC property test
  (A2, D2) is the guard that its output re-enters deterministically.
- **R4. `.sgt` migration** (A1, D1) from `order`/`bundles`/`witnesses` to log+conflicts —
  one-way migration + round-trip test.
- **Dependencies:** the effect-log-primary redesign (origin) is a hard prerequisite;
  reuses collab-plan C1u (delta), transport, and fan-out U7 (rewrite-to-commute).

---

## Open Questions (deferred to implementation)

- **OQ1.** Statement-id scheme: fractional index vs RGA vs Logoot — which best fits AST
  body slots and JSON persistence? (Prototype decides; C1.)
- **OQ2.** Should an `Unresolved` conflict materialize the LWW winner *silently* or emit a
  visible in-tree marker? (Default: materialize winner + surface in `status`/`pull` report;
  markers risk un-parseable trees, which the invariant gate forbids.)
- **OQ3.** Does `resynthesize` (T2) operate per-conflict or batch related conflicts for a
  coherent multi-unit merge? (Per-conflict first; batching later.)
- **OQ4.** How are statement-granular effects distilled from an out-of-band edit (reverse
  direction, `diff.py`)? Sub-statement reverse-distillation may lag the forward ops.

---

## Sources / Research
- Origin redesign: `docs/design/2026-06-18-effect-log-primary-redesign.md`
- Builds on: `docs/plans/2026-06-18-002-feat-local-first-collaboration-plan.md`
- Reused machinery: `sgt/engine/confluence.py`, `sgt/orchestrate/loop.py` (fan-out U7),
  `sgt/project.py` (quarantine), `sgt/effects/model.py`.
- Prior art: jj first-class conflicts; Pijul/Darcs commutative patches; CRDT positional
  identifiers (RGA / Logoot / fractional indexing); CodeCRDT (syntactic ≠ semantic merge).
