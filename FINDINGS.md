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

### Operation-ideal kernel — general-code robustness audit: the byte-fidelity fold (2026-07-08)

Before starting U12 (Phase P3, feature lens), an audit of U1-U11 found the kernel's correctness
was verified only against top-level-function Python: every fixture in `tests/laws/corpus.py` was
bare `def`s with no class, method, decorator, or file mixing a module-level statement with an
entity. Under that blind spot, four defect classes reproduced empirically on ordinary real-world
code, all silent (no error, no crash -- just wrong bytes or missing content):

1. **Top-level decorators misapplied.** `extract.py`'s span for a def/class started at
   `def`/`class`, excluding a Python `decorated_definition` parent or TS `export_statement`
   wrapper. Two decorated top-level functions materialized with *both* decorators piled onto the
   first and *none* on the second -- e.g. two Flask routes silently swap handlers.
2. **Duplicate entity ids -> silent code loss.** `id = file::name`; `@overload` stubs, a
   `@property` getter beside its `@x.setter`, and similar same-name groups collided, and the
   fold's `entity_images[name]` was last-write-wins (three `def f` in, one materialized).
3. **Line-based addressing (`mine.py`'s `_entity_bytes`/`_residue_lines`, `splitlines()` +
   `"\n".join`) cannot be byte-faithful on arbitrary bytes.** CRLF silently became LF; a form
   feed or a U+2028 line separator inside a string literal truncated the image; a non-UTF-8
   tracked file corrupted permanently via `decode("utf-8", "replace")` (U+FFFD substitution);
   `GitBinding._git`'s `subprocess.run(text=True)` (strict decode) *crashed outright* on any
   non-UTF-8 byte anywhere in a `git diff`'s output, since diff embeds the changed file's raw
   content inline.
4. **Residue was one position-agnostic blob per file**, joined with a hardcoded `b"\n\n\n"`: a
   trailing `if __name__ == "__main__":` guard rendered at file *top*; blank-line counts
   inflated; every materialized file got a synthetic trailing `\n` regardless of the original.

**Fix: byte-native addressing + positional residue segments, not a residue patch.** Tree-sitter
parses raw bytes directly (verified: `has_error=False` on CRLF, latin-1, and form-feed input; a
full-file byte partition `raw[:a] + raw[a:b] + raw[b:]` reconstructs any input exactly) -- so the
fold became a pure verbatim concatenation of anchored segments with **zero synthesized bytes**
anywhere (no separator, no derived content, no trailing newline). This is a stronger property
than "handles more constructs": *any* unrecognized construct (a TS enum, a lambda, an obscure
language feature) just becomes faithful residue and still round-trips byte-for-byte -- entity
recognition only governs revert/cherry-pick *granularity*, never correctness.

- `sgt/entities/extract.py`: `Entity` gained `start_byte`/`end_byte` (the only fields content is
  ever sliced by now; `start_line`/`end_line` survive as a display-only derivative).
  `extract_file`/`extract_codebase` accept `bytes | str` (str auto-encoded once, losslessly, for
  ergonomic hand-written callers). `_entity_span` climbs through a decorator/export wrapper
  parent (`_climb_declaration`, empirically verified against both tree-sitter-python and
  tree-sitter-typescript's actual grammar shapes -- notably a TS class member's decorator is a
  *sibling*, not a child or wrapping parent, so `_widen_over_decorator_siblings` handles that
  case separately from the wrapper climb). `_coalesce` folds a contiguous same-id group (no
  foreign entity's span between its members, checked with containment-of-nested-children
  excluded so a duplicated class's own methods don't block merging the class) into one entity
  spanning the verbatim union; a non-contiguous collision (rare) falls back to a stable
  document-order ordinal suffix so the "unique id per file" invariant holds unconditionally.
  `_content_hash_range`/`_structural_hash_range` generalize the old single-node hashers to a
  sibling range (used by both decorator-widening and coalescing).
- `sgt/core/mine.py`: `_entity_bytes` is a raw slice (`source[start_byte:end_byte]`), no decode.
  `_residue_lines` (one blob) replaced by `_residue_segments`/`_RESIDUE_HEAD`: one segment per
  gap between top-level entities, keyed by the preceding entity's name (or the HEAD sentinel).
  Every git-blob read in the mining hot path (`new_bytes`/`old_raw`/cross-file-move reads) is now
  raw bytes end to end -- the lossy `blob.decode("utf-8", "replace")` step is gone entirely.
  **Known v1 boundary, documented, not fixed:** a residue segment's chain is keyed on its anchor
  entity's *current* name; renaming the anchor orphans the gap's chain (a fresh add under the new
  name) rather than surviving the rename -- the same tier as the anchor-fact mechanism's own
  pre-existing "never revised" limitation.
- `sgt/core/identity.py`: `snapshot` tokenizes via a raw byte-range split (`bytes.split()`), no
  `splitlines()`/decode; `Snap.tokens` is now `frozenset[bytes]` (fuzzy-tier tokens are a
  heuristic match signal only, never stored/materialized, so lossy comparison there is fine).
- `sgt/core/fold.py`: `_fold_file` concatenates residue + entities in anchor order verbatim,
  `b"".join(parts)`, nothing else. **`_derived_imports` deleted outright (a deliberate R6
  deviation, not an oversight):** once the fold is pure verbatim splicing it cannot also rewrite
  an import block without breaking exactly the byte-fidelity this rewrite exists to guarantee --
  and auto-deriving never actually worked for calls inside methods anyway (`requires` attaches to
  the method symbol, not the file-level entity list, confirmed empirically: a clean cross-file
  call inside a top-level function DID populate `requires`, but the derivation was never
  exercised for the common case of a call inside a *method*). An import is just residue now;
  reverting its only consumer leaves it exactly where it was. Surfacing "this revert leaves an
  unused import" is a verb/preview-layer concern (the reference edges + `verb_preview_view`'s
  before/after materialized diff already carry the information) -- no new verb was built this
  pass; it stays deferred, same as the plan's other "Deferred to Follow-Up Work" items.
- `sgt/store/gitbind.py`: `GitBinding._git` decodes with `errors="replace"` instead of strict --
  every caller only reads ASCII-safe structural markers (hunk headers, `+++ b/path`, name-status
  letters) out of `_git`'s output, never content bytes (those always go through `blob_bytes`
  separately), so the lossy replacement here never touches anything byte-fidelity depends on.
- `sgt/core/op.py`: `_symbol_kind` recognizes `__residue__::{anchor}` (was the exact string
  `__residue__`). `MINER_VERSION` bumped `1` -> `2` per R12 (mining/addressing logic changed);
  both golden snapshots regenerated (`SGT_UPDATE_GOLDEN=1`) and reviewed -- the diffs are exactly
  the expected shape (op-id churn from the version bump, `+1` op per file from the residue-blob
  split into HEAD+per-entity segments), nothing structurally surprising.
- **Commutativity re-verified, not just re-stated, under the new model:** two branches each
  inserting a *different* entity at a *different* anchor mine, union by content-addressed op id
  (the shared base ops collide automatically), and materialize correctly interleaved with the
  original gaps intact (`tests/laws/corpus.py::_case_commuting_features` +
  `test_anchor_disjoint_additions_compose`). Two branches editing the *same* residue segment
  differently is a genuine chain fork, detected by the same `is_fork_free` machinery an entity
  chain fork uses, no special-casing (`_case_residue_fork` + the paired fork law) -- confirming
  residue segments are ordinary chains, not a second class of citizen.

Regression coverage: 10 new corpus fixtures across byte/structural/layout/TS-shape layers
(`crlf_endings`, `no_trailing_newline`, `formfeed_and_unicode_sep`, `latin1_encoded`,
`decorated_routes`, `overload_group`, `property_pair`, `class_with_methods`, `imports_and_main`,
`ts_export_decorated`), each parametrized through the byte-fidelity law, the real-mining
round-trip law, and a new no-duplicate-entity-ids invariant law; a dedicated decorator-never-
strands-in-residue law; the two commutativity/fork laws above. Full suite green (exit 0, three
independent full runs); both golden snapshots regenerated and reviewed.

### Operation-ideal kernel — U12 hierarchical feature tree over ops (2026-07-08)

`sgt/lens/{cluster,tree,pins,label}.py` (promoted from `experiments/patch_clustering/` per the
plan, re-sourced from the kernel op store instead of `patches.json`) build the "map": a
hierarchical feature tree clustering every alive content-bearing symbol over a fused
co-change⊕structural⊕scope coupling graph, with binary-search arity control (D2), durable pins
(D3), Greene identity across runs (D5), and cached LLM labeling with a deterministic offline
fallback (D6). Pure engine + persistence; `sgt map`, feature verbs, and blame are U13.

**D2 — arity control (binary-search CPM resolution, target 5–9 children).** Replaces the
experiment's fixed `GAMMAS`/`ESCALATE` ladder with a log-scale binary search over `gamma`
(`[1e-4, 1.0]`, ≤20 iterations) at each split, scoring by the count of ≥`MIN_LANE` groups; the
`global MAX_DEPTH` mutation is gone (`max_depth` is a threaded param). On sgt's own store the tree
has 9 internal nodes with child counts `[4, 4, 6, 7, 7, 9, 9, 9, 9]` — 7/9 splits land inside
`[5, 9]`, the two `4`s carry a `closest_arity` reason (the search's nearest achievable count when
no in-range `gamma` exists), never a silent violation. NO-ORPHAN holds (every one of the 1464
alive symbols lands in exactly one leaf; partition, not cover — a test asserts this).

