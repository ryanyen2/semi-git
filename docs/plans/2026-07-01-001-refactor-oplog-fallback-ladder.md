---
title: "refactor: op-log ontology as a fallback ladder — recording lens first, composition gated on measurement"
type: refactor
status: wip / draft (round 2 — aligned to the PL theory + adversarial critique)
date: 2026-07-01
origin: docs/design/2026-07-01-operation-log-ontology.md
builds-on:
  - docs/design/wip/pl-theory-and-dsl.md                       # the now-decided theory: structured content CRDT, 5 primitives, laws with proofs/bounds
  - docs/design/wip/researcher-critique.md                     # the adversarial audit; §D's "pure recording lens" is Rung 0
  - docs/design/2026-07-01-operation-log-ontology.md           # §7 the concrete cut
  - docs/design/2026-07-01-intent-patch-algebra-and-recording-lens.md
  - docs/design/2026-07-01-symbol-identity-scheme.md
  - docs/design/2026-06-30-contracts-over-git-substrate.md
  - docs/plans/2026-06-30-001-feat-contracts-substrate-spike-plan.md  # the report-don't-veto recompose (its U4 is our P3)
author-note: written by Claude (staff-eng planning pass, round 2). Re-cut as a FALLBACK LADDER, not a single bet — Rung 0 (the provable recording lens) is a complete, independently-shippable milestone that delivers the owner's #1 ask and depends on nothing unbuilt; Rung 1 (structured-CRDT composition) is gated behind measurement M1/M2; Rung 2 is the honest git-3-way degrade. Green-field permission is explicit; every phase is additive-then-subtractive with a named rollback, the op-log is read-authoritative before the Node store is deleted, and there is never a *released* double-write state.
---

# refactor: sgt as an operation log — a fallback ladder

## Summary

Move sgt from a **three-truth store** (a mutable `Node` graph in `.sgt/graph.json`, a content-effect
log in `.sgt/effects.json`, and git commits) to the target **two-truth store**: **git (content bytes)**
+ **an append-only, CRDT-mergeable semantic op-log** (`record`/`relabel`/`regroup`/`remap`/`select`),
with every other artifact — patch views, footprints, symbol map, dependency DAG, per-selection trees,
blame — a deterministic fold over those two.

The PL theory (`docs/design/wip/pl-theory-and-dsl.md`) proves this cannot be a single bet. Its
recompose theorem (§C) splits content-faithfulness into two provably-different problems:

- **C.2 byte-faithfulness of the full recorded set** — `⟦get(c)⟧ = c` — is **[PROVED]** and needs only
  a verbatim-splice materialize (no `ast.unparse`). *Cheap. Depends on nothing unbuilt.*
- **C.3 parseability of an arbitrary subset** — the toggle/cherry-pick/branch showcase — is **[PROVED
  only under a structured, block-integrity content CRDT]** and **[FALSE-IN-GENERAL]** for the flat span
  sequence the earlier corpus assumed. *The hardest unbuilt piece.*

So the plan is a **ladder**:

- **Rung 0 — the provable recording lens (always shippable).** Op-log authoritative, reads on the fold,
  `Node` store deleted, `sym_id` the join key, **byte-faithful HEAD reflection**, drift gate, blame,
  labeled decision graph. **No composition promise.** Delivers the owner's #1 ask (record intent +
  faithfully reflect real code changes). This is P0–P5.
- **Rung 1 — structured-CRDT composition.** Toggle/cherry-pick/branch as real byte-faithful subset
  recompose, via the structured content CRDT. **Gated on M1 (parseable-subset rate ≈ 1) and M2
  (bounded-repair reliability ≥ a pre-committed floor).** This is P6–P7.
- **Rung 2 — the honest degrade.** If the structured CRDT cannot be built within budget, composition
  falls back to **git-3-way merge at function granularity** (the PL theory's Corollary C: the coarsest
  granularity at which every selection is trivially well-bracketed). Weaker — no statement-level
  toggle, conflicts are real — but honest. An explicit alternative branch of P6, not an afterthought.

The refactor is tractable because much of Rung 0 already exists in disguise:
`sgt/decisions/store.py:build_decisions` (store.py:121) is already a pure fold of the log into
`Decision` views, `Frontier` (decisions/model.py:156) already derives HEAD, and `attribute.py` blame is
already a fold sharing the tree's path (attribute.py:89,124). The one thing Rung 0 must *newly* build
is the verbatim-splice materialize that retires the `ast.unparse` formatting-loss bug — the researcher's
highest-value under-invested item (critique §C, A1.1).

---

## Problem Frame

The design ADR (`op-log-ontology §7`) declares today's storage model incorrect: a mutable `Node` with
stored `provides`/`needs`/`status`/edges is a source of truth that goes stale — the live inconsistency
where `provides`/`needs` exist on `PLANNED` nodes but vanish once ACTIVE (graph.py:79-80; the
`pending.planned` block at api.py:81-91 papers over it). The target retires the record: a patch is the
fold of the ops sharing its identity; `PLANNED`/`QUARANTINED` become derived attributes.

