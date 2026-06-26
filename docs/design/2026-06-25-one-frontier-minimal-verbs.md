---
date: 2026-06-25
topic: one in-force axis (the frontier), a minimal recompose surface (revert/restore), one ref resolver
status: design / ADR
supersedes-decisions:
  - graph-only-pivot ("switch off suspends via NodeStatus.SUSPENDED" — suspension is now a frontier state, not a node status)
  - decision-dag ("compose pins a lane" — folded into restore)
origin: this conversation (2026-06-25); builds on docs/design/2026-06-19-graph-only-agent-driven-sgt.md and the decision-DAG pivot
---

# One frontier, minimal verbs

## The problem this fixes

sgt grew two parallel ways to say "what code is in force," from two different eras:

1. **Node status** (older, `graph-only` era). `materialize()` replays effects of nodes whose
   `NodeStatus is ACTIVE`. `revert` *deletes* nodes from the graph and tombstones their log
   entries; `switch off` flips a node to `SUSPENDED` so it's skipped. Addressing is by **node id**.
2. **The frontier** (newer, `decision-DAG` era). `.sgt/frontier.json` selects one in-force
   decision per lane; `materialize()` *also* has a second path (`materialize_frontier`) that
   composes from it; `compose` pins a lane, `tag` names a selection, `diff` compares two.
   Addressing is by **lane** and **decision id** (`node@landing`).

These overlap and can disagree. "Take feature X out of my tree" has three spellings — `revert`
(destructive), `switch off` (status), and an implicit frontier deselection — that live in different
modules, address state differently, and are gated separately. That is the incohesion. It is not
fixable by adding a fourth verb; it is fixable by deleting an axis.

## The one rule of state

> **The frontier is the only thing that decides what is in force.** The working tree is always the
> materialization of the frontier. Everything else — node status, the log — is upstream of it.

Concretely:

- `Frontier.selection: dict[lane, decision_id | OFF]`. A lane maps to the decision that is in
  force, or the `OFF` sentinel (out of force entirely). The default for a lane with no explicit
  entry is its **tip** (latest landed decision).
- `materialize()` **always** composes from the frontier. There is no second path. With every lane
  at its tip (the default), this is byte-identical to today's "replay all ACTIVE effects" — so the
  change is transparent until you actually recompose.
