---
date: 2026-06-18
topic: core data-model & pipeline redesign (effect-log-primary, statement-granular, conflict-as-object)
status: design / ADR
supersedes-decisions:
  - core-plan KTD (replace_def is unit-granular)
  - core-plan (materialize = order-list replay)
  - collab-plan KTD1 (bundles are a derived index)
  - fanout-plan KTD1 (constraint graph is a separate transient type)
  - fanout-plan KTD3 (quarantine is a node status)
  - fanout-plan KTD7 (rewrite-to-commute is a bounded retry)
origin: docs/brainstorms/2026-06-18-semi-git-collaboration.md
---

# Effect-Log-Primary Redesign

## Why this doc exists

The collaboration plan (`docs/plans/2026-06-18-002-...`) showed that an *elegant* merge
is blocked by data structures shaped for the serial/fan-out era. Two product decisions
are now confirmed: **(1) sub-statement effect granularity is in scope** (it directly caps
merge quality), and **(2) deterministic tree construction replaces the LLM-first default**.
Both reach below the merge layer into the core model. Rather than patch merge on top of
structures that fight it, this doc redesigns the core so the merge algorithm falls out
cleanly. It is an ADR: it names the prior decisions it revises and why.

## Thesis

> **The effect log is the single source of truth and the single thing that merges.
> The working tree, the node grouping, the dependency DAG, and the materialization
> order are all *deterministic projections* of the log. Effects are addressed at
> AST-statement granularity with stable identifiers; commutation is a static algebra
> over `(op, path)` with a semantic fallback only on path overlap. Conflicts are
> first-class objects, and a single Resolution operation — which authors new effects —
> unifies rewrite-to-commute, multiverse collapse, and intent-aware merge.**

If only one structure merges and everything else is a pure function of it, then "merge"
is "union two logs and re-project," and Strong Eventual Consistency is a property of one
deterministic projection function rather than a property we must defend across five
mutable structures.

## Current frictions (grounded in the code)

1. **`materialize()` replays in insertion order, not dependency order.**
   `Project.active_effects()` iterates `self.order` (append order, `project.py:112`) and
   `materialize` applies in that order (`project.py:119`). `SemanticGraph.topo_order()`
   exists (`graph.py:182`) but is **not used** for materialization. Two replicas that
   appended the same effects in different orders can produce different trees → **SEC is
   not guaranteed by construction today**, only by accident of single-writer append order.

2. **`replace_def` is whole-unit; same-unit edits can never commute.**
   `apply_effect` replaces the entire def node (`model.py:266-275`), and `_disjoint_paths`
   treats same path as non-disjoint (`confluence.py:30`), so two edits to the *same
   function* always fall to the apply-both-orders path and conflict — even when they touch
   different lines. Unit granularity is the dominant source of avoidable conflicts.

3. **Commutation is O(n²) apply-and-compare with re-parse.**
   `max_coordination_free_batch_explained` calls `is_invariant_confluent` per prefix
   (`confluence.py:116`), each re-applying sequences and re-parsing source
   (`confluence.py:69-71`). Fine for a handful of fan-out effects; quadratic-with-reparse
   is the wrong shape for merging two large divergent suffixes.

4. **State is spread across four mutable structures that each must be kept consistent.**
   `self.order` (list), `self.bundles` (node→effects), `self.witnesses` (node→witness),
   and `SemanticGraph` (nodes+edges) all co-encode what is fundamentally *one ordered set
   of authored operations* (`project.py:59-63`). Merge would have to reconcile all four.

5. **Dependencies are incrementally mutated graph state, not a function of the log.**
   `_infer_dependencies` runs at land time and mutates edges (`project.py:142-145`,
   `300-311`). On merge we'd have to merge edge sets too — but edges are *derivable* from
   the effects, so merging them is redundant state to keep consistent.

6. **Conflict is binary and lossy in representation.** `quarantine()` stores the loser as
   a `QUARANTINED` node excluded from materialize (`project.py:169-191`); there is no
   first-class object holding *base + competing sides + resolution*. Multiverse (T1) and
   intent-merge (T2) have nowhere to live.

