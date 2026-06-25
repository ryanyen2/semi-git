# Decision Graph layout — grounded in a 5-project stress corpus

What layout best presents a decision DAG so it is **compact**, **shows where HEAD is**, and stays
**robust + intuitive** as the graph grows? This doc answers from evidence, not taste: it is driven by
`scripts/graph_stress`, which evolved five real projects (data analysis, web API, ML pipeline,
benchmark harness, CLI) through ~7 mixed lifecycle moves each — `plan` (LLM decomposition + an LLM
coding agent that actually writes the code) interleaved with `revert` / `suspend` / `restore`. The
final graphs are in `test-workspace/stress/runs/*/final_graph.json`.

## What the corpus shows

| project | decisions | lanes | orphans | max depth | edges | frontier heads | integrator sinks |
|---|---|---|---|---|---|---|---|
| web_api | 6 | 2 | 0 | 3 | revise 4, builds-on 4 | 2 | **1** |
| cli_tool | 4 | 2 | 0 | 3 | revise 2, builds-on 3 | 2 | **1** |
| data_analysis | 5 | 4 | 1 | 3 | revise 1, builds-on 3 | 4 | **1** |
| ml_pipeline | 4 | 4 | 0 | 1 | builds-on 5 | 4 | 1 (+1 minor) |
| benchmark | 6 | 6 | 2 | 1 | builds-on 4 | 6 | **1** |

Two archetypes fall out:

- **Spine** (web_api, cli_tool) — capability decisions accrete *revisions* (fold into their lane), so
  the graph collapses to **1–2 deep lanes** with **few heads**. The current git-log lane rail renders
  these beautifully. This is the shape good decomposition + folding produces.
- **Fan / star** (ml_pipeline, benchmark) — several independent leaf capabilities all feed **one
  integrator** (`train_and_evaluate`, the benchmark `runner`), at **depth 1**. The lane model gives
  each leaf its own column → a wide staircase with crossing edges and **4–6 co-equal "heads."** This
  is where the current layout fails: nothing tells the eye *what the codebase currently is*.

**The single most important finding:** every project, regardless of archetype, has **exactly one
dominant integrator** — the in-force decision with high builds-on out-degree and zero in-degree
(nothing builds on it). The frontier's 2–6 "heads" are an artifact of *one tip per lane*; the thing a
human reads as HEAD is the integrator. `benchmark` reports 6 heads but has 1 integrator (the runner);
`ml_pipeline` reports 4 but has 1 (`train_and_evaluate`).

Secondary findings:
- **Folding is the compaction lever.** web_api went 6 decisions → 2 lanes purely through `revise`
  folding (add/update/delete operations folded into the store lane). Where a fold *misses* (benchmark:
  "add memory measurement to the timer" got `provides:[measure_memory]`, a new name, instead of
  revising the timer) a lane needlessly proliferates. Fold coverage is both a generation fix and a
  layout win.
- **Orphans are leaf utilities + fold misses**, not graph bugs — a `group_and_aggregate` nobody calls
  yet is genuinely edge-less in the call graph. The layout must still place it sensibly.
- **Planner context grew 179 → ~3.7k chars** over a run (full codebase re-rendered each plan). Modest
  at this size; at scale this is the overload risk — see *Context*, below.

## The layout algorithm

Encode the same payload, but order and root it around what the evidence says matters.

### 1. HEAD is the integrator, not "every lane tip"
Define the **primary head** = the in-force decision maximizing `(builds-on out-degree, depth, landing)`
among those with zero builds-on in-degree. Pin it at the **top**, visually emphasized (filled disc +
ring + a `HEAD` chip). Other in-force tips are still in force, but rendered as ordinary lane tips, not
co-equal "heads." This directly answers "where's the head": one anchor, deterministically chosen.

`sgt.api.decision_graph_view` now emits `head` (the primary integrator id) alongside `frontier`, so
every surface agrees. When there is no integrator (pure spine), `head` is the newest in-force tip.

### 2. Dependency-major, time-minor row order
Today rows sort by landing (newest on top), with dependency depth only a tiebreak. Invert the priority
**from the head**: row order = a DFS from the primary head over builds-on/revises (head first, then the
things that feed it, transitively), with landing breaking ties. A fan then reads top-down as
"HEAD ← the capabilities feeding it" — a rooted tree — instead of N temporally-shuffled columns. Spine
graphs are unaffected (the head is already newest).

### 3. Keep lanes for identity + revise spine
A feature's lane (column) and its revise spine are the part that already works (the folding win). Keep
OKLCH hue = feature identity, status = glyph. Lane *assignment* changes only so a decision is placed
adjacent to the dependency it most directly feeds (short vertical connectors), reusing the existing
`avoidCrossings` forbidden-lane packer.

### 4. Collapse pure fans into a bus
When *k* leaf decisions feed one integrator and have no other edges (benchmark's sorts+timer → runner),
don't give each a full-height lane. Render them as a compact **feeder bus**: a short stack directly
beneath the integrator joined by one shared connector. This bounds width at `O(active spines)` instead
of `O(decisions)`, the difference between benchmark reading as 6 columns vs. 1 trunk + a small bracket.

### 5. Path routing
Edges route with the existing spear-avoidance (an edge never crosses an intervening dot; open a column
if it must). The dependency-rooted order makes most edges adjacent, so routing rarely needs extra
columns — robustness without width.

### Context (large graphs) — implemented
The planner used to re-render the whole codebase each plan (the corpus showed it climbing 179 → ~3.7k
chars over one run). `sgt/agents/plan_context.py` (`build_plan_context`) replaces that with a
**graph-driven** view: a **capability map** (the HEAD composition + the names each in-force decision
provides — O(lanes), always cheap, and it tells the planner what already exists *and what it's called*,
reducing `provides` name drift) plus **retrieved code** — entities seeded by name overlap with the
intent, expanded one hop over the call graph, rendered up to a char budget. `Orchestrator.plan` builds
it and passes it to `decompose`; offline/keyword + graph structure only, no embeddings. For small repos
the codebase fits under budget so context is unchanged; the bound bites once the tree exceeds it
(`tests/agents/test_plan_context.py::test_context_stays_bounded_as_the_codebase_grows`). Remaining
refinement: the capability map is itself O(lanes) — cap it to HEAD's transitive spines at very large
scale.

## Status
Implemented + tested:
- `_primary_head` detection + a `head` field on `decision_graph_view` (every surface agrees on HEAD).
  Verified to pick the integrator on all five corpus projects (e.g. benchmark: the runner, not the
  newest leaf revise; 6 frontier tips → 1 HEAD).
- Dependency-rooted row ordering in `computeLayout` (principles 1–2): when the payload carries `head`,
  HEAD is row 0 and its feeders nest beneath; otherwise the original newest-on-top order is unchanged
  (backwards-compatible). Validated in `tests/test_decision_layout.py`.
- HEAD emphasis in the webview (a `HEAD` chip + accent ring on the head node).

Follow-ups (designed here, not yet built):
- **Fan-bus collapse** (principle 4): render k pure leaf feeders as one bracket into the integrator
  rather than k lanes. Lane-adjacency packing (principle 3) refinement.
- **Fold coverage**: the "add memory measurement to the timer" miss (planner emitted a new `provides`
  name instead of revising the timer) — a planner-grounding fix that also shrinks lanes.
