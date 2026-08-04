---
module: launch
tags: [launch, cleanup, docs, ux, llm, mcp, robustness]
problem_type: release-readiness
status: in-progress
date: 2026-08-03
---

# Launch-readiness plan (T-1 day)

Consolidated from six parallel audits (robustness, CLI surface, LLM, MCP/Claude, docs, TUI/VSCode).
This is the working synthesis + execution plan. Ordered by launch value × safety.

## Cross-cutting theme confirmed by the goal
Users operate at a **high/abstract level** (features, "revert this thing"), not at the entity/op
level the internals assume. The most damaging bugs come from that mismatch. The fix philosophy:
**every user-facing suggestion must name a working, high-level, spine verb** — never a raw op-id
pair, never a re-homed verb without its `advanced`/`feature` prefix, never a renamed/deleted verb.

---

## P0 — launch blockers

### P0-A. Broken / entity-level "what next" suggestions (systemic, confirmed)
Verbs were re-homed under `advanced`/`feature` and several were renamed to `sgt log` modes, but
printed output strings still name the old bare verbs, so the suggested command **silently no-ops**
(falls through to top-level help, exit 0).
- `sgt now` / `_next_action` (`sgt/api.py:2197`) surfaces the stored fork remedy `sgt merge-op
  6e58fc5c ff95aab5` — both broken (`merge-op` is under `advanced`) AND entity-level. The
  high-level verb `sgt resolve <symbol>` already exists (`sgt/cli/resolve.py`) and wraps it.
- `sgt log --summary` repair hint `sgt fsck --tree` (`sgt/cli/inspect.py:593`) — `fsck` is under
  `advanced`; should be `sgt advanced fsck --tree`.
- TUI fork remedies (`sgt/tui/graph.py:466,1194,1306`) render `sgt merge-op <a> <b>`.
- Stored remedy producers: `core/propose.py:173`, `core/migrate.py:210`,
  `core/sync/materialize.py:110`, `core/sync/__init__.py:153`.
- Note: `sgt log --summary` ALREADY uses the good form (`sgt resolve <symbol>`, `sgt advanced
  forks`) — the fix is to make every surface consistent with that.
- Decision (per goal): elevate the surfaced fork remedy to `sgt resolve <symbol>`; keep
  `sgt advanced merge-op` as the power-user escape hatch. Tests at `test_api.py:1128`,
  `test_propose.py:109-110`, `test_so_what.py:109,117` encode the OLD low-level contract and must be
  updated to the new one (not weakened — re-pointed to the high-level verb). BLOCKED on CLI-surface
  audit to confirm we prefer resolve-surface over re-adding top-level aliases.

### P0-B. Robustness of unexpected workflows (reproduced)
- **P0-1 backward git history desyncs the ideal (REPRODUCED, wedges `sgt save`).** After `git reset
  --hard`/`amend`/`branch -f`, `ideal_table[key]` is never re-intersected with the ref's current
  ancestry (`lens.py:633`, seed at `:665`), so dropped ops stay "live": `log`/`--map` show dropped
  symbols, and a later `sgt save` dead-ends with `put() would overwrite uncommitted changes`. No
  recovery verb exists (only manual `rm .sgt/local/ideal.json`). Fix: intersect base_ids with
  current ancestry on the forward-catchup branch, and ship `sgt resync [--reseed]` escape hatch.
- **P1-2 conflict-marker bytes mined into the append-only store (REPRODUCED, permanent).** The
  merge-in-progress guard exists only in `save` (`porcelain.py:170`); `revert`/`switch`/read views
  call `get()` with no guard (`ideal_edit.py:233`, `porcelain.py:110`). Running `sgt revert` mid
  unresolved-merge mines `<<<<<<<` markers into a permanent op. Fix: lift the pseudo-ref check into
  the shared mine-on-contact entry (`lens.get`/`_sync`).
- **P1-3 `put()`→`record_ideal()` not atomic** (`verbs.py:273-276`): interruption leaves HEAD
  advanced with stale witness/ideal, unrecoverable by `undo`.
