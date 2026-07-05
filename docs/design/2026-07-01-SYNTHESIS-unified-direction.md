---
date: 2026-07-01
topic: the unified direction for sgt — reconciles the 6/29→7/01 design arc into one theory, one refactor ladder, and one measurable crux. This is the canonical entry point; the three backing docs hold the proofs, the plan, and the audit.
status: design / ADR — PROPOSED as the reconciling decision over the whole corpus. Supersedes the contradictions between the five prior ADRs; keeps their machinery where the math survives.
supersedes-contradictions:
  - "recompose = git cherry-pick" (2026-06-29, 2026-06-30 §5)            # refuted: RISK-2 is fatal
  - "recompose = concat flat verbatim spans, conflict-free + byte-faithful" (2026-07-01 op-log §6)  # refuted: subsets unparseable
  - "footprint never misses a real dependency / sound over-approx" (2026-07-01 op-log §6)            # refuted: unsound
  - "the crux is conflict rate" (2026-06-30 §10.1 spike)                 # demoted: the crux is bounded-repair reach
backing-docs:
  - docs/design/2026-07-01-sgt-formal-theory-algebra-dsl.md              # THE THEORY: object model, algebra + proofs, recompose theorem, 5 primitives, workflow table, lens
  - docs/plans/2026-07-01-001-refactor-oplog-fallback-ladder.md          # THE PLAN: current→target map, 7 phases as a fallback ladder, invariants, risk register
  - docs/design/2026-07-01-adversarial-critique-and-verification.md      # THE AUDIT: must-resolve challenges + round-2 verification (GO-WITH-CONDITIONS, C1–C7 closed)
prior-corpus:
  - docs/design/2026-06-29-git-as-substrate.md
  - docs/design/2026-06-30-contracts-over-git-substrate.md
  - docs/design/2026-07-01-intent-patch-algebra-and-recording-lens.md
  - docs/design/2026-07-01-operation-log-ontology.md
  - docs/design/2026-07-01-symbol-identity-scheme.md
author-note: written by Claude as the synthesis of a three-role process — a PL/formal designer (theory + proofs), a staff engineer (refactor plan), and an adversarial research supervisor (audit) — run in parallel with two convergence rounds. Where the three converged, it is stated as decided; where the math forced an honest "false-in-general", it is bounded, not hidden.
---

# sgt: the unified direction

## 0. What this document is

The 6/29→7/01 arc produced five ADRs reaching for the same object from different angles. They contain
three live contradictions and three load-bearing claims asserted without proof. This doc is the
reconciliation: it states the one theory the corpus was converging toward, the one refactor path, and
the one number that decides whether the ambitious half of the vision is real. It is deliberately short —
**the proofs live in the theory doc, the phases in the plan doc, the challenges in the audit doc.** Read
those three for depth; read this for the decision.

## 1. The thesis, in one paragraph

sgt is an **asymmetric lens** between *intent* (a CRDT op-log of typed operations) and *content*
(byte-faithful git commits). The **primary, total, deterministic** direction is **`get` — recording**:
distilling real code into intent, so sgt *reflects what the code actually changed*, not just what a
prompt asked for. The reverse (`put` — planning, a coding agent turning intent into code) is
non-deterministic and is never performed by sgt. A **codebase** is a third thing: the **fold** of a
*selection* (a set of patch identities with version pins) over content. **Branches are sets; revert is
set difference; reorder is a no-op.** The metadata converges without coordination (Strong Eventual
Consistency over a join-semilattice). The codebase fold is deterministic and byte-faithful. **sgt
authors no code** — the LLM is quarantined to decomposing a prompt into ops (front, optional) and, only
when an oracle catches a semantic break, a fenced seam-repair (back). This is exactly the owner's ask:
recording-primary, commutative-and-reversible where the math allows, collaborative, and grounded.

## 2. The three corrections that make it honest

The corpus's elegance hid three errors. The math forces all three; the design survives all three.

1. **Recompose is neither git cherry-pick nor flat-span concatenation.** Cherry-pick conflicts whenever
   two patches touch one function (RISK-2 — this shelved the 6/29 direction). Flat verbatim spans
   concatenated in fractional-index order are convergent and byte-faithful **but their subsets do not
   parse** (a `try:` selected without its `except:` — proof in theory §C.3). The only substrate that
   earns "conflict-free + byte-faithful + parseable-subset" is a **structured content CRDT** with
   block-integrity (a tree of verbatim-leaf atoms, mandatory bodies kept non-empty). That object is the
   hardest unbuilt piece and is therefore **gated behind measurement**, never assumed.

2. **The dependency predicate is unsound, not a "sound over-approximation."** Name-level def/use cannot
   see dynamic dispatch, decorators, monkeypatching, registries, reflection, or config/schema coupling,
   so `depends` **misses real edges** and `commute` has false positives (theory §B.4). The consequence
   is made load-bearing rather than hidden: **the build/test oracle is the sole ground truth, no gate
   ever vetoes, and the oracle runs on every materialized selection.** The `after` escape hatch lets a
   human declare an edge the analyzer can't see.

