---
date: 2026-07-01
topic: sgt as an operation log — a patch is a fold, not a record; intent rides on operations, not beside them; two sources of truth (git for content, an op-log for semantics) and everything else is a deterministic fold; the DSL is the primitive layer
status: design / ADR — proposed; refactors the storage model assumed by the effect-log design and the Node-graph store. Supersedes "patch/Node as a mutable record"; keeps the algebra, lens, gates, and symbol scheme of the two 2026-07-01 docs intact.
builds-on:
  - docs/design/2026-07-01-intent-patch-algebra-and-recording-lens.md   # the algebra whose atoms this doc re-homes as ops
  - docs/design/2026-07-01-symbol-identity-scheme.md                     # canonical sym-id: here it's a fold over `remap` ops
  - docs/design/2026-06-30-contracts-over-git-substrate.md               # provides/requires, report-don't-veto gates
  - Fowler, Event Sourcing                                               # state = fold over an op log; snapshots are cache
  - Mimram & Di Giusto, "A Categorical Theory of Patches" (Pijul)        # a patch is a morphism (verb), not a state (noun)
  - Shapiro et al., CRDTs (OR-Set, LWW/MV register)                      # the op-log merges without coordination
author-note: written by Claude as the ontology decision behind the two 2026-07-01 ADRs. [CALL] marks a judgment; [RISK] a failure mode; [Q] an interrogation target left open on purpose. Green-field permission was explicit: cut/refactor the current store freely; aim for the cleanest model, not backward-compat.
---

# sgt as an operation log — patch and footprint are folds, not records

## 0. Thesis in one paragraph

There is **no stored `Patch` struct** and **no `Intent` type beside it**. The semantic history of a
repo is an **append-only, CRDT-mergeable log of typed operations**; content bytes live in git. A
"patch" is a **derived view** — the fold of every operation sharing a `PatchId`, the way a file is
the fold of its edits. `Intent` is not a peer entity: it is the natural-language annotation *carried
by* a content-introducing operation. The DSL is not a front-end over some other data model — **the
DSL operations are the primitives**, and the patch graph, footprints, dependency DAG, symbol map,
and every materialized tree are deterministic folds over exactly two sources of truth: **git
(content)** and **the op-log (semantics)**. This is Event Sourcing + CRDT, applied one altitude
above the current content-effect log.

## 1. What this changes (the cut)

The algebra ADR (2026-07-01) and symbol-id ADR (2026-07-01) are kept whole — three axes,
`provides`/`requires`, three gates, canonical `sym_id`, set-valued branches. This doc changes only
**where those things live**:

- **[CALL] `Patch` is a fold, not a record.** Today's store has a mutable `Node` with fields
  (`intent`, `provides`, `needs`, `status`, edges). We delete the mutable node. `provides`/`requires`
  are re-derived on read from the commits; they are never stored, so they can never be stale and are
  not a field to keep in sync. (This directly retires the live inconsistency where `provides`/`needs`
  exist on `PLANNED` nodes but vanish once a node goes `ACTIVE`.)
- **[CALL] `Intent` collapses into the operation.** "Intent = Patch(es)" was the right instinct: one
  intent may bind several commits, and the *patch identity* bundles them. Intent is one facet of that
  identity — the annotation on a `record`/`relabel` op — not a struct beside `Patch`.
- **[CALL] `distill`/`reconcile` are not primitives.** They are the *recorder*: a process that reads
  a git diff and **emits operations** (`record`, and via the drift gate `remap`). The recording
  direction and the hand-authored direction produce the **same op stream**, so the DSL is at once the
  input language and the printed form of the log.
- **Kept, re-homed:** the append-only log with causal metadata (version vectors), LWW tie-break, and
  the fractional-index statement CRDT (`Slot`/`PosId`) all survive — as the **content** layer. This
  doc lifts a *second* log to the **semantic** layer above them.

## 2. Two sources of truth, everything else derived