- **P1-4 foreign commit between plan and apply silently reverted** (`lens.py:896-911`).
- **P2-5 multi-file `_sync` checkpoint not crash-atomic** despite comment (`lens.py:684-695`).
- Handled well (don't chase): forward out-of-band commits, LLM label bounding/fallback, per-file
  atomic store writes, undo F3 guard, dirty-tree revert refusal. Nit: auth-error warning prints on
  every `sgt log` without a key.

## P1

### P1-C. Stale command references in docstrings / help / MCP tool descriptions
Widespread: `sgt map`→`sgt log --map`, `sgt graph`/`sgt episodes`→gone/`--rail`,
`sgt status`→`sgt log --summary`, `sgt why`→`sgt feature why`, `sgt after/forks/state/repair/
review-queue/oracle/tiers/migrate`→`sgt advanced …`, and `sgt put` (`core/sync/land.py:369`) which
does not exist. User-facing printed ones are P0-adjacent; pure docstrings are P1 doc hygiene.

### P1-D. MCP context size & latency (from MCP audit)
- P0-1 `sgt_grid` returns `history_view(full=True)` — largest uncapped payload; drop from MCP or add
  compact mode (`server.py:70`→`api.py:1291/1314`).
- P0-2 `sgt_checkpoint` hardcodes `plan_view(full=True)` (`server.py:223`); compact carries the ids
  the confirm call needs — drop `full`, add opt-in `detail`.
- P0-3 read tools re-run full dirty mine every call (`lens.py:457/525-532`) — debounce reads.
- P1-4 rewrite jargon-heavy tool descriptions (`I \ ↑X` etc.) to one plain sentence each.
- P1-7 lower `sgt_log` default `limit=100`→~30 (`api.py:86`). P1-6 drop `fsck`/`oracle_run` from MCP.
- P2-8 skill vs server `claude_session_id` env-var contradiction (`SKILL.md:24` vs `server.py:338`).

### P1-E. LLM cohesion / robustness / cost (from LLM audit)
- P1-1 `label.py:195/216` omit `reasoning=` → defaults to medium on real OpenAI (cost inversion on
  hottest path). Set explicit `{"effort":"low"}`.
- P1-2 unchecked `output_parsed is None` in `label.py:238` / `theme.py:169` crashes instead of
  falling back. Guard both.
- P2-3 extract one shared LLM-call helper (4× duplicated `_request`/token/cost/EFFORT).
- P2-5 `repair/api_backend.py:90` `save_json`→`save_json_if_changed` (watcher churn).

## P2 — docs (from docs audit)

### P2-F. Delete root artifacts that shouldn't ship
`FINDINGS.md` (self-labeled superseded), `LOOP.md` (generic essay), `allinone.md` (paper-search
dump). Git history preserves them. Lift the 2-3 still-true limits into `docs/guide/workflows.md`.

### P2-G. README rewrite (full outline in docs audit)
Promote install near top; verified block `uv venv --python 3.12` + `uv pip install -e
".[entities,lens]"`; add `sgt now` (undocumented everywhere); fix `sgt fsck`→`sgt advanced fsck`
(README:51); fix MCP "13 tools"→14 (add `sgt_recall`); jargon pass (ideal→"current set of edits",
op→"symbol-level edit", oracle→"your build/test checks", fold, hollow op, drift, CAS).
Archive `docs/design|plans|brainstorms|ideation` legacy set under `docs/archive/` or soften the
guide-index pointer.

## P1/P2 — TUI/VSCode UX (from UX audit)
Root cause: `grid_view` is un-paged (`api.py:1291`) so capping must happen in renderers; only some do.
- **TUI A-P0-1** `sgt log --map` has no row cap (`tui/graph.py:952`, `render_graph_lines` lacks
  `max_rows`) — huge history dumps thousands of lines. Save-list/rail already cap at 40.
- **TUI A-P1-1** no terminal-width detection anywhere (no `get_terminal_size`); hardcoded
  `bar_width=38` → rows hard-wrap on narrow terminals, break lane alignment.
- **TUI A-P1-2** title column never truncates (`graph.py:944`) — one long label shoves the bar
  off-screen. Cap `title_w`≈32 + ellipsize.
- **TUI A-P1-3 / VSCode B-P1-3** default `sgt log` + `--rail` + Now-tree lead with raw commit
  subject → "sss"/"done" junk leaks; `--map`/Gantt correctly use feature+intent labels. Fall back to
  dominant feature's checkpoint intent when subject is low-signal.
- **VSCode B-P0-1** no webview virtualization (`workbench.js:2622`), Rail emits one row per commit —
  ~20k DOM nodes on a 3-5k-commit repo; `applyFrontier`/`renderInspector` run on every pointermove.
- **VSCode B-P1-1/2** vertical resize ignored (width-only ResizeObserver, `js:2847`); scrubber below
  the fold. **B-P1-4** Rail preview is a silent no-op (morph CSS targets `.glane`, rail uses
  `.rail-row`). **B-P2-4** dead `media/decision.js` shipped in bundle.
- Good, leave alone: before/after morph, so_what line, "next:" hints, Now→next-action.

---

## Delegation (avoid file conflicts)
- **Me (own sgt/cli, sgt/core, sgt/api, sgt/tui, tests):** P0-A suggestions, P0-B robustness, CLI cuts.
- **Docs subagent (own *.md, delete root artifacts):** P2-F/G. Isolated from code.
- **LLM subagent (own lens/label.py, intent/theme*.py, repair/api_backend.py):** P1-E. Isolated.
- **MCP + TUI-render:** after my api.py/inspect.py edits land (shared-file hazard), delegate or do.

---

## Execution order
1. Await robustness + CLI + TUI reports (in flight).
2. P0-A (fork/repair suggestions) once CLI audit confirms resolve-surface vs aliases.
3. P0-B robustness fixes.
4. P1-D MCP + P1-E LLM (safe, high-value, parallelizable via subagents).
5. P2-F/G docs + README.
6. Full test suite green gate after each batch.