3. **"Collaboration without conflict" is true only for the metadata.** Concurrent edits to *one symbol*
   provably cannot be merged conflict-free (3-way-merge impossibility, theory §B.5). sgt's honest
   promise: **footprint-disjoint work merges with zero conflict; overlapping work surfaces an explicit
   versioned divergence** — never a silent LWW winner, never a garbage interleave. That is the
   mathematically maximal claim, and no surface may say "collaboration without conflict" unqualified.

## 3. The kernel — five primitives, everything else derived

A patch is **a fold, not a record.** The semantic source of truth is an append-only op-log; content is
git; every other artifact (patch views, footprints, symbol map, dependency DAG, per-selection trees,
blame) is a deterministic fold over those two. Five logged operations are the entire surface:

| primitive | does | why irreducible |
|---|---|---|
| `record` | bind intent ↔ byte-faithful commit(s); mint ids | the only content-introducing op |
| `relabel` | edit intent, content frozen | pure `get`-side annotation; no footprint |
| `regroup` | repartition patch↔commit (decompose/merge); content never moves | identity axis, not in-force |
| `remap` | carry `sym_id`s across name/structure change — **a provenance relation** `R ⊆ Σ×Σ`: 1:1 rename/move, 1:n split, n:1 inline | the one fact content can't express (content shows delete+create) |
| `select` | edit a selection's membership + version pins | branch/revert/cherry-pick/switch are **all** this |

`branch = set`, `revert = set difference`, `cherry-pick = set union`, `reorder = no-op`, `merge = OR-Set
join`. The generalization of `remap` from a bijection to a **relation** is the clever move: it absorbs
symbol-split/inline (which the symbol-id ADR had deferred into a hole) **without a sixth primitive**.
Status (`PLANNED`/`QUARANTINED`) is derived, never stored.

## 4. The fallback ladder — a provable core, an ambitious layer, an honest degrade

The single most important structural decision: **this is a ladder, not a bet.** The rung that *ships
value on its own* depends on nothing unbuilt; the rung that *justifies sgt over git+labels* is gated on
the hardest unbuilt piece and a measurement.

| rung | what ships | recompose substrate | depends on | status |
|---|---|---|---|---|
| **0 — Recording lens** | `get`/distill + drift gate + `sym_id`-stable blame + labeled patch-DAG; **byte-faithful HEAD reflection** | *none* — never folds a subset | only a local `ast.unparse`→parse-and-slice fix | **always shippable; delivers the owner's #1 ask** |
| **1 — Structured-CRDT composition** | toggle / cherry-pick / version-select / branch (the "rows that justify sgt") | structured tree CRDT, block-integrity, verbatim leaves | building the CRDT; passing **M1** and **M2** | **the product bet — gated** |
| **2 — Coarse degrade** | composition at function granularity only | git 3-way / rebase-replay | git already holds bytes | **honest fallback if rung 1 is unbuildable or fails M1** |

Rung 0 is safe *regardless of the recompose crisis* because a pure recording lens never materializes a
selection that isn't already on disk — so subset-parseability (the whole Tension-1 problem) does not
arise. This is the researcher's fallback, and it is provably sound today.

## 5. The crux — one number, pre-committed, measured *before* the expensive work

Because rung-1 recompose is a convergent fold, **conflict rate is the wrong kill metric** (the 6/30
spike measures it — it measures the wrong thing, and wires no repair hook). The real crux:

> **Can an arbitrary in-force subset recompose into a byte-faithful, parseable tree, and be driven to
> building-and-tests-passing by a *bounded, fenced* seam-repair — and how often?**

Three pre-committed ceilings, each with a named retraction (theory §G):

- **M1 — parseable-subset rate ≥ 0.95.** Predicted ≈ 1.0 *by construction* under block-integrity.
  **If < 0.95:** rung 1 is not viable → drop to rung 2 (function-level) or rung 0.
- **M2 — bounded-repair reach ≥ 0.80** (of parseable-but-oracle-failing selections, the fraction a
  seam-bounded repair drives to green within k≤3 attempts). **This is the single kill metric for the
  composition product. If < 0.80:** withdraw "the algebra is made total by repair" — composition ships
  only for the already-green subset, no repair promise.
- **M3 — footprint-miss rate ≤ 0.10** (toggles the interface gate blessed but the build breaks —
  measures `depends` unsoundness). **If > 0.10:** restrict toggle/cherry-pick to leaf-or-disjoint
  patches; always run the oracle before surfacing a composed tree.

The repair fence is **not** "no net-new top-level defs" (rejected — both too strict and too loose).
It is a conjunction of four *checkable* properties: **seam-locality** (diff touches only orphaned/
conflict regions), **sandboxed provides** (`⊆ in-force ∪ integration.*`), **retirement-on-toggle**
(removing either reconciled side auto-retires the repair), and **oracle red→green** (the only hard
guarantee). This bounds blast radius and keeps repairs attributable and reversible; it does not pretend
to prove "the LLM wrote no logic" (undecidable) — that is the honest restatement of "sgt never authors
code" at the seam.