- `NodeStatus` loses `SUSPENDED`. A suspended feature is just a lane whose frontier value is `OFF`.
  Status now means only: `ACTIVE` (has landed effects, in the log), `PLANNED` (no effects yet),
  `QUARANTINED` (held effects that don't commute). In-force-ness is *not* a node property.
- `revert` becomes **lossless**: it sets lanes `OFF` in the frontier; it does not delete nodes or
  tombstone the log. This is strictly better — it makes revert/restore symmetric, lets you bring a
  feature back, and fixes a real merge wart (today's destructive revert tombstones *shared* log
  entries, which then can't ship to peers).

## The minimal verb set

Three groups. Every verb addresses state through the frontier or the log; none invents a fourth way.

**Build (creates decisions):**
- `sgt plan <intent>` — propose decisions as `PLANNED` nodes (no code). (Plan-editing verbs are a
  later phase; out of scope here.)
- `sgt checkpoint [--intent …] [--fulfills <ref>]` — distill the agent's on-disk edits into
  effects, gate, land an `ACTIVE` decision. **`sync` is folded in** (it was checkpoint-without-intent).

**Recompose (edits the frontier — the whole point):**
- `sgt revert <ref> [--dry-run]` — set the lane(s) `OFF`, and with them every lane that builds on
  them (downward closure). Reversible.
- `sgt restore <ref> [--dry-run]` — set a lane's in-force decision (its tip = "on"; an older
  decision = a pin, i.e. compose feature-A@v3 beside feature-B@latest), pulling in the lanes it
  builds on (upward closure) so the result resolves. **Absorbs `switch on` and `compose`.**

Both keep HEAD valid by construction (closure), with the invariant gate as the backstop; both
re-materialize the tree (the `git checkout` analog). `--dry-run` previews the delta and writes
nothing (absorbs the `emit` verb; the `emit_payload` API stays for UIs).

**Read (offline, one projection in `sgt.api`):**
- `sgt log` / `sgt graph` — the decision DAG over time (`decision_graph_view`).
- `sgt show <ref>` — one decision in full.
- `sgt blame <file>` — line → owning decision.
- `sgt status` — HEAD composition + drift.
- `sgt diff <ref> <ref>` — composition delta (`frontier_diff`).
- `sgt tag <name>` — name the current composition (git-tag analog; cheap, kept).

**Recovery / infra:** `sgt reconcile [<ref>]` (re-gate held quarantines — the one quarantine-exit
path), `sgt init`, `sgt export`, `sgt tui`, `sgt mcp`, `sgt help`.

Removed as verbs: `switch` (→ revert/restore), `compose` (→ restore), `sync` (→ checkpoint),
`emit` (→ `--dry-run`), `decisions` (→ `log`/`graph`, which now show the decision DAG).

## One ref resolver

Every verb that takes a `<ref>` resolves it the same way, replacing the substring-only
`resolve_ref`. Resolution is by *kind of handle*, not a cascade of guesses:

- a **decision id** (`node@landing`) → that decision;
- a **node id** → its lane's decision;
- a **lane name** → the lane (its tip decision);
- an **entity key** (`file::name`, the `Decision.footprint` join key) → the lane that owns it;
- otherwise a **phrase** → ranked match over slug/intent/footprint; one hit resolves, several
  disambiguate, none reports missing.

Temporal (`@v3`, `before:auth`) and semantic/LLM phrase-matching are a later phase; the resolver is
written so they slot in without touching the verbs. The point now is that *one* function backs
revert/restore/show/blame/diff, so a ref means the same thing everywhere.

## What stays exactly as-is

The effect model, the confluence/invariant gate, the append-only log, dependency inference, the
distiller, `PLANNED` semantics, quarantine-as-status, the `sgt.api` projection shape, the merge
engine's log-primary export. This ADR re-routes *which state decides materialization* and *how many
verbs touch it* — it does not change how effects are recorded or gated.

## Migration / sequencing

1. `materialize()` always routes through the frontier (default = tip). Validate by running the full
   suite green with no other change — proves tip-frontier ≡ today's `active_effects`.
2. Add the `OFF` sentinel; `load_frontier` preserves it (an `OFF` lane is not defaulted back to tip).
3. Rewrite `lifecycle/algebra` + `orchestrate/loop`: `revert`/`restore` as frontier edits with
   closure. Drop `NodeStatus.SUSPENDED` (update `merge/engine`, `sync`, `graph`, tests).
4. Collapse the CLI/MCP/TUI surface to the set above; fold `sync`/`emit`/`compose`/`switch`/
   `decisions`. Keep `sgt.api` as the single projection.
5. Run the full suite + the graph-stress harness; adapt tests intentionally to the new model.

## Open questions

1. **Lossless revert vs GC.** Revert no longer deletes; do we ever need a `forget`/GC verb to drop a
   lane from history permanently, or is "off forever" enough? (Leaning: no GC verb until something
   demands it — losslessness is the feature.)
2. **Performance.** `materialize()` now always builds decisions; it's called by `valid()`/drift/
   status. Build_decisions is O(log); cache per call if it bites. Measure before optimizing.
3. **restore closure depth.** Restoring B pulls in its build-on ancestors at *their tip* — correct
   when they were merely off, but if an ancestor was pinned to an old version, do we honor the pin
   or move it to tip? (Leaning: honor an explicit pin, only auto-restore `OFF` ancestors.)
4. **Same-name rivals are one lane.** Two decisions that define the same entity merge into one lane
   (`_assign_lanes` footprint-union), and a lossless frontier holds one in-force decision per lane —
   so the old "suspend the rival, reconcile the held same-name def" recovery has no toggle analog
   (a held rival can't be promoted by reverting its rival; they're the same lane). This is a
   *single-lane pick*, not a toggle. The general reconcile paths (provider-lands, empty-bundle,
   blocked-while-active) are unaffected. A future `fork` verb (UC4 in the robustness brainstorm)
   that makes a competing definition its *own* lane is the clean way to hold genuine alternatives.
