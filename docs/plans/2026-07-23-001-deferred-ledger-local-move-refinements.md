---
title: "deferred: save-time ledger local-move refinements (edge source + per-lane gamma)"
type: design
status: proposed
date: 2026-07-23
origin: docs/plans/2026-07-22-001-refactor-ownership-ledger-grid-verb-collapse-plan.md
---

# Deferred save-time-ledger local-move refinements

> A decision record for the two follow-ups the ownership-ledger refactor
> (`docs/plans/2026-07-22-001-...`) shipped a default for and deferred the "proper" version of.
> Both concern `sgt.lens.ledger.local_move_assign` — the bounded Leiden local move that assigns a
> genuinely-new symbol a lane at `save` time (cascade step 3). Neither is a bug: the shipped
> defaults are correct and deterministic. The question each answers is *"is the proper version worth
> its complexity, and if so, how is it built?"* — so this is a plan, not a patch.

## Why these were deferred, not built

The local move exists so a new symbol lands in a **stable** lane the moment it is saved, without a
whole-repo recluster. It approximates what a full recluster's binary search would do, using two
inputs it has to approximate cheaply:

1. the **coupling edges** incident to the new symbol (its neighbourhood in the fused graph), and
2. the **CPM resolution** `gamma` at which to resolve join-vs-split.

Both approximations were shipped with a defensible default (a full whole-repo `cluster.signals`
reparse for (1); the geometric-midpoint gamma for (2)) precisely because the "proper" version of
each turns out to be a design change with real correctness surface — the kind of thing that earns
its own unit with its own tests, not an inline tweak during a refactor.

---

## Refinement A — the local-move edge source

### What ships today

