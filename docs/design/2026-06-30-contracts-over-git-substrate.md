---
date: 2026-06-30
topic: contracts over a git substrate — operations are total, validity is a two-altitude predicate, coupling is an inferred provides/requires interface, the codebase is a reducible derivation
status: design / ADR — proposed; supersedes the direction of 2026-06-29-git-as-substrate.md and folds the open questions of 2026-06-25-one-frontier-minimal-verbs.md
supersedes-decisions:
  - eico-gate (AST confluence gate forbids non-confluent operations)
  - effect-log-primary (replay + ast.unparse is the source of truth)
  - decision-as-node (a "decision" conflates identity, content, and coupling)
origin: a long brainstorm (2026-06-29..30); builds on git-as-substrate, one-frontier-minimal-verbs, and Meng & Jackson "What You See Is What It Does" (concept/sync structural pattern)
author-note: written by Claude as an independent synthesis. Sections marked [CALL] are my judgments, some of which narrow or reject ideas raised in the brainstorm. Sections marked [RISK] are where I think this can fail.
---

# Contracts over a git substrate

## 0. The thesis in one paragraph

git stores the **content** (byte-faithful snapshots, diff, merge). sgt stores the **contract-DAG**:
what units of purpose exist, what each one *provides* and *requires*, and which are *in force*. A
sgt operation is a pure rewrite of that graph; it is **always valid** as an operation. Whether the
composed tree *builds* is a separate, reported fact at a lower altitude, never a veto. The graph is
a derivation of the codebase, and it is reducible to the minimal set of contracts that explains the
current tree. sgt authors no code; the LLM reasons about the graph and, only when a composition
breaks below the interface, repairs the break.

This *proposes to* delete — in phase 3, after the §10 spike validates the model — the AST
effect-log, the `ast.unparse` round-trip, and the EICO confluence gate. It keeps git, the
one-projection rule (`sgt.api`), the surfaces, and the frontier (recast as a per-lens selection).

## 1. Requirements (extracted from the brainstorm, deduplicated and ordered)

- **R1 — No vibes in operations.** Every operation has a deterministic, graph-level meaning that
  does not depend on an LLM.
