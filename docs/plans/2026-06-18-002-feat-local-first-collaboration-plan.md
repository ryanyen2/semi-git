---
title: "feat: git transport & CLI for two-replica semantic sync (semi-git #collab)"
type: feat
status: draft
date: 2026-06-18
origin: docs/brainstorms/2026-06-18-semi-git-collaboration.md
depends-on:
  - docs/plans/2026-06-18-003-feat-merge-algorithm-and-policy-plan.md
  - docs/design/2026-06-18-effect-log-primary-redesign.md
---

# feat: git transport & CLI for two-replica semantic sync

> **Scope note (trimmed 2026-06-18).** This plan originally owned the causal foundation
> (replica id, version vectors, effect log) and the merge engine. Those have moved to the
> **effect-log-primary redesign** (`docs/design/2026-06-18-effect-log-primary-redesign.md`)
> and the **merge algorithm & policy plan** (`#003`), because they are core-model concerns
> that the merge algorithm is built on, not networking concerns. What remains here is the
> **transport and user surface**: moving a log delta between replicas over a git remote,
> the `clone`/`push`/`pull` verbs, and the end-to-end transport verification. This plan
> **depends on** `#003` (it consumes `#003`'s log, delta computation, and `merge()`).

## Summary

Make `sgt` synchronize between two replicas **local-first**: the local effect log is
authoritative, a git remote is a dumb rendezvous, and `pull` reconciles by running
`#003`'s `merge()` client-side. This plan supplies only the plumbing: serialize a log
delta to a file the git remote carries, and the `clone`/`push`/`pull` verbs that surface
landed nodes and open conflicts. All merge intelligence (log union, commutation, conflict
objects, T0→T2 resolution) lives in `#003`.

---

## Problem Frame

Once `#003` makes the effect log the single source of truth and `merge()` a deterministic
union → re-project, "collaboration" is reduced to a transport question: *get my peer's log
delta onto my machine and run the local merge.* That is deliberately thin. The only real
design choices here are (1) how the append-only log rides a git remote without git's own
3-way merge corrupting it, and (2) how `pull` presents the merge outcome (landed +
conflicts) at the CLI.

---

## Key Technical Decisions

- **KTD1. The remote is dumb; merge is client-side.** The git remote stores serialized log
  deltas and node metadata; it computes nothing. The authoritative `merge()` is always
  local (`#003` D2). This is the local-first guarantee.
- **KTD2. The log rides git as an append-only, structurally-merged file.** The log is
  serialized so that two replicas' versions are reconciled **by `eid` union**, never by
  git's line-based 3-way. Concretely: store the log as one entry per line (or per shard
  keyed by author) so concurrent appends do not produce git conflict markers; if git ever
  does conflict on it, resolution is "union both sides by eid" (idempotent, since `#003`
  replay dedups by eid).
- **KTD3. `pull` is the sanctioned reconciliation path; the drift guard stays.** Mutating
  verbs remain blocked under working-tree drift (existing pre-flight guard); `pull` runs
  `merge()` then `write_working_tree()`, so the tree only ever advances through a gated
  merge.
- **KTD4. `delta_since(peer_vv)` lives in `#003` (it is a pure log op).** This plan only
  serializes/deserializes the delta and moves it with git.

---

## High-Level Technical Design

```
sgt push
  → serialize local log delta (entries the remote frontier doesn't dominate) to .sgt/log/
  → git add/commit/push

sgt pull
  → git pull                                   # brings peer's .sgt/log/ shards
  → read incoming entries; compute delta vs local frontier
  → #003 merge(local_project, incoming_delta)  # union → re-project → conflicts
  → write_working_tree(); report landed nodes + open conflicts (witnesses + sides)

sgt clone <remote> [path]
  → git clone; load .sgt; replay the full log to materialize the tree
```

---

## Output Structure

New files:
- `sgt/sync/__init__.py`, `sgt/sync/transport.py` — log-delta (de)serialization + git move.
- `scripts/e2e_collab.py` — live clone → diverge → push/pull → converge (transport-level).
- `tests/sync/__init__.py`, `tests/sync/test_transport.py`.

Modified files:
- `sgt/cli.py` — `clone` / `push` / `pull` verbs + merge/conflict surface (reuse the
  fan-out checkpoint/quarantine printing already in the CLI).

Consumed from `#003` (not built here): the effect log, `EffectLog.delta_since`, `merge()`,
the `Conflict` object, `canonical_cluster`.

---

## Requirements

- **C1.** The log serializes to a git-carriable form reconciled by `eid` union, not git 3-way.
- **C2.** `push` uploads exactly the entries the remote has not observed.
- **C3.** `pull` runs `#003`'s `merge()` locally and surfaces landed nodes + open conflicts.
- **C4.** `clone` reconstructs a working replica by replaying the remote log.
- **C5.** The drift guard is preserved; the tree only advances through a gated merge.

---

## Implementation Units