**D5 — Greene identity θ.** `match_identities` does mutual-best member-overlap (Jaccard) matching
between the previous committed tree (`.sgt/tree/tree.json`) and a fresh build: continuation keeps
the old feature id, one-old-to-many is a split, many-old-to-one a merge, unmatched-new a birth,
unmatched-old a death. θ defaults to **0.5** (the Greene-paper standard). The one recorded
measurement (per the plan's "documented starting point, not a research task"): re-clustering the
same store with no history change is **100% continuation, zero id churn** at θ=0.5 (test
`test_rebuild_on_unchanged_history_renames_nothing`), and an `assign`-pinned member holds its
feature id across ten re-clusters (D3 override beats Greene —
`test_assign_pin_overrides_greene_and_survives_reruns`).

**BET-C — MoJoFM vs a hand-labeled package map (R22).** Measured on sgt's own `.sgt/ops/` store
(5714 mined ops), gold = top-level packages, MoJo distance via the accepted greedy (Wen & Tzerpos
2004), denominator = the all-singletons distance `n − |gold|`; script
`scripts/bet_c_mojofm.py`, reproducible:

- **Product-focused (the plan's "6–8 packages"): sgt/ symbols only, 480 syms, 9 gold packages
  (`core`, `entities`, `store`, `cli`+`api`, `config`, `mcp`, `tui`, `agents`, root): MoJoFM =
  63.9%.**
- Whole corpus (1464 syms, 15 gold groups incl. `docs`/`tests`/`experiments`): MoJoFM = 47.6%.

63.9% is a fair result for a co-change/structural/scope clustering scored against a *package*
gold — features here legitimately cross packages (a verb spans `cli`+`api`+`core`), so a
by-package gold under-credits real feature lanes; the plan treats BET-C as measured-and-recorded,
not a gate. A materially higher number would need either a feature-labeled gold or heavier pin
curation, both of which are the intended UX (D3), not a clustering fix.

**Finding — sgt's own 67-commit history is not a valid ideal.** Reconstructing the current ref's
ideal over the full self-history hits ~440 forked symbol chains (functions deleted in U10 then
similar names re-added → two competing tips; plus symbols whose genesis op isn't in the
provenance-reconstructed set), so `lens.get()` and `order.frontier` **correctly refuse** it — the
chain-fork guard doing its job on real messy history, not a bug. BET-C therefore takes each
symbol's tip via a fork-tolerant frontier (in-set op whose after-version no in-set op consumes;
fork tie-break = largest op id) purely to obtain the current codebase's symbol set for
measurement; co-change still reads the full store, structural edges the current tree. This is a
measurement convenience, flagged so it's a decision: dogfooding sgt on itself at HEAD would want
a genesis-horizon `init` (R10) or explicit `merge-op`/`pin` fork resolution (U11), not the full
provenance union.

Regression coverage: `tests/lens/` (47 tests) — `test_cluster.py` (hub-strip, structural
reduction, scope grouping, fused = sum), `test_tree.py` (binary-search arity + reasons,
NO-ORPHAN partition, plurality-vote op→leaf, all five Greene events, load/save round-trip,
unchanged-history continuity, assign-pin survives ten re-runs, DEDUP sibling-merge + folder
disambiguation + op_leaf remap, deterministic offline labels), `test_pins.py` (all three
contradiction cases never raise, must-link contraction, cannot-link reassignment),
`test_label.py` (member-hash cache hit/miss, fallback tag re-attempt vs LLM-tag skip).
`igraph`/`leidenalg` declared under `[project.optional-dependencies.lens]` (first real consumer).
Full suite: 292 passed, 1 skipped (exit 0).

### Operation-ideal kernel — U13 feature verbs and surface re-pointing (2026-07-09)

`sgt/lens/verbs.py` adds `merge`/`split`/`rename`/`move` as `plan_*`/`apply_*` pairs (mirroring
`sgt.core.verbs`'s shape) that patch the loaded `tree.json` plus write one durable pin in the
same call, and `plan_revert_feature` bridges a feature ref to the kernel algebra by resolving it
to its op-set and reusing `order.upset_in`/`core_verbs._validated` verbatim -- a feature revert
refuses on a chain fork exactly as a single-op revert does. All four metadata verbs are
byte-neutral **by construction**, not merely by test: `code(I)` (`sgt.core.fold.code`) is a pure
function of ops + ideal and never reads `.sgt/tree/` or `.sgt/pins/`, so there is no code path by
which any of them could touch a materialized byte (`test_feature_verbs_never_change_materialized_bytes`
exercises this as a regression, not a load-bearing guarantee).

`sgt/api.py` gained `map_view` (pure read of `tree.json`, empty-but-well-shaped when no tree has
been built), `blame_view` (`sym -> max-op-in-I -> feature` via the frontier, one lookup per live
entity, an unassigned tip omitted rather than guessed at), and `status_view` (file/symbol/feature
counts, R7 coverage, oracle status, and working-tree drift -- paths whose on-disk bytes no longer
match `code(current_ideal)`). `sgt map` replaces `sgt graph` in the CLI; `sgt feature
merge/split/rename/move` and `sgt blame`/`sgt status` are new verbs.

**TUI and VS Code extension were rewritten onto the new projection, not patched.** The old
decision-DAG webview (`decisionView.ts`, `decision.js`/`.css`, the activity sidecar, codelens,
hover-preview machinery) is deleted outright rather than adapted -- there is no kernel-backed
equivalent of "decision" as a first-class node, so porting it would have meant inventing one.
`sgt/tui/app.py` is a from-scratch Textual app over `map_view`/`status_view`: browse the tree,
preview/apply a feature revert, rename a feature, all through the same `sgt.api` projection the
CLI and VS Code consume. The editor extension keeps the OKLCH hue-is-identity discipline (a
feature's color is stable across the TUI, the editor gutter, and -- previously -- the graph
webview) but now sources it from `map_view` instead of the deleted decision graph.

Regression coverage: `tests/lens/test_feature_verbs.py` (12 tests) covers every verb's preview/
apply split, the two refusal paths (self-merge, unresolvable ref), the byte-neutrality property,
blame resolution, label-pin round-trip, and rename surviving a `sgt map` re-cluster (Greene
identity holding the id stable across the metadata edit). Golden snapshots regenerated for the
new `map_view`/`status_view`/`blame_view` shapes.

**Left for the next pass, not a defect:** this unit's own plan text and FINDINGS.md were not
updated when the code shipped (commit `f000a7b`, 2026-07-09) -- this entry and the plan's Status
line close that gap after the fact, written from the shipped diff rather than from a running log.

### Operation-ideal kernel — U14 plan intake, checkpoint matching, drift review (2026-07-09)

`sgt/loop/plan.py` + `sgt/loop/match.py` implement the agentic-loop substrate: `intake` decomposes
plan text into one hollow op per step (LLM-first via `sgt.config.get_client`, deterministic
numbered-list/paragraph split on any failure -- no API key required), each hollow off-chain
(`Store.add_hollow`, `Op.off_chain=True`) so a prediction can never fork a chain or block a human
editing the same symbol mid-plan. Sessions persist in `.sgt/local/plan_sessions.json`;
`baseline_op_ids` is the store's op-id set at intake time, so `compute_checkpoint` only ever
considers ops mined *since*.

`compute_checkpoint` is pure and offline: per active session, footprint-overlap (Jaccard over
real, non-`__plan__::` symbols) at or above `THRESHOLD=0.3` between a pending step's hollow and
an op mined since baseline is a candidate edge; candidate edges union-find into n:m groups, so
"one commit fulfills two steps" and "two commits fulfill one step" both fall out of the same
mechanism rather than needing special-casing. An op that joins no group for a session is drift for
that session; it's global drift only if every session considering it new also calls it drift (a
real match in one session isn't overridden by a stale, unrelated session B). `confirm_match` is
the sole writer -- it records `.sgt/local/plan_matches.json` (op id -> session/hollow ids/intent,
a side table; the immutable content-addressed `Op` itself is never rewritten to carry the
rationale), marks the confirmed steps `matched`, and deletes their now-consumed hollow files, so a
confirmed match can never resurface as drift later.

Surfaced through `sgt plan`/`sgt checkpoint`/`sgt drift` (CLI), `sgt_plan_intake`/`sgt_checkpoint`/
`sgt_drift` (MCP, tested through the pure `handle_request` dispatch per existing convention), and
`plan_view`/`drift_view` (`sgt.api`). The VS Code extension gained `src/plan.ts`
(`PlanCodeLensProvider`/`PlanDiffProvider`/`PlanStatusBar`) showing matched/drifted lines inline
and a status-bar summary, reusing the same views.

**Note on provenance, not a defect:** this unit's implementation, tests, MCP wiring, and VS Code
surface landed bundled inside commit `6a1557b`, whose message describes only an unrelated
`mine.py`/`cli.py` fix -- the bundling was discovered during this review, not flagged at commit
time. The code itself is complete against the plan's test-scenario list (three-step plan drafts
three off-chain hollows; a commit fulfilling two hollows shows the 2:1 mapping; an unpredicted op
surfaces as drift; an abandoned session's hollows are swept; a human edit to a hollow-predicted
symbol creates no phantom fork since the hollow is off-chain; intake degrades gracefully offline)
and the full suite is green, but neither the plan doc nor this file had a closing entry until now.

Regression coverage: `tests/loop/test_plan.py` (9 tests, including one live-LLM-gated grounding
test), `tests/loop/test_match.py` (9 tests: n:m grouping, drift classification, baseline
exclusion, non-active-session skip, hollow-never-enters-ideal, confirm/never-resurfaces-as-drift),
`tests/mcp/test_server.py` (plan/checkpoint/drift tool round-trips).

**Known gap surfaced by this review, deferred to U15:** declared order edges (`sgt after`,
`.sgt/local/declared.json`) live under the gitignored `.sgt/local/` tree, same as the ref->ideal
table and oracle verdicts -- so two clones never see each other's declared edges through git at
all today. R19 ("declared-edge cycles introduced by union are detected and surfaced") presumes
declared edges *do* travel between clones; U15 needs to either promote `declared.json` to a
committed location or define an explicit exchange path for it, or the requirement is vacuous by
construction.

### Operation-ideal kernel — U15 sync: op-store union and tree reconciliation (2026-07-09)

`sgt/core/sync.py` implements `sync(repo, remote, branch) -> SyncReport`: `lens.get(repo)` absorbs
local reality first (R9) and sync refuses on a dirty tree, same guard as `put`. It fetches
`remote/branch`, and a `theirs` already an ancestor of `ours` is a no-op -- what makes a second
`sync` idempotent. Op-store union is nearly free: `git merge --no-commit -X ours theirs_sha` brings
in every op file git can merge without conflict (distinct content-addressed paths never collide),
then every op path under theirs' tree is re-added via `Store.add_bytes` to re-union provenance that
`-X ours` would otherwise drop on a same-id collision (both clones independently mining the
identical edit). `order.forks` (new) over the unioned ideal surfaces same-symbol chain forks with
the exact `sgt merge-op <a> <b>` remedy and aborts the merge uncommitted, rather than picking a
side. Pins union via `reconcile.union_pins` (dict-merge `assign`/`labels` theirs-wins, set-union
`must_link`/`cannot_link`, then `find_contradictions` reports but never blocks — ordinary
re-pinning is not a contradiction, only a genuine must-link/assign clash is). The feature tree is
*rebuilt* from the unioned op store and Greene-matched against ours' last-committed tree
(`reconcile.reconcile_tree`) rather than merging two stored trees, since the tree is a deterministic
clustering overlay, not a source of truth.

**Closes the U14 FINDINGS gap:** declared edges (`sgt after`) are now committed at
`.sgt/declared.json` (previously gitignored `.sgt/local/declared.json`), so they travel between
clones through git exactly as ops do. A teammate's committed ideal is read purely from their
fetched tip commit's `Sgt-Op:` trailers (`parse_op_ids(gb.commit_message(theirs_sha))`) — no
checkout needed; this is the same trailer convention `put` already writes on every real commit.

**Real bug found and fixed during test-writing (not anticipated by the plan):** `sync` originally
constructed the merged `Ideal` from the full unioned declared-edge set before ever checking for
cycles. A genuinely cyclic union — two clones each declaring the opposite order on the same pair
(`foo <= bar` on one, `bar <= foo` on the other) — made `order.is_valid_ideal`'s grounding fixpoint
deadlock (neither op could ground, since each needed the other as an ungroundable declared
predecessor), raising `ValueError` instead of the graceful "cycle detected, edges need retracting"
report the plan calls for. Fixed by computing `order.find_declared_cycles` *before* building the
ideal and excluding cyclic edges from the fold (`usable_declared = declared - set(cycles)`), while
still persisting and reporting the full union so `sgt after` retraction has something to act on.

**Test-fixture gotcha worth naming for future two-clone tests:** a plain `GitBinding.commit_all()`
with no `trailers=` argument (the pattern every other test module in this repo uses, since local
mining reads the commit diff directly and never needs trailers) is *not* sufficient for a commit
that will become a synced ref's tip — `sync` depends entirely on `Sgt-Op:` trailers to read a
remote ref's ideal, since it never checks that ref out. Fixtures that push a commit for `sgt sync`
tests must route it through `lens.put` (or otherwise pass `trailers=format_op_trailers(...)`) so
the tip carries them, exactly as real `sgt` usage always does outside tests.

Regression coverage: `tests/core/test_sync.py` (6 tests) — AE4 disjoint-symbol merge with zero
interaction; idempotence and double-mine determinism (U1 law) across both clones; a same-symbol
fork surfaced with the `merge-op` remedy, merge aborted, tree left clean; the identification law at
sync time (an op independently mined identically on both clones dedups to one id with both sides'
provenance, `ops_added == 0`); a pin contradiction (`assign_conflict_in_must_link_group`) reported
while the sync still merges; a declared-edge cycle from two replicas reported, with the (now
non-cyclic-blocking) declared edges traveling to both sides. Full suite green.

### Collaboration & review foundations — U16 freeze the ground (2026-07-10)

The refactor plan (`docs/plans/2026-07-10-001-...`) opens by freezing behavior before touching it.
Two harnesses landed as the falsifiable ground everything after builds on: `tests/golden/` captures
each CLI verb's text and `--json` bytes (the C11 byte-parity contract — the non-human backstop for
surface drift across the U18 CLI move), and `tests/laws/test_convergence.py` is an N-replica
sync-schedule harness with one named test per convergence law (LAW-0 determinism, LAW-U order
independence, LAW-I idempotence, LAW-F fork completeness/soundness, LAW-R resolutions travel, LAW-G
green-to-green, LAW-L locality). LAW-G lands `xfail(strict=True)` — deliberately, it is the
oracle-gated law U23 turns green; a strict xfail means the suite fails loudly the day it starts
passing by accident, forcing the flip to be intentional. The two dead layout tests the plan's Open
Questions flagged (`test_decision_layout.py`, `test_color_parity.py`) were checked and kept — they
still guard live rail-webview output.

