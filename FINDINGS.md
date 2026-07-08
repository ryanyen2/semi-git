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
  heatmap, CodeLens, rich hovers with preview command-links, a **GitLens-style Feature Graph** in
  the bottom panel (see below), and `sgt emit`-driven diff previews of plug-outs. Shells out to
  `sgt … --json`.
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

### Feature Graph redesign — GitLens/GitKraken parity (2026-06-20)

The first graph was a sparse, free-floating node-link diagram in an editor-tab webview — wrong
paradigm and visually thin. Rebuilt against the actual GitLens source (`references/vscode-gitlens`)
as a dense, **row-based commit-graph**, hosted as a Webview *View* in the **bottom panel**
(`GraphViewProvider`):

- One feature per **row** (most-derived on top), with a **KIND** ref-pill column (identity-tinted
  pill + status glyph), a git-style **swim-lane GRAPH** column (lane-allocation sweep → identity-
  colored node circles + bezier dependency edges, planned=hollow, conflict=red ring), and an
  **INTENT** column with dependent counts.
- A canvas **minimap** (activity spline over effects-per-feature + status markers), a breadcrumb
  header with a drift chip, search-dims-in-place, arrow-key nav, and click/Enter to inspect.
- Verified with a headless-Chrome screenshot of a dev harness (`editor/vscode/dev/preview.html`)
  before shipping — the paradigm and density now match GitLens's Commit Graph.

**Layout compactness (3rd round).** The first row-graph ordered nodes by dependency *depth*, which
scattered a node far from its dependency and produced long crossing diagonals. Replaced with a
**depth-first topological order** (LIFO Kahn: place a node only after all its dependents, diving
into a dependency chain before backtracking) + nearest-free-lane assignment — so a node sits
directly above its dependency, edges stay short and vertical, and lanes are reused. On the
knowledge-graph CLI example this drops the graph to 3 lanes with most edges spanning 1–2 rows
(the only long lines are the shared-base "branch lines", exactly as GitLens renders them). The
tangle was a *layout* bug, not a planning bug — the plan is a valid (wide, shallow) DAG. A follow-up
"gap in the middle" was a real **forest** — two weakly-connected components (the CLI-arg group vs the
knowledge-graph engine) with no edge between them. The graph now detects components (union-find) and
draws a subtle inset divider at each boundary so the gap reads as an intentional group separator. A
disconnected `main` may also be a *planning* signal (a missing dependency edge) — surfaced, not
auto-fixed.

A second feedback round drove: **(1) no modal popups** — inspection (a reversible action) now opens
an **in-situ detail pane** beside the graph instead of a `showInformationMessage` modal; apply
actions use a two-click inline confirm. **(2) Live agent presence** — features with uncommitted
drift (resolved to owning nodes via blame) show a `✎ editing` badge + pulsing node halo + a header
indicator, and a `**/*.py` watcher refreshes in near-real-time so you can watch the agent work the
graph; a just-landed feature flashes. **(3) Short labels** — the FEATURE column shows a derived
`xxx-yyy-zzz` kebab label (≤5 words); the full intent lives in the pane. **(4) Rich text** — the
detail intent renders backtick code spans and turns `@`/`#`/backtick references that resolve to a
feature into clickable cross-references.

### Operation-ideal kernel — U2 mining measurement (2026-07-07)

`sgt/core/mine.py` + `sgt/core/identity.py` (promoted from `experiments/patch_clustering/`,
plan `docs/plans/2026-07-06-001-feat-operation-ideal-kernel-plan.md`) mine an op stream with
def-use untangling, whole-file pseudo-symbols, and per-file layout/residue pseudo-symbols.
Measured against this repo's own history (57 commits, self-clone, first-parent) and the
synthetic `tests/laws/corpus.py` fixtures:

- **BET-A (untangling precision).** The one hand-labeled tangled-commit fixture (an unrelated
  function added in one file and an unrelated function edited in another, same commit, no
  calls between them) untangles into exactly the 2 expected ops — 1/1 on the fixture sample.
  Dogfooding on this repo: 56 of 57 commits produced more than one op (most commits here touch
  several unrelated symbols/files at once), which is the expected shape for untangling to be
  doing real work rather than a no-op.
