# Stage B — intent clustering + label enrichment (living plan)

> This is a **living** document. Update it as understanding changes during implementation.
> Do not treat it as a fixed spec. Each work session appends a dated NOTE with what was
> tried, what the numbers said, and what the next smallest informative step is.

## The developer problem this must solve

The crux (user, round 3): *"When I saved a bunch of code changes, I remember I changed
code here and there — not 'I made a code change at this function, by that function.'
Reverting a feature is illegible: I don't know where it lands."*

Today's clustering fuses **co-change ⊕ hub-normalized structural ⊕ conventional-commit
scope** (`cluster.signals` → `tree._fuse`). Every signal is **spatial/structural**: it
groups *code that sits together or calls each other*. But a developer rewinds by
**intent and episode** — "the thing I was doing", "that afternoon's work". So the map
answers "what is this codebase made of" when the user is asking "what did I *do*, and
what happens if I undo this".

Symptom on this repo: reverting one feature removes **3337 / 6254 ops** — a god-cluster.
The clusters are not meaningful revert units.

## The reflection questions (re-ask these every session — the real acceptance test)

1. **Reason-through**: Looking at the graph, can a developer say *what a feature is* and
   *what undoing it costs* without reading code? (label + size + dependents legible)
2. **Meaningful revert unit**: Is a feature a unit someone would actually want to rewind
   to — an intent/episode — rather than a structural blob?
3. **Informed next step**: From the graph alone, can they decide what to do next
   (revert this / keep that / edit here)?
4. **Stability**: Do feature ids/labels stay put across refreshes (Greene identity), so
   the map is trustworthy during active dev?

If a change doesn't move at least one of these, it is not Stage B work.

## Hard constraints (do not break)

- **Node universe stays symbols** (`path::qualname`). New signals are *additive parallel
  edge weight maps* fused at `tree._fuse`. This keeps Greene member-set identity
  (THETA=0.5) and pins/labels intact. Re-scoping the node universe would churn ids and
  orphan pins — forbidden.
- Signals already on every `Op`: `Attribution[].session`, `kind`, `intent`,
  `provenance` (SHAs), and `commit_index` (via op-index ordering). No per-op wall-clock
  timestamp yet — `history()` returns `(sha, parent, subject)`. Adding `%ct` is a wide
  signature change; **defer it** unless a real-time axis proves necessary (use
  `commit_index` for temporal proximity instead).
- Labeling is deterministic-offline-capable (`_fallback_label`); every prompt change must
  keep the fallback working.

## Candidate signals (unproven — measure before committing to any)

- **session_edges**: symbols co-touched within one `Attribution.session` → "I did these
  together." Directly the "meaningful episode" signal. RISK: on a dirty tree all in-flight
  edits share one pseudo-session → could *reinforce* the god-blob, not split it. Must test
  on committed history.
- **temporal_edges**: symbols in ops close in `commit_index` → same period. Weak; likely
  redundant with session. Use commit_index, not wall-clock.
- **symbol_scope refinement**: `scope_edges` already exists (conv-commit scope). Maybe the
  lever is *reweighting* the fusion, not a new signal.

## Label enrichment (separate, lower-risk lever)

`_leaf_prompt` today sees only entity names + files + commit subjects. Enrich with each
member's dominant op `kind` (add/extend/rework/prune) and `intent` text so labels read as
*what was done* ("Reworked revert frontier") not just *what exists* ("Revert Frontier").
This may move reflection-Q1 more than any clustering change, for far less risk.

## Plan of attack (smallest informative step first — revise freely)

- [ ] **Step 0 — MEASURE the baseline.** Feature count, ops-per-feature distribution,
      id-churn across two consecutive `sgt map` runs, on this repo AND `SGT_PROBE_REPO`.
      Eyeball current labels against reflection-Q1/Q2. Record numbers here. *No code yet.*
- [ ] Step 1 — decide the first lever from the numbers (label enrichment vs. a signal).
- [ ] later steps appended as they're chosen.

## Session notes

### NOTE 2026-07-21 — kickoff
Read `tree.py`, `cluster.py`, `label.py`, `op.py`, `config.py`, `gitbind.history` in full.
Injection point confirmed: `tree.fused_graph` calls `cluster.signals` + `scope_edges`,
fuses via `_fuse`. Additive signals slot in there. Next: Step 0 measurement.

