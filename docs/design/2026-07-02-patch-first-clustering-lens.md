---
date: 2026-07-02
topic: sgt as a patch-first clustering lens — the decision-DAG is not authored and not a deterministic fold of an op-log; it is a STABLE clustering overlay maintained incrementally over a deterministic symbol-patch substrate mined from git. Bottom-up, faithful-by-construction at the leaves, LLM confined to labeling + tie-breaks in a dirty region.
status: design / ADR — PROPOSED. Challenges the 2026-07-01 synthesis on its load-bearing assumption (structure = deterministic fold of an authored op-log). Keeps its deterministic-core discipline; inverts its source of truth.
challenges:
  - "the semantic source of truth is an append-only op-log of 5 authored primitives" (2026-07-01 SYNTHESIS §3)  # inverted: the source of truth is the mined symbol-patch stream; the op-log is derived
  - "the decision graph is a deterministic, replayable fold (fold_ops ≡ build_decisions)" (2026-07-01 SYNTHESIS §6, P4)  # relaxed: the grouping overlay is stable-not-deterministic by design (temporal smoothing is path-dependent on purpose)
  - "intent decomposition is the only non-derivable input" (2026-07-01 intent-patch §2)  # split: the PARTITION is derivable (clustering); only the RATIONALE is not
builds-on:
  - docs/design/2026-07-01-SYNTHESIS-unified-direction.md          # keeps: deterministic core, LLM-out-of-structure discipline, drift/oracle gates, symbol-id join key
  - docs/design/2026-07-01-symbol-identity-scheme.md               # the minted canonical symbol-id is now the clustering identity substrate, not just the footprint key
grounding-literature:  # the five research strands this doc synthesizes (full refs at the end)
  - architecture recovery / module clustering  # ACDC, Bunch, Leiden/CPM, Wu-Hassan-Holt stability, Rigi omnipresent nodes
  - evolutionary / logical coupling (MSR)       # Gall, Zimmermann/ROSE, Oliva-Gerosa, untangling (Herzig-Zeller)
  - AST diffing + refactoring / identity        # GumTree, RefactoringMiner, CodeShovel, Godfrey-Zou origin analysis
  - dependence graphs + change impact           # SDG summary edges, forward slicing, PyCG soundiness, tri-source dirty set
  - feature location + incremental clustering   # Dit et al. survey, Greene community tracking, Chakrabarti evolutionary clustering, AFFECT
author-note: written by Claude as the synthesis of five parallel literature-grounding passes plus a four-turn design dialogue with the owner. The owner's ask, verbatim: a "stable (though not fully deterministic) architecture to construct and maintain sgt based off the actual codebase changes (patches)." [CALL] marks a judgment; [RISK] marks where it can fail; [BET] marks a claim only measurement can close.
---

# sgt: the patch-first clustering lens

## 0. What this document is

The 6/29→7/01 corpus converged on a lens whose *source of truth* was an **authored op-log** — a human/LLM decomposes intent into typed operations, and the decision graph is a deterministic fold of that log. This document inverts that. The source of truth becomes the **symbol-patch stream mined deterministically from git**; the decision-DAG becomes a **stable clustering overlay** maintained incrementally over it. The op-log survives, but as a *derived* artifact, not the root.

This is a bigger change than it sounds, and it is deliberately made honest about one trade the owner explicitly accepted: **the grouping layer is stable, not deterministic.** It cannot be a replayable fold, because the mechanism that makes it stable (temporal smoothing) is path-dependent *on purpose*. What stays deterministic is everything the grouping *rests on* — and, critically, everything that *operations* (toggle/revert/compose) execute on. The non-determinism is quarantined to an advisory, correctible overlay.

The doc is grounded: every load-bearing algorithmic choice traces to a specific result in the software-engineering or community-detection literature, cited inline and collected in §11.

## 1. The thesis, in one paragraph

