---
title: "feat: Semantic repair loop for revert --keep-dependents hollow fulfillment"
type: feat
status: completed
date: 2026-07-14
---

# feat: Semantic repair loop for revert --keep-dependents hollow fulfillment

## Summary

Automate fulfilling the hollow ops `revert --keep-dependents` drafts, via a pluggable,
LLM-backed repair loop that reuses the existing `stage → oracle → land` gate verbatim and never
bypasses it. Ships a direct OpenAI API backend; a coding-agent-handoff backend is a documented
seam, not built.

## Problem Frame

`revert --keep-dependents` removes a target op's full up-set but drafts a continuation hollow per
direct reference-dependent, naming exactly the symbol that must stop calling the removed one. A
human fulfills those hollows by hand today (`sgt fulfill --from-tree`, `sgt land`). sgt already
owns the three hard parts of an automated-program-repair system for free — localization (hollow
ops), one-hop context (footprint/requires/intent), sandbox-free validation (oracle keyed to the
exact ideal) — so only patch generation and a loop controller were new.

## What was built

### `sgt/repair/` (new package, mirrors `sgt/lens/` — LLM/network-touching, stays out of `sgt/core/`)

- `backends.py` (U1) — `RepairRequest`/`RepairProposal` (pydantic), abstract `RepairBackend`,
  and `EchoBackend` (returns the image unchanged — useless for a real repair, useful for
  deterministically exercising the reject-and-retry path in tests).
- `context.py` (U2) — compressed per-hollow context: current image, the removed symbol's
  intent/signature, one-hop reference neighbors (signature lines only), and prior-attempt
  feedback (Tier-0 residual or oracle tail).
- `verify.py` (U3) — Tier-0: free, pure, in-memory static verification. Checks (in order) that
  every proposed image parses, that none of them still name the reverted target's own symbol
  (a lexical/regex leaf-name check — see "Kernel fact 6" below for why this had to be lexical,
  not graph-based), and that the resulting candidate forms a valid ideal
  (`sgt.core.rewrite.build_candidate`).
- `api_backend.py` (U4) — `ApiBackend`, copying `sgt/lens/label.py`'s OpenAI call pattern
  (`gpt-5.4-mini`, low effort, member-hash cache). No offline fallback: a wrong-but-plausible fix
  is worse than none.
- `loop.py` (U5) — `repair(repo, draft, backend, *, max_attempts, max_oracle_rounds, plan)`: a
  per-hollow Tier-0 loop (with a `StuckDetector` that gives up on a hollow once the backend
  repeats an already-rejected image) feeding into a real oracle round (`stage` once; on a red
  verdict, `unstage` and deterministically re-draft with the oracle's `output_tail` as feedback,
  up to `max_oracle_rounds`). Lands only on a passing verdict; a red final verdict is
  `ok=False`, never overridden.

### `sgt/core/rewrite.py`

Factored the pure candidate-construction step out of `stage` into
`build_candidate(repo, draft, images) -> tuple[Ideal, dict[str, Op]]`, so Tier-0 and `stage`
share one source of truth for what a candidate is, without `stage`'s side effects (`store.add`,
tree write, `staged.json`).

### CLI (U6)

- `sgt revert <ref> --keep-dependents --repair` (`sgt/cli/ideal_edit.py`) — hands the produced
  draft straight to the repair loop instead of printing it.
- `sgt repair <draft-id>` (`sgt/cli/rewrite.py`) — resumes an already-drafted hollow set.
- Both take `--backend` (v1 accepts only `api`) and `--json`. Registering the new `repair`
  subparser also required adding `"repair"` to `sgt/cli/__init__.py`'s `_VERBS` allowlist —
  `main()` checks that set before argparse ever runs, so a correctly-wired subparser with a
  missing `_VERBS` entry silently falls through to the top-level help text (found and fixed this
  round; see kernel fact 7).

### Tests (U8)

`tests/repair/test_loop.py` — five deterministic tests against a `FakeBackend` (scripted images,
indexed by total call count so far, not `RepairRequest.attempt`, which restarts at 1 every oracle
round): happy-path land + attribution, Tier-0 reject-then-recover, `StuckDetector` stop (tree
stays clean), an oracle round failing then recovering on the next, and oracle exhaustion
(`ok=False`, tree clean). Fixture is same-file/separate-commit (helper/user), which sidesteps two
unrelated, already-documented gaps rather than triggering them (see Scope Boundaries).

### U7 — transitive-dependent hollows (shipped, not the deferred one-hop-only version)

`revert_keep_dependents` still drafts a real, backend-facing hollow only for *direct*
reference-edge dependents. Everything else in `upset_in` that's still alive at the pre-removal
frontier is recorded by symbol name in `draft.meta["carry_forward"]`; `build_candidate` mints it
directly (same footprint, same image, `requires` cleared), mirroring the existing `split-op`
automatic-tail pattern. `sgt.repair.loop.repair` needed zero changes — it only ever iterates
`draft.hollow_ids` — so the entire transitive tail resolves for free, with no backend call
regardless of chain depth. See kernel fact 8 and FINDINGS.md's U7 entry.

## Kernel facts confirmed or newly discovered