`assign_at_save` (`sgt/lens/ledger.py`) obtains the fused coupling graph via
`tree.fused_graph_with_hubs(repo, ops, ideal)` → `cluster.signals`, whose dominant cost is
`build_entity_graph(gb.tree_at(head))` — a **whole-repo source parse** (measured at ~1.2s of
`signals`'s ~1.3s on this repo; see `sgt/lens/cluster.py::_structural_edges_at`).

That parse is cached by HEAD sha (`structural_edge_cache`). But the save flow is:

```
get()          # mine-on-contact; the miner parses the entity graph at the tip, transiently
put()          # materialize the ideal as a *witness commit* -> HEAD moves to a fresh sha
assign_at_save # cluster.signals -> _structural_edges_at(new_head) -> CACHE MISS -> full reparse
```

So the cache **always misses** on a save that adds a new entity, and the whole-repo parse is paid
again — on a common operation. The cost is bounded to saves that add a *genuinely-new* symbol
(`assign_at_save` returns early on the modify-only path, `sgt/lens/ledger.py`), and the reparse does
warm the cache for the next `sgt log`, so it is not *pure* waste. But it is exactly the
whole-repo-mining cost the incremental miner (U10) exists to eliminate, reintroduced one layer up.

### The key observation

The miner already computed `build_entity_graph(codebase_after)` for the tip **during the same
save's `get()`** (`sgt/core/mine.py::_mine_one`), and the witness commit's tree is content-identical
to the working tree the miner parsed (R2 round-trip: `code(ideal) == HEAD tree`). The entity graph
is a pure function of source content, so the graph `assign_at_save` reparses is byte-identical to
one the same process already built and threw away.

### Options

- **A0 — Accept (status quo).** Zero code. `save` is a write (not a glanceable read); it already
  pays `get` + `put` + a git commit, so +~1.3s on new-entity saves is tolerable, and the reparse
  self-warms the cache. *Cost: the reparse recurs on a common op.*
- **A1 — Tree-sha-keyed structural cache, populated by the miner (recommended path if built).**
  Re-key `structural_edge_cache` by `git write-tree` sha instead of commit sha, and have the miner
  persist the edges it computes for the tip. Because the witness commit's tree-sha equals the mined
  working tree's, `_structural_edges_at` resolves the witness HEAD's tree and **hits the miner's
  entry — zero reparse.** Reuses work already done; adds no new algorithm. *Correctness surface: all
  `cluster.signals` callers must agree on tree-sha (content) rather than commit-sha keying, and a
  test must pin "identical content → identical cache hit → identical edges."*
- **A2 — Incremental delta graph (U10's original intent).** Compute only the structural edges
  incident to the changed files' entities and merge into the prior graph. Largest change; fully
  eliminates the whole-repo parse even on the first read, but needs an incremental-merge algorithm
  plus a determinism guarantee that the merged graph equals the batch `build_entity_graph` result.

### Recommendation

**Ship A0 (already shipped); build A1 when the save-latency budget justifies it.** A1 is the
highest-ROI real fix — it deletes the reparse by reusing work the same save already did, with the
smaller correctness surface of the two. It is not a surgical edit (it changes a cache key's
semantics), so it warrants its own unit: (1) re-key `structural_edge_cache` by tree-sha; (2) thread
miner population of it; (3) a determinism/identity test. A2 is only worth it if profiling shows the
*first* read (cold cache, no prior save) is itself a bottleneck, which today it is not.

### Measurement

Measured on this repo's tree at HEAD (`feat/ownership-ledger-grid`), 333 source files / 6,467
structural edges: `build_entity_graph(edges_only=True)` is **~0.57s warm** (best of 3; the cold
first-parse the save actually pays runs higher — the code's own note records ~1.2s on a larger
vintage). That is the *structural* term alone; `fused_graph_with_hubs` adds the co-change / scope /
co-commit / path passes over the full op history on top. So a save that adds a genuinely-new symbol
pays on the order of a **half-second-plus whole-repo pass** it never needed — the number that makes
A1 worth building once save latency is on the critical path, and that A0 currently just eats.

---

## Refinement B — per-lane split gamma (KTD3 sources 1/2)

### What ships today

`local_move_assign` resolves its bounded partition at `gamma = _GAMMA_MIDPOINT` — the geometric mean
of the clusterer's search bounds (`tree.GAMMA_LO=1e-4`, `GAMMA_HI=1.0`), i.e. the scale a full
recluster's binary search centres on (`sgt/lens/ledger.py`). Durable, deterministic, and lane-blind.

The "proper" version (KTD3 sources 1/2) would resolve a new symbol at the **same gamma the target
lane was actually carved at**, so it joins/splits at that lane's real granularity rather than a
one-size-fits-all midpoint.

### Why it is a genuine design problem, not a plumbing task

A single local-move call's boundary spans the new symbol's TOP_K highest-weight owned neighbours,
and **those neighbours can belong to different lanes carved at different gammas** (`_split_once`
runs a fresh binary search per subtree, `sgt/lens/tree.py`). CPM resolves a partition at **one**
global resolution. So "use the target lane's gamma" has no clean answer when the boundary is
multi-lane — which is the normal case for a well-connected new symbol. This ambiguity, not the
storage, is why sources 1/2 were deferred.

Storage itself is cheap on the write side (`_split_once` already computes each split's gamma; it
could stash `node["gamma"]`), but durability across clones would need a new `AuthoredFeature` field
(the CRDT the ledger persists lanes through has none today, `sgt/lens/authored.py`), and the read
side still needs a multi-lane-boundary heuristic.

### Impact bound (why the midpoint is defensible)

In the local move, owned neighbours are **fixed** and the new symbol either joins a fixed
community or stays a free singleton. Gamma only moves the *join-vs-stay threshold*: coarser →
likelier to join a neighbour, finer → likelier to seed its own lane. A new symbol with strong
coupling to one lane joins it **regardless of gamma**; gamma only decides *marginal*,
weakly-coupled cases. So the quality gap between the midpoint and a per-lane gamma is bounded to the
margin — and whatever the local move decides is (a) pinned durably and (b) correctable by the user
via `sgt feature move` or an accepted U7 suggestion.

### Options

- **B0 — Accept the midpoint (status quo).** Simple, deterministic, "same scale a recluster centres
  on." Marginal cases may over/under-merge relative to a lane's true granularity.
- **B1 — Record per-lane gamma; use the dominant neighbour lane's gamma, midpoint fallback for a
  multi-lane boundary.** Stash `node["gamma"]` at split; add an `AuthoredFeature.gamma` for cross-
  clone durability; at save, if one lane dominates the boundary's coupling weight use its gamma,
  else fall back to the midpoint. Real complexity (node field + CRDT field + heuristic) for a
  marginal-case gain.
- **B2 — Per-symbol re-derivation.** Binary-search the new symbol's own boundary for target arity.
  Defeats the "bounded, cheap local move" purpose; rejected.

### Recommendation

**Keep B0 (the midpoint) until measurement shows marginal misassignment is a real, recurring user
problem.** The assignment is gamma-robust except at the margin; the multi-lane-boundary ambiguity
means even the "proper" source needs a heuristic (no clean answer); and a marginal miss is durably
pinned and one `feature move` away from corrected. Building the node→CRDT→local-move gamma plumbing
now is complexity spent against a guess about future quality — the YAGNI case. If the U7 suggestion
queue starts frequently proposing `move`s that trace back to a granularity mismatch at save time,
that is the signal to build B1.

---

## Also in this cleanup pass (for the record)

- The vestigial `sgt/cli/inspect.py::_map_for_view` `verb` parameter (all callers passed `"log"`
  after U14) was **removed** — commit `40b8af6`.
- The three `tests/core/test_land.py` "no-trace" failures (`fidelity.json`/`sync_cache.json` writes
  surviving a non-landing land) were **fixed** by extending land's transactional rollback to the
  gitignored local caches `restore_worktree_to` never sees — see `sgt/core/sync/land.py`.