7. **Three resolution mechanisms, one concept.** `rewrite_to_commute` (fan-out U7),
   future multiverse-collapse, and future intent-merge all do the same thing — *produce
   new effects that supersede a conflict* — but would be three code paths.

## The redesigned model

### 1. The effect log is primary; everything else is a projection

One persisted structure: an **append-only log** of entries

```
LogEntry = (eid, node_id, effect, author_replica, version_vector, deps?)
```

Derived by pure functions of the log (no independently-mutated state):

- **materialization order** = topological sort over effect-level dependencies, broken by
  the total order `(vv.rank, replica_id, counter)` (KTD5 from the collab plan, now the
  *only* ordering authority);
- **node grouping** = `group_by(node_id)` plus the deterministic `canonical_cluster` for
  un-attributed effects;
- **dependency DAG** = `derive_edges(log)` re-running name inference over the whole log;
- **working tree** = replay in materialization order.

`self.order`, `self.bundles`, and the persisted edge set stop being authored state and
become caches of these projections. **Merge reconciles exactly one thing: the log.**

### 2. Statement-granular addressing with stable identifiers (tree-CRDT)

Extend the address space from "defs/classes at any depth" (`units()`, `model.py:105`) to
**statements inside function bodies**. The hard part is *stable identity under concurrent
insertion*: positional indices (`foo.body[2]`) break when two replicas insert at the same
spot. Adopt a tree-CRDT positional identifier (fractional-index / RGA-style) per body
slot so that:

- `insert_stmt(path_with_position, source)`, `replace_stmt(stmt_id)`, `remove_stmt(stmt_id)`
  become effect ops alongside the existing def-level ops;
- two inserts at *different* positions in the same function commute by construction;
- a replace of statement *k* commutes with a replace of statement *j≠k* in the same
  function — the case unit-granularity wrongly conflicts on today.