```
┌── git ───────────────┐      ┌── op-log ─────────────────────────────┐
│ content bytes         │      │ CRDT set of typed ops (causal vv)      │
│ commits, verbatim      │      │ intent · structure · identity · select │
└───────────────────────┘      └────────────────────────────────────────┘
         │                                     │
         └──────────────────┬──────────────────┘
                            ▼   pure, deterministic folds (no LLM, offline)
   patch-views · footprints · symbol-map · dependency-DAG · selections' trees · blame
```

**[CALL] Only git and the op-log are stored. Nothing else is.** The symbol map is a fold over
`record` (mints) + `remap` (renames) ops. Footprints are a fold over a patch's commits (ast def/use,
resolved through the symbol map). The dependency DAG is a fold over footprints. A branch's tree is a
fold over a selection. Blame is a fold over the content log. If it can be recomputed, it is not
stored — snapshots are cache, subject to the same discipline as the current `materialize()`.

## 3. The primitive vocabulary (the DSL)

Five operation families. This is the **entire logged surface**; everything a user or agent does
reduces to appending these.

| primitive | appends | touches | notes |
|---|---|---|---|
| `record` | mint a `PatchId` binding `intent` ↔ `[commit]` | intent + content pointer | **the atom.** `revises: p` ⇒ refine; `kind: repair` + explicit `deps` ⇒ the LLM integration patch. Footprint & deps are **not** arguments — they're derived from the commits. |
| `relabel` | change a patch's `intent` (gloss / kind / raw-span) | intent only | content frozen; pure intent-layer edit |
| `regroup` | repartition identity | id ↔ commit binding | `decompose` = tombstone(p) + record(p1,p2 `from p`); `merge` = inverse. Provenance edges; **content never moves.** |
| `remap` | carry a `sym_id` across a surface-name change | symbol map | `rename`/`move`; the mechanism that keeps the join key honest (symbol-id ADR) |
| `select` | edit a named Selection's membership + per-patch version pins | selection | branch / toggle / revert / cherry-pick are **all** this |

The op record itself (the log entry) is the only concrete struct:

```text
Op:
  id:      OpId              # opaque, causal (version vector) — for CRDT merge + LWW tie-break
  kind:    record | relabel | regroup | remap | select
  target:  PatchId | SymId | SelectionName
  payload: kind-specific     # record → {intent, commits, revises?, deps?}; remap → {sym, new_surface}; …
  author:  ReplicaId
```

Two things deliberately **not** in the vocabulary:

- **No `revert` primitive.** Revert = a `select` that removes a patch from a Selection; its
  dependents come off by the derived DAG. (Ends the *revert-drops-the-create* lineage —
  `memory/statement-distill-eid-lww.md` — because there is no create/extend ordering to overload.)
- **No `reorder`.** The log's patch set is unordered; only the *derived* dependency topo-sort orders
  the fold. "What if the order were different" is already answered: it is order-independent.

## 4. A patch is a fold

```text
patch(p)  =  fold { ops in log where target == p }
          =  record(p)  ⊕  relabel*  ⊕  provenance(from regroup)  ⊕  pins(from select)
          ──derive──▶  { intent, commits, provides, requires, deps }
```

`provides`/`requires` hold **canonical `sym_id`s** (never surface names), resolved through the
symbol-map fold at read time. `deps` is then:

```text
deps(q) = { p : q.requires ∩ p.provides ≠ ∅  ∨  same-sym write-write }  ∪  q.after
```

Because the view is recomputed, there is no "update the footprint" code path, no staleness, and no
node-mutation API. Reading a patch is the same act as reading a file: fold the ops.

## 5. The derived algebra is sugar + queries, not new primitives

```text
branch b = all − p2          ─┐
revert p                      ├─  expand to `select` ops (membership edits)
cherry-pick p3 onto b         ─┘
merge branch b1 b2  =  b1 ∪ b2   #  OR-Set join — never textually conflicts
reorder                        ─── no-op
impact-of-removing p           ─── query: forward slice-closure over the dep-DAG; logs nothing
recompose(selection)           ─── the fold: order patches' commits by derived deps, concat verbatim spans
```

