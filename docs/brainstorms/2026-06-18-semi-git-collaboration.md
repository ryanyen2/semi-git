---
date: 2026-06-18
topic: semi-git collaboration / local-first / "replace git"
status: exploration
---

# semi-git (sgt) — Collaboration & Local-First Architecture

## Summary

The ambition: let users operate version control **at the `sgt` level** — features,
concepts, intents, and typed effects — rather than at git's line/commit level, and do
it **local-first**: the local replica is authoritative, an online "semantic GitHub" is
just a sync rendezvous. To replace git rather than wrap it, semi-git must own
**identity, causality, merge, and history** at the semantic layer with a precise
convergence guarantee.

The central thesis of this doc: **semi-git already has the hard primitive most
local-first systems lack** — typed operations with a formal commutation relation plus
an invariant gate. That makes the effect log close to an *operation-based CRDT with an
invariant constraint*, and it makes **multi-replica merge structurally identical to the
existing parallel fan-out**. The collaboration layer is therefore mostly about adding
(1) causality metadata, (2) deterministic tree construction, and (3) an intent-aware
conflict policy — not about inventing a new merge engine.

## Goal & Non-Goals

- **Goal.** Users `commit`, `branch`, `merge`, `pull`, `push`, `blame`, and time-travel
  in terms of *nodes and intents*. Text/commits become a derived materialization.
- **Goal.** Local-first: every operation works offline against the local `.sgt` store;
  sync is an explicit, conflict-aware reconciliation, never a precondition.
- **Goal.** A merge that is *correct by construction* w.r.t. invariants (no broken tree
  ever lands), and *convergent* (any two replicas with the same operations compute the
  same state).
- **Non-goal (initially).** Real-time co-editing (OT-style, sub-second). We target
  async, PR-shaped collaboration first; live co-presence is a later layer.
- **Non-goal (initially).** Dropping git as the *transport*. Git can remain the dumb
  pipe under the remote while the user-facing model is fully semantic.

## Core Insight: merge == fan-out, and the log is a constrained CRDT

Today's parallel fan-out (`sgt/orchestrate/loop.py`) dispatches concurrent agents that
produce effects against a shared materialized state, then reconciles them with the
confluence gate (`can_land` / `max_coordination_free_batch_explained`), quarantining
what does not commute and attempting `rewrite_to_commute`.

Multi-replica merge is the **same problem with a different source of concurrency**:
instead of N agents on one machine, it is N humans/agents on N machines producing
effects against a shared base. So the merge engine *is* the fan-out engine:

```
fan-out:   one base  +  concurrent effect sets from local agents   → confluence gate → land / quarantine
merge:     common base + concurrent effect sets from remote replicas → confluence gate → land / quarantine
```

This reuse is the most important architectural fact in this doc. It means the merge
algorithm is largely built; what is missing is the *bookkeeping* that tells us which
effects are concurrent (causality) and the *policy* for the genuinely non-commuting
cases (same-unit edits).

The effect log is then an **op-based CRDT** in all the easy cases (commuting effects
converge automatically) plus an **invariant-constrained conflict set** for the hard
cases — unlike a classic CRDT, not all operations commute, and that non-commutation is
exactly the signal we want to surface, not hide.

## What syncs: the effect log, not the text

The synced object is the **operation log** + node metadata + causal stamps — *never*
the materialized source (it is derived by replay). This is smaller, structured, and
mergeable in a way text never is. A push is "here are my effects/nodes you haven't
seen"; a pull is "give me yours, I'll reconcile locally."

Per-replica durable state grows to:

- The existing semantic graph (`Node`s, edges, bundles).
- An **append-only effect log**: each entry is `(effect, effect_id, node_id, author_replica, version_vector)`.
- A **version vector** (one Lamport counter per known replica) tracking what this
  replica has observed.

## Identity & causality

1. **Node identity.** Node IDs are already UUIDs (`new_node_id()`), so two replicas
   never *collide* by construction — good for offline creation. But two people who
   independently build "the same" feature produce *different* UUIDs touching
   *overlapping targets*. That is a **semantic duplicate**, detectable only by target
   overlap, and must be *surfaced*, never silently merged. Rule: merge by id when ids
   match; flag target-overlap-across-distinct-ids as a review item.

2. **Effect identity.** The model already has an optional `eid` field — make it
   mandatory and globally unique (`replica_id : counter`). This lets us dedupe replays
   and reason about causality at effect granularity.