Three migrations hide inside "delete the store"; conflating them is the central risk, and the
adversarial critique (§C rabbit-holes) warns the op-log rewrite "delivers zero new user-visible
capability" if done for its own sake before the measurement is in. The ladder answers that: **every
Rung 0 phase either fixes a live correctness bug the owner can see (formatting loss, stale
`provides`) or is a strictly-internal re-homing gated by an equivalence proof** — no phase is pure
abstraction, and the expensive, uncertain composition work is quarantined behind M1/M2.

1. **Semantic-truth migration (Rung 0, P4–P5).** Replace the mutable `Node` graph + grouping metadata
   with a semantic op-log that folds to the same views. Specified; not blocked.
2. **Full-set content faithfulness (Rung 0, P2).** Replace `ast.unparse` replay with verbatim splice so
   the recorded HEAD reflects real bytes (comments/formatting). Cheap; C.2; not blocked.
3. **Subset content recompose (Rung 1, P6).** The structured content CRDT for arbitrary-selection
   parseability. Hard; C.3; **gated on M1/M2**, with the git-3-way degrade (Rung 2) as the escape.

Requirement IDs here (P0–P7 phases; INV-* invariants; M1–M5 metrics) are plan-local.

---

## Current-state map (grounded)

### Stored truth (three stores + five sidecars)

| store | file | module | what it holds |
|---|---|---|---|
| semantic DAG | `.sgt/graph.json` | `sgt/store/graph.py` | **mutable** `Node` (id, kind, intent, `status` ACTIVE/PLANNED/QUARANTINED, `commit_ids`, `provides`, `needs`, `provenance`) + typed `Edge` (DEPENDS_ON / REVISES / DERIVES_FROM). graph.py:67-114, 26-51 |
| content-effect log | `.sgt/effects.json` (key `"log"`) | `sgt/store/oplog.py` | append-only `LogEntry` (eid, node_id, `Effect`, author, `vv`, `landing`) + `tombstones` + `landing_seq`. oplog.py:23-158. Plus `order`, `managed_files`, `witnesses` (project.py:134-148) |
| content bytes | git commits | `sgt/store/gitbind.py` | commits carry only the `Sgt-Node-Id` trailer (gitbind.py:17); **no diff helper** — only `file_at`/`tree_at` (gitbind.py:101-123) |
| authored intent | `.sgt/decisions.json` | decisions/store.py:24,275 | intent context/consequence/slug + alternatives, keyed by decision id |
| in-force selection | `.sgt/frontier.json` | decisions/store.py:25,288 | `{lane → decision_id | "off"}` |
| named snapshots | `.sgt/frontier_tags.json` | decisions/store.py:26,315 | tagged frontier selections |
| replica identity | `.sgt/replica.json` | store/replica.py:21-84 | `replica_id` + monotonic counter; eid = `"{replica}:{n}"` |

The content-effect vocabulary is `EffectOp` (effects/model.py:21-49): `add_def`/`add_import`/`set_const`/
`rename_def`/`add_call`/`replace_def`/`remove_def`/`*_assign` and statement-granular `insert_stmt`/
`replace_stmt`/`remove_stmt` (`STMT_OPS`, model.py:49).

### The already-derived layer (the refactor's biggest asset)

- **`build_decisions(project)`** (decisions/store.py:121-183) folds `log.live_entries()` into
  `Decision`s, one per `(node_id, landing)`; footprint = `{file::target}`; lane assignment is a
  union-find with an anti-weld rule (`_assign_lanes`, store.py:54-118); lifecycle from per-lane landing
  order + REVISES/DERIVES_FROM. **This is already the "patch is a fold" prototype.**
- **`Frontier`** (decisions/model.py:156-192): `selection: {lane→decision_id}`; `in_force()` filters
  `!= "off"`; `tip_of` derives default HEAD. `IN_FORCE` is stamped by the projection (api.py:397), never
  stored — the store sets only PLANNED/LANDED.
- **Blame** (`attribute.py`): a fold over `in_force_entries()` → `materialize()` →
  `build_statement_seq()` (attribute.py:54-137; shared path :89,124). Never a text diff.
- **Entity graph** (`entities/extract.py`): symbol key is **position-derived** `id=f"{path}::{name}"`
  (extract.py:29,156). **No minted/canonical id exists anywhere** (confirmed by grep).

### The two live content bugs Rung 0 must fix (critique A1, verified in code)

- **`ast.unparse` formatting loss.** `_apply_stmt_ops` does `ast.parse(slot.source)` then
  `ast.unparse(tree)` on the whole file (effects/model.py:479-482). Comments and formatting are stripped
  on every materialize — so today's "reflect what changed" cannot even represent a comment change, and
  blame line numbers drift from disk. This is the shelved span-log fix that was recommended and never
  built (`memory/git-substrate-shelved-span-log.md`).
- **The statement CRDT only exists inside top-level functions.** `build_statement_seq` seeds from
  `_func_body_src` (model.py:420-443); `_apply_stmt_ops` rejects non-function targets. Module-level
  statements, class bodies, decorators, signatures, imports have **no slot identity**
  (`memory/distill-module-level-and-import-constraints.md`).

### Verbs, gates, surfaces (re-cited)

- **Recompose surface** `lifecycle/algebra.py`: `plan_revert`/`plan_restore` (pure) + `apply`; **the
  EICO veto is `apply`'s `if not project.valid(): rollback`** (algebra.py:126); `project.valid()` =
  `codebase_valid(materialize())` (project.py:484-490).
