---
date: 2026-07-01
topic: the formal theory, algebra, and DSL for sgt — an asymmetric lens whose primary (get) direction records code into an op-log, whose branches are selections over a join-semilattice, and whose codebase is a fold over a *structured* content CRDT. States each law with a proof, a proof-sketch, a conjecture, or an honest "false-in-general, bounded by X".
status: design / theory — proposed as the formal foundation the four prior ADRs (git-substrate, contracts, intent-patch, op-log-ontology, symbol-identity) were reaching toward. Reframes and, in three places, formally *corrects* them.
builds-on:
  - docs/design/2026-07-01-intent-patch-algebra-and-recording-lens.md   # the algebra + lens framing this formalizes
  - docs/design/2026-07-01-operation-log-ontology.md                    # the 5-primitive op-log; this proves/bounds its central claim
  - docs/design/2026-07-01-symbol-identity-scheme.md                    # the join key the algebra rests on
  - docs/design/2026-06-30-contracts-over-git-substrate.md              # report-don't-veto gates, two altitudes
  - docs/design/2026-06-29-git-as-substrate.md                          # git-holds-content; recompose≠cherry-pick (refuted here)
  - Foster, Greenwald, Moore, Pierce, Schmitt, "Combinators for Bidirectional Tree Transformations" (asymmetric lenses; get/put + round-trip laws)
  - Shapiro, Preguiça, Baquero, Zawirski, "Conflict-free Replicated Data Types" (SEC = commutative/associative/idempotent join over a join-semilattice)
  - Weiss, Urso, Molli, "Logoot" / Roh et al. "RGA" (sequence CRDTs; convergence of a densely-indexed sequence)
  - Mimram & Di Giusto, "A Categorical Theory of Patches" (stable vertex identity ⇒ commutation; conflict-as-state)
  - Batory, Sarvela, Rauschmayer, feature algebra / AHEAD (program = composition; interaction deltas)
  - Weiser, "Program Slicing" (the backward slice = the sound dependency notion the footprint approximates)
author-note: written by Claude as PL/formal-systems designer. Every claimed law is tagged [PROVED] / [SKETCH] / [CONJECTURE] / [FALSE-IN-GENERAL — bounded by X]. I have refuted or corrected the corpus where the math demands it; those points are marked [CORRECTION]. This doc is written to be adversarially audited — the holes are named, not hidden.
---

# The PL theory, algebra, and DSL of sgt

## 0. The one-paragraph thesis, stated formally

sgt is an **asymmetric lens** between an *intent* space `I` (a CRDT op-log of typed operations) and a
*content* space `C` (byte-faithful git commits). The **primary, total, deterministic** direction is
`get : C → I` — *recording*: distilling real code into intent. The **partial, non-deterministic**
direction is `put : I × C ⇀ C` — *planning*: a coding agent turns intent into code. A **codebase** is
neither `I` nor `C` but a third object: the **fold** `⟦·⟧ : Sel → C` of a *selection* `Sel` (a set of
patch identities with version pins) over a **structured content CRDT**. Branches are selections;
revert is set difference; reorder is a no-op. The metadata (`I`, the symbol map, selections) is a
product of join-semilattices, so it converges without coordination (Strong Eventual Consistency).
The codebase fold is deterministic and byte-faithful, and — *this is the load-bearing theorem* — it
is **parseable for every selection iff the content CRDT is structured (block-integrity-preserving)**;
semantic validity of a selection is not decided by the fold but reported by a build oracle, and the
residue is the sole domain of a bounded LLM repair. sgt authors no code.

Three corrections to the corpus, proven below:

- **[CORRECTION-1]** Recompose is **not** git cherry-pick (refutes 6/29; RISK-2 is fatal) **and** the
  op-log doc's "concatenate verbatim spans in fractional-index order" is **only conflict-free and
  byte-faithful for the recorded set — it is NOT parseable for an arbitrary selection over a *flat*
  span sequence.** Subset-parseability requires a *structured* (tree/block-integrity) CRDT. §C.
- **[CORRECTION-2]** "Footprint is a sound over-approximating backward slice — never misses a real
  dependency" (op-log §6) is **[FALSE-IN-GENERAL].** It is sound only w.r.t. the *static name-level*
  dependency graph, which is itself an under-approximation of true (dynamic) dependence. The build
  oracle is the only ground truth. §B.4.
- **[CORRECTION-3]** Same-symbol concurrent edits are **fundamentally not conflict-free.** The
  conflict-free-collaboration guarantee holds *exactly* on footprint-disjoint work; concurrent edits
  to one symbol are represented as explicit divergent versions, never silently interleaved (which
  yields convergent garbage) nor LWW-collapsed (which drops a side). §B.5.

---

## 0.5 LOCKED DECISIONS (round 2 — these are final; §§A–G are the derivation)

Five decisions the refactor-planner is blocked on, plus the substrate ladder and the crux. **These
override any contradictory statement in the five prior ADRs.**

### L1 — The substrate is a FALLBACK LADDER, not a bet. [LOCKED]

Composition is **gated behind measurement**; the recording core is **always shippable**. Three rungs:

| rung | what ships | recompose substrate | composition promise | preconditions |
|---|---|---|---|---|
| **0 — Recording lens** *(always ships; the floor)* | `get`/distill + drift gate + semantic blame + labeled patch-DAG | **none** — the tree on disk is never re-folded from a subset | *none* (annotate reality only) | byte-faithful **get** (parse-and-slice); does **not** need byte-faithful recompose |
| **1 — Structured-CRDT composition** *(the product bet)* | rung 0 + toggle/cherry-pick/version-select/branch (C1–C5) | **structured tree CRDT, verbatim leaves, block-integrity** (§A.3), per-language via tree-sitter | conflict-free + byte-faithful + **parseable subsets** (§C.3 positive) | build the structured CRDT; **replace `ast.unparse`** (model.py:479-482); pass **M1** (§G) |
| **2 — Coarse degrade** *(honest fallback if rung 1 is unbuildable or fails M1)* | rung 0 + composition at **function/top-level granularity only** | git 3-way / rebase-style replay dropping OFF patches | **best-effort, not conflict-free** — conflicts are *reported*, routed to repair or "cannot compose cleanly" | git already holds bytes; RISK-2 conflicts accepted as reports |