3. **Causality via version vectors.** Each effect (or node) is stamped with the
   author's version vector at creation. Given two effect sets, partition into:
   - **causally ordered** (one vector dominates the other) → sequential, just replay in
     order, no conflict;
   - **concurrent** (neither dominates) → run through the confluence gate.
   This is the standard CRDT machinery and the only new theory we must add.

## The merge algorithm

Merging local replica `L` with incoming replica `R`:

1. **Find the common causal base** from the version vectors; everything after it on
   each side is the divergent suffix `ΔL`, `ΔR`.
2. **Replay the base** to a `Codebase` (existing `materialize()`).
3. **Feed `ΔL ∪ ΔR` through the confluence gate** against that base
   (`max_coordination_free_batch_explained`). Effects that commute land in a single
   canonical order; effects that do not are **quarantined with a witness** (the
   `QUARANTINED` status already exists for exactly this).
4. **Establish cross-replica dependency edges**: deps are inferred from name usage
   (`_infer_dependencies`); run the same inference across the merged set so a node from
   `R` that uses a name defined by a node from `L` gets the edge and correct layering.
5. **Attempt `rewrite_to_commute`** on quarantined effects against the post-merge state
   (already done for fan-out under-serialization) before escalating to a human.

Crucially, steps 2–5 are *existing code paths*. The new part is steps 1 and 4's
cross-replica framing.

## Conflict policy: tiers from deterministic to intent-aware

The hard case is `replace_def` vs `replace_def` on the **same unit** — two people edit
the same function. These do not commute. Policy, in increasing sophistication:

- **T0 — Deterministic quarantine + LWW tie-break.** Keep one (total order on
  `(version_vector, replica_id, effect_id)`), quarantine the other with a witness.
  *Fully deterministic*, never loses data (the loser is durable, just inactive),
  satisfies convergence. This is the v1 floor.
- **T1 — Multiverse / superposition.** Keep *both* edits as parallel branches of the
  same node and let the user collapse with a measure. This is exactly deferred idea #5
  (`docs/ideation`), and same-unit merge is its first real use case.
- **T2 — Intent-aware re-synthesis (the strategic payoff).** Because every node carries
  **intent**, semi-git can resolve a conflict git fundamentally cannot: hand an agent
  *the base unit, both edited versions, and both intents*, ask it to synthesize a unit
  satisfying both intents, then re-gate through confluence. This is the feature that
  justifies "replace git" rather than "git with extra steps" — semantic, intent-driven
  merge instead of textual 3-way.

**Determinism caveat for T2.** Re-synthesis is non-deterministic and would break
convergence if treated as an automatic merge function. Resolution: T2 is a
**human-in-the-loop operation that *authors a new effect*** (with its own id and
author), not a pure merge function. The automatic merge stays deterministic (T0/T1);
re-synthesis is an explicit user action layered on top whose *output* re-enters the log
as a normal authored effect. This keeps the convergence guarantee intact.

## The load-bearing constraint: deterministic tree construction (SEC)

For local-first to be sound we need **Strong Eventual Consistency**: *any two replicas
that have observed the same set of effects materialize byte-identically.* That forces:

- **Tree construction is a pure, deterministic function of the effect log.** The
  deterministic `fallback_cluster` path (group by file/owner) — or a formally specified
  canonical clustering rule — must be the **source of truth** for structure. The
  `llm_cluster` path is **advisory only**: it may *suggest* labels/intents/grouping, but
  its output is committed as ordinary authored effects/metadata, never used as a
  non-deterministic merge function. (This directly tensions with today's
  LLM-first distillation — resolving it is prerequisite to collaboration.)
- **A total order on effects** for replay (`(version_vector, replica_id, effect_id)`),
  so "the canonical order" is well-defined on every replica.
- **Convergence test as a correctness gate**: property test that `merge(L,R)` and
  `merge(R,L)` and any interleaving produce identical materialization. This is
  falsifiable and should be CI-enforced.

SEC is the line between "a real local-first VCS" and "a tool that sometimes diverges."
Everything else is negotiable; this is not.

## "Semantic GitHub" — the remote

- **The remote is dumb; merge is client-side** (local-first principle). The server
  stores per-replica effect logs + node metadata and serves deltas by version vector.
  It may *optionally* precompute a merged mainline for fast clone, but the authoritative
  merge can always be recomputed locally.