- **Identity churn.** Kind distribution over 1401 mined ops: `add` 631, `rework` 396, `prune`
  322, `extend` 50, `move` 2. Only 2 ops resolved as a rename/move (fuzzy or hash-tier link)
  across 57 commits — this repo's history has few pure renames; most restructuring reads as
  add+prune pairs rather than linked moves, which is a fair reflection of how this codebase
  has actually evolved (few `git mv`-style renames, several delete-and-rewrite refactors).
  181 ops landed on whole-file (non-parseable-path) pseudo-symbols out of 1401 — this repo is
  mostly Python, so most of that is `.md`/`.json`/`.toml`/config churn, not a red flag.
- **Performance (BET-E precursor).** Mining is **not yet bounded**: 200s for 57 commits (~3.5s/
  commit) because `mine()` re-extracts and rebuilds the whole-codebase entity graph
  (`build_entity_graph`) from scratch on every commit to resolve def-use edges for untangling.
  This is O(commits × repo size) and will not meet R10's adoption-scale bar on a real corpus;
  it is flagged in `mine.py`'s own comments as the known thing to fix (incremental/cached
  entity-graph reuse across consecutive commits) before the BET-E large-corpus law in
  `tests/laws/corpus.py` (`SGT_LARGE_CORPUS_REPO`) can be run for real. Correctness first here,
  per the unit's scope; performance is U6/U10's dogfood-run problem to close before adoption.

### Operation-ideal kernel — U6 lens wiring (2026-07-07)

`sgt/core/lens.py` closes the round-trip loop: `get` mines what's new to the current ref since
its last witness (tracked per-ref in `.sgt/local/witness.json`), persists via the store (whose
provenance-merge on a content-address collision *is* the identification law), then reconstructs
the ref's ideal as every stored op whose provenance intersects that ref's own commit ancestry.
`put` runs `code(I)` to the working tree and commits with `Sgt-Op:` trailers. Verified against
real git operations: squash merge, rebase, a foreign (non-sgt) commit, and diverged branches all
behave per the ADR without special-casing any of them -- they fall out of content-addressing
plus ref-ancestry membership.

Two more real bugs surfaced wiring get/put together end to end (both fixed in this unit):

- `put()`'s `git add -A` staged `.sgt/ops/*` and `.sgt/lock` themselves, and the next `get()`
  mined them back as ordinary whole-file codebase content (since they're not `.py` files) --
  put-get was failing because *sgt's own state* was being re-mined as if it were user code.
  Fixed by excluding `.sgt/` from mine.py's diff loop entirely.
- A moved-then-later-touched symbol's canonical id anchors to its *original* surface path (the
  union-find in `_UnionFind` puts the earlier side as root), so an op on that symbol minted
  much later can carry a footprint key naming a path a given commit's own diff didn't touch --
  the commit changed the symbol's *current* file, not the file its canonical name still
  references. This isn't wrong, but it means "locality" has to be checked against the
  cumulative set of paths a symbol's whole history has touched, not just one commit's own
  diff -- documented at length in `tests/laws/test_roundtrip.py::test_locality`.

**Two deliberately deferred gaps (both RESOLVED in U7.5 — see the next entry):** at U6, `get()`
only mined *committed* history (not the live working tree) and `put()` overwrote the working tree
unconditionally — a combination that would silently discard uncommitted work once U8's verbs call
`put()` from user-facing code. Both are closed below before any verb ships.

### Operation-ideal kernel — U7.5 persist ref→ideal; safe working-tree get/put (2026-07-07)

