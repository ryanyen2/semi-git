# semi-git — v1 findings

> **Superseded in part (2026-06-19):** sgt has pivoted to **graph-only** — it no longer authors
> code (the `sgt do` / OpenAI-backend path described below is removed). The current spine is
> `plan` → implement (your coding agent) → `checkpoint`. See **"Graph-only pivot (2026-06-19)"**
> near the end of this file; the sections below document the pre-pivot behavior and the still-valid
> core (effects, gate, materialization, dependency closure, sync).

Status: the v1 vertical slice is built and verified end-to-end against the real
OpenAI backend. A user can modify a codebase at the **intent/feature level** and
plug features in and out cleanly.

## What works (verified)

- **Intent → valid code.** `sgt do "<intent>"` classifies the prompt, delegates to
  the OpenAI coding backend (typed effects via structured output), gates the result
  through the EICO confluence check, and lands it as a feature node. The generated
  code is invariant-valid and runnable, and matches the stated intent (e.g. a 6-char
  md5 URL shortener). Verified via `scripts/e2e_smoke.py` and the `sgt` CLI.
- **Clean plug-out.** Reverting an independent feature removes exactly that feature
  and leaves the rest of the codebase invariant-valid and runnable.
- **Dependency-aware revert (closure).** When feature B calls a function defined by
  feature A, the dependency is inferred from the call graph; reverting A removes B
  too, so no dangling caller is ever left behind. Support-kind nodes (concept /
  infrastructure) are garbage-collected when orphaned; capabilities are not.
- **Never leaves broken code.** Every revert re-materializes from the remaining
  active effects and is gated by the invariant predicate before commit.
- **Iterate on an existing feature (`modify` → `replace_def`).** `sgt modify` re-derives
  a body change in place: the backend emits a `replace_def` that rewrites an existing
  function (one definition, no same-named duplicate) rather than appending a new unit.
  A `replace_def` whose new body calls another feature forms a dependency edge, so
  revert closure stays correct. Verified via `scripts/e2e_modify.py`.
- **Git underneath.** Each operation materializes the working tree and commits, with
  a `Sgt-Node-Id` trailer mapping commits to semantic nodes (survives amend/rebase).

The canonical state function is the replay of active nodes' effect-bundles from
empty — which is what makes "drop a feature and re-materialize" sound.

## Parallel fan-out + continuous confluence (verified)

