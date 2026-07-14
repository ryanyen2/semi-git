---
title: "feat: the lived surface — porcelain, selection, tiers, onboarding, sessions, provenance, review"
type: feat
status: proposed
date: 2026-07-10
origin:
  - docs/design/2026-07-10-sgt-as-version-control.md
  - docs/design/2026-07-10-collaboration-and-review.md
continues: docs/plans/2026-07-10-001-refactor-collab-foundations-plan.md   # U16–U24; units here continue at U25
---

# feat: the lived surface

## Summary

The foundations plan (U16–U24) restructures the seams and lands the collaboration substrate:
hardened sync, semilattice metadata, `land`, the proposal object. This plan is everything the two
design docs describe that a *user actually touches* and that was deliberately scoped out of the
foundations (its D9): the full porcelain, branch-as-selection at the feature layer, the
three-tier file boundary, the onboarding on-ramp, the multi-agent session layer, provenance and
trust surfaces, and the review surface over proposals. The organizing rule carries over — every
unit is a surface, a routing rule, or excluded-from-id metadata; nothing here can touch a kernel
object. The plan's distinctive posture: **its flagship UX (selection) is gated on a measurement
unit that runs first**, because the design doc says plainly that if feature-layer closures are
not human-scale, shipping the selection UI would ship a lie.

---

## Problem Frame

With U16–U24 done, sgt is *correct* in company but still a sidecar in feel: the user's daily
loop still speaks git, real repos still trip over non-entity files, `sgt init` on a large repo
is an unknown quantity, an agent session has no named shape, provenance is structured but
invisible, and a proposal is an object with no place to be reviewed. Each of these is called out
in the design docs as either the adoption path or the differentiation. What none of them need is
new algebra — the risk profile of this plan is UX judgment and measured bets, not kernel
correctness. The two failure modes to design against: (1) shipping surfaces whose underlying bet
is unmeasured (selection over an entangled clustering); (2) porcelain that fights the user's
existing git muscle memory instead of absorbing it.

---

## Key Decision Points

### D1. Measure before the flagship: selection UX is gated, and the gate runs first

- **Alternatives:** (a) build `sgt select` now, measure later — fastest demo, but if closures
  pull in half the repo the flagship UX demonstrates the product's weakness; (b) improve
  clustering (BET-C, 63.9% MoJoFM) first — open-ended research blocking all UX; (c) a cheap
  measurement unit (U25) that answers only the load-bearing question — "what fraction of
  feature-node selections induce a closure a human can read?" — and gates U29 on the answer.
- **Decision:** (c). The measurement is a day of harness work, run where the bet can actually
  fail: sgt's own repo op store (the BET-C corpus) and the ~5k-commit real probe repo U25
  already mines for BET-E — the synthetic law/golden fixtures are demoted to harness sanity
  checks (a handful of hand-built commits greens any threshold by construction). If it fails,
  the *finding* routes work to clustering or to closure-explanation UX — a better outcome than
  a bad flagship.
- **Pitfall pre-registered:** defining "human-scale" post hoc to pass the gate. The threshold is
  fixed now: median closure ≤ 25 ops and ≤ 3 features dragged in, *and* at least 80% of
  feature-node selections within those same bounds (the question is a fraction — a median bound
  alone greens the gate with 40% of selections unreadable), on both real corpora, or the gate
  is red.

### D2. Porcelain interception: refuse-with-remedy inside `sgt git`, never a PATH shim

