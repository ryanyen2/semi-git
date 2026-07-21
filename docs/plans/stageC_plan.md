# Stage C — episodic lens, operation legibility, cross-surface parity (living plan)

> Living document (same discipline as `stageB_plan.md`). Design-think FIRST — the user's
> explicit ask: *"first think about what informations required to be encoded in our
> visualization."* Each session appends a dated NOTE. Do not treat as a fixed spec.

## The developer problem this must solve

Stage B made features legible **spatially** — the map now says *what the codebase is made
of* (god-lane 99%→22%, 36 readable packages, capability labels). But the user's standing
crux (round 3) is temporal/agentic:

> *"When I saved changes I remember I changed code here and there — not 'feature cluster
> #3.' Reverting a feature is illegible: I don't know where it lands."*

And now (round 5), the operation-choosing frame:

> *"there are many constraints — intent clustering, hierarchical structure, dependencies
> across features — that matter when the user chooses a certain operation. The interface
> should be informative about the action: a preview of the action, the action processing,
> and the after. Design the agentic-coding workflow: plan, `sgt revert "…desc"`, restore at
> intent level. TUI has multi-select — what about VS Code? Let users see their effects,
> where they are in the visualization."*

So Stage C is not "another view." It is: **make the act of choosing and running an
operation legible — before / during / after — on both surfaces, with the dependency and
episode structure visible exactly at decision time.**

## STEP 0 — What must the visualization encode? (the foundation)

A developer standing in front of the graph is about to choose an operation (revert /
restore / plan). To decide well they need, in rough priority:

| # | Information | Why it's needed to choose an op | Where it lives today |
|---|---|---|---|
| 1 | **Feature identity + label** | "what is this thing" | Stage B labels ✓ (`map_view`) |
| 2 | **Magnitude** (op count) | "how big is the undo" | Gantt disc/heatstrip ✓ |
| 3 | **Temporal extent** (born→last, episodes) | "when did I do this / is it recent work" | Gantt x-axis ✓ (composition), but no *episode* grouping |
| 4 | **Hierarchy** (subsystem→feature→op) | "where does it sit" | Stage B regroup + swimlanes ✓ |
| 5 | **Dependencies across features** (blast/carry/foundation) | **"if I undo this, what else falls?"** — THE decision input | `verb_preview_view` computes it, but only *after* you pick a target; on the graph it's hover-only co-change (not the revert closure) |
| 6 | **Action lifecycle** (preview → processing → after) | "what exactly will happen, is it happening, did it work" | `verb_preview_view` has files{before,after}+frontier+affected; **no processing/after state in the graph itself** |
| 7 | **Where am I** (frontier/HEAD, selection, working drift) | "what's my current position and pending intent" | frontier veil ✓; selection = TUI only; drift = quick-save card ✓ |
| 8 | **Episode/intent** ("the thing I was doing") | "rewind what I *did*, in order" | **missing** — see the episode note below |

### The episode problem (a Stage-B fact that reshapes Stage C)

Stage B proved `Attribution.session` is **empty on all mined history** (sessions are only
stamped by sgt's own land/checkpoint going forward). So an "episodic lens" **cannot** be
built on `sessions_view` for historical work. On mined history the only episode signal is
**co-commit** (`provenance` SHA) — which Stage B already computes as `commit_edges`. So:

- **Episode on mined history = a commit (or a co-commit cluster).** Reuse Stage B's signal.
- **Episode going forward = a real sgt session** (`sessions_view`), which *is* a true
  "afternoon's work" once the user drives sgt with `plan`/`checkpoint`.

This means the episodic axis is a **projection we already have the data for** — no new
mining, no `%ct` timestamp signature change required for a first cut (`commit_index`
orders episodes; provenance groups them).

### The gap, stated as the Stage-B-style "measurement"

Current surfaces encode #1–4 and #7-partial well. They **do not** encode, at decision time:
- **#5 dependency-as-revert-closure** on the graph (only hover co-change, which is a
  *different* relation than blast/carry/foundation).
- **#6 processing/after** — the graph is static; running a verb doesn't animate to its result.
- **#8 episode ordering** — "what I did, in order" has no view.
- **#7 selection** in VS Code (TUI-only multi-select).

Stage C closes exactly these four gaps. Nothing else is Stage C work.

## The reflection questions (re-ask every session)

1. **Reason-about-the-op**: from the graph, before committing, can the developer see *what
   the operation will do and what it costs* (blast radius, affected features, resulting diff)?