## 6. The refactor — much of rung 0 already exists in disguise

The plan doc's grounding win: `build_decisions` (decisions/store.py:121) is **already a pure fold** of
the log into patch views, and blame already shares the `materialize()` path. So rung 0 is mostly
"lift a *semantic* op-log above today's *content*-effect log, flip reads onto the fold, and delete the
mutable `Node` store" — not a rewrite. Seven phases, additive-then-subtractive, no released
double-write state:

- **P0** golden-master harness + contract hygiene (the color invariant is *already broken* — the third
  mirror `color.ts` is untested and CLAUDE.md points at a `graph.js` that doesn't exist; P0 fixes it).
- **P1** mint canonical `sym_id`, dual-keyed; `remap` as a relation; batch-confirm drift UX (one prompt
  per refactor, not per call site — killing the mass-rename prompt-storm).
- **P2** byte-faithful HEAD via verbatim splice — **retire the `ast.unparse` formatting-loss bug**
  (model.py:479-482), the highest-value fix, recommended a corpus of doc-days ago and never built.
- **P3** report-don't-veto gates; drift gate first-class; build oracle as the backstop for the unsound
  `depends`.
- **P4** shadow-write the semantic op-log; prove `fold_ops ≡ build_decisions` (with randomized-history
  stress and a `project.graph`-read-free constraint — audit conditions C5, C6).
- **P5** flip reads to the fold, delete the `Node` store. **◀ RUNG 0 ENDS HERE** — a complete,
  shippable product.
- **P6** structured content CRDT + subset recompose (rung 1), entry-gated on the M1 harness, exit-gated
  on M1≈1 ∧ M2≥0.80, with the git-3-way degrade (rung 2) as an explicit branch. **P7** collaboration.

Blame's INV-3 status is a *stated, gated* consequence of the P6 substrate choice: the structured CRDT
preserves blame-as-a-fold; the git-3-way degrade makes it text-derived and function-granular (audit C7).

## 7. What is decided vs. what is measured

**Decided (the math is in):**
- Recording is primary; sgt is a lens; recording is total and deterministic-given-pins (the four
  advisory knobs — parser, θ, partition, LLM label — only *propose*; a logged op *decides*; the drift
  gate *confirms*).
- Five primitives; `remap` as a provenance relation; branch=set, revert=set-diff, reorder=no-op.
- Metadata converges (SEC) with LWW keyed by the total order `(Lamport, ReplicaId)`; `regroup` and
  ambiguous `remap` are causally serialized, not force-joined.
- Recompose is byte-faithful and parseable **iff** the content CRDT is structured; git is off the
  recompose hot path.
- The rung-0 recording lens ships value on its own and depends on nothing unbuilt.

**Measured (the bets, in priority order):**
- **M2 bounded-repair reach** — the product crux. The next executable step must *wire the repair hook*
  and measure this on real semantic breaks. The current spike does not.
- **M1 well-bracketedness** — whether real selections are structurally clean cuts (gates rung-1
  viability).
- **M3 footprint-miss rate** — how aggressive toggles may safely be.

## 8. The immediate next move

Build **rung 0 (P0–P2)** — it is pure upside and unblocks the owner's #1 ask immediately: a
byte-faithful recorder that reflects real code changes, with stable-across-rename blame. In parallel,
stand up the **M1/M2 measurement harness** (amending the 6/30 spike to measure repair reach with a real
repair hook, not conflict rate) so the rung-1 gate has data before any structured-CRDT code is written.
Do **not** build the structured content CRDT, the collaboration formalism, or `reduce()` before M1/M2
are in — those are the audit's named rabbit-holes.

If M2 < 0.80 or M1 < 0.95, **sgt still ships as the rung-0 recording lens** — and that is a good product,
not a consolation prize. The ambitious composition algebra is a gated extension of a provable core, which
is the shape a robust system should have.

## 9. Open questions carried forward (small, bounded)

- Drift-resolution *policy* (auto-split vs. relabel vs. ask) — a UX call, metric well-defined (theory §F).
- Trailer survival across rebase/amend (RISK-4, punted three docs running) — a concrete hook, needed
  before production; not research.
- Fold recompute cost on large repos — memoize per `PatchId`; measure before optimizing.
- Non-Python: the canonical `sym_id` tier is already language-agnostic; the surface locator and the
  per-grammar block-integrity rules are not, and gate cross-language composition.

---

*Three docs back this one: the [formal theory](2026-07-01-sgt-formal-theory-algebra-dsl.md) (proofs),
the [refactor ladder](../plans/2026-07-01-001-refactor-oplog-fallback-ladder.md) (phases), and the
[adversarial audit](2026-07-01-adversarial-critique-and-verification.md) (challenges + verification).
This document is their reconciliation and the decision.*