### Collaboration & review foundations — U17 state.py: layout registry + schema codecs (2026-07-10)

`sgt/state.py` centralizes the `.sgt/` layout: one `_Artifact` registry row per file (its path
parts, committed-vs-local, and — critically — its exact `sort_keys`/`newline` byte behavior so the
move preserves each writer's on-disk bytes), plus a `{"schema": N, "data": ...}` envelope. Readers
dispatch on schema forever: v0 (no envelope, pre-U17 blobs) and v1+ both parse, and
`load_blob_json(gb, sha, name)` reads a *teammate's arbitrary-vintage committed* metadata straight
from a fetched commit without checkout. Shipped in two commits, read-side first (`c58b9d4` adds the
registry and dispatch while every writer still emits raw v0; `27673a4` flips writers to the
envelope), so the read path provably accepts old bytes before any new bytes exist. `tests/test_state.py`
covers round-trip, v0/v1 dispatch, and blob-read dispatch.

### Collaboration & review foundations — U18 CLI restructure (2026-07-10)

`sgt/cli.py` became the `sgt/cli/` package: one module per verb family behind an argparse
subcommand tree, replacing the hand-rolled `_strip_opt` parsing. New `sgt git <args>` passthrough
runs git in the repo and, for tree-mutating verbs (`checkout`/`switch`/`reset`/`merge`/`rebase`/…),
prints a one-line note that the command bypasses sgt's own tracking (mine-on-contact absorbs the
result on the next verb, so it is a warning, not a block). The golden byte-parity suite (U16) gated
the move verb-family by verb-family — no `--json` byte drift across the restructure.

### Collaboration & review foundations — U19 sync decomposition + explicit tree (2026-07-10)

`sgt/core/sync.py` became the `sgt/core/sync/` package of pure stages: `fetch` → `ingest` →
`resolve` → `materialize`. The behavior-preserving win is that `ingest` builds the op union
*purely in memory* (parses each op via `_deserialize`, never `store.add_bytes`), so fork detection
happens before anything touches disk and `materialize` is the single writing stage. This let the
plan's D4 delete `git merge --no-commit -X ours` entirely (`merge_ours_no_commit`/`merge_abort`
gone from `gitbind.py`): `complete_merge` now writes `.git/MERGE_HEAD` by hand and lets an ordinary
`git commit` produce a real 2-parent merge whose tree is exactly what sgt explicitly wrote — no
textual 3-way merge ever runs, so a same-symbol divergence can never be silently resolved by git's
merge driver behind sgt's back.

### Collaboration & review foundations — U20 SYNC-1 hardening: divergence-as-state (2026-07-10)