- **`sgt push`** = upload effects/nodes the remote hasn't seen (by version vector).
- **`sgt pull`** = download remote deltas, run the local merge, surface quarantines.
- **Semantic Pull Request** = "I propose these N nodes (intents + effect bundles);
  here is how they land/quarantine against your mainline, with witnesses." Review
  happens at the level of *intent and effects*, not text hunks — though a materialized
  text diff is always renderable for humans who want it.
- **Transport phasing.** v0 can ride a git remote: serialize the effect log as files
  under `.sgt/` and let git move the bytes. A purpose-built sync server comes later only
  if the git-as-transport seams hurt.

## Semantic history, blame, time-travel

- **History** is the effect log; **time-travel** is "replay up to version-vector cut."
- **Semantic blame** beats `git blame`: every unit traces to the node and *intent* that
  introduced or last replaced it — you get the *why*, not just the commit.
- **Branches** become *named version-vector cuts* / sets of active nodes, not
  pointer-to-commit. `switch on|off` already approximates feature-level branching
  locally.

## Hard problems / open risks

1. **Same-unit concurrent edit** is the core conflict and the whole game; T0 is the
   floor, T2 is the differentiator. Get T0 provably convergent before attempting T2.
2. **Mutual-validity-but-joint-invalidity.** Two effects each invariant-valid alone but
   invalid together (e.g. divergent renames of one symbol; one removes a def the other
   calls). The invariant gate *detects* the broken result; the *policy* for which to
   quarantine must be deterministic.
3. **Effect granularity caps merge quality.** Unit-granular `replace_def` means *any*
   same-function edit conflicts, even on disjoint lines. **Finer-grained
   (sub-statement) effects would directly raise merge resolution rate** — this connects
   the deferred granularity item to collaboration value, and may justify pulling it
   forward.
4. **Determinism vs LLM** everywhere structure is decided (clustering, gardener,
   re-synthesis). The discipline "LLM proposes, deterministic function disposes, output
   re-enters as authored effects" must hold globally.
5. **Cross-replica dependency edges** rely on name-inference; ambiguous/duplicate names
   across replicas need a tie-break.
6. **Log compaction / GC.** The log grows unbounded; snapshot+compaction must preserve
   the ability to merge with a replica that branched before the snapshot (keep a
   causal frontier).
7. **Identity of "the same feature" built twice** — semantic duplicates by target
   overlap; surface, don't auto-dedupe.

## Suggested phasing

- **P1 — Causal foundation (local).** Mandatory effect ids + per-replica version
  vectors + total-order replay. Make tree construction deterministic
  (`fallback_cluster` canonical; LLM advisory). Add the SEC property test. *No
  networking yet* — this is the prerequisite and is all local.
- **P2 — Two-replica merge, T0 policy.** Implement `merge(L,R)` reusing the confluence
  gate; deterministic quarantine + LWW; cross-replica dep inference. Prove convergence
  in CI. Transport = shared git remote carrying the serialized log.
- **P3 — Semantic PR & remote UX.** `sgt push` / `pull` / semantic-PR surface with
  witnesses; quarantine review UI.
- **P4 — Intent-aware merge (T2) + multiverse (T1).** Re-synthesis as a HITL
  effect-authoring operation; superposition for same-unit edits.
- **P5 — Finer effect granularity & live co-editing.** Sub-statement effects to raise
  auto-merge rate; optional real-time layer.

## Open decisions (need product/owner input)

- **D1.** Is async/PR-shaped collaboration the right first target, or is real-time
  co-editing in scope sooner? (Assumed async-first here.)
- **D2.** Conflict default: T0 LWW-quarantine vs. T1 multiverse as the *baseline*
  experience? (Assumed T0 floor, T2 as the headline feature.)
- **D3.** Transport: ride a git remote first (fast, leaky) vs. build a semantic sync
  server early (slow, clean)? (Assumed git-as-transport for P2.)
- **D4.** Do we pull sub-statement effect granularity (a deferred item) *forward*,
  given it directly limits merge quality? (Recommend: yes, before P4.)
- **D5.** How hard do we enforce determinism — is byte-identical SEC the bar, or
  semantic-equivalence (normalized AST) sufficient? (Recommend: normalized-AST identity,
  matching the existing `normalize()` round-trip.)