**[CALL] The kernel is 5 logged primitives; every "version-control verb" is either one of them,
sugar over `select`, a no-op, or a read.** That the surface is large but the kernel is tiny is the
main evidence the ontology is the right shape.

## 6. Merge semantics — the op-log is a CRDT; conflict is bounded, not eliminated

Three stored CRDTs; merge each, then re-derive everything (§2):

- **op-log** — an **OR-Set of ops** with causal version vectors. Ops on distinct targets commute;
  two ops on the same target (e.g. two `relabel`s of `p`) resolve by **LWW** on the causal counter.
  `select` membership is an OR-Set (add-wins with causal remove).
- **symbol map** — per-`sym_id` register. **[CALL] MV (multi-value), not LWW:** a concurrent rename
  of the same symbol surfaces as a drift prompt rather than silently picking a winner.
- **content** — the existing `PosId` + `Slot` sequence CRDT; `Slot.source` is verbatim bytes.

**[CALL] This makes "no conflict on the patch side" true — and shows exactly where it stops.**
Recompose runs over the content CRDT (order slots by `PosId`, concat verbatim `source`), **not** over
`git merge`/`git apply` of the selected commits — so it is conflict-free *and* byte-faithful at once.
(`Slot.source` *is* the "verbatim span-log" of `memory/git-substrate-shelved-span-log.md`.) Git holds
commits for durability and interop; git is **not** on the recompose hot path.

Conflict that CRDTs cannot remove, in three rungs:

| rung | conflict | detector | CRDT removes it? |
|---|---|---|---|
| textual | two edits, same region | content CRDT / `PosId` | **yes** |
| linkage | in-force `requires`, no in-force `provides` | interface gate — set logic over footprints | it's a query |
| **semantic** | footprints commute, tree links, behavior/type still wrong | build/type/test oracle | **no — this is the thesis** |

The three gates of the algebra ADR are three points on the **precision/cost curve of program-
dependence analysis**: name-level footprint (cheap, sound-for-linkage) → signature/type (mid) →
build+test oracle (ground truth). The LLM `repair` fires only on the gap between the best static gate
and the oracle. `depends(q,p) ⟺ q.requires ∩ p.provides ≠ ∅` is a symbol-granularity, **over-
approximating** backward slice: it never misses a real dependency (safe) and sometimes over-includes
(a spurious dep just comes off with the toggle). Precision is a tunable ladder; start cheap, let the
oracle catch the residue.

## 7. The concrete cut on today's store

Green-field permission taken literally — what changes in the current codebase:

- **Delete** the mutable `Node`/patch record as a source of truth (`sgt/store/`). It becomes a
  derived view (`patch(p)` fold). `NodeStatus` (`ACTIVE`/`PLANNED`/`QUARANTINED`) becomes a *derived
  attribute*: `PLANNED` ⇔ `commits == []`; drift/quarantine ⇔ a gate result, not stored state.
- **Promote** the effect log to a semantic op-log: entries are `record`/`relabel`/`regroup`/`remap`/
  `select`, not raw content effects. The content effects (`add_def`, `INSERT_STMT`, …) become the
  *derivation* of a `record`'s footprint, computed by the recorder from the commit — not top-level
  log entries.
- **Keep** verbatim: `PosId`/`Slot`/`between()` (content CRDT), version vectors + `order_key` (causal
  merge), `attribute.py` (blame is already a fold), and one recompose path shared with blame so they
  can't disagree.
- **`sgt/api.py`** stays the single projection, but every view is now explicitly a fold over
  (git, op-log). No surface stores its own shape (unchanged invariant, now enforced by construction).

## 8. The recorder — bottom-up construction is clustering + labeling

`distill` emits `record` ops. Building a patch from a diff is two guesses, and **only these two are
non-derivable** (everything else falls out):