A codebase's history is a stream of **symbol-patches** — AST-level entity changes (add/modify/rename/move/split/inline of a function/class/method), each carrying a **minted, opaque symbol-id** that survives refactoring. This stream is extracted deterministically and is *faithful by construction*: it is a function of the diffs, not of a prompt. Over this substrate sit two derived, weighted graphs — **structural coupling** (calls/imports/def-use, with omnipresent hubs stripped) and **evolutionary coupling** (co-change, untangled and decayed). A **deterministic community detector** clusters the fused graph into groups; a **cross-snapshot identity matcher** — not the detector — assigns each group a stable **decision-node id** by member-set overlap, surfacing `birth/death/merge/split/continuation` as named, reviewable events. An LLM is confined to two jobs inside the *dirty region* of each change: **labeling** a node and **breaking ties** when a symbol is ambiguous between nodes. The **plan/prompt is not the generator of structure** — it is a *supervision prior* (must-link hints) and the *carrier of rationale* that clustering can never recover. sgt authors no code; it maintains a faithful, stable, locally-updatable map of what the code became and — where a human told it — why.

## 2. What this overturns, and what it keeps

| | 2026-07-01 synthesis | this doc |
|---|---|---|
| **Source of truth** | authored op-log (5 primitives) | **mined symbol-patch stream** |
| **Decision graph** | deterministic *fold* of the log (replayable; `fold_ops ≡ build_decisions`) | **stable clustering overlay** (evolutionary clustering; path-dependent by design) |
| **Node identity** | minted `Patch-Id` on a recorded op | **member-set overlap match** over stable symbol-ids (detection ≠ identity) |
| **Non-derivable input** | the intent *decomposition* | only the *rationale* (the why); the *partition* is derived |
| **Drift** | commit footprint vs. intent claim | **reflexion**: hypothesized grouping vs. real coupling; significance-gated rebaseline |
| **Grouping determinism** | required (verifiable fold) | **relaxed to stability** (the owner's explicit ask) |

**Kept intact** (the discipline that made the synthesis good):
- **The deterministic core.** The atom, its identity, the coupling graphs, and the dirty-set are all deterministic. Operations execute on deterministic symbol-*sets*, never on LLM output.
- **LLM out of the structural path.** The LLM labels and tie-breaks; it never decides what composes. (Now provable-worth: §5 shows structural clustering alone is *known* to miss human intent — the LLM fills a documented gap, it isn't papering over a weak clusterer.)
- **The build/test oracle as sole ground truth** for whether a set actually works (the `depends` predicate is unsound in Python — §6.3).
- **The symbol-id as the join key.** The 2026-07-01 symbol-identity ADR is not superseded; it is *promoted* — the minted canonical id is now also the substrate the clustering identity is matched on.

## 3. The architecture — six layers, determinism decreasing upward

```
 L5  Rationale overlay      the WHY — captured at authoring time from plan/prompt/session hooks   [not derivable]
 L4  Semantic labels        LLM names a node; tie-breaks ambiguous membership — CONFINED to dirty region  [LLM, gated]
 L3  Decision-DAG           stable clustering: nodes = identity-stable SETS of symbol-ids; edges = builds-on  [stable, not det.]
 ────────────────────────── the determinism boundary ──────────────────────────
 L2  Coupling graphs        structural (hub-stripped) ⊕ evolutionary (untangled) → fused, weighted        [deterministic]
 L1  Symbol identity        minted opaque id; provenance DAG (continues/rename/move/split/merge)           [deterministic]
 L0  Symbol-patch atom      GumTree insert/delete/update/MOVE over tree-sitter AST; body-vs-signature class [deterministic]
```

The single most important structural fact: **L0–L2 are deterministic and are what operations run on; L3 is a stable overlay; L4–L5 are advisory.** A wrong label or a debatable grouping can never corrupt a composition, because composition resolves a decision-node to its member symbol-set (L1) and folds *those* — the LLM's opinion is not in that path. This is the two-layer split the dialogue converged on, now with a literature-grounded mechanism for each layer.

## 4. The algorithm, concretely

### L0 — The symbol-patch atom
Adopt the **Chawathe (1996)** tree-edit action set — `insert / delete / update / MOVE` — computed by a **GumTree**-style matcher (Falleri et al., ASE 2014) over a **tree-sitter** or **srcML** AST. MOVE is first-class, which is what makes an atom "the same statement, relocated" rather than delete+insert. Each atom is scoped to the enclosing entity (function/class/method) and classified by **change kind** — `body_only | signature | rename | move` — using the **ChangeDistiller** significance taxonomy (Fluri et al., TSE 2007) and **RefactoringMiner**'s Change-Signature family. The body-vs-signature distinction is load-bearing for locality (§6.1).

### L1 — Minted symbol identity + provenance DAG
Identity is **minted, opaque, and never derived from surface** (`file::qualname` is a *lookup index and matching evidence*, never the id). Per commit, join parent→child entities with a **CodeShovel-style matcher ladder** (Grund et al., ICSE 2021), cheapest-confident-first:

1. exact surface match (same qualname+signature) — the fast path;
2. **threshold-free AST statement matching** (RefactoringMiner 2.0: 99.6% precision / 94% recall on Java, Tsantalis et al., TSE 2020) — catches rename/move/signature-change-while-body-stable and body-change-while-declaration-stable;
3. token-similarity fallback (RefDiff CST / git similarity index) for the residue and non-Java;
4. **extract/inline detection** → 1:n and n:1 mappings.

The result is a **provenance DAG** (Godfrey–Zou origin analysis, TSE 2005; W3C PROV `wasDerivedFrom`) with typed edges `continues / renamed / moved / signature_changed / split_into / merged_from`; `born/died` only when no derivation is found. The stable symbol-id propagates along 1:1 edges (rename/move/body/signature all preserve it); a split keeps the id on the majority-body child and mints siblings with a back-edge (deterministic tie-break ⇒ idempotent). **[CALL]** threshold-free detection is the property that matters most here — it makes identity assignment *idempotent and reproducible*, which is what actually contains the instability risk the owner fears. Low-confidence joins are **quarantined for the drift-gate confirm-loop**, never silently merged (a wrong merge corrupts the join key downstream).

### L2 — The two coupling graphs, fused
**Structural graph.** Symbols are nodes; edges are calls/imports/def-use (PyCG-class for Python). **Mandatory preprocessing: strip omnipresent hubs.** This is a named, 30-year-old problem (Müller et al., Rigi, 1993) with quantified stakes — Wu, Hassan & Holt (ICSM 2005) showed one utility file made ACDC's largest cluster ~2× too big; removing 2–3 dominators fixed it. Detect hubs by fan-in/fan-out threshold + **inverse-frequency edge weighting** (a target referenced by everything is non-discriminative — the information-bottleneck principle from LIMBO, Andritsos & Tzerpos, TSE 2005). Route hubs to a separate `infrastructure` layer; they never anchor a decision node.

**Evolutionary graph.** From git history, per symbol-pair: `support` (co-change count) and asymmetric `confidence(A⇒B)=support(A,B)/support(A)` (Zimmermann et al., ROSE, TSE 2005). This signal is **empirically orthogonal to structural coupling** — the majority of co-changes are *not* explained by structural dependencies (Oliva & Gerosa, ISSRE 2015; Ajienka & Capiluppi, JSS 2017; Cataldo et al., TSE 2009), which is exactly why it can separate features that structure fuses. But raw co-change *re-introduces* the fusion via noise, so cleaning is mandatory:
- **untangle tangled commits first** (6–15% of fixes are tangled — Herzig & Zeller, MSR 2013) via def-use partitioning (ClusterChanges, Barnett et al., ICSE 2015) or PDG name-flow (Flexeme, Pârțachi et al., FSE 2020) — both apply directly at our AST-entity granularity;
- **size-normalize** (weight each pair by 1/files-in-commit), drop bulk/merge/format commits, apply **temporal decay**;
- **rename-tracking is free here** — the L1 symbol-id carries co-change across renames, which is the sharpest classic confounder.

**Fusion — co-change as a constraint, not an average.** Do not blend the two into one weighted number and hope (Beck & Diehl, WCRE 2010, found naive evolutionary clustering *underperforms* structural alone on data density). Instead use co-change to *correct* structure (the cleaner de-confounder per the MSR synthesis): **`cannot-link`** on high-structural / low-confidence pairs (cut the logger/DB-session edges structure over-connects) and **`must-link`** on high-confidence / low-fan-out pairs (confirm feature-internal cohesion). This is semi-supervised clustering with history-derived constraints.

### L3 — The stable clustering overlay
Per snapshot, run a **deterministic-enough** community detector on the constrained fused graph. Two viable detector choices, same wrapper:
- **[CALL, recommended] Leiden with the Constant Potts Model** (Traag et al., 2019): guarantees connected communities, has an iteration-stability fixed point, and — crucially — CPM is **resolution-limit-free**, which prevents the slow ossification where fine-grained feature nodes get swallowed as the codebase grows (the resolution limit, Fortunato & Barthélemy, PNAS 2007). Pin seed + node order for run-to-run reproducibility.
- **[alt] ACDC dominator/pattern skeleton** (Tzerpos & Holt, WCRE 2000): deterministic and local by construction, and gives an interpretable identity anchor ("node = region dominated by symbol X"). Ranked *most stable* in Wu et al. Weaker on authoritativeness for evolving systems; needs the same hub-strip in front.

**Avoid** Bunch/GA and label-propagation (stochastic, globally unstable — Wu et al. rank Bunch worst on stability; modularity's near-degeneracy, Good et al. 2010, means a one-edge change can flip a global optimum to a very different partition). This is the formal reason global-objective clustering is hostile to incremental stability.

**Nodes are identity-stable SETS of symbol-ids** (validated as the right choice by the identity mechanism below) — *not* subtrees — so a cross-cutting feature (auth spanning routes/models/middleware) is one node, and a changed symbol maps to its owning node(s) for locality. Edges between decision-nodes are the `builds-on` DAG (derived from inter-node dependency direction).

### L4 — The confined LLM
Structural clustering provably lands well short of human-meaningful modules — *every* technique in Garcia et al.'s ground-truth comparison (ASE 2013) did, with ~60–70% of files misplaced on evolving systems (Wu et al.). So the LLM is not optional garnish; it fills a *documented* gap. Its two jobs, both **gated to the dirty region** (write-set ⊆ dirty set, the seam-locality fence from the 7/01 synthesis, reused):
1. **Label** a (re)formed node — name the decision/feature.
2. **Tie-break** orphan/ambiguous membership using **feature-location fusion**: text/embedding similarity to existing node vocabularies (IR channel, Dit et al. survey) ∩ structural neighbors' memberships (Dora-style seeded propagation) ∩ co-change confidence — the three canonical fusion mechanisms (score-fusion PROMESIR, filtering SITIR, seeded-propagation Dora/FLAT³).

### L5 — Rationale overlay
The *why* — rejected alternatives, constraints, goals — is **not derivable** from any amount of clustering. It is captured at authoring time (plan, prompt, session hooks) and *attached* to the persistent node, surviving because the node id is stable (§6.2). This is the one thing bottom-up genuinely cannot reconstruct, and it is the reason the plan is kept (§7).

## 5. The crux — de-confounding shared infrastructure

This was the owner's (and the literature's) #1 feared failure: auth and metrics both call the logger and the DB session, so *structural* clustering fuses them. The design answers it in three independent, stacked ways, each grounded:

1. **Strip hubs before clustering** (Rigi; quantified by Wu et al.). Infrastructure never anchors a feature node.
2. **Down-weight by inverse frequency** (LIMBO/IB): an edge to a target used everywhere carries little information.
3. **Cut with co-change `cannot-link`**: infra has high structural fan-out but *low asymmetric co-change confidence* into any single feature (it changes for many independent reasons) — so the temporal signal, which is orthogonal to structure, severs exactly the edges structure over-connects.

No single one is sufficient (raw co-change re-fuses via tangled commits; hub-stripping alone loses legitimate infra→feature signal). Stacked, they are the documented state of the art.

## 6. Locality and stability — the two properties that make maintenance viable

### 6.1 Locality — the dirty set
When a commit changes symbols `S`, the affected region is the **forward slice** from `S` over the System Dependence Graph (Horwitz–Reps–Binkley, 1990; Weiser slicing, 1981). The **interface-vs-body boundary is precise**: a symbol's SDG **summary edges** are its dependence interface — recompute them after a body edit; **unchanged summary ⇒ the change is provably local** (no forward path crosses into callers); changed summary ⇒ it legitimately ripples to callers/overriders. Only decision-nodes owning a dirtied symbol (plus interface-ripple dependents) are recomputed; the rest are frozen by construction. Compute this incrementally (demand-driven query, Duesterwald et al. 1997; incremental IFDS, Reviser).

**[RISK] static analysis is unsound in Python.** PyCG achieves ~99% precision but only **~70% recall** (Salis et al., ICSE 2021) — dynamic dispatch, decorators, registries, monkeypatching, config/schema coupling all create real edges it misses. A static-only dirty set therefore *under-approximates*, so "provably untouched" cannot rest on static alone. The documented fix (Lehnert's survey conclusion) is **tri-source**: `dirty = static-forward-slice ∪ co-change-coupling`, then **confirm with the build/test oracle**. Co-change recovers the dynamic/config edges static misses; the oracle is the only thing that can *confirm* locality. Where the oracle disagrees with the static set, the disagreement is signal — fold it back into co-change.

### 6.2 Stability without ossification — the maintenance loop
The owner named the exact tension the field calls the **snapshot-quality vs. temporal-smoothness tradeoff** (Chakrabarti, Kumar & Tomkins, KDD 2006), whose `cp` knob is literally the stability↔ossification dial. The fix is *not* a fixed smoothing constant (that **is** ossification). Three mechanisms:

- **(a) Identity from cross-snapshot matching, decoupled from detection** (Greene et al., ASONAM 2010). The clustering may shuffle; the *identity* does not, because a new group inherits a decision-node id by **Jaccard overlap of its member symbol-id set** with the previous node (threshold θ). This is the single most transplantable result: it dissolves the flickering fear entirely, and it makes rename/move a *non-event* for identity (the member set is preserved). `merge/split/birth/death` become **named, reviewable transitions**, not silent reshuffles — which is also exactly where the L1 provenance edges and the L5 rationale get carried or forked.
- **(b) Local re-solve seeded from the prior partition** (Aynaud & Guillaume, 2010): initialize the detector with the previous labels and re-optimize *only the dirty neighborhood*. Untouched regions stay put by construction; small code change ⇒ small clustering change. Add a temporal-smoothness / `PCM` penalty (Chi et al., KDD 2007) against moving a symbol out of its current node.
- **(c) Adaptive, significance-gated rebaseline** (the anti-ossification valve): make the smoothing weight **self-tuning** (AFFECT, Xu et al., DMKD 2014) — high while structure is stable, dropping when genuine reorganization is detected; **gate the trigger on statistical significance** (bootstrap resampling, Rosvall & Bergstrom, PLoS ONE 2010) so transient edits don't trip it; on rebaseline, re-cluster from scratch with **consensus clustering** (Lancichinetti & Fortunato, 2012) for reproducibility, then **re-run the Greene matcher** so identity survives wherever structure did.

### 6.3 Faithfulness is graded — and gates target what is testable
Faithfulness degrades up the stack, and the design says so: **L0–L2 are faithful** (deterministic functions of the diffs); **L3 grouping is a stable interpretation**; **L4 labels are interpretations**. There is *no oracle for "correct clustering,"* so the gates test **invariants**, not quality: total coverage (every symbol in exactly one node), **locality** (a change dirties only the predicted nodes), **idempotence** (re-run on unchanged history ⇒ no change), and **conformance** via a reflexion model (Murphy–Notkin–Sullivan, TSE 2001: hypothesized grouping vs. real coupling ⇒ convergence/divergence/absence). Clustering *quality* is evaluated empirically against human judgment on a corpus (MoJoFM / a2a — Wen & Tzerpos; Lutellier et al.), never asserted.

## 7. The agentic pipeline

Three entry paths, **one maintenance algorithm** (cold-start = incremental-from-empty; if they differ, a rebaseline would vandalize a curated tree — so they must be the same code path):

**W1 — greenfield (prompt → code).** prompt → *plan* (the predicted decomposition) → coding agent writes code, **session hooks emit task/subtask boundaries** → extract patches (L0) → identity (L1) → coupling graphs (L2) → cluster (L3) **seeded by the plan as must-link priors** → label (L4) → **attach the plan's rationale** (L5). Here the plan is not thrown away (pitfall #3): it is the strongest clustering prior *and* the rationale carrier, applied at the moment intent is richest.

**W2 — adoption (existing repo, no plan, no hooks).** Walk git history → L0 → L1 → L2 (structural + co-change; co-change is rich here because history exists) → cluster from scratch with consensus (L3) → label (L4). Graceful degradation: no plan ⇒ pure bottom-up; the rationale layer starts empty and fills as the user edits.

**W3 — incremental (both, steady state).** new commit → L0 patches → L1 identity update (matcher ladder) → **dirty set** (forward-slice ∪ co-change, oracle-confirmed; §6.1) → **local re-solve seeded from prior** on the dirty neighborhood (§6.2) → Greene identity-match → surface `merge/split/birth/death` → LLM **relabels only dirty nodes** → adaptive significance-gated rebaseline check.

**Where the LLM lives / gates:**

| zone | who | LLM | gate |
|---|---|---|---|
| extract patches, identity, coupling, dirty-set, cluster | deterministic pipeline | **no** | idempotence, coverage, locality |
| label + tie-break | confined LLM | yes, **write-set ⊆ dirty region** | seam-locality fence |
| plan → predicted partition (W1) | front LLM, optional | yes, advisory prior only | never authoritative; corrected by real patches |
| "does this in-force set build?" | build/test oracle | no | sole ground truth |

**[RISK] hooks vs. universality.** Session hooks give the best co-change/boundary signal, but making them *load-bearing* breaks the "works with any coding agent" on-ramp (the reconcile-from-git path needs zero cooperation). **[CALL]** hooks are an *optional enrichment with graceful degrade to git-temporal signal* — never required. W2 must cluster acceptably with none.

## 8. How the six pitfalls (from the design dialogue) are closed

| # | pitfall | resolution | grounding |
|---|---|---|---|
| 1 | "LLM clustering" contradicts "minimize LLM / maximize stability" | clustering is deterministic (Leiden-CPM/ACDC); **identity decoupled from detection** (Greene); LLM only labels/tie-breaks | Traag 2019; Greene 2010 |
| 2 | structural clusters fuse shared infrastructure | hub-strip + IB down-weight + co-change `cannot-link` (3 stacked de-confounders) | Rigi 1993; LIMBO 2005; Oliva-Gerosa 2015 |
| 3 | cold-start discards intent at the richest moment | plan = must-link prior + rationale carrier; hooks = boundary signal; degrade for W2 | Dit et al. survey; feature-location fusion |
| 4 | "faithful to codebase" oversold up the stack | graded faithfulness; gates test invariants (coverage/locality/idempotence) + reflexion conformance, not "correctness" | Murphy-Notkin-Sullivan 2001 |
| 5 | incremental fights restructuring (ossification) + split/merge breaks history | AFFECT adaptive smoothing + Rosvall significance gate + consensus rebaseline; Greene merge/split as named events; L1 provenance DAG | Chakrabarti 2006; AFFECT 2014; Greene 2010; Godfrey-Zou 2005 |
| 6 | verification is the real advantage | deterministic core is testable; **operations execute on symbol-sets, not LLM clusters**; oracle confirms dirty set | HRB SDG 1990; PyCG soundiness 2021 |

## 9. Decided vs. bet

**Decided (grounded in results):**
- The atom (GumTree/Chawathe), identity (minted id + RMiner-class matcher + provenance DAG), and hub-stripping are settled techniques with reported precision.
- Co-change is orthogonal to structure and *can* de-confound — but only cleaned and used as constraints.
- Identity must be **decoupled from detection** (member-set matching) — this is the keystone that delivers stability.
- Leiden-CPM over global-objective methods for the detector; local seeding for incrementality.
- Operations must run on the deterministic symbol-sets, not the overlay.

**Bet (only measurement closes):**
- **[BET-1] Fused structural⊕co-change constrained clustering actually matches human feature boundaries** better than either alone, on *code* graphs. Beck & Diehl warn evolutionary clustering can underperform on sparse history — this is real risk, especially cold-start. *Metric:* MoJoFM/a2a vs. a hand-labeled decision map on ≥3 stress projects.
- **[BET-2] The dirty set is sound-enough.** How often does a change's true impact escape `static ∪ co-change` and only the oracle catches it? *Metric:* oracle-caught-miss rate; target the escape into co-change-augmentation.
- **[BET-3] Stability holds under real churn without ossifying.** Does incremental seeding + adaptive rebaseline keep node identity stable across N commits while still splitting when the code genuinely reorganizes? *Metric:* identity-churn rate vs. legitimate-split recall.
- **[BET-4] LLM tie-break/label quality** inside the dirty region is good enough that humans rarely override. *Metric:* human-override rate.

## 10. The smallest experiment that de-risks the most

One harness, one stress project's real git history, no CRDT, no new UI:

1. Extract the symbol-patch stream + minted identity over the full history (L0–L1).
2. Build the hub-stripped structural graph and the cleaned co-change graph; fuse with constraints (L2).
3. Cluster with Leiden-CPM; run the Greene matcher across every commit (L3).
4. **Measure the four bets directly:** (a) MoJoFM/a2a of the final clustering vs. a hand-labeled decision map [BET-1]; (b) per-commit identity-churn vs. legitimate-split recall [BET-3]; (c) dirty-set escape rate against the test suite [BET-2]. Defer LLM labeling [BET-4] to a second pass.

If BET-1 or BET-3 fails, the bottom-up thesis is wrong *before* any expensive build — and the fallback is the 7/01 recording lens, which still ships. If they pass, we have a faithful, stable, locally-updatable substrate that the authored op-log can sit *on top of* rather than *under*.

## 11. References (verified in the grounding passes; venues/years as reported)

- **AST diff / identity:** Chawathe et al., SIGMOD 1996 (tree edit script); Falleri et al., ASE 2014 (GumTree); Fluri et al., TSE 2007 (ChangeDistiller); Tsantalis et al., ICSE 2018 / TSE 2020 (RefactoringMiner, 99.6%/94%); Silva & Valente, MSR 2017 / TSE 2020 (RefDiff); Grund et al., ICSE 2021 (CodeShovel); Zou & Godfrey, WCRE 2003 / Godfrey & Zou, TSE 2005 (origin analysis, split/merge); W3C PROV-DM.
- **Clustering / architecture recovery:** Mancoridis et al., IWPC 1998 / ICSM 1999 (Bunch); Tzerpos & Holt, WCRE 2000 (ACDC); Andritsos & Tzerpos, TSE 2005 (LIMBO); Müller et al., JSM 1993 (Rigi / omnipresent nodes); Blondel et al., 2008 (Louvain); Traag et al., 2019 (Leiden/CPM); Fortunato & Barthélemy, PNAS 2007 (resolution limit); Good et al., PRE 2010 (modularity degeneracy); Wu, Hassan & Holt, ICSM 2005 (stability comparison); Garcia et al., ASE 2013 (ground-truth comparison); Wen & Tzerpos, 2004 (MoJoFM); Lutellier et al., ICSE 2015 (a2a); Murphy, Notkin & Sullivan, TSE 2001 (reflexion).
- **Evolutionary coupling / MSR:** Ball et al., ICSE-WS 1997; Gall et al., ICSM 1998 (logical coupling); Zimmermann et al., ICSE 2004 / TSE 2005 (ROSE); D'Ambros et al., WCRE 2009; Oliva & Gerosa, ISSRE 2015; Ajienka & Capiluppi, JSS 2017; Cataldo et al., TSE 2009; Beck & Diehl, ESEC/FSE 2011 & WCRE 2010; Herzig & Zeller, MSR 2013 (tangled commits); Barnett et al., ICSE 2015 (ClusterChanges); Pârțachi et al., FSE 2020 (Flexeme); Beyer & Noack, IWPC 2005 (CCVisu); Silva et al., Modularity 2014 (co-change clusters).
- **Dependence / change impact:** Weiser, ICSE 1981 (slicing); Ferrante et al., TOPLAS 1987 (PDG); Horwitz, Reps & Binkley, TOPLAS 1990 (SDG / summary edges); Arnold & Bohner, ICSM 1993 (impact-set vocabulary); Ren et al., OOPSLA 2004 (Chianti); Lehnert, IWPSE-EVOL 2011 (CIA survey); Salis et al., ICSE 2021 (PyCG, ~70% recall); Livshits et al., CACM 2015 (soundiness); Duesterwald et al., TOPLAS 1997 (demand-driven).
- **Feature location / incremental clustering:** Dit, Revelle, Gethers & Poshyvanyk, JSEP 2013 (survey); Poshyvanyk et al., TSE 2007 (PROMESIR); Liu et al., ASE 2007 (SITIR); Eaddy et al., ICPC 2008 (CERBERUS); Robillard & Murphy, ICSE 2002 (concern graphs); Chakrabarti, Kumar & Tomkins, KDD 2006 (evolutionary clustering); Chi et al., KDD 2007 (PCQ/PCM); Greene et al., ASONAM 2010 (community tracking); Aynaud & Guillaume, 2010 (incremental seeding); Xu, Kliger & Hero, DMKD 2014 (AFFECT); Rosvall & Bergstrom, PLoS ONE 2010 (significance); Lancichinetti & Fortunato, 2012 (consensus).

---

*This doc challenges the 2026-07-01 synthesis on its root (source of truth) while keeping its discipline (deterministic core, LLM-out-of-structure, oracle-as-truth). The keystone claim is that **identity decoupled from detection** turns "stable but not fully deterministic" from a hope into a mechanism. The four bets in §9, measured by the §10 experiment, decide whether the inversion is real.*