### NOTE 2026-07-21 — Step 0 measurement DONE. Original plan's central bet is DEAD.
Baseline on semi-git (`experiments/stageB_measure.py`):
- 6 features; **ONE holds 4807/4879 ops (99%) and 3456/3530 symbols (98%)**, at
  `split_reason=max_depth`. Textbook god-cluster.
- id-churn = 0 (Greene identity is rock-solid — do not touch it).

Root cause is **NOT "structure vs intent"** — it's an **edge-starved graph**:
- Only ~448 total edges over 3530 symbols → most symbols are isolated singletons.
- **4850/4879 ops touch exactly ONE symbol** (median footprint = 1). U2's def-use
  untangling splits each commit into per-symbol ops, so co-change (co-membership in one
  *op's* footprint) is structurally dead. cochange = 393 edges.
- **87% of symbols are `residue`** (3070/3530) — structurally isolated by construction;
  the 55 structural edges only connect the 350 `entity` symbols.
- **hub_cut = 1351** (0.15·9007 ops) → **0 hubs detected**. Hub suppression is dead code
  here.
- **0/9007 ops carry a session attribution** (same on probe). `session_edges` produces
  ZERO edges on any mined history — it only helps repos developed *through* sgt going
  forward. **Cut it from Stage B.**

The missing dense signal: the miner untangles a commit into per-symbol ops, but those ops
**still share a provenance SHA**. Co-membership in one *commit* = "I changed these
together" = an episode. This is the real intent signal, it reaches residue too, and it is
currently unused. Measured: co-commit gives **17,793 edges (40× co-change)** but only 27%
symbol coverage on semi-git — because **10 mega-commits (max 2375 symbols)** are excluded
by an 80-symbol cap, and they hold most residue. Probe (small commits) = 100% coverage.

Honest conclusion: ~73% of semi-git's symbols arrived in a few giant commits (initial
import / mass refactors) and have **no fine episodic structure to recover** — no signal
can invent it. Those belong in **path-based** lanes, which is exactly what NO-ORPHAN
mishandles today (`_attach_orphans` folds by coupling weight, but orphans have zero
coupling → `best_w` stays -1 → they ALL dump into `groups[0]`, the biggest lane).

## Revised attack (two independent, measurable levers)

- [x] **Step 0 — measure** (above).
- [ ] **Lever A — co-commit edges** (`cluster.commit_edges`, additive at `_fuse`): recover
      the co-change signal the untangling destroys, by binning alive non-hub symbols by
      provenance SHA (weight `scale/(size-1)`, cap mega-commits). Gives Leiden real
      communities for the 27% that has episode structure. Node universe stays symbols →
      Greene identity safe. **Measure: does the god-lane shrink? do real lanes appear?**
- [x] **Lever B — path (file-cohesion) edges** (`cluster.path_edges`, additive at `_fuse`).
      REVISED from "orphan fallback" after measuring: the god-lane is **72% zero-coupling
      isolated symbols** dumped by `_attach_orphans` into the one 877-node component's lane.
      A file is a coherent unit (residue + its entities change for one reason); file-level
      path edges reach **96%** of symbols (median 5/file, 1 file over cap). So make path a
      first-class *weak* connective signal, not a fold heuristic — cleaner and integrates
      with Leiden. co-commit (strong episode) + structural (imports) bridge *across* files;
      path binds *within* a file. **Measure: god-lane share, lane count, id-churn.**
- [ ] **Lever C — label enrichment** (`_leaf_prompt`): add member op `kind`/`intent` so
      labels read as *what was done*. Do last; cheapest reflection-Q1 win.

Do A → measure → B → measure → C. One lever per step, re-ask the reflection questions each
time. Defer `%ct` timestamps (wide `history()` signature change) — `commit_index`/provenance
suffice for Stage B.