1. **Partition** effects into patch identities. Seed by git commit boundaries; refine by modularity
   maximization on the effect-level def/use graph (max intra-patch cohesion, min inter-patch
   coupling). In the live path where the agent used `plan`/`checkpoint --fulfills`, the partition is
   **given**, not guessed. `regroup` fixes a bad partition after the fact — content never moves.
2. **Label** each cluster. LLM → `gloss`; `raw` = commit message if present; degrades offline to
   `"changed {provides}"`.

`rename`/`move` detection is a special case: the existing body-similarity heuristic proposes; the
**drift gate confirms**; confirmation *is* the `remap` op (symbol-id ADR path B). **[CALL] The whole
inference is safe because it is revisable, not because it is right** — false-negative degrades
visibly (delete+create), false-positive corrupts the join silently, so every guess biases toward
asking.

## 9. Risks

- **[RISK] Recompute cost.** If every read re-folds the whole op-log, large repos get slow. Needs a
  snapshot/cache layer with the same "cache, not truth" discipline as `materialize()`. Measure before
  optimizing; the fold is embarrassingly incremental (memoize per `PatchId`).
- **[RISK] Op-log LWW granularity.** Two concurrent `relabel`s LWW cleanly; two concurrent `regroup`s
  of overlapping patches may not have a well-defined join. `regroup` may need to be a coarse,
  causally-serialized op rather than a free CRDT op. Interrogate before building §3.
- **[RISK] `record` provenance under `decompose`.** Splitting one patch into two must preserve enough
  provenance that a later `merge` or a `select` referencing the old id resolves. Provenance edges are
  asserted here, not yet specified.
- **[RISK] The cut is large.** Deleting the `Node` store touches every surface. The migration is only
  safe because reads are folds — but the intermediate state (old Node store + new op-log) must not
  double-write. Sequence the cut so the op-log is authoritative before `Node` is deleted.

## 10. Decision

**Proposed.** One op-log as the semantic source of truth; git as content truth; patch, footprint,
dep-DAG, symbol map, and trees all derived folds; the DSL's five primitives (`record`, `relabel`,
`regroup`, `remap`, `select`) are the only logged operations; every other verb is sugar, a no-op, or
a query. This refactors the storage model of the effect-log/`Node` design while preserving the
algebra, lens, gates, and symbol scheme of the two 2026-07-01 ADRs. Additive-then-subtractive: stand
up the op-log and make it authoritative, then delete the mutable `Node` record.

## Open questions — interrogation targets

- **[Q] Selection: shared history or local scratch?** Is a branch a `select`-op in the shared,
  mergeable op-log (my default — "which branches exist" is collaborative state), or an ephemeral
  per-user view that never enters shared history? Changes the collaboration model materially.
- **[Q] Decision→commit cardinality.** This ontology *forces* the answer: "one patch = many commits,
  derived by the recorder, repartitioned by `regroup`." Confirm — `regroup` is well-defined only under
  this policy (algebra ADR §8.5.5).
- **[Q] Is `regroup` one primitive or two?** Presented as tombstone + record with provenance. If a
  free CRDT op, its concurrent-merge semantics need specifying (§9 RISK). If causally serialized,
  say so.
- **[Q] Where does footprint derivation run — read-time or record-time?** Read-time keeps "nothing
  stored" pure but costs on every query; record-time caches on the op but reintroduces a stored,
  potentially-stale field. Bias toward read-time + memoized snapshot.
- **[Q] Does `record` store the *distilled effects* or only the commit SHA?** Storing only the SHA is
  purest (effects re-derive from git); storing effects caches the ast walk but couples the op-log to a
  parser version. Leaning SHA-only with an effect cache keyed by (SHA, parser-version).
- **[Q] Status as a derived attribute.** Confirm `PLANNED`/quarantined can be *computed* (empty
  commits / gate result) with no stored status field — or find the case that forces stored status.