Closes both gaps the U6 entry above flagged, plus a pre-existing `order.py` correctness bug found
while designing the fix (folded in here since it lives in the same file and the same "which op is
*the* producer of this version" reasoning).

- **`order.py` value-collision (found + fixed here).** `frontier`/`is_valid_ideal` keyed producer
  bookkeeping by `(symbol, after_value)` with dict overwrite, so a symbol whose content reverts to
  an earlier byte-identical value (add → modify → revert) silently lost its true tip and `code(I)`
  materialized `b""` instead of the reverted content. `is_valid_ideal` now checks downward-closure
  *existentially* (some in-ideal op produces the `(symbol, version)` pair, never "the" graph-picked
  producer); `frontier` walks forward via a `before_value → op_id` map, unambiguous by fork-freedom
  (which is keyed on `before_version`, not `after`). `chain_edges`/`reference_edges`/`upset`/
  `downset` retain the latent ambiguity but nothing exercises them yet — flagged in-code as a
  prerequisite for whichever U8 verb first computes an up-set/down-set for real.
- **Gap 1 — ref→ideal now persisted** in `.sgt/local/ideal.json` (`{ref_key: [op_ids]}`, parallel
  to `witness.json`). `get()` seeds it from the provenance scan on a ref's first contact, then
  treats the stored set as authoritative — so a future explicit ideal edit (U8 revert/pin) survives
  a re-`get()` instead of being re-derived back in from git ancestry, which has no way to represent
  "excluded though still in history". The durable set is committed-only; the dirty overlay (below)
  never lands in it, so discarding a working-tree edit simply stops it appearing next time.
- **Gap 2 — dirty-tree mining + `put()` guard.** `mine(include_dirty=True)` mines one virtual
  "pending commit" for the uncommitted working tree (diffed against HEAD via
  `GitBinding.working_tree_snapshot()`'s scratch `GIT_INDEX_FILE`), emitting ops with empty
  provenance until a real commit witnesses that content. `put()` now runs `get()` first (R9) and
  refuses via `DirtyWorkingTreeError` if the fold would overwrite an uncommitted change with
  different bytes, rather than clobbering it — the normal `get()`→`put()` flow, where the ideal
  already reproduces the edit, passes untouched.

Regression coverage: `tests/laws/corpus.py::revert_to_original` threaded through
`test_roundtrip.py`, `test_order.py::test_frontier_survives_a_revert_to_an_earlier_byte_identical_value`,
and three new `test_lens.py` scenarios (dirty-edit visible-but-not-persisted, `put()` refuses to
clobber, persisted ideal survives re-`get()`).

### Operation-ideal kernel — U8 ideal-edit verbs (2026-07-07)

`sgt/core/verbs.py` adds the first user verbs as exact ideal edits (pure `plan_*` + gated
`apply`, `--emit`-previewable, refusing any edit that would leave an invalid ideal): `revert`
(`I \ ↑X`), `pin` (truncate a chain at a version), `restore`/`cherry-pick` (`I ∪ ↓X`, cherry-pick
across refs surfacing chain forks and refusing — AE2), and `after` (a persisted declared edge).
Up/down-sets use new collision-safe, ideal-relative `order.upset_in`/`downset_in`; `apply`
materializes via `lens.put` then `lens.record_ideal` (persist the edited set + advance the witness,
so the edit survives the next `get()` instead of being re-mined as an inverse op). Full suite 497
passed, 1 skipped.

**Correctness refinement found here (tightens the U7.5 entry above):** U7.5 described
`is_valid_ideal`'s downward-closure as *existential* ("some in-ideal op produces the
`(symbol, version)`"). The verb-validity property test exposed that this is too weak: reverting a
chain *head* out of add→modify→revert leaves `{modify, revert}`, an **originless cycle** (modify's
`before` is produced by revert and vice-versa) that the existential check wrongly accepts but which
has no chain head to fold — `frontier` then `KeyError`s. The fix replaces the existential check
with **grounding** (`order._grounded`, a least fixpoint from `before=None` heads through real
production): an op is valid only once it bottoms out at a real add. Grounding still reasons about
*versions produced* (never a single canonical producer), so it keeps the U7.5 collision immunity
while additionally rejecting the cycle. `upset_in` is then simply `ideal \ _grounded(ideal - {X})`
— reverting a head correctly removes the whole chain. The old universe-level `upset`/`downset`
(collision-unsafe) are retained only for `tests/core/test_order.py`; verbs use the `_in` forms.

Regression coverage: `tests/core/test_verbs.py` (9 scenarios incl. the AE2 fork refusal + the
every-verb-output-is-a-valid-ideal property over `linear_history` and `revert_to_original`) and 5
new `test_order.py` cases pinning the grounding / `upset_in` / `downset_in` collision behavior.

### Operation-ideal kernel — U9 the oracle (2026-07-08)

`sgt/core/oracle.py` adds async tiered build/test verdicts (R13), with the "async" requirement
satisfied by construction rather than a background thread: `verbs.apply`/`lens.put` never import
or call this module, so materialization is unconditionally non-blocking, and a verdict is simply
absent ("pending") until `sgt oracle run` is invoked explicitly. A verdict is keyed to a hash of
the exact `Ideal.op_ids` it was run against (`oracle.ideal_key`), never to a ref, so an edit that
changes the ideal produces a fresh key rather than needing any reset logic.

- **Config vs. verdict split.** `.sgt/oracle.json` (committed, team-shared -- `sgt.config.
  load_oracle_config`, the first `.sgt/`-scoped config format in the repo, plain JSON rather than
  TOML since `requires-python = ">=3.10"` predates stdlib `tomllib`) declares tier commands in
  run order. `.sgt/local/oracle.json` (gitignored) is the per-ideal-key verdict cache, following
  `lens.py`'s witness/ideal/declared small-JSON-table convention exactly (no atomic-rename --
  this is an advisory cache, not content-addressed).
- **Pipeline semantics.** `oracle.run(repo, tier=None)` runs all configured tiers in declared
  order, stopping at the first failure (a real CI shape); `tier="name"` re-runs just that one,
  replacing its stale result regardless of pipeline position. `oracle.override` records a human
  verdict (status/reason/by/timestamp) that supersedes tier results in `overall_status`.
- **`run`/`verdict_for`/`override` all take an explicit `ideal` parameter** (defaulting to
  `lens.current_ideal`) rather than hard-coding "current" -- this is deliberate: U11's rewrite
  verbs need to gate landing on the verdict for a *candidate* ideal that isn't committed yet, and
  accepting `Ideal` as a parameter here makes that compose for free later instead of requiring a
  second oracle API.
- **`state_view` gained `oracle_configured` (additive) and a real `oracle_verdict`** (was a
  literal `None` placeholder since U7). Golden snapshots regenerated for the additive key only
  (R21) -- diff reviewed, no other drift.
- **CLI:** `sgt oracle run [--tier NAME]` / `sgt oracle override --status pass|fail --reason
  "..." [--by NAME]`, following the `_fsck`/`_opt_value` dispatch pattern. Verified by hand
  end-to-end against a scratch repo (no-config warning, a 2-tier pipeline with one failing tier,
  override, and `sgt state --json` surfacing the verdict) before committing.

Regression coverage: `tests/core/test_oracle.py` (7 scenarios: no-config warns and writes
nothing, failing tier records exit code + output tail, pipeline stops at first failure, override
supersedes with attribution, re-run replaces a stale record, verdict keyed to the ideal resets on
an edit, and a materializing verb with no oracle configured never touches the verdict table).

### Operation-ideal kernel — U10 delete the legacy mechanisms; flip CLI/MCP onto the kernel (2026-07-08)

The one-way door (per the plan's Risks section and memory): removed every pre-kernel subsystem
now that U8/U9 + the round-trip laws are green. Deleted outright: `sgt/effects/`, `sgt/engine/`,
`sgt/orchestrate/` (whole package -- `loop.py`/`sync.py`/`constraint.py` backed only verbs being
retired, so "rewrite" collapsed to "delete"), `sgt/store/{graph,oplog,replica,clock}.py`,
`sgt/decisions/`, `sgt/lifecycle/`, `sgt/merge/`, `sgt/entities/cluster.py`, `sgt/project.py`,
`sgt/agents/distill.py` + `sgt/agents/planner.py`. Kept, unused-for-now (no legacy imports,
self-contained -- candidates for U12/U14 reuse rather than legacy carryover): `sgt/agents/
resolve.py`, `intent_dsl.py`, `plan_context.py`. Kept because the kernel itself depends on them
despite the plan's file list being silent on them: `sgt/entities/graph.py`, `sgt/entities/
extract.py`, `sgt/store/gitbind.py`.

**A real, flagged product regression, not an oversight.** Feature-lens verbs (`merge`/`split`/
`rename`/`move`, `sgt map`) and the agentic-loop verbs (`plan`/`checkpoint`/drift) have no kernel
backing until U12/U14 -- retired from `_VERBS`/help/MCP rather than left half-working against a
deleted subsystem, per the plan's own acceptance bar ("every CLI verb either works on the kernel
or is removed from help"). `sgt revert`/`restore` flip onto `sgt/core/verbs.py`; `sgt init` flips
onto `sgt.core.lens.init`. MCP's 13 legacy tools (none imported `sgt.core`) drop to the
kernel-parity set: `sgt_revert`, `sgt_restore`, `sgt_init`, `sgt_log`, `sgt_state`, `sgt_diff`,
`sgt_fsck`, `sgt_oracle_run`.

`sgt/tui/app.py`/`color.py` and `editor/vscode/` were left on disk but unregistered (the `tui`
verb removed from dispatch) rather than deleted-then-recreated -- both already import views U10
deletes (`export_view`/`show_view`/`status_view`/`Project`), so they're non-functional either way,
and U13's own file list already names them for a real rewrite once feature-lens views exist.

Characterization-first execution: the two legacy-`CORPUS` golden cases (`linear_deps`,
`fanout_multifile`, built from the now-deleted `Project`) were deleted along with `tests/golden/
corpus.py`'s `capture_views`/`CORPUS`; the kernel-backed `KERNEL_CORPUS` cases were the surviving
characterization net and stayed green throughout the flip. `from __future__` import-ordering and
module-level-binding regression coverage (originally in `tests/effects/`) was confirmed already
ported into `tests/core/test_fold.py`/`test_mine.py` during U5/U6 before deleting their old homes.

Regression coverage: full suite green post-flip; `grep -rn "EffectLog|NodeStatus|SemanticGraph|
VersionVector" sgt/` returns nothing; `sgt --help` lists only surviving verbs; a manual `sgt init
&& sgt revert <op> --emit` smoke-run against a scratch repo.

### Operation-ideal kernel — U11 rewrite verbs: the explicit escape hatch (2026-07-08)

`sgt/core/rewrite.py` adds R14's escape hatch for edits the ideal algebra can't express exactly:
`merge_op`/`split_op`/`transplant`/`revert_keep_dependents` each compute the exact part and draft
hollow op(s) off-chain (`Op.off_chain`, `Store.add_hollow` -- substrate shipped in U3, never
exercised until now); `stage`/`fulfill` supply real images (agent-authored, or `from_tree=True`
reading the working tree entity-by-entity) and fold+write the candidate to the working tree
**without committing**; `land` is the only step that commits, and refuses unless the oracle's
verdict for that *exact* candidate ideal is "pass" (or an attributed override resolves to one) --
R14's landing gate, distinct from R13's async, non-blocking *materialization* gate every other
verb uses. `identity_split`/`identity_join` correct the tiered matcher itself, not a chain -- no
hollow op involved.

**Correction to the plan's own sketch, found during implementation (recorded, not silently
changed -- see `rewrite.py`'s module docstring for the full argument).** The `structured-juggling-
cocoa.md` execution plan proposed that `merge-op`'s drafted hollow `requires` the *other* fork
tip's produced version, on the theory that the existing reference-edge machinery would then place
both tips below the merge op "for free", needing zero `order.py` changes. It does not: `requires`-
grounding (`order._grounded`) demands the referenced version's producer be a member of the *same*
ideal -- and that producer is exactly the other fork tip, which still shares `(symbol,
before_version)` with the first tip, so `is_fork_free` correctly rejects the union as a genuine
fork regardless of which tip is nominally the "chain parent". There is no way to satisfy
`requires`-grounding for the other tip's version without either including that tip (which forks)
or weakening fork detection itself (which U8's cherry-pick refusal, AE2, depends on) -- the two
invariants are in genuine tension for this exact shape, not solvable by a footprint-assignment
choice. `merge_op` instead drafts a plain chain-extension of the *ideal's own* tip; the other
tip's identity rides only in the drafted op's advisory `intent`, for the agent/human authoring the
merge to read both diffs and reconcile them by hand -- still "resolves the AE2-style fork" (the
draft lands cleanly, no fork, once fulfilled) and still "explicit rewrite, oracle-gated" per R14,
just via chain-extension + advisory provenance rather than a structural two-parent edge.

**`split-op`'s "no agent involvement for the tail" is exact, not approximate.** The drafted
hollow's `before` = the original op's own `before_version`; once its agent-authored intermediate
image is fulfilled, `stage` mints a second op automatically: `before` = the intermediate's own
now-known `after_version`, `after` = the *original op's own after-image, reused verbatim*. Net
materialized content is byte-identical before and after a split -- the chain gains a checkpoint
(`original(before) -> intermediate(agent) -> tail(original's bytes)`) that a future `pin`/`revert`
can target; it does not change what's on disk. Verified end-to-end against a real fixture (a
two-concern `process()` mined from git, split into an intermediate cut, chain and final bytes
both asserted).

**`revert --keep-dependents` scope (v1): one hop only.** Computes the target's full `upset_in`
(exactly what a plain revert would drop) but only drafts a continuation hollow for *direct*
reference-edge dependents (`order.reference_edges` filtered to edges originating at the target);
anything further downstream is dropped exactly like a plain revert. A grand-dependent chain of
continuations is a real gap (not exercised by the corpus, not requested by the plan's test
scenario, which names only "keeps dependents' symbols present" without a transitivity claim) --
named here as a v1 boundary, not an oversight, per CLAUDE.md's guidance against building for
unrequested cases.

**Identity constraints (`identity_split`/`identity_join`) needed one hardening beyond the plan's
sketch.** `sgt.config.IdentityConstraints` (committed `.sgt/identity_constraints.json`, loaded
once per `mine()` call and threaded through `sgt.core.identity.match_pair`/`link_residual`/
`_link_pass`) sorts pairs on load/save, but `_link_pass` additionally re-normalizes `never_link`
to a sorted pair on every lookup rather than trusting the caller's storage convention -- a caller
constructing `IdentityConstraints` directly with an unsorted pair (any code that isn't
`load_identity_constraints`, e.g. a test) would otherwise silently fail to match, since frozenset
membership needs the exact tuple order the checking side computes. `force_link` needed no such
fix -- its lookup already tries both orderings via `by_before.get(x) or by_before.get(y)`.
Confirmed a rename genuinely needs the *fuzzy* tier to link once the name itself is part of the
compared bytes (a `def foo` -> `def bar` header changes both `content_hash` and `structural_hash`,
since the identifier leaf's text is part of both) -- tiers 2/2b mostly catch pure moves with
unchanged bytes, not renames; the `identity_split` test therefore uses a mostly-unchanged,
multi-line body (single differing token) to exercise a genuine fuzzy-tier link before splitting it.

**Draft/stage/land persistence** lives under `.sgt/local/{drafts,staged}.json` (gitignored,
following `lens.py`'s small-JSON-table convention) so `sgt merge-op`/`split-op`/`transplant`/
`revert --keep-dependents` (one process) and the later `sgt fulfill <draft-id> --from-tree` /
`sgt land` (a separate process, once the agent has edited the tree) can hand off across CLI
invocations without keeping the draft object alive in memory. A draft's hollow files are deleted
from `.sgt/local/hollow/` on fulfillment (not merely superseded) so `sgt.api.rewrite_view`'s
pending-drafts list stays accurate.

CLI: `sgt merge-op <a> <b>`, `sgt split-op <op>`, `sgt transplant <op>... --onto <ref>`, `sgt
identity split|join <a> <b>`, `sgt fulfill <draft-id> --from-tree`, `sgt land [--message ...]
[--override pass|fail --reason "..."]`, and `sgt revert <ref> --keep-dependents` (a flag on the
existing U8/U10 verb, routed to `rewrite.revert_keep_dependents` instead of `verbs.revert`).
`sgt.api.rewrite_view` is the review projection (pending drafts' hollow ops + the staged
candidate's oracle status), read-only and additive per R21.

Regression coverage: `tests/core/test_rewrite.py` (14 scenarios) -- merge-op drafts + refuses
when nothing's forked + land gating (pending refuses, failing override refuses, passing override
lands and the ideal is valid); split-op's original/intermediate/after chain and byte-identical
final content; revert-keep-dependents drops the target and its old dependent op but keeps the
dependent's symbol live via a fresh hollow (plus a refusal case); transplant's destination-tip
`before_version` (AE3) and an unresolvable-source refusal; `never_link`/`force_link` at the
matcher level and end-to-end through a real `mine()` call (split blocks a would-be fuzzy link,
join forces one, both persist and a *subsequent* `mine()` respects them); `rewrite_view` reporting
pending drafts and a staged candidate's oracle status. Full suite: 211 passed, 1 skipped.

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
