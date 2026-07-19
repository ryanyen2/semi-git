---
title: Chunked Incremental Sync - Plan
type: fix
date: 2026-07-18
planned: 2026-07-18
topic: chunked-incremental-sync
artifact_contract: ce-unified-plan/v1
artifact_readiness: implementation-ready
product_contract_source: ce-brainstorm
execution: code
---

# Chunked Incremental Sync - Plan

## Goal Capsule

- **Objective:** Redesign sgt's mine-on-contact sync so a bounded-timeout client (VS Code extension, TUI) can safely poll a ref of unknown sync state — never-before-tracked or merely far behind — and always end up strictly closer to fully caught up, never back at square one, while preserving the existing witness+ideal-table atomicity invariant.
- **Authority hierarchy:** The R5/U3 invariants in `docs/plans/2026-07-12-001-fix-kernel-invariants-and-sync-plan.md` (witness and ideal table must always move together; critical sections never nest) are fixed constraints this design satisfies, not tradeoffs it renegotiates.
- **Stop conditions:** Stop and flag if any proposed checkpoint mechanism can leave a witness persisted without an ideal table consistent with it at that same checkpoint, even transiently — that violates R5 and is a hard line, not a design choice.
- **Execution profile:** Standard code change concentrated in `sgt/core/lens.py` (`_sync`/`get`), `sgt/core/mine.py` and `sgt/store/gitbind.py` (chunk-bounded history ranges), `sgt/core/store.py` (`fsck`/`FsckReport.chain_gaps`), `sgt/api.py` (`compose_view`/`map_view` partial-state signal), and the VS Code extension + TUI (rendering that signal). No new dependencies.
- **Tail ownership:** Whoever implements this also updates `fsck`'s chain-gap semantics and the R5 invariant's doc comment in `lens.py` to describe per-chunk checkpointing instead of "only after the full mine completes."

## Product Contract

### Summary

sgt's mine-on-contact sync makes monotonic, checkpointed progress in bounded chunks — forward toward HEAD and backward toward genesis — so a client with a fixed timeout always advances a ref's sync state on every call, never discards a whole attempt's work. Clients get an explicit signal when a ref's mined history is still partial.

### Problem Frame

`sgt/core/lens.py`'s mine-on-contact (`get()` → `_sync()`) mines a ref's entire history from genesis whenever that ref has no prior witness in `.sgt/local/witness.json` — `since=None` makes `GitBinding.history()` (`sgt/store/gitbind.py:327-346`) build the git-log range as just `target` (full HEAD history), with no lower bound. On this repo, a first sync of `main` (154 commits into an ~11k-op store) took ~11 minutes wall-clock, empirically measured.

The VS Code extension calls every `sgt` subprocess, including the workbench's `compose` poll, through a hardcoded 30-second `execFile` timeout (`editor/vscode/src/sgt.ts:68-87`). It kills the process well before an 11-minute mine finishes, with no output yet flushed, surfacing as a bare `Command failed: ...sgt compose --json --full`.

`_sync` persists the witness/ideal-table pair only after the *entire* mine completes (`sgt/core/lens.py:325-332`), atomically, by deliberate design (R5): a crash that moved the witness without the table would make the next `get()` trust a stale ideal against a witness that already moved. The consequence for a bounded client is that a 30-second kill makes zero forward progress — the identical ~11-minute genesis mine restarts from scratch on every subsequent poll, forever, until someone manually runs an unbounded `sgt` command from a terminal. This is the "FEATURES panel keeps spinning, never resolves" symptom reported in the originating bug. It is not unique to a never-synced ref: any ref whose witness has fallen far behind HEAD (a long-idle branch, a large rebase) hits the identical shape of problem.

### Key Decisions

- **No background daemon.** sgt stays a stateless, invoke-per-command CLI; the fix works entirely within a single `sgt <verb>` call's lifetime, made resumable rather than made faster via a long-lived process. Ruled out explicitly to keep sgt's invoke-per-command architecture intact rather than trading it for an always-warm background miner.
- **Bounded-but-self-healing coverage, not a permanent cap.** First contact with a never-tracked (or very-stale) ref mines a bounded window first; older/newer history not yet covered is filled in by later calls until the ref reaches full genesis-to-HEAD coverage on its own. Rejected a permanently-capped horizon (deep history would then require an explicit user-triggered catch-up) in favor of incompleteness that is always transient and shrinks unattended.
- **Checkpoint after every chunk, not only at mine completion.** The existing R5 atomic witness+ideal-table pairing now fires once per bounded chunk instead of once per call. The pairing rule itself is unchanged — only how often the pair gets written moves. A client-side kill between chunks leaves durable, consistent partial progress instead of discarding the whole attempt.
- **Partial state is explicit on the read surface.** Ref-scoped read views carry a signal for whether the ref's mined history is fully caught up, so clients render a lightweight "still indexing" affordance instead of silently presenting a partial map as final.
- **One mechanism, two directions.** Forward catch-up (behind HEAD) and backward backfill (behind genesis) are the same bounded-chunk-plus-checkpoint primitive applied in opposite directions, not two separate features to build and maintain independently.