**Why rung 0 is safe regardless of the recompose crisis:** a pure recording lens **never materializes a
selection that is not already on disk.** Blame maps real lines→symbols→patches; drift compares
footprints; the graph labels. No fold of a *subset* is ever produced, so subset-parseability (the whole
Tension-1 problem) **does not arise at rung 0.** This is the researcher's fallback, and it is provably
sound today. The current `ast.unparse` round-trip (model.py:479-482) still must be replaced with
parse-and-slice for `get` to be byte-faithful, but that is a local fix, not the structured CRDT.

**The verdict on the two contradicting 7/01 docs:** flat verbatim `Slot`/`PosId` spans are **FALSE for
composition** (proven §C.3 negative — `try:` without `except:`); git-3-way is the **degrade (rung 2)**,
not the primary. Neither prior doc is right as written. Rung 1's structured tree CRDT is the only
substrate that earns the "conflict-free + byte-faithful + parseable" claim, and it is **gated, not
assumed.**

### L2 — (a) Content recompose substrate. [LOCKED]

**Not flat spans** (FALSE, §C.3). **Not git cherry-pick as primary** (RISK-2 fatal). At **rung 1**:
a **structured tree CRDT with verbatim byte-span leaves and block-integrity** (§A.3). At **rung 2**:
git 3-way at function granularity. At **rung 0**: there is *no* recompose substrate — reject the
question, we only annotate the real tree.

### L3 — (b) `select`/branch scope. [LOCKED]

**Named branches live in the shared, mergeable op-log** (a `select` op; membership = OR-Set, version
pin = LWW/MV register — "which branches exist" is collaborative state). **Unnamed what-if selections
are ephemeral, per-user, and never logged** — they exist only to preview a fold (workflow W3) and are
promoted to shared state *only* by an explicit `branch <name>`. This gives the what-if showcase a cheap
local scratch while keeping collaboration convergent (§B.1) over the named branches.

### L4 — (c) decision→commit cardinality. [LOCKED]

**Many commits per patch, one patch per intent.** `regroup` reconciles a bad partition after the fact;
content never moves. This is *forced* — `regroup` is well-defined only under this policy — and it ranks
**above DSL syntax**. Corollary: reformatting and an unrelated bug-fix bundled in one editor session
should land as **separate commits**, each its own patch; the recorder's initial guess is corrected by
`regroup`/`decompose` (both logged, hence deterministic — §G).

### L5 — The crux is M2 (bounded-repair reach), and the current spike measures the wrong thing. [LOCKED]

Because recompose (rung 1) is a **convergent fold, conflict rate is the wrong kill metric.** The
executable plan must (i) **wire the repair hook** and (ii) measure **M2** (§G) on real *semantic*
breaks — not conflict-rate + false-green with no repair hook. Kill criteria are pre-committed numeric
ceilings on **M1, M2, and the footprint-miss rate** (§G), each with an explicit *retraction*.

---

## A. The formal object model

### A.1 Carrier sets

| symbol | name | definition |
|---|---|---|
| `Σ` | **symbols** | the set of canonical, minted, immutable symbol ids `sym_*` (symbol-identity ADR). A symbol is an *identity*, not a name and not a body. |
| `N : Σ ⇀ Name` | **surface map** | partial function from symbol id to its *current* surface name `file::qualname`. Mutable; not an identity. |
| `B` | **byte-spans** | verbatim source fragments (the atoms of content). |
| `A = Σ⊥ × P × B` | **content atoms** | an atom `α = (owner, pos, bytes)`: an optional owning symbol, a **dense position id** `pos ∈ P` (fractional index, `P` a dense total order — Logoot/LSEQ), verbatim bytes. |
| `T` | **content states** | a *structured* content CRDT value: a forest of atoms (see A.3). git commits are the durable serialization of `T`. |
| `Π` | **patch ids** | minted, opaque `p_*`, in a commit trailer. |
| `Ω` | **ops** | the five typed operations (§D). Each carries `OpId` = an opaque id + a causal version vector. |
| `L ⊆ Ω` | **op-log** | a set of ops (an OR-Set). *The semantic source of truth.* |
| `Sel` | **selections** | `Sel = (M, V)`: `M ⊆ Π` an OR-Set of in-force patch ids; `V : Π ⇀ Ver` a per-patch version pin (LWW/MV register). A *branch* is a named `Sel`. |

Two sources of truth only: **git** (holds `T` byte-faithfully) and **the op-log `L`** (holds `I`).
Everything else — patch views, footprints, the symbol map `N`, the dependency DAG, and every
codebase — is a **deterministic fold** over `(git, L)`.

### A.2 Patches, footprints, dependency (as folds, not records)

A **patch** is the fold of all ops sharing a `PatchId`:

```
patch(p) = fold { ω ∈ L : target(ω) = p }
         ⟶ { intent, commits ⊆ T, provides ⊆ Σ, requires ⊆ Σ, deps ⊆ Π }
```

- `provides(p) = { canon(d) : d ∈ defs(commits(p)) }`   — canonical ids of symbols defined.
- `requires(p) = { canon(r) : r ∈ refs(commits(p)) } \ provides(p)`   — referenced-not-defined.
- `footprint(p) = provides(p) ∪ requires(p)`.

`canon` resolves a surface name to a canonical id through the *symbol-map fold* (below), so footprints
are **sets of canonical ids and never see a name**. This is what makes rename/move edge-preserving.

**Symbol map as a fold.** `N = fold` over `record` (mints ids) and `remap` (carries ids across
name/structure changes). Per-`sym_id` it is a **multi-value (MV) register**: a concurrent rename of
one symbol surfaces two candidate names (a drift prompt), never a silent LWW winner. [CORRECTION-3
in miniature — identity conflicts are surfaced, not collapsed.]

**Dependency** is a *derived* relation:

```
depends(q, p)  ⟺  ( requires(q) ∩ provides(p) ≠ ∅ )      -- use-after-def
              ∨  ( provides(q) ∩ provides(p) ≠ ∅ )      -- same-symbol write-write
              ∨  ( p ∈ after(q) )                       -- declared escape hatch
```

`depends` induces a directed graph `D` on `Π`. Where `D` has a cycle (mutual recursion split across
patches), the strongly-connected component is an **atomic bundle**: co-selected or co-excluded. Let
`⟳(S)` = the closure of `S` under (a) `depends` and (b) SCC membership.

### A.3 The structured content CRDT (the correction that makes recompose work)

**[CORRECTION-1, structural core.]** The corpus proposes a flat sequence CRDT: atoms carry a `PosId`,
recompose concatenates in `PosId` order. A flat sequence converges (SEC) but **does not respect the
grammar's bracketing** — see §C for the counterexample. We therefore define content as a **tree
CRDT with verbatim leaves and a block-integrity constraint**:

```
Node  ::=  Unit(header_span, sym_id, children : OrderedSeq⟨Node⟩)     -- a def/class/block
        |  Leaf(span, owner : sym_id?)                                -- a statement / line
OrderedSeq  =  a Logoot/RGA sequence of children, keyed by dense PosId
```

- **Siblings** within a `children` sequence are ordered by dense `PosId` — that layer is exactly the
  proven sequence CRDT.
- **Block-integrity constraint (BI):** a child atom may not be *selected* independently of the `Unit`
  header that grammatically encloses it, and a `Unit` whose grammar mandates a non-empty body (e.g. a
  Python `def`/`if`/`try`) carries a synthetic `pass`-leaf so that removing all *optional* children
  never yields an unparseable empty body. BI is a property of the *selection*, enforced by §D's
  `select` closing every membership under `⟳` **and** under syntactic containment.

This is the single most important design commitment in the doc and the one the prior corpus omits.

---

## B. The algebra and its laws

Notation: `⟦S⟧` is the codebase fold of selection `S` (defined in §C). `p ⊔ q` is CRDT join.

### B.1 Merge convergence (Strong Eventual Consistency) — [PROVED]

**Claim.** The semantic state `(L, N, Sel)` converges: any two replicas that have delivered the same
set of ops compute equal state, independent of delivery order or duplication.

**Proof.** Each component is a join-semilattice:
- `L` is an **OR-Set** of ops keyed by `OpId`; join = union with causal tombstones. OR-Set join is
  commutative, associative, idempotent (Shapiro et al.). ∎-component. **[C2 — `regroup` exception]:**
  `regroup` is the one op whose two concurrent instances over *overlapping* patch sets have no
  well-defined lattice join (op-log §9 RISK). It is therefore **causally serialized**, not a free CRDT
  op: a `regroup` carries the causal version vector it observed, and two concurrent `regroup`s touching
  overlapping targets are ordered by `(Lamport counter, ReplicaId)` with the loser re-based onto the
  winner's partition (or surfaced as a drift prompt when re-basing is ambiguous). SEC below is
  **[PROVED] for `record`/`relabel`/`remap`(§C3-rule)/`select`; `regroup` converges by serialization,
  not by a semilattice join.**
- `N` is a product of per-`sym_id` **MV-registers**; MV-register join = version-vector-dominance union.
  Commutative/associative/idempotent. ∎-component.
- `Sel.M` is an **OR-Set**; `Sel.V` is a product of **LWW-registers keyed by the total order
  `(Lamport counter, ReplicaId)`** (lexicographic). The `ReplicaId` tie-break is load-bearing: a
  Lamport clock is only a *partial* order, so two concurrent writes can carry **equal** counters;
  without the `ReplicaId` tie-break LWW-by-counter-alone is order-dependent (not commutative). With
  the total order, LWW join is c/a/i. `relabel`'s intent register uses the same total order.
  Both are semilattices. ∎-component.

A finite product of join-semilattices is a join-semilattice; on a join-semilattice the CRDT join is
c/a/i, which is exactly the SEC precondition (Shapiro et al., Thm.). Hence the composite state
converges. ∎

**Bound.** SEC is about the *metadata* `(L,N,Sel)`. It says nothing about whether `⟦S⟧` builds — that
is §C.4. Convergence ≠ correctness.

### B.2 Commutativity of footprint-disjoint patches — [PROVED, w.r.t. static footprint]

**Claim.** If `footprint(p) ∩ footprint(q) = ∅` then for any selection `S`,
`⟦S ∪ {p,q}⟧ = ⟦(S ∪ {q,p})⟧` and neither the presence/order of `p` affects `q`'s contribution.

**Proof.** Disjoint footprints ⇒ `p` and `q` own disjoint atom sets in `T` and neither's `requires`
intersects the other's `provides` ⇒ `depends` has no edge between them ⇒ the fold's dep-topo-sort
places them in either order without changing either's owned atoms. The fold (§C) is a function of the
owned atom set and their PosIds, both independent across disjoint symbols. Hence order-independent. ∎

**[FALSE-IN-GENERAL — bounded by the oracle].** "Disjoint footprint" is a *static name-level*
predicate. Two patches with disjoint static footprints can still interfere dynamically (p monkeypatches
a class q dispatches on; p mutates a module global q reads via `getattr`). Then they do **not** truly
commute even though the algebra says they do. The cost is bounded: a false-commute produces a
selection the interface gate blesses but the build/behavior oracle rejects → routed to repair (§F).
So commutativity is *sound for the static fragment, best-effort globally, and the oracle is the
backstop.* See B.4.

### B.3 Reversibility, reorder, cherry-pick, branch — [PROVED as selection algebra; validity bounded]

- **revert** `p` `:= S ↦ S \ ⟳({p})`. **reorder** — no-op. **cherry-pick** `p` `:= S ↦ S ∪ ⟳({p})`.
  **branch** `b := (M', V')` a named selection. **fork** — two named selections over one base.

**Claim (algebra).** These are total operations on the selection semilattice; revert and cherry-pick
are mutual inverses on the membership lattice modulo `⟳`: `(S ∪ ⟳{p}) \ ⟳{p} = S` when `⟳{p} ∩ S = ∅`.
**Proof.** Set algebra on the OR-Set. ∎ Reorder-is-a-no-op: the fold orders by the *derived*
dep-topo-sort, a function of footprints, not of any insertion order; disjoint patches are order-free
by B.2; dependent patches have a forced order. So no selection carries order information to change. ∎

