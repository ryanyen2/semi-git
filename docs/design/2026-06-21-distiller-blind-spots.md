---
date: 2026-06-21
topic: distiller blind spots — module-level statements and cross-feature import landing order
status: resolved — #1 fixed; #2 fixed (module bindings); #3 superseded by the accretion fix
origin: surfaced live while a coding agent landed a 9-node RAG + CLI plan in a test workspace;
  repeated invariant_violated quarantines traced to three distinct distiller/gate gaps
related:
  - docs/design/2026-06-18-statement-aware-distill.md
  - docs/design/2026-06-19-graph-only-agent-driven-sgt.md
---

# Distiller blind spots

Landing real, multi-module features through `sgt checkpoint` repeatedly quarantined nodes with
`invariant_violated`. Three root causes, all in the distill → materialize → gate path. One is a
clean bug (fixed); two are design gaps (deferred, written up here so we change the gate
deliberately rather than reactively).

## 1. `from __future__` reordered into a SyntaxError — FIXED (2026-06-21)

`apply_effect`'s `ADD_IMPORT` branch prepended every import with `tree.body[0:0] = imp`. Imports
replay in `order_key` order, so any import authored after the `__future__` import landed above it,
demoting it off line 1 → `SyntaxError` on the next parse → `invariant_valid` fails → quarantine.

**Fix:** `_insert_imports()` in `sgt/effects/model.py` now anchors `__future__` imports directly
below an optional module docstring and ordinary imports below the future block, so any replay
order stays valid. Covered by `tests/effects/test_model.py::test_future_import_*`.

## 2. Module-level name bindings are now captured — FIXED (2026-06-21)

`distill_file` (`sgt/effects/diff.py`) emitted effects only for top-level defs/classes and added
imports; everything else (`X = re.compile(...)`, top-level assignments) went through
`_other_toplevel` as a *note*, never an `Effect`, so the binding was lost on rematerialize and any
reference to it failed name resolution (repro confirmed).

**Fix:** new def-mirroring effect ops `ADD_ASSIGN` / `REPLACE_ASSIGN` / `REMOVE_ASSIGN` for
**single-name** top-level bindings (`X = …`, `X: T = …`), target = the bound name, payload = the
full statement source. They are plain def-level core ops (not `STMT_OPS`), so commute is the
existing apply-and-compare path — `sgt/engine/commute.py` is unchanged. Wired through
`model.py` (factories, precondition, apply with placement *after imports, before defs*),
`diff.py` (a three-way assign diff replacing the note), `attribute.py` (blame via the
`const_owner` map), and `project.py` `_defines` (so other nodes can depend on the binding).
Tuple-unpacking / bare expressions / `__main__` blocks still produce a note, now an explicit
"NOT captured — will be lost" one. Tests: `tests/effects/test_model.py`, `test_diff.py`,
`test_attribute.py`.

## 3. Cross-module forward-reference gate — SUPERSEDED by the accretion fix

The original #3 (make `_cross_module_ok` forward-reference aware) was **misdiagnosed**.
Reproduction showed `_cross_module_ok` (`sgt/effects/invariants.py`) **already skips relative
imports** (`from .query import X`, `node.level > 0`) — which is what the log actually used — so the
forward-reference gate would not have changed the observed behavior. It only ever rejects
*absolute* local imports of a not-yet-exported sibling symbol, and weakening it would make
`project.valid()` (used by `revert`/`switch` rollback in `lifecycle/algebra.py`) report the active
tree invalid until the producer lands. Deferred indefinitely as low-value.

The actual log pain was **quarantine accretion**, fixed instead (see below).

## Accretion: superseded (zombie) quarantines are swept — FIXED (2026-06-21)

When a node is held and the agent then *fixes* the code, the good version lands as a new active
node but the original quarantine **lingered forever**: its held `add_def X` precondition is now
permanently False (X is already active), so `reconcile` can never clear it (repro: "resolved 0,
still pending 1"). That is why recovery required a manual `revert` + `replan`.

**Fix:** after a checkpoint lands, `run_sync` sweeps quarantines whose held effects (a) all have
failing preconditions on the current active codebase AND (b) touch a `(file, name)` **(re-)defined
in this same run** (`superseded_quarantines` in `sgt/orchestrate/sync.py`). The "this run" guard
is what keeps the sweep surgical — it reclaims only the hold the current fix superseded, never a
legitimately recorded merge/uniqueness conflict against a pre-existing active rival (which stays
recoverable via suspend + `reconcile`). Removal reuses `project.remove_nodes`; swept ids are
reported on `SyncReport.swept`, the CLI report, and the MCP `sgt_checkpoint` result. Tests:
`tests/orchestrate/test_sweep.py`.
