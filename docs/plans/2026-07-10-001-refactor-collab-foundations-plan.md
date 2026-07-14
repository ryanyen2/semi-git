---
title: "refactor: collaboration-ready foundations — restructure before the next feature wave"
type: refactor
status: proposed
date: 2026-07-10
origin:
  - docs/design/2026-07-10-sgt-as-version-control.md
  - docs/design/2026-07-10-collaboration-and-review.md
continues: docs/plans/2026-07-06-001-feat-operation-ideal-kernel-plan.md   # U1–U15 shipped; units here continue the numbering at U16
---

# refactor: collaboration-ready foundations

## Summary

U1–U15 delivered the kernel clean: one algebra, laws harness, legacy mechanisms deleted. The two
2026-07-10 design docs now demand roughly a doubling of the surface — porcelain verbs, a hardened
sync, semilattice metadata, land/propose, the proposal object. **If that work lands as patches on
the current structure, three specific modules become the next "mixture":** the 980-line
hand-parsed `cli.py`, the monolithic `sync()` pipeline, and the `.sgt/` on-disk layout whose
knowledge is scattered across 13 files with no schema versioning. This plan restructures those
three seams *first*, behind characterization tests, then lands the collaboration features on the
restructured seams. Every decision point below is stated with its alternatives, tradeoff, and the
pitfall that made us choose — per the owner's instruction: think before writing, hunt the unknown
unknowns, no patch-piling.

---

## Problem Frame

The audit for the collaboration design doc found two shipped [GAP]s (foreign remote work
invisible in sync; pin reconcile is sync-order-dependent) and three fragilities (`-X ours` as a
union device, trailer-only ideal records, no miner-version handshake). Each is *individually* a
small patch into `sync.py` — and that is exactly the trap. Four different features (sync, land,
propose, adoption-on-contact) need the same internal stages of that pipeline; five new metadata
artifacts (`ideal.json`, `forks.json`, claims, proposals, aliases) need the same schema/versioning
treatment the existing four artifacts never got; and ~10 new CLI verbs need the parser that
`_strip_opt` hand-rolling already fumbled once (the `--horizon` bug, commit `4cc7b88`).

The evidence that the codebase is otherwise sound: kernel modules are small and law-tested,
`sgt.api` single-projection discipline held through U13's surface rewrite, the oracle already
keys verdicts to exact op-sets (`oracle.ideal_key`), and content-addressing gives replica
determinism (LAW-0) for free. The refactor is narrow and targeted, not a rewrite.

---

## Current-state audit (evidence, not vibes)

R-numbered ids cited in this plan (R8, R11, R21) are requirements of the kernel plan this plan
continues (`docs/plans/2026-07-06-001-feat-operation-ideal-kernel-plan.md`); C-numbered ids are
this plan's own.

| area | state | debt |
|---|---|---|
| kernel (`core/`) | 11 modules, ≤500 lines each, law-tested | none structural; `sync.py` is the exception (below) |
| `sync.py` | one 95-line function, 6 integration tests | monolith; `-X ours` load-bearing; trailers-only theirs-ideal (`sync.py:118`); fork ⇒ abort-all |
| `cli.py` | 980 lines, hand-rolled `_strip_opt` parsing | parser bug class already bitten; ~10 verbs incoming incl. `sgt git` passthrough (needs REMAINDER semantics) |
| `.sgt/` layout | knowledge spread across 13 files; zero schema versioning | every new artifact scatters further; historical blobs are read raw (`_pins_at` reads teammate blobs at arbitrary SHAs) |
| metadata reconcile | `union_pins` latest-wins = file order (`pins.py:61`); feature ids replica-local (Greene vs ours); declared edges G-Set, no retraction | violates LAW-U (order-independent sync); poisons cross-replica references |
| provenance | `Op.provenance: tuple[commit-sha, ...]` | unstructured; the design docs need `{session, agent, plan}`; excluded from id, so additive |
| oracle | verdicts keyed to op-set hash — already LAW-G-shaped | verdict cache is gitignored-local; claims can't travel |
| tests | laws + golden harness healthy | stale dirs from deleted subsystems (`tests/{decisions,effects,engine,merge,orchestrate}/` hold only `__pycache__`); root `test_decision_layout.py` probably tests deleted U13 surface |
| store locking | single-writer lock + atomic renames (R11) | designed for one process; SYNC-2 wants concurrent sessions — lock *scope* unexamined |

---

## Key Decision Points

Each: the decision, the alternatives weighed, the pitfall that decides it. These are the places
a reviewer should push back.

### D1. Refactor-first, features-second — with a characterization gate

- **Alternatives:** (a) land collab features directly on current structure, refactor later;
  (b) big-bang refactor everything then features; (c) refactor exactly the three seams the
  features demonstrably stress, each behind characterization tests, features immediately after.
- **Tradeoff:** (a) is fastest to first demo and produces the patch-pile the owner vetoed; (b)
  maximizes regression risk with no feature payoff for weeks; (c) costs ~2 units of "no visible
  progress" but every subsequent unit gets cheaper.