`replace_def` is retained as the coarse op (and as the fallback when an edit genuinely
rewrites a whole unit), but it is no longer the *only* way to express a body change.
This is the single largest lever on merge quality (frictions #2).

### 3. Commutation as a static algebra with semantic fallback

Replace the "apply both orders and compare" default with a **commutation table** keyed on
`(op1, op2, path-relationship)`:

- monotone + disjoint paths → commute (already the fast path, `confluence.py:45`);
- statement ops on distinct stable stmt-ids in the same body → commute;
- same-target `replace`/`remove` → do **not** commute (declare conflict directly, no apply);
- genuinely ambiguous pairs (rename touching another's references, etc.) → fall back to
  the existing apply-and-compare, now with a **parsed-AST cache** so a batch parses each
  file once, not O(n²) times.

This turns merge from quadratic-with-reparse into near-linear in the common case and makes
the *reason* for non-commutation a property of the algebra (better witnesses, friction #3).

### 4. Conflicts are first-class objects

```
Conflict = (id, base_ref, sides: list[Side], resolution)
Side       = (author_replica, intent, effects)
resolution = Unresolved | Collapsed(side_id) | Resynthesized(new_effect_ids)
```

A conflict is durable, lives in the log's projection, and **materializes deterministically**
while `Unresolved` (default side = the total-order/LWW winner, with the others visibly
held — this *is* today's quarantine behavior, now as one case of a general object). T1 and
T2 become *resolution states* of the same object rather than new subsystems (friction #6).
`NodeStatus.QUARANTINED` is reframed as "node backing an `Unresolved` conflict."

### 5. One Resolution operation authors new effects

`resolve(conflict, strategy) -> new effects`, where `strategy` is:

- **auto** — the bounded `rewrite_to_commute` (fan-out U7) attempt;
- **collapse(side_id)** — pick a side (T1 multiverse collapse);
- **resynthesize** — agent reads base + all sides + all intents, emits a merged unit (T2,
  the differentiator git cannot do).

All three produce ordinary authored `LogEntry`s (new eids, new vv) that supersede the
conflict. Determinism is preserved because the *output* re-enters the deterministic log;
the LLM is in the *act* of resolving, never in the projection function (friction #7,
and it satisfies the SEC discipline from the collab brainstorm).

## Revised verdicts (this is a redesign, not a patch)

| Prior decision | Source | Revision | Rationale |
|---|---|---|---|
| `replace_def` is unit-granular; sub-statement deferred | core plan | **Add statement-granular ops with stable ids** | Unit granularity is the dominant avoidable-conflict source (D4 confirmed) |
| `materialize` = `self.order` insertion replay | core / `project.py:112` | **Replay in derived topological + total order** | Insertion order is replica-dependent → breaks SEC |
| `bundles` is the (soon derived) index | collab KTD1 | **Log is primary; bundles/order/edges are caches** | One structure merges; the rest are pure projections |
| Constraint graph is a separate transient type | fanout KTD1 | **Unify ordering: dependency layering is one concept** used by fan-out *and* merge | Both "order effects by dependency before gating"; one layering function |
| Quarantine is a `NodeStatus` | fanout KTD3 | **Conflict is a first-class object; `QUARANTINED` = node backing an `Unresolved` conflict** | Gives T1/T2 a home; quarantine is one resolution state |
| Rewrite-to-commute is a bounded retry | fanout KTD7 | **One `resolve()` op** (auto / collapse / resynthesize) | Three mechanisms collapse into one; all author new effects |
| LLM-first distillation default | core / `sync` | **Deterministic `canonical_cluster` is source of truth; LLM advisory** | SEC requires deterministic structure (D2 confirmed) |

What is **kept** (still load-bearing, do not re-litigate): the confluence gate as the
universal mutation gate; intent as the source of truth; effects as the versioned object;
git underneath as materialization/transport; the invariant predicate `I`.

## Refactor sequencing (avoid a big-bang)

The redesign lands behind a stable seam, not as one rewrite:

1. **Log-primary first, behavior-identical.** Introduce the `LogEntry` log; make
   `order`/`bundles`/edges *derived* from it; prove `materialize()` is byte-for-normalized
   identical to today on the existing test corpus. (Absorbs collab-plan Phase A.)
2. **Derived ordering.** Switch `materialize` to topological + total order; SEC property
   test. (Absorbs collab-plan A4/KTD5.)
3. **Commutation algebra + AST cache**, behavior-identical to apply-and-compare on the
   current op set (pure optimization + better witnesses).
4. **Conflict object** wrapping current quarantine behavior (no new policy yet).
5. **Statement-granular ops** behind the existing def-level ops (additive; old effects
   keep working — the model already promises bare-name back-compat, `model.py:5-6`).
6. **Then** the merge policy plan (T0→T1→T2) builds on all of the above.

Steps 1–2 supersede the collaboration plan's Phase A/B; steps 3–6 are new and are
sequenced by the companion merge plan.

## Risks

- **R1. Stable statement identity is the hard research bit.** Fractional indexing /
  RGA for AST body slots is well-trodden in CRDT literature but new to this codebase;
  prototype it in isolation before wiring it into effects.
- **R2. Making everything a projection means the projection functions are now critical
  path** — they must be fast and total. Cache invalidation is the cost we accept for
  single-source-of-truth merge.
- **R3. Back-compat of persisted `.sgt`.** Existing `effects.json`
  (`order`+`bundles`+`witnesses`) must migrate into the log on first open; write a
  one-way migration and a round-trip test.
- **R4. Scope.** This is a core refactor touching `model.py`, `confluence.py`,
  `project.py`, `graph.py`, and the orchestrator. It must stay behavior-identical through
  step 4 (guarded by the existing ~140 tests) so the *merge* work is the only behavior
  change.

## Relationship to existing plans

- **Collaboration plan (#002):** its Phase A (causal foundation) and Phase B (deterministic
  clustering) are *absorbed and strengthened* here (log-primary subsumes "bundles as index").
  Its Phase C/D (two-replica merge + transport) remain valid and now sit on a cleaner base.
- **Merge algorithm & policy plan (#003, companion):** consumes this redesign as its
  foundation and implements T0→T1→T2 + the Conflict/Resolution model.
