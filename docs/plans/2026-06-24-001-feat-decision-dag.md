---
title: "feat: Decision DAG — semantic version control over decisions"
type: feat
status: active
date: 2026-06-24
origin: docs/brainstorms/2026-06-23-semantic-decision-dag.html
supersedes-ui: docs/plans/2026-06-22-001-feat-time-aware-semantic-map-plan.md (Code Map view + scrubber only; entity backend retained)
---

# feat: Decision DAG — semantic version control over decisions

## Summary

Make the unit of version control a **decision** (an intent — `Context / Decision / Consequence` plus
the alternatives weighed — realized by a typed effect transaction), and surface the project history
as a **labeled DAG of decisions laid out by time**, with the working tree materialized from a
**composition** (the frontier) rather than a single commit. Dependency between decisions is **not
authored** — it is *derived* by projecting the already-shipped entity dependency graph
(`entity_graph_view`) through each decision's code footprint. The only intrinsic edges are
versioning lineage (`revises` / `forks`). This replaces both existing graph surfaces — the **Feature
Graph** (a disconnected forest) and the **Code Map** (entity map + scrubber) — with one decision
graph where **time is a layout axis** (the tip of each feature lane is its latest decision) and
**"currently in force" is a separate channel** (glyph/brightness), which is exactly what lets a user
hold *feature-A@v3 + feature-B@latest* in one tree. `sgt` still never authors code; the LLM only
decomposes prompts, proposes reconciliations, and distills deliberation.

---

## Problem Frame

Two shipped surfaces each serve half a need and neither serves version control of *decisions*:

- The **Feature Graph** draws features as nodes with `depends_on` edges, so independent features form
  a disconnected forest; it reads as a bag of toggles, not a history.
- The **Code Map** (the time-aware semantic map) is a deterministic entity graph with a **checkpoint
  scrubber**. It is excellent for *comprehension* ("how does the code connect") but represents time
  as an off-canvas slider and has **no first-class notion of a decision, its rationale, its
  alternatives, or composing/reverting feature versions**.