### NOTE 2026-07-21 — Levers A+B measured (force_rebuild). Big win + two new problems.
**A CRITICAL measurement gotcha**: `sgt map` uses `_dirty_subdivide`, which splices unchanged
member-sets from the *previous* tree verbatim. A global *signal* change is invisible to it
(cross-edge dirtying only sees coupling *between existing leaves*, not that a leaf should now
split *internally*). So all Stage B numbers must be taken with `tree.build(..., force_rebuild=
True)`. Implication: the improvement won't reach users until a full recluster → need a
**signals-version bump that forces one rebuild** (no such mechanism exists; `MINER_VERSION` is
mining-only). Wire this once clustering quality is locked.

**Force-rebuild results (semi-git):** god-lane share **99% → 22%** (top lane 4807→1059 ops),
6 → 195 leaves, top lanes are recognizable subsystems (editor/vscode, tests/core, sgt/api.py,
sgt/cli, sgt/mcp, sgt/lens). Isolated symbols 72% → 6%, fused edges 448 → 47,962. id-churn 0.

**Two remaining problems:**
1. **Flat root**: 138 top-level "subsystems", ~130 are single-leaf singletons. CPM can't
   find 5–9 balanced groups (graph ≈ 423 weakly-linked file-cliques; co-commit only bridges
   27%). Unnavigable. FIX candidate: a weak *directory-cohesion* edge so same-dir files attract
   → a real dir→file hierarchy.
2. **Path dominates**: only 24/195 leaves cross a directory. Expected on THIS repo (73% is
   bulk-imported, no episode signal) — co-commit *should* dominate for incrementally-committed
   work, and does for that 27%. Not a bug per se; validate on the probe / a live-dev repo.

Reflection check: Q2 (meaningful revert unit) is much better (22% vs 99%), Q4 (stability) still
perfect. Q1/Q3 blocked on the flat root — a 138-wide top level is not reason-through-able.
Next: test directory-cohesion edges for the hierarchy.

### NOTE 2026-07-21 — resolution curve proves single-Leiden CAN'T give a 5-9 top level.
Probed CPM #big-groups across gamma at the root: 1e-4 → 141 big groups (largest still 985);
1.0 → 6 big groups but everything shattered (largest 10, 3481 total singletons). **No gamma
lands in [5,9] with real structure.** Directory-cohesion edges also tested → slow (big-dir
cliques are O(n²), tests/core=379 syms) and blob-prone; not the answer. Conclusion: the flat
root needs a **deterministic post-clustering super-grouping by package**, not edge tuning.

**Scope decision (§10 anti-runaway-refactor):** LOCK the validated core win (Levers A+B,
god-lane 99%→22%) FIRST — add a signals-version rebuild trigger so it ships, unit tests for
`commit_edges`/`path_edges`, verify suites. THEN do the directory super-grouping (hierarchy)
as a clean, separately-tested step. Do not fold both into one change.

Checklist:
- [x] SIGNALS_VERSION const (="2") + one-time force-rebuild when the stored version differs
      (`tree.build`, `stale_signals`). Verified: on-disk v1 tree auto-reclusters on next `sgt map`.
- [x] **Bug found + fixed (exposed by 195 leaves):** `_dedup` merged same-label *internal*
      siblings into a leaf, orphaning their subtrees and leaking internal ids into `op_leaf`
      (`KeyError: N60`, phantom feature). Its docstring said "leaves"; now enforced — only
      leaf siblings merge, internal nodes kept. Verified clean across force-rebuild→splice.
- [x] unit tests: commit_edges, path_edges (test_cluster.py), signals-version rebuild + _dedup
      leaf-only merge (test_tree.py). All green.
- [x] test fallout fixed: 12 `test_feature_verbs.py` split/merge/move tests assumed
      `mixed_coverage` splits into 2; it now (correctly) coheres into one 8-symbol leaf. Pointed
      the split-dependent ones at `linear_history` (a natural 2-way cut). Refuse-tests kept on
      `mixed_coverage`. All 26 pass.
- [x] golden `cli_surface`: diff is EXACTLY the `split_preview` groups (new signals cut the
      linear_history feature differently) — feature id + all other verbs unchanged. Regenerated.
- [x] core lens/api/map/graph-layout/tui suites green (89 tests, 0 fail).
- [x] **Hierarchy `_regroup_flat_root` DONE**: groups the flat Leiden root's children by package
      dir into synthetic subsystem nodes when arity > 9. Result on semi-git: root **138 → 36**
      (22 package subsystems + lone leaves), reads as project structure (editor/vscode, sgt/core,
      sgt/cli…). Idempotent (distinct-dir guard → no double-nest on splice); **195/195 leaf ids
      stable** (touches only internal N* nodes, never leaf identity). Unit-tested.