- **R2 — Decouple semantic validity from content validity.** A well-formed operation is always
  permitted; "does it build" is reported, not gated. (This *flips* today's EICO gate.)
- **R3 — Keep content, byte-faithful, per transaction.** No reconstruction by `ast.unparse`. A
  checkpoint is a real git commit. This kills comment/format drift at the root.
- **R4 — The codebase is a derivation.** The asset is the contract-DAG (how it formed); content
  snapshots are the materialization/cache. Both coexist — git itself is exactly this (commit DAG +
  trees), so R3 and R4 do **not** conflict.
- **R5 — Operate at the interface altitude.** We care whether a contract's *requires* are met by an
  in-force *provides* (graph-level, deterministic). We do **not** care about byte-level type fitting.
- **R6 — Two-altitude break model.** *Interface break*: an in-force `requires` has no in-force
  `provides` — caught pre-materialize, this is what we care about. *Implementation break*: interfaces
  line up but the tree won't build — delegated to repair, this is what we don't care about up here.
- **R7 — Infer, then confirm.** Don't make users hand-declare interfaces. Derive provides/requires
  from the commit's defs/uses; let the LLM/human name and group. Mirrors plan→checkpoint.
- **R8 — Identity, content, and coupling are three separate things.** A node is an identity
  (a contract). Its versions are commits on a lane (content). Its relationships are edges (coupling).
  Today's "decision" smears all three; that is the cause of "revert a revision drops the create."
- **R9 — Minimal route.** The contract-DAG reduces to the minimal subset of contracts whose
  composition equals the current tree — an idealized history. Pure graph; no LLM.
- **R10 — The one rule, sharpened.** sgt never authors code for a substrate it already has content
  for. The LLM may (a) reason about the graph and (b) repair a composition break — make it build,
  never originate a feature's logic.
- **R11 — Collaboration merges the graph, not the code.** Applying/merging another contributor's
  work is a v1 requirement. Convergence lives in the contract-DAG (grow-only contract/commit sets +
  derived edges + an LWW `Selection`) — a CRDT over a small sidecar — while content is git's 3-way
  merge with conflicts routed to repair. No effect-log is needed for this (see §2.4).

## 2. The model

### 2.1 Three axes, never conflated [CALL]

A **Contract** is an identity for a unit of purpose. It is *not* a snapshot and *not* an operation.

- **Identity axis** — the contract node (stable id, purpose, interface).
- **Content axis** — an ordered list of commits on the contract's lane (its versions).
- **In-force axis** — the selection: is this contract in force, and at which version (commit)?

This split directly resolves two long-standing warts:
- *Revert dropping the create* (git-as-substrate #2/#3): revert is an **in-force-axis** toggle (lane
  → OFF); choosing an earlier version is a **content-axis** move (`switch …@vN`). Two verbs, two
  axes, no overload.
- *"Is a node a snapshot or an operation?"* (the brainstorm's central confusion): neither. A node is
  the identity; snapshots live on the content axis; operations are graph rewrites.

### 2.2 Coupling is an inferred provides/requires interface [CALL]

A contract exposes two symbol sets:

- **provides** — entities it defines.
- **requires** — entities it references but does not define.

`Symbol` reuses the existing footprint join key (`file::qualname`). An edge is **derived, never
stored as truth**: `B builds-on A ⟺ B.requires ∩ A.provides ≠ ∅`.

[CALL] **Moves are tracked via git rename detection.** Because `Symbol` embeds the file path, a def
moving to another file would change its symbol and silently drop edges (the old refactor/rename
limitation). At checkpoint, `analyze()` consumes git's rename/similarity detection and rewrites the
file component of moved symbols in `provides`/`requires`; `rename` (§6) generalizes from
qualname→qualname to *remap a Symbol's qualname and/or file*. This is the mechanism that actually
fixes the limitation rather than restating it.

[CALL] **Granularity stays at the entity for v1.** The brainstorm floated a coarser "capability"
abstraction above entities. Reject it for now: entity-level provides/requires is *free* (it falls
out of def/use analysis we already do), and a named-capability layer is sugar we can add later by
grouping symbols. Designing the capability vocabulary up front is premature abstraction.

### 2.3 Lenses: one for v1, the model leaves room for more [CALL]

A **lens** is a labeling of content into contracts. The current frontier *is* the "feature" lens.
The brainstorm wanted overlapping lenses (feature/layer/concern) with off-dominates masking.

[CALL] **v1 ships a single lens (feature).** The data model is lens-keyed so multi-lens slots in
without a rewrite, but we do not build it. Reason: every core win (R2/R5/R6/R9) is fully realized
with one lens, and multi-lens carries an unsolved hard case (two lenses fighting over shared
content) that would dominate the work for a feature we cannot yet justify. `Contract.lens` exists in
the schema; only `"feature"` is populated.

### 2.4 Merge is a CRDT over the contract-DAG, not the content [CALL]

Collaboration — applying and merging another contributor's local work (R11) — is a v1 requirement.
The realization that makes it tractable: the layer that must *converge* is the contract-DAG metadata,
not the code.

- **Content stays git.** Two people editing the same function differently should produce a
  *conflict*, not a silently interleaved or LWW-collapsed result — a text CRDT over source yields
  garbage or discards a side. So content is git's 3-way merge; a real conflict routes to the repair
  hook (§8.2), exactly the content gate.
- **The contract-DAG is a CRDT.** It is small and set-shaped, so it converges with no effect-log:
  - `contracts` — a grow-only set keyed by `ContractId` (trailers keep identity stable). Union.
  - `commits` per contract — a grow-only set of shas; compose order is git's topo order. Union.
  - `provides`/`requires` — derived, so they recompute from the merged tree. No merge needed.
  - `Selection` — a per-`ContractId` LWW register keyed by a logical (Lamport) clock, covering the
    version selector and OFF uniformly. [CALL] LWW (latest in-force decision wins) over
    "OFF-dominates" because `Selection` carries a *version*, not just a boolean; OFF-dominates
    resolves only the boolean and never converges back to in-force.

This delivers deterministic convergent merge of the semantic structure — the property the deleted
effect-log used to provide — at the layer where CRDT semantics are *correct*, without keeping the
effect-log. R3/§10.3 (delete the log; git holds content byte-faithfully) is unaffected. A
verbatim-span log is **not** required for collaboration; it is relevant only if statement-level
distill granularity finer than a git hunk is independently wanted — a separate motivation.

## 3. Data structures

```text
Contract:
  id:        ContractId
  lens:      LensId            # "feature" in v1
  purpose:   str               # one line; LLM- or human-labeled; deterministic fallback = slug
  status:    PLANNED | ACTIVE | QUARANTINED   # in-force-ness is NOT here (it is the selection)
  commits:   [CommitSha]       # the content axis (byte-faithful snapshots)
  provides:  set[Symbol]       # entities defined across this contract's commits
  requires:  set[Symbol]       # external symbols referenced

Selection (per lens):
  inforce:   dict[ContractId, CommitSelector | OFF]   # default = tip; OFF = out of force

Edge (derived on read, cached):
  A -> B   iff   B.requires ∩ A.provides != {}

Symbol:  "file::qualname"      # same join key the footprint already uses
```

The decision→commit map lives in commit trailers (`Contract-Id: <id>`) so it survives plain-git
inspection and (with a re-tag hook) rebase. There is no separate effect log.

## 4. How a contract is formed (the algorithm)

```text
checkpoint(intent?, fulfills?):
    sha  = git commit -A                       # R3: byte-faithful snapshot, trailer Contract-Id
    diff = git diff parent..sha
    defs, refs = analyze(diff, language)        # best-effort per-language def/use (see RISK-1)
    provides = { canon(d) for d in defs }
    requires = { canon(r) for r in refs } - provides
    C = lookup(fulfills) if fulfills else lane_contract(sha)   # PLANNED -> ACTIVE on fulfill
    if fulfills and C.status == PLANNED: C.status = ACTIVE      # realize the planned contract
    C.commits.append(sha)
    C.provides |= provides ; C.requires |= requires
    recompute_edges()                           # purely from provides/requires intersection
```

`reconcile` is the same body run against the *working tree* instead of a fresh commit, used to
re-derive interfaces after out-of-band edits and to attempt to clear `QUARANTINED`.

`analyze()` is the only language-aware part. [RISK-1] For Python it is `ast` def/use. For other
languages it needs tree-sitter/LSP. **Language-agnosticism is the floor, the interface gate is an
enhancement:** with no analyzer, `provides/requires` are empty, the interface gate is a no-op, and
sgt degrades to pure git-as-substrate (build is the only gate). We never *block* on having an
analyzer.

## 5. The two gates

```text
interface_break(selection) -> set[(Contract, Symbol)]:        # R5/R6, semantic, cheap, pre-materialize
    inforce  = { C : selection.inforce[C] != OFF }
    provided = ∪ { C.provides for C in inforce }
    return { (C, s) for C in inforce for s in C.requires if s not in provided }

materialize(selection):                                       # R2: operation ALWAYS succeeds
    orphans = interface_break(selection)                      # report, never veto
    tree    = git_compose(selected commits, topo order)       # phase 2+; spike (§10.1) uses current replay
    build   = build_check(tree)                               # content gate (ground truth)
    return tree, orphans, build
```

- **Interface gate** (the successor to EICO): deterministic, graph-level, language-light. It is the
  thing operations are *checked against and reported on* — it is **necessary but not sufficient** for
  content validity, and it never forbids.
- **Content gate**: build/typecheck/test on the composed tree. Failure (or a git conflict) →
  the repair hook.
- **Compose guarantee (R2): total, not clean.** `git_compose` never fails *as an operation*. It
  yields a clean tree only when the dropped (OFF) contracts are leaves (no in-force contract requires
  their provides) or touch disjoint files; a mid-history drop that conflicts yields a *conflict
  report* routed to the repair hook (§8.2), not an error. The spike (§10.1) measures how often
  mid-history drops actually conflict — that rate is RISK-2.

[CALL] The EICO/AST confluence gate is deleted, not weakened. Trying to make composition
*provably* confluent is what drove everything into quarantine and function-level degradation. We
replace "prove it commutes" with "report what's orphaned, then try to build."

## 6. User operations and how they change contracts

| op | content axis | in-force axis | DAG | gates run |
|---|---|---|---|---|
| `plan <intent>` | — | — | add PLANNED node(s); intended provides/requires from intent | interface gate can pre-flag "plan requires X nobody provides" |
| `checkpoint [--fulfills r]` | append commit | (PLANNED→ACTIVE) | derive interface, recompute edges | both, reported |
| `revert <ref>` | — | lane → OFF (lossless) | — | interface gate → orphaned requires downstream; then materialize |
| `restore <ref>` | — | lane → in force (pull OFF transitive build-on providers — contracts whose provides satisfy this one's requires) | — | interface gate ensures requires satisfiable |
| `switch <ref@vN>` | select version vN | — | — | both (this is "revert a *version*", not the feature) |
| `branch <name>` | — | fork a named alternative Selection over the same contracts | — | — |
| `decompose <ref>` | partition the contract's commits/symbols into two contracts | — | recompute edges | interface gate (both halves' requires must resolve) |
| `rename <sym> <sym'>` | — | — | rewrite Symbol map so provides/requires keep matching | interface gate (should show *fewer* orphans) |
| `reduce` | — | — | compute minimal route (read-only) | — |
| `reconcile <ref>` | — | — | re-derive interface from working tree | both; quarantine-exit |
| `merge` / `apply <ref>` | union foreign commits onto lanes | LWW-merge Selections | union contracts; recompute edges | both; content conflict → repair (§8.2) |
| reads (`log/graph/show/blame/diff/status/tag`) | — | — | — | — |

[CALL] **`rename` is a new first-class operation.** The refactor/rename limitation has had no home
because identity was tied to AST/text. Once identity is the contract and coupling is a symbol map,
a rename is exactly "rewrite the vocabulary so edges don't spuriously break" — content already
carries the new name; only the interface map needs the rewrite. The same remap covers a cross-file
*move* (git rename detection supplies the old→new path; see §2.2), so moves no longer drop edges.
This finally fixes the limitation instead of deferring it.

[CALL] **`branch`, `decompose`, and cherry-pick.** `branch` forks a `Selection` — a named alternative
in-force set over the same contracts (cheap; the selection already exists). `decompose` is the prior
model's `split`, reintroduced on the content axis: partition one contract's commits/symbols into two.
User-level cherry-pick is sugar over `restore <contract>` onto a chosen selection — no new primitive.
This closes the gap between §6's verbs and the user operations the project promises.

## 7. Minimal route (the reduction) [CALL]

[CALL] **Ship transitive-reduction + dead-contract elimination, not optimal set cover.** The
"smallest subset that reproduces the tree" is set-cover-flavored (NP-hard) and *not unique*. A cheap,
deterministic, good-enough reduction is worth far more than an optimal intractable one:

```text
reduce(HEAD_selection):
    # 1. drop in-force contracts whose provides nobody in-force requires
    #    AND whose removal leaves materialize() byte-identical (verify by recompose+diff)
    # 2. transitive-reduction of builds-on over what remains
    return idealized_dag
```

This yields the "idealized history" the brainstorm wanted — the minimal route of contracts that
explains the concurrent codebase — as a read-only view, never mutating the real history.

## 8. The LLM boundary (R10, exact wording)

The LLM may:
1. **Reason about the graph** — decompose a `plan` intent into contracts; name a contract's purpose;
   propose symbol→capability groupings. Deterministic fallback with no API key (slug names,
   entity-level groups).
2. **Repair a content break** — given the conflict hunks / failing build + the involved contracts'
   intents + the changed/orphaned symbols, produce a repair commit attributed to a synthetic
   `integration` contract. It makes the composition build; it never originates a feature's logic.

Transfer (realize a contract subset onto a *fresh* substrate, the "similar artifacts on another
codebase" idea) is the *only* place generation could become primary. It is **out of v1 scope** and
explicitly flagged so it cannot drift in: v1 never regenerates a substrate it already has content for.

## 9. Risks / where this can fail

- **[RISK-1] Symbol analysis is language-bound.** Mitigated by making the interface gate an
  enhancement over a git-only floor (§4). Measure how often the gate is a no-op on real repos.
- **[RISK-2] Patch-application fragility is the real enemy, not interface mismatch.** Composing an
  arbitrary subset by cherry-pick will conflict on context drift *even when symbols match*; the
  interface gate cannot predict these. [CALL] compose is **"replay in original relative order,
  dropping OFF contracts"** (rebase-style), not arbitrary reordering — this minimizes context drift.
  The spike must *measure* the real conflict rate; if it is high, the whole "compose any subset"
  premise is in question. This is the single biggest bet in the design.
- **[RISK-3] Two-axis UX.** `switch` (version) vs `revert` (off) can confuse. The decision-graph
  already has a time axis (version) and a selection (in force) — the UI must render both, or users
  will conflate them again, reintroducing R8's problem at the surface.
- **[RISK-4] Trailer survival across rebase/amend.** The decision→commit map rots if trailers don't
  survive. Needs a re-tag hook before any production use (inherited unresolved from git-as-substrate).

## 10. Plan — additive spike first, deletion second

1. **Spike (no deletion).** Alongside the current store: derive provides/requires at checkpoint;
   implement `interface_break`; make `revert` always succeed and *report* orphans + a build result
   without vetoing. **Compose a real subset** — `git_compose` of 2–3 contracts with a *middle* one
   dropped (not just a tail) — so patch-application conflict is actually exercised, not just the
   interface gate. Goal: validate R2 + R5 + R6 on a real repo and **measure two rates**: (a) RISK-2's
   patch-application conflict rate, and (b) the interface gate's **false-green rate** — of the breaks
   the content gate catches, the fraction the interface gate predicted (a signature/arity change
   keeps the qualname and passes the gate, so this rate could be high; if it is, R5/R6 must be
   restated to claim only symbol-orphaning, not "the breaks we care about"). **Kill criterion:**
   pre-commit a conflict-rate ceiling and a false-green ceiling above which the contracts model is
   abandoned rather than tuned.
2. **If the spike holds:** make git the substrate for compose (`git_compose`), add `switch`-by-version
   and `rename`, add `reduce`, and add `merge`/`apply` (contract-DAG CRDT — graph union + LWW
   `Selection`; content via git 3-way + repair — §2.4).
3. **Then delete:** effect-log replay, `build_statement_seq`, the reverse differ, AST blame, the EICO
   gate. Re-point `sgt.api` to derive from `git log` + the contract sidecar: blame is recomputed from
   the commit/contract log instead of `attribute()`, and the checkpoint scrubber's per-frame
   projection (`materialize_at(frame)`) needs a git-native equivalent — it has no replay to fall back
   on. Surface *rendering* is unchanged; the projection they read is re-derived (the scrubber is the
   hard case, not a one-liner).
4. **Defer:** multi-lens, named capabilities, transfer/regeneration.

## 11. Decision

**Proposed.** The spike in §10.1 is cheap, reversible, and tests the two altitude claims the model
depends on — that operations can be total with validity reported (R2), and that an interface gate
predicts the breaks we care about while staying cheap and language-light (R5/R6) — and it measures
the one risk (RISK-2) that could sink the model. The `git_compose` substrate, `switch`-by-version,
`rename`, and `reduce` remain unvalidated until §10.2. No machinery is deleted until the spike
reports against its pre-committed kill criterion (§10.1).

## Deferred / Open Questions

### From 2026-06-30 review

*Resolved into the sections above: the compose-validity guarantee — total, not clean (§5);
file-move tracking via git rename detection (§2.2 / §6); and the `branch` / `decompose` /
cherry-pick verbs (§6).*

- **[OPEN] LLM repair hook vs the one rule (§8.2 / R10).** A repair commit that "makes the
  composition build" authors code; "repair, never originate a feature's logic" has no enforcement.
  Candidate boundary (not yet adopted): repair may touch only conflict-marker / orphaned-symbol
  lines, with a deterministic check rejecting net-new top-level defs, attributed to the `integration`
  contract (which is then exempt from `reduce()` dead-elimination). Alternative: flag R10 as a
  deliberate amendment to "sgt never authors code." Decide before the spike implements §8.2.
- **[RESOLVED] Collaborative merge is a CRDT over the contract-DAG, not the content (§2.4 / R11).**
  The convergence the deleted effect-log provided is recovered at the metadata layer — grow-only
  contract/commit sets + derived edges + an LWW `Selection` — a CRDT over a small sidecar, not over
  the code. Content stays git's 3-way merge with conflicts routed to repair. So R3 / §10.3 (delete
  the effect-log) stands, and full CRDT merge ships in v1 at the layer where CRDT semantics are
  correct. The span-log is needed only for sub-hunk statement granularity — a separate motivation.
  (feasibility, product-lens, adversarial, scope-guardian)