2. **In-the-loop**: is the action legible while it runs and after — preview → processing →
   after — so they trust it and see their effect?
3. **Where-am-I**: can they always see their current position (frontier), their pending
   selection/intent, and their working drift?
4. **Parity**: do TUI and VS Code offer the same power (multi-select, preview, revert-by-
   intent), each idiomatic to its surface?
5. **Smooth at scale**: does the view stay responsive on the real 36×195 tree (lazy /
   staged layout), and readable (shared spines drawn once)?

## The layout-algorithm mapping (user's ask: CSE / stage-chaining / lazy eval)

The user asked to compact the tree/graph with compiler-style techniques for smooth,
informative UX. Concretely, in layout terms:

- **CSE (common-subexpression elimination) → shared-spine collapse.** When many features
  build on one common foundation, draw that shared dependency *once*, not as N redundant
  edges. The orphaned `decision.js` rail engine already does the two moves this needs:
  **transitive reduction** of the builds-on DAG (drop A→C when A→…→C exists) and the
  **fan-bus collapse** (many pure-leaf feeders of one integrator share a single bus lane,
  width O(spines)+1). Reuse that engine for the dependency overlay.
- **Stage chaining → memoized layout pipeline.** Split layout into independent stages:
  `order → span → lane-pack → edge-route → veil`. A change that touches only a later stage
  skips the earlier ones. The current Gantt already moves the frontier veil in O(1) with no
  relayout — that is exactly this principle; extend it so scrub/select/hover never trigger a
  full recompute, only their own stage.
- **Lazy evaluation → don't lay out what isn't shown.** Collapsed subsystems are not laid
  out until expanded; off-viewport lanes are culled. Keeps 36×195 smooth. (`computeGraph
  Layout` currently lays out everything; make sublayout on-demand per expanded swimlane.)

These are *rendering-smoothness + readability* levers, applied on top of the existing pure
`computeGraphLayout` / `computeLayout` functions (both already pure & node-testable).

## The action lifecycle (gap #6 — the heart of "informative about the action")

One consistent 3-phase contract across both surfaces, driven entirely by data we already have:

1. **PREVIEW** (`verb_preview_view`): highlight the target feature + its blast/carry/
   foundation closure on the graph (dim the rest); side panel shows affected features, op
   deltas, and the resulting unified diff (files{before,after}). *Reason-about-the-op.*
2. **PROCESSING**: on confirm, the affected lanes animate (the closure "lifts"); a
   determinate indicator while the verb runs. *In-the-loop.*
3. **AFTER**: the graph settles to the new composition; a transient "what changed" summary
   (N ops removed, features re-drafted) + one-click undo. *See your effect.*

Preview data exists (`_project_verb_preview`). Stage C adds the *staging of it on the
graph* and the *processing/after* states — not new backend compute.

## The agentic-coding workflow to design

- **Plan**: user drives Claude Code to run `sgt plan` (draft intent → hollow features).
  The graph should show a *planned* (not-yet-fulfilled) feature distinctly (dashed/ghost),
  so the developer sees intent before code exists. (Prior art: unanchored dashed ring.)
- **`sgt revert "…description"`**: NL-intent revert already resolves via `_resolve_via_
  intent`. The surface should let the user *type an intent* and see the preview closure —
  the intent-level restore/revert, not op-id picking.
- **Restore at intent level**: same pathway, forward (restore a reverted episode).
- The through-line: the developer (or their agent) expresses **intent**; sgt shows the
  **preview closure**; they confirm; the graph shows **after**. This is the on-ramp in
  [[agent-integration-direction]].

## Cross-surface parity + presence (gaps #7 VS Code, #8)

- **VS Code multi-select**: the TUI has space-to-select + a blast/carry/foundation
  checklist (`app.py`). VS Code has none. Add multi-select on the Gantt lanes (click/
  shift-click/⌘-click) feeding the same `selection_specs`→`resolve_selection` closure the
  TUI uses, then the same preview lifecycle. Same backend, idiomatic input per surface.
- **"Where am I" presence** (both surfaces): a persistent, legible current-position readout
  — frontier index, current selection + its live closure count, working-tree drift — so the
  developer never loses their place. TUI can strengthen its header; VS Code adds a status band.
- **Episode rail (#8)**: revive `decision.js`'s vertical git-log as the "what I did, in
  order" lens — rows = episodes (co-commit clusters on mined history / sessions going
  forward), newest on top; lanes = features (interval-colored, shared spines via the CSE
  collapse above); this is the rewind view the Gantt's composition-view complements.