**[FALSE-IN-GENERAL — bounded].** "revert always yields a valid tree" is **false**: `S \ ⟳{p}` can
strand a `requires` (interface break) or break behavior (semantic break). The *operation* is total and
content-lossless (p's commits are never destroyed; it can be restored); the *result's validity* is a
separate reported fact (§C.4). This is precisely the "revert-drops-the-create" bug dissolved: a set
has no create/extend ordering to overload, and `⟳` names exactly what must come off with `p`.

### B.4 Soundness of the dependency predicate — [FALSE-IN-GENERAL — bounded by the build oracle]

**The op-log doc asserts** (§6): *"`depends` … never misses a real dependency (safe) and sometimes
over-includes."* This is the single most dangerous overclaim in the corpus. Refutation:

`depends` is computed from `provides`/`requires`, which come from **static def/use (`ast`) analysis**.
Static name-level analysis provably misses: dynamic dispatch, monkeypatching, `getattr`/`setattr`,
reflection, metaclasses, decorators that rebind, `__init_subclass__`, import-time side effects,
string-keyed registries, `eval`/`exec`, C-extension callbacks, and every non-lexical coupling. For all
of these there exists a true runtime dependency `q → p` with `requires(q) ∩ provides(p) = ∅`. Hence
`depends` **misses real dependencies**; it is **unsound** as a model of true dependence. ∎(refutation)

**The honest statement.** There are *two* gaps, and the corpus conflates them:

```
footprint  ⊆(sound over-approx)  STATIC name-level dep graph  ⊈(UNSOUND)  TRUE semantic dependence
     ↑ cheap, decidable                ↑ Weiser's static slice           ↑ decidable only by running
```

`depends` is a **sound over-approximation of the static slice** (it may over-include a spurious static
edge — harmless, the extra patch just comes off with a toggle) but the static slice is an
**under-approximation of true dependence.** Therefore `commute` yields *false positives* (declares
independence that does not hold dynamically).

**Why the design stays honest.** The build/test **oracle is the sole ground truth**, and *no gate ever
vetoes*. Unsoundness of `depends` cannot cause silent corruption; it can only cause a selection that
the cheap gates bless but the oracle rejects → repair. The design must therefore (a) **run the oracle
on every materialized selection** (never trust the interface gate as sufficient) and (b) provide the
`after` escape hatch to *declare* a dynamic edge the analyzer cannot see. The measurable cost is the
**false-green rate** M3 (§G).

### B.5 Collaboration: where conflict-freedom stops — [PROVED boundary]

**Claim.** Concurrent edits by two authors converge conflict-free **iff** their footprints are
disjoint (or one strictly `revises` the other). Concurrent edits to the *same* symbol cannot be merged
conflict-free.

**Proof (positive side).** Footprint-disjoint edits own disjoint atom subtrees; by B.1 the metadata
joins by SEC and by B.2 the fold is order-free; parseability holds by §C.3. So both land and converge
with no conflict. ∎

**Proof (impossibility side).** Suppose two authors concurrently rewrite the *body* of one function
`f` to two different intended versions `v1, v2`. Any deterministic merge `μ(v1,v2)` must either
(i) pick one (LWW) — drops a side, violating "no lost update"; (ii) interleave atoms by PosId —
produces `μ` that is neither author's intent and generally not even correct (a convergent, byte-faithful,
*semantically-garbage* tree — the exact failure the flat-CRDT claim hides); or (iii) refuse and
represent both — i.e. a conflict. There is no `μ` that is deterministic, loses nothing, and preserves
intent for arbitrary `v1,v2` (this is the standard 3-way-merge impossibility; a text/AST CRDT does not
escape it — it only makes (ii) *look* clean). ∎

**Consequence & design.** sgt takes route (iii) at **symbol granularity**: concurrent same-symbol
writes become two **versions** on that symbol's content axis (a per-symbol fork), surfaced to a human
or a `refine`/`repair`. This is why B.2/B.3 resolve write-write as a *dependency/version* relation,
never as statement interleaving. **The conflict-free-collaboration promise is precisely: disjoint
footprints merge with zero conflict; overlapping footprints surface an explicit versioned divergence.**
That is the mathematically maximal honest claim.

### B.6 Summary of law status

| law | status |
|---|---|
| metadata SEC (merge is c/a/i) | **[PROVED]** for `record`/`relabel`/`remap`/`select` over a join-semilattice with LWW keyed by the total order `(Lamport, ReplicaId)`; **`regroup` converges by causal serialization, not a semilattice join** (§B.1 C2) |
| disjoint patches commute | **[PROVED]** w.r.t. static footprint; **[FALSE-IN-GENERAL]** for dynamic coupling — bounded by oracle |
| revert = set difference (total, lossless *operation*) | **[PROVED]** |
| revert *yields a valid tree* | **[FALSE-IN-GENERAL]** — bounded by §C.3/§C.4 |
| reorder = no-op | **[PROVED]** |
| cherry-pick / branch / fork = selection algebra | **[PROVED]** |
| `depends` is sound ("never misses a real dep") | **[FALSE-IN-GENERAL]** — sound only for static names; oracle is ground truth |
| conflict-free collaboration | **[PROVED — bounded]** to footprint-disjoint work; same-symbol is provably not conflict-free |

---

## C. The recompose theorem

This is where Tension 1 is nailed. Recompose is the fold `⟦·⟧`, defined on the **structured content
CRDT** of §A.3 — **not** on git cherry-pick, and **not** on a flat span sequence.

**Definition (fold).** `⟦S⟧`:
1. `S' := ⟳(S)` — close the selection under dependency, SCC, and syntactic containment (BI).
2. For each in-force symbol, pick the atoms of its **pinned version** `V(p)` (defaults to tip). A
   symbol with concurrent divergent versions and no pin is a *reported divergence* (B.5), not folded.
3. Gather the atom forest owned by `S'`; order siblings by dense `PosId`; splice `pass`-leaves into any
   grammatically-empty mandatory body.
4. Concatenate verbatim leaf bytes depth-first. Result ∈ `T` (bytes), handed to git and the oracle.

**Theorem (Recompose).**

1. **Convergence — [PROVED].** For fixed `S`, `⟦S⟧` is identical on all replicas. *Proof:* steps
   2–4 are pure functions of the CRDT value + `S`; the sibling order is the total order of a sequence
   CRDT, which is convergent (Logoot/RGA SEC); version pins are LWW/MV reads. Determinism throughout. ∎

2. **Byte-faithfulness — [PROVED].** If `S` = the full recorded set with no concurrent divergence,
   `⟦S⟧` equals the recorded bytes. *Proof:* recording partitions the source into leaf spans with no
   gaps/overlap (a parse-and-slice), and the fold concatenates that partition in original order; concat
   ∘ partition = id. No `ast.unparse` anywhere ⇒ comments and formatting preserved. This is the death
   of the comment/format-drift bug at its root. ∎