- **One intent fans out (#2).** `sgt "<multi-part intent>"` runs a decomposition agent
  that emits a transient constraint graph of sub-tasks (intent + provides/needs +
  depends-on), topo-layered into coordination-free batches. A checkpoint confirms a
  >1-task plan (`--yes` / auto-confirm skips it); an atomic intent runs inline. Verified
  via `scripts/e2e_fanout.py`: "validate + normalize + register email" decomposed into
  2 parallel sub-tasks + 1 dependent, all landed as nodes, dependency edges inferred
  from the call graph, and `register_email` composed the two correctly.
- **Layers dispatch concurrently.** Each independent layer runs through a thread pool
  (backend calls are blocking I/O); a dependent layer is dispatched against the
  post-land tree, so it sees its providers' code.
- **Continuous confluence is real (#3).** Held effects are no longer silently dropped:
  they become durable `QUARANTINED` nodes (excluded from materialization) carrying a
  witness (which invariant/effect, and why). A bounded, non-blocking auto
  rewrite-to-commute re-dispatches the held task against post-land state — when the
  conflict was an ordering issue, it lands; when not, it stays pending and the run still
  completes. Quarantines participate in revert closure.

## Observations

- The intake classifier exercises real judgment: "add `short_link` that calls
  `shorten`" was sometimes routed as a **refinement of the shortener capability**
  (merged into one node) and sometimes as a **separate capability depending on it**.
  Both are correct; they differ only in revert granularity. This is the
  system-distilled-graph behavior working as intended.

## Fan-out reliability follow-ups (from code review, deferred)

These are known, lower-severity items surfaced by an adversarial review pass; the
fan-out path is sound without them (the confluence gate is always the backstop):

- **Rewrite-to-commute budget vs. transient failures.** A backend error during
  `attempt_rewrite_to_commute` consumes a commute attempt and the witness reason can be
  stale (gate reason, not "backend failed"). Should distinguish transient backend
  failures from genuine non-commutation, with a small backoff.
- **No per-task dispatch timeout / `BaseException` handling.** A hung backend call blocks
  the layer (`ThreadPoolExecutor.shutdown(wait=True)`); `KeyboardInterrupt` is not caught
  as a failed task. Relies on the OpenAI client's own request timeout today.
- **Reshape (R31) is via quarantine+rewrite, not edge re-layering.** A planner
  under-serialization (dependent placed in its provider's layer) is held and reconciled
  against post-land state — safe, but `ConstraintGraph.add_dependency` is not invoked by
  the loop. The explicit add-edge/re-layer path remains future work.
- **Witness label for `add_call` conflicts** names the enclosing function, not the callee.
- **Fan-out commit carries no node trailer** (it spans multiple landed nodes), so
  `node_id_for_commit` returns None for fan-out commits (unlike the serial path).

## Core hardening (2026-06-18) — addressable units + bidirectional sync

A focused pass to make the core survive real cases before any new features. All prior
behavior preserved; suite grew from 108 to 140 tests.

- **Unit of versioning is now an addressable AST node at any depth, not a top-level def.**
  Effects target a scope-qualified path (`shorten`, `UrlService.shorten`); `add`/`replace`
  are uniform over paths and a new `remove_def` op completes the model (deletion was
  previously only reachable via node revert). OOP and method-level versioning now work.
  A bare name is the top-level case, so all pre-existing effects are unchanged.
- **Invariants are scope- and codebase-aware.** Uniqueness is per scope; reference
  resolution covers all binding forms and resolvable method calls (`self.m()`/`Class.m()`,
  conservative on unresolvable `obj.m()`); arity catches too-many/unknown-kwargs; and
  `from <local> import x` is verified against the local module's exports — so reverting a
  concept another module imports is caught at the gate, not at runtime.
- **Dependency inference is file-aware.** A use of `foo` links only to a node defining
  `foo` in the *same file* (or imported from that node's module) — no more false edges
  between same-named defs in different files, and import-based cross-file edges are found.
- **Commutativity is disjoint-path.** Sibling methods commute; a class and its method do not.
- **No silent failures.** `valid()` treats an un-materializable state as invalid instead
  of crashing (so `switch off` a depended-on node refuses gracefully); the serial `do`
  path now durably *quarantines* held effects instead of dropping them; and `commit()`
  rolls `.sgt` back if the git commit fails (no split-brain).
- **Bidirectional sync (`sgt sync`).** Out-of-band edits no longer get clobbered. A
  pre-flight guard blocks mutating verbs when the tree drifted (with `--force` to override);
  `sgt sync` distills the disk diff into typed effects (deterministic AST diff), clusters +
  labels them (LLM, with a deterministic fallback), confirms, and lands them through the
  same gate. Once landed, re-materialization reproduces the edit. Non-distillable changes
  (unparseable files, module-level code) are reported, never dropped. Verified by
  `tests/orchestrate/test_sync.py` and `scripts/e2e_sync.py`.

## Refine fan-out (2026-06-19)

- **A compound refine/fix no longer collapses to one node.** The intake classifier picks a
  single lane + target, so "change shorten to return 8 chars **and** make slugify strip
  whitespace first" used to attach to one node and drop the other half. A new refine splitter
  (`sgt/agents/refine_split.py`) — symmetric to the capability planner — decomposes a refine/fix
  prompt into the smallest set of independent sub-changes, each carrying the existing node id it
  touches (or empty for new work). The orchestrator (`Orchestrator._refine_fanout`) routes each
  sub-change to its node via the same gated `_extend`/`_add`, applied in turn so later parts see
  earlier ones. An atomic split (one part) keeps the verified single-`_extend` path exactly, and a
  splitter failure or hallucinated target degrades gracefully — so it is a strict improvement.
  Verified by stub-driven tests in `tests/orchestrate/test_loop.py` and a live run: the two-refine
  prompt above split into 2 sub-changes across the 2 correct nodes, each landing a `replace_def`.
  **Deferred:** refine sub-changes apply sequentially (not parallelized like capability layers),
  and there is no separate interactive checkpoint for the refine split yet.

## Rename-aware distillation (2026-06-19)

- **A refactor that renames a function no longer orphans its node.** The reverse differ
  (`sgt/effects/diff.py`) now does git-style rename detection: a top-level `def` removed
  under one name and re-added with a near-identical body (name-blind similarity ≥ 0.8) is
  distilled as a single `rename_def` (plus a `replace_def` when the body also changed),
  not `remove_def` + `add_def`. The feature evolves in place on its existing node — revert
  restores the original — instead of leaving a zombie node (whose add/remove net to empty)
  plus a spurious new node owning the real code. Matching is one-to-one, greedy by
  similarity, deterministic, and restricted to sync top-level functions (the `rename_def`
  op's capability). Found while probing the "big refactor resets/changes most features"
  edge case; verified by `tests/effects/test_diff.py` and a live rename-through-`sgt sync`
  run (1 node, 0 new, drift 0, clean revert). The distiller prompt also gained a refactor
  rule keeping a removal grouped with the addition that supersedes it.
  **Still deferred:** cross-scope moves (a top-level function relocated into a class method)
  — the differ stays top-level, so that case is still delete+add until methods are diffed
  as scope-qualified units.

## Graph-only pivot (2026-06-19) — sgt no longer authors code

A product-defining refactor: sgt is now **git for semantics**, operated by the coding agent.
It manipulates the semantic graph and reconstructs the tree from it; it never writes code. The
LLM is confined to *graph reasoning* (decompose a plan, label a checkpoint). See
`docs/design/2026-06-19-graph-only-agent-driven-sgt.md` and the plan
`docs/plans/2026-06-19-001-...`.

- **Plans are first-class (`sgt plan`).** A new persisted `PLANNED` node status: an intent
  decomposes into reviewable, inert nodes (no effects → skipped by materialization) carrying the
  planner's declared `provides`/`needs` as `DEPENDS_ON` edges. The constraint graph is no longer
  transient.
- **Fulfillment (`sgt checkpoint --fulfills <node>`).** Lands the agent's real on-disk edits
  (distilled, deterministic) under a planned node and flips it `PLANNED → ACTIVE`, **atomically**:
  all effects commute or the node itself is held `QUARANTINED` with a witness (recoverable via
  `reconcile`). The planned intent is preserved in `provenance` when the declared intent is
  adopted (reality wins). `checkpoint` is the canonical record verb; `sync` is the no-intent
  alias.
- **Code authoring removed.** Deleted: the OpenAI coding backend (`sgt/adapter/`), the fan-out
  dispatch (`orchestrate/dispatch.py`), the authoring `Orchestrator` methods, and the
  authoring-router agents (`classifier`, `refine_split`). `sgt do`/`sgt modify` are gone; a bare
  `sgt "<intent>"` is now shorthand for `plan`. The planner + distiller stay (graph reasoning).
- **Reconcile is agent-free (`attempt_recommute`).** Instead of re-asking a backend to rewrite
  held effects, `reconcile` re-gates a quarantine's *existing* held effects against current
  active state; once the conflict clears (rival reverted/suspended, provider landed) they commute
  as-is and resolve. No LLM.
- **`--emit` dry-run.** `sgt revert|switch --emit` previews the semantic delta (and any refusal
  witness) on a throwaway sandbox — writes nothing — so the agent can apply a delicate change
  itself. Materialization on a real revert/switch is the `git checkout` analog (reconstruct
  recorded state, never author).
- **Architectural boundary (documented, by design):** `PLANNED` nodes are **replica-local** —
  they have no log entries, so `merge/engine.py:export_delta` does not ship them; a plan enters
  the shared, mergeable log only when *fulfilled*. You merge realized work, not drafts.
- Verified by the test suite and a live walkthrough (`scripts/e2e_plan_checkpoint.py`):
  plan → fulfill in order → `--emit` preview → revert, project valid throughout.

## Review hardening (2026-06-19) — graph-only pivot, post-review pass

A multi-reviewer code review of the pivot surfaced (and we fixed) a cluster of edge-contract,
recovery, and agent-parity issues. All resolved; suite grew to 232 tests.

- **The `--fulfills` contract is status-aware and atomic.** An empty drift is an explicit
  no-op (the node is *not* silently flipped). A `--fulfills` routes on the target's status:
  PLANNED → fulfill, ACTIVE → extend, QUARANTINED → *retry* (re-gate the freshly distilled disk
  state — the "I fixed it" path), SUSPENDED → refused. The clustered (bare-checkpoint) path
  never fulfills a PLANNED node — that is only ever the explicit `--fulfills`.
- **Out-of-order fulfillment is sound.** A fulfill no longer absorbs a *sibling* held node's
  code (held effects look like drift but belong to the held node). Held code is moved off disk
  into the log on hold, so the tree stays `== active materialization`; once the provider/rival
  lands, `reconcile` restores the held node from the log. Recovery story: **`reconcile`** =
  a rival changed; **`checkpoint --fulfills`** = the agent revised the code.
- **`reconcile` is drift-guarded** like every other mutating verb (it re-materializes and
  commits, so it must not clobber un-checkpointed edits). An empty held bundle now resolves
  rather than wedging forever.
- **Reverting realized code preserves PLANNED drafts** — a plan is not invalidated by removing
  the feature it declared a dependency on (closure skips PLANNED predecessors).
- **A bare `checkpoint` degrades to deterministic grouping with no API key** (the LLM label is
  an enhancement, not a requirement) — the whole loop is offline-capable.
- **Full MCP/agent parity:** added `sgt_reconcile` and `sgt_init`; held checkpoints return the
  witness (reason/held/against), and graph/plan/revert/switch responses expose quarantined nodes
  — an MCP-only agent can now close the quarantine loop.
- **Defense-in-depth:** `write_working_tree` refuses any effect path that escapes the repo root.
- A held fulfill keeps the declared intent + planned-intent provenance (KTD3), the same as a
  successful one.

## Visual surfaces (2026-06-20) — VS Code extension, TUI, semantic blame

GitLens-style views, but mapped from *commits* to *semantic nodes*. Built on one machine-readable
projection so every surface (CLI `--json`, MCP, the extension, the TUI) reads one schema. Suite
grew to 251 tests. See `docs/plans/2026-06-20-001-feat-sgt-vscode-tui-docs-plan.md` and the
[user guide](docs/guide/README.md).

- **One JSON projection (`sgt/api.py`).** `graph`/`node`/`show`/`status`/`conflicts`/`blame`/
  `export` views; the MCP read tools delegate to it, so the surfaces can't drift. New CLI verbs:
  `blame`, `export`, `emit`, `tui`, plus `--json` on `graph`/`status`/`show`.
- **Line-level semantic blame (`sgt/effects/attribute.py`).** Each rendered line → the feature
  that authored it, recovered from the effect log (eid→node; statement-slot LWW identity→node;
  seed→definer) and computed against the same `materialize()`/`build_statement_seq` path the tree
  is built from — so blame and the tree can't disagree. Statement-exact for top-level functions;
  whole-unit for class methods/reorders (honest, not faked).
- **VS Code extension (`editor/vscode/`).** Current-line + status-bar blame, on-demand per-feature
  heatmap, CodeLens, rich hovers with preview command-links, a DAG sidebar + a hand-rolled graph
  webview, and `sgt emit`-driven diff previews of plug-outs. Shells out to `sgt … --json`.
- **Terminal UI (`sgt tui`, optional `[tui]` extra).** Browse/inspect/preview/apply, keyboard-
  driven, in-process over `sgt.api`.

### Design + simplicity review hardening (2026-06-20)

A simplicity reviewer and a senior UI/UX reviewer drove a post-build pass:

- **Color unified in OKLCH.** The extension and webview had been emitting *different* colors for
  the same feature (HSV vs HSL). Now one OKLCH→sRGB generator is mirrored byte-identically in TS,
  webview JS, and Python (a test asserts JS == Python), theme-aware and WCAG-contrast-floored.
  **Hue is identity only**; status is a glyph + dim on every surface — the channels never collide
  (previously the TUI used hue for status while blame used it for identity).
- **Graph scales.** Within-layer ordering moved from alphabetical to median crossing-reduction;
  long edges route around nodes via dummy nodes; the webview gained pan/zoom/Fit, filter debounce
  with dim-in-place (no relayout), keyboard nav + ARIA, and CSS-transition layout animation (no
  animation dependency; reduced-motion guarded) so `plan`/`reconcile` shows what moved.
- **TUI responsive.** Width-derived columns, a `/` filter, narrow-mode detail modal, and uppercase
  apply-keys (`X`/`O`/`U`) to separate mutations from safe previews.
- **Simplicity cuts.** Dead `Sgt.graph()` and unused `EmitView.refused` removed; `ownerAt`/
  `truncate` de-duplicated; `attribute.py` imports hoisted.

## Known v1 limitations (deferred, see the plan)

- **On-demand reconcile shipped.** `sgt reconcile [<ref>]` retries rewrite-to-commute on
  pending quarantines (all, or one by ref); a node that now commutes is resolved and
  flipped ACTIVE (its rewritten effects replay last). Previously auto-reconcile ran only
  during the original fan-out.
- **Effects are unit-granular** (`add_def` / `replace_def` / `remove_def` / `add_import`
  / `set_const` / `rename_def` / `add_call`), addressable at any depth via scope-qualified
  paths. `replace_def` still rewrites a whole *unit* (function/method/class); sub-statement
  edits (changing one line, splitting a function) are expressed as a full-unit replacement,
  not a finer-grained patch. `set_const`/`rename_def` remain top-level only.
- **Cross-module integrity covers `from <local> import x` against present modules.**
  `import x` attribute usage and the "imported module deleted entirely" case are not yet
  checked (conservative to avoid false-flagging third-party imports).
- **Single language** (Python AST), per plan KTD3.
- **Gardener** (auto split/merge/relabel) and the **quarantine/reconciliation UI**
  are minimal in v1: conflicting effects are held back and reported, not yet
  re-written to commute. Multiverse, the confluence corpus, RL training, and the
  Codex/Gemini backends are deferred.

## Reproduce

```bash
uv venv --python 3.12 && uv pip install -e ".[dev]"
uv run pytest -q                              # deterministic suite
uv run python scripts/e2e_plan_checkpoint.py  # live graph-only walkthrough (uses .env for `plan`)
```