## Genuine design forks (surface to the user before building — §2/§10)

- **F1 — primary deliverable.** (A) operation-aware Gantt overlay [preview/processing/after
  + dependency-at-decision-time + VS Code multi-select], vs (B) revive the vertical episode
  rail as a distinct view, vs (C) both. Recommendation: **A first** (it directly closes the
  highest-priority gaps #5/#6/#7 on the base the user already likes), then B as the episode
  lens once A is solid. Sequenced, not simultaneous (anti-runaway, §10).
- **F2 — episode definition on mined history.** co-commit cluster vs single commit vs
  commit_index-window. Recommendation: **single commit = one episode row**, co-commit
  cluster = a collapsible episode group (reuses Stage B's `commit_edges`); real sessions
  supersede this going forward.
- **F3 — VS Code multi-select interaction.** lane click-to-select vs a side checklist
  mirroring the TUI. Recommendation: **lane click/⌘-click on the graph** (spatial, matches
  the Gantt), feeding the same closure the TUI checklist shows.

## LOCKED decisions (user, 2026-07-21)

- **F1 = BOTH** — operation-aware Gantt overlay AND the vertical episode rail. (I still
  *order my own work* so each piece is independently testable — that's verifiability, not
  a scope change; the delivered set is both.)
- **F2 = commit is one episode, co-commit cluster is a collapsible episode group.** Reuses
  Stage B's `commit_edges` grouping. Real sgt sessions supersede this going forward.
- **F3 = lane click / ⌘-click / shift-click on the graph** → the same
  `selection_specs`→`resolve_selection` closure the TUI uses → the preview lifecycle.

## Plan of attack (smallest informative step first — revise freely)

- [x] Step 0 — enumerate what must be encoded + locate each in the code (above).
- [x] Step 1 — lock F1/F2/F3 with the user (done — BOTH / commit-episode / lane-click).
- [x] **Step 2 — episode projection (shared substrate, pure + tested).** DONE. No backend
      change needed: `history_view(full=True)` already carries commits (sha/subject/index)
      + ops (id/kind/feature_id/commit_index), so the rollup is a pure client-side function
      over data both surfaces already fetch. Built as a mirrored pair (repo precedent:
      Gantt is JS `computeGraphLayout` ↔ Py `graph_layout`): `rollupEpisodes` in
      `workbench.js` + `episodes()` in `sgt/tui/graph.py`. Contract: one episode per commit
      that carried ops (ops sharing a `commit_index`), `dominant_feature` = most-touched
      feature (ties → larger id), episode-groups keyed by dominant feature (the collapsible
      co-commit unit) ordered by first appearance, with op-ids/kinds/subject carried so an
      episode is an actionable revert unit. Parity locked by identical contract tests
      (`tests/tui/test_episodes.py` + `tests/test_episodes.py`, 10 green).
- [x] **Step 3 — dependency-closure overlay (PREVIEW phase of the lifecycle).** DONE.
      Found the hover-preview lifecycle already exists (`previewVerb`→`previewResult`→
      `paintBlast`) but it lumped ALL affected features into one uniform amber, discarding
      the role distinction the preview already computes. Note: the *feature*-level preview
      (`feature_verb_preview_view`, what the graph hovers) carries `affected` rows with
      `direction` ∈ {blast=losing ops, foundation=gaining re-drafted ops} — NOT the op-level
      blast/carry/foundation frontier (that's only in the op/symbol `verb_preview_view`; a
      feature revert removes a whole op-set, so it has no single-op dependent frontier — the
      god-cluster note). So at feature granularity the honest three roles are **target /
      collateral-blast / re-draft-foundation**, matching `sgt revert`'s terminal language.
      Shipped: pure `classifyAffected(result, targetId)` (sliced, node-tested) + `paintClosure`
      applying `.ghost-target`/`.ghost-blast`/`.ghost-foundation` (green `--land` for re-draft)
      + off-screen pills over all roles. Now a Revert hover reads "this one, these lose ops,
      these get re-drafted" instead of one amber blob. Tests: `tests/test_closure.py` (3),
      DOM smoke green, tsc+esbuild clean.
      DEFERRED to a later step: the PROCESSING + AFTER phases (animate the closure lifting on
      apply, settle to the new composition + "what changed" summary) — apply currently just
      invalidates→full re-render. Scoping those with the rail work so the animation is designed
      once for both views.
- [x] **Step 4 — VS Code lane multi-select → union closure.** DONE. ⌘/ctrl/shift-click
      accretes a set of lanes (plain click = single-select toggle, clears the set) — the VS
      Code parallel of the TUI's space-select. `state.multi` holds the set; a ≥2 selection
      takes over the inspector with a **union-closure card** ("N features → M ops in closure",
      the OTHER features it pulls in, hub warning, deselectable chips) fed by `sgt select`
      (`selection_view`, report-only) via a new `selectClosure`→`selectionResult` round-trip.
      `paintSelectionClosure` ambers the pulled-in features so "where this selection lands" is
      visible. **Revert all** mirrors the TUI (which also applies per-feature): the host reverts
      each ref in turn (re-resolving via mine-on-contact), STOPPING on the first refusal — one
      confirm up front. Stale selections pruned on each fresh state. New store method
      `Sgt.select` + `SelectionView` type. tsc+esbuild clean, DOM smoke green.
- [x] **Step 5 — the episode rail (vertical git-log), both surfaces.** DONE. The compaction is
      greedy interval-graph lane coloring: a feature's episodes are one column; lanes are reused
      across non-overlapping row-spans — that shared-lane reuse IS the CSE the user asked for (on
      this repo 28 features → 4 lanes). NOTE on `decision.js`: its fan-bus + transitive-reduction
      target a *dependency DAG* we don't have at episode granularity (episodes are a per-feature
      time sequence, a spine not a fan), so the honest realization is the focused interval-coloring
      packer, not the full decision engine. The fan-bus CSE applies once real feature-dependency
      edges exist (tied to the deferred operation-DAG promotion). Mirrored JS↔Py like the rollup:
      `episode_rail_layout` (Py) ↔ `episodeRailLayout` (JS), parity-tested (`tests/test_rail.py`
      + `tests/tui/test_episodes.py`). Surfaces: `sgt episodes` CLI (fast cached read) + `render_
      rail_lines`; TUI `e`/`EpisodeScreen`; VS Code `renderRail` + a titlebar Timeline⇄Rail toggle
      (feature spines + dots, click selects the episode's feature → same revert/preview/multi-select
      path). tsc+esbuild + DOM smoke + 21 JS-slice tests + tui/golden all green.
- [x] **Step 6 — presence / "where am I" (both surfaces).** DONE. VS Code: a persistent
      `#presence` footer band — composition · view (timeline/rail) · current selection + its
      live closure op-count · scrub position · uncommitted-work count — always visible, updated
      on render, selection-closure, and scrub. TUI: the existing `#status-line` gains a "▸ N
      selected" segment, re-rendered live on space-select (cached `_last_status`). So neither
      surface loses the developer's place.
- [x] **Cleanup + docs.** DONE — see the final NOTE.

## Action lifecycle: what shipped vs. deferred (honesty note)
The user asked for preview → processing → after. What ships:
- **PREVIEW**: the three-role closure overlay (Step 3) + the multi-select union closure (Step 4)
  + the terminal/`--emit` diff (Stage A). Legible before committing. ✓
- **PROCESSING**: VS Code's native modal confirm + the subprocess run (revert commits); the
  "Revert all" batch reports progress/stops-on-refusal. Functional, not a bespoke graph animation.
- **AFTER**: `store.invalidate()` → re-render to the new composition + an info toast ("Reverted N
  feature(s)") + `sgt undo` available. Functional.
- **DEFERRED polish**: a bespoke on-graph animation (closure "lifts", graph settles) — nice-to-have,
  not required for legibility; noted for a future pass.

## Session notes

### NOTE 2026-07-21 — Stage C kickoff, design reflection (Step 0)
Committed Stage B milestone (`899061e`). Read the prior art in full: orphaned
`editor/vscode/media/decision.js` (a complete, pure, node-tested vertical git-log lane
engine — transitive reduction + interval-coloring + overprint avoidance + fan-bus collapse
+ island gutter; currently a test fixture only), the current Gantt (`workbench.js`
`computeGraphLayout`, TUI `sgt/tui/graph.py`), the verb/preview API (`verb_preview_view`,
`_project_verb_preview`, `_frontier_rows` blast/carry/foundation, `resolve_selection`,
`sessions_view`, `forks_view`), and the TUI multi-select (`app.py` space-select + frontier
checklist). Key reframing fact carried from Stage B: **sessions are empty on mined history**,
so the episodic axis must be projected from co-commit/`commit_index`, not `sessions_view`.
Design captured above. Next: lock F1/F2/F3 with the user, then Step 1.