3. **Subset parseability — [PROVED under BI; FALSE-IN-GENERAL without it].**

   *Positive (structured CRDT with block-integrity):* every `S` is, after step 1, **well-bracketed**:
   closed under syntactic containment and SCC. Claim: a well-bracketed forest of atoms concatenates to
   a grammatically parseable string. *Proof sketch → proof:* induct on nesting depth. Base: the module
   level is `Module ::= Stmt*`; any subsequence of complete top-level `Stmt`s is a valid `Module`.
   Step: a selected `Unit(header, children)` includes its header (containment closure) and, by the
   `pass`-leaf, a non-empty body; each child is itself well-bracketed by IH. No block-opener is ever
   present without its enclosing header, and no mandatory body is empty. Hence the concatenation
   derives from the grammar. ∎

   *Negative (flat span sequence — the corpus's model):* **[FALSE-IN-GENERAL].** Counterexample: `p1`
   contributes leaf `try:`; `p2` contributes leaf `except E: handle()`. In a flat `PosId` sequence with
   no containment constraint, `S = {p1}` folds to `try:` with no handler → `SyntaxError`. Symmetric
   cases: selecting a body statement without its `def` header; selecting a decorator without its
   target. A flat sequence CRDT is convergent and byte-faithful (parts 1–2 still hold) but **its
   subsets are not parseable.** This is the precise refutation of "concatenating verbatim spans in
   fractional-index order yields a parseable tree." ∎

   *Interior-edit attack (the orchestrator's sharpest form):* two patches both edit the interior of one
   function. Two sub-cases. (a) **Additive & disjoint** (p1 adds statement A, p2 adds statement B,
   different lines): both are `Leaf`s under `f`'s `children`; they interleave by `PosId`; any
   well-bracketed subset is parseable by part 3-positive, and the *order* is convergent —
   **parseable + convergent, but semantic coherence is oracle-bounded, NOT "safe"** (counterexample:
   p1 inserts `x = 1`, p2 inserts `return x`; the dense `PosId` order may place `return x` first — a
   parseable, convergent, semantically-broken body). This is B.4's dynamic-dependence gap re-appearing
   at statement granularity: the fold guarantees syntax and convergence; the build oracle (part 4) is
   the only judge of coherence. (b)
   **Conflicting rewrites** of `f`'s body: this is same-symbol write-write ⇒ `depends` makes them a
   version/refine relation, **not** concurrent statement atoms ⇒ they never interleave; the fold takes
   one pinned version (B.5). So the "interleaving produces garbage" case is *structurally excluded* by
   resolving write-write at symbol granularity. The garbage the orchestrator fears is real **only** if
   you (wrongly) model conflicting rewrites as concurrent statement atoms — which §A.3/§B.5 forbid.

4. **Semantic validity — [FALSE-IN-GENERAL — bounded by the oracle].** A parseable `⟦S⟧` may still
   fail typecheck/build/test (a dropped patch changed a signature callers rely on; a dynamic dep from
   B.4). The fold does **not** decide this. `materialize(S)` returns `(bytes, interface_breaks, oracle_result)`;
   the oracle result is ground truth; the residue routes to bounded repair (§F). No veto.

**What the theorem buys.** Recompose is deterministic and byte-faithful *unconditionally* (1,2),
parseable *by construction* (3, under BI), and semantic breaks are a *bounded, reported* residue (4).
git is off the recompose hot path (durability/interop only), so RISK-2 (cherry-pick conflicts on
full-tree snapshots) never arises. The price is building a correct **structured multi-language content
CRDT** — the hardest unbuilt piece (§G).

**Corollary (why the current code degrades to function-level).** Function/top-level granularity is the
*coarsest* granularity at which every selection is trivially well-bracketed (a module is `Stmt*`).
The current codebase degrades there not by accident but because that is the boundary of part 3 *without*
a structured CRDT. Finer (statement) granularity is sound **iff** you add the tree + BI constraint.
The corpus treats function-level as a bug; it is the theorem's edge, and BI is the way past it.

---

## D. The minimal primitive set (the DSL)

The kernel is **five logged operations**. Everything else is sugar over them, a no-op, or a read.

| primitive | signature | axis | what only it can do |
|---|---|---|---|
| `record` | `record(p, intent, commits, revises?, kind?)` | content + intent | **the atom.** Bind an intent annotation to byte-faithful commit(s), minting `PatchId`/new `sym_id`s. `revises:q` = refine (version axis). `kind:repair` = the integration patch. Footprint/deps are *derived*, never arguments. |
| `relabel` | `relabel(p, intent')` | intent only | Edit intent with **content frozen** — the pure `get`-side human annotation. |
| `regroup` | `regroup(p → {p1,…} \| {…} → p)` | patch identity | Repartition the `PatchId ↔ commit` binding. `decompose` = tombstone + record-with-provenance; `merge` = inverse. **Content never moves.** |
| `remap` | `remap(R ⊆ Σ_old × Σ_new)` | symbol identity | Carry canonical `sym_id`s across a **surface-name or structure change**. 1:1 = rename/move; **1:n = symbol-split, n:1 = inline** (generalized — see below). The only info content cannot express (content shows delete+create). |
| `select` | `select(branch, ΔM, ΔV)` | in-force + version | Edit a Selection's OR-Set membership and per-patch version pins. **branch / toggle / revert / cherry-pick / switch are all this.** |

The op record is the only stored struct: `Op = {id, vv, kind, target, payload, author}`.

### D.1 Irreducibility (why not fewer than 5)

Each primitive owns a **distinct axis** identified in the contracts ADR (identity / content / in-force)
plus the two the algebra forces (symbol-identity, intent-annotation). Cross-axis collapses fail:

- `relabel` ≠ `record`: folding it in would force `record` to accept empty content, destroying the
  invariant "record binds content" and muddying the drift gate (drift compares *new* footprint to
  intent; a relabel has no new footprint). **Irreducible.**
- `remap` ≠ `record`: a rename's *bytes* are a `record`; the *identity preservation* (`sym_7f3a` keeps
  its id) is information **not derivable from content** (content shows delete+create — B.5/symbol ADR).
  **Irreducible.**
- `regroup` ≠ `select`: `regroup` changes the `PatchId↔commit` partition (identity); `select` changes
  which patches are in force. Different lattices. **Irreducible.**
- `select` ≠ everything: it is the only op on the in-force/version axis. **Irreducible.**
- `record` is the only content-introducing op. **Irreducible.**

Fewer than five collapses two axes and reintroduces exactly one wart the corpus spent five ADRs
removing (e.g. merging identity and in-force is the "decision smears three things" bug).

### D.2 Completeness — and the one place it needed a sixth (absorbed into `remap`)

The op-log doc's five primitives are complete **except for symbol split/inline** (one function becoming
two, or two collapsing to one — the symbol-identity ADR §7 explicitly *defers* this as needing a
`split-symbol` provenance edge). `regroup` handles *patch* repartition, not *symbol* provenance; they
are different join keys. Rather than add a sixth primitive, **generalize `remap` from a bijection to a
provenance relation** `R ⊆ Σ_old × Σ_new`:

