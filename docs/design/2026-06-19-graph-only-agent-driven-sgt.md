---
date: 2026-06-19
topic: graph-only, agent-driven sgt — sgt as semantic version control the coding agent operates, never as a code author
status: design / ADR
supersedes-decisions:
  - core-plan (sgt do delegates intent → typed effects to an OpenAI coding backend)
  - fanout-plan (a fan-out layer dispatches sub-tasks to the coding backend and auto-implements them in parallel)
  - fanout-plan (rewrite-to-commute re-asks the coding backend to re-author held effects)
  - core-plan (the constraint graph / plan is a transient in-memory object, never persisted)
origin: this conversation (2026-06-19); builds on docs/design/2026-06-18-effect-log-primary-redesign.md
---

# Graph-Only, Agent-Driven semi-git

## Why this doc exists

semi-git currently has two ways code gets written: the CLI path (`sgt do "..."`) hands an
intent to an **OpenAI coding backend** that authors typed effects, and the MCP path lets an
external coding agent edit files itself and then `sgt_checkpoint` to record them. The first
makes sgt a *code generator*; the second makes it a *recorder*. This doc commits fully to the
second and removes the first.

The product is being restated: **sgt is git for semantics.** It does not write your code — the
coding agent (Claude Code, or any agent the user already runs) writes the code with its own
tools, exactly the way it already shells out to `git`. sgt's job is to let the user and their
agent operate at the **feature/concept level**: read the semantic graph, optionally plan it,
record what was actually built, and plug features in and out — never to author logic.

## Thesis

> **sgt manipulates the semantic graph and reconstructs the tree from it; it never authors
> code.** The coding agent is the only writer of new logic. sgt uses an LLM only to reason
> *about the graph* (decompose a plan, label a change), never to *produce code*. The plan is a
> first-class, persisted, reviewable artifact (`PLANNED` nodes), not a transient object. Two
> entry points — plan-first and code-first — converge on the same durable `ACTIVE` nodes. Every
> graph operation is gated so it can never emit code that fails to resolve.

The test that defines the boundary: *does the operation invent logic that wasn't there?* If yes,
it's authoring and belongs to the coding agent. If it only reproduces previously-recorded state
(materialize on revert/switch) or reasons about meaning (plan, label), it's sgt's job.

## The two senses of "touch the code" (the core distinction)

| Action | Example | Verdict |
|---|---|---|
| **Author** — generate new logic from intent | `sgt do` → OpenAI → new function | ❌ removed; the coding agent does this |
| **Reconstruct** — replay recorded effects to rebuild the tree | `sgt revert X` re-materializes | ✅ kept; this is the `git checkout` analog |
| **Reason about the graph** — decompose / label / classify | `sgt plan` → `PLANNED` nodes | ✅ kept; manipulates meaning, not code |
| **Distill** — read the agent's diff into typed effects | `sgt checkpoint` → effects | ✅ kept; reads code, invents nothing |

`git checkout` rewrites your working tree and no one says git "touches your code," because it
reconstructs recorded state rather than inventing logic. That is exactly the line we draw:
authoring is forbidden, reconstruction is the job.

## What gets removed, kept, and reshaped

**Removed (code authoring):**
- `sgt/adapter/` wholesale (`openai_agent.py` + `base.py` + the contract) — the coding backend.
- `sgt/orchestrate/dispatch.py` and the thread-pool fan-out that dispatches sub-tasks to the
  backend to be implemented in parallel.
- `attempt_rewrite_to_commute`'s **re-author-via-backend** behavior in
  `sgt/orchestrate/quarantine.py` → replaced by an agent-free `attempt_recommute` (re-gate the
  existing held effects).
- `sgt do "<intent>"` / `sgt modify` as code-writing verbs; the `ingest` front door.
- **`sgt/agents/classifier.py` + `sgt/agents/refine_split.py`** — authoring *routers* (lane
  classification, compound-refine splitting) with no graph-only caller. (Revises the line below:
  these do **not** survive; only `planner` + `distill` are the kept graph-level LLM uses.)

**Kept intact (the version-control core):**
- The effects model (`sgt/effects/`), the confluence gate (`sgt/engine/confluence.py`),
  materialization, dependency inference and closure, and quarantine-as-status.
- The MCP server (`sgt/mcp/server.py`) and the graph verbs.
- Deterministic reverse distillation (`sgt/effects/diff.py`), including rename detection.

**Kept but graph-only (LLM reasons about meaning, never code):**
- `sgt/agents/planner.py` — decomposes an intent into a constraint graph. Now it **persists
  `PLANNED` nodes and stops**; it no longer feeds a coding backend.