What is missing is the layer the time-aware plan explicitly deferred ("an agent-facing, queryable
version of the entity/cluster graph") and rejected ("cluster-lanes as the version unit"): a
**decision/versioning graph**. The brainstorm (`docs/brainstorms/2026-06-23-semantic-decision-dag.html`,
an interactive prototype of all the operations) settled its shape. This plan builds it **on top of**
the shipped entity graph — the entity graph becomes the grounding substrate, not a competing view.

The core insight that keeps this from being "git with fancy commit messages": git fuses *time* and
*dependency* into one parent edge, forcing a single connected history with one HEAD. We split them —
**time is the layout axis, dependency is derived from the code, and HEAD is a composition** — so two
feature versions can coexist in one working tree, which git structurally cannot express.

---

## High-Level Technical Design

Three layers, each canonical for one thing, all feeding one projection:

```mermaid
flowchart TB
  log["Append-only effect log (oplog)"] -->|group entries by landing checkpoint| dl["Decision layer: Decision = intent + footprint + git txn + lifecycle pointer"]
  disk["Working tree"] -->|tree-sitter (shipped)| eg["entity_graph_view: entities + dep edges"]
  dl -->|footprint x entity dep graph| derive["DERIVED: builds-on between decisions + Consequence-clash detection"]
  eg --> derive
  dl --> proj["sgt.api: decision_graph_view / frontier ops"]
  derive --> proj
  proj --> cli["CLI --json"]
  proj --> mcp["MCP read tools"]
  proj --> vscode["VS Code Decision Graph (the only graph surface)"]
  proj --> tui["TUI"]
```

- **Effect log** stays canonical for *what changed* (unchanged).
- **Decision layer** (new) is canonical for *why / when / which version* — it groups log entries by the
  checkpoint they landed at and adds intent, alternatives, footprint, and lifecycle (`revises`/`forks`).
- **Entity graph** (shipped, retained) is canonical for *structure* — and is the source of all *derived*
  relationships (`builds-on`, Consequence-clash), so dependency is never re-authored.

The working tree is `materialize(frontier)`, where the **frontier** is a composition manifest:
one in-force decision per feature lane (default = the lane tip; pinnable to an earlier decision). This
generalizes the shipped `materialize_at(frame)` (one global frame) into `materialize(manifest)`
(per-feature selection) — the only deep backend change.

---

## Output Structure

```text
sgt/
  decisions/
    __init__.py
    model.py        # Decision, Frontier (composition manifest), lifecycle kinds
    store.py        # persistence over the oplog: group entries -> decisions; load/save frontier
    graph.py        # decision_graph_view assembly: lifecycle edges + DERIVED builds-on + clash set
  api.py            # + decision_graph_view, frontier_view; retains entity_graph_view as substrate
  orchestrate/      # + revise / fork / restore / promote / compose / tag / diff verbs (extend revert/switch/reconcile)
  cli.py            # + `decisions --json`, frontier/compose/diff verbs; REMOVE `map` + `timeframe` verbs
editor/vscode/
  src/decisionView.ts   # the single graph webview (ported from the brainstorm prototype)
  media/decision.js     # swimlane layout: x = time, lane = feature, derived builds-on faint
  media/decision.css
tests/
  decisions/ test_model.py test_store.py test_graph.py
  test_api.py (decision_graph_view shape + derived-edge parity with entity_graph_view)

# REMOVED
editor/vscode/src/graphView.ts, media/graph.js, media/graph.css     # Feature Graph (forest)
editor/vscode/src/mapView.ts,  media/map.js,  media/map.css          # Code Map view + scrubber
# package.json: drop views sgtFeatureGraph + sgtEntityMap; add sgtDecisionGraph
# RETAINED as backend substrate (no UI): entity_graph_view, materialize_at/tree_at, clustering, blame
```

---

## Requirements

**Decision model**

- R1. A **Decision** is a first-class object: `{id, feature, intent:{context,decision,consequence},
  alternatives:[{option, why_rejected, source}], footprint:[entity_id], commits:[sha], landing,
  lifecycle:{kind: introduce|revise|fork, of: decision_id?}}`. It is recovered from the effect log by
  grouping entries at the checkpoint they landed at; it adds no new authoritative copy of the effects.
- R2. The **footprint** of a decision is the set of entity IDs (from `entity_graph_view`) its effects
  create or touch, computed from blame/attribution — never from a text diff.
- R3. **Alternatives** are recorded only from witnessable deliberation (agent transcript, plan/brainstorm
  docs, the prompt exchange); each carries a `source` and a confidence marker. LLM-distilled alternatives
  are marked low-confidence; the system never fabricates rationale.

**Edges (mostly derived)**

- R4. The only **intrinsic, stored** edges are `revises` and `forks` (versioning lineage) — what the code
  graph cannot know.
- R5. `builds-on` between decisions is **derived**: decision B `builds-on` A iff some entity in B's
  footprint depends (per `entity_graph_view`) on some entity in A's footprint. It is never authored or
  LLM-labeled; it is recomputed from the substrate.
- R6. **Consequence-clash** (the old "but"/override) is **derived**: two in-force decisions clash iff their
  footprints overlap on an entity with incompatible effects. Independence ("also") is simply the absence of
  a derived edge.

**Frontier / composition**

- R7. **HEAD is a frontier**: a composition manifest selecting one in-force decision per feature lane.
  Default selection is the lane tip; any lane may be pinned to an earlier decision. The working tree is
  `materialize(frontier)`.
- R8. The frontier is a **first-class, nameable, hashable object**. Naming a frontier (`tag`) and diffing two
  frontiers at the decision level (added / revised / revoked) are supported.
- R9. Composing a frontier whose in-force Consequences clash (R6) is refused by the existing EICO confluence
  gate; the clash is surfaced, never silently merged.

**Operations** (each proven in the brainstorm prototype)

- R10. Supported decision operations: `decompose` (prompt → decisions), `revise` (+flavor refine/replace),
  `fork` (= revoke-by-alternative-line), `restore` (re-enter a revoked decision), `promote`/`merge` (fold a
  fork into a lane's canonical line, auto-retiring now-unneeded glue), `revert` (dependency-aware, see R11),
  `compose` (edit the frontier), `tag`/`diff` (R8).
- R11. **Revert is dependency-aware**: it first computes the *blast radius* by transitive `builds-on`
  traversal, then offers fork-and-re-point (default), cascade-revoke, or leave-dangling-flagged. Revoked
  decisions are never deleted — they remain fully replayable (`restore`).

**LLM as glue (graph reasoning only)**

- R12. The LLM is used only to: normalize a prompt into candidate decisions + predicted footprints + lane
  assignment; propose **reconciliation decisions** when a composition can't materialize cleanly; and distill
  alternatives (R3). It never authors feature code. With no API key, a deterministic decomposer/glue path
  runs and conflicts are surfaced for the human.

**Decomposition & lane assignment**

- R13. Lane (feature) assignment is **footprint-grounded**: a decision earns a new lane only when its
  footprint is largely disjoint from every existing lane, independently versionable, and one coherent intent;
  otherwise it `revises`/`builds-on` within an existing lane. The LLM *proposes*; the distilled footprint is
  the arbiter and may move/split a decision after code lands.

**Surfaces**

- R14. `decision_graph_view` and `frontier_view` live in `sgt/api.py`; CLI `--json`, MCP, VS Code, and TUI all
  consume them — no per-surface graph computation.
- R15. The VS Code **Decision Graph** is the single graph surface. **Time is a layout axis** (x), **lanes are
  features** (y bands), the **tip of each lane is its latest decision**, and **in-force is a separate channel**
  (bright ● vs dim ◇, ⊘ for revoked) — status is never hue (the color contract holds). Derived `builds-on`
  edges render faint; within-lane forks use commit-graph-style sub-lane routing.
- R16. The **Feature Graph** and **Code Map** views are removed; the entity backend they shared is retained
  as the grounding substrate (no UI).

---

## Key Technical Decisions

- KTD1. **One decision ↔ one checkpoint; one prompt → many decisions.** A Decision *is* the group of log
  entries that landed at one checkpoint (`landing` ordinal, already stamped) plus intent + lifecycle +
  alternatives — a projection over the log, never a second source of truth. A single user prompt decomposes
  into N decisions, i.e. N checkpoints (the `decompose` verb lands one checkpoint per decision). No manual
  squash of multiple checkpoints into one decision (resolved OQ2). This keeps blame/materialize honest.
- KTD2. **Dependency is derived, not stored** (R5/R6). This is the highest-leverage anti-vibe move: it makes
  the graph reproducible (same footprints + same entity graph → same edges) and removes a whole class of
  LLM-authored noise. Only lifecycle lineage is intrinsic.
- KTD3. **HEAD = composition manifest** (R7). Generalize the shipped `materialize_at(frame)` into
  `materialize(manifest)` where a manifest is `{feature → decision_id}`. `materialize_at(frame)` becomes the
  special case "every lane's tip as of `frame`." Reuse `build_statement_seq` so a composed tree cannot
  disagree with the live path.
- KTD4. **Time on the axis, in-force as a channel.** Unlike git (tip = HEAD always), we separate "latest in
  time" (position) from "in force" (glyph/brightness). This separation *is* compose-feature-versions; without
  it the feature is inexpressible.
- KTD5. **Multiple roots, no synthetic genesis.** `builds-on` is dependency; independent lanes need no parent.
  Real pipelines become mostly-connected naturally via shared upstream features. The graph is a DAG that may
  be a forest — connectedness is not required and disconnection is meaningful (independence).
- KTD6. **Reuse the existing verb spine.** `revert`, `switch`, `reconcile` already go through the drift guard +
  EICO gate; `revise`/`fork`/`restore`/`promote`/`compose` extend that spine rather than bypass it. No new
  mutation path escapes the gate.
- KTD7. **Remove surfaces, keep substrate.** Delete `mapView` and its assets; **replace** `graphView`
  (Feature Graph) with the Decision Graph in the same panel slot. Retain `entity_graph_view`,
  `materialize_at`/`tree_at`, clustering, and blame as headless grounding. The `map` and `timeframe` CLI
  verbs are **removed** (resolved OQ1); the retained backend is reached only via `decision_graph_view`.
  Delete a retained backend piece only if it proves to have **no remaining consumer** (truly legacy).
- KTD8. **Port the prototype.** `media/decision.js` is a direct descendant of the brainstorm HTML
  (swimlane layout, OKLCH color contract, node detail = Context/Decision/Consequence + alternatives + git txn).
  It already encodes R15; the work is wiring it to the live projection.

---

## Implementation Units

### Phase 1 — Decision model + frontier (backend, no UI)

#### U1. Decision model + store over the log
- **Goal:** Define `Decision` and `Frontier`; recover decisions by grouping log entries at their landing
  checkpoint; persist/load the frontier in `.sgt/`.
- **Requirements:** R1, R2, R7.
- **Dependencies:** none (consumes shipped oplog `landing`, `attribute.py`, `entity_graph_view`).
- **Approach:** `model.py` dataclasses; `store.py` builds decisions from `oplog` entries grouped by `landing`,
  computes each footprint from blame spans → entity IDs, and reads commit shas from the `Sgt-Node-Id` trailer
  set. Frontier persisted as `{feature → decision_id}`; default-tip resolver.
- **Test scenarios:** a checkpoint with 3 effects → one decision owning 3 commits; footprint = the touched
  entity IDs; frontier round-trips; default frontier = each lane's tip; determinism across two loads.

#### U2. `decision_graph_view` projection (with derived edges)
- **Goal:** Assemble decisions + lifecycle edges + **derived** `builds-on` + clash set + frontier into one
  pure projection.
- **Requirements:** R4, R5, R6, R8, R14.
- **Dependencies:** U1.
- **Approach:** lifecycle edges from `Decision.lifecycle`; `builds-on` derived by mapping footprints onto
  `entity_graph_view` dependency edges; clash set from footprint overlap with incompatible effects. Add
  `decision_graph_view(project)` and `frontier_view(project)`; `diff(frontier_a, frontier_b)` as add/revise/
  revoke sets. Pure over a freshly-opened `Project`.
- **Test scenarios:** A→B footprint dep produces one derived `builds-on`; independent lanes produce no edge;
  overlapping incompatible footprints produce a clash; lifecycle `forks` survives; projection shape stable.

#### U3. CLI + MCP read parity
- **Goal:** `sgt decisions --json` and a matching MCP read tool delegate to `decision_graph_view`/`frontier_view`.
- **Requirements:** R14. **Dependencies:** U2.

### Phase 2 — Operations (backend, through the gate)

#### U4. `materialize(manifest)` generalization
- **Goal:** Generalize `materialize_at(frame)` to compose an arbitrary frontier; `materialize_at` becomes the
  tip-as-of-frame special case.
- **Requirements:** R7, R9. **Dependencies:** U1.
- **Approach:** replay the effects of the in-force decisions in `order_key` order via `build_statement_seq`;
  run the EICO gate; surface clashes (R9). Characterization-cover current `materialize()`/`materialize_at`
  first so the live path is provably unchanged.

#### U5. Lifecycle verbs
- **Goal:** `revise`, `fork`, `restore`, `promote`/`merge`, `compose`, `tag`, `diff`, and dependency-aware
  `revert` (blast-radius traversal + fork/cascade/dangle choice).
- **Requirements:** R8, R10, R11. **Dependencies:** U2, U4.
- **Approach:** extend `sgt/orchestrate/` verbs; reuse `revert`/`switch`/`reconcile` plumbing and the drift
  guard. `promote` auto-retires reconciliation decisions whose clash is gone. All emit `emit_payload`
  before/after for UI dry-run.
- **Test scenarios:** the brainstorm's 10 steps as integration tests (decompose→refine→fork→compose→reconcile
  →restore→promote→blast-radius revert→tag→diff).

### Phase 3 — The Decision Graph surface (and removals)

#### U6. VS Code Decision Graph view
- **Goal:** Port the prototype to a live webview: swimlane (x=time, lane=feature), derived `builds-on` faint,
  frontier readout (`⌂ working tree = compose(…)`), node detail, operations via context menu.
- **Requirements:** R15. **Dependencies:** U3 (and U5 for action wiring).
- **Approach:** new `decisionView.ts` + `media/decision.js` descended from the brainstorm HTML; read
  `decisions --json` through the single `sgt.ts` seam; verify headless via `dev/preview.html` screenshot.

#### U7. Remove Feature Graph + Code Map
- **Goal:** Delete `graphView`/`mapView` + assets; drop `sgtFeatureGraph`/`sgtEntityMap` views from
  `package.json`; register `sgtDecisionGraph`. Retain entity backend (KTD7).
- **Requirements:** R16. **Dependencies:** U6 (don't remove the old surface until the new one renders).

### Phase 4 — LLM glue (degrades offline)

#### U8. Decomposition + reconciliation + alternative distillation
- **Goal:** Prompt → candidate decisions + predicted footprints + lane assignment (R13); reconciliation-decision
  proposals on clash (R9/R12); alternatives from witnessable deliberation (R3).
- **Requirements:** R3, R9, R12, R13. **Dependencies:** U5.
- **Approach:** mirror the existing graph-reasoning agent pattern (`sgt/agents/`, `_default_clusterer`
  offline fallback). Distilled lane assignment is reconciled against the real footprint after code lands.

### Phase 5 — TUI parity + cleanup
- TUI decision view consuming `decision_graph_view`; remove dead entity-map TUI paths if any; docs/guide update.

---

## Acceptance Examples

- AE1 (R1, R7, R15). After a prompt decomposes into embedding/retrieval/KG decisions, the graph shows three
  lanes with time on x; the rightmost ● in each lane is its latest decision; the HEAD readout lists the
  frontier. (Brainstorm step 1.)
- AE2 (R5). Adding a new "chunking" feature upstream of embedding creates a new lane *only because* its
  footprint is disjoint; embedding gets a `revises` because its footprint overlaps `embed()` — and the
  `builds-on` edges appear without anyone authoring them. (Decomposition scenario.)
- AE3 (R7, R10, KTD4). Pinning retrieval back to its original decision while KG stays at latest re-materializes
  a mixed composition with no cherry-pick and no textual merge. (Brainstorm step 4.)
- AE4 (R6, R9, R12). Composing two clashing decisions is refused; offline the clash is reported; with a key the
  LLM proposes one reconciliation decision, gated by EICO. (Brainstorm step 5.)
- AE5 (R10, R11). Reverting the embedding decision highlights its `builds-on` blast radius (retrieval) and
  forks an alternative line rather than cascade-deleting. (Brainstorm step 8.)
- AE6 (R8). `tag release-v1` then `diff release-v1 HEAD` returns decisions added/revised/revoked, not a line
  diff. (Brainstorm step 9.)
- AE7 (R16). The Feature Graph and Code Map panels are gone; the Decision Graph is the only graph surface;
  entity grounding still powers derived edges.

---

## Scope Boundaries

**Deferred to follow-up:** TUI scrubber-equivalent; large-repo footprint recompute perf; cross-feature
cherry-pick (needs a `derives-from` lineage edge); semantic rebase / squash of decisions; multi-replica
(collaborator) decision-DAG merge (the CRDT angle).

**Outside identity:** code authoring of any kind; reintroducing time-as-a-slider; a separate spatial map as a
second graph surface.

---

## Risks & Open Questions

- **`materialize(manifest)` (U4) is the riskiest unit** — it generalizes the live materialize path. Mitigation:
  characterization tests on `materialize`/`materialize_at` before changing them; reuse `build_statement_seq`.
- **Alternative capture (R3) without fabrication** — the deliberation source (transcript/plan) and confidence
  model need design; start chosen-decision-only with a structured slot, fill from witnessable sources.
- **Footprint stability across refactors** — a rename with no rename-op can fragment a footprint (known distill
  limitation); flag, don't silently mis-assign lanes.
_Resolved 2026-06-24:_
- **OQ1 → removed.** The `map` and `timeframe` CLI verbs are deleted; the entity backend stays only as
  grounding behind `decision_graph_view`.
- **OQ2 → one checkpoint = one decision.** No manual squash. One prompt decomposes into multiple
  decisions = multiple checkpoints (KTD1).
- **OQ3 → fork-and-re-point is the default revert** (no per-revert prompt); cascade/dangle remain available
  as explicit flags.

_Still open:_
- Alternative-capture source + confidence model (R3) — which deliberation artifacts, how marked.
- Footprint stability across rename-less refactors (known distill limitation) — flag vs. re-derive.

---

## Sources & Research

- Settled design + all operations: `docs/brainstorms/2026-06-23-semantic-decision-dag.html` (interactive prototype).
- Substrate retained: `sgt/entities/`, `sgt/api.py` (`entity_graph_view`, `timeframe_view`, `materialize_at`),
  `sgt/store/oplog.py` (`landing`), `sgt/store/gitbind.py` (`tree_at`, `Sgt-Node-Id`), `sgt/effects/attribute.py`.
- Verb spine + gate to extend: `sgt/orchestrate/` (`revert`/`switch`/`reconcile`, `emit_payload`), drift guard, EICO.
- Color contract: `editor/vscode/src/color.ts`, `editor/vscode/media/graph.js`, `sgt/tui/color.py`, `tests/test_color_parity.py`.
- Superseded UI: `docs/plans/2026-06-22-001-feat-time-aware-semantic-map-plan.md` (Code Map view + scrubber).