```
rename/move : R = {(s, s)}          -- 1:1, surface-name/file change only
split       : R = {(s, s1),(s, s2)} -- 1:n, s's provides fan out to new ids with provenance
inline      : R = {(s1, s),(s2, s)} -- n:1
```

This keeps the kernel at **5**, gives symbol-split/inline a home, and preserves the join key's honesty:
`requires` edges pointing at `s` are re-pointed through `R` at read time. Detection of split vs.
delete+create remains a **drift-gate confirm-loop** (bias to asking; a false-positive merge corrupts
the join silently, so default to delete+create when uncertain — symbol ADR §4).

**Concurrent-`remap` join rule (C3 — closes the split‖rename merge hole). [LOCKED]** Because `remap`
now carries a *relation* that can mint ids one replica never saw (a split), its concurrent merge is not
a plain per-`sym_id` register join. The rule: the merged symbol map is the **relational composition of
the two provenance relations** `R_A ∘ R_B` applied to the pre-merge map. This is well-defined and
convergent (relation composition is associative) **whenever the composite is still a function on each
live `sym_id`**. When it is **not** a function — the diagnostic case being concurrent **split‖rename**
of one `s` (replica A: `s ↦ {s1,s2}`; replica B: `s ↦ s`-with-new-name) — the composite maps `s` to
conflicting targets; that is exactly an **MV-register divergence**, surfaced as a drift prompt
("`s` was concurrently split *and* renamed — resolve") rather than silently picking a side. `requires`
edges pointing at `s` re-point to the *human-confirmed* resolution, never to a guessed union. So
generalized `remap` stays CRDT-clean: convergent by relational composition on the common case, and a
surfaced divergence (never a silent mis-join) on the genuinely-ambiguous concurrent case — the same
bias-to-ask discipline as detection. Like `regroup`, a `remap` whose relation is *ambiguous* under
composition is causally serialized rather than force-joined.