The plan's four confirmed-in-code facts held as designed. Two more surfaced during
implementation and are recorded here (and in `FINDINGS.md`) since they weren't visible until the
code was actually run against real fixtures rather than just imported:

1. Fulfilled repair ops carry empty `requires` (`revert_keep_dependents` drafts hollows with no
   `requires`) — accepted by grounding, but means the kernel itself can never reject a lingering
   call to the removed symbol; only Tier-0's lexical check or the real oracle can.
2. `stage` is destructive and single-slot; called exactly once per candidate, only after a
   Tier-0-approved winner set.
3. Tier-0 can run fully in memory over `build_candidate`/`fold.code` — no disk, no oracle.
4. Attribution is applied post-land, per-SHA, mirroring `session.land`.
5. **`rewrite.land()` never re-mines** (unlike `session.land()`, which explicitly does). A
   hollow-fulfilled op's `provenance` therefore stays permanently `()` unless something seeds it
   — and `Store._serialize` only persists an `Attribution` entry for a SHA that is *also* present
   in `provenance`. `loop.py`'s post-land step now does `store.add(replace(op,
   provenance=(sha,)))` immediately before `store.attribute(...)` to make the write actually
   land on disk. Without this, every successful repair would silently produce unattributed ops.
6. **A graph-based dangling-reference check can never fire for the one thing it exists to
   catch.** `build_entity_graph`'s name resolution is built entirely from the candidate codebase
   it's given; the reverted target's own definition is, by construction, always absent from that
   codebase, so a reference to it can never resolve into an edge (an unresolved name is dropped,
   not turned into a dangling edge). `verify.tier0`'s check for "does this proposal still call
   the removed symbol" had to be a lexical/regex leaf-name check instead — the graph-based
   version that was drafted first was structurally dead code, verified by direct test before
   being replaced.
7. `sgt/cli/__init__.py`'s `_VERBS` allowlist gates dispatch before argparse — a new subcommand
   needs an entry there in addition to its family module's `register()`, or it silently falls
   through to top-level help.
8. A transitive dependent's own bytes almost never need to change: `order.upset_in` puts it in
   the up-set only because its `requires` names an exact `(symbol, version)` pair a *direct*
   dependent produced, and that pair stops being produced once the direct dependent is rewritten
   to a new op id. Re-minting the transitive dependent with the same footprint/image and cleared
   `requires` (no hollow, no backend call) is sufficient — confirmed by a 3-op chain fixture
   landing with the same `backend.calls` count as the direct-dependent-only case.

## Scope Boundaries (unchanged from the plan, confirmed still accurate)

- Repairs only reference-dependent hollows from `revert_keep_dependents`.
- No sandbox, no parallelism — Tier-1 runs in place, serially.
- No offline repair fallback — needs an API key or an injected backend.
- ~~One-hop only~~ — U7 shipped 2026-07-14: transitive dependents are carried forward unchanged
  (no hollow, no backend call) rather than dropped; see `FINDINGS.md`'s U7 entry.
- Empty-`requires` blind spot: a future revert won't cascade through a repaired op.
- No auto-override of a red oracle; no GC of abandoned candidate ops.
- **Newly confirmed, not fixed:** a dependent symbol's module-level import (e.g. a top-of-file
  `from x import y`) lives in a separate residue entity a single-symbol hollow fix cannot touch.
  A cross-file fixture that actually imports/executes the fixed file can fail the oracle on a
  leftover dangling import even after the symbol's own body is correctly rewritten. Worked around
  in tests by using a same-file fixture; not a product fix in this round.

## Verification

- `uv run pytest tests/repair tests/core/test_rewrite.py -q` — green, no regressions.
- `uv run pytest -q` — full suite green.
- Golden CLI-surface snapshot regenerated (`SGT_UPDATE_GOLDEN=1 uv run pytest
  tests/golden/test_cli_golden.py -q`); diff is empty — the golden fixture set doesn't exercise
  the new repair surface, and no existing captured verb's output changed.
- Manual end-to-end (scripted `FakeBackend`, no live API key) covering: happy path, Tier-0
  reject-then-recover, `StuckDetector` stop, an oracle round failing then recovering, and full
  oracle exhaustion — all five now also live as the `tests/repair/test_loop.py` pytest cases.
- U7: `tests/core/test_rewrite.py::test_revert_keep_dependents_carries_transitive_dependent_forward_unchanged`
  and `tests/repair/test_loop.py::test_transitive_dependent_survives_without_costing_a_backend_call`
  — a 3-op chain (`helper <- user <- caller`) drafts one hollow (for `user`), carries `caller`
  forward with byte-identical content and `requires == frozenset()`, and lands with
  `backend.calls == 1`, same as the direct-dependent-only case.

## Sources & Research

- `sgt/lens/label.py` — the OpenAI call pattern `api_backend.py` copies.
- `sgt/core/session.py` — the post-land `Attribution` stamping pattern mirrored (with the
  provenance-seeding correction above).
- `sgt/entities/graph.py` — `build_entity_graph`'s codebase-scoped name resolution, root cause of
  kernel fact 6.
- `tests/core/test_rewrite.py` (line ~169) — the separate-commits fixture pattern, and
  `tests/core/test_oracle.py`'s `_configure` helper, both reused directly in `test_loop.py`.