### Requirements

**Chunked, checkpointed mining**

- R1. A ref's mine-on-contact processes its outstanding history — toward HEAD, toward genesis, or both — in bounded chunks, each small enough that a single chunk reliably fits inside a client's timeout budget.
- R2. After each chunk, the witness and ideal-table pair for that ref persist atomically together, exactly as today's R5 invariant requires — only the checkpoint frequency moves to per-chunk; the pairing rule is unchanged.
- R3. A client-side kill between chunks leaves the ref's sync state at the last completed chunk boundary; the next sync call resumes from there rather than re-mining anything already checkpointed.

**Self-healing coverage**

- R4. First contact with a ref that has no witness, or one far behind HEAD/genesis, starts with a small bounded recent window rather than mining full history in one unbounded pass.
- R5. Every subsequent sync call on a ref not yet fully caught up performs additional bounded chunks — toward HEAD if behind, toward genesis if mined history doesn't yet reach the repo's root — until the ref's mined range covers genesis-to-HEAD.
- R6. A ref that reaches full coverage stays fully covered; ordinary incremental syncs (new commits since the last witness) continue to work exactly as today once a ref is caught up.

**Partial-state visibility**

- R7. Any read view scoped to a ref (at minimum `compose_view`/`map_view`) reports whether that ref's mined history is fully caught up to genesis-to-HEAD, and if not, how far back mining currently reaches.
- R8. The VS Code workbench and the TUI surface a lightweight "still indexing history" indicator when a ref's sync status is incomplete, instead of presenting a partial feature map as final.

**Diagnostic integrity**

- R9. `fsck`'s chain-gap reporting distinguishes a gap caused by not-yet-backfilled history (expected, self-resolving) from a gap caused by rebased/squashed-away history (today's existing advisory meaning) — the two must not be conflated in one undifferentiated report.

### Acceptance Examples

- AE1. **Covers R1, R3, R4.** A repo has never synced `main` (no witness); `main` is 500 commits ahead of an empty store. The first `sgt compose --json` call completes within the client's timeout, mining only a bounded recent window, and returns a valid (partial) compose view. Killing the process before that first chunk finishes means the next call resumes from the last completed chunk, not from scratch.
- AE2. **Covers R5, R6.** After N successive `sgt compose` calls on the ref from AE1, each contributing one more bounded backfill chunk, the ref's witness eventually reaches the genesis commit and its sync-status signal reports complete; subsequent calls behave exactly as today's incremental sync (only new commits since the last witness).
- AE3. **Covers R7, R8.** While a ref is mid-backfill, `compose --json --full`'s output includes a field indicating incompleteness and how far back mining currently reaches; the VS Code workbench renders a small "Indexing history…" badge near the FEATURES tree for as long as that field reports incomplete, and the badge disappears once it reports complete.
- AE4. **Covers R9.** `sgt fsck` on a ref that is mid-backfill (a real not-yet-mined portion of history behind its current frontier) reports that portion distinctly from a chain gap caused by a rebase that dropped commits from the ref's current history — a reader can tell which case they're looking at.

### Scope Boundaries

- The unmerged `fix/vscode-sgt-concurrent-timeout` branch (concurrent activation-time lock contention among ~5 extension reads) is a separate, already-diagnosed bug. This plan doesn't fold it in, though nothing here conflicts with merging it independently.
- The exact chunk-sizing policy (commit count vs. wall-clock time-box vs. op-count budget) is left to planning — this plan only requires that chunks reliably fit a bounded client timeout, not a specific sizing algorithm.
- Backfill cadence policy (whether every read call contributes one backfill chunk, or only some verbs do) is left to planning.

### Outstanding Questions

**Resolve Before Planning:**
- None — the brainstorm dialogue resolved the three load-bearing product questions (daemon-or-not, permanently-bounded-or-self-healing, silent-or-explicit partial state).

**Deferred to Planning:**
- How `FsckReport`/`chain_gaps` should represent "not-yet-backfilled" distinctly from "rebased-away" (new field, new value, or separate list) — R9 fixes the requirement, not the data shape.
- How chunk size is chosen or tuned (fixed commit count, adaptive time-box, op-count budget) and whether it needs to be configurable.
- Whether the partial-state signal belongs on every ref-scoped view or only `compose_view`/`map_view` initially.
- Whether the TUI needs its own indicator affordance or can reuse the same JSON field with simpler rendering.

### Sources / Research