- **Pitfall being dodged:** refactoring *without* characterization first is how behavior drifts
  silently — U10's pattern (goldens before legacy deletion) is the house style; keep it.
- **Decision:** (c). U16 freezes behavior (golden CLI snapshots, replica-schedule law harness —
  deliberately red where LAW-U is known-broken) before any restructuring.

### D2. CLI: argparse subcommands in a `sgt/cli/` package — not click, not more hand-rolling

- **Alternatives:** (a) keep `_strip_opt`, split file; (b) stdlib `argparse` subparsers, one
  module per verb family; (c) `click`/`typer`.
- **Tradeoff:** (a) zero migration churn but every verb re-implements flag handling and the
  `--horizon` bug class recurs; (c) best ergonomics but adds a dependency to a deliberately
  stdlib-lean tool (and CLAUDE.md's dependency bar); (b) declarative parsing, zero new deps,
  with `sgt git <args...>` passthrough via a pre-dispatch intercept: if the first CLI token is
  `git`, forward the remaining argv verbatim (advisory check on the subcommand) without entering
  argparse at all — `argparse.REMAINDER` is *not* used; it mis-parses leading git globals
  (`sgt git --no-pager log`, `-c a.b=c`) and is undocumented since Python 3.9 for exactly this
  bug class. Hand-rolling handles the passthrough worst of all (flag-vs-positional ambiguity is
  exactly the `--horizon` failure shape).
- **Pitfall:** argparse changes help text and some error messages — scripts and goldens will see
  it. Mitigation: U16's golden CLI snapshots make every diff deliberate; `--json` output (the
  actual machine contract, R21) must be byte-identical through the migration.
- **Decision:** (b). `sgt git` passthrough ships *in* the migration unit as its proof.

### D3. One `sgt/state.py` owns the `.sgt/` layout, with per-file schema versions — and blob readers dispatch on historical schemas forever

- **Alternatives:** (a) status quo (each module knows its paths/format); (b) central path
  registry only; (c) registry + per-file `schema: n` headers + versioned read/write functions
  that *also* serve reads of blobs at arbitrary SHAs.
- **Tradeoff:** (c) touches 13 files once; (a) touches every future schema change into N files
  forever. (b) is (c) minus the part that actually bites.
- **The unknown unknown this surfaces (found while auditing, worth stating loudly):** schema
  migration is usually framed as "upgrade the working tree once." **sgt readers also read
  *historical* blobs** — `sync` reads pins/tree/declared at `theirs_sha`, which may be any
  teammate's any-vintage commit. So old formats are never retired from the read path; a missing
  `schema` field means v0, and v0 readers live forever. Designing this in now is cheap; painful
  to retrofit after `ideal.json`, `forks.json`, claims, proposals, and aliases quintuple the
  artifact count.
- **Pitfall:** self-hosting hazard — this repo's own `.sgt/` migrates while sgt mines sgt's own
  refactor (the `4cc7b88` mine-rename bug was exactly this class). Mitigation: migration runs
  read-side-first (readers accept v0 *and* v1 before any writer emits v1), so a half-migrated
  state is never unreadable.
- **Old-reader policy (the reverse direction):** a teammate on an *older* sgt can `git pull` v1
  metadata that a v0-only reader silently misparses (garbage pins, not an error) — shipped
  binaries cannot be patched, so the policy is writer-side. Rule: a v1 revision of an existing
  `.sgt/` file must stay v0-parseable (additive keys only); any shape a v0 reader cannot safely
  ignore lands at a *new* path (absent-file already degrades to the documented empty default),
  the old path retained in v0 shape until a stated deprecation window ends. Applies explicitly
  to U21's pin-record witness anchors.
- **Decision:** (c).

### D4. Sync becomes a staged pipeline via strangler decomposition — no behavior change in the same unit as the restructuring

- **Alternatives:** (a) patch the five gaps into `sync()` one by one; (b) rewrite sync fresh
  against the new design; (c) decompose the existing function into pure stages
  (`fetch → ingest → reconcile → resolve → materialize → commit`) with byte-identical behavior
  and the existing 6 tests green, *then* change behaviors stage-by-stage in the next unit.
- **Tradeoff:** (a) is the patch-pile; (b) discards a working, integration-tested module and
  couples "did the restructure break it" with "did the new behavior break it" — undebuggable
  when a two-clone test fails; (c) two passes over the same code but each pass has one reason
  to fail.
- **Why decomposition is justified at all (the reuse argument, not aesthetics):** `land` (U23)
  is `ingest → resolve → materialize` with a local source; `propose` validation (U24) is
  `ingest → resolve` dry-run; adoption-on-contact is the `ingest` stage alone. Four consumers
  is what elevates this from taste to architecture.
