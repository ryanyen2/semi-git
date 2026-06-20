---
title: "feat: graph-only, agent-driven sgt — persist the plan, remove code authoring"
type: feat
status: completed (Phases A–E, 2026-06-19)
date: 2026-06-19
origin: docs/design/2026-06-19-graph-only-agent-driven-sgt.md
depends-on:
  - docs/design/2026-06-18-effect-log-primary-redesign.md
---

# feat: graph-only, agent-driven sgt

## Summary

Turn sgt into **git for semantics**: the coding agent (or a human) writes the code; sgt
records, plans, and reorganizes the **semantic graph** and reconstructs the tree from it —
but never authors code. Concretely this plan (1) **persists the plan** as first-class
`PLANNED` nodes a human/agent can review before code exists, (2) adds **fulfillment**
(`checkpoint --fulfills`) that lands the agent's real edits under a planned node and flips it
`PLANNED → ACTIVE`, (3) **removes the OpenAI code-authoring backend** and the fan-out/dispatch
machinery that depended on it, and (4) makes graph operations (revert/switch/reconcile) fully
deterministic with an `--emit` dry-run escape hatch.

The LLM stays — but only to **reason about the graph** (decompose an intent into `PLANNED`
nodes, label a checkpoint), so a manual coder without an agent can still use sgt. It never
produces code.

## Problem Frame

Today code reaches the tree two ways: `sgt do` asks an OpenAI backend to *author* typed
effects, and `sgt checkpoint`/`sync` *distills* the agent's real edits. The first contradicts
the product: sgt should not be a code generator competing with the user's own coding agent.
And the plan that `sgt do` builds (the `ConstraintGraph` in `Orchestrator._fanout_or_add`,
`loop.py:166`) is **transient** — consumed during dispatch, never saved — so there is nothing
to review, share, or implement against.

The replacement already mostly exists and is backend-free: deterministic distillation
(`effects/diff.py`), the confluence gate (`engine/confluence.py`), `run_sync`
(`orchestrate/sync.py`), the planner (`agents/planner.py`), and the MCP surface
(`mcp/server.py`). The work is to **persist the planner's output**, **wire fulfillment**, and
**excise the authoring path** cleanly.

## Key Technical Decisions

- **KTD1. sgt never authors code.** The only writers of effects are (a) deterministic
  distillation of the agent's on-disk edits (`distill_codebase` → `checkpoint`) and (b) graph
  reorganization (revert/switch/reconcile re-projecting the log). The LLM is confined to
  graph reasoning (`planner`, `classifier`, `refine_split`, cluster-labeling). (origin thesis)
- **KTD2. `PLANNED` is a persisted, inert `NodeStatus`.** A planned node has
  `effect_bundle_id=None` and contributes no effects, so `active_effects()` (`project.py:159`,
  ACTIVE-only filter) and `materialize()` skip it automatically — it is safe by construction.
  It carries the planner's declared `provides`/`needs` (as node metadata) and `depends_on`
  edges (reusing the existing `DEPENDS_ON` `Edge` machinery, `graph.py:46`).
- **KTD3. Planning and implementation are decoupled; reality wins on fulfillment.**
  `sgt plan` decomposes an intent into `PLANNED` nodes and **stops**. The agent implements
  with its own tools. `sgt checkpoint --fulfills <ref>` distills the diff, lands the effects
  **under the planned node's id**, and flips it `PLANNED → ACTIVE`. The distilled effects are
  the truth; the original planned intent is preserved as node provenance (history), not
  overwritten silently.
- **KTD4. sgt owns materialization (the `git checkout` analog).** revert/switch re-materialize
  from the remaining active effects and gate before commit. `--emit` prints the semantic delta
  + witness instead of writing files, for delicate/lossy regions the agent should apply.
- **KTD5. Graph-only reconcile is re-gate, not re-author.** `attempt_rewrite_to_commute`'s
  backend call is removed. `reconcile` now re-checks whether a quarantine's *existing* held
  effects commute against current active state — they often do once the conflicting node was
  reverted/suspended — and resolves deterministically; otherwise it stays pending. No LLM.
