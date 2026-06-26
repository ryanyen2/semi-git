---
date: 2026-06-25
topic: making sgt robust enough to replace git — five stress use cases, a cleaner verb algebra, and a controlled-NL intent/selector DSL
status: brainstorm
origin: this conversation (2026-06-25); builds on docs/design/2026-06-19-graph-only-agent-driven-sgt.md and the decision-DAG pivot
---

# Robustness + the intent DSL — what it takes to replace git

## Framing: what "replace git" actually demands

The toy walkthroughs (`scripts/graph_stress/projects.py`) all follow one happy shape:
`plan → fold → suspend → revert → restore`, with selectors that are clean substrings of a
slug. Real usage is messier in four ways git survives and sgt currently does not:

1. **The plan is wrong before any code exists** and you need to reshape it, not append to it.
2. **Recorded work needs re-decomposing** — a lane grew too big, or two lanes were really one.
3. **Refactors cross the structural boundary** (rename, move, extract) and reset the
   feature→code map (`refactor-rename-distill-limitation`, `distill-module-level-and-import-constraints`).
4. **You don't remember the node id** — you remember *what it did* ("the preprocessing method")
   or *when* ("before auth").

git is robust because its primitives (`add`, `commit`, `reset`, `revert`, `rebase -i`, `cherry-pick`,
`reflog`, `bisect`) compose over *any* edit, and its selectors (`HEAD~3`, `@{yesterday}`, `:/fix typo`,
path globs) are uniform across every verb. sgt is more semantic but less complete: it has no
plan-editing, no history-rewrite, no fork verb, and a substring-only resolver. The five use cases
below are chosen to expose exactly those gaps; Part 2 proposes the architecture to close them.

---

## Part 1 — Five use cases that break today

For each: the scenario, what the user types, what git does, what sgt does **today**, where it
breaks, and what it implies for the design.

### UC1 — Iterate on a plan *before* there is any code