### T1. Log-delta transport (serialize / deserialize / git move)
- **Goal:** Move a log delta between replicas over a git remote without git corrupting it.
- **Requirements:** C1, C2
- **Dependencies:** `#003` A1 (the log), `#003` C-delta (`delta_since`)
- **Files:** `sgt/sync/transport.py`, `tests/sync/test_transport.py`
- **Approach:** Serialize the log as per-author append-only shards under `.sgt/log/` (one
  file per `replica_id`), each entry a self-contained JSON line keyed by `eid`. `export_delta(project, peer_vv)` writes/returns the entries not dominated by `peer_vv`;
  `import_delta(project, entries)` ingests them (dedup by `eid`). Per-author sharding means
  two replicas append to *different* files → no git conflict; a same-author conflict
  resolves by eid-union (idempotent). No merge logic here — that is `#003`.
- **Patterns to follow:** `Project.save/_snapshot_sgt` (`project.py:95`, `209`); JSON
  round-trip in `store/graph.py`.
- **Test scenarios:**
  - Happy: export with empty peer vv yields the whole log; with frontier vv yields empty.
  - Happy: a delta round-trips through serialize → deserialize unchanged (by eid set).
  - Edge: two authors' shards never overlap; importing both is order-independent (eid union).
  - Edge: re-importing an already-known entry is a no-op (idempotent).

### T2. `clone` / `push` / `pull` CLI verbs
- **Goal:** The user-facing local-first sync surface.
- **Requirements:** C2, C3, C4, C5
- **Dependencies:** T1; `#003` D2 (`merge`)
- **Files:** `sgt/cli.py`, `tests/test_cli.py`
- **Approach:** `sgt push` = `export_delta` to `.sgt/log/`, `git add/commit/push`.
  `sgt pull` = `git pull`, read incoming shards, `import_delta`, run `#003` `merge()`,
  `write_working_tree()`, print landed nodes + open conflicts with witnesses+sides (reuse
  the existing quarantine/checkpoint printing). `sgt clone <remote> [path]` = `git clone`,
  load `.sgt`, replay log. Keep the pre-flight drift guard on other mutating verbs; `pull`
  is the gated reconciliation entry.
- **Patterns to follow:** verb dispatch + quarantine printing in `sgt/cli.py`; `GitBinding`
  in `store/gitbind.py`.
- **Test scenarios:**
  - Happy: clone → local edit → push → second clone pulls and converges (materialization equal).
  - Happy: a pull surfaces an open conflict with its witness and both sides.
  - Edge: pull with no incoming delta is a no-op; pull mid-drift guides the user (no clobber).

### T3. Live two-replica transport verification
- **Goal:** Prove the transport round-trip and convergence end-to-end (distinct from `#003`
  F1, which proves the *merge/resolution* logic).
- **Requirements:** C1–C5
- **Dependencies:** T2
- **Files:** `scripts/e2e_collab.py`
- **Approach:** Two clones of one repo over a local bare git remote. Disjoint edits on each
  → push/pull both ways → assert both present, identical materialization, zero conflicts.
  Then a same-target edit on each → push/pull → assert exactly one open conflict surfaces
  with a witness (resolution itself is `#003`'s e2e). Mirror `scripts/e2e_sync.py`.
- **Patterns to follow:** `scripts/e2e_sync.py`, `scripts/e2e_fanout.py`.
- **Test scenarios:** the two above; run live.

---

## Scope Boundaries

### Owned by `#003` / the redesign (NOT here)
- The effect log, replica identity, version vectors, `delta_since`, deterministic
  clustering, the commutation algebra, statement granularity, the `Conflict` object, and
  `merge()` + all conflict resolution (T0→T2). This plan strictly consumes them.

### Deferred to follow-up work
- Semantic-PR proposal/review surface beyond `push`/`pull` print.
- N-replica / server-mediated sync; a purpose-built (non-git) sync server.
- Log compaction / GC and snapshot-based `clone` (full replay is the v1 clone).

---

## Risks & Dependencies

- **R1. Hard dependency on `#003`.** Nothing here is buildable until `#003`'s log,
  `delta_since`, and `merge()` exist. Sequence `#003` Phases A–D first.
- **R2. Git-as-transport leaks (KTD2).** Per-author log sharding is the mitigation; if a
  git conflict on a shard ever occurs, the union-by-eid resolution must be wired as a git
  merge driver or a post-pull repair step.
- **R3. `clone` via full replay** is O(log) — fine for v1; revisit with compaction.

---

## Sources / Research
- Origin brainstorm: `docs/brainstorms/2026-06-18-semi-git-collaboration.md`
- Foundation: `docs/design/2026-06-18-effect-log-primary-redesign.md`,
  `docs/plans/2026-06-18-003-feat-merge-algorithm-and-policy-plan.md`
- Reused: `sgt/store/gitbind.py`, `sgt/project.py`, `sgt/cli.py`, `scripts/e2e_sync.py`.