- `sgt/agents/distill.py` — labels/clusters the distilled drift for a checkpoint. It touches no
  code (and degrades to deterministic grouping with no API key).

> **Confirmed (2026-06-19):** keep graph-level LLM reasoning. Rationale: a user coding
> manually — *without* a coding agent — can still use sgt, and the LLM lets them decompose an
> intent into `PLANNED` nodes and auto-label a checkpoint instead of hand-authoring every node.
> The LLM reasons about the graph; it still never authors code. Only `planner` + `distill` stay;
> the authoring routers `classifier`/`refine_split` were **removed** (see "Removed" above).

## Node lifecycle

```
PLANNED ──(agent implements + checkpoint --fulfills)──▶ ACTIVE
   │                                                      │
   └──(abandoned / rejected at review)──▶ deleted         └──(conflict on land)──▶ QUARANTINED
```

- **`PLANNED`** — tentative, reviewable, *no code yet*, **not gated** (it's a proposal). Carries
  declared `needs`/`provides` edges from the planner. Written to `.sgt/` so a human or another
  agent can review or amend it before any code exists.
- **`ACTIVE`** — implemented, distilled into effects, gated, recorded. The durable record.
- **`QUARANTINED`** — landed effects that don't commute; excluded from materialization, carries a
  witness. (Unchanged from today.)

`PLANNED` is the one new persisted status; today the plan is the transient `ConstraintGraph` in
`Orchestrator._fanout_or_add` (consumed during dispatch, never saved).

## The two entry points converge

Planning is an **optional precursor**, never a gate:

- **Plan-first / collaborative:** `sgt plan "..."` → `PLANNED` nodes → review/amend → agent
  implements with its own tools → `sgt checkpoint --fulfills <node>` flips `PLANNED → ACTIVE`,
  reconciling the planned intent with what was actually built (reality wins).
- **Code-first:** the user/agent just edits → `sgt checkpoint` distills reality straight into
  `ACTIVE` nodes (today's path).

Both land at the same place: `ACTIVE` nodes recording what happened. The plan is a draft that
the checkpoint's distilled truth overwrites.

## The most intuitive, generalizable workflow

```
1. ORIENT     agent reads `sgt graph` / `sgt show`        → sees existing features
2. PROPOSE    (optional) `sgt plan "..."`                 → tentative PLANNED nodes, reviewable
3. IMPLEMENT  agent edits code with ITS OWN tools         → sgt is not involved
4. RECORD     `sgt checkpoint --intent "..." [--fulfills <node>]`
              → distill diff → effects → gate → land ACTIVE node (like `git commit` for features)
5. OPERATE    `sgt revert / switch / reconcile`           → semantic manipulation
              → re-materialize the tree (the `git checkout` analog)
```

- Pure code-first → steps 1, 3, 4, 5.
- Plan-first / multi-agent → all five; `PLANNED` nodes are the shared contract each agent claims.

## Materialization: sgt owns tree reconstruction

`revert` / `switch` re-materialize the tree from the remaining active effects and write it. This
is **required**, not optional: the defining property of version control is that a recorded state
deterministically reproduces the tree. If a graph op only *suggested* edits for the agent to
apply, two reverts could diverge, the core invariant *state = replay of active nodes* would be a
lie, and sgt could not guarantee its own graph↔tree consistency.

Fidelity: effects store each unit's source verbatim, so reconstruction is faithful *within* a
unit and only normalizes layout *between* units. Where a region cannot be reconstructed
losslessly, the gate refuses rather than clobbering (see below).

**Escape hatch — `--emit` / dry-run.** `sgt revert --emit` (and `switch --emit`) prints the
semantic delta and the witness instead of writing files, so the agent can apply a delicate or
lossy change by hand. Default = materialize (deterministic, offline, true VC); `--emit` = hand
off to the agent.

## Safety of graph operations (dependency issues on plug-in/out)

Plugging a feature in or out can break code dependencies. This is the failure mode sgt exists to
prevent. Every graph op ends with the same guard before it may commit:

1. **Re-materialize** the tree from the remaining active effects.
2. **Gate** it through the invariant predicate. If invalid, the op is **refused** — sgt never
   writes code that fails to resolve.

Two outcomes when plugging off a feature:

- **`revert` (remove): closure pulls dependents.** If B calls A's function, the edge is inferred
  from the call graph; `revert A` removes **B too**, so no dangling caller is left. Orphaned
  support nodes (concept/infrastructure) are garbage-collected; capabilities are not.
- **`switch off` (suspend): refuse if it would orphan a reference.** `switch` is a surgical toggle,
  not a cascade. If something still references the suspended node, materialization fails the gate
  and the op is refused gracefully, reporting *which* reference would dangle. The user decides:
  switch off the dependent first, or revert the closure, or `--emit`.

The gate checks (AST-level, deterministic, no LLM): **name resolution** (every call resolves to a
def/import/builtin), **arity**, **per-scope uniqueness**, and **`from <local> import x`** against
the module's exports.

**The honest boundary — structural, not behavioral.** The gate guarantees you can never emit code
that *fails to resolve*. It does **not** catch:

- runtime/semantic dependencies with no syntactic reference (B assumes A ran via a shared global
  or ordering, but never calls it),
- dynamic references (`getattr`, string dispatch, reflection),
- `import mod; mod.foo()` attribute usage and "the whole imported module was deleted,"
- non-Python files and comments/docs.

Two levers tighten this in the new design:

1. **Declared edges.** `PLANNED` nodes carry the planner's `needs`/`provides`, becoming explicit
   dependency edges that capture *intended* dependencies even before a syntactic call exists —
   closing part of the gap the inferred call graph cannot see.
2. **`--emit`** hands refused ops to the agent with the dangling-reference witness, so it resolves
   with full code context.

If behavioral safety is later wanted, the gate is pluggable — "run the test suite as an additional
invariant" would catch the runtime-dependency class, at the cost of speed/offline. A future knob,
not v1.

## Integration: commands are the product; skills/hooks are opt-in ergonomics

This is **not a plugin.** The capability *is* the command surface, operated by the agent the way
it already operates `git`:

1. **CLI + MCP commands** — the actual capability. An agent that can shell out uses the CLI; one
   that can't uses the MCP tools. Both read the graph, and record/operate through sgt.
2. **Skills / slash commands** (`/sgt-plan`, `/sgt-checkpoint`) — optional. They mainly *teach the
   convention* ("after each feature, checkpoint") and give humans a typed entry point. Not
   required for function.
3. **Hooks** (a `Stop` hook that auto-runs `sgt checkpoint`) — optional automation for hands-free
   recording. Pure sugar on top of #1.

Nothing auto-activates by default — same as git doesn't commit for you.

## Command surface (target)

- `sgt graph` / `sgt show <ref>` / `sgt status` — read (no key).
- `sgt plan "<intent>" [--yes]` — decompose into reviewable `PLANNED` nodes. **Replaces `sgt do`'s
  code-writing role.** Graph-only; writes no code.
- `sgt checkpoint --intent "<...>" [--fulfills <ref>]` — distill on-disk edits into effects, gate,
  land `ACTIVE` (CLI twin of the MCP `sgt_checkpoint`). Subsumes today's `sgt sync`.
- `sgt revert <ref> [--emit]` / `sgt switch <ref> on|off [--emit]` / `sgt reconcile [<ref>]` —
  operate on the graph; re-materialize the tree (or emit the delta).
- `sgt mcp` — the stdio server, gaining `sgt_plan` and `--fulfills` on `sgt_checkpoint`.

## Architectural boundary: plans are replica-local until fulfilled

Surfaced by stress-testing the collaboration flow against `merge/engine.py`. The merge model
is effect-log-primary: `export_delta` ships only nodes that have **log entries**
(`touched = {e.node_id for e in entries}`). A `PLANNED` node has no effects and therefore no
log entries, so it does **not** travel in a merge delta — **plans are replica-local; a plan
enters the shared, mergeable log only when it is *fulfilled*** (fulfillment authors real
effects, which merge normally).

This is deliberate, not a gap: you don't merge half-formed drafts, you merge realized work, and
fulfillment is exactly the moment a plan becomes shareable. Plans can still be shared coarsely by
committing/pulling `.sgt/graph.json` over git. If first-class plan *sharing* (review a peer's
plan before any code) is later wanted, the clean move is a `plan` log entry (a no-op effect that
carries the node into the log) — not special-casing the graph in merge. Out of scope for v1.

## Open questions to resolve in planning

1. ~~**LLM scope**~~ — *Resolved: graph-level reasoning stays (manual coders use it too).*
2. **`sgt sync` vs `sgt checkpoint`** — collapse into one verb, or keep `sync` as the no-intent
   distill and `checkpoint` as the intent-declared one?
3. **`PLANNED` storage** — extend the existing node store with a status + declared edges, or a
   separate plan file projected into the graph?
4. **`--fulfills` reconciliation** — when distilled reality diverges from the planned intent, do we
   keep both (plan intent as history, actual as the node), or overwrite?
5. **Migration** — is there existing `.sgt/` state in the wild to migrate, or is removing the
   backend a clean break?