**Scenario.** `sgt plan "add API-key auth with rate limiting"` returns four `PLANNED` nodes. On
review the user wants to: merge nodes 2+3 (they're one concept), drop node 4 (out of scope),
re-decompose node 1 into finer pieces, and add a `needs` edge from rate-limiting to the key store.

**Git analog.** There is none for *drafts* — but `rebase -i` is the mental model: reorder, squash,
drop, edit, before anything is shared.

**sgt today.** `PLANNED` nodes are durable and reviewable (good) but **immutable from the CLI**.
Re-running `sgt plan` *appends* a fresh decomposition (`Orchestrator.plan` only ever calls
`add_plan`, `loop.py:116`) — it never replaces, so you accrete duplicate/overlapping drafts. There
is no `merge`, `split`, `drop`, `relabel`, or `edit-edge` for a `PLANNED` node. Your only recourse
is hand-editing `.sgt/graph.json`.

**Where it breaks.** The plan is supposed to be "the shared contract each agent claims"
(design doc, "the two entry points converge"), but a contract you can't amend without a text editor
isn't a contract. This is the single most-requested missing capability and the cheapest to add
(PLANNED nodes are inert — editing them gates nothing).

**Implies.** A **plan-space edit verb set** that mutates `PLANNED` nodes in place:
`replan`, `split`, `merge`, `drop`, `relabel`, `link`/`unlink` (declared `needs`/`provides`).

---

### UC2 — Re-decompose *recorded* work (split / merge / re-home a lane)

**Scenario.** The `auth` lane was checkpointed as one decision but now contains login, session, and
password-reset. The user wants to split it into three lanes so each can be reverted/suspended
independently. Conversely: two lanes (`csv_loader`, `csv_reader`) turn out to be the same concept and
should merge into one lane so their history reads as one feature.

**Git analog.** `git rebase -i` + `git split`-via-`reset -p`, or `git filter-repo` for re-homing.
Painful but possible.

**sgt today.** The unit of versioning is the lane (`Decision.feature`), and there is **no operation
that re-partitions a lane's effects**. `revert` removes a whole closure; `checkpoint` only adds. To
split you'd revert the lane and re-checkpoint three times by hand — losing the original decision's
provenance and landing order.

**Where it breaks.** This is the semantic equivalent of the refactor problem: the graph should be
*reorganizable* (the README literally promises "reorganizes the semantic graph"), but the only
reorganization that exists is fold-on-revise (the planner collapsing an enhancement into an existing
lane). Splitting and merging *landed* lanes — moving a subset of a decision's `footprint` to a new
`node_id`/lane while preserving effect identity — is unbuilt.

**Implies.** A **history-space** op set over `ACTIVE` decisions: `split <lane> by <selector>`,
`merge <lane> <lane>`, `move <entity> to <lane>`. These rewrite the *attribution* (which node owns
which effects) without re-materializing differently — the tree is identical before/after, only the
semantic map changes. That is exactly the kind of move sgt should make trivial and git makes brutal.

---

### UC3 — Survive a cross-cutting refactor

**Scenario.** The agent extracts `parser.py`'s three functions into a `lexer/` package, renames
`tokenize → lex`, and moves a top-level helper into a class method. Then the user wants to revert an
*unrelated* feature and expects the auth lane and the (now-moved) lexer lane to stay intact.

**Git analog.** `git` tracks this as deletions+additions but `--follow`/rename detection mostly
recovers blame. It degrades gracefully.

**sgt today.** Documented limitation (`refactor-rename-distill-limitation`,
`distill-module-level-and-import-constraints`): the reverse differ does top-level `rename_def`
detection (FINDINGS, "Rename-aware distillation") but **cross-scope moves are still delete+add**, so
the moved helper orphans its node and a spurious new node claims the real code. Module-level
statements, `from __future__` imports, and sibling imports can `invariant_violated`-quarantine on
checkpoint. A real refactor therefore *resets the feature→code map* — the opposite of robust.

**Where it breaks.** This is the existential robustness threat. A tool that "versions by feature"
but loses the feature map on every refactor cannot replace git, because refactoring is continuous.
The semantic map must be *refactor-invariant*: moving code between scopes/files must be a
`move`/`rename` effect that the lane evolves through, not a destroy+recreate.

**Implies.** (a) A `move_def`/cross-scope `rename` effect op so the differ can express relocation;
(b) distill must never quarantine ordinary Python (module-level statements, import forms) — these
are the "breaks across usage" reports; (c) the **selector and DSL must let a human assert identity**
when the differ can't infer it ("this `lex` *is* the old `tokenize`") — see UC5 and the DSL.

---

### UC4 — Hold and compare alternatives (fork → compose → A/B → promote)

**Scenario.** Two caching strategies (LRU vs TTL). The user wants to keep **both** as alternatives on
the `cache` lane, compose one into HEAD, benchmark, swap the other in, `diff` the two compositions,
and promote the winner — without deleting the loser (it's the documented road-not-taken).

**Git analog.** Two branches + manual cherry-picking + a throwaway compare. git cannot hold
"feature-A@v3 alongside feature-B@latest" in one working tree — this is sgt's standout advantage.

**sgt today.** The *model* supports it: `LifecycleKind.FORK` (`decisions/model.py:42`), `Frontier`
selection, `compose` (pin a lane to a decision, `loop.py:261`), `tag` (name a composition), `diff`
(decision-level delta), `blast_radius`. **But the command surface doesn't expose the creation move:**
there is no `sgt fork` verb. A `FORK` decision can exist in the model yet nothing in `_VERBS`
(`cli.py:16`) produces one, and `Alternative` capture is "distilled, low confidence" or plan-derived
— never asserted by the user at the moment of choice.

**Where it breaks.** The most compelling "better than git" story is half-wired: you can navigate and
compose alternatives but you can't *declare* one. And the rationale (why LRU lost) — the thing an ADR
exists to record — has no first-class capture path.

**Implies.** A **compose-space** verb set that finishes the decision layer: `fork <lane> as <name>`,
`compose`/`promote`, and rationale capture (`--because`, `--instead-of`) that writes `Alternative`
with `source="user"`, `confidence="high"`.

---

### UC5 — Operate by meaning and time, not by id

**Scenario.** "Revert the preprocessing method." "What breaks if I remove the rate limiter?" "Go back
to before auth landed." "Undo my last checkpoint." The user never types a node id.

**Git analog.** `git revert :/preprocess`, `git log -S`, `@{yesterday}`, `HEAD~2`, `reset --soft
HEAD@{1}`. Selectors are uniform and powerful across every verb.

**sgt today.** `resolve_ref` (`agents/resolve.py`) is **substring-only**: exact id → exact intent →
substring of id/intent. "the preprocessing method" misses a lane slugged `normalize-input-rows`.
There is no entity-key selector (even though `Decision.footprint` stores `file::target` keys — the
perfect handle), no temporal selector, no relational selector, and no LLM/embedding fallback. There
is also no "undo the last record" (no amend/uncheckpoint; `reflog` has no analog).

**Where it breaks.** Selector fragility is the quiet reason the demos "break across usage": the
moment a slug doesn't contain the user's word, every verb fails. Robust selection is a precondition
for *all* the other use cases — you can't `split`, `fork`, or `revert` what you can't name.

**Implies.** A real **selector subsystem** (Part 2B): tiered, shared by every verb, with an explicit
controlled syntax for power users *and* a semantic fallback for freeform.

> **Honorable mentions (sixth+ use cases worth a line):** **semantic bisect** — "which decision broke
> this test" by binary-searching frontier selections (`git bisect` analog, trivial given `compose`);
> **parallel-agent merge** — two agents fulfill different `PLANNED` nodes and merge realized lanes
> (the design doc's replica-local-plans boundary); **conflict/rebase** when two decisions touch the
> same `footprint` entity.

---

## Part 2 — Architecture: a cleaner algebra, a selector subsystem, and an intent DSL

### A. Re-think the verbs into four spaces

Today's verbs grew organically and overlap. `revert` means "remove a decision **and** its closure
and GC orphans"; `switch off` means "deselect from the frontier without cascading"; `compose` means
"pin a lane to an earlier decision." Those last two are *the same operation on the frontier
selection* viewed differently. I'd reorganize the surface into four honest spaces:

| Space | What it touches | Verbs (proposed) | Replaces / subsumes |
|---|---|---|---|
| **Plan** | `PLANNED` nodes (inert, ungated) | `plan`, `replan`, `split`, `merge`, `drop`, `relabel`, `link`/`unlink` | UC1 — all new |
| **Record** | distill reality → `ACTIVE` | `checkpoint` (`--fulfills`/`--intent`), `amend`, `uncheckpoint` | `sync` is the no-intent alias; `amend`/`uncheckpoint` new (UC5) |
| **Compose** | the `Frontier` selection (HEAD) | `use`/`unuse` (was `switch on/off`), `pin` (was `compose`), `fork`, `promote`, `tag`, `diff` | unifies `switch`+`compose`; adds `fork` (UC4) |
| **History** | which node owns which effects | `split`, `merge`, `move`, `revert` (remove+closure) | UC2/UC3 — mostly new |

Specific calls on the existing verbs the user flagged:

- **`switch on|off` → `use`/`unuse` (or `suspend`/`restore` aliases).** `switch <ref> off` is an
  awkward spelling of "exclude this lane from HEAD." It's really a frontier edit, so it belongs in
  compose-space next to `pin`. Keep `suspend`/`restore` as human aliases (the TUI already uses that
  language). The *refuse-on-dangling-reference* behavior (design doc) is correct and stays — it's
  what makes suspend surgical vs revert's cascade.
- **`revert` is overloaded vs `git revert`.** In git, `revert` *adds* an inverse commit; in sgt it
  *removes* a node + closure. When pitching "replace git" this will confuse. Consider `remove`/`drop`
  for the destructive closure op and reserve `revert` for "introduce a decision that undoes another"
  (which, in the decision model, is just a `FORK`/`REVISE` to the prior state — a natural fit).
- **`reconcile` vs `checkpoint --fulfills`** are two recovery doors ("a rival changed" vs "I revised
  the code"). They're individually principled (FINDINGS, "Review hardening") but the *names* don't
  tell you which to use. A single `resync` that detects the case and routes would be friendlier;
  keep the two as explicit escape hatches.

The win: every mutation is now obviously in one space, and **compose-space is entirely
frontier-selection edits** — one persisted object (`.sgt/frontier.json`), one materialize path.

### B. The selector subsystem — one resolver, many verbs

This is the highest-leverage robustness fix and mirrors the project's existing "one projection, many
clients" invariant. Replace the substring `resolve_ref` with a **tiered resolver shared by every
verb** (`revert`/`use`/`pin`/`fork`/`blame`/`show`/`split`…). Resolution order, cheap→expensive,
deterministic→semantic:

```
1. id            a3f9                         exact node / decision id            (today)
2. lane          lane:auth                    feature-lane name
3. entity        entity:prep.py::normalize    Decision.footprint join key  ← already stored!
4. dsl-relation  dependents-of:cache          derived from the entity graph (blast_radius)
                 lane-of:prep.py::normalize
5. temporal      @v3  @before:auth  @~2        landing index / lane tip / relative
6. intent-exact  "normalize input rows"       case-insensitive full intent       (today)
7. semantic      ~"the preprocessing method"  fuzzy → embedding → LLM, ranked, REQUIRES confirm
```

Tiers 1–6 are **offline and deterministic** (no key) — power users get exact control. Tier 7 is the
freeform fallback the user asked for (`sgt revert "the preprocessing method"`): it ranks candidates by
embedding/LLM over slug+intent+footprint and, because it's a guess, **always echoes the resolved
target for confirmation** before a destructive op. Entity selectors (tier 3) are essentially free —
`Decision.footprint` already holds `file::target` keys; we just need to expose them as a handle. This
single subsystem makes UC2/UC3/UC4 *addressable* and fixes the quiet selector-fragility breakage of
UC5.

### C. The Intent DSL — controlled NL over the *same* structured schema

The user's instinct (`Add X to Y as Z`) is exactly right, and it's stronger than it first looks: the
planner already emits a **structured schema** (`agents/planner.py:_SCHEMA` — `slug`, `intent`,
`context`, `consequence`, `provides`, `needs`, `depends_on`) and decisions carry `lifecycle_kind`
(`INTRODUCE`/`REVISE`/`FORK`) + `Alternative`. A controlled-NL grammar is just a **deterministic,
learnable front end that fills that same schema** — so it composes with everything downstream and
needs no new backend.

**The grammar (verbs map to schema + lifecycle):**

```
ADD     <capability> TO <lane|module> [AS <name>]      → INTRODUCE (REVISE if lane exists); provides += names
EXTEND  <lane> TO <behavior>                            → REVISE  (provides = existing lane names)
REPLACE <target> WITH <approach> [BECAUSE <reason>]     → REVISE  + Alternative{option:<old>, why:<reason>}
FORK    <lane> AS <name> [TO <behavior>]                → FORK    + Alternative (keeps the loser)
EXTRACT <names> FROM <lane> INTO <new-lane>             → history-space split
MOVE    <entity> TO <lane|module>                       → move_def (refactor-invariant, UC3)
WIRE    <lane> TO <lane>   (alias: MAKE <X> USE <Y>)     → declared needs edge
REMOVE  <target> FROM <lane>                            → remove_def within lane (REVISE)
```

**Three properties make this worth building:**

1. **Round-trip confirmation = the "structured NL template" the user wants.** Freeform always
   works; sgt *normalizes it to canonical DSL and echoes it back* for confirmation. Freeform
   `sgt "swap bubble sort for quicksort, it's too slow"` →
   `REPLACE sort::bubble_sort WITH quicksort BECAUSE "O(n²) too slow"` → confirm. The user learns the
   patterns by seeing their own intent rendered into them, and a power user can skip the LLM by
   typing canonical form directly. Controllability when wanted, freeform when not — exactly the ask.
2. **The DSL doubles as decision/context capture.** `REPLACE … WITH … BECAUSE …` *is* an
   `Intent{decision, context}` + `Alternative{option, why_rejected, source:"user", confidence:"high"}`.
   `FORK … AS …` records the road taken *and* not taken at the moment of choice — fixing UC4's missing
   rationale capture. Today `Alternative` is "distilled, low confidence" (`decisions/model.py:68`);
   the DSL is how it becomes high-confidence, user-asserted ADR data, with no separate UI.
3. **Offline determinism + graceful degradation.** Canonical DSL parses with no API key (regex/PEG,
   no LLM); freeform falls back to the LLM planner, which already degrades to deterministic grouping
   offline. So the whole DSL story honors the existing "the loop works with no key" invariant.

### D. Capturing context/decision better (beyond the DSL)

The decision model is well-shaped (`Intent` + `Alternative` + `lifecycle`) but **starved of input**:
authored fields "default empty until the LLM-glue path fills them" (`decisions/model.py` docstring).
Three feeds:

- **The DSL** (C) — user-asserted rationale at decision time. Highest fidelity.
- **The agent transcript** — the `feature-graph-activity-sidecar` already tails the Claude transcript
  for *presence*; the same stream is a rationale source. A distill pass over "why did the agent do
  this" (kept out of `sgt.api`, attached to `meta`) populates `context`/`alternatives` with
  `source:"transcript"`. Already half-built.
- **Checkpoint flags** — `checkpoint --because "…" --instead-of "…"` for the code-first path that
  skips planning. Cheap, explicit, offline.

Keep the `confidence`/`source` provenance discipline (R3) so a UI never shows a distilled guess as
fact — that discipline is *why* user-asserted DSL capture is valuable.

### E. Robustness hardening (the unglamorous prerequisite)

None of the above matters if checkpoint quarantines on ordinary code. Before new verbs, the
"breaks across usage" reports (`distill-module-level-and-import-constraints`) must close:

- distill must handle module-level statements, `from __future__` imports, and sibling imports
  **without quarantining** — these are normal Python, not conflicts.
- a `move_def`/cross-scope rename effect (UC3) so refactors evolve a lane instead of resetting it.
- the **graph-stress harness is where this gets proven**: extend `scripts/graph_stress/projects.py`
  beyond `plan/fold/suspend/revert/restore` to include `replan`, `split`, `merge`, a real refactor
  move, and `fork`/`compose`/`promote`, with per-move expectations. "Robust across usage" is a
  measurable claim — make the harness measure it.

---

## Part 3 — Sequencing (what I'd build first)

1. **Selector subsystem (B)** — unblocks every other verb; tiers 1–6 are pure/offline; ship tier 7
   behind confirmation. *Highest leverage, lowest risk.*
2. **Distill hardening (E)** — stop quarantining ordinary Python; this is the bleeding wound.
3. **Plan-space edits (UC1)** — `replan`/`split`/`merge`/`drop`/`relabel` on `PLANNED` nodes; inert,
   gates nothing, cheap, most-requested.
4. **Intent DSL parser + round-trip confirm (C)** — canonical-form parse (offline) → schema; echo
   freeform back as DSL. Wire `BECAUSE`/`INSTEAD-OF` into `Alternative`.
5. **Compose-space completion (UC4)** — `fork`, unify `switch`/`compose` into `use`/`pin`/`promote`.
6. **History-space (UC2) + refactor effects (UC3)** — `split`/`merge`/`move` over `ACTIVE` decisions
   and the `move_def` effect. Hardest; do last, on top of a solid selector + distill base.

## Open questions

1. **DSL surface:** a strict parser (PEG) for canonical forms + LLM only for freeform→canonical, or
   one LLM pass that emits the schema and we *render* canonical for confirmation? (Leaning: parser for
   determinism/offline, LLM only to normalize freeform.)
2. **`revert` rename:** is breaking from git's `revert` meaning worth the clarity, given the
   "replace git" pitch? Or alias and document?
3. **History-space identity:** when `split` re-homes effects, do decisions keep their original
   `landing`/`commits` (history preserved, attribution moved) or get new ones? Affects merge/export.
4. **Semantic selector confirmation UX:** always confirm tier-7 resolves, or only for destructive
   verbs (`revert`/`drop`/`split`) and auto-resolve for reads (`show`/`blame`)?
5. **Transcript rationale:** opt-in per checkpoint, or a background distill? Privacy/noise tradeoff.