- **Landing gate** `engine/confluence.py:is_invariant_confluent` (:68-107) gates land-vs-hold in
  `run_sync` (sync.py:236-330); commutation by `static_commute` (commute.py:41-58).
- **Verbs** `orchestrate/loop.py`: `plan`(:80), `merge`(:272), `split`(:330), `revert`(:456),
  `restore`(:490), `reconcile`(:507); checkpoint/`--fulfills` = `run_sync`/`_fulfill_drift`.
  `emit_payload`(:429) is the UI dry-run.
- **One projection** `sgt/api.py` (15 functions): CLI `--json`, MCP (mcp/server.py:47), TUI
  (tui/app.py:22), VS Code (shells `cli --json`) all read it; `types.ts` hand-mirrors it.
- **Scrubber** `timeframe_view`/`materialize_at` (api.py:186, project.py:188) replays effect-log
  `landing` — **finding: no live consumer** (no CLI/MCP/TS caller); effectively orphaned.
- **Merge** `merge/engine.py`: unions effect-log entries by eid; vv for concurrency + total order.
- **LLM boundary** (OpenAI; config.py:32-39 raises offline): `planner`/`distill`/`intent_dsl`/
  `decisions.distill`, all with deterministic fallbacks; **only `scripts/graph_stress/coding_agent.py`
  authors code.**

### Coupling that makes this hard