**Mass-rename UX — the confirm-loop is per-identity-event, not per-site (researcher #5). [LOCKED]**
Renaming `login` used at 50 call sites is **one `remap` op** over `R = {(sym_login, sym_login)}` for the
*one* symbol whose canonical id is preserved. The 50 call sites are `requires` *references* that resolve
*through* the map — they need **zero** per-site confirmation. The confirm-loop fires once per **symbol
identity event**, not per reference, so a 50-site rename is **one** drift prompt, not a storm. A
repo-wide refactor that renames *N distinct symbols* yields *N* candidates, **batched into one drift
review** ("N renames detected — confirm all / review each"); batch-confirm is required so a large
refactor is one decision. Silent mis-join risk is bounded by bias-to-ask + the fact that a wrong remap
is **visible** as a wrong edge in the graph and **reversible** by a corrective `remap` — never a silent
irreversible corruption.

---

## E. Workflow coverage table

`get` = deterministic recording (distill emits ops). `+`/`−` are selection edits. Every row is checked
against the algebra (§B) and the recompose theorem (§C).

| # | workflow | op sequence | algebra check |
|---|---|---|---|
| A1 | write new code | `record(p, …, commits)` | new symbols minted; footprint derived. ✓ |
| A2 | hand-edit outside the agent | `get(working-tree)` → `record`/`remap`(if rename detected) | recording is `get`, total & deterministic (§F). ✓ |
| A3 | refactor (behavior-preserving restructure) | `record` (+ `remap` for any moved/renamed sym) | edges preserved via canonical ids (B.2). ✓ |
| A4 | rename | `remap({(s,s)})` (+ `record` of the byte change) | join key stable ⇒ deps survive (symbol ADR §5). ✓ |
| A5 | move (cross-file) | `remap` with file dim changed | git rename detection proposes; drift confirms. ✓ |
| A6 | reformat | `record` with **empty footprint delta** | byte-faithful (C.2); semantically a no-op patch, toggle-safe. ✓ |
| A7 | change signature / arity | `record(revises:p)` (same `sym_id`, `provides` changes) | **interface gate PASSES (name unchanged) but callers may break → false-green → oracle/repair.** ✓ handled; exposes B.4. |
| A8 | split one function into two | `remap({(s,s1),(s,s2)})` + `record` | 1:n provenance (D.2). ✓ (detection heuristic — §G). |
| A9 | inline two into one | `remap({(s1,s),(s2,s)})` + `record` | n:1 provenance. ✓ |
| B1 | refine ("429 not 200") | `record(revises:p)` | version axis; `⟳` keeps it with its base. ✓ |
| C1 | build **without** feature p2 | `select(minimal, ΔM=−p2)` | `⟳{p2}` names co-removed deps; parseable by C.3; validity by C.4. ✓/oracle |
| C2 | cherry-pick p3 onto minimal | `select(minimal, ΔM=+⟳{p3})` | set union; B.3. ✓ |
| C3 | revert the whole auth feature | `select(HEAD, ΔM=−⟳{auth patches})` | set difference, lossless; no create/extend to overload (B.3). ✓ |
| C4 | switch a feature to v2 | `select(HEAD, ΔV={metrics↦v2})` | content/version axis; pin read (C.1 step 2). ✓ |
| C5 | "what if the order were different?" | **no op** | set is unordered; fold orders by derived deps (B.3). ✓ |
| E1 | bundled prompt → 3 patches | front-LLM `decompose` → 3×`record`, or 1×`record` + `regroup` | cardinality knob (§ below); provenance kept. ✓ |
| E2 | "fix bug **and** add logging" (unrelated) | `regroup` split into 2 patches | disjoint footprints commute (B.2). ✓ |
| F1 | fork (JWT vs sessions) | two `select` branches on a base | B.3. ✓ |
| — | non-Python | `record` (bytes commit); footprint via tree-sitter/LSP or **empty** | git-only floor; interface gate a no-op with no analyzer (contracts §4). ✓ degraded |

**Flagged gaps (findings, not hidden):**
- **A7 signature/arity** is the canonical **false-green**: name-level footprint cannot see it; only the
  oracle catches the break. This is the honest cost of B.4, quantified by M3 (§G).
- **A8/A9 symbol split/inline** are expressible via generalized `remap`, but *detecting* them (vs.
  delete+create) is heuristic; a false-positive silently corrupts the join. Bias-to-ask required.
- **A6 reformat** creates a semantically-empty patch. If reformatting is bundled with a real change in
  one commit, distinguishing the format bytes from the logic bytes inside a symbol is the
  sub-symbol-footprint problem (the decision→commit cardinality knob). Recommend **many commits per
  patch, one patch per intent**, so reformatting is its own commit — this ranks *above* DSL syntax.

Every workflow reduces to the five primitives. The two genuine theory-limits (A7 false-green, A8/A9
detection) are properties of the *dependency predicate's unsoundness* and *identity heuristics*, not
of the primitive set.

---

## F. The bidirectional lens

sgt is an **asymmetric lens** `(get, put)` between `C` (content/git) and `I` (intent/op-log), with the
**codebase** as a fold of a selection — three strictly separated layers (Tension 4):

```
        put : I × C ⇀ C            (planning: the coding agent; NON-deterministic, PARTIAL)
   I  ⇄  C
        get : C → I                (recording: distill; DETERMINISTIC, TOTAL)   ← PRIMARY

   codebase  ⟦S⟧ : Sel → C         (recompose fold; DETERMINISTIC; §C)
```

- **`get` (recording, primary).** `get(c)` parses `c`, slices it into the structured content CRDT,
  derives footprints, resolves canonical ids, and **emits the op stream** `record`/`remap`. It is
  **total** (every content state distills to *some* op-log) and **deterministic**. In lens terms the
  well-behavedness precondition — *get is defined everywhere* — is satisfied by recording, which is why
  recording, not planning, is the mathematically primary direction. This matches the owner's #1 ask.
- **`put` (planning).** `put(i, c)` = the coding agent produces new content realizing intent `i` from
  current `c`. **Non-deterministic** (same intent → different code) and **partial** (may fail). sgt
  never performs `put` — the agent or human does. Planning is "`get`'s grammar run forward with the
  `commit` slot empty" (a `PLANNED` patch = `record` with `commits = ∅`).

**The lens laws, honestly.**

- **PutGet (round-trip) — [holds only up to drift].** Ideally `get(put(i,c)) = i`. Because `put` is
  non-deterministic, this fails; the residual is exactly **drift**:
  `drift(i, c') = footprint(get(c')) ⊖ intent-footprint(i)` (symmetric difference). The **drift gate**
  surfaces it: an agent that added an unrelated refactor produces a footprint wider than its intent →
  drift flags it → the user `regroup`s (split) or `relabel`s. Drift is a *first-class measured
  quantity*, not an error. [SKETCH — the metric is well-defined; the resolution policy (auto-split vs
  ask) is an open UX call.]
- **GetPut (stability) — [PROVED for the no-op case].** `put(get(c), c) = c` when the agent is asked to
  realize exactly what is already there: recording then recomposing the full set is `⟦get(c)⟧ = c` by
  C.2 (byte-faithfulness). So the lens does not churn a codebase that hasn't changed. ∎
- **Consistency (the operative law).** After `reconcile` (a `get` over the working tree), the sgt tree
  must be *faithful* to git: `⟦get(working_tree)⟧ = working_tree`. This is C.2 again, and it is what
  makes "adjust sgt from the real code" a closed loop rather than a hope.

**Determinism partition (Tension 4 + researcher #4, discharged). [LOCKED]** The *only* fundamentally
non-deterministic arrow is `put` (intent→content). But four **knobs** leak non-determinism into the
recording direction; the rule that keeps the *stored graph* deterministic is: **advisory guesses only
ever propose; a logged op decides; deterministic folds compute; the drift gate forces every advisory
guess to be confirmed into a logged op before it can touch a deterministic quantity.**

| knob | affects | status | how it stays honest |
|---|---|---|---|
| **parser / grammar version** (`ast`, tree-sitter grammar) | footprint slicing, symbol locators | **deterministic-given-pin** | pinned per store; effect cache keyed by `(SHA, parser-version)`; changing the pin re-derives and the drift gate surfaces any footprint delta. |
| **θ** (rename/move/split similarity threshold) | *which* `remap` candidates are proposed | **advisory** | θ never auto-applies; it only proposes. The `remap` op (once confirmed via drift gate) is the deterministic truth. θ changes what's *asked*, never what's *stored*. |
| **partition** (modularity-max clustering into patches) | initial patch boundaries when *not* given | **advisory** | in the live `plan`/`checkpoint --fulfills` path the partition is **given** (deterministic). When guessed (bottom-up recorder), it's a non-unique proposal; the logged `regroup` ops are truth, so non-uniqueness never makes the *stored* graph non-deterministic — only the first proposal. |
| **LLM label** (gloss / purpose) | the `intent` string | **advisory, cosmetic** | never affects footprint/deps/`⟦·⟧`; deterministic fallback = `"changed {provides}"`. Truth is the logged `relabel` string, not the model. |

**Deterministic-given-pins outputs:** footprint, symbol resolution, `depends`, `⟦S⟧`, and metadata
merge. **Advisory outputs:** rename/split proposals (θ), initial partition (modularity), labels (LLM).
Determinism is recovered downstream of any captured commit by **recomposing byte-faithful content,
never regenerating** — and upstream, by never letting an advisory guess become truth except through a
logged, confirmable op.

**The LLM boundary, formally.** The LLM appears in exactly two arrows and authors code in neither
except the fenced repair:
1. **front:** freeform prompt → DSL ops (optional; deterministic fallback = 1 prompt ⇒ 1 `record`).
   Authors *ops*, never code.
2. **back:** on an oracle-caught semantic break, a **bounded repair** emitted as `record(kind:repair)`,
   attributed to a synthetic `integration` patch, dependent on the pair it reconciles. See **the repair
   fence** below — "no net-new top-level defs" is **rejected** as the boundary.

**The repair fence — "no net-new top-level defs" is the wrong boundary. [LOCKED]** It is *both* too
strict (a legitimate seam repair often needs a small adapter or import shim) *and* too loose (you can
originate arbitrary logic *inside* an existing function body, or add one import that pulls in anything,
without a single new top-level def). Replace it with a **conjunction of four deterministic checks plus
the oracle**, none of which claims to prove "the LLM didn't write logic" — they **bound the blast
radius and make the repair attributable and reversible**:

1. **Seam-locality.** The repair diff may touch only lines in the **seam** = (orphaned-symbol
   defs/refs ∪ git conflict hunks ∪ their smallest enclosing syntactic units). Deterministically
   diffable; a hunk outside the seam is rejected.
2. **Sandboxed provides.** The repair's `provides` must be `⊆ (provides of the in-force patches)
   ∪ integration.*` — a **reserved `integration` namespace**. New public symbols outside that sandbox
   are rejected. This *permits* adapters (fixing the too-strict problem) while fencing them.
3. **Retirement-on-toggle.** The repair patch depends on exactly the pair it reconciles; `select`
   removing *either* side **auto-retires the repair** (it never lingers as orphaned authored logic).
4. **Oracle-gated red→green.** The repair is accepted *only* if it moves the oracle from red to green,
   and is rejected otherwise. This is the real backstop and the only hard guarantee.

**Honest admission (RISK-D, §G):** these four bound *where* and *whether* a repair applies and make it
reversible and attributable; they do **not** prove the repair "did not originate a feature's logic" —
that property is undecidable. The enforceable guarantee is: **a repair is a sandboxed, seam-local,
auto-retiring patch whose sole license is turning the oracle green.** That is the honest restatement of
"sgt never authors code" at the repair seam — not a proof, a fence.

---

## G. Honest failure section — where the theory breaks and what to measure

**Theory limits (proven or conceded above):**
- `depends` is **unsound** w.r.t. true dependence (B.4). The algebra's `commute` has false positives.
  *Backstop:* oracle is ground truth; `after` escape hatch. *Unavoidable* — static analysis cannot
  decide dynamic dependence (reduces to the halting problem in general).
- Same-symbol concurrent edits are **provably not conflict-free** (B.5). Collaboration is conflict-free
  *only* on footprint-disjoint work. *Unavoidable* (3-way-merge impossibility).
- Subset-parseability requires a **structured CRDT with block-integrity** (C.3). A flat span sequence
  (the corpus's model) does not give it. This is a *buildable* fix but **unbuilt and hard**, especially
  multi-language (needs a tree-sitter-backed structured CRDT per language, with per-grammar
  mandatory-body rules). This is the highest-risk engineering item.
- Symbol split/inline **detection** is heuristic; false-positive corrupts the join silently (D.2, A8/A9).

**Kill metrics — the crux made precise (resolves Tension 3; ceilings pre-committed).** Because
recompose is a convergent fold (not cherry-pick), *conflict rate is the wrong metric.* Each metric
below has a **numeric ceiling** and a **named retraction** — the exact claim it withdraws on failure.
Sample from real repo histories (replay commit sequences, then run each C-case selection).

- **M1 — clean-cut / well-bracketedness rate.** Fraction of real product selections (C1–C5) whose
  `⟳(S)` is a structurally clean cut (closed under containment + SCC *without* demanding a sub-unit
  split) **and** whose `⟦S⟧` parses. *Prediction:* ≈ 1.0 by construction under BI (parseability *given*
  well-bracketed is proven = 1, §C.3; M1 measures how often real selections *are* well-bracketed).
  **Ceiling: M1 ≥ 0.95.** **Retraction if M1 < 0.95:** withdraw the "compose any subset" claim; drop
  to **rung 2** (function-level composition) or **rung 0** (recording lens only). This is the empirical
  test of L1-rung-1's viability.
- **M2 — bounded-repair reach (THE crux, RISK-A, researcher #3).** Of selections that *parse* but fail
  the oracle (semantic break), the fraction a seam-bounded repair (§F fence) drives to green within a
  fixed budget (**k ≤ 3 repair attempts, N-token cap**). **Ceiling: M2 ≥ 0.80.** **Retraction if
  M2 < 0.80:** withdraw "the algebra is made total by repair"; composition ships *only* for the
  well-bracketed-**and**-oracle-green subset, with **no repair promise** (semantic breaks are reported,
  not fixed). *The executable spike MUST wire the repair hook and measure this* — the current plan
  (conflict-rate + false-green, no repair hook) measures the wrong quantity (L5).
- **M3 — footprint-miss rate (soundness cost of B.4/A7).** Fraction of real toggles whose dropped patch
  was declared *commuting* (interface gate green) yet the drop **breaks the build** — i.e. `depends`
  missed a real dependency. Measured by perturbation: drop each "leaf" patch, run the oracle.
  **Ceiling: M3 ≤ 0.10 to expose *unrestricted* toggles.** **Retraction if M3 > 0.10:** restrict
  user-facing toggle/cherry-pick to **leaf-or-disjoint patches**, always run the oracle before
  surfacing a composed tree, and re-label the interface gate "symbol-orphaning only, not a validity
  predicate." (M3 never kills rung 0 — it only constrains composition aggressiveness.)
- **M4 — collaboration disjointness rate** *(diagnostic, no ceiling).* Of concurrent real-world patch
  pairs, the fraction footprint-disjoint (auto-merge, B.5 positive) vs. same-symbol (surfaced version
  divergence). Bounds how often the conflict-free promise actually applies; informs UX, does not gate.

**Crux ordering:** **M2 is the single kill metric** for the composition product (rung 1); M1 gates
*whether rung 1 is buildable at all*; M3 gates *how aggressive* toggles may be. All three leave rung 0
(recording lens) shippable unconditionally.

**Also unmeasured / unenforced:**
- **M5 — drift rate** (RISK-B): how often `put` exceeds its intent. If pervasive, the intent→content
  binding is weak and the graph is noisy.
- **RISK-D — repair fence adequacy:** "no net-new top-level defs" may be too strict (a legitimate repair
  may need a small adapter) or too loose. Not proven adequate; must be tuned against M2.
- **Perf:** every read is a fold; needs a memoized snapshot cache with "cache-not-truth" discipline
  (§op-log RISK). Fold is embarrassingly incremental per `PatchId`.
- **Trailer survival** across rebase/amend (all prior ADRs' RISK-4): the `Patch-Id`/`Symbol-Id`
  trailers must survive or the whole join rots. Needs a re-tag hook before any production use.

**One-line verdict.** The metadata algebra is on solid CRDT ground [PROVED]. The recompose fold is
deterministic and byte-faithful [PROVED] and parseable **iff** you build the structured/block-integrity
CRDT the corpus omitted [PROVED-conditional]. The dependency predicate is unsound and the design
survives that only because the oracle is ground truth and no gate vetoes [conceded, bounded]. The
product's viability rests on **M2 (bounded-repair reliability)** and on **shipping the structured CRDT
that makes M1 ≈ 1** — those two, not conflict rate, are the crux.