- **Included behavior-preserving fix:** replace `-X ours` with explicit tree construction
  (take ours; add theirs' op files; write reconciled metadata; fold source). It is
  behavior-*equivalent* today only by vigilance; making it explicit is the decomposition's
  litmus test — two-clone tests must assert exact tree equality before/after.
- **Decision:** (c).

### D5. Divergence-as-state without weakening the fork-free-ideal invariant

- **The risk (a genuine unknown unknown, defused by construction):** "fork no longer aborts
  sync" sounds like every downstream consumer (`fold`, `verbs.plan_*`, oracle, map) must now
  tolerate forked ideals — an unbounded audit of implicit assumptions.
- **Decision that removes the risk instead of auditing it:** open forks live *outside* the
  ideal. Sync unions the store, advances the branch ideal by the fork-free part only, and
  records excluded tips in committed `.sgt/forks.json`. The invariant "any ideal a verb sees is
  fork-free" is preserved *by construction*; fork-awareness is additive surface (`sgt forks`,
  warnings in `status`/`sync` views), not a kernel-wide behavior change.
- **Tradeoff accepted:** a symbol with an open fork materializes at the pinned tip, so "my
  synced work is missing" is possible-by-design — must be loud in `status` (count of open
  forks) or users will file it as data loss. This is jj's posture and it is the right one for
  multi-agent, but the loudness is load-bearing, not cosmetic.

### D6. Metadata semilattices: witness-anchored tie-breaks; birth-minted feature ids with aliases; OR-Set declared edges

- **Tie-break alternatives:** (a) keep latest-wins file order (status quo — order-dependent,
  LAW-U red); (b) pure content-hash order (fully replica-free, but *arbitrary*: a teammate's
  deliberate re-pin can lose to a stale one forever); (c) witness-DAG topological order with
  hash tie-break — causally later curation wins, deterministic, wall-clock-free (the git DAG is
  already the causal log; no new clock).
