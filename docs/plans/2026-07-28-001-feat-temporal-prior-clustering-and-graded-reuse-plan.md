# Temporal-prior clustering, graded label reuse, and incremental segmentation

> Status: planned. Decisions locked 2026-07-28 (see §8). Scope: the full grouping/labelling
> pipeline — feature tree (`sgt/lens/cluster.py`, `sgt/lens/tree.py`), labels (`sgt/lens/label.py`),
> and checkpoints/segments (`sgt/intent/segment.py`, `sgt/intent/theme_segment.py`) — in one pass.
> Every mathematical claim below was verified against the code and, where load-bearing, against
> leidenalg 0.12.0 empirically (§3.1).

## 0. Verdict on the three proposed improvements

An earlier design conversation proposed three staged changes (label-cache graded reuse; DF-Leiden
frontier incremental clustering; temporal-smoothness term in the CPM objective). Assessment after
reading the code:

1. **Label-cache graded reuse — VALID, with two mandatory corrections.** As proposed ("reuse when
   Jaccard vs. the previous snapshot ≥ ε") it is unsound: Jaccard is not transitive, so a chain of
   ε-similar steps composes to arbitrarily large total drift (ship of Theseus — a label survives
   total membership replacement without ever re-earning it). Drift must be anchored to the
   **generation-time** member set (§3.2). Second correction: weight members by op mass, not count.

2. **DF/frontier incremental clustering — CONCEPT VALID, machinery over-scaled.** The papers
   (Sahu et al. 2024–26) target graphs of 10⁶–10⁹ edges. This graph has 10³–10⁴ symbols, and the
   profiling record (2026-07 perf pass) shows build cost lives in signal construction and backfill,
   not in Leiden. What we should take is the frontier's **dirty semantics** — in particular the
   intra-leaf edge-delta case that `_cross_edge_dirty_leaves` provably misses (the documented reason
   `SIGNALS_VERSION` bumps nuke the tree). The vertex-level queue machinery is specified (§5, Phase
   D) but gated on measured need.

3. **Temporal-smoothness in the objective — VALID, and misjudged as "high risk / research".**
   It is the *cheapest* of the three. The evolutionary objective `CPM_γ(P) + ω·sim(P, P_prev)`
   requires **no modification to leidenalg**: it is exactly representable by zero-size anchor
   vertices (§3.1), verified working in the installed leidenalg 0.12.0. It subsumes the stability
   role of dirty-subtree splicing, which then survives purely as a speed optimization.

The three are not independent fixes; they are one principle applied at three layers (§2), so this
plan implements them as one coherent change with a shared evaluation harness.

## 1. Problem and diagnosis

Four distinct instabilities, each traced to a specific mechanism:

- **Partition churn (amnesia).** Every re-cluster optimizes CPM over the *current* fused graph
  with no memory of the previous partition (`tree._resplit_real` → `cluster._leiden_partition`).
  Community detection has many near-tied optima; a small edge-weight change flips which near-tie
  wins. Greene matching (`match_identities`, θ=0.5) renames leaves *after* the fact — a bandage
  over the wobble, not a cure. Below θ overlap the id dies and is reborn, cascading into a fresh
  label (new member hash) and a reset segment record.

- **Binary stability (the splice gate).** `_dirty_subdivide` either splices a previous subtree
  verbatim (member set exactly equal, no cross-leaf edge crossing `MIN_EDGE_SIGNAL`) or re-clusters
  it cold. Stability today is achieved by *refusing to look*, which is why the gate is blind to a
  leaf that should split internally — cross-edge dirtying only compares coupling *between* previous
  leaves, never *within* one (`_cross_edge_dirty_leaves`). The `SIGNALS_VERSION` full-rebuild
  hammer exists solely to compensate for this blindness.

- **All-or-nothing label cache.** `label._key` = SHA-1 of the sorted member set; exact match only.
  Adding one symbol to a 20-symbol lane is a full miss → a fresh LLM call → possibly a different
  name for what a human sees as the same feature. (One structural mercy already in place: super
  labels are keyed on child labels + files, so stabilizing leaf labels stabilizes every ancestor
  for free.)

- **Whole-timeline segment re-cuts.** `theme_segment._feature_key` hashes a feature's *entire* run
  sequence. One new commit busts the feature's segment cache; the LLM then re-cuts the whole
  timeline and may move *past* chapter boundaries. `pin_key` (a segment's first commit sha) then
  stops matching, silently dropping user relabels, and `@n` indices reshuffle. The deterministic
  rung has the same flaw in miniature: `_cap_cuts` re-ranks all seams globally, so a new seam can
  evict a previously-kept one.

## 2. The unifying principle

> **Carry the past forward as a soft prior with an explicit price — never as amnesia, and never as
> a hard freeze.**

- Tree membership: the previous partition enters the *objective* with weight ω. Near-ties break
  toward history; genuine new evidence still wins (verified phase transition, §3.1). Splicing
  stops being a correctness/stability mechanism and becomes a pure fast path.
- Labels: a name is reused while the feature's membership stays within a priced drift budget of
  the membership that *earned* the name; crossing the budget re-pays the LLM and resets the anchor.
- Segments: the one layer where a **hard freeze is domain-correct, not a compromise** — a chapter
  describes what the developer was doing *then*; a future commit is not evidence about the past.
  Retroactive re-cuts are churn by definition. So: committed chapters freeze; only the active tail
  window is (re)cut. (`overlay_persisted` already implements exactly this on the *read* path — the
  fix extends the same discipline to the *write* path.)

Precedence hierarchy, made explicit and uniform across layers:

```
user pins (hard: must/cannot-link, assign, label pins, segment relabels)
  ≻ current coupling evidence vs. temporal prior      (the ω-weighted contest)
  ≻ deterministic tie-breaks                          (SEED, sorted orders)
```

What deliberately does NOT change (standing invariants, all preserved):

- **The LLM never touches membership.** Labels name leaves; segment LLM emits validated commit
  shas only; op-sets remain deterministic functions of membership/runs (KTD6). Nothing below
  changes that boundary.
- **Save-time cascade stays frozen.** `ledger.local_move_assign` freezing all owned symbols is
  the prior with ω=∞ — correct *there*, because a save must be durable and pin-backed, not
  revisable. No change.
- **`sgt map --rebuild` stays a cold start** (prior off) — the user's explicit amnesia escape
  hatch. Only the *automatic* `SIGNALS_VERSION` migration switches from cold to prior-guided (§8).
- **Determinism and replica convergence.** The prior is derived from the *committed*
  `.sgt/tree/tree.json` / `.sgt/intent/segments.json`, which replicas share; anchors are inserted
  in sorted leaf-id order; `SEED` pins the optimizer. Same store + same committed state ⇒
  byte-identical output, exactly as today.

## 3. Mathematical foundation

### 3.1 The anchored-CPM objective (tree layer)

**Construction.** Let `P_prev` be the previous build's *leaf* partition (from the committed tree),
restricted to the current split's member set M. Augment the split's induced graph G[M] with one
anchor vertex `a_L` per previous leaf L with `|L ∩ M| ≥ 2`, edges `(a_L, i)` of weight ω for each
`i ∈ L ∩ M`, and `node_size(a_L) = 0`. Run the existing CPM Leiden (`find_partition`, same γ
search, same seed) over the augmented graph; strip anchors from the result before NO-ORPHAN /
arity counting.

**Exactness claim.** Because anchors have zero size (no CPM penalty contribution), no
anchor–anchor edges, and each anchor's placement is independent given the real partition P, the
optimum over augmented partitions equals

```
max_P  CPM_γ(P)  +  ω · Σ_L max_{c ∈ P} |L ∩ c|
```

i.e. CPM plus ω times **plurality agreement** with the previous leaf partition — each anchor
optimally joins the community holding the plurality of its leaf's members. This is the one-sided
Hamming similarity (Hamming distance up to optimal one-sided label matching), the discrete analog
of Chi et al. 2007's PCM temporal-smoothness term. Verifiable by brute force on small graphs
(test, §6).

**Why plurality agreement and not Rand/VI/NMI.** Pair-counting and information-theoretic partition
similarities are not node-local: encoding them in a vertex-partition quality function would need
O(|M|²) phantom pairwise terms, which both blows up the graph and distorts CPM's own penalty
structure. Plurality agreement is the *only* member of the standard partition-similarity families
that is exactly encodable with linear-size augmentation and zero distortion of the CPM term. This
is a principled choice, not a convenience.

**Empirically verified (leidenalg 0.12.0, this machine, 2026-07-28):**
- `node_sizes=0` is honored: manual CPM recomputation matches `part.quality()` (up to leidenalg's
  ×2 undirected double-count convention), confirming anchors pay no size penalty.
- Monotone pull with a clean phase transition: on a two-triangle bridge graph whose current
  coupling contradicts the previous partition, ω=0 reproduces the pure-CPM cut; moderate ω leaves
  the current-evidence cut intact (anchors merely join their plurality groups); only ω past the
  marginal-coupling threshold restores the previous grouping. Deterministic across repeated runs.
- The `is_membership_fixed` and `initial_membership` primitives needed for Phases B–D exist in the
  installed API (`ledger.local_move_assign` already uses the former in production).

**γ-search compatibility.** The anchor bonus is γ-independent. Every candidate partition's
augmented objective remains linear in γ with unchanged slope (`-Σ_c (n_c choose 2)` over real
sizes); the added term shifts intercepts only. Hence the resolution profile's monotonicity
argument (more communities as γ grows, for global optima) is untouched, and `_split_once`'s
binary search needs no modification — it stays the same heuristic it is today, no worse. The
augmented graph is built once per split; only γ varies across the ≤ `MAX_SEARCH_ITER` probes,
preserving the existing `_leiden_graph`-reuse economy.

**Split price and normalization.** Splitting a previous leaf of size 2k into two halves forfeits
ω·k of bonus (plurality drops from 2k to k) — the price of breaking history scales with the leaf's
smaller half. Under `ANCHOR_NORM="member"` (edge weight ω) big leaves resist splitting more than
small ones; under `ANCHOR_NORM="leaf"` (edge weight ω·C/|L∩M|, C a constant) every previous leaf's
total break price is ≈ constant. Both are sound; which matches user perception is an empirical
question — the harness sweeps both (§6). Default: `member`.

**Calibration.** ω must be dimensioned against the fused graph's weight scale, which varies per
induced subgraph. Set per split: `ω = STABILITY_ALPHA × mean positive edge weight of G[M]`.
`STABILITY_ALPHA` is an internal module constant (decision §8), chosen by harness sweep;
`STABILITY_ALPHA = 0` short-circuits the entire augmentation *and* warm start, reproducing today's
code path byte-for-byte (regression gate, §6).

**Composition with existing machinery** (all checked against the code):
- *Must-link contraction* (`pins.apply_must_link`): anchors are attached **after** contraction; a
  synthetic pin vertex receives the summed anchor weights of its real members (it may connect to
  several anchors — the strongest previous home pulls hardest; the hard constraint itself is
  unaffected because contraction is structural).
- *NO-ORPHAN, `_regroup_flat_root`, `enforce_cannot_link`, DEDUP*: all operate downstream on real
  members after anchors are stripped; unaffected. Anchors never count toward `MIN_LANE` or arity.
- *Degenerate priors are inert by construction*: a previous leaf with < 2 surviving members gets
  no anchor (a single-member anchor adds a constant to every partition — zero selective effect);
  dead symbols simply have no anchor edge; genuinely new symbols are unconstrained (no prior).
- *Greene matching stays*: the prior reduces how often Greene must rescue an id, but births,
  genuine splits/merges, and id minting remain Greene's job. θ unchanged.
- *Warm start*: `initial_membership` from `P_prev` (new members as singletons) biases the search
  basin toward the prior consistently with the objective. Active only when α > 0.

**Known risk, priced.** ω too high freezes the map (stops reflecting real change); ω too low
changes nothing. The phase-transition behavior means there is a meaningful middle: the harness's
churn-vs-cohesion frontier (§6) picks α, and cohesion regression gates it.

### 3.2 Graded label reuse (label layer)

**Schema.** Leaf label cache re-keyed by **feature id** (leaf node ids *are* feature ids at
`label_tree` time — `_apply_id_map` runs inside `build`, before labeling). Entry:

```json
{ "label": ..., "rationale": ..., "source": "llm" | "fallback",
  "gen_members": [...], "member_hash": "..." }
```

**Reuse rule.** For a leaf with feature id f and current members M: reuse the cached label iff
`source == "llm"` and `J_w(M, gen_members(f)) ≥ TAU_LABEL`, where `J_w` is weighted Jaccard
`Σ_{x∈A∩B} w(x) / Σ_{x∈A∪B} w(x)` with `w(x)` = number of ops touching symbol x (default 1 when
unknown) — the label follows the feature's center of historical mass, not its raw symbol count.
Otherwise call the LLM and reset `gen_members := M`.

**Why generation-anchored (the ship-of-Theseus lemma).** Thresholding drift against the *previous*
snapshot gives no bound on cumulative drift: for any ε > 0 and any target similarity δ > 0 there
is a chain M₀, M₁, …, M_k with J(M_{i-1}, M_i) ≥ 1−ε for all i and J(M₀, M_k) < δ (replace one
member per step). Anchoring at `gen_members` bounds total drift before a forced relabel by
construction, and each relabel resets the anchor — hysteresis is inherent, no extra state. A
useful corollary: growth beyond 1/TAU_LABEL× the generation size forces a relabel automatically
(J ≤ |gen|/|M| < τ), so no separate growth cap is needed.

**Interplay, checked:** fallback-sourced entries keep today's retry-on-next-client semantics
(graded reuse applies to `source=="llm"` only). DEDUP and pin/authored label overrides run after
cache resolution, unaffected. Super labels stay content-keyed on child labels — the cascade
stabilizes for free once leaves do. `TAU_LABEL = 0.5` initially (deliberately aligned with Greene
θ: identity continuation and name continuation share a notion of "still the same thing"), swept
in the harness. Staleness is the real risk and gets its own probe (§6).

### 3.3 Dirty semantics and the frontier (build path)

**Now (Phase C): leaf-granular delta dirtying.** The fused snapshot
(`_load_fused_snapshot`, fingerprint-keyed) already gives us the exact edge delta between builds.
Add the missing case: a previous leaf is dirty when its **internal** delta is significant —
`Σ_{pairs ⊆ L} |Δw| ≥ max(ABS_FLOOR, INTERNAL_DIRTY_FRAC × Σ_{pairs ⊆ L} w_old)` — alongside the
existing cross-leaf trigger. This is a strict superset of the DF frontier's seed set at leaf
granularity, closes the documented intra-leaf blindness, and costs one pass over the delta.
Splicing survives for subtrees that are clean under *both* triggers — now purely as a fast path,
since the prior (§3.1) independently guarantees stability wherever re-clustering does happen.

**Later, gated (Phase D): vertex-granular frontier.** The DF fixed-point argument is sound: a
node whose incident edges are unchanged and none of whose neighbors changed community has
unchanged move gains, so local-moving a queue seeded with changed-edge endpoints (expanding on
community change) converges to a partition that is single-move-stable for the *full* objective.
Two honest caveats the papers under-emphasize: (a) this reproduces only Leiden's local-move
guarantee — the connectivity guarantee needs a refinement pass restricted to touched communities,
and an edge *deletion* inside an otherwise-untouched community can silently disconnect it, so any
community incident to a changed edge must be marked touched; (b) at this repo's scale the expected
wall-clock win over per-subtree warm-started resplit is small. Hence: implement only if Phase C
measurements show per-build resplit cost that matters (gate in §6).

### 3.4 Segments and checkpoints (intent layer)

**Causality argument.** Unlike tree membership — where new evidence legitimately reinterprets
structure — an intent chapter is a claim about a contiguous *past* stretch of a feature's
timeline. Future commits are not evidence about it. Therefore the correct stability policy is
stronger than a soft prior: **committed chapters freeze; only the tail is live.**

- **Rung 2 (LLM, `SegmentThemer`) — incremental tail re-cut.** Replace the whole-timeline
  `_feature_key` cache-bust with: given the persisted record, freeze all chapters except the last;
  send the LLM only the window `[last persisted chapter's runs + new runs]` (same prompt contract,
  same sha validation, same `_coalesce`); splice the result after the frozen prefix. Cost becomes
  O(new work) per feature instead of O(whole history); every frozen `pin_key` and `@n` index below
  the tail survives by construction. `MAX_RUNS` applies to the window, not the timeline, so
  long-lived features regain LLM segmentation instead of falling back. Escape hatch:
  `sgt intent build --recut <feature>` re-cuts a whole feature (mirrors `--rebuild`'s role).
- **Rung 1 (deterministic) — seam hysteresis.** In `_boundary_score`/`_cut_points`, add
  `SEAM_BONUS` (η, with 0 < η < CUT_THRESHOLD) to a seam that starts a chapter in the persisted
  record. η below the cut threshold means the bonus can only *preserve* an existing boundary
  hovering near threshold, never invent one — pure hysteresis against flicker, including through
  `_cap_cuts` re-ranking (a previously-kept seam is strictly less likely to be the weakest).
- **Invariant preserved:** op membership stays a deterministic function of covered runs
  (`overlay_persisted` unchanged as the read path); the LLM still only ever moves boundaries and
  names, on a now-bounded window.

## 4. Phases

Ordering is by dependency and blast radius; Phase 0 gates everything (measurement first).

### Phase 0 — Temporal replay harness + baseline (extends `experiments/patch_clustering/`)

Extend `cohesion_harness.py` with a **replay protocol**: reconstruct the op store at each of a
sequence of historical points of this repo, run `build` at each point feeding the previous point's
result as `previous` (exactly the production incremental path), and record per step:
- identity events (birth/death/split/merge counts; spurious-churn rate = deaths whose members
  reappear in a birth within the same step),
- partition drift (plurality agreement — the quantity ω prices — plus VI as an independent
  second opinion, computed post-hoc),
- label churn (leaves whose label text changed) and label LLM-call count,
- segment-boundary churn (chapters whose commit-sha span changed) and segment LLM-call count,
- existing cohesion metrics (per-leaf co-commit cohesion, cross-feature mass, Greene stability),
- wall-clock per stage.
Record the α=0 baseline. All read-only against the target repo (existing harness discipline).

### Phase A — Graded label reuse (`sgt/lens/label.py`, call site in `tree.label_tree`)

Per §3.2. `leaf_request` gains the feature id; `Labeler` gains the id-keyed entry with
`gen_members` and the `J_w ≥ TAU_LABEL` reuse rule; op-touch weights passed in from the build
site (`map.build_map` already holds ops). Old member-hash entries migrate lazily (first hit under
the new rule re-keys them). Constants: `TAU_LABEL = 0.5`.

### Phase B — Anchored-CPM prior + warm start (`sgt/lens/tree.py`, `sgt/lens/cluster.py`)

Per §3.1. New: `_augment_with_prior(members, induced, prior_leaf_of, alpha, norm)` returning
`(aug_nodes, aug_edges, node_sizes, anchor_ids)`; `_split_once`/`_resplit_real`/`_subdivide`
thread `prior_leaf_of` (computed once in `build` from `previous`); anchors attached
post-contraction; anchors stripped before big/small counting; `initial_membership` warm start.
Constants: `STABILITY_ALPHA` (swept, then fixed), `ANCHOR_NORM = "member"`.
`STABILITY_ALPHA = 0` ⇒ byte-identical to today (test).
*B2 (gated on Phase 0 evidence):* weighted Jaccard (same op-mass weights) in `match_identities`,
θ unchanged — only if the replay shows id rescues failing on mass-asymmetric overlaps.

### Phase C — Dirty semantics + migration policy (`sgt/lens/tree.py`)

Per §3.3: `_internal_dirty_leaves` alongside `_cross_edge_dirty_leaves`; splice requires clean
under both. `SIGNALS_VERSION` mismatch switches from cold `_resplit_real` to prior-guided full
re-optimization (mark everything dirty, α as normal) — decision §8; `force_rebuild=True` remains
cold. Constants: `INTERNAL_DIRTY_FRAC`, `ABS_FLOOR` (swept). Retires the "signal bump ⇒ id-safe
but shape-amnesiac tree" behavior.

### Phase D — Vertex-granular frontier (gated)

Per §3.3. Implement only if Phase C replay shows p50 per-build clustering time above the gate
(§6). Seed = endpoints of changed edges; queue expansion on community change;
`Optimiser.move_nodes(is_membership_fixed=...)` over the stable complement; touched-community
connectivity repair (igraph connected-components per touched community; disconnected ⇒ refine).

### Phase E — Segment stability (`sgt/intent/segment.py`, `sgt/intent/theme_segment.py`)

Per §3.4: tail-window incremental re-cut in `SegmentThemer.segment_features` (window key replaces
whole-timeline `_feature_key`); `SEAM_BONUS` hysteresis in the rung-1 scorer; `--recut` escape
hatch in `sgt intent build`. Constants: `SEAM_BONUS = 0.5` initially (half the cut threshold),
swept.

## 5. Evaluation protocol and gates

All sweeps run on the Phase 0 replay over this repo's own history (200+ commits, organic,
feature-shaped — the harness's own argument for sufficiency), read-only.

| Sweep | Range | Selects |
|---|---|---|
| `STABILITY_ALPHA` | {0, 0.05, 0.1, 0.25, 0.5, 1.0, 2.0} × both `ANCHOR_NORM` variants | the knee of the churn-vs-cohesion frontier |
| `TAU_LABEL` | {0.4 … 0.8} | label-churn vs. staleness trade-off |
| `INTERNAL_DIRTY_FRAC` | {0.1, 0.25, 0.5} | dirty-rate vs. missed-restructure |
| `SEAM_BONUS` | {0.25, 0.5, 0.75} | boundary-flicker elimination without artificial seams |

Gates (provisional targets; finalized against the recorded α=0 baseline):
- **No quality regression:** per-leaf co-commit cohesion and cross-feature mass within 2% of
  baseline at the chosen α.
- **Churn:** spurious-churn rate (death+rebirth pairs) reduced ≥ 40%; Greene continuation rate up.
- **Label economy:** leaf-label LLM calls on modify-only replay steps reduced ≥ 50%; label churn
  on continuing features near zero.
- **Staleness probe (the calibration risk, §3.2):** on a sample of reused labels, force a fresh
  LLM label and compare; the fraction of materially different names bounds the staleness cost of
  `TAU_LABEL` and must stay under an agreed ceiling (proposed: 10%).
- **Segments:** zero committed-chapter boundary changes on append-only replay; segment LLM calls
  O(new commits).
- **Phase D trigger:** implement only if p50 clustering (post-signals) wall time at Phase C
  exceeds ~300 ms on replay.

Unit tests pinned in-repo (not just harness runs): anchor size-neutrality + phase-transition
(promote the 2026-07-28 scratchpad experiment); brute-force equivalence of augmented-CPM optimum
vs. `CPM + ω·plurality` on all partitions of n ≤ 8 graphs; α=0 byte-identity with today's build;
ship-of-Theseus chain forcing a relabel within ⌈1/(1−τ)⌉ single-swap steps; frozen-prefix
invariance of segment records under appended runs; determinism (repeated builds byte-identical).

## 6. Risks

| Risk | Where | Mitigation |
|---|---|---|
| α too high → map stops reflecting real change | §3.1 | phase-transition property + cohesion gate; `--rebuild` stays cold |
| Stale label survives a real meaning change | §3.2 | generation anchoring bounds drift; staleness probe gates τ |
| leidenalg changes `node_sizes=0` behavior in a future version | §3.1 | pinned unit test fails loudly; version noted in test docstring |
| Anchor bridges hold a leaf together against strong split evidence | §3.1 | split-price analysis; `ANCHOR_NORM` sweep; harness watches stop_split rates |
| Prior-guided migration damps a deliberate signal-recipe change | Phase C | phase transition means real signal changes still win; `--rebuild` escape hatch; harness replays a synthetic recipe change |
| Frozen segment prefix preserves a genuinely wrong old cut | Phase E | `--recut` per feature; user relabels (`pin_key`) now *survive*, which is the greater good |
| Replica divergence via the prior | §2 | prior reads only committed state; no local-only input affects membership |

## 7. References

- Traag, Waltman, van Eck 2019 — Leiden; Traag, Van Dooren, Nesterov 2011 — CPM,
  resolution-limit-free.
- Mucha, Richardson, Macon, Porter, Onnela 2010 — multislice community detection (interslice
  coupling ≙ the anchor construction's ancestor).
- Chakrabarti, Kumar, Tomkins 2006; Chi et al. 2007; Al-sharoa et al. 2019 — evolutionary
  clustering (snapshot quality + temporal cost).
- Greene, Doyle, Cunningham 2010 — community tracking (already in `match_identities`).
- Sahu et al. 2024–26 — DF Louvain / dynamic Leiden (arXiv 2404.19634, 2410.15451, 2405.11658,
  2601.08554) — dirty semantics adopted; machinery gated.
- Bang 2023 (GPTCache); SCALM 2024; calibration-gap / cache-routing line 2026 — graded LLM
  response reuse and its safety threshold (mapped here to `TAU_LABEL` + the staleness probe).

## 8. Decisions log (2026-07-28, with the user)

1. **α exposure:** internal module constant (next to `PATH_SCALE`/`SEED`), harness-tuned.
   No config key, no CLI flag. Revisit only if the sweep shows genuinely distinct operating points.
2. **Scope:** everything in one pass — tree prior, label reuse, dirty semantics, *and* segment
   stability are one plan with one harness. (Phase D alone remains measurement-gated.)
3. **`SIGNALS_VERSION` migration:** prior-guided full re-optimization at normal ω (not cold, not
   damped). `--rebuild` remains the cold-start escape hatch.
4. Defaults set in this plan (revisable by sweep, not by taste): `TAU_LABEL = 0.5` (aligned with
   Greene θ), `ANCHOR_NORM = "member"`, `SEAM_BONUS = 0.5`, ω scaled by mean induced edge weight.