Five hardenings (C3/C4/C5/C6/C7). The load-bearing one is **divergence-as-state (D5/C4)**: a
same-symbol fork no longer aborts the whole sync. The fork-free part of the union lands and the fork
becomes durable committed state in `.sgt/forks.json` (read by `sgt status`/`sgt forks` and a
teammate's next sync). The ideal algebra that makes this sound is `order.fork_free(ideal_ids, ops,
declared)` = the union minus the up-sets of *both* tips of every fork: removing two upward-closed
up-sets from a downward-closed set leaves it downward-closed, and having dropped both claimants of
every forked step it is fork-free — a valid ideal by construction. Also: mine-on-contact mines a
plain-git teammate's foreign commits at sync (C3, `merge_base..theirs` with no checkout); committed
ideal recovery from `.sgt/ideal.json` survives a GitHub squash-merge (C5/R8 — identify, don't
re-mint); a miner-version handshake refuses a cross-version union (C6); and non-forcing `sgt push`
routes a rejection into sync (C7).

### Collaboration & review foundations — U21 metadata semilattices [HARD GATE] (2026-07-10)

The plan's one genuinely destructive unit, gated. Three semilattices so metadata converges
order-independently (LAW-U): **ACI pin unions** with a witness-topological tie-break (contested
pins resolve by git-DAG ancestry of their introducing commit, deterministic hash as the fallback);
**birth-minted feature ids** — `f-<founding-op-id>` content-addressed ids replacing replica-local
sequential `F<n>`, with `sgt migrate feature-ids [--apply]` doing an atomic re-mint (tree ids + pin
references + alias G-Set together, dry-run by default) and an alias table keeping old ids resolvable
forever; and **OR-Set declared edges** (unique-tag add, observed-tag tombstone on retract).

**Confirmed data-loss bug found at the gate (the reason the gate exists).** The plan's D6 as first
written birth-minted on *every* build: `tree.build()` unconditionally re-minted a legacy `F<n>`
continuation to its `f-<op>` form on any ordinary `sgt map`/`sgt sync` — no `sgt migrate` needed —
silently orphaning any `pins.labels`/`pins.assign` reference to the old id. A hand-written repro
confirmed a pinned label vanished on a plain rebuild. The naive fix (never auto-remint on
continuation) breaks `test_law_u_feature_ids_are_replica_independent`, which *requires* an
unreferenced legacy id to converge. The shipped fix (`a553d8f`) is surgical: `build()` computes
`protected = frozenset(pins.assign.values()) | frozenset(pins.labels)` and a legacy id re-mints
only when *not* referenced by a pin — referenced ids migrate solely through the explicit,
transactional `sgt migrate`. **Plan correction:** D6's "birth-minted on every build" is wrong as
stated; the shipped semantics are "birth-minted for unreferenced ids on any build, referenced ids
only through the atomic migration." Two Open Questions resolved as the plan hoped: no witness
backfill (pre-U21 pins tie-break by hash), and alias chains are single-hop on this corpus.

### Collaboration & review foundations — U22.5 fork-free local mining (2026-07-11)

Not a planned unit — surfaced by actually running the plan's own per-unit self-hosting rule
(`sgt fsck` + `sgt state` on this repo), which had never been exercised because the repo's own
`.sgt/` store was empty until now. `sgt state` **crashed** on this repo, and would on any repo with
real history: `lens._sync` built `Ideal.from_ops` on the raw committed∪pending provenance union with
no reduction, so mining this repo cold (7028 ops) hit 464 forked `(symbol, before_version)` pairs
and raised "not a valid ideal". U20 gave the *sync* path `order.fork_free`; the *local mining* write
path (`lens.get`/`_sync`) and even its pure-read siblings (`_committed_ids_by_provenance`,
`ideal_for_ref`) never got the equivalent — and the read siblings additionally lacked *grounding*,
so they too would raise on the 4 ungrounded ops this repo carries.

Root cause is not merge history (the miner walks first-parent only): a symbol **added, deleted, then
re-added** rebirths with `before=None` both times, so both births claim `(symbol, None)` — a fork by
`is_fork_free`'s definition, produced by ordinary linear single-clone history. The 4 ungrounded ops
are `__residue__` symbols whose chain predecessor's provenance fell outside this ref (a
squashed/rebased-away branch); grounding correctly drops them. Fix: one `order.reduce_to_ideal(ids,
ops)` = `fork_free(_grounded(ids))` — ground first (downward-closure), then drop forked up-sets; one
pass suffices because `fork_free` removes only upward-closed sets, which preserves grounding.
Measured `fork_free(grounded(raw))`, `grounded(fork_free(raw))`, and the alternating fixpoint all
converge to the same 5626-op valid ideal, so a single ordered pass is exact. Wired into all three
call sites; `_sync` additionally now reduces *before* persisting `.sgt/local/ideal.json` (it
previously wrote the invalid table to disk and *then* raised, leaving corruption behind). After the
fix `sgt state` exits clean and `sgt fsck` genuinely checks 7035 ops on this repo — the self-hosting
rule is no longer vacuous. Regression:
`tests/core/test_lens.py::test_get_survives_add_delete_readd_fork_in_linear_history`.

### Collaboration & review foundations — U22 structured provenance + published claims (2026-07-11)

D7 (structured provenance) + D8 (verdicts-as-claims). The load-bearing constraint was that no op id
may move: provenance is excluded from `compute_id`, so the codec change is pure on-disk enrichment.

Provenance stays `Op.provenance: tuple[str,...]` (bare witness SHAs — all 10 existing consumers
untouched); a parallel `Op.attribution: tuple[Attribution,...]` (sparse, one `{sha, session?, agent?,
plan?}` per SHA that carries a field) rides alongside, and the two are folded together at serialize
time into the v1 on-disk shape `provenance: [{sha, session?, ...}]`. `_deserialize` dual-reads: a
list of *strings* is v0 (every committed repo today), a list of *dicts* is v1. `compute_id` is
byte-identical to before (AST-verified; the golden corpus's op ids are unchanged, only an additive
`attribution: []` / `sessions: []` appears in `oplog_view`/`blame_view`). `merge_attribution` is the
ACI union (group by SHA; per field take the non-None side, `min` on a genuine conflict) so the
D7-pitfall case — two clones holding a v0 and a v1 file for the *same* op id — unions shapes rather
than comparing bytes, on both `Store.add` and sync's in-memory ingest union. Session attribution is
stamped (not at mine time — `compute_checkpoint` stays pure) by `confirm_match` and the sibling
`stamp_drift`, via a new `Store.attribute(op_id, entries)` that only ever grows the excluded-from-id
provenance shape.

D8 claims: a published verdict is an immutable committed file `.sgt/claims/<ideal_key>.<runner_fp>.json`
(runner_fp = short hash of `{by, host, python, platform}`), so two runners' claims for one ideal are
distinct files and sync's union is a trivial file-level G-Set (`materialize._union_claims` copies any
`.sgt/claims/` blob it lacks, byte-for-byte). The private per-clone verdict cache
(`.sgt/local/oracle.json`) is unchanged; `sgt oracle publish` is the explicit publication step, so a
red experiment never leaks into shared history. Verified with a real two-clone test: A publishes,
pushes; B syncs and reads A's claim with A's runner identity (`tests/core/test_claims.py`).
Implemented by a subagent against a fully-specified brief; independently verified (re-ran the suite
to exit 0, read the whole diff, confirmed `compute_id` untouched and the goldens purely additive).

### Collaboration & review foundations — U23 the shared-store concurrency audit (2026-07-11)

The plan's pre-registered unknown-unknown honeypot: R11's store lock was designed single-process,
and SYNC-2 wants concurrent sessions, so the lock *scope* was declared unexamined. Audited
empirically (two real OS processes, not by assertion — throwaway measurement, three deterministic
runs) rather than by reading the code alone. Result matches the expectation:

- **Distinct ops (different ids/paths):** two processes each appended 200 distinct ops
  concurrently → `fsck` clean, all 400 present, zero corruption. Distinct-path writes never
  collide (each is write-temp-then-`os.replace`, atomic on POSIX), so they'd be collision-free even
  without the lock.
- **Same op id, different provenance each add (the lost-update case):** two processes each did 300
  `store.add(...)` of the *same* content-addressed op, each add carrying a distinct fake witness
  sha → the single op file ended with all **600** provenance shas, none dropped, `fsck` clean. The
  provenance-union read-modify-write inside `add()` (`if path.exists(): read; merge; _write_atomic`)
  is protected by the per-`add()` exclusive `flock` on `.sgt/lock`, so concurrent unions serialize
  and no witness is lost.
- **Lock scope — per-`add()`, NOT per-verb:** `_locked()` is entered *inside* each `add()` and
  released when it returns. Measured directly: a "verb" that does `add()` … `sleep(0.5)` … `add()`
  (holding no lock across the gap) finished at ~0.58s, while a competing single `add()` from another
  process completed at ~0.17s — i.e. *inside* the gap. The flock serializes each individual store
  mutation, never a whole verb.

**Conclusion (drives the `land` design):** the single-writer lock is already correct for op
appends and must **not** be widened. Because it does not serialize whole verbs, it is *not* the
mechanism that makes a shared-branch advance atomic — two concurrent landers could each pass their
fork/oracle gate and both try to move the branch. So `land` needs a **branch-record CAS**, not a
wider store lock: the branch record *is* the git ref `refs/heads/<branch>`, and
`git update-ref <ref> <new> <old>` is an atomic compare-and-swap across processes (it fails if the
ref moved off `<old>`). That ref CAS is the entire concurrency-safety mechanism for `land`; the
store lock keeps doing exactly its existing job (safe op appends) underneath it. The one residual
hazard the ref CAS does *not* cover is two landers sharing one working tree/index while
materializing — handled in `land` by giving each session its own clone/worktree (the test does
this; see `land.py`'s docstring), never by a second lock around the CAS.

**The `land` verb it drove (C9 + C12/LAW-G).** `sgt/core/sync/land.py` reuses the sync pipeline's
`ingest → resolve` verbatim over a *local* source (this session's HEAD vs the branch tip), persists
the reconciled union with the extracted `materialize.persist_reconciled` (sync's own reconciled-tree
construction, behavior unchanged), then departs from sync in *how it commits*: it builds the landing
commit off-ref (`git commit-tree`, a real 2-parent merge when HEAD diverges from the tip) and
advances the branch by CAS (`git update-ref <ref> <new> <old>`), re-looping on a lost race to
re-ingest against the moved tip (the "re-union retry"). LAW-G is enforced *here*, not in `sync` (the
U20 contract note): the CAS runs only after the exact resulting op-set passes the oracle — and with
**no oracle configured `land` also refuses** (a green verdict cannot exist, so the shared tip must
not move). A genuine same-symbol fork blocks the land (with the `sgt merge-op` remedy) rather than
advancing over it. The LAW-G test was *rewritten* (not unflipped) to exercise `land` and passes for
real; concurrency is proven with two landers in separate `git worktree`s of a shared bare repo
(genuine ref-CAS contention, separate indexes) — one CAS winner per contended round, the loser
re-unions and either lands or surfaces a fork, `fsck` clean and no lost provenance across randomized
rounds (`tests/core/test_land.py`, deterministic across repeated runs). One surprise the worktree
model surfaced and fixed: `working_tree_snapshot` assumed `.git` was a directory, which is false in a
linked worktree (there `.git` is a file) — it now resolves the real gitdir via `--absolute-git-dir`.
**Naming:** `sgt land` was already U11's "commit the staged rewrite candidate", and argparse allows
one subparser per name, so the SYNC-2 verb is `sgt land <branch>` (an explicit positional); bare
`sgt land` still routes to U11 unchanged (its golden/behavior untouched), and the
`land(repo, branch=None)` API keeps the default-current-branch form for programmatic use.

### Collaboration & review foundations — U24 the proposal object + GitHub rendering (2026-07-11)

The last foundations unit. A **proposal** is a reviewable base+Δ over the op DAG (C10): a base
frontier (some ref's committed ideal at create time), the Δ op-set this session adds on top, the
feature ids Δ touches, a link to the published oracle claim for base∪Δ, and provenance. It exists
end-to-end — create → status → land → render — and is a *pure projection* to GitHub with **no
`gh`/GitHub API dependency** (that is follow-on porcelain); the renderer takes a template seam so
GitLab/other forges are a later addition, not a redesign (plan Open Question).

- **`sgt/core/propose.py`.** `create` mines-on-contact, computes Δ = current − `ideal_for_ref(base)`,
  and **refuses a base∪Δ that isn't a valid ideal** (`is_valid_ideal` — downward-closed *and*
  fork-free): a branch that forks the base is not a reviewable object. Proposals are content-addressed
  by base+Δ and stored one file per id under `.sgt/proposals/`, mirroring claims (D8) — immutable
  G-Set members, so `materialize._union_proposals` (a sibling of `_union_claims`) makes a teammate's
  proposal arrive on `sgt sync`.
- **Staleness is computed, never stored (`status`).** It re-reads the base's ideal *now* and
  re-unions it with the recorded Δ: base unchanged → `current`; base advanced disjointly → 
  `clean-reunion`; Δ now forks the moved base → `fork` with the per-symbol `sgt merge-op` remedy. A
  pure read (persists nothing). The claim is looked up on the base∪Δ ideal reduced to a valid ideal,
  so a forked union still keys cleanly (and returns empty, correctly, once the base re-keys the
  op-set).
- **`land` reuses U23 verbatim.** It refuses a stale-forked proposal (blocked report), else delegates
  to `sync.land(repo, branch=<base branch>)` — the session's HEAD carries Δ, which the CAS advance
  unions onto the (possibly moved) branch tip and gates oracle-green. Partial acceptance ships: an
  `accept_ids` subset of Δ that must itself be downward-closed on the base (`is_valid_ideal(base ∪
  accept)`); a proper subset is materialized onto HEAD first so `sync.land`'s re-union lands exactly
  base_now∪accept. Approvals are **schema + storage only** (no enforcement this unit).
- **`proposal_view` + `render_github`.** The view (additive, R21) carries the feature delta
  (id/label/op-count), Δ op count, the claim, a provenance summary, and the full `status`.
  `render_github` is a pure function of that view returning `{branch, pr_title, pr_body}`; the
  `pr_body` is plain markdown — a feature-delta table, the oracle claim (status + runner identity), a
  provenance summary, and a staleness/fork banner up top — that a reviewer *without sgt* can act on.

**A self-hosting note that shaped the tests (not a code change).** The op store `.sgt/ops/` is a
*committed, per-branch* directory, so a plain `git checkout` between two divergent branches *swaps*
it — two conflicting reworks of one symbol never coexist in a single working tree's store that way.
A same-symbol **fork only ever arises through a store union** (sync's ingest), which is how forks are
constructed everywhere in the suite. So the fork-based-contributor tests (matrix row 6, and the
create-time validity rejection) union the base's conflicting op in via a real `sgt sync` (or the
mine-into-store half of one) rather than a checkout — matching how a contributor actually learns the
base moved. Rows 6/7/12 + G-Set travel + round-trip are green (`tests/core/test_propose.py`); no op
ids change (proposals never touch ops), and the only golden drift is the new `sgt propose` help
lines.

### Product surface — U25 the closure-scale gate [HARD GATE] (2026-07-11)

The measurement U29 (`sgt select`, branch-as-selection) hangs on: **does selecting a feature node
induce an op-closure a human can read?** For every feature node F in a repo's built tree, compute
`↓ops(F)` = `order.downset_in` unioned over F's op-set (exactly what selecting F would drag into the
working ideal), and report closure size + dragged-feature count. Harness:
`experiments/closure_scale/measure.py`. Pre-registered threshold (D1, fixed *before* measuring):
median closure ≤ 25 ops AND median dragged ≤ 3 features, AND ≥ 80% of selections within *both*
bounds, on both real corpora — else RED.

**BET-C (sgt's own op store, this repo): RED.** 24 feature nodes over a 5861-op ideal.
median closure **34 ops** (> 25), p90 96, **max 991**; median dragged **0** features (≤ 3 ✓, max 2);
**only 46% of selections within bounds** (< 80%). Two of the three sub-conditions fail, so BET-C is
unambiguously red — and the gate requires *both* corpora green, so the gate is RED regardless of
BET-E.

**Root cause (the useful part of the finding).** The failure is *not* feature entanglement — dragged
≈ 0 everywhere, so each feature's closure is almost entirely its *own* ops; features are cleanly
separated. The gate fails on feature *size*, concentrated in **docs-file and `residue__::`
pseudo-symbol features**: the single worst node is a 990-op `docs/brainstorms/…` cluster; 13 of 24
nodes exceed 25 ops and nearly all of those are docs/residue. The real *code-entity* features
(`normalize` 4 ops, `clique_graph` 1, `FakeClient` 1, …) are tiny and pass by a wide margin — but the
clustering produces very few of them: only 1793 of the 5861 ideal ops (~30%) are assigned to any
feature leaf at all, and most that are land in residue/doc mega-features. So the gate is red because
the current clustering (design-doc BET-C, 63.9% MoJoFM) lumps prose and residue into large
pseudo-features nobody would meaningfully "select," not because code selection is unreadable.

**BET-E (the ~5k-commit probe repo): UNMEASURED.** `SGT_PROBE_REPO` is unset in this environment; the
acceptance bar is unfalsifiable without a named, SHA-pinned repo (per U25's own note), so the
onboarding probe (BET-E wall-clock/peak-memory for `init --horizon`, D8's 10-minute budget) and the
second closure corpus both await a supplied probe repo.

**Re-scope attempted (does NOT flip the gate).** Re-measuring over only code-entity feature nodes
(majority of own ops revise a real code entity) gives 5/5 within bounds — but those 5 features hold
just **8 of 1793 assigned ops (0.4%)**, a degenerate sample, because the op representation is
residue-heavy: of 5861 ideal ops, only 287 are code entities (245 entity + 42 nested); the rest are
residue (3429), anchor (1969), whole_file (176). Measuring closure in *files* instead of ops doesn't
rescue it either — median 6 files but a 146-file tail, only 42% of nodes touch ≤5 files. So the RED
is robust across op-count, code-scoping, and file-count lenses; it is not a metric artifact.

**Disposition: gate RED, U29 rerouted to closure-explanation UX (per D1).** The bottleneck is the
residue-heavy representation plus docs/residue clustering, not feature entanglement (dragged ≈ 0 —
selecting a feature almost never pulls in *other* features). Per the pre-registered D1 rule (a red
gate routes to clustering or closure-explanation UX over a bad flagship) and the fixed no-post-hoc
threshold, `sgt select` does **not** ship as silent branch-as-selection; it ships showing the
closure it would materialize (files + op count) for the human to confirm. Improving clustering so
docs/residue don't form large selectable nodes is the deferred path to a future green. Recorded in
this plan's U29 status line.

### Product surface — U26 porcelain completion: the D2 refusal table + D3 daily-loop verbs (2026-07-11)

Gate-independent, run in parallel with the U25/U29 disposition above. Two small pieces, both data
over conditionals (design doc §1):

- **D2, `sgt git` refusal table (`sgt/cli/porcelain.py::REFUSALS`).** U18 shipped `sgt git
  <tree-mutating-sub>` as *advisory* — it printed a hint but still ran git, so the working tree
  could move behind sgt's tracking. This unit turns it into a **refusal**: 12 subcommands
  (`checkout`/`switch`/`restore`/`pull`/`merge`/`reset`/`rebase`/`revert`/`cherry-pick`/`stash`/
  `am`/`apply`) are refused outright, each naming the sgt verb that owns that job (`pull`/`merge`/
  `rebase` all route to `sgt sync` — integration is op-set union + fork-surfacing, not a text
  merge; `reset`→`sgt undo`; `stash`→`sgt save`, since a dirty tree is just ops not yet landed).
  `--force` overrides: the token is consumed, the plain git command runs, and the existing
  out-of-band detector (`gitbind`) re-mines on next contact — the same net that already catches a
  raw `git` call made outside sgt entirely. Anything not in the table (read/inspect verbs, plumbing
  like `remote`/`config`/`tag`) still passes through untouched, unchanged from U18.
- **D3, three daily-loop verbs (`sgt switch`/`sgt save`/`sgt undo`).** Each composed from existing
  lens machinery, no new kernel call: `switch <branch>` is `get` (absorb current reality, R9) +
  `gitbind.checkout_branch` + `get` (mine the newly-current ref); `save [-m]` is the put-path sugar
  (`get` + `lens.put` + `lens.record_ideal`), with "nothing to save" decided by **the ideal**, not
  git's dirty flag (`get(repo).op_ids == current_ideal(repo).op_ids`) — necessary because an
  unconfigured `.sgt/` reads as dirty to `gb.is_clean()` unconditionally (the pre-existing
  `sync_refuses_dirty_tree` golden), which would make every `save` look dirty. `undo` inverts the
  most recent recorded ideal edit.
- **The ideal-edit journal, the one piece of new state.** `lens.record_ideal` previously kept only
  the *latest* ideal per ref — sufficient for persistence, but nothing to invert. It now pushes the
  outgoing ideal onto a local, gitignored per-ref stack (`.sgt/local/ideal_journal.json`, a new
  non-committed artifact in `state.py`) before overwriting, guarded by a `journal: bool = True`
  kwarg so `undo_ideal` itself can pop-and-restore without re-journaling its own write (`journal=
  False`) — otherwise every undo would push a redo-able entry and the stack would never drain.
  `undo_ideal` restores the popped ideal via a fresh witness commit (history is append-only; undo
  is a forward edit, never a rewind) and reports `removed`/`added` op-id sets by set difference
  against the ideal just replaced.
- **Verified:** `tests/test_porcelain.py` — the journal's empty/single/repeated-pop semantics
  directly against `lens.undo_ideal`, and `switch`/`save`/`undo` through `cli.main` on real repos,
  ending in the plan's named scenario (switch → save → undo → a raw `git checkout` still refuses —
  the whole loop is sgt verbs, never a direct git tree mutation). `tests/test_cli_git_passthrough.py`
  (already updated) covers the refusal table + `--force` override end to end.
- **Golden snapshot note.** `save`/`undo` are the only verbs that report their own witness commit's
  sha (every other verb reports only content-addressed op ids) — and unlike the corpus fixtures'
  pinned commit timestamps, that commit is made with the real wall clock, so its sha is not
  reproducible run to run. `tests/golden/test_cli_golden.py::_redact_witness_sha` freezes a
  placeholder instead of the literal value; confirmed stable across two independent runs before
  committing.

### Product surface — U27 the three-tier file boundary (D4) (2026-07-12)

Config-driven tier resolution, plus a latent grounding bug this unit's verification surfaced.

- **Three tiers, config over conditionals.** `sgt/core/tiers.py` resolves each mined path to
  `entity` (tree-sitter grammar available, the existing default), `opaque` (whole-file
  pseudo-symbol -- the built-in fallback for a grammar-less path, or an explicit override), or
  `ignored` (excluded from mining entirely). Two config sources, both read via `.sgtignore`
  (gitignore-style patterns, ignored-tier only) and `.sgt/tiers.json` (explicit
  entity/opaque/ignored pattern lists); conflict priority is `ignored` > `opaque` > `entity`.
  **LAW-0:** `tiers.load_tiers_at(gb, sha)` reads both files from the *mined commit's own tree*,
  never the working tree -- tier assignment stays a pure function of the commit, so two replicas
  with divergent uncommitted tier maps still re-mine identical history to byte-identical ops
  (`test_divergent_working_tier_maps_mine_to_byte_identical_ops`).
- **Two named safety guards (`sgt tiers set`, `sgt/cli/tiers.py`).** Promoting a grammar-less path
  to `entity` before this kernel actually has a grammar for it is a content no-op -- it silently
  degrades back to `opaque`, so earlier commits' ops are byte-identical and the chain just
  continues linking before/after versions across the override boundary
  (`test_entity_override_on_ungrammared_path_is_a_content_noop`). Marking a currently-covered path
  `ignored` refuses outright (data loss shape: it would silently stop tracking live content) and
  names `sgt revert` as the remedy (`test_ignoring_a_live_path_refuses_and_names_revert` --
  reverting *every* content-bearing op touching the path, not just its entity op: a single-function
  file mines an entity op plus head/tail residue ops, all three content-bearing and all three
  independently covering the path).
- **Genuine kernel bug found during verification, fixed in the same unit.** `mine.py`'s
  opaque-tier branch and its pre-existing "unparseable mid-edit degrades to whole-file" branch
  both unconditionally computed a new whole-file touch's `before_version` from the real git blob
  OID whenever a parent commit existed -- correct only when the *old* version of the file was
  itself mined as a whole-file symbol (`symbol == path`) at that parent. When the old version was
  mined per-entity instead (exactly the entity→opaque demotion this unit adds, U27's own test 5),
  no op with `symbol == path` ever produced that OID as an `after_version`, so `order.py`'s
  `_grounded` fixpoint (`before is None OR (sym, before) in produced`) could never match it -- the
  new whole-file op became **permanently ungrounded** and was silently dropped from every
  `lens.get()`-derived ideal, despite still appearing in `mine()`'s raw output. This is not
  U27-specific: it's a **pre-existing latent bug** in the mid-edit-degrade path too (the existing
  `test_unparseable_midedit_degrades_to_whole_file` never caught it because it only inspected
  `mine()`'s raw list, never materialized through `lens.get()`/`code()`). **Fix:** a shared helper,
  `_prior_whole_file_version(gb, old_ref, parent, tier_cfg_parent)`, that returns the parent's real
  blob OID only when a whole-file producer genuinely existed there (parent tier resolves to
  `opaque`, or the old content itself failed to parse -- mirroring the exact existing degrade
  condition), and `None` otherwise (including `ignored`, where no producer ever existed) -- a
  `None` before_version is a fresh chain root, which *is* groundable. Both call sites now go
  through this helper instead of a bare `gb.blob_oid(parent, old_ref) if parent else None`.
  Strengthened `test_unparseable_midedit_degrades_to_whole_file` to assert the degraded op is
  actually live (`op.id in ideal.op_ids`, `code()` materializes it) rather than only checking
  `mine()`'s raw output, so this class of regression can't go uncaught again.
- **Verified:** `tests/core/test_tiers.py` (5 tests: derived-flag collapse on lockfiles, the
  entity-override content-no-op, the ignored-tier revert-then-refuse-then-succeed guard, LAW-0
  divergent-working-tree determinism, and entity→opaque demotion materializing as whole-file
  post-fix) plus the strengthened `test_unparseable_midedit_degrades_to_whole_file`. Full suite
  green; three golden snapshots regenerated for the new `derived_paths` field (state/tiers views)
  and the new `sgt tiers`/`sgt tiers set` help lines -- reviewed diffs, no unrelated drift.

### Product surface — U29 closure-explanation UX, rerouted from branch-as-selection (2026-07-12)

U25's gate came back red (median closure 34 ops, only 46% of feature nodes in bounds), so this
unit ships the disposition it recorded: `sgt select`/`sgt why` explain a selection's closure --
never materialize one.

- **`sgt select <feature>...`** (`sgt/lens/select.py`) resolves each feature ref via
  `lens.verbs.resolve_feature`, unions the op-sets, and computes the induced closure with
  `order.downset_in` -- the same collision-safe, ideal-relative primitive the ideal-edit verbs
  use. Reports direct op count, closure op count, touched files, and every op the closure pulled
  in beyond the direct set, grouped by *that op's own feature* with one representative
  chain/requires path each.
- **Closure follows chain + `requires` edges only, never clustering co-membership** (the design
  doc's true-vs-incidental-coupling rule, S2 point 3). The explanatory path is a BFS scoped to
  the already-computed closure (`_closure_edges` restricts `order.chain_edges`/
  `reference_edges`/declared edges to the closure set up front, so an edge naming an op outside
  it can never appear) -- computed once, not re-derived per group.
- **Hub diagnosis** (`_hub_diagnosis`) tallies, among `requires` edges whose consumer is in the
  selected feature(s) and whose producer is in the closure but attributed elsewhere, which
  referenced symbol accounts for the most distinct foreign producer ops -- only `requires` edges
  count, since a chain edge is the same symbol and can't cross a feature boundary. Reports the
  top symbol and its producer count, with `sgt split`/`sgt identity split` named as the remedy.
- **`sgt why <op> [--for <feature>]`** (same module): with no target feature, the plurality vote
  (`tree.assign_ops_to_leaves`'s own logic, re-run over one op's footprint via
  `tree._leaf_member_index`) that assigned the op its feature, every leaf it touched and how many
  symbols voted for each. With `--for`, the same chain `select` would report for that op's
  group -- refusing by name (`"... is not part of ...'s selection closure"`) rather than
  guessing when the op genuinely isn't in that feature's closure.
- **Test fixture required committing the dependency and the dependent in separate commits.**
  Two def-use-connected entities added in the *same* commit get unioned into one op by mining's
  untangling, producing no cross-op `requires` edge -- confirmed empirically before writing the
  fixture. `tests/lens/test_select.py` hand-crafts the feature tree directly (`tree.save`,
  bypassing the Leiden clusterer -- consistent with U25's own finding that clustering quality,
  not `requires`-closure, is this codebase's actual bottleneck) but still computes `op_leaf` via
  the real `tree.assign_ops_to_leaves`, so membership stays consistent with genuinely mined ops.
- **Verified:** 6 new tests -- a cross-feature `requires` chain reports exactly that chain and
  the hub it names; three independent features never drag each other in; three callers of the
  same foreign symbol still produce one hub op count, not an inflated closure; `why` with and
  without `--for`, including the explicit refusal when the op isn't in the target closure. Full
  suite green (no regressions), golden snapshots unchanged (the golden CLI-surface test doesn't
  enumerate every verb's help line, so the two new verbs didn't require regeneration).

### Product surface — U30 the session layer (D5) (2026-07-12)

A thin named wrapper around a real `git worktree`, not a daemon -- `sgt session start`/`status`/
`land`/`gc`, plus `fsck` surfacing (never reaping) leaked sessions.

- **A session's scratch tree is a real `git worktree`** (`sgt/core/session.py`), checked out on a
  fresh `sgt-session/<name>` branch off the current (or `--base`) branch, living at
  `.sgt/local/sessions/<name>` -- nested inside the main repo's own gitignored `.sgt/local/`, so it
  never shows up in the main tree's `git status`. `sgt/state.py` gets a new `sessions` artifact
  slot (`.sgt/local/sessions.json`, local, gitignored, never travels) recording each session's
  branch, scratch path, target branch, the op-ids present at its base, owning pid, and start time.
- **`land` needed no new union machinery -- it reuses U23's CAS `sgt.core.sync.land` verbatim**,
  called with `repo` = the scratch worktree's own path. Worktrees share one ref store, so the CAS
  in `land()` operates on the *shared* `refs/heads/<branch>` regardless of which worktree issues
  the call -- this was the key unblocking insight; no new op-union code was written for this unit.
  `Session.target_branch` (a branch name, resolved at `start()` from `--base` or the current
  checked-out branch) is tracked separately from `Session.base_ref` (the sha) -- a session always
  lands onto a named branch, never a detached sha, matching `land`'s nature as a branch-record CAS.
- **Provenance is stamped in the *main* repo's store, not the scratch tree's.** `session.land()`
  computes `new_op_ids` before calling `sync.land`, then -- once the landing commit is mined into
  the main repo (`lens.get(repo)`) -- calls `Store(repo).attribute(op_id, (Attribution(sha=sha,
  session=name) for sha in op.provenance))` per new op, mirroring `sgt.loop.match._stamp_session`'s
  existing convention (a different, unrelated notion of "session"). This must run against the main
  repo's `Store`: `.sgt/ops/<id>` files are separate on-disk copies per worktree, so attribution
  written into the scratch tree's copy would never be visible to anyone reading the main repo.
- **A successful `land` is the session's terminal step**, unlike `gc` (crash-only reaping): it
  removes the scratch worktree and drops the session record, since the session's job is done. A
  *refused* land (fork/red oracle/contention) leaves both in place so the agent can fix and retry.
- **Owning-pid liveness, not age, distinguishes a crash from a long-running session (D5's
  pitfall).** `start()` records `os.getppid()` (the invoking agent/shell -- sgt's own CLI pid is
  transient and meaningless by the time anyone checks); `gc` reaps a session once `os.kill(pid, 0)`
  raises `ProcessLookupError` (POSIX, stdlib, no new dependency), or every session with `--force`.
  `sgt fsck` now also reports (never reaps) any session whose owning pid has died -- a
  `stale_sessions` JSON field / a printed line per leaked session, advisory only (doesn't flip
  `ok`, since a leaked worktree isn't store corruption).
- **The early-fork warning is a report, never a lock (S6).** `overlaps(repo)` diffs each live
  session's new ops' footprints pairwise and names any pair that shares a symbol; `sgt session
  status --watch` polls this every couple of seconds until interrupted -- the only thing that ever
  loops, and only for as long as it's asked to. No daemon anywhere in this unit.
- **Test-fixture bug found and fixed during verification.** A helper calling `gb.commit_all(...)`
  then `lens.get(repo)` produces a base commit whose tree never includes `.sgt/local/.gitignore` or
  `.sgt/ops/*`, since `get()`/`_sync()` is what writes those files, *after* the commit that would
  need to carry them. A `git worktree` checked out at that base sha inherits the same gap and is
  therefore permanently dirty per `git status` -- which trips `sync.land()`'s clean-tree
  precondition. Fixed (matching `tests/core/test_land.py`'s existing `_seed_shared`/`_stage_local_op`
  pattern) by following every `get()` with `lens.put(repo, ideal, message=...)` +
  `lens.record_ideal(repo, ideal, put_sha)`, which materializes and commits a witness so the tree is
  genuinely clean and `.sgt/ops/*` is tracked.
- **A single `def bar(): ...` in a new file mines to 4 ops, not 1** (anchor + residue + HEAD-residue
  + the symbol itself) -- the land-provenance test's initial `ops_added == 1` assumption was wrong
  about the kernel's actual mining granularity, not about session semantics; fixed to compare
  against `len(session_mod.new_op_ids(session))` computed before landing, rather than a hardcoded
  count.
- **Verified:** `tests/core/test_session.py`, 9 tests -- a session materializes a real worktree on
  its own branch and refuses a name collision; two sessions editing the same symbol are reported
  overlapping (and independent edits are not); a landed session's ops carry
  `Attribution(session=...)` in the *main* repo's store and the scratch worktree + record are both
  gone; a red-oracle land leaves the session fully intact; `gc` reaps only a dead-pid session (or
  every session with `--force`) and `fsck` stays clean after; `stale_sessions` reports without
  reaping. Full suite green (no regressions); golden CLI snapshot regenerated twice -- once for the
  four new `sgt session ...` help lines, once for `fsck --json`'s new `stale_sessions: []` field --
  both diffs reviewed, no unrelated drift.

### Product surface — U31 provenance and trust surfaces (2026-07-12)

Renders what U22 structured and U30 populates: `sgt.api.trust_view` (the trust queue) and
`map_view`'s additive `sessions` rollup, plus the two verbs the plan scoped -- `sgt revert
--session` (addressing by provenance) and `sgt review-queue ack` (the one new mutation, a
committed G-Set review record).

- **A review record is a fourth content-addressed G-Set artifact**, following D8/claims and
  C10/proposals exactly: `sgt/core/review.py`'s `ReviewRecord` is keyed by the sorted op-id set
  alone (`scope`/`note` ride along but aren't part of identity), so re-acking the same op-set is a
  no-op on content and `sgt sync`'s union is the same trivial file-presence check as the other two
  (`materialize._union_reviews`, added next to `_union_claims`/`_union_proposals`). No field-level
  merge, no conflict possible -- confirmed by a two-clone sync test round-tripping a committed
  review file byte-for-byte.
- **Addressing by provenance resolves a session *name* to an op-set via structured attribution,
  never the session record.** `session.ops_by_session(repo, name)` scans `Store(repo).all_ops()`
  for `Attribution(session=name)` (D7, U22) -- this is deliberately independent of
  `plan._load_sessions`/`land`'s bookkeeping, which is dropped the moment a session lands. The
  effect: `sgt revert --session s1` still resolves correctly arbitrarily long after `s1` landed
  and its record is gone, exactly as the plan's provenance framing requires. `verbs.
  plan_revert_session` then reuses `plan_revert_feature`'s grouped `I \ (∪ upset_in(x))` closure
  verbatim -- no new ideal-algebra code, just a different way to name the op-set going in.
- **`trust_view` is a pure grouping read, not a new source of truth.** It walks every mined op
  once, skips anything already covered by `review.reviewed_op_ids` (a review, once acked, is gone
  from the queue for good -- there is no "unack"), and buckets the rest by provenance key: each
  distinct `session`/`agent` string an op's `Attribution` list carries, or the literal string
  `"drift"` for an op with no attribution at all but flagged by `compute_checkpoint(repo).
  drift_op_ids` (U14). An op with *both* real attribution and drift status appears once, under its
  attribution key, not under `"drift"` -- attribution is the more specific claim about who's
  responsible, and the plan's own worked example groups by session/agent first.
- **`map_view`'s `sessions` field is an additive rollup with the same recursion shape as the
  pre-existing `op_count`**: a leaf feature node's sessions are exactly the set of `session`
  strings among its ops' attribution; an interior node's is the union of its children's. This
  keeps the tree's existing children-recursion invariant (every rollup is a fold over `children`,
  never a second pass over the flat op list) and required no change to how the tree itself is
  built -- only a new field on the already-emitted node dicts.
- **No new mutation semantics, exactly per the plan's boundary.** `review-queue ack` is the *only*
  new way to change state; acting on a trust-queue group (accepting, rejecting, retagging) is
  entirely the pre-existing verb surface (`revert --session`, `feature move`). `trust_view` never
  writes anything.
- **Rail/TUI panel work deferred, following U29's precedent.** Per D6 (not yet implemented in
  code), the live surface for the trust queue is scoped to core + `sgt.api` + CLI this unit --
  `sgt review-queue list`/`ack` and `sgt revert --session` are the full deliverable; a dedicated
  panel rendering `trust_view` interactively is left for whichever unit actually builds the rail.
- **Verified:** `tests/core/test_review.py` (4 tests) -- ack/load round-trip and re-ack idempotence,
  an empty op-set refusal, cross-record union, and G-Set travel across a sync. Three new
  `plan_revert_session` tests in `tests/core/test_verbs.py` (removes exactly a landed session's
  op-set; refuses an unknown session name; reports "no change" once already reverted) -- 12/12 in
  that file. Six new `tests/test_api.py` tests for `map_view`'s `sessions` field and `trust_view`
  (empty queue, a landed session's ops grouped under its name, dequeuing on ack, an unattributed
  drift op grouped under `"drift"`). Full suite green (`uv run pytest -q`, live-LLM test
  deselected as usual); golden snapshots regenerated once, diff reviewed as purely additive -- a
  new `"sessions": []` field on every feature-tree node, plus the two new `sgt revert --session`/
  `sgt review-queue` help lines in the captured `sgt help` output.

### Product surface — U32 the review surface + GitHub publish (2026-07-12)

Closes S8: `sgt.api.proposal_review_view` (the partial-accept checklist), `sgt propose land
--subset` (the CLI half of the down-closed-subset flow D6 assigns to a future rail), and `sgt
propose publish` (the `gh`-CLI porcelain D7 specifies).

- **`proposal_review_view` computes the checklist's `requires` edges by reusing U29's closure
  primitive, not new logic.** Each delta feature's `requires` is `order.downset_in(op, base∪Δ,
  ops, declared)` unioned over that feature's own Δ ops, restricted to *other* delta features'
  ids and to chain/reference/declared edges -- the same trace `sgt why`/`sgt select` walk, just
  scoped to a proposal's op-set instead of the current ideal. `op_ids` rides along per feature so
  a caller (the CLI, or a future rail) can build an `accept_ids` set without recomputing
  attribution itself.
- **`--subset` takes feature ids or labels**, resolved against the checklist: an unknown ref
  refuses by name; a chosen feature that omits a feature it `requires` refuses naming the missing
  one *by label* ("'Baz' requires Qux -- include it in --subset too") -- the CLI does this
  resolution itself (`propose.land`'s own guard only checks `accept_ids <= Δ` and downward-closure,
  not requires-completeness), matching the plan's "the rail computes nothing itself" framing by
  keeping the closure computation in `proposal_review_view`, not duplicated in the CLI.
- **A real, if narrow, kernel finding surfaced while testing partial acceptance: two ops from
  adjacent insertions in the *same file* are not an independently-landable slice.** Adding two
  functions back-to-back mints a shared `__residue__` op for the whitespace gap between them; a
  feature-checklist entry built from *only* a function's own bare symbol op (no
  anchor/residue) is `is_valid_ideal`-passing but not self-consistent -- materializing it alone
  produced a corrupt file (one function's body spliced directly against another's with no
  separator). This is not a `propose.land` bug: `land`'s own landing-commit trailers and the
  materialized tree were exactly right for whatever op-set was actually accepted; the trap is
  constructing an `accept_ids` set that looks like "one feature" but is missing a residue op a
  positionally-adjacent feature also touches. Two features in two disjoint files have no such
  coupling and partial-accept exactly as the plan's acceptance wording requires. Filed as a v1
  limitation below rather than fixed -- `proposal_review_view`'s `op_ids`/`requires` fields
  already give a caller everything needed to avoid it (a feature's full closure, computed the same
  way `sgt why` would), and the plan's test scenario doesn't require intra-file adjacent-feature
  acceptance to work.
- **A second, unrelated trap while verifying the fix: `lens.ideal_for_ref` is not the right tool
  to check what a `land` actually landed onto a *different* branch than the checked-out one.**
  Its docstring already says so (a coarse provenance-scan over a ref's whole ancestry, never
  consulting the persisted ideal table) but it is easy to reach for anyway. When `land` targets a
  branch other than the checked-out one, the landing commit is a real 2-parent merge and that
  merge's ancestry still reaches back through the pre-split commit that carried the *full* Δ's
  trailers -- so a provenance-scan over-reports even though the landing commit's own trailers (and
  the materialized tree) are exactly the accepted subset. The fix was in the test (read the landing
  commit's own trailers / file content), not in `land`.
- **`publish` resolves create-vs-update by branch, not by proposal id.** `gh pr list --head
  <branch> --state open` before every publish; an existing open PR gets `gh pr edit` (title/body
  refreshed from the current `render_github`), otherwise `gh pr create` -- so re-publishing after
  a claim lands or the base moves updates the same PR rather than opening a second one, per D7. No
  network call happens without `shutil.which("gh")` first; its absence refuses cleanly with the
  install URL rather than a raw `FileNotFoundError`.
- **Rail work deferred, following U29/U31's precedent (D6).** The checked-list UI, greyed-out
  disabled-uncheck-with-requires-tooltip, and staleness banner are rail concerns explicitly out of
  scope this unit; `proposal_review_view` is the complete API surface a rail would call, and `sgt
  propose land --subset`/`sgt propose publish` are the complete CLI surface a scripted or
  agent-driven caller has today.
- **Verified:** `tests/core/test_propose_review.py` (8 tests) -- the checklist's disjoint
  `op_ids`/empty `requires` for two unrelated features, the unknown-proposal-id error path, partial
  land exactly landing the chosen feature's ops (checked via the landing commit's own trailers and
  file presence, not `ideal_for_ref` -- see above), `propose.land`'s own subset-validity guard, the
  CLI's requires-refusal naming the missing feature by label, `gh`-absent refusal, the
  create-vs-edit branch (subprocess faked, no network), and a `gh`-present integration guard
  (skipped when `gh` is absent) that pushes to a real local `origin` and confirms `gh pr create`
  fails cleanly off a non-GitHub remote. Full suite green (`uv run pytest -q`, live-LLM test
  deselected as usual); golden snapshots regenerated -- diff is purely additive (`sgt propose
  land`'s summary line gains `[--subset ...]`, a new `sgt propose publish` help line).

### Kernel invariant & sync fixes — U1-U4 (2026-07-13)

Executing `docs/plans/2026-07-12-001-fix-kernel-invariants-and-sync-plan.md` (the multi-agent
review's four failure classes). Phases A-C change no op identity and land individually.

- **U1 safe materialization (R3/R4).** `_write_working_tree` no longer writes or deletes through a
  symlink (leaf or ancestor), and no longer deletes a tracked path whose live bytes no *valid*
  ideal can regenerate — the add/delete/re-add rebirth fork drops such a path from `code(I)` though
  its content is genuinely live, and a silent delete there was unrecoverable. "Reproducible" is
  defined as `code(reduce_to_ideal(all_ops), all_ops)` (the maximal valid ideal): a rebirth file
  is kept (backstop), a legitimate revert stays reproducible and is deleted. Skips surface as
  `unmanaged`/`backstop_kept` rather than being lost silently.
- **U2 fsck completion + `sgt fsck --tree` (R11).** fsck now also checks ideal-table validity,
  witness reachability (peeled `^{commit}`, so a well-formed but absent SHA is caught), and a
  mixed-miner-version backstop; chain-gaps stay advisory (real single-clone histories carry benign
  off-ref-predecessor gaps, so they never flip `ok`). `--tree` classifies every HEAD-vs-`code(I)`
  divergence into drift/unmanaged/backstop_kept/staged/unseeded. On this repo `--tree` reports 156
  drift — correct visibility of the documented ~20% U22.5 closure loss, not a new regression; a
  freshly `put` kernel repo shows zero drift.
- **U3 atomic writes + paired locked RMW (R5/R6).** Every `.sgt` registry write goes through an
  atomic temp+fsync+rename; sync's union byte-copies too (a torn copy would otherwise be skipped
  forever by their `exists()` guard). Pairs that must move together (ideal-table+witness,
  journal-push+table, forks+ops, staged-bytes+record) write under one flock; ops are always added
  *before* the section (flock is non-reentrant — nesting self-deadlocks). Torn *local* artifacts
  reseed; torn *committed* artifacts fail loudly to fsck.
- **U4 clean-tree guard (R16).** `_sync` now runs the dirty mining pass only when
  `GitBinding.has_dirty_source()` is true — some path outside `.sgt/` has an uncommitted
  working-tree change (modified/deleted/staged/untracked source). `.sgt/` is excluded because every
  `get()`/`put()` leaves untracked `.sgt/ops/*` churn that would otherwise keep the guard
  permanently dirty; untracked *source* files still read dirty and are still mined (the `save`
  golden depends on it). Goldens byte-identical; behavior proven by a `working_tree_snapshot`
  call-count fake.

  **Wall-clock, honestly:** the guard does what it claims — the dirty pass costs **~1.8s** on this
  repo (`mine(include_dirty=True)` 1.85s vs `False` 0.03s at an up-to-date witness) and U4 removes
  it on a source-clean tree. But it does **not** measurably move `sgt status` here (~71s both
  before and after), because `sgt status` is dominated by a *different* cost: `order.reduce_to_ideal`
  over the full 7840-op store takes **~28s per call**, and `status_view` invokes it more than once
  (once inside `get()`/`_sync`, once via U1's `materialization_skips` → `_reproducible_content`).
  Flagged, not fixed under U4 (out of scope): the reduce-to-ideal cost is the real `sgt status`
  bottleneck, and U1's per-status full fold for the maximal ideal roughly doubles it — worth a
  dedicated caching/incremental-reduction pass (or memoizing the maximal ideal across the two
  call sites) before claiming `sgt status` is fast on a large store.

## Known v1 limitations (kernel, deferred -- see the plan's Scope Boundaries)

- **Local mining reduces a forked/ungrounded history silently, and it can be lossy** (U22.5). On
  this repo the reduction to a valid ideal drops ~20% of ops (1399 of 7025: 1265 real-symbol, 86
  residue, 48 anchor) — chiefly whole files that were added/deleted/re-added, which `fork_free`
  drops entirely (both births' up-sets). Unlike a *sync* fork, these are not recorded in
  `.sgt/forks.json` (they are historical, already resolved in git, and not actionable by `sgt
  merge-op`), matching the pre-existing silent reduction in the read-side siblings. Consequence to
  keep in view: `put` materializes `code(I)`, so an add/delete/re-add file is absent from a
  materialized tree. This 20% closure figure is exactly the kind of signal U25's measurement gate
  exists to quantify; the deeper fix (re-add chaining `before=<deleted version>` instead of `None`,
  which would remove the pseudo-fork) touches op identity and is deferred, not attempted here.
- **Residue-segment chain identity does not survive a rename of its anchor entity** (documented
  above) -- a v1 boundary, not exercised by the corpus as a correctness bug (byte content is
  still exact; only chain continuity across that one rename is lost).
- **Import lifecycle is not yet a verb.** Imports are ordinary residue bytes; no verb yet warns
  "this revert leaves an unused import" or offers to prune one, though the reference edges and
  `verb_preview_view`'s before/after diff already carry what such a verb would need.
- **A feature's "own" ops are not always an independently-landable slice when it sits directly
  adjacent to another feature in the same file** (U32). Two functions added back-to-back share a
  `__residue__` op for the whitespace between them; accepting only one function's bare symbol op
  via `propose.land(accept_ids=...)` passes `is_valid_ideal` but materializes corrupt content (no
  separator between the accepted function and whatever followed it in the original edit). Two
  features in disjoint files have no such coupling. `proposal_review_view`'s per-feature closure
  (`requires`) does not currently surface this positional/residue coupling as a `requires` edge
  (it only traces chain/reference/declared edges, and a shared residue op is attributed to
  exactly one feature, not flagged as shared) -- a caller that builds `accept_ids` from anything
  narrower than a feature's full owned-op set for adjacent same-file features can reproduce this.
  Deferred rather than fixed: the plan's own test scenario is satisfied by disjoint features, and
  a real fix (residue ops declaring a `requires` on whichever feature is positionally adjacent, or
  rejecting a checklist split that would cut through a shared residue) touches feature-tree
  construction, not this unit's scope.
- **`revert --keep-dependents` is one-hop only** (U11): only direct reference-edge dependents of
  the target get a continuation hollow; anything further downstream drops like a plain revert.
- **Two languages** (Python, TypeScript/TSX) via the tree-sitter grammars wired into
  `sgt/entities/extract.py`'s `_DEFS`/`_EXT_LANG`; anything else materializes as faithful
  whole-file residue (R7), never mis-decomposed, just not independently addressable.
- **Reference-edge resolution for non-UTF-8 files is incomplete.** `mine.py`'s `calls_by_src`/
  `entity_version`/`container_of` come from `build_entity_graph(gb.tree_at(sha))`, and `tree_at`
  drops any file `file_at` can't UTF-8-decode -- such a file's own entities/residue still mine and
  materialize correctly (via the separate `blob_bytes`-based per-file loop), but it won't
  participate in cross-file `requires`/reference-edge resolution. Not fixed this pass (would
  require rebuilding `tree_at`'s whole read path on bytes); named here so it's a decision, not an
  oversight.
- **TS grammar constructs outside `_DEFS`** (enums, `type` aliases, namespaces, ambient
  declarations) are not extracted as entities -- they materialize as exact residue bytes (never
  corrupted, per the byte-partition guarantee), just not independently revertable.

## Reproduce

```bash
uv venv --python 3.12 && uv pip install -e ".[dev]"
uv run pytest -q                              # deterministic suite
uv run python scripts/e2e_plan_checkpoint.py  # live graph-only walkthrough (uses .env for `plan`)
```