- **Decision:** (c); loser always *reported*, never silent (keeps U15's posture).
- **Feature-id migration pitfall (atomicity):** existing trees hold replica-local ids and pins
  *reference* those ids. Re-minting birth ids without rewriting pin references in the same
  transaction corrupts curation. The migration (U21) re-mints deterministically
  (`f-<min founding op id>`), rewrites pin references, and records old→new in the alias table —
  one atomic write set, and aliases are a G-Set so stale references from other clones' history
  still resolve after sync. Birth-id determinism assumes the founding op is shared history at
  migration time; where divergent unsynced curation yields different founding ops for the same
  conceptual feature, the colliding birth ids merge via the alias G-Set (the alias-merge rule),
  covered by a dedicated two-clone test in U21.
- **Declared edges:** OR-Set (add with unique tag, remove kills observed tags) replaces the
  fold-time cycle exclusion loop — a retraction becomes durable, travelling state instead of a
  forever-re-reported warning.

### D7. Structured provenance: dual-read, new-write, no bulk rewrite

- Op files gain `provenance: [{sha, session?, agent?, plan?}, ...]`; readers accept the v0
  tuple-of-shas shape indefinitely (D3's rule — old shapes live in history forever); writers
  emit v1. **No bulk rewrite of the op store**: provenance is excluded from `compute_id`, so
  ids are stable either way, and rewriting thousands of committed op files would be pure git
  churn (and would still not touch historical blobs).
- **Pitfall:** two clones holding v0 and v1 files for the *same op id* — `Store.add_bytes`
  unions on collision, so the union rule must merge shapes, not compare bytes. Test explicitly.

### D8. Verdicts-as-claims: a committed claims table beside the local cache

- Today `.sgt/local/oracle.json` is a private cache — correct for "my machine's verdicts."
  Claims that travel (proposal verdicts) go to committed `.sgt/claims/` keyed by the same
  `ideal_key`, carrying runner identity + environment fingerprint. Local cache stays; a claim
  is a *published* verdict, publication is explicit (`sgt oracle publish` / done by `propose`).
- **Alternative rejected:** committing the whole verdict cache — leaks every private
  experiment's red runs into shared history and makes the cache a merge surface for no reason.

### D9. Scope boundary of THIS plan

- **In:** the three seam refactors (state, CLI, sync), metadata semilattices, SYNC-1 hardening,
  structured provenance + claims, SYNC-2 core (`land` + CAS), proposal object + GitHub
  rendering (foundations).
- **Out (follow-on plan, rides these seams but doesn't shape them):** branch-as-selection UX
  and the BET-C closure measurement, three-tier file boundary / `.sgtignore`, native review
  rail/TUI surfaces, live fs-watch fork warnings (stretch in U23, cuttable), SYNC-3 entirely.
- **Rationale:** everything "in" is either a seam the features stress or a LAW the design doc
  declares; everything "out" is UX that cannot destabilize the substrate if it comes later.

---

## Requirements

- C1. All four LAW-U-relevant reconciliations (pins, declared, tree/feature-ids, provenance
  union) are order-independent: any sync schedule over any replica set converges to identical
  `.sgt/` state. Property-tested with randomized two/three-replica schedules.
- C2. Every `.sgt/` artifact carries a schema version; every reader (working-tree *and*
  blob-at-SHA) dispatches on it; absent = v0. One module owns layout and codecs.
- C3. `sync` ingests foreign (non-sgt) remote commits by mining them — a plain-git teammate's
  work participates in the union (adoption ⊂ sync, one code path).
- C4. A same-symbol fork surfaced by sync is durable, shared state (`.sgt/forks.json`), does
  not block the fork-free remainder from landing, and never enters any verb-visible ideal.
- C5. A ref's committed ideal is recoverable after history rewrites (squash/rebase) from
  in-tree state, not only from trailers.
- C6. Sync refuses (with instructions) to union stores across differing `miner_version`s.
- C7. `sgt push` exists; it never forces; rejection routes to `sgt sync`.
- C8. `sgt git <args...>` passes through verbatim; tree-mutating subcommands get a warning
  naming the sgt-native verb (interception stays advisory in this plan — refusal is porcelain
  policy, deferred with the porcelain plan).
- C9. `sgt land` advances a shared branch record by CAS: fork-free check + oracle green on the
  exact resulting op-set (LAW-G) + re-union retry on CAS failure.
- C10. A proposal (base frontier + Δ op-set + derived feature delta + claim + provenance) is
  creatable, pushable as a git ref, checkable for staleness (re-union), landable, and
  renderable as a GitHub PR body. Machine surface via `sgt.api` views, additive-only (R21).
- C11. `--json` outputs of all pre-existing verbs are byte-identical across the CLI migration.
- C12. The full laws harness (LAW-0, U, I, F, R, G, L from the design doc §8) is executable;
  LAW-U/R red at U16 (documented), green by end of U21; LAW-G green by end of U23 (`land` is its
  enforcement point — see the U20 contract note).

---

## Implementation Units

### U16. Freeze the ground: hygiene + characterization + the replica-schedule harness

- **Goal:** make every later behavior change deliberate and every current defect *documented as
  a red test*, before touching structure.
- **Dependencies:** none.
- **Files:** `tests/laws/test_convergence.py` (new: 2–3 replica randomized sync schedules
  asserting store/order/fork/pin/tree equality — LAW-U, LAW-I, LAW-F, LAW-R, plus dedicated
  LAW-0 (two clones mine the corpus to byte-equal op stores) and LAW-L (no sync schedule moves a
  replica's HEAD selection) assertions, so every C12 law has a named home from the freeze unit
  onward), `tests/golden/`
  (CLI text + `--json` snapshots for every verb), deletions: `tests/{decisions,effects,engine,
  merge,orchestrate}/` (pycache-only), `tests/test_decision_layout.py` + `tests/test_color_parity.py`
  *iff* verified dead (they test the U13-deleted decision surface — verify imports before deleting).
- **Approach:** the harness borrows `tests/core/test_sync.py`'s two-clone rig, generalized to N
  replicas and schedule permutations. LAW-U tests are expected-red (pin order-dependence,
  feature-id divergence) and marked `xfail(strict=True)` with the unit that flips them — the
  repo's "written first, red" convention.
- **Test scenarios:** schedule permutation over disjoint edits (green today); contradicting
  pins under two schedules (red today, xfail); same-fork-resolved-twice convergence (LAW-R).
- **Verification:** full suite green (xfails counted); goldens committed.
- **Status (2026-07-10, shipped `cb28f3a`):** as planned. Both dead layout tests checked and *kept*
  (still guard the live rail webview, resolving that Open Question). LAW-G is the only remaining
  `xfail(strict=True)`, scheduled for U23. FINDINGS "U16 freeze the ground".

### U17. `sgt/state.py`: layout registry, schema codecs, historical-blob dispatch

- **Goal:** C2. One owner for `.sgt/` paths and formats; readers version-dispatch; the five
  incoming artifacts get their slot defined here *before* they exist.
- **Dependencies:** U16.
- **Files:** `sgt/state.py` (new), mechanical call-site migration in the 13 modules that
  currently hand-read/write `.sgt/` paths, `tests/test_state.py` (v0 + v1 fixtures, blob-read
  dispatch via a GitBinding-at-SHA fake).
- **Approach:** read-side-first (D3 pitfall): land readers accepting v0/v1 everywhere, then
  flip writers to emit versioned formats in the same unit but a separate commit. No format
  *content* changes here — this unit is plumbing only, so goldens must not move.
- **Verification:** goldens byte-identical; a v0 fixture repo (copied from a pre-U17 golden
  corpus checkout) round-trips through every verb.
- **Status (2026-07-10, shipped `c58b9d4` + `27673a4`):** as planned; two commits, read-side first.
  FINDINGS "U17 state.py".

### U18. CLI restructure: argparse package + `sgt git` passthrough

- **Goal:** D2. `sgt/cli/` package (one module per verb family: inspect / ideal-edit / feature /
  loop / sync / oracle / rewrite), argparse subparsers, `sgt git` via the pre-dispatch
  verbatim-forward intercept (D2) with the tree-mutating advisory warning (C8, C11).
- **Dependencies:** U16 (goldens), U17 (verbs read paths via `state`).
- **Files:** `sgt/cli/` (new package; `sgt/cli.py` becomes the entry shim), `tests/test_cli.py`
  extended, goldens updated deliberately where help/error text changes.
- **Approach:** migrate verb-family-by-verb-family, goldens re-approved per family — never one
  big diff. `--json` byte-parity asserted by a dedicated test that runs old-vs-new on the golden
  corpus (delete the old path at unit end).
- **Verification:** C11 test green; `sgt git log` / `sgt git checkout` manual smoke; help text
  reviewed once, whole-surface.
- **Status (2026-07-10, shipped `be8251a`):** as planned. `sgt/cli.py` became the `sgt/cli/`
  package; C11 byte-parity held. FINDINGS "U18 CLI restructure".

### U19. Sync decomposition (behavior-preserving) + explicit tree construction

- **Goal:** D4 stage 1. `sgt/core/sync/` package: `fetch.py, ingest.py, reconcile.py,
  resolve.py, materialize.py` with `sync()` as the composition; `-X ours` deleted in favor of
  explicit tree construction; **zero behavior change**.
- **Dependencies:** U16, U17.
- **Files:** `sgt/core/sync/` (from `sync.py`), `tests/core/test_sync.py` untouched and green,
  new stage-level unit tests.
- **Approach:** each stage a pure function over explicit dataclasses (no stage reads disk it
  wasn't handed). The existing 6 integration tests are the contract; they may not change in
  this unit. Two-clone tests additionally assert exact post-merge tree equality (the `-X ours`
  litmus).
- **Verification:** suite green with tests unmodified; diff review confirms no logic change
  rode along (the surgical-change discipline).
- **Status (2026-07-10, shipped `a6b19e9`):** as planned. The package is `fetch/ingest/resolve/
  materialize` (`reconcile.py` stayed under `sgt/lens/`, not the sync package — the one naming
  deviation from the Files list); `-X ours` deleted, explicit `.git/MERGE_HEAD` construction in.
  FINDINGS "U19 sync decomposition".

### U20. SYNC-1 hardening: mine-on-contact, divergence-as-state, ideal recovery, handshake, push

- **Goal:** C3–C7 — the five audit gaps, each now a small change to one stage.
- **Dependencies:** U19.
- **Files:** `ingest.py` (mine `merge_base..theirs` commits lacking trailers; miner-version
  handshake), `sgt/core/mine.py` (mine() gains a target-ref parameter so `merge_base..theirs`
  mines without checkout), `resolve.py` + `sgt/state.py` (`forks.json`; branch advances by
  fork-free part), `materialize.py`/`lens.py` (committed `.sgt/ideal.json` written in the
  *pre-commit* materialize path — `lens.put` and sync's materialize stage — so the blob at each
  witness SHA describes that SHA's ideal, not the previous one; the local table stays
  authoritative for the current ref; recovery path), `sgt/store/gitbind.py` (new non-forcing
  `push` primitive with rejection detection), `sgt/cli/sync.py` (`sgt push`, `sgt forks`),
  `sgt/api.py` (`forks_view`, extended `sync_view` — additive), `tests/core/test_sync_hardening.py`.
- **Approach:** one behavior per commit, in the order listed (mine-on-contact first — it
  unblocks the mixed-team workflow and is pure-additive; divergence-as-state last — it changes
  the sync contract and needs the loud-status work from D5). LAW-F flips green here. Contract
  note: sync's branch-ideal advance is *not* oracle-gated in this plan — LAW-G stays xfail until
  U23's `land` ships the gate, and LAW-G's scope is land-mediated advancement of the shared
  branch record.
- **Test scenarios:** plain-git teammate's commits union correctly and later self-dedupe when
  that teammate adopts sgt (LAW-0); fork lands fork-free part + durable fork record + loud
  status; squash-merge on GitHub then recovery from `ideal.json`; version-skew refusal message;
  push rejection routes to sync.
- **Verification:** workflow-matrix rows 3, 4, 5, 10 (design doc §7) each have a named test.
- **Status (2026-07-10, shipped `0a5e19d`→`89dbd99`, five commits):** as planned, one behavior per
  commit in the listed order. LAW-F flipped green; LAW-G stays xfail per the contract note. The
  fork-free ideal algebra (`order.fork_free`) landed here but proved to also be needed by the
  *local* mining path — see U22.5. FINDINGS "U20 SYNC-1 hardening".

### U21. Metadata semilattices: ACI pins, birth-minted feature ids + aliases, OR-Set declared

- **Goal:** C1; LAW-U green. The D6 decisions.
- **Dependencies:** U17 (schema versioning is what makes the migration expressible), U19/U20
  (reconcile stage is where the new rules live).
- **Files:** `sgt/lens/pins.py` (pin records gain introducing-witness anchor; `union_pins`
  becomes witness-topo + hash tie-break), `sgt/lens/tree.py` + `reconcile.py` (birth-minted ids,
  alias G-Set, atomic re-mint + pin-reference rewrite migration), `sgt/core/lens.py`/`order.py`
  (declared OR-Set + retraction verb `sgt after --retract`), `tests/laws/test_convergence.py`
  xfails flipped to asserts.
- **Approach:** pins first (smallest, flips half of LAW-U), then feature ids (the migration is
  the risky part — dry-run mode that prints the re-mint mapping before writing), then declared
  OR-Set. Alias-chain length gets a corpus measurement (the design doc's [BET]).
- **Verification:** LAW-U green across randomized schedules; migration idempotent (running it
  twice is a no-op); a pre-migration clone syncs cleanly with a post-migration clone (aliases
  doing their job); two clones with divergent *unsynced* curation migrate independently then
  sync — identical feature ids, or colliding birth ids resolve via the alias-merge rule (D6).
- **Status (2026-07-10, shipped `b197c73`→`a553d8f`, four commits) — GATE CLEARED with a plan
  correction:** LAW-U green across randomized schedules; migration idempotent; alias-merge collision
  test passes. **Correction to D6:** the design's "birth-minted on every build" caused confirmed
  silent data loss — an ordinary `sgt map`/`sgt sync` re-minted a legacy `F<n>` continuation and
  orphaned any pin referencing it, *without* `sgt migrate`. Shipped semantics (fix `a553d8f`):
  unreferenced legacy ids re-mint on any build (LAW-U needs this), but a pin-*referenced* legacy id
  migrates **only** through the explicit atomic `sgt migrate feature-ids --apply`; `build()` guards
  it with a `protected` set. Open Questions resolved: no witness backfill (hash tie-break for pre-U21
  pins); alias chains single-hop on this corpus. FINDINGS "U21 metadata semilattices".

### U22.5. Fork-free local mining (unplanned — surfaced by the self-hosting rule)

- **Goal:** `sgt state`/`sgt get`/`lens._sync` must produce a valid ideal from *any* real
  single-clone history, not just merge-free corpora. Found by running the plan's own per-unit
  self-hosting check (`sgt fsck` + `sgt state` on this repo), which had never actually been
  exercised — the repo's `.sgt/` store was empty until now, making that rule vacuous.
- **Root cause:** the local mining path built `Ideal.from_ops` on the raw provenance union with no
  reduction. Ordinary linear history forks it — a symbol added/deleted/re-added rebirths
  `(symbol, None)` twice — and squashed-away predecessors leave ungrounded ops; U20's
  `order.fork_free` covered only the sync path, and the read-side siblings lacked grounding too.
- **Files:** `sgt/core/order.py` (`reduce_to_ideal` = `fork_free(_grounded(...))`), `sgt/core/
  lens.py` (`_sync` reduces *before* persisting the ideal table — it previously wrote an invalid
  table then raised — plus `_committed_ids_by_provenance`/`ideal_for_ref`),
  `tests/core/test_lens.py`.
- **Status (2026-07-11, shipped this pass):** `sgt state` clean and `sgt fsck` genuinely checks
  7035 ops on this repo. Blocks nothing already shipped; unblocks U25 (measures real repos) and U28
  (init on real repos). ~20% closure reduction on this repo is a Known v1 limitation and *input* to
  U25's gate, not a regression. FINDINGS "U22.5 fork-free local mining".

### U22. Structured provenance + published claims

- **Goal:** D7 + D8. Provenance becomes a structured record on the reconcile path; oracle
  verdicts become publishable claims.
- **Dependencies:** U17, U19 (claims must travel — the materialize stage unions committed claim
  files generically, which the post-U19 explicit tree construction does not do by accident);
  independent of U20–U21 (can parallelize with those).
- **Files:** `sgt/core/op.py` (dual-shape provenance codec — id computation untouched),
  `sgt/core/store.py` (collision-union merges shapes), `sgt/loop/plan.py`+`match.py` (session/
  plan/agent fields populated at checkpoint/drift), `sgt/core/oracle.py` + `sgt/state.py`
  (committed `.sgt/claims/` — registered, alongside `.sgt/proposals/` for U24, as G-Set artifacts
  the sync materialize stage unions generically from theirs' tree; `sgt oracle publish`), `sgt/api.py` (provenance in blame/log
  views — additive).
- **Test scenarios:** v0-file + v1-file same-id union merges provenance shapes; drift ops carry
  session attribution; claim published by clone A is readable (and re-runnable) by clone B.
- **Verification:** no op id changes across the entire golden corpus (provenance exclusion
  proven end-to-end).
- **Status (2026-07-11, shipped `9960474`→`02b003a`, three commits):** as planned. Op id stability
  proven three ways (AST-identical `compute_id`; corpus round-trip; goldens additive-only —
  `attribution:[]`/`sessions:[]` added, every op id byte-identical). One design refinement worth
  noting: `Op.provenance` stays the bare-SHA tuple (all consumers untouched) with a *parallel*
  `Op.attribution` field, the two folded into the v1 `[{sha,…}]` shape only at serialize time — the
  surgical alternative to reshaping `provenance` itself. Session attribution is stamped by
  `confirm_match`/`stamp_drift` (not the pure `compute_checkpoint`). Claims are file-per-(ideal_key,
  runner) so the sync union is a trivial file-level G-Set. FINDINGS "U22 structured provenance".

### U23. SYNC-2 core: `sgt land` with CAS + the shared-store concurrency audit

- **Goal:** C9. Multiple sessions on one machine advance a shared branch safely.
- **Dependencies:** U20 (stages), U21 (LAW-U — concurrent landings exercise reconcile).
- **Files:** `sgt/core/sync/land.py` (land = ingest→resolve→materialize with a local source +
  CAS on the branch record), `sgt/core/store.py` (lock-scope audit: op appends stay lock-free
  temp+rename; the single-writer lock narrows to branch-record CAS and metadata writes —
  *measure* whether the current lock already permits this or serializes whole verbs),
  `sgt/cli/sync.py` (`sgt land`), `tests/core/test_land.py` (two concurrent landers via
  subprocess, both orderings).
- **Approach:** the store-lock audit is the unknown-unknown honeypot of this plan (R11's lock
  was designed single-process) — it runs *first* as a written finding in FINDINGS.md before the
  land verb is built on assumptions. fs-watch fork warnings are explicitly stretch: cut them
  before cutting anything else.
- **Verification:** concurrent-lander test: exactly one CAS winner, loser re-unions and lands
  or surfaces a genuine fork; no store corruption under `fsck` after 100 randomized rounds.
- **Status (2026-07-11, shipped `bc6f8ae`→`466c14e`, three commits):** as planned. The lock audit
  ran first (`bc6f8ae`) as an empirical FINDINGS finding: the `.sgt/lock` flock is *per-`add()`* and
  already correct for op appends, so it was **not** widened — the branch record is the git ref and
  `git update-ref <ref> <new> <old>` is the CAS (a correction to the Files note's "lock narrows to
  branch-record CAS"; the lock stays exactly as-is and the *ref* CAS is a separate, git-native
  mechanism). `land` reuses `ingest→resolve` + an extracted `materialize.persist_reconciled` then
  commits off-ref via `git commit-tree` + the ref CAS. **LAW-G flipped GREEN** — its test was
  *rewritten* (not unflipped) to exercise `land`, since sync stays deliberately ungated. Two
  deviations: (1) `sgt land` was already U11's staged-rewrite-commit verb, so the SYNC-2 verb is
  `sgt land <branch>` (positional disambiguation) — a possible follow-up rename; (2) the worktree
  concurrency model surfaced a real bug (a linked worktree's `.git` is a *file*, not a dir), fixed
  via `--absolute-git-dir`. fs-watch warnings cut as planned. FINDINGS "U23 the shared-store
  concurrency audit".

### U24. The proposal object + GitHub rendering (foundations)

- **Goal:** C10. The review object exists end-to-end: create → push ref → staleness check →
  land → render.
- **Dependencies:** U20 (re-union staleness), U21 (feature ids that travel), U22 (claims,
  provenance), U23 (`land`).
- **Files:** `sgt/core/propose.py` (proposal codec + validity: `base ∪ Δ` downward-closed),
  `sgt/state.py` (`.sgt/proposals/`), `sgt/cli/propose.py` (`propose create/status/land`,
  `propose render --github` emitting branch + PR body), `sgt/api.py` (`proposal_view` —
  feature delta, claim, provenance, fork check), `tests/core/test_propose.py`.
- **Approach:** native object first, GitHub rendering second (rendering is a pure projection of
  the view — no GitHub API dependency in this unit; `gh` invocation is porcelain for the
  follow-on plan). Partial acceptance (down-closed Δ′) ships; approvals/review-policy records
  ship as schema + storage only, enforcement UX deferred with the review surface.
- **Verification:** matrix rows 6, 7, 12: proposal from a fork-based contributor's branch;
  rendered PR body readable without sgt; claim with runner identity re-verified by reviewer
  clone.
- **Status (2026-07-11, shipped `6ba4319`→`e81f67c`, two commits):** as planned. `propose.create`
  gates base∪Δ to a valid ideal; `status` computes staleness by re-union (`current`/`clean-reunion`/
  `fork`) rather than storing it, so a proposal correctly goes stale when the base reworks a symbol Δ
  also touches; `land` refuses a stale-fork and otherwise delegates to the U23 CAS advance, with
  partial acceptance (a downward-closed Δ′ subset). `render_github` is a pure projection of
  `proposal_view` (feature-delta table + oracle claim with runner identity + provenance + staleness
  banner) — no `gh`/GitHub API. Proposals travel as a G-Set (`materialize._union_proposals`,
  mirroring claims). Approvals are schema+storage only. All three matrix rows tested. FINDINGS "U24
  the proposal object". **The foundations plan (U16–U24) is complete; every law in the harness is
  green (LAW-G flipped in U23), zero xfails remain.**

---

## Acceptance Examples

- AE7. Two clones apply contradicting pins and sync in both orders — both end byte-identical
  in `.sgt/`, the losing pin reported on both, twice (LAW-U, U21).
- AE8. A plain-git teammate commits to the shared branch; `sgt sync` unions their mined work;
  when they later run `sgt init`, zero duplicate ops mint (C3 + LAW-0, U20).
- AE9. Five ops sync cleanly while one symbol forks: branch advances by the clean five, `sgt
  status` shouts one open fork, `sgt merge-op` resolves it, and the resolution — synced to a
  third clone — closes the same fork there without interaction (C4 + LAW-R, U20).
- AE10. GitHub squash-merges an sgt branch; next `sgt sync` recovers the ideal from
  `.sgt/ideal.json` and identifies rather than re-mints (C5 + R8, U20).
- AE11. Two agent sessions `sgt land` concurrently; one wins CAS, the other re-unions and lands
  without human input; `fsck` is clean (C9, U23).
- AE12. `sgt propose render --github` produces a PR body whose feature-delta table and oracle
  claim a reviewer without sgt can act on; the sgt-side proposal goes stale when main moves,
  and `propose status` says exactly why (fork or clean re-union) (C10, U24).

---

## Risks, Pitfalls, Unknown Unknowns

- **Historical-schema blobs (D3).** The read path can never retire a format. Named, designed
  for, and the reason U17 precedes everything. Residual risk: a format we *think* is v0-stable
  but that older sgt versions wrote inconsistently — mitigated by building v0 fixtures from
  actual old checkouts of this repo's history, not from memory.
- **Store lock scope under concurrency (U23).** The single-writer design may already serialize
  everything or may already be unsafe for two sessions — unknown until audited. The audit is
  sequenced *before* `land` is built, and its finding goes to FINDINGS.md either way.
- **Divergence-as-state UX backlash (D5).** "Synced but my change isn't in the tree" reads as
  data loss if the fork isn't loud. The loudness requirement is stated as part of C4, not left
  to surface polish.
- **Feature-id migration is the one genuinely destructive step (U21).** Mitigations: dry-run
  printing the full re-mint map, idempotence test, alias table making old ids resolvable
  forever, and cross-vintage clone sync in the test scenarios. If it still goes wrong in a real
  repo, `.sgt/` is committed — `git revert` recovers.
- **Golden churn fatigue (U18).** The CLI migration re-approves goldens per verb family; if
  reviewers rubber-stamp, drift slips in. The `--json` byte-parity test (C11) is the non-human
  backstop for the surface that actually matters.
- **Parallelism temptation.** U22 is parallel-safe; U19→U20→U21 is a strict sequence (each
  builds on the previous unit's structure). Running U21 concurrently with U19/U20 *will*
  conflict in `reconcile.py` — don't.
- **Self-hosting during the refactor.** sgt mines this repo while this repo changes sgt's
  schemas. Rule: every unit's final commit runs `sgt fsck` + `sgt state` on the repo itself;
  any wobble is a FINDINGS entry before it's a fix.
- **Unknown unknowns budget.** Two are pre-registered (lock scope, v0 fixture fidelity). The
  convention from U8/U11/U15 continues: when a unit's implementation contradicts this plan, the
  plan doc gets a correction note in the unit's Status line — the plan is falsifiable, not
  aspirational.

---

## Open Questions

- Does `test_decision_layout.py` / `test_color_parity.py` still guard anything live? (Checked
  at U16, deleted only with evidence.)
- Witness-topo tie-break needs the introducing commit per pin — is backfilling that for
  existing pins worth it, or do pre-U21 pins tie-break by hash only? (Decide in U21 from how
  many contested pins the corpus actually has; likely near zero.)
- Alias-chain length in practice (design-doc [BET]) — measured in U21; if long, feature
  references fall back to op-sets.
- Should `sgt land` exist for the single-user case as sugar over put+record (uniformity) or
  only appear with multi-session state? (Decide in U23 from dogfood feel.)
- `propose render` output format for GitLab/other forges — out of scope; the renderer takes a
  template seam from day one so it's a follow-on, not a redesign.

---

## Sources & Research

- The two origin design docs (2026-07-10), including the U15 audit findings this plan turns
  into units, the workflow matrix (§7) that acceptance examples trace to, and the laws (§8)
  that become `tests/laws/test_convergence.py`.
- Kernel plan U1–U15 status lines and FINDINGS.md entries — the house conventions this plan
  continues: harness-first-and-red, goldens gate deletions, plan corrections recorded in-place,
  single `sgt.api` projection, additive-only machine schemas (R21).
- Code audited for the current-state table: `sgt/cli.py` (`_strip_opt`), `sgt/core/sync.py`
  (`:118`, `-X ours`), `sgt/lens/pins.py` (`:61`), `sgt/core/op.py` (provenance shape,
  `MINER_VERSION` in `compute_id`), `sgt/core/oracle.py` (`ideal_key`), `sgt/core/mine.py`
  (deterministic version hashing), test-tree remnants of deleted subsystems.