- **KTD6. `checkpoint` is the canonical record verb; `sync` becomes a thin alias.** Both
  distill on-disk drift through the same gate; `checkpoint` adds `--intent` (declared label)
  and `--fulfills`. Keep `sync` as a deprecated alias for one release to avoid breaking muscle
  memory. (origin open-Q2)
- **KTD7. Remove the adapter contract once unreferenced.** Deleting the backend and the
  backend-coupled `Orchestrator` methods leaves `AgentResult`/`AgentStatus`/
  `CodingAgentAdapter` (and `dispatch.py`) with no production caller; remove them and the
  stub-agent tests. The only "agent" left is the *external* coding agent, which talks to sgt
  via CLI/MCP — not via a Python Protocol.
- **KTD8. No on-disk migration.** `NodeStatus` defaults to `ACTIVE` on load (`graph.py:86`)
  and `PLANNED` is purely additive; removing the backend does not touch the `.sgt/` format.
  Existing projects load unchanged.

---

## Phases

Ordering rationale: A and B are **additive** — they leave the existing backend working, so the
plan→implement→checkpoint replacement is proven *before* C deletes anything. C is the
destructive cut. D refines graph ops. E cleans up.

### Phase A — Persist the plan (`PLANNED` nodes + `sgt plan`)

Additive; the backend still works after this phase.

1. Add `PLANNED = "planned"` to `NodeStatus` (`store/graph.py:26`). Confirm `active_effects`
   and `materialize` exclude it (they filter on `ACTIVE`, so this is free).
2. Extend `Node` with declared-edge metadata: `provides: list[str]`, `needs: list[str]`
   (default `[]`), persisted through `to_dict`/`from_dict` (`graph.py:69-90`).
3. `Project.add_plan(nodes, edges)` — persist `PLANNED` nodes + their `DEPENDS_ON` edges to
   `graph.json`/`order`, **without** appending to the effect log (mirror `add_feature`,
   `project.py:182`, but skip the effect-append and `effect_bundle_id` set).
4. `Orchestrator.plan(intent)` — call `decompose()` (`agents/planner.py:61`), map each
   `SubTask` → a `PLANNED` `Node` (kind from a lightweight classify or default CAPABILITY),
   resolve `depends_on` keys → node ids → `DEPENDS_ON` edges, persist via `add_plan`, commit.
5. CLI `sgt plan "<intent>" [--yes]` and MCP `sgt_plan` tool. `sgt graph`/`sgt show` already
   render status + edges; verify `[planned]` shows.

**Verify:** `sgt plan` on a multi-part intent writes N reviewable PLANNED nodes with edges;
`sgt status`/`materialize` unchanged (planned nodes contribute nothing); revert of a PLANNED
node removes just it.

### Phase B — Fulfillment (`checkpoint --fulfills`) + verb unification

Still additive (backend untouched). **Revised after the Phase A review + reading
`sync.py`/`distill.py`:**

- **R1 (provenance is a real field).** Add `provenance: list[str]` to `Node` (persisted). On
  fulfill, push the planned intent there and adopt the declared intent — reality wins (KTD3)
  without losing the plan.
- **R2 (`fulfill` joins `order`).** Planned nodes are not in `self.order` (Phase A note 2), so
  `fulfill` must append the node to the replay order — same as `resolve_quarantine`
  (`project.py:238`).
- **R3 (`--fulfills` lands the *whole* current drift under one node).** It bypasses clustering
  entirely: the contract is *implement and checkpoint one planned node at a time*. The distilled
  drift since the last checkpoint becomes that node's effects.
- **R4 (`_land` is status-aware).** A target that is `PLANNED` routes to `fulfill`; an `ACTIVE`
  target keeps the existing `extend_feature` path. Reuses `_land`'s gate + quarantine logic
  (`sync.py:56`).

Steps:

1. `Project.fulfill(node_id, effects, intent=None)` — set `effect_bundle_id`, append effects to
   the log, flip `PLANNED → ACTIVE`, append to `order` (R2), stash planned intent in
   `provenance` + adopt declared intent (R1), infer DEPENDS_ON edges.