- `sgt/core/lens.py:259-345` — `_sync`/`get`, the all-or-nothing witness+ideal-table persistence this plan changes to checkpoint per chunk.
- `sgt/store/gitbind.py:327-346` — `GitBinding.history`, confirms `since=None` mines full HEAD history with no lower bound.
- `sgt/core/mine.py:627-673` — `mine()`, the entry point that would need chunk-boundary parameters.
- `sgt/core/store.py:196-218,331-395` — `Store.add`/`fsck`, the per-op write path and the `FsckReport.chain_gaps` field R9 extends.
- `editor/vscode/src/sgt.ts:68-87,167-169` — `Sgt.run`/`compose()`, the 30-second timeout and the exact "Command failed: ..." error text this bug produces.
- `docs/plans/2026-07-12-001-fix-kernel-invariants-and-sync-plan.md` — source of the R5/U3 invariants this design satisfies, not relaxes.
- Empirically measured: first-contact mine of 154 commits into an ~11k-op store took ~11 minutes wall-clock; a subsequent incremental sync (witness present) took ~11.7 seconds.
- `sgt/core/mine.py:627-673` (`mine()`) — its per-call `_UnionFind` resolves renames only across the commits in that one call's range (module docstring: "Identity: rename/move resolution runs per `mine()` call via a union-find spanning the commits in that call's range -- not persisted across separate calls"). This is the load-bearing fact behind Planning Contract Decision KTD-1: chunking via repeated bounded `mine()` calls introduces no new identity risk, because every incremental `get()` call today already has exactly this per-call scoping.
- `sgt/core/mine.py:255-289` (`_apply_rebirth_chaining`) — walks a path's git history independently of the mined range to re-chain a fresh add onto its true prior deletion. Confirms cross-chunk identity for the add-delete-re-add case is already handled outside `mine()`'s per-call union-find, reinforcing KTD-1.
- `sgt/core/lens.py:348-363` (`init(horizon=...)`) — the existing `treat_as_root` genesis-horizon mechanism mints a permanent synthetic `add` at the horizon commit. Confirmed unsuitable for R4's bounded first-contact window (Decision KTD-2): a synthetic add can never be "un-sealed" by a later backfill without violating the store's append-only, content-addressed identity law — a real dangling `before_version` reference (an ordinary, already-tolerated `_chain_gaps` advisory) is the correct boundary shape instead.
- `sgt/core/store.py:315-328` (`_chain_gaps`) — the existing ref-agnostic advisory gap check this plan's R9 extends with a `pending_chain_gaps` classification (Decision KTD-5).
- `sgt/state.py:206-247` (`load_json`/`save_json`) — the existing per-name JSON persistence helpers `_load_witnesses`/`_load_ideal_table` already use; the new backfill-frontier table follows the identical pattern.
- `sgt/api.py:1139-1174` (`compose_view`), `:1302` (`status_view`) — `compose_view` already nests `status_view`'s output as its `status` key, so extending `status_view` alone satisfies R7's "at minimum `compose_view`" without duplicating logic (Decision KTD-6).
- `sgt/tui/app.py:194-241` (`action_refresh`/`_render_status`) — the TUI's existing status-line render already consumes `status_view`'s dict directly (in-process, not via subprocess), confirming R8's TUI affordance is a same-field, simpler-rendering reuse (Decision KTD-7), not a new mechanism.
- `editor/vscode/src/statusBar.ts` (`GitStatusBar.refresh`) — already polls `store.status()` and renders a status-bar chip; the natural, minimal-diff home for R8's "still indexing" badge on the VS Code side.

## Planning Contract

### Approach

The existing all-or-nothing `_sync()` becomes a per-call, single-chunk state machine: each `sgt <verb>` invocation mines **one** bounded chunk (forward toward HEAD, or backward toward genesis) and checkpoints atomically before returning. No verb's call graph changes — `get()` still calls `_sync()` once — only `_sync()`'s internal loop and persisted state grow. `mine()` gains an optional wall-clock `deadline`; everything else (`_mine_one`, `_build_ops`, the per-call `_UnionFind`) is untouched, so its existing identity guarantees carry over unchanged (KTD-1).

First contact (no witness) and backward genesis-backfill are unified into one direction of the same primitive (KTD-2): on first contact, the witness is set to HEAD immediately (ordinary forward incremental sync works from the very next call), while a bounded recent window is mined backward from HEAD, one chunk per call, until the ref's mined range reaches its root commit. Forward catch-up (a stale-but-previously-synced ref) is the mirror direction: `since=<witness>`, chunked toward HEAD by the same deadline mechanism.

### Key Technical Decisions

