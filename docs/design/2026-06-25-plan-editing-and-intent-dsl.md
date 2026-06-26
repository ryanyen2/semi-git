---
date: 2026-06-25
topic: plan-editing verbs (merge/split) + the intent DSL as a deterministic front end for `plan`
status: design / shipped
origin: this conversation (2026-06-25); builds on the one-frontier ADR
  (docs/design/2026-06-25-one-frontier-minimal-verbs.md) and the robustness brainstorm
  (docs/brainstorms/2026-06-25-robustness-and-intent-dsl.md, UC1 + Part 2C)
---

# Plan-editing + the intent DSL

## The two gaps this closes

After the one-frontier refactor the spine was clean but two brainstorm gaps remained — the ones
that made demos "break across usage":

1. **A plan was immutable from the CLI.** `PLANNED` nodes were reviewable but append-only; reshaping
   meant hand-editing `.sgt/graph.json` (UC1). A contract you can't amend isn't a contract.
2. **Intent had one register.** Freeform prose → the LLM planner → rich JSON. There was no
   deterministic, offline, *learnable* way to state intent, and rationale (`BECAUSE`/alternatives)
   was always low-confidence/distilled, never user-asserted (UC5, Part 2C).

## What shipped — and the deliberate cuts

The guiding constraint was the one-frontier ADR's discipline: **a minimal, essential command set**,
not one verb per brainstorm bullet. So:

- **Plan-editing is two structural reshapes only: `merge` and `split`.** `drop` is already
  `revert <planned-ref>` (it discards a draft, which has no recorded work). Fixing a draft's wording
  or adding a dependency edge are **not** new verbs — they are re-planning in canonical DSL. The
  brainstorm's `relabel`/`link`/`unlink` were cut on that basis; the powerful DSL front end makes
  them redundant. The mutating surface stays at **seven**: `plan`, `merge`, `split`, `checkpoint`,
  `revert`, `restore`, `reconcile`.
- **The intent DSL fills the *same* `SubTask` schema** the planner already emits
  (`orchestrate/constraint.py`) — a new front end, not a new backend (the "one projection / one
  schema" invariant). Nothing downstream (`add_plan`, fold-on-revise, decisions, gate) changed.

### The intent DSL (`sgt/agents/intent_dsl.py`)

Grammar — the **uppercase verb is the opt-in** (so ordinary prose stays freeform):

```
ADD     <names> [USING <names>] [BECAUSE <reason>]      new capability; provides=names, needs=USING
EXTEND  <lane> (TO|WITH) <behavior> [BECAUSE <reason>]  revise an existing lane
REPLACE <name> WITH <approach> [BECAUSE <reason>]       revise + Alternative{option:name, why:reason}
REMOVE  <names> [FROM <lane>] [BECAUSE <reason>]        revise that removes def(s)
```

- `parse(text) -> ParsedIntent | None` — deterministic, **offline (no key)**; `None` ⇒ freeform.
- `render(...)` — a node → canonical string, for the learnable echo.
- `normalize(text, codebase, *, client, model)` — the one LLM touch: freeform → a **list** of
  canonical statements (one per capability, so decomposition is preserved); `[]` offline.

Tiered into `Orchestrator.plan(intent, confirm=None)` (`orchestrate/loop.py`):

1. **Canonical** → parsed offline into one node, no LLM. `EXTEND`/`REPLACE`/`REMOVE` resolve their
   target via the shared resolver and adopt the lane's owned names as `provides`, so
   `_fold_planned_revisions` folds the node into that lane as a REVISE. A `REPLACE … BECAUSE …`
   reason becomes a **user-asserted** `Alternative` (`source="user"`, `confidence="high"`) in the
   `decisions.json` sidecar.
2. **Freeform + `confirm`** → `normalize` to a canonical program, echo for approval, then parse.
3. **Else** (no key / non-interactive / declined) → the rich planner, **unchanged**, with a canonical
   echo appended for learnability.

So power users get offline determinism, freeform users learn the grammar by seeing their intent
rendered into it, and the no-key / MCP / test paths are byte-compatible with before.

### `merge` / `split` (`orchestrate/loop.py`)

Both act on **`PLANNED` drafts only** (inert — they gate nothing and don't change the materialized
tree), refuse a realized/held target with a pointer, drift-guard (they `commit`), and resolve refs
through the one resolver.

- **`merge(refs)`** folds drafts into the first (survivor): union `provides`/`needs`, keep the
  others' intents in the survivor's provenance, **redirect every incident edge** onto the survivor
  (its dependencies and its dependents), union authored alternatives, then remove the rest.
- **`split(ref, intents)`** replaces one draft with N pieces parsed by the DSL (canonical ⇒ precise
  interface; freeform ⇒ intent-only island). The original's edges go with it; pieces **relink by
  declared interface** (the same needs↔provides rule `plan` projects edges with, scoped to edges
  touching a new piece). Any of the original's `provides` no piece claims is reported as
  **unassigned** rather than silently lost.

## Surfaces (one mutation path, agent-native parity)

- **CLI**: `sgt plan` (accepts canonical DSL; `--yes` auto-accepts normalization), `sgt merge`,
  `sgt split`.
- **MCP**: `sgt_merge`, `sgt_split`; canonical-DSL `sgt_plan` needs no change (it's just an intent
  string). Non-interactive ⇒ no normalization confirm.
- **TUI**: intentionally **out of scope** — plan curation isn't the TUI's compose focus.

## What this still defers (future phases, not regressions)

- **History-space** re-decomposition of *realized* lanes (UC2: split/merge/move `ACTIVE` decisions).
  `merge`/`split` deliberately refuse non-PLANNED targets — rewriting landed attribution is a
  separate, harder phase that touches the log, not just the graph.
- **Refactor-survival** (`move_def`, UC3) and a **`fork`** verb for genuine alternatives (UC4 — the
  clean answer to the same-name-rival limitation in the one-frontier ADR's open question #4).
- **Semantic / temporal selectors** (UC5 tiers 5–7) — the resolver is written to accept them later.

## Verification

`tests/agents/test_intent_dsl.py`, `tests/orchestrate/test_planedit.py`, and additions to
`tests/orchestrate/test_plan.py`; the offline live walkthrough
`scripts/e2e_plan_editing.py`. The whole loop (parse, plan, merge, split, fold-as-revise, drop)
runs with **no API key**.