2. `run_sync(..., fulfills=None, intent=None)` — when `fulfills` resolves to a node, build a
   single synthetic `Cluster(target=fulfills)` over all distilled effects (R3) and skip the
   clusterer; otherwise unchanged. `_land` gains the PLANNED→fulfill branch (R4) and reports via
   a new `SyncReport.fulfilled` list.
3. CLI `sgt checkpoint [--yes] [--intent "..."] [--fulfills <ref>]` (canonical), reusing the
   deterministic-clusterer + declared-intent pattern from MCP `tool_checkpoint` (`server.py:107`).
   `sgt sync` stays as a thin no-intent alias.
4. MCP `sgt_checkpoint` gains optional `fulfills`.

**Safety surfaced by review (correct behavior, documented not fixed):** fulfilling a node whose
distilled code references a *not-yet-implemented* planned dependency fails name resolution at the
gate, so those effects are **held/quarantined** and the node stays `PLANNED` — the user is told
which reference is unresolved. This is the gate doing its job, not a bug.

**Deferred (noted, not built in B):** auto-matching a checkpoint's provided names to a `PLANNED`
node's `provides` (auto-fulfill without `--fulfills`). Risks mis-firing; `--fulfills` stays
explicit for now.

**Verify:** plan → write the files implementing one planned node → `sgt checkpoint --fulfills
<ref>` lands effects under that node, flips it ACTIVE, gate passes, re-materialize reproduces the
edit; `sgt show` still shows the planned intent in provenance; fulfilling out of dependency order
holds the effects with a clear witness.

### Phase C — Remove code authoring  *(re-evaluated after A+B)*

The destructive cut, now that A+B replace it. Four revisions from reading the code:

- **C-rev1: the `agent` param is removed *entirely*.** Once the backend methods go, nothing in
  `Orchestrator` calls an agent — so `agent`, `confirm`, `refine_splitter`, `max_workers`,
  `rewrite_attempts` all drop. The constructor becomes `(project, repo_path=".",
  decomposer=decompose, force=False)`. This cascades cleanly into CLI/MCP `_orchestrator`
  (no `OpenAICodingAgent` construction at all).
- **C-rev2: reconcile's re-gate (was Phase D/KTD5) folds into C.** Removing the backend *breaks*
  `reconcile` (it called the agent via `attempt_rewrite_to_commute`), so the agent-free re-gate
  must land here to keep the suite green. New `attempt_recommute(project, node_id)` re-gates the
  quarantine's *existing* held effects (from `project.bundles[node_id]`) against current active
  state; commutes ⇒ `resolve_quarantine`. No `SubTask`, no agent.
- **C-rev3: delete `classifier.py` + `refine_split.py`.** They were authoring *routers* (lane
  classification, compound-refine splitting) with no graph-only caller once `ingest`/
  `_refine_fanout` are gone. The manual-coder LLM uses are `planner` (→ PLANNED) and `distill`
  (→ checkpoint labels) — those stay. (Corrects the design doc, which listed classifier/
  refine_split as "kept".)
- **C-rev4: bare `sgt "<intent>"` re-points to `plan`.** The freeform front door no longer
  authors; the sensible default is "decompose into a reviewable plan." `ingest` and `sgt do`/
  `sgt modify` are removed.

Steps:

1. Delete `sgt/adapter/` wholesale (`openai_agent.py` + `base.py` + `__init__.py`): the contract
   (`AgentResult`/`AgentStatus`/`CodingAgentAdapter`) has no remaining production caller after
   the cut.
2. Delete `sgt/orchestrate/dispatch.py`.
3. Rewrite `loop.py`: keep `Report`, `Orchestrator.__init__` (C-rev1), `_guard` (message now
   says `sgt checkpoint`), `plan`, `revert`, `switch`, `reconcile` (C-rev2). Delete `ingest`,
   `_run_agent`, `_add`, `_extend`, `_fanout_or_add`, `_run_plan`, `_resolve_or_quarantine`,
   `_refine_fanout`, `modify`, `_quarantine_held`, `_merge_reports`, `_resolve_deps`, `_desc`.