- [x] **Lever C label enrichment DONE**: build_map now computes per-leaf commit subjects
      (frequency-ordered) + a kind summary and passes them to `label_tree`→`_leaf_prompt` (the
      "Commit intents:" slot was previously NEVER populated). `intent` deliberately unused (empty
      on mined history). Measured: enrichment changed 134/180 labels, mostly more specific &
      capability-grounded ("Semantic API Views", "Operation Ideal Sync", "Feature Graph Panel",
      "Dependent Revert Repair"). Softened the prompt steer (entities = ground truth for what code
      IS; intents = what it was FOR) after a few labels over-indexed on commit subjects. Fallback
      path unchanged → golden (offline) unaffected.

**STAGE B STATUS: COMPLETE.** god-lane 99%→22%, flat root 138→36 packages, labels enriched +
legible, identity stable (195/195), all suites green (163-test broad run + tree/cluster/verbs +
golden). On-disk tree materialized (root arity 36, 193 leaves, v2, fully labeled).

### NOTE 2026-07-21 — finale + an incidental robustness fix
The final networked `sgt map --rebuild` **hung** on the LLM label pass (CPU flat at 26s while
wall-clock passed 5 min → network-blocked). Root cause: `config.get_client` built `OpenAI()` with
the SDK-default **600s** timeout, and the labeler's offline fallback only fires on an *exception*,
never on a hang — so a slow endpoint spins `sgt map` forever (exactly the user's earlier "spinning"
complaint, in a different spot). **Fixed**: `OpenAI(timeout=60.0)` → a stalled endpoint now raises
→ deterministic fallback. Materialized the on-disk tree via the offline fallback (`OPENAI_API_KEY=""`)
so it's valid + labeled now; nice LLM labels regenerate on the next networked `sgt map` (recluster
already done, so it's just a label pass).

Optional future tuning (not blocking): a 2nd hierarchy level if 36 top-level is still too wide;
PATH_SCALE sweep on a live-dev (small-commit) repo where co-commit coverage is high; `%ct`
per-commit timestamps (deferred — wide `gitbind.history` signature change) for a real time axis
(also feeds Stage C).

### Hierarchy fix design (do AFTER A+B is green) — `_regroup_by_package`
Problem: single-Leiden gives a flat 138-wide root (proven unavoidable by the resolution curve).
Fix: a deterministic post-clustering re-parent that groups the root's children by their dominant
top-level package into synthetic subsystem nodes — ONLY when the root arity exceeds TARGET_ARITY.
- **Safety invariant**: touches only *internal* structure — never leaf ids, members, or op_leaf.
  Internal `N*` ids are build-local and re-derived each run (already non-identity-bearing), so
  Greene identity / pins / authored features are untouched. Run it in `build` on the nested-dict
  root BEFORE `_register` (so ids/parents are assigned once, post-regroup).
- **Package key**: `_dominant_dir` already gives a 2-segment prefix; group root children whose
  dominant dir shares a top-level segment (`sgt`, `tests`, `editor`, ...). A bucket with 1 child
  stays flat (don't over-nest a lone package).
- **Depth**: inserting a level pushes leaves down; subdivide with `max_depth-1` when a regroup is
  anticipated, or just re-stamp display depth (depth is display metadata post-subdivide). Decide
  by checking what consumes `depth` (map_view/Gantt row math) — verify before implementing.
- Reflection target: Q3 (decide-from-graph) — a ~10-15 package top level is navigable; 138 is not.
- Open question to re-check against the developer mindset: is package-grouping the right top axis,
  or does it fracture a cross-package episode-feature? (A leaf spanning dirs goes to its dominant
  one; the leaf itself stays whole, so a cross-package *feature* is intact — only its shelving is
  by dominant dir. Acceptable.)

Final labels after A+B are legible revert units: "Intent DSL", "Rename Detection", "Graph
View", "Materialized Lens", "Intent Views", "Rewrite Workbench", "Chunked Sync", "MCP Tool
Server". Reflection Q1 (reason-through) is now genuinely met at the leaf level.