- **Alternatives:** (a) advisory warning only (U18's posture) forever; (b) `sgt git` refuses
  tree-mutating subcommands (`checkout`, `switch`, `restore`, `pull`, `reset --hard`, `merge`,
  `rebase`, `revert`, `stash pop`, `stash apply`, `cherry-pick`, `am`, `apply`) with the named
  sgt verb (`pull` → `sgt sync`; refusing `checkout` while passing its replacements
  `switch`/`restore` would be incoherent), `--force` to override; (c) intercept *bare* `git` via
  PATH shim/alias so even non-`sgt git` invocations are caught.
- **Tradeoff:** (a) never establishes sgt as the write path — drift stays common and the
  out-of-band detector fires constantly; (c) is the unknown-unknown factory (breaks scripts,
  IDEs, hooks, CI runners invoking git; debugging becomes "which git am I running") and the
  design doc already rejected it; (b) captures the user who has adopted the `sgt` prefix while
  leaving raw `git` untouched — consistent with "escape hatch is explicit and total."
- **Decision:** (b), with the existing `gitbind` out-of-band detector remaining the net for raw
  git (mine-on-contact, never corrupt). The routing table from the design doc §1 becomes a data
  table in one module, not scattered conditionals.

### D3. The daily-loop verbs are few and named now — not a git-command-for-command clone

- The porcelain ships exactly: `sgt switch <branch|selection>` (materialize a named ideal),
  `sgt save [-m]` (get + witness commit; the put-path sugar), `sgt undo` (revert last ideal
  edit — set arithmetic makes this exact), and the already-shipped inspect verbs. Everything
  else stays `sgt git …`.
- **Tradeoff:** users will ask for `stash`/`bisect`/`tag` equivalents. Saying no keeps the verb
  surface teachable and the passthrough honest; a wrapped verb is only justified when the
  sgt-native version is *semantically different*, not merely renamed (design doc §1 non-goal).
  `stash` in particular dissolves: a dirty tree is just ops not yet landed — `sgt save` onto a
  scratch selection covers it; document the pattern instead of cloning the verb.

### D4. Tier boundary: configuration observed, never guessed silently

- **Alternatives for the entity/opaque/ignored map:** (a) hardcoded extension list; (b)
  `.sgtignore` + a visible, versioned tier map in `.sgt/tiers.json` with empirically-chosen
  defaults, surfaced by `sgt state` (which already reports coverage); (c) per-file auto-detect
  at mine time only (status quo — implicit).
- **Decision:** (b). The map is data, project-overridable, and *diffable in review* — a file
  moving tiers is a visible event. Defaults come from measuring 2–3 real repos in U27, not from
  intuition (design doc [UNKNOWN] honored).
- **Pitfall:** tier changes rewrite mining behavior for a path — a tier-2→tier-1 flip must not
  re-mint history (the identification law protects; test it explicitly), and tier→ignored must
  refuse if the path has in-ideal ops (data loss shape) — route to revert first. The third
  transition, entity→opaque demotion, freezes existing symbol chains at their tips; subsequent
  edits version as whole-file ops — never two live representations of the same bytes.
- **Determinism guard:** mining resolves a path's tier from the `tiers.json`/`.sgtignore`
  committed in the *mined commit's own tree* (built-in defaults when absent), never the current
  working map — tier assignment stays a pure function of the commit, so LAW-0 replica
  determinism survives divergent working tier maps and re-mines of pre-tier-change history.

### D5. Sessions are a thin named wrapper, not a daemon

- **Alternatives:** (a) no session object — agents keep using harness worktrees and `land`
  (foundations already suffice); (b) `sgt session` verbs: `start` (ephemeral materialization of
  a base ideal into a scratch tree), `status`, `land` (distill + U23 land), with session id
  flowing into structured provenance; (c) a session *daemon* managing agents, watching, locking.
- **Tradeoff:** (a) leaves the design docs' "ephemeral materialization is a session
  implementation detail" as everyone's private duct tape and provenance's `session` field
  unpopulated by anything official; (c) is infrastructure with a lifecycle, crash-recovery, and
  platform surface this product does not need yet.
- **Decision:** (b). The fs-watch early-fork warning (rescued from U23's cut list) lives here as
  `sgt session status --watch` — polling fs-events only while a session asks, no daemon.
- **Pitfall:** scratch trees leak (crashed agents). Sessions record their scratch path, owning
  pid, and start time in `.sgt/local/sessions.json`; `sgt session gc` reaps only sessions whose
  recorded pid is no longer alive (`--force` to override — age alone cannot distinguish a crash
  from a long-running agent mid-edit, and a naive age reap destroys live undistilled work);
  `fsck` reports them.

### D6. One review surface first: the VS Code rail, fed by `sgt.api`, with the TUI reading the same views

- **Alternatives:** rail-first, TUI-first, or a web artifact.
- **Decision:** rail-first — it was just redesigned (`fc3b2d4`) and is where provenance, feature
  delta, and partial acceptance can be *manipulated*, not just printed. The TUI gets read-only
  proposal/fork/trust views from the identical `sgt.api` projections (R21 keeps them in
  lockstep); a web surface is out of scope entirely.
- **Navigation:** the rail is *one* webview panel with a persistent view switcher (Map /
  Selection / Provenance & Trust / Proposal Review), every view reading the same pushed
  `sgt.api` state — U29/U31/U32 add views to the switcher, never their own panel registrations.
- **Pitfall:** review *verbs* implemented in the webview instead of the API — every mutation
  goes through a CLI/MCP verb the rail calls, or the surface forks from the product (the U13
  lesson: surfaces read projections, they don't own logic).

### D7. GitHub publish is `gh`-CLI porcelain; claims render into the PR body, comments do not round-trip

- U24 renders the PR body; this plan invokes `gh pr create/edit` and keeps the body in sync on
  proposal update. The oracle claim renders *into the body* (not as a GitHub status check —
  impersonating CI invites trust confusion with actual CI; a claim is attributed, CI is
  authoritative).
- Review-comment round-trip stays out (design doc's explicit scope cut) — the PR body footer
  carries the proposal id so a future unit can close the loop.

### D8. Onboarding is a product unit with a stopwatch, not a docs page

- The genesis-horizon wall is the named adoption blocker. `sgt init --horizon` exists
  mechanically (kernel-plan R10: `init` accepts `--horizon <ref>` and routes it to `lens.init()`,
  fixed in commit `4cc7b88`); what doesn't exist is the *experience*: progress reporting on a large
  mine, a first-run summary ("N features found, coverage M%, here's your map"), and honest
  guidance when horizon choice matters. The acceptance bar is pre-registered (D1's own
  discipline, applied here too): repo of ~5k commits to first useful `sgt map` in under 10
  minutes — the number this unit's title already commits to. The BET-E probe in U25 is a
  go/no-go on whether a scoped performance unit must precede U28, not the bar-setter
  (measure-then-set-N would make the bar unfalsifiable).
- **Rejected-alternatives provenance capture** (design doc's "highest-value provenance we don't
  have") stays an open experiment — it needs agent-transcript access whose shape (hooks? MCP?)
  is unsettled; pre-committing a mechanism now would be guessing.

---

## Requirements

- S1. A user can complete the daily loop — switch, edit, save, undo, inspect, sync, push —
  without typing raw `git`; `sgt git` passes everything else through, refusing tree-mutating
  subcommands with the named native remedy and honoring `--force`.
- S2. Selection: `sgt select <feature...>` produces a named ideal from feature nodes; the
  induced closure is reported with true-coupling chains distinguished from clustering
  co-membership; `sgt why <op>` traces any included op back to the requesting feature through
  `requires`. Ships only if U25's gate is green.
- S3. Over-coupled closures produce a diagnosis (which edge/feature causes the drag) and route
  to `feature split` / `identity split` — never a silent giant checkout.
- S4. Every path's tier (entity / opaque / ignored) is visible, project-configurable
  (`.sgtignore`, `.sgt/tiers.json`), and tier changes are safe: no re-minting on tier
  promotion, refusal-with-remedy on ignoring tracked paths. Derived files carry a `derived`
  flag that review surfaces collapse.
- S5. `sgt init` on a ~5k-commit repo reaches a useful `sgt map` within the U25-measured
  budget, reporting progress and a first-run summary.
- S6. `sgt session start/status/land/gc` wraps ephemeral materialization; session identity
  flows into structured provenance (U22 fields); `session status --watch` warns when another
  writer advances a symbol the session holds drafts on.
- S7. Blame, log, map, and the rail render structured provenance (session, agent, plan,
  drift); the user can act on it: re-point ops to features, revert by intent/session, and see
  a trust queue of unreviewed work grouped by provenance.
- S8. A proposal is reviewable end-to-end in the rail: feature delta, oracle claim, provenance,
  staleness/fork state, partial acceptance (down-closed Δ′) — with every mutation going
  through API-backed verbs. `sgt propose publish` creates/updates the GitHub PR via `gh`.
- S9. All new machine surfaces are additive `sgt.api` views (R21); TUI and rail read identical
  projections.

---

## Implementation Units

### U25. The measurement unit: closure-scale gate + onboarding probe

- **Goal:** answer the two questions this plan's biggest units hang on. Gate for U29 (D1
  threshold fixed: median ≤ 25 ops, ≤ 3 dragged features, ≥ 80% of selections within bounds);
  go/no-go probe for U28 (BET-E: wall-clock + peak memory for `init --horizon` on the
  ~5k-commit probe repo, against D8's pre-registered 10-minute budget).
- **Dependencies:** foundations U16 (corpus harness). Independent of U17–U24 — can run first.
- **Files:** `tests/laws/test_closure_scale.py` (or `experiments/closure_scale/` if it stays a
  probe), FINDINGS.md entry with the numbers and the go/no-go.
- **Approach:** for every feature node in the two real corpora's trees (sgt's own op store; the
  probe repo), compute `↓ops(F)` and report distribution of closure size, dragged-feature count,
  and the true-vs-incidental split. The probe repo is a specific public repository pinned to a
  SHA (~5k commits), supplied via an `SGT_PROBE_REPO` env var (the `SGT_LARGE_CORPUS_REPO`
  opt-in convention) and named in the FINDINGS entry — the acceptance bar is unfalsifiable
  without a named repo. Nothing ships to users; the deliverable is the FINDINGS entry.
- **Verification:** numbers in FINDINGS; gate decision recorded in this plan's U29 status line.

### U26. Porcelain completion: routing table + daily-loop verbs

- **Goal:** S1. The D2 refusal table and the D3 verb set (`switch`, `save`, `undo`), semantic
  inspect verbs confirmed as defaults.
- **Dependencies:** foundations U18 (CLI package, passthrough), U20 (`sgt push` exists).
- **Files:** `sgt/cli/porcelain.py` (routing table as data + the three verbs), `sgt/api.py`
  (additive views where needed), goldens, `tests/test_porcelain.py`.
- **Approach:** `switch` materializes a named ideal via existing lens machinery; `save` is
  get + witness commit with trailer discipline; `undo` inverts the last recorded ideal edit,
  read from a new ideal-edit journal (append {previous ideal, witness sha} to a `.sgt/local/`
  log before each `record_ideal` overwrite) — an explicit U26 deliverable: today `record_ideal`
  keeps only the latest per-ref entry, so there is no existing history to invert. No new kernel
  calls — if a verb needs one, it's mis-scoped (stop and re-plan).
- **Test scenarios:** `sgt git checkout` refuses naming `sgt switch`, `--force` passes through
  and the out-of-band detector mines on next contact; the full daily loop runs git-free.

### U27. The three-tier file boundary

- **Goal:** S4. `.sgtignore`, visible tier map, `derived` flag, safe tier transitions.
- **Dependencies:** foundations U17 (state module owns the new artifacts). Parallel-safe with
  U26.
- **Files:** `sgt/state.py` (tiers.json, sgtignore codec), `sgt/core/mine.py` (consult tiers;
  today's implicit fallback becomes the explicit tier-2 path), `sgt/cli/` (`sgt tiers`,
  extended `sgt state`), `tests/core/test_tiers.py`.
- **Approach:** defaults measured from 2–3 real repos first (the D4 [UNKNOWN]); tier
  transitions get the two named guards (no re-mint on promotion — identification law test;
  refuse-with-remedy on ignoring live paths). `derived` rides the metadata slot, excluded from
  ids.
- **Test scenarios:** lockfile edits version as whole-file ops and collapse under the flag; a
  promoted `.yaml→entity-grammar-later` path keeps its chain history; ignoring a live path
  refuses and names the revert; two replicas with divergent *working* tier maps mine the same
  history to byte-identical ops (LAW-0); a demoted (entity→opaque) path's post-demotion edit
  materializes correctly (frozen chains + whole-file op).

### U28. Onboarding: the first ten minutes

- **Goal:** S5. `init --horizon` becomes an on-ramp: progress, first-run summary, horizon
  guidance; budget pre-registered at 10 minutes (D8) — U25's probe decides whether a
  performance unit must precede this one.
- **Dependencies:** U25 (budget), U27 (tier map — a real repo's init is mostly tier-2/3
  traffic).
- **Files:** `sgt/cli/init.py`, `sgt/core/mine.py` (progress callbacks — no logic change),
  `docs/guide/` quickstart rewrite, `tests/test_init_onramp.py`.
- **Approach:** measure-first (U25 probe tells us where time goes before we optimize
  anything); if the budget misses, the fix unit is scoped *then* — this unit does UX, not
  performance work it hasn't measured. The progress UI includes an interrupted/failed state:
  on Ctrl-C or a mining error, report how far the mine got and whether re-running
  `sgt init --horizon` resumes or restarts — a first-run user mid-onboarding is never left
  guessing whether re-running is safe.
- **Verification:** stopwatch acceptance on the probe repo, in CI-less reality: a scripted
  run in FINDINGS with timings.

### U29. Branch-as-selection at the feature layer — GATED on U25

- **Goal:** S2, S3. The flagship: `sgt select`, closure explanation, `sgt why`, over-coupling
  diagnosis.
- **Dependencies:** U25 green gate; foundations U21 (feature ids that travel — selections
  reference them); U26 (`switch` is how a selection materializes).
- **Files:** `sgt/lens/select.py` (feature-set → closure with per-op provenance of *why*:
  which `requires` chain), `sgt/cli/select.py` (`select`, `why`), `sgt/api.py`
  (`selection_view` with true/incidental split), rail selection pane, `tests/lens/test_select.py`.
- **Approach:** closure follows `requires` only — never clustering co-membership (the design
  doc's hard rule); the explanation is computed *during* closure (record the edge that pulled
  each op in) rather than re-derived after. Diagnosis = the cut vertex/edge whose removal
  shrinks the closure most, reported with its `feature split`/`identity split` remedy.
- **Test scenarios:** selection with a known cross-feature `requires` chain reports exactly
  that chain; two co-clustered but reference-independent features select independently; a
  synthetic hub symbol produces the diagnosis, not a giant silent closure.
- **Status note:** if U25's gate is red, this unit converts to a clustering/`requires`-quality
  unit — the plan is falsifiable here by design.
- **U25 GATE RESULT (2026-07-11): RED (BET-C) → this unit reroutes.** The gate is red on sgt's own
  op store: median closure 34 ops (> 25), only 46% of feature nodes within bounds (< 80%). The
  failure is *feature size*, not entanglement — dragged features ≈ 0, so `requires`-based closure is
  already clean; the size comes from the residue-heavy op representation (of 5861 ideal ops, only 287
  are code entities; the rest are residue/anchor/whole-file) plus docs/residue clustering into large
  nodes (worst: a 990-op / 146-file `docs/brainstorms` cluster). Re-scoping to code-entity features
  passes but on a degenerate 8-op sample, and a file-count lens (median 6 files, 146-file tail) does
  not rescue it either — the RED is robust. **Disposition:** `sgt select` does NOT ship as silent
  branch-as-selection. It ships as **closure-explanation UX** — `sgt select`/`sgt why` show the
  closure (files + op count) and the exact `requires`-chain that pulled each op in, and the
  over-coupling diagnosis names the hub — for the human to confirm, never a silent giant closure.
  The deferred path to a future green gate is clustering quality (docs/residue must not form large
  selectable nodes). See FINDINGS "U25 the closure-scale gate". BET-E remains unmeasured (no
  `SGT_PROBE_REPO`), which also blocks U28's onboarding probe.

### U30. The session layer

- **Goal:** S6. Named sessions, scratch-tree lifecycle, provenance flow, early fork warning.
- **Dependencies:** foundations U22 (provenance fields), U23 (`land`).
- **Files:** `sgt/core/session.py`, `sgt/cli/session.py`, `sgt/state.py` (local
  sessions.json), `tests/core/test_session.py` (incl. gc of a killed session's scratch).
- **Approach:** D5's thin-wrapper decision. Watch mode is a poll/fs-event loop that lives only
  as long as `--watch` runs — no daemon, no background process to leak. The same fs-event also
  reaches the rail: the extension (already watching `.sgt/**/*.json` for state refresh) surfaces
  the early-fork warning as a VS Code notification/status-bar item, so the differentiator fires
  in D6's primary surface, not only for whoever remembered to run `--watch` in a spare terminal.
  MCP exposes session start/land so Claude-Code agents get it natively.
- **Test scenarios:** two sessions on overlapping footprints — the watcher warns before land;
  provenance of landed ops names the session; gc reaps a crashed session's scratch and `fsck`
  is clean.

### U31. Provenance and trust surfaces

- **Goal:** S7. Render what U22 structured; make it actionable; the trust queue.
- **Dependencies:** foundations U22; U30 (sessions populate the interesting provenance);
  parallel-safe with U29.
- **Files:** `sgt/api.py` (provenance in `blame_view`/`log_view`/`map_view` — additive;
  `trust_view`: unreviewed ops grouped by provenance), `sgt/cli/` (`blame`/`log` rendering,
  `sgt review-queue`), rail provenance + trust panes, TUI read-only equivalents,
  `tests/test_trust_view.py`.
- **Approach:** "act on it" verbs already exist (`feature move`, `revert`) — this unit adds
  *addressing by provenance* (e.g., `sgt revert --session <id>` resolves to an op-set and
  routes through the existing exact verb with preview). One deliberate exception: "reviewed"
  means an op is covered by an op-set-scoped review record (the U24 review-record shape), and
  this unit ships one API-backed verb — `sgt review-queue ack <op-set|--session id>` — that
  writes such a record; `trust_view` dequeues ops covered by any review record (retag and
  revert alone would be the wrong exit semantics — organization and rejection, not trust). No
  other new mutation semantics.
- **Test scenarios:** revert-by-session previews exactly the session's ops; drift ops appear
  in the trust queue until reviewed/retagged; rail and TUI render identical view JSON.

### U32. The review surface + GitHub publish

- **Goal:** S8. Proposals become reviewable and publishable.
- **Dependencies:** foundations U24 (proposal object, render — the feature delta renders from
  U24's `proposal_view` vocabulary); U31 (trust/provenance panes reused in review); U29
  *optional* (selection cross-links — `sgt why` traces, selection-pane links — wired only if
  U25's gate is green).
- **Files:** rail proposal review (feature delta, claim, provenance, staleness, partial-accept
  flow calling `propose land --subset`), `sgt/cli/propose.py` (`publish` via `gh`, body kept
  in sync on update), `sgt/api.py` (`proposal_review_view` — additive), integration test
  behind a `gh`-available guard.
- **Approach:** D6/D7 decisions. Partial acceptance UI drives the U24 down-closed-subset
  machinery — the rail computes nothing itself. Concretely: the proposal pane renders the
  feature delta as a checked list (checked = land, unchecked = hold); un-checking a feature
  that a still-checked feature `requires` is disabled — grayed, naming the requiring feature
  (mirroring U29's closure explanation) — and the rail then calls `propose land --subset` with
  the checked set. Approvals enforcement turns on the U24-stored
  policy (repo config), enforced in `land`, surfaced in the rail.
- **Test scenarios:** partial accept of a two-feature proposal lands one feature exactly and
  the remainder survives as a valid proposal; a stale proposal shows fork-vs-clean re-union
  state; published PR body updates when the proposal does.

---

## Acceptance Examples

- AE13. A developer works a full feature — switch, edit, save, sync, propose, land — and
  `history | grep git` shows only `sgt` invocations (S1, U26).
- AE14. `sgt select payments` on the corpus repo reports "31 ops; also pulled 4 ops from
  `auth` because `charge` requires `verify_token`" and `sgt why <op>` prints that chain; the
  same repo's two co-clustered-but-independent features select without dragging each other
  (S2, U29 — only if U25 green).
- AE15. `sgt init --horizon` on the ~5k-commit probe repo reaches a browsable map within the
  U25-fixed budget, ending with the first-run summary (S5, U28).
- AE16. An agent session lands work; `sgt blame` on the touched symbol names the session and
  plan; `sgt revert --session <id>` previews exactly that work (S6+S7, U30/U31).
- AE17. A reviewer partially accepts a proposal from the rail; the accepted half lands
  oracle-green; the remainder remains a valid, non-stale proposal; the GitHub PR body reflects
  both events (S8, U32).
- AE18. Ignoring a path with live ops refuses and names the revert; a lockfile's 4000-line
  regeneration renders collapsed in the proposal view (S4, U27).

---

## Risks, Pitfalls, Unknown Unknowns

- **The gate could be red (U25).** Pre-committed response: U29 converts to substrate-quality
  work; U26–U28 and U30–U31 are independent of the gate, and U32 degrades rather than blocks
  (feature delta from U24's vocabulary, selection cross-links dropped) — the plan does not
  stall on its flagship.
- **Porcelain muscle-memory backlash.** Users typing `git checkout` in the wrong terminal hit
  raw git — by design (D2), and the out-of-band net absorbs it. The risk is users typing
  `sgt git checkout` and resenting the refusal: the refusal message must show the exact
  equivalent command, copy-pasteable, or the porcelain reads as a nanny.
- **Undo semantics (U26).** "Undo the last ideal edit" is exact, but users will expect it to
  also undo *file edits* not yet distilled. Scope `undo` to recorded ideal edits and say so in
  its help text — fuzzy undo is how trust dies.
- **Watch-mode platform variance (U30).** fs-events differ across macOS/Linux; the watcher is
  advisory-only (a missed warning costs nothing correctness-wise — the fork still surfaces at
  land). Stated so a flaky watcher never becomes a correctness bug report.
- **Rail scope creep (U32).** The webview will tempt logic into TypeScript. The D6 rule (every
  mutation through an API-backed verb) is enforced by review; the test that rail and TUI render
  identical view JSON is the tripwire.
- **`gh` as a dependency boundary (U32).** Publish degrades gracefully when `gh` is absent
  (print the rendered body + instructions); never a hard dependency.
- **Sequencing traps.** U25 first, always. U26/U27 parallel-safe; U28 needs both U25 and U27.
  U29 needs U21 from the foundations plan — do not start it against replica-local feature ids
  or selections won't survive sync.

---

## Open Questions

- Where does the human-scale threshold really sit? 25 ops is a defensible guess fixed to keep
  us honest (D1) — U25's distribution may argue for a different constant *before* U29 starts,
  and that renegotiation happens in the plan doc, in writing.
- Does `sgt save` auto-run the oracle (tier-1 parse check is free) or stay silent-fast?
  Decide from dogfood friction in U26.
- Rejected-alternatives provenance capture (D8): transcript-derived vs agent-declared —
  parked until the hooks/MCP shape settles; revisit when U30's session layer shows what
  transcript access sessions actually have.
- TUI parity depth: read-only mirrors of rail panes, or does the TUI eventually get review
  verbs too? Decide after U32 dogfooding — not before there's something to dogfood.
- SYNC-3 (live relay), claim signing, review-comment round-trip: inherited deferrals from the
  design docs; nothing in this plan forecloses them (sessions poll, claims carry a signature
  slot, PR bodies carry the proposal id).

---

## Sources & Research

- The two 2026-07-10 design docs — this plan implements their §§ that the foundations plan's
  D9 scoped out: VCS doc §1 (porcelain routing), §2 (selection + closure explanation), §3
  (session shape), §5 (tiers), §6 (provenance rendering priority), §7 (onboarding/trust
  blind spots); collab doc §5.2 (early fork warning), §6.2–6.5 (review surface, GitHub
  interop, publish).
- Foundations plan U16–U24 — dependency anchors (state module, CLI package, land, proposal
  object, structured provenance) and the falsifiable-plan convention (status-line corrections)
  continued here.
- Measurement lineage: BET-C (63.9% MoJoFM baseline) and BET-E from the kernel plan's R22 —
  U25 is their product-facing re-run with pre-registered thresholds.