4. Rewrite `orchestrate/quarantine.py` → `attempt_recommute` (C-rev2).
5. Delete `sgt/agents/classifier.py` + `sgt/agents/refine_split.py` (C-rev3).
6. CLI: remove `do`/`modify` verbs + `confirm_plan`; bare arg → `plan` (C-rev4); drop the agent
   from `_orchestrator`. MCP: drop the agent from `_orchestrator`.
7. Tests: delete `test_dispatch.py`, `test_loop.py` (all authoring), `test_quarantine.py`
   (rewrite-to-commute) → replace with a small `test_recommute.py`; fix `test_sync.py` +
   `test_plan.py` to drop the now-removed `agent` constructor arg.

**Verify:** full suite green; `grep` finds no `adapter`/`dispatch_layer`/`execute_task`/`ingest`
references; `sgt plan`/`checkpoint`/`revert`/`switch`/`reconcile` run with **no `OPENAI_API_KEY`**
except where graph-reasoning LLM calls happen (`plan` decompose, `checkpoint` LLM labeling).

### Phase D — `--emit` dry-run on graph ops

(Reconcile's re-gate moved to C-rev2; D is now purely additive.)

1. Add `--emit` to `revert`/`switch` (CLI + `Orchestrator`): compute the target delta + witness
   and return/print it instead of writing the tree and committing.

**Verify:** `sgt revert <ref> --emit` prints the semantic delta and leaves the tree untouched.

### Phase E — Docs, tests, cleanup

1. Update `README.md` and `FINDINGS.md` (new workflow, removed authoring, `--emit`, safety
   section). Update `scripts/` (drop `e2e_smoke`/`e2e_modify`/`e2e_fanout` authoring scripts;
   add an `e2e_plan_checkpoint.py` walking plan → edit → checkpoint → revert).
2. Refresh memory: the `agent-integration-direction` note now describes plan+checkpoint as the
   spine; add a note for the graph-only pivot.

---

## Risks & Mitigations

- **Distillation fidelity on materialize.** Replaying effects normalizes layout between units
  and can drop non-distillable regions. *Mitigation:* the gate already refuses un-materializable
  states; `--emit` hands lossy regions to the agent (KTD4). Tracked, not blocking.
- **Behavioral (non-syntactic) dependencies on plug-out.** The gate is structural; a runtime-only
  dependency won't be caught. *Mitigation:* declared `needs`/`provides` edges from plans capture
  intent the call graph can't see; document the boundary (design doc §Safety); tests-as-invariant
  is a future knob.
- **`--fulfills` divergence (plan ≠ reality).** *Mitigation:* reality wins for the active record;
  planned intent kept as provenance (KTD3) so nothing is lost.
- **Hidden backend coupling in tests.** The map flags stub-agent usage in `test_sync.py`. *Mitigation:*
  Phase C verifies `run_sync` needs no agent before deleting the contract.

## Test Strategy

- **Phase A/B unit:** PLANNED persistence + load round-trip; `materialize` ignores PLANNED;
  `fulfill` flips status and lands effects under the same id; `--fulfills` routing in `_land`.
- **Phase C:** suite stays green after deletions; a `grep` guard test (no `execute_task`).
- **Phase D:** deterministic reconcile (held set commutes after rival removed); `--emit` writes
  nothing.
- **E2E:** `scripts/e2e_plan_checkpoint.py` — plan → manual edit → checkpoint --fulfills →
  revert closure, asserting valid() throughout. (Plan step uses the graph-reasoning LLM; the
  rest is deterministic/offline.)

## Open Questions (carried from design doc, resolved here)

- Q2 sync vs checkpoint → **KTD6** (checkpoint canonical, sync alias).
- Q3 PLANNED storage → **KTD2** (status + node metadata + reused DEPENDS_ON edges).
- Q4 fulfills reconciliation → **KTD3** (reality wins; plan kept as provenance).
- Q5 migration → **KTD8** (none needed).
- Remaining: should `ingest`/`sgt do` survive as a *router* (classify → plan or answer) or be
  removed outright? Leaning remove (explicit `sgt plan`); confirm during Phase C.