- **KTD-1 (identity safety of chunking).** Splitting one logical mine into several `mine()` calls over disjoint sub-ranges is architecturally identical to what already happens between any two ordinary incremental `get()` calls today (each has its own `_UnionFind` scope; cross-call renames are already reconciled by the store's chain-linking and `_apply_rebirth_chaining`, not by `mine()`'s per-call union-find). Chunking introduces no new correctness risk beyond what incremental sync already accepts. No change to `mine.py`'s rename/identity internals.
- **KTD-2 (no `treat_as_root` for the bounded first-contact/backfill boundary).** The existing genesis-horizon mechanism (`sgt/core/lens.py:348-363`) mints a permanent synthetic add at its boundary commit — appropriate for a deliberate, permanent `sgt init --horizon`, but wrong for a *self-healing* boundary: an op's footprint is part of its content address, so a synthetic add can never be retroactively "reopened" once a later chunk mines the real earlier history. The bounded window's edge instead produces an ordinary dangling `before_version` reference — already a tolerated, advisory `_chain_gaps` shape — which a later backfill chunk resolves simply by adding the earlier op that produces that version (append-only, no rewrite).
- **KTD-3 (deadline-based chunking, not commit-count).** A fixed commit-count chunk is not robust to per-commit cost variance (a single large-diff commit can blow the budget on its own). `mine()` accepts an optional `deadline: float | None` (a `time.monotonic()` cutoff, default `None` = unbounded, preserving every existing caller's behavior, e.g. sync's `merge_base..theirs` teammate-mining) and an optional `history_override: list[tuple[str, str | None, str]] | None` (a pre-computed, already-ordered commit list — lets a caller feed U2's backward-ordered rows through this identical deadline-checked loop instead of `mine()` deriving `history()` itself; `None` preserves today's forward-from-`GitBinding.history()` behavior). After fully processing each commit, `mine()` checks the deadline and stops before starting the next one if it has passed, returning `(ops, last_processed_sha)` instead of a bare list. Default mine-loop budget: 10 seconds, deliberately reserved out of the VS Code extension's 30s `execFile` timeout to leave roughly 20 seconds of headroom for the fixed per-call cost the deadline does *not* bound: `_sync()`'s post-mine ideal-reduction and op-index rebuild, empirically ~11.7 seconds on this repo's ~11k-op store today (Sources/Research), plus CLI startup and JSON serialization. That fixed cost scales with total store size, not chunk size, so it is not itself chunkable within this plan's scope; as the store grows further, the 10s/20s split may need revisiting (see Risks & Mitigations), but making ideal-reduction/op-index rebuild incrementally resumable is a separable, larger effort this plan does not take on. A constant near the call site, not a new config surface (no policy knob requested by the Product Contract).
- **KTD-4 (per-ref backfill-frontier state).** A new persisted table (via `sgt.state.load_json`/`save_json`, same pattern as witness/ideal-table) records, per ref key: `genesis_frontier` (the oldest commit sha fully mined so far, or `null` before first contact) and `reached_genesis` (bool). This is the third member of the checkpoint that must move atomically with witness + ideal-table (R5's pairing becomes a triple, still one `locked_section`, still never nested — U23 unchanged).
- **KTD-5 (R9 gap classification).** `FsckReport` gains `pending_chain_gaps: tuple[str, ...]`. Classification: a gap `sym@before_version` (from the existing `_chain_gaps` computation) is *pending* iff the op that produced that footprint step has a provenance sha equal to some ref's currently recorded `genesis_frontier` — i.e., it sits exactly at a known, still-open backfill boundary. Every other gap keeps today's meaning (advisory, presumed rebase/squash artifact) in `chain_gaps`. This needs no history walk: it's a set-membership check against the small backfill-frontier table.
- **KTD-6 (signal placement).** Compute the partial-state signal once, in a new pure read `lens.sync_status(repo, ref=...)` (no mining triggered — reads only the persisted witness/backfill-frontier tables), and surface it as a `sync_status` field on both `status_view` and `map_view`. `compose_view` already nests `status_view`'s full output, so this satisfies R7's "at minimum `compose_view`/`map_view`" without a third copy of the logic.
- **KTD-7 (TUI reuse, no bespoke mechanism).** The TUI's `action_refresh`/`_render_status` (`sgt/tui/app.py`) already renders `status_view`'s dict in-process; it gains one more conditional segment reading the same `sync_status` field, mirroring the VS Code status-bar chip's rendering, not a new indicator concept.

### Files & Modules Touched

- `sgt/core/mine.py` — `mine()` gains `deadline` and `history_override` params (the latter lets the backward-backfill path in `sgt/core/lens.py` reuse this same deadline-checked loop instead of duplicating it), and `(ops, last_processed_sha)` return shape; `_mine_one`/`_build_ops`/`_UnionFind` unchanged.
- `sgt/core/sync/ingest.py` — update its `mine()` call site(s) to unpack the new `(ops, last_sha)` return shape (currently unpacks a bare `list[Op]`).
- `sgt/core/migrate.py` — same call-site update for its `mine()` call site(s).
- `sgt/store/gitbind.py` — new method to walk a bounded backward window (newest-first from a given tip, for backfill's opposite direction); `history()` unchanged.
- `sgt/core/lens.py` — `_sync()`'s driving loop rewritten around one-chunk-per-call + triple checkpoint; new `_load_backfill_state`/`_save_backfill_state` helpers; new `sync_status()` pure read; `get()`/`init()`/`current_ideal()`/`ideal_for_ref()` call signatures unchanged.
- `sgt/state.py` — one new `_ARTIFACTS` registry entry for the backfill-frontier table's key (`load_json`/`save_json` dispatch through this registry the same way the existing `witness`/`ideal_table` entries do — an unregistered table name raises `KeyError` on first use); no other code change.
- `sgt/core/store.py` — `FsckReport` gains `pending_chain_gaps`; `_chain_gaps`/`fsck()` extended to classify against the backfill-frontier table.
- `sgt/api.py` — `status_view`/`map_view` gain a `sync_status` field; `compose_view` unchanged (inherits via nesting).
- `sgt/tui/app.py` — `_render_status` gains a conditional "indexing history" segment.
- `editor/vscode/src/statusBar.ts` — `GitStatusBar.refresh` gains a conditional badge/tooltip segment when `status.sync_status.complete` is false.
- `editor/vscode/src/types.ts` (or wherever `StatusView`/`MapView` types are declared) — add the `sync_status` field to match the new JSON shape.

### Dependencies

None. No new third-party packages — this is entirely internal control-flow and persistence-shape changes using existing helpers (`sgt.state.load_json`/`save_json`, `sgt.core.store.locked_section`).

### Risks & Mitigations

- **Risk: a chunk that mines zero commits (deadline hit before even one commit completes).** Mitigation: the deadline check only fires *after* a commit fully completes (KTD-3), and the first commit of a chunk is not deadline-gated — every chunk makes at least one commit of progress at the store level. This does not fully close the gap for a client-side kill, though: a single commit whose own diff takes longer than the client's 30s hard timeout still produces zero *observed* progress for that call, because the client kills the process mid-commit before `mine()` ever reaches its own post-commit deadline check. This plan treats that case as an accepted, rare residual risk (a single commit large enough to exceed a 30s diff is pathological) rather than one it fully closes — R1/R3's guarantee holds at the store level (the commit does complete and checkpoint on a subsequent, unkilled call) but not at the client-observed level for that one call.
- **Risk: the 10s mine-loop deadline (KTD-3) plus the ~11.7s fixed post-mine cost (ideal-reduction, op-index rebuild) could together approach the client's 30s kill as the store grows.** Mitigation: the 10s/20s split already reserves headroom against today's measured ~11.7s baseline; if that baseline grows materially as the store scales, the split needs revisiting, or the post-mine work needs to become incrementally resumable in its own right — out of scope for this plan, but flagged here rather than assumed solved.
- **Risk: forward catch-up and backward backfill both being outstanding for the same ref could starve one direction forever if a caller always has new commits to catch up on.** Mitigation: prioritize forward catch-up when `witness != HEAD` (staying correct/current is more valuable than completeness of old history), but this is a policy choice worth restating in code comments, not a correctness requirement — R6 only requires that once caught up, it stays caught up, and R5 only requires "until the ref's mined range covers genesis-to-HEAD" without a fairness guarantee between directions.
- **Risk: `pending_chain_gaps`' classification (KTD-5) could mis-classify a gap as pending forever if a ref's backfill silently stalls (e.g., a bug always chunks 0 commits backward).** Mitigation: this is exactly the shape of bug the Risk above already asks to guard against operationally (every chunk makes ≥1 commit of progress); no additional design surface needed, but flag as a natural test scenario (see U5).

## Implementation Units

### U1 — Deadline-bounded `mine()`

- **Files:** `sgt/core/mine.py`
- **Depends on:** none
- **Approach:** Add `deadline: float | None = None` and `history_override: list[tuple[str, str | None, str]] | None = None` to `mine()`'s signature. When `history_override` is given, the main commit loop iterates it directly instead of calling `GitBinding.history(repo, since, target)` — this lets U4's backward-backfill path (U2's `history_backward` output) reuse this exact deadline-checked loop rather than duplicating it. In the main commit loop (currently `for order, (sha, parent, _subject) in enumerate(history): ...`), after each commit's `_mine_one` call completes, check `deadline is not None and time.monotonic() >= deadline`; if true, stop consuming `history` and record the last fully-processed sha. Skip the `include_dirty` pass entirely when the loop stopped early (a partial chunk should never mine the working tree — that only makes sense once the chunk reaches `target`). Change the return type from `list[Op]` to a small `(ops: list[Op], last_sha: str | None)` tuple — `last_sha` is `None` if `history` was empty, otherwise the last commit sha actually processed (which equals `target`'s resolved sha only if the loop ran to completion). Every existing caller of `mine()` passes no `deadline`/`history_override`, so this is purely additive; update those call sites — including `sgt/core/sync/ingest.py` and `sgt/core/migrate.py`, plus the existing `mine()` test suite's call sites — to unpack the new tuple shape.
- **Test scenarios:**
  - `mine(repo, since=None, target="HEAD", deadline=<time.monotonic() + huge>)` on a small fixture repo returns identical ops to `mine(repo, since=None, target="HEAD")` today (deadline far in the future never triggers).
  - `mine(repo, since=None, target="HEAD", deadline=<time.monotonic() - 1>)` (already expired) mines zero commits, returns `([], None)` — confirms the "at least one commit always makes progress" guarantee is enforced by the caller (`_sync`), not by `mine()` itself refusing to start.
  - A fixture repo with N commits and a deadline calibrated (via a fake/injectable clock, not real sleep) to expire after commit K returns exactly the first K commits' ops and `last_sha == <sha of commit K>`.
  - `include_dirty=True` combined with an expired mid-range deadline: the dirty pass is skipped (asserted by absence of any `provenance=()` op in the result).
- **Verification:** existing `mine()` test suite (wherever it lives — likely `tests/core/test_mine.py` or similar) passes unchanged with the new tuple-unpacking call-site updates, including `sgt/core/sync/ingest.py`'s and `sgt/core/migrate.py`'s own test coverage with their call sites updated for the tuple shape; new deadline-specific tests above pass.

### U2 — Backward-chunked history walk

- **Files:** `sgt/store/gitbind.py`
- **Depends on:** none
- **Approach:** Add a method (e.g. `history_backward(self, tip: str, limit: int | None = None) -> list[tuple[str, str | None, str]]`) that walks `git log --format=...` from `tip` **newest-first** (unlike `history()`'s `--reverse`), optionally capped at `limit` rows (a coarse upper bound on the git-log walk itself, not a substitute for deadline-bounding the mining work), returning the same `(sha, first_parent, subject)` shape `history()` uses. `_sync`'s backward-backfill path (U4) reverses the fetched rows to oldest-first and passes them to `mine()` as `history_override` (U1) alongside the same per-call `deadline`, so the backward direction's mining cost is bounded by the identical per-commit deadline check U1 implements for the forward direction — `history_backward` itself only produces candidate commits; it does no deadline-bounded work of its own.
- **Test scenarios:**
  - On a fixture repo, `history_backward(repo, tip=<some sha 5 commits deep>)` returns the same 5 `(sha, parent, subject)` rows `history(repo, since=None, target=<that sha>)` would, just in reverse order.
  - `history_backward` at the root commit returns a single row whose `first_parent` is `None`.
  - `limit` truncates to the newest N rows without needing to walk the full history first (verify via a repo where full history is deliberately larger than `limit`, confirming no full walk artifact e.g. no error on an otherwise-unreachable earlier corrupt commit).
- **Verification:** new unit tests pass; no existing caller of `GitBinding` is affected (purely additive method).

### U3 — Per-ref backfill-frontier persistence

- **Files:** `sgt/core/lens.py`
- **Depends on:** none
- **Approach:** Register `"backfill"` in `sgt/state.py`'s `_ARTIFACTS` dict (the same registry entry `witness`/`ideal_table` already have) — `load_json`/`save_json` dispatch through this registry, so an unregistered table name raises `KeyError` on first call. Then add `_load_backfill_state(repo) -> dict[str, dict]` / `_save_backfill_state(repo, table) -> None` following the exact pattern of `_load_witnesses`/`_save_witnesses` (via `sgt.state.load_json`/`save_json` with the new `"backfill"` table name). Each ref key maps to `{"genesis_frontier": str | None, "reached_genesis": bool}`. No mining logic here — pure state plumbing, mirroring the existing witness/ideal-table helpers exactly so the later `_sync` rewrite (U4) can treat all three tables uniformly inside one `locked_section`.
- **Test scenarios:**
  - Round-trip: save a table, load it back, get the identical dict.
  - Missing file (fresh repo) loads as `{}`, matching `_load_witnesses`'s current missing-file behavior.
- **Verification:** unit tests pass; no behavior change for any existing ref (the table is empty until U4 starts writing to it).

### U4 — Chunked, checkpointed `_sync()`

- **Files:** `sgt/core/lens.py`
- **Depends on:** U1, U2, U3
- **Approach:** Rewrite `_sync()`'s body (`sgt/core/lens.py:259-336`) around the KTD-1/KTD-2 design:
  1. Load witness, ideal-table, and backfill-state (U3) for this ref's key.
  2. Decide this call's one chunk: if there is no witness yet (true first contact), the chunk is a backward window from `head` via U2's `history_backward`, fed into `mine()` as `history_override` with the same deadline bound (`deadline=<now + 10s>`, KTD-3) — this is the *only* first-contact case, and it bootstraps `witness=head` at this same chunk's checkpoint in step 4 below, never via a separate forward mine (see the closing paragraph). If `witness != head` (a previously-synced ref that has since fallen behind), do ordinary forward catch-up (`mine(repo, since=witness, target=head, deadline=<now + 10s>)`). Otherwise (witness already caught up to `head`, but `reached_genesis` still `False`), do backward backfill via U2's `history_backward` from `genesis_frontier`, again deadline-bounded and fed through `history_override`.
  3. Run `store.add(op)` for every mined op (existing per-op locked pattern, unchanged), same as today.
  4. Checkpoint: one `locked_section(repo)` writing witness (advanced to `last_sha` if forward, unchanged if backward), ideal-table (existing reduce-to-ideal logic, unchanged), and backfill-state (`genesis_frontier` advanced to the backward chunk's oldest processed sha, or `reached_genesis=True` once that sha's parent is `None`) — whenever *any* of the three changed, not only when the reduced ideal set did. This is a deliberate widening of today's skip-write guard: a backward-only chunk can advance `genesis_frontier` while leaving the witness and the reduced ideal set untouched, and that alone must still persist, or backfill progress silently fails to checkpoint.
  5. Return the `Ideal` exactly as today.
  First-contact bootstrapping (the branch named in step 2): the very first chunk on a never-witnessed ref sets `witness=head` as part of that same chunk's checkpoint (not a separate call) — mine a small backward window from `head` (via U2), checkpoint `witness=head` + the resulting `genesis_frontier` together, so the ref is immediately "forward current" and every subsequent call is pure backward backfill until `reached_genesis`.
  `init(horizon=...)`'s existing `treat_as_root` path (R10) takes precedence over this bootstrapping and is otherwise untouched: a ref initialized with an explicit horizon seals its boundary permanently at `init` time, so this plan's backfill machinery never activates for it — `reached_genesis` is set `True` immediately when the horizon is established, since there is deliberately nothing earlier to backfill. The backward-window bootstrap above applies only to the ordinary, un-horizoned first-contact case.
- **Test scenarios:**
  - AE1: never-synced ref, 500 commits ahead of empty store — one `_sync()` call with a short test deadline completes, returns a valid (partial) `Ideal`, and persists `witness=head` + a `genesis_frontier` short of the root.
  - AE1 (kill/resume): simulate a process kill by simply not calling `_sync` again from the same in-memory state — the next call re-reads persisted state from disk and resumes from `genesis_frontier`, never re-mining commits already checkpointed (assert via op-store call count or mined-sha set disjointness between the two calls).
  - AE2: repeated `_sync()` calls on the same ref eventually drive `reached_genesis` to `True`; once true, a further call with no new commits mines zero ops and leaves state unchanged (idempotent steady state).
  - AE2 (mid-flow forward + backward interleave): a ref mid-backfill that also gains new real commits on `head` prioritizes forward catch-up on the next call (per the Risks section's stated policy), verified by asserting the chunk mined is forward-direction when `witness != head`.
  - R5/R2/R3 invariant: after every single `_sync()` call (success or simulated-killed-before-return), `fsck`'s `unreachable_witnesses`/`invalid_ideals` are empty — the triple checkpoint is never observed in a torn state (test by asserting witness, ideal-table, and backfill-state files' mtimes/content are always mutually consistent after each call, e.g. by injecting a crash immediately after `store.add` but before the `locked_section` block and confirming *none* of the three tables moved).
- **Verification:** the existing `_sync`/`get`/`init` test suite passes with no regressions (particularly around R5's atomicity, U23's no-nesting contract, and R10 — a pre-existing invariant this plan does not introduce or renumber alongside R1-R9 above: `init(horizon=...)`'s existing `treat_as_root` behavior, kept unchanged since KTD-2 only concerns the *un-horizoned* first-contact path). New scenarios above pass.

### U5 — `fsck` pending-vs-confirmed chain gaps

- **Files:** `sgt/core/store.py`
- **Depends on:** U3, U4
- **Approach:** Add `pending_chain_gaps: tuple[str, ...] = ()` to `FsckReport`. In `fsck()`, after computing `_chain_gaps(ops)` as today, load the backfill-state table (U3) and build the set of all refs' `genesis_frontier` shas that are not yet `reached_genesis`. For each gap string `f"{sym}@{version}"`, look up which op produced that `version` for `sym` (the op whose footprint's `after_version` for that symbol equals `version`) and check whether that op's provenance intersects the open-frontier sha set; if so, move it to `pending_chain_gaps` instead of `chain_gaps`.
- **Test scenarios:**
  - A ref mid-backfill (real fixture: init a repo, run one chunked `_sync` with a tiny deadline so it stops mid-history) produces a gap at exactly the `genesis_frontier` boundary; `fsck` reports it under `pending_chain_gaps`, not `chain_gaps`.
  - A rebase/squash on a fully-`reached_genesis` ref (existing `_chain_gaps` test fixture, if one exists — reuse it) still reports under `chain_gaps`, unchanged from today.
  - A repo with no incomplete refs at all reports `pending_chain_gaps == ()` always, regardless of `chain_gaps` content (regression guard: never mis-classify once nothing is mid-backfill).
- **Verification:** existing `fsck`/`_chain_gaps` tests pass unchanged (new field defaults to `()`, `ok` is unaffected exactly like today's `chain_gaps`); new AE4 scenario passes.

### U6 — Partial-state read signal

- **Files:** `sgt/core/lens.py`, `sgt/api.py`
- **Depends on:** U3, U4 (test scenarios below rely on U4's short-deadline fixture technique to produce a ref stopped mid-backfill)
- **Approach:** Add `sync_status(repo, ref=None) -> dict` to `lens.py` — a pure read (no mining) returning `{"complete": bool, "reached_genesis": bool}` (and optionally the raw `genesis_frontier` sha for diagnostics) by loading the witness + backfill-state tables for the ref's key; `complete` is `True` iff `witness == head` and `reached_genesis` is `True`. Wire this into `status_view` (`sgt/api.py:1302`) as a new `sync_status` key, and into `map_view` (`sgt/api.py:426`) the same way. `compose_view` needs no direct change (it already nests `status_view`'s output).
- **Test scenarios:**
  - A freshly-`init`ed, fully-synced small fixture repo reports `sync_status.complete == True` from both `status_view` and `map_view`.
  - A ref with a `_sync()` call stopped early by a short deadline (same fixture technique as U4's tests) reports `complete == False` from both views, and this never triggers additional mining (assert no new ops are added by calling `status_view`/`map_view` alone).
- **Verification:** existing `status_view`/`map_view`/`compose_view` snapshot/shape tests updated for the new field (additive key, should not break byte-equality guardrails that check specific keys rather than exact dict equality — if `compose_view`'s R21 byte-equality guardrail mentioned in its docstring does an exact equality check against `{"map": map_view(repo), ...}`, this is preserved automatically since `sync_status` now lives inside `map_view`'s own return value, not bolted on separately).

### U7 — VS Code "indexing history" indicator

- **Files:** `editor/vscode/src/statusBar.ts`, `editor/vscode/src/types.ts` (or the actual location of the `StatusView`/`MapView` TypeScript types)
- **Depends on:** U6
- **Approach:** Add `sync_status: { complete: boolean; reached_genesis: boolean }` to the `StatusView` (and `MapView`) TypeScript interface. In `GitStatusBar.refresh()` (`editor/vscode/src/statusBar.ts:26-43`), when `status.sync_status.complete` is `False`, prepend an indexing glyph/text (e.g. `"$(sync~spin) indexing · "`) to `this.item.text` and extend the tooltip; when complete, render exactly as today (no change for the common case).
- **Test scenarios:** (VS Code extension tests, if a harness exists — otherwise manual verification per the project's UI-testing norms) — status bar shows the indexing indicator when `sync_status.complete` is false and clears it once a subsequent poll reports `true`, without needing an extension reload.
- **Verification:** manual check in the Extension Development Host against a fixture repo mid-backfill, per this project's stated norm of testing UI changes in a live browser/host before reporting complete.

### U8 — TUI "indexing history" line

- **Files:** `sgt/tui/app.py`
- **Depends on:** U6
- **Approach:** In `_render_status` (`sgt/tui/app.py:233-241`), add a conditional segment reading `st["sync_status"]["complete"]`, mirroring the existing `oracle_txt` conditional pattern immediately above it in the same function.
- **Test scenarios:** existing TUI status-line tests (`tests/tui/`) extended with a case asserting the indexing segment appears/disappears based on `sync_status.complete`.
- **Verification:** existing TUI tests pass; new case passes.

### U9 — Doc-comment tail ownership

- **Files:** `sgt/core/lens.py`, `sgt/core/store.py`
- **Depends on:** U4, U5
- **Approach:** Update `_sync`'s R5 doc comment (and the module docstring's genesis-horizon note) to describe per-chunk checkpointing instead of "only after the full mine completes"; update `_chain_gaps`'/`FsckReport`'s doc comments to describe the pending-vs-confirmed distinction (U5). Pure documentation, no behavior change — this is the Goal Capsule's explicit "Tail ownership" item.
- **Test scenarios:** none (doc-only).
- **Verification:** review only.

## Verification Contract

- **AE1 → U1, U2, U4.** Fixture-repo test: never-synced ref, deadline-bounded first `_sync()` call completes fast, returns a partial `Ideal`, and a simulated kill-and-resume never re-mines a checkpointed commit.
- **AE2 → U4.** Repeated calls drive `reached_genesis` to `True`; post-completion calls behave exactly like today's plain incremental sync (reuse existing incremental-sync tests as a regression baseline against the *after*-chunking-completes behavior).
- **AE3 → U6, U7, U8.** `compose --json --full`'s (nested `status`) output carries `sync_status`; VS Code and TUI both render/clear an indicator keyed off it.
- **AE4 → U5.** `fsck` distinguishes a real mid-backfill gap from a rebase-caused one via `pending_chain_gaps` vs `chain_gaps`.
- **R5/U23 regression baseline.** Every existing test asserting witness+ideal-table atomicity, and every existing test asserting `locked_section`/`Store.add()` never nest, must continue to pass unmodified — this plan extends the checkpoint's payload (witness, ideal-table, backfill-state) but not its locking discipline.
- **R10 regression baseline.** `sgt init(horizon=...)`'s existing `treat_as_root` behavior and its tests are untouched (KTD-2 only changes the *unhorizoned* first-contact path).

## Definition of Done

- [ ] `mine()` accepts an optional `deadline` and returns `(ops, last_sha)`; every existing call site updated; existing `mine()` tests pass unchanged plus new deadline tests (U1).
- [ ] `GitBinding.history_backward()` added and unit-tested (U2).
- [ ] Backfill-frontier state table added, round-trip tested (U3).
- [ ] `_sync()` performs one bounded, checkpointed chunk per call in either direction; AE1/AE2 scenarios pass; R5/U23/R10 regression baselines pass (U4).
- [ ] `FsckReport.pending_chain_gaps` distinguishes backfill-pending gaps from confirmed gaps; AE4 passes (U5).
- [ ] `sync_status` read added and surfaced on `status_view`/`map_view` (and thus `compose_view`); no mining triggered by the read itself (U6).
- [ ] VS Code status bar shows/clears an indexing indicator, verified live in the Extension Development Host (U7).
- [ ] TUI status line shows/clears the same signal, existing TUI tests plus new case pass (U8).
- [ ] `_sync`'s R5 doc comment and `fsck`'s chain-gap doc comments updated to describe per-chunk checkpointing and the pending/confirmed distinction (U9).
- [ ] Full existing test suite passes with no regressions; no new dependencies added.