`materialize()` (project.py:173-265) folds the *content-effect log*, gated by the *frontier* (sidecar)
and filtered by *`Node.status`* (graph) — three stores entangled in one call that blame, drift,
`valid()`, and every projection go through. `landing` (the scrubber's only time axis) lives on effect
entries. `Node.commit_ids` is written *after* the commit (project.py:479), so the semantic↔content
join is a mutable field until P1 makes the trailer authoritative.

---

## Target-state map

| concern | today | Rung 0 target | Rung 1/2 |
|---|---|---|---|
| content truth | effect-log replay (`ast.unparse`) | **git commits, verbatim-splice full-set materialize** (C.2) | subset recompose over **structured content CRDT** (C.3, P6) / **git-3-way @ function grain** (Rung 2) |
| semantic truth | mutable `Node` graph | append-only **op-log** `record`/`relabel`/`regroup`/`remap`/`select` | — |
| patch/decision | `build_decisions` + `Node` read | `fold_ops(op-log, git)` — same shape, no `Node` | — |
| `provides`/`requires` | `Node` fields + `file::qualname` | derived from commit diff, keyed by canonical `sym_id` | — |
| status | stored `NodeStatus` | derived: `PLANNED ⇔ commits==[]`; `QUARANTINED ⇔ gate result` | — |
| deps | stored `Edge` | derived: footprint overlap + `revises`/`regroup` provenance. **UNSOUND w.r.t. dynamic dependence — oracle is ground truth** (B.4) | — |
| revert/branch | `algebra` + frontier veto | `select` ops (OR-Set membership); revert = set diff; reorder = no-op | real recompose (P6) |
| validity gate | `project.valid()` **veto** | interface + **drift** + build gates that **report** | — |
| `sym_id` identity | none | minted `sym_id`; `remap` as a **provenance relation** `R⊆Σ×Σ` (1:1 rename/move, 1:n split, n:1 inline — PL §D.2) | — |
| merge / collaboration | effect-log eid-union | single-replica log (vv/eid) — collaboration deferred | op-log OR-Set + symbol MV-register + content CRDT (P7) |

**Deleted as truth** at end of Rung 0: the mutable `Node`/`SemanticGraph` write path and
`.sgt/graph.json`; the content-effect log *as truth* (its `Effect` derivation survives as a cache keyed
by `(sha, parser-version)`). **Kept verbatim**: version vectors + `order_key`; `attribute.py` (already a
fold); one recompose path shared by tree and blame.

---

## Verdicts read from the PL theory (the three round-1 blockers, now resolved)

- **(a) Content recompose substrate — RESOLVED to a structured, block-integrity content CRDT** (PL §A.3,
  §C.3). The flat verbatim span sequence is **rejected** — proved to yield unparseable subsets (a `try:`
  split from its `except:`; PL §C.3-negative). The structured CRDT (tree of `Unit`/`Leaf` atoms, siblings
  ordered by dense `PosId`, mandatory-body `pass`-leaf, `select` closing under dependency + syntactic
  containment) is the hardest unbuilt piece → Rung 1, gated M1/M2, with git-3-way @ function grain as the
  Rung 2 degrade (PL Corollary C). **Note where the choice changes the work:** Rung 0 is unaffected —
  full-set byte-faithfulness (C.2) needs only the verbatim splice, not the tree CRDT.
- **(b) `select`/branch scope — RESOLVED to shared, mergeable op-log state** (PL §A.1: `Sel=(M,V)` is part
  of the converging metadata, `Sel.M` an OR-Set, `Sel.V` an LWW/MV register; §B.1). `select` is a logged
  op; branches are collaborative state. Aligns P7's merge (shared, not local scratch).
- **(c) Decision→commit cardinality — RESOLVED to many-commits-per-patch, one-patch-per-intent** (PL §E,
  A6; ranks above DSL syntax). `fold_ops` and `regroup` assume it; reformatting is its own commit so an
  empty-footprint patch is toggle-safe.

---

## Key Technical Decisions

- **Ladder, not a single bet.** Rung 0 delivers real user value (faithful reflection + drift + blame +
  labeled graph) and is independently shippable; Rungs 1–2 may never be built and the product still
  stands. This directly adopts the critique's §D fallback and the coordinator's re-cut.
- **Byte-faithful *reflection* (C.2) is Rung 0; byte-faithful *subset composition* (C.3) is Rung 1.**
  The two are different theorems with different costs; splitting them is what lets Rung 0 ship without
  the structured CRDT.
- **`depends` is UNSOUND, and the plan says so.** Round 1 wrongly called it a sound over-approximation;
  the PL theory §B.4 proves it misses dynamic dependence (dispatch, monkeypatch, `getattr`, registries,
  decorators). Consequence baked into the phases: the **build oracle is the sole dependency backstop and
  runs on every materialized selection**, an `after` escape hatch declares edges the analyzer can't see,
  and the miss rate is measured (M3). No gate vetoes.
- **No released double-write state.** The op-log is shadow-written in P4 (writers append; readers ignore)
  with a fold-equivalence gate; the flip of reads and the deletion of the `Node` write path happen
  together in P5, gated on P4. The critique is auditing this gate — it is stated as an executable equality
  over the corpus, not a promise.
- **`build_decisions` is the equivalence oracle.** Its output over the stress corpus + three e2e scripts
  is the P0 golden master every later phase reproduces.
- **`remap` is a provenance relation, not a bijection** (PL §D.2): 1:1 rename/move, 1:n split, n:1 inline
  — this absorbs the deferred "symbol split" sixth-primitive into the five-op kernel. Detection stays a
  drift-gate confirm-loop biased to asking, with **batch UX (one prompt per refactor, grouped by the git
  `-M` rename set)** to answer the critique's prompt-storm risk (A5).
- **Color contract fixed in P0, not assumed.** CLAUDE.md + `color.ts:8` name the mirror `graph.js`; the
  file is `media/decision.js`; the parity test leaves `color.ts` unverified. P0 corrects the doc and
  extends the test to all three mirrors.
- **Migration: cut-over-new-repos + one-shot `sgt migrate`** (the only real `.sgt` on disk is a bare
  `replica.json`); `Project.open` refuses an un-migrated legacy store (loud) rather than reading a stale
  shape (silent).

---

## Invariants every phase gate must preserve

- **INV-1 One projection, many clients.** View-shape changes are made in `sgt/api.py`, additive only;
  no surface invents a shape. Gate: P0 golden `*_view` snapshots byte-stable modulo additive keys.
- **INV-2 Color contract byte-identical across three mirrors** (`color.py`/`color.ts`/`decision.js`);
  status is glyph+dim, never hue. Gate: P0-extended `test_color_parity`.
- **INV-3 Blame is a fold, never a text diff, and consistent with the tree.** Blame and the
  materialized/recomposed tree share one path. Gate: blame spans partition the exact tree bytes — and,
  from P2, the *disk* bytes (verbatim splice makes line numbers exact). **Condition C7 (researcher
  round-2) — INV-3 survival is contingent on the P6 substrate choice.** The **structured-CRDT** substrate
  (Rung 1) keeps blame a fold over recorded spans → INV-3 fully preserved. The **git-3-way degrade**
  (Rung 2) makes blame `git blame` relabeled through the sidecar — *line→owner inferred from a text
  diff*, the exact thing INV-3 forbids. So at Rung 2, INV-3 is explicitly **downgraded to "best-effort,
  function-granular, text-derived"** and that downgrade is a *stated, gated* consequence of taking the
  degrade — never a silent regression. The two P6 branches have different INV-3 status; the M1 gate
  chooses which.
- **INV-4 Reads offline; graph-only boundary holds.** No fold or `*_view` calls the LLM/network; sgt
  authors no code. Gate: fold + every `*_view` green with `OPENAI_API_KEY` unset.

---

## Phased plan

Each phase: goal · files · invariant it must not break · test gate · rollback point · **degradation
note** (what still works if the *next* phase is never built).

═══════════════════  RUNG 0 — THE PROVABLE RECORDING LENS  ═══════════════════
*Delivers the owner's #1 ask (record intent + faithfully reflect real code). No composition promise.
Depends on nothing unbuilt. Independently shippable.*

### P0 — Characterization harness + contract hygiene (additive; no deletion)

- **Goal.** Freeze observable behavior as a golden master; fix the two contract-hygiene gaps.
- **Files.** `tests/golden/` (new: recorded `sgt.api` outputs per corpus project + e2e script),
  `scripts/graph_stress/digest.py` (diff basis), `CLAUDE.md` + `editor/vscode/src/color.ts:8`
  (`graph.js`→`decision.js`), `tests/test_color_parity.py` (add `color.ts`), `sgt/store/gitbind.py`
  (add `diff_name_and_text(parent, sha, find_renames=True)` — the missing diff helper).
- **Invariant.** INV-1, INV-2 (this phase establishes their enforcing tests).
- **Test gate.** Golden snapshots re-runnable; color parity green across all three mirrors; full suite
  (385 tests) + both e2e scripts green.
- **Rollback.** Pure additions — revert the commit.
- **Degradation note.** If nothing else ships, P0 still leaves the repo with a real color-parity test and
  a regression harness — pure upside.

### P1 — Canonical symbol identity, minted & dual-keyed (additive)

- **Goal.** Mint `sym_id` + the symbol map, populated in parallel with `file::qualname`; **not** yet the
  join key. Support `remap` as a relation (1:1/1:n/n:1). Add batch-confirm drift UX.
- **Files.** `sgt/store/symbols.py` (new: `SymbolMap` — `sym_id→surface`, reverse index, relation-valued
  `remap`; sidecar `.sgt/symbols.json`), `sgt/store/gitbind.py` (`Symbol-Id:` trailer), distill path
  (`effects/diff.py` rename detection :49-80; `decisions/distill.py`), `entities/extract.py` (carry
  `sym_id`), `tests/store/test_symbols.py`.
- **Invariant.** INV-3, INV-4 (blame unchanged; minting deterministic + offline fallback).
- **Test gate.** On corpus rename/move: a renamed def keeps its `sym_id`; the derived dep-DAG via `sym_id`
  equals the P0 golden DAG (dual-key changes no edge yet). A false-positive rename (near-duplicate bodies)
  surfaces via the drift gate and defaults to delete+create — never a silent merge (symbol ADR §4; PL
  §D.2). Batch confirm: one prompt per `-M` rename set, not per symbol (A5).
- **Rollback.** Readers still use `file::qualname`; delete `symbols.py` + trailer writes.
- **Degradation note.** Even if the op-log rewrite never lands, `sym_id` blame is stable across renames —
  a standalone improvement to the existing store.

### P2 — Byte-faithful HEAD reflection (verbatim splice; retire `ast.unparse`) (additive → flip)

- **Goal.** Make the recorded full in-force set materialize to the **verbatim recorded bytes** (comments +
  formatting preserved), from git content, not an `ast.unparse` replay (C.2). This is the researcher's
  highest-value fix and what makes "reflect what changed" honest. **No subset-composition promise** — a
  reverted/pinned frontier still uses the legacy path and is only honest at function granularity until
  Rung 1.
- **Files.** `sgt/effects/model.py:479-482` (`_apply_stmt_ops`/`materialize` → splice raw `source`
  ranges, no whole-tree unparse), `sgt/project.py` (`materialize` full-set path reads git commit content
  for the in-force set), `sgt/effects/attribute.py` (blame maps disk lines → `sym_id`/patch against the
  spliced bytes), `tests/effects/test_materialize.py` + a comment/blank-line corpus fixture.
- **Invariant.** INV-3 (blame == disk bytes now that formatting is preserved), INV-1, INV-4.
- **Test gate.** Round-trip identity: for every corpus file, `⟦get(c)⟧ == c` **including comments and
  blank lines** (fails today — A1.1); blame spans align to disk line numbers. Behind `SGT_VERBATIM=1`
  until proven, then flipped default.
- **Rollback.** Unset the flag; the effect-log replay path is retained as the derivation cache.
- **Degradation note.** This is a terminal capability on its own — faithful reflection needs no later
  phase. If P3+ never ship, sgt is already a byte-faithful recorder on the current store.

### P3 — Report-don't-veto gates; drift gate first-class (additive parallel → flip)

- **Goal.** Replace the `project.valid()` veto (algebra.py:126) with gates that **report** (interface +
  **drift** + build); the build oracle is the dependency backstop for the unsound `depends` (B.4). Adopt
  the spike's U4 `ContractRecomposeOutcome`.
- **Files.** `sgt/contracts/` (spike's `gate.py`/`recompose.py`/`build.py`), `sgt/lifecycle/algebra.py`
  (`apply` persists-and-reports), `sgt/api.py` (surface `orphans`/`build`/`drift`/derived-status
  **additively**), `sgt/orchestrate/loop.py`, tests mirroring. The drift metric `footprint(get(c')) ⊖
  intent-footprint` (PL §F) becomes a first-class projection key.
- **Invariant.** INV-1 (additive), INV-4.
- **Test gate.** A revert that today refuses (algebra.py:127) now succeeds and reports orphans; the build
  oracle flags a signature/arity break the interface gate misses (A7 false-green); the `after` escape
  hatch records a dynamic edge; PLANNED/QUARANTINED render correctly.
- **Rollback.** Veto behind `SGT_REPORT_GATES` until the flip is proven.
- **Degradation note.** The drift gate is itself a shippable recording-lens feature ("your commit did more
  than its intent said") independent of the op-log rewrite.

### P4 — Semantic op-log, shadow-written & fold-equivalent (additive shadow)

- **Goal.** Stand up the op-log and make `fold_ops` reproduce `build_decisions` **exactly** — the "op-log
  authoritative" milestone — while `Node` is still the read path.
- **Files.** `sgt/store/semlog.py` (new: `Op{id,vv,kind,target,payload,author}` + `SemanticOpLog`
  OR-Set; kinds `record`/`relabel`/`regroup`/`remap`/`select`; sidecar `.sgt/oplog.json`),
  `sgt/project.py` (every mutation — project.py:296-449 — *also* appends the matching op; `save_frontier`
  → `select`), `sgt/fold.py` (new: `fold_ops(project) → [Decision] + frontier`), `sgt/cli.py`
  (`sgt migrate`), `tests/test_fold_equivalence.py`.
- **Invariant.** INV-1, INV-3, INV-4 — nothing read from the op-log yet, so behavior is unchanged by
  construction; the only new assertion is equivalence.
- **Test gate (the crux the researcher is auditing).** `fold_ops(project) == build_decisions(project)` —
  same decisions, ids, footprints, **lanes** (the `_assign_lanes` union-find, store.py:95), lifecycle,
  and frontier — over all 5 corpus projects + 3 e2e scripts. If the lane assignment cannot be reproduced
  from ops alone, that is the finding that `regroup` provenance must carry (op-log §9 RISK); the gate
  fails loudly rather than papering over it.
- **Condition C5 (researcher round-2) — golden-master alone is NOT sufficient for an order-sensitive
  fold.** A lane-weld path can be *unexercised by the fixed 5-project corpus, pass green, then diverge
  post-cutover in P5.* The gate **adds property-based / randomized-history stress** that permutes landing
  order and forces multi-shared-key fold nodes (`ADD_ASSIGN` targets, provides-nothing fix nodes), not
  just the canned projects.
- **Condition C6 (researcher round-2) — `fold_ops` MUST be `project.graph`-read-free.** If `fold_ops`
  reads `project.graph` to *match* `build_decisions`, equivalence goes green while secretly depending on
  the store P5 deletes. `fold_ops` derives edges from `record.revises`/`regroup` provenance and provides
  from commit diffs only; a test asserts it opens no `graph.json` handle.
- **Rollback.** Op-log is a write-only shadow readers ignore; delete `semlog.py` + append calls.
  **Must not ship alone** (see P5).
- **Degradation note.** If P5 never lands, the shadow op-log is inert and harmless; the product is still
  the P0–P3 recording lens on the `Node` store.

### P5 — Flip reads to the fold; delete the `Node` store (subtractive; the crux) ◀── **RUNG 0 ENDS HERE**

- **Goal.** Re-point `build_decisions` + every `project.*` read onto `fold_ops` (op-log + git); make
  `sym_id` the join key; derive status; delete the mutable `Node`/`SemanticGraph` write path and
  `.sgt/graph.json`.
- **Files.** `sgt/decisions/store.py` (`build_decisions` → thin adapter over `fold_ops`), `sgt/project.py`
  (drop `self.graph` as truth; status/deps derived), `sgt/api.py` (`node_view`/`graph_view`/
  `decision_graph_view` fold the op-log; status derived), `sgt/store/graph.py` (delete write path; keep
  the `Edge`/`EdgeType` enum only if a surface needs it), `sgt/store/symbols.py` (now the authoritative
  join key), `sgt/orchestrate/*` (verbs emit ops).
- **Invariant.** INV-1 (golden `*_view` byte-stable modulo additive keys), INV-3, INV-4.
- **Test gate.** P4 equivalence still green with the op-log the *only* semantic source; P0 golden
  `sgt.api` snapshots match; suite green with `graph.json` never written.
- **Rollback.** One revert restores the P4 state (op-log shadow + `Node` truth both present); because
  P4+P5 are gated together on equivalence, the released transition is atomic — no double-write ships.
- **Degradation note.** **This is the Rung 0 terminus and a complete product:** op-log-authoritative
  recording lens with byte-faithful HEAD, `sym_id`-stable blame, drift gate, and a labeled decision
  graph — the owner's #1 ask, delivered, with zero dependency on the structured CRDT. If Rungs 1–2 are
  never built, sgt ships here as "git for semantics, recording + reflection only, no composition."

═══════════════════  RUNG 1 — STRUCTURED-CRDT COMPOSITION  ═══════════════════
*Gated ENTRY on an M1 measurement harness; gated EXIT on M1 ≈ 1.0 and M2 ≥ floor. The hardest unbuilt
work. If it can't be built in budget, take the Rung 2 branch of P6.*

### P6 — Structured content CRDT + subset recompose (the composition promise)

- **Goal.** Build the block-integrity content CRDT (PL §A.3) and the recompose fold `⟦S⟧` (PL §C) so that
  toggle/cherry-pick/branch produce **byte-faithful, parseable** subset trees. `select` closes membership
  under dependency + SCC + syntactic containment; mandatory bodies get a synthetic `pass`-leaf.
- **Files.** `sgt/content/` (new: `Node=Unit(header,sym_id,children)|Leaf(span,owner)`; dense `PosId`
  sibling order; BI enforcement), `sgt/effects/model.py` (`materialize` → `⟦S⟧` over the tree CRDT),
  `sgt/lifecycle/algebra.py` (revert/cherry-pick = `select` set ops), `sgt/api.py:timeframe_view`
  (re-derive `materialize_at` from **git commit ordinals**, or drop it — it has no live consumer),
  `sgt/effects/attribute.py` (blame reads the same `⟦S⟧` spans), `tests/content/`.
- **Invariant.** INV-3 (blame == recompose bytes), INV-1, INV-4.
- **ENTRY gate (M1 harness).** Extend `scripts/graph_stress/` to run real C1–C5 selections and measure
  **M1 = parseable-subset rate**. Predicted ≈ 1.0 by construction under BI; if < 1, the CRDT/BI model is
  wrong or real selections need sub-unit cuts.
- **EXIT gate.** M1 ≈ 1.0 **and** M2 (bounded-repair reliability, below) ≥ its pre-committed floor; plus
  byte-identical recompose vs captured commits including comments (no `ast.unparse`); blame spans identical
  to the tree.
- **Rollback / Rung 2 branch.** If M1 falls short or the CRDT proves unbuildable within budget, **take the
  degrade:** composition falls back to **git-3-way merge at function granularity** (PL Corollary C — the
  coarsest trivially-well-bracketed grain). Conflicts are real (git's), surfaced and routed to repair; no
  statement-level toggle. This is a first-class alternative implementation of P6, selected by the M1 gate,
  not an afterthought.
- **Degradation note.** If P7 (collaboration) never lands, single-user composition still works — P6 needs
  no merge.

═══════════════════  RUNG 1+ — COLLABORATION  ═══════════════════

### P7 — CRDT merge over the op-log (subtractive/replace)

- **Goal.** Move `merge/engine.py` from effect-log eid-union to op-log OR-Set + symbol-map **MV-register**
  + content CRDT; `select` membership is a shared OR-Set (verdict b); `relabel` LWW on the causal counter.
- **Files.** `sgt/merge/engine.py`, `sgt/store/symbols.py` (MV-register: concurrent rename → drift prompt,
  never a silent winner — PL §B.5), `tests/merge/`.
- **Invariant.** INV-1, INV-3, INV-4; SEC convergence (PL §B.1 PROVED).
- **Test gate.** `tests/merge/test_t0.py` + concurrency cases converge; concurrent `relabel`/`relabel`
  resolves by LWW; concurrent same-symbol edit surfaces an explicit versioned divergence, not a merge
  (PL §B.5 impossibility); ops on distinct targets commute.
- **Rollback.** Keep effect-log merge until op-log merge convergence is proven.
- **Degradation note.** Collaboration is the last rung; every prior capability is single-user-complete
  without it. (The critique C flags OR-Set/MV formalism as premature for v1 — hence it is dead last and
  gated behind Rung 1 shipping.)

---

## The hard cases (called out explicitly)

**(a) The scrubber's `materialize_at(frame)` re-derivation.** The frame axis is `landing` on content-effect
entries (oplog.py:82-92); it vanishes with the effect log. Re-derivation: each `record` names commits, git
gives a total commit order, so `materialize_at(frame)` = recompose the selection restricted to commits at
DAG-position ≤ frame — landed in **P6** (it needs the recompose fold). **Mitigation:** the path has no live
consumer today, so the low-risk option is to *drop* it in P5 and reintroduce it git-ordinal-based only when
a surface needs it. Either way it never dangles against a deleted log.

**(b) Blame stays a fold and consistent with recompose.** `attribute.py` already shares the tree's path
(:89,124). **P2** already tightens INV-3 (blame == *disk* bytes once the verbatim splice preserves
formatting); **P6** preserves it by having blame read the same `⟦S⟧` spans. `sym_id`-keyed blame is stable
across renames for free (PL §6). INV-3's gate — spans partition the exact bytes — catches any divergence.

**(c) Three surfaces, one projection.** Every phase gate includes INV-1 (api additive-only, golden stable)
and INV-2 (color parity across all three mirrors). VS Code shells `cli --json`, MCP/TUI import `sgt.api`,
`types.ts` mirrors it — so no surface code changes in P1–P7 except to render *additive* keys.

**(d) Migrating existing `.sgt` vs new-repos-only.** Cut-over-new-repos-only + a one-shot `sgt migrate`
(P4) that folds a legacy `graph.json`+`effects.json` history into the op-log. Rationale: the only store on
disk is a bare `replica.json`, dual-read has ongoing cost, and reads are folds so migration is a pure
re-projection. `Project.open` refuses an un-migrated store (loud), never reads a stale shape (silent).

---

## Metrics (the gates that move between rungs)

| id | measures | gates | predicted / floor |
|---|---|---|---|
| **M1** | parseable-subset rate — fraction of real C1–C5 selections whose `⟦S⟧` parses | Rung 1 ENTRY/EXIT | ≈ 1.0 by construction under BI; < 1 ⇒ take Rung 2 |
| **M2** | bounded-repair reliability — of parseable-but-oracle-failing selections, fraction a seam-bounded repair drives to green | Rung 1 EXIT | pre-commit a floor (critique D suggests **80%**); below ⇒ stay at Rung 0 (no composition promise) |
| **M3** | footprint false-green / miss rate — of oracle-caught breaks, fraction `depends` failed to predict (B.4) | informs whether C-cases are safe | critique D: **> 25%** ⇒ restrict to recording + narration |
| **M4** | collaboration disjointness rate — fraction of concurrent patch pairs that are footprint-disjoint (auto-merge) | scopes P7's promise | descriptive |
| **M5** | drift rate — how often `put` exceeds its intent (PL §F, RISK-B) | recording UX tuning | descriptive |

---

## Risk register

| # | where it can silently corrupt state | phase | the test that catches it |
|---|---|---|---|
| R1 | Op-log fold diverges from `build_decisions` (lane union-find, store.py:95, not reproducible from ops) | P4 | `test_fold_equivalence` — full corpus + e2e equality incl. lane ids/lifecycle |
| R2 | `sym_id` **false-positive rename/split** merges two symbols → join corrupts silently (PL §D.2, symbol ADR §4) | P1 | adversarial near-duplicate-body case: drift gate asks, defaults to delete+create; batch-confirm test |
| R3 | Recompose/reflect **formatting drift** via `ast.unparse` (the shelved-span-log bug, still live at model.py:479) | P2 | round-trip identity incl. comments/blank lines |
| R4 | `depends` **unsoundness** silently drops a required patch on toggle/revert (B.4 — dynamic dep) | P3/P6 | oracle runs on every materialized selection; M3 measures miss rate; `after` escape hatch test |
| R5 | Status **misderivation** (held node read ACTIVE, PLANNED-with-commits) | P5 | status-derivation table test over corpus |
| R6 | **Released double-write** where op-log and `Node` both mutate and diverge | P4→P5 | P4 ships only as inert shadow; P5 gated on equivalence — enforced by not shipping P4 alone |
| R7 | Subset **unparseability** — a selection folds to a `try:` with no `except:` (PL §C.3-negative) | P6 | M1 harness; BI closure test (`select` closes under containment + SCC) |
| R8 | Same-symbol concurrent edit **silently interleaved to garbage** (PL §B.5 impossibility) | P7 | concurrent same-symbol edit must surface a versioned divergence, not a merge |
| R9 | Scrubber frame mismatch after `landing` removed | P6 | per-frame golden vs git-ordinal re-derivation (or path dropped) |
| R10 | Color contract drift (unverified `color.ts` mirror) | P0 | extended `test_color_parity` across all three mirrors |
| R11 | A projection reads the LLM/network or a surface invents a shape | all | INV-1/INV-4 golden run with key unset |

---

## What NOT to do yet (deferred, with the gate that unblocks each)

- **Build the structured content CRDT (P6) before the M1 harness exists.** Gate: an M1 measurement over
  real selections. (Critique C: this is the premature-abstraction trap — do not do it for its own sake.)
- **The LLM back-end seam-repair patch** (`record(kind:repair)`; PL §F). Gate: **M2** measured against a
  pre-committed floor. Until then gates only report. The repair fence ("no net-new top-level defs") must
  be a *checkable predicate* (diff touches only orphaned/conflict lines ∧ adds no symbol outside a
  whitelisted `integration` namespace) tuned against M2, or dropped as an explicit amendment to "sgt never
  authors code" (critique A3, RISK-D).
- **`regroup` concurrent-merge semantics** (PL §A.2 uses causal tombstones; op-log §9 RISK). Gate: confirm
  causal serialization vs a specified join; P4 surfaces whether provenance edges suffice.
- **`reduce()` / minimal-history derivation.** Gate: a user workflow is actually blocked on it (critique
  C: read-only and useless for v1 — defer entirely).
- **Collaboration formalism (OR-Set/MV/Lamport) beyond P7.** Gate: Rung 1 ships and M4 says multi-user is
  worth it; single-user needs only vv/eid.
- **Non-Python / cross-language symbol identity.** Gate: a non-Python target enters scope; the canonical
  tier is already language-agnostic, only the surface locator + the per-grammar BI rules are not.
- **Footprint derivation at read-time vs record-time; SHA-only vs cached effects** (op-log Open-Qs). Gate:
  measure fold cost on a large repo; default read-time + memoized snapshot until a number says otherwise.

---

## Sources / Research

- `docs/design/wip/pl-theory-and-dsl.md` — §A.3 structured content CRDT, §B laws (B.1 SEC PROVED, B.2/B.4
  commute/`depends` bounds, B.5 collaboration boundary), §C recompose theorem (C.2 byte-faithful, C.3
  subset parseability + Corollary C function-grain degrade), §D five primitives + D.2 `remap` relation,
  §F the lens + drift metric, §G metrics M1–M5.
- `docs/design/wip/researcher-critique.md` — A1 (`ast.unparse` still live), A2/B.4 (`depends` unsound),
  A3 (repair reliability is the crux, not conflict rate), A5 (batch-confirm UX), C (rabbit-holes: don't
  rewrite the store for its own sake), **§D (the pure-recording-lens fallback = Rung 0)**.
- `docs/design/2026-07-01-operation-log-ontology.md` §7 the cut; `docs/plans/2026-06-30-001-*` U1 (diff
  helper), U4 (report-returning recompose = P3), U6 (harness).
- Code: `sgt/store/graph.py:26-114` (Node, deleted P5), `sgt/store/oplog.py:23-158` (effect log, demoted),
  `sgt/decisions/store.py:34-183` (`build_decisions` — the P4 equivalence oracle),
  `sgt/lifecycle/algebra.py:119-129` (the veto flipped P3), `sgt/project.py:173-265,484-490` (entangled
  `materialize`/`valid`), `sgt/effects/model.py:420-504` (`build_statement_seq`, the `ast.unparse` bug at
  :479-482 fixed P2), `sgt/effects/attribute.py:54-137` (blame fold), `sgt/entities/extract.py:25-166`
  (position key, no minted id), `sgt/store/gitbind.py:17,101-123` (trailer + no diff helper).
- Learnings: `git-substrate-shelved-span-log.md` (R3 formatting drift), `statement-distill-eid-lww.md`
  (revert-drops-create; garbage-under-LWW), `refactor-rename-distill-limitation.md` (the rename failure P1
  fixes), `distill-module-level-and-import-constraints.md` (slot-CRDT-only-in-functions).
</content>
