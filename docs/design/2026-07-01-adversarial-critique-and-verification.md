---
date: 2026-07-01
role: adversarial research supervisor / reviewer
scope: the 6/29 → 6/30 → 7/01 design arc (git-as-substrate → contracts → intent-patch algebra → op-log ontology → symbol-id) + the 6/30 spike plan
verdict: the model is elegant and mostly self-consistent on paper, but three load-bearing claims are asserted-not-proven, two of them are contradicted by the codebase or by a sibling doc in the same day's corpus, and the only executable plan measures the wrong number.
---

# Adversarial critique — intent-patch / contracts / op-log arc

Read against the code (`sgt/effects/model.py`, `body.py`, `stmt_distill.py`) and the failure-history
memory. Citations are `doc §` or `file:line` or `memory/<name>`.

## A. Must-resolve challenges (ranked, most fatal first)

### A1. The "conflict-free AND byte-faithful" content-recompose is a revival of the shelved span-log, and the codebase does not implement it. **FATAL / self-deception.**

`operation-log-ontology.md §6` makes the load-bearing claim:

> "Recompose runs over the content CRDT (order slots by `PosId`, concat verbatim `Slot.source`), **not** over `git merge`/`git apply` … so it is conflict-free *and* byte-faithful at once. (`Slot.source` *is* the 'verbatim span-log' of `memory/git-substrate-shelved-span-log.md`.)"

Three things are wrong with this, in increasing severity:

1. **`Slot.source` is not verbatim today, and the recompose path is still `ast.unparse`.** In
   `sgt/effects/model.py:479-482`, `_apply_stmt_ops` does `new_body.extend(ast.parse(slot.source).body)`
   then `cb[file] = ast.unparse(tree)`. It re-parses each slot and unparses the *whole tree*. So the
   exact defect `memory/git-substrate-shelved-span-log.md` names — "`materialize()` round-trips the
   whole file through `ast.unparse()` … stripping comments + normalizing formatting" — is *still
   live*. The span-log was the **recommended fix that was never built**; the ontology doc cites it as
   if it were shipped infrastructure. Anything downstream that says "byte-faithful" is claiming a
   property the code does not have. **Proof obligation:** either build the verbatim splice (materialize
   by concatenating raw `Slot.source` byte ranges, no `ast.unparse`) and prove round-trip identity on
   a corpus with comments/blank-lines, or stop calling recompose byte-faithful.

2. **The slot CRDT only exists inside top-level functions.** `build_statement_seq` seeds from
   `_func_body_src(defining.payload["source"])` (`model.py:420-443`); `_apply_stmt_ops` rejects a
   target that "is not a function." Module-level statements, class bodies, decorators, signatures,
   and imports have **no slot identity** (confirmed by `memory/distill-module-level-and-import-constraints.md`
   and `git-as-substrate.md:47-49`). So "recompose over the content CRDT" covers a fraction of a real
   file; everything else must fall back to *something*, and the doc never says what. This is the same
   granularity hole `git-as-substrate.md` opened the whole arc to fix, re-inherited unacknowledged.

3. **Conflict-free ≠ correct — this is the exact trap `statement-distill-eid-lww` already recorded.**
   Concatenating two patches' statement inserts in `PosId` (fractional-index) order always *produces a
   sequence*, but a sequence of individually-valid statements is not a correct or even parseable
   function. Two concurrent inserts that each read/write a local, an early `return` from one patch that
   dominates the other's inserts, or an insert whose name the other patch's edit removed — all
   "converge" to garbage under LWW. `memory/statement-distill-eid-lww.md` says the merge tests
   *"passed only by luck (their edits happened to be valid / only edited stmt0)."* The CRDT removes
   *textual* conflict and hides *semantic* conflict — which is precisely the residue A3 says the LLM
   must repair. So "conflict-free" is true and misleading in the same breath.

**Live contradiction inside the same day's corpus.** `contracts-over-git-substrate.md §2.4` and the
spike plan (`§10.3`) say the opposite of the ontology doc: *content stays git 3-way merge, real
conflicts route to repair, delete `build_statement_seq` and the statement CRDT.* `operation-log-ontology.md §6`
says *keep `PosId`/`Slot`, recompose over the content CRDT, git off the hot path.* **These two 7/01
docs disagree about what the content substrate even is.** One deletes the statement CRDT; the other
makes it the recompose engine. This is unresolved and it is the center of the whole design. **Resolve
first:** pick one content substrate. If git-3-way → conflicts are real (kills the "conflict-free"
promise, see A6). If content-CRDT → build the verbatim splice and prove #1/#2/#3 above, on Python
*with comments and non-function code*.

### A2. "Footprint never misses a real dependency (sound over-approximating backward slice)" is **false**, and the doc asserts the opposite of the truth. **FATAL for toggle/revert/cherry-pick correctness.**

`operation-log-ontology.md §6`:

> "`depends(q,p) ⟺ q.requires ∩ p.provides ≠ ∅` is a symbol-granularity, **over-approximating**
> backward slice: it **never misses a real dependency (safe)** and sometimes over-includes."

Name-level `ast` def/use (the spike's `analyze()`, and `Project._used_names`/`_defines`) cannot see:
dynamic dispatch / subclass override where the call site names the base; `getattr`/`setattr`/`__getattr__`;
monkeypatching; decorators that wrap or register; metaclasses; string-keyed registries and plugin
dispatch (`ENTRY_POINTS["foo"]()`); `importlib`/`__import__`; framework registration by decorator or
naming convention (Flask routes, pytest fixtures, Django signals, click commands); `__all__` re-exports;
conditional/lazy imports; reflection; and cross-file *non-symbol* dependencies (a JSON schema, a config
key, a SQL string, a template name). Each of these is a real dependency the name-intersection **misses**.
A backward slice that misses edges is **unsound**, not over-approximating. The doc has the safety
direction backwards: over-approximation would be *safe* (spurious deps come off with a toggle, merely
annoying); *under-*approximation is what actually happens, and it **silently drops a required patch**.

Consequence: `branch = all - p2` composes a tree where p2 was needed but the footprint didn't say so.
The tree may still *import* (build oracle green) while being behaviorally broken, so the build gate
catches it only if a test exercises that path. **What the design owes:** (a) restate the claim honestly
— footprint is a *cheap, unsound* linkage gate, sound only for direct lexical name use; (b) make the
**build+test oracle the actual dependency backstop** and say so, which means the oracle's coverage
(not just "does it import") is load-bearing (see C); (c) measure the miss rate — of real dependencies,
the fraction the footprint sees — on the corpus, and treat a high miss rate as a reason the C-cases
(the product, per `intent-patch §8.5.1`) are unsafe, not merely imprecise.

### A3. Repair reliability is declared "the crux," and **the only executable plan does not measure it and wires no repair hook.** **FATAL process gap.**

`intent-patch-algebra §1 / §9 RISK-A` explicitly re-points the crux: *"Conflict rate is not the crux …
repair reliability is."* But the spike plan (`2026-06-30-001`, the *only* plan with implementation
units) measures `conflict_rate` + `false_green_rate`, pre-commits kill criteria on **those**, and states
"**LLM-free spike … No repair hook**" (KTD; U-list; Scope Boundaries). So the document the orchestrator
calls closest to the mental model (the 7/01 algebra doc) says the spike must measure X; the only plan
measures Y and Z and explicitly excludes X. The kill criterion is pre-committed on the wrong quantity.

Additionally the LLM fence — "no net-new top-level defs" — is asserted, unenforced (`§9 RISK-D`), and
possibly wrong: a legitimate seam repair often *needs* a small adapter/shim (a wrapper function, a
compat alias), which is a net-new top-level def. `intent-patch` Open Questions admits this ("too strict…").
So the fence both (a) has no implementation and (b) may reject the exact repairs it needs to allow.
**Resolve:** the spike must build a bounded-repair harness and measure bounded-repair success on real
semantic breaks, with a pre-committed floor; and the fence must be specified as a *checkable predicate*
(diff touches only lines inside the orphaned/conflict region ∧ AST of the repair adds no symbol to
`provides` outside a whitelisted `integration` namespace) and tested against real repairs, or dropped
in favor of "flag R10 as an explicit amendment to 'sgt never authors code.'"

### A4. The determinism split leaks: "everything downstream of a captured commit is deterministic" is only true given a fixed parser, a fixed θ, a fixed partition heuristic, and no LLM labeling. **Load-bearing overstatement.**

`intent-patch §1` and `op-log-ontology §0` claim `get` (code→graph) is deterministic. Enumerate the
non-deterministic / version-pinned inputs the corpus itself lists:
- **Parser version.** `op-log-ontology` Open Question admits the effect cache must be keyed by
  `(SHA, parser-version)` — i.e. the fold is deterministic *only relative to* an `ast` version. A
  Python upgrade changes qualnames/positions.
- **θ and the rename/arity/body-similarity heuristic** (`symbol-id §4`, `refactor-rename-distill-limitation`)
  decide whether a change is `remap` vs delete+create. Different θ → different symbol identities →
  different `provides`/`requires` → different dep-DAG. The join key's *value* depends on a tunable.
- **The partition** (`op-log-ontology §8`: "modularity maximization on the def/use graph"). Modularity
  maximization is seed/tie-break dependent and not unique; "which commits form one patch" is a heuristic
  guess, not a deterministic derivation, except on the live `plan`/`checkpoint --fulfills` path where the
  partition is *given*.
- **LLM labeling.** `gloss` is non-deterministic (falls back to a slug offline).

So "deterministic recording" is really "deterministic *set arithmetic* over footprints, given fixed
(parser, θ, partition)." That is a much weaker and more honest statement. **Resolve:** state the pinned
dependencies explicitly and show that the *load-bearing* outputs (dep edges used by toggle/revert) are
invariant under the heuristic knobs, or accept that recording is heuristic and design the drift/confirm
loop as the primary correctness mechanism (which is the honest framing, and cheap).

### A5. Symbol-identity confirm-loop: scales badly at exactly the workflow it exists for, and defers its hardest case. **Serious.**

`symbol-id §4` path B: heuristic proposes a rename, the **drift gate confirms**, "bias toward asking."
`intent-patch §8.5.3` says the D-cases (rename/move) are "the mechanism that keeps the C-cases surviving
refactors" — i.e. identity maintenance matters *most* during large refactors. But a large refactor is
precisely a mass event: a rename touching 50 call sites, or a bundled "rename + move + resignature"
commit, produces many simultaneous vanish/appear pairs → many candidates → either a prompt storm
(UX death) or an auto-policy that silently mis-joins (the "false positive corrupts the join silently"
failure the doc itself flags as worse). The confirm-loop's cost is proportional to refactor size, and
refactor size is unbounded. Worse, **symbol *split*** (extract half of `login` into two functions) is
explicitly **deferred** (`symbol-id §7`) — and splits are common in real refactors. So the identity
scheme punts its hardest and most frequent refactor case into a hole, at the exact workflow (D-cases)
that is supposed to make the product's thesis (C-cases) survive. **Resolve:** define the batch-confirm
UX (one prompt per *refactor*, not per symbol, grouped by the git-`-M` rename set), and either specify
symbol-split provenance now or explicitly scope the product to "no split-refactors survive" and measure
how often that bites on the corpus.

### A6. "Collaboration without conflict" is honest only for the metadata layer; content conflict re-enters, and the two 7/01 docs disagree on whether it does. **Marketing risk / spec contradiction.**

`contracts §R11 / §2.4`: metadata (contract-DAG) is a CRDT and converges; **content is git 3-way merge,
real conflicts route to repair.** That is honest and correct ("a text CRDT over source yields garbage or
discards a side"). But then `op-log-ontology §6` claims recompose is over the content CRDT and *textually
conflict-free* — contradicting §2.4 (A1's live contradiction, restated for the collaboration promise).
Net: the requirement-5 promise "multi-user collaboration without conflict" is true for *which contracts
exist / are in force* and **false for content** — two people editing one function still conflict and hit
LLM repair. **Resolve:** state the promise as "conflict-free convergence of the semantic graph; content
conflicts are git's, surfaced and repaired" — and never let a surface or README say "collaboration
without conflict" unqualified. Also decide (`op-log-ontology [Q]`) whether a `Selection`/branch is shared
mergeable state or local scratch; the collaboration model changes materially and is currently undecided.

### A7. `regroup`/`decompose` concurrent-merge semantics are unspecified — the op-log CRDT has a hole at its own primitive. **Medium, but it undercuts the "5 clean primitives" claim.**

`op-log-ontology §9 RISK` + Open Questions admit two concurrent `regroup`s of overlapping patches "may
not have a well-defined join," and provenance under `decompose` is "asserted, not specified." Since
`regroup` is 1 of the 5 kernel primitives and `decompose`/`merge` are headline reorg verbs (W2, the
place a hand-authored DSL "earns its keep"), an undefined merge for it means the CRDT is not actually a
CRDT for its own vocabulary. **Resolve:** either causally-serialize `regroup` (state it) or specify the
join; don't ship "everything is a clean fold" while one of five folds has no join.

## B. Workflow-coverage gaps (independent list; compare to the PL agent's table)

Legend: ✅ clean · ⚠️ expressible but lossy/unsound · ❌ cannot cleanly express.

| workflow | status | why |
|---|---|---|
| hand-edit / additive (A1–A3) | ✅ | table stakes; footprint + record |
| refine (change behavior in place) | ✅ | `refine`/`record revises:` |
| **revert a mid-history feature** | ⚠️ | set-diff is clean *iff* dep-DAG is complete; A2 makes the DAG unsound → may drop or keep the wrong patches, tree builds but breaks |
| **cherry-pick onto another base** | ⚠️ | same unsoundness; plus content merge (A1/A6) can conflict or garble |
| toggle/reorder | ⚠️ | "order-independent" only if footprints are complete (A2) and content splice is faithful (A1) |
| version-select (`switch @vN`) | ✅ | content-axis pick; clean if commits are byte-faithful |
| **rename / move** | ⚠️ | works *if* the drift gate fires and confirms; prompt-storm / silent mis-join at scale (A5) |
| **split a function (symbol split)** | ❌ | explicitly deferred (`symbol-id §7`); reads as rename+create |
| **signature / arity change** | ❌ | keeps qualname → footprint unchanged → dep edge unchanged, but callers now break; interface gate *false-green* by construction (spike U2 measures exactly this) |
| **reformat / whitespace / comment-only** | ❌ (today) | no symbol footprint → empty patch; and `ast.unparse` recompose *destroys* comments/format anyway (A1). The owner's "reflect what changed" cannot even represent a comment change. |
| module-level / class-body / decorator / import change | ⚠️/❌ | no statement-slot identity (A1.2); distiller note-only for arbitrary module statements (`memory/distill-module-level-and-import-constraints`) |
| **non-Python** | ❌ for the thesis | interface gate no-ops → "degrades to git-only" → sgt adds *nothing* over plain git for the C-cases that justify it. Requirement-5 "works for non-Python" is satisfied only trivially. |
| cross-file non-symbol dep (config/schema/SQL/template) | ❌ | invisible to name-level footprint (A2) |
| **fork / two-way what-if (F1)** | ✅ | two `branch`es on a base — genuinely clean |
| upstream rebase / amend | ❌ (unresolved) | trailer survival (RISK-4) punted since 6/29; decision→commit map rots |
| generated/vendored/lockfile/binary | ⚠️ | git-only; no semantic layer, fine if labeled so |

**Biggest gap:** signature/arity change + comment/reformat + non-Python together mean the layer that
"reflects what the code actually changed" (owner's #1 ask) is blind to a large class of real edits, and
the recompose path actively *destroys* formatting. The showcase C-cases the corpus says "justify sgt"
(`intent-patch §8.5.1`) are the ones most exposed to the unsound dep-DAG.

## C. Rabbit-hole warnings

**Over-engineering to stop:**
- **`reduce()` / minimal route / "idealized history"** (`contracts §7`, `intent-patch §6`). Read-only,
  beautiful, and useless for v1 — nobody's blocked workflow needs it. It's set-cover-adjacent; the
  temptation to make it optimal is a time sink. Defer entirely.
- **The whole op-log ontology rewrite** (`operation-log-ontology.md`). Elegant ("5 primitives,
  everything is a fold"), but it delivers **zero new user-visible capability** and, by its own RISK,
  "the cut is large … touches every surface." Refactoring the store from Node-record to op-log-fold
  *before* the repair-reliability number is in is premature abstraction of the highest order. It should
  not precede A3's measurement.
- **MV-registers, Lamport clocks, OR-Set formalism for a single-user v1.** Correct eventually; premature
  now. Collaboration is deferred behind the spike anyway (spike Scope Boundaries).
- **Named capabilities / multi-lens** — already correctly deferred; keep them dead.

**Under-invested but load-bearing (invest here instead):**
- **The build oracle's coverage.** The spike plan (U4) already flags that `py_compile` is insufficient
  and it must "import each changed module and run the corpus project's tests." This is *the* backstop
  for A2's unsoundness and A1's semantic garbage — yet "run the tests" on arbitrary composed subsets is
  flaky, slow, and only as good as the corpus's test coverage. If the oracle is weak, `false_green_rate`
  deflates and the spike returns a false HOLDS (U4 says this explicitly). The oracle strength is the
  quiet crux behind two other cruxes.
- **Verbatim-faithful materialize (A1.1).** The unsexy splice-raw-bytes-instead-of-`ast.unparse` change
  is the actual fix the memory recommended a *year of doc-days* ago and nobody built. It is worth more
  than any new abstraction.
- **Drift-gate batch UX (A5).** The difference between a usable and unusable refactor story.
- **Trailer survival across rebase (RISK-4).** Punted three docs in a row; it is the thing that makes
  the whole decision→commit map durable, and it's a concrete hook, not research.

## D. The single crux + pre-committed kill criterion

**Crux:** *Can an arbitrary in-force subset be recomposed into a tree that is (i) byte-faithful to the
captured content and (ii) driven to building-and-tests-passing by bounded seam-repair (seam-only, the
checkable fence), and how often?* This is one question because it fuses the owner's #1 ask (recording
+ faithful reflect/recompose) with the declared crux (repair reliability, `intent-patch §9`) and the
real enemy (semantic breaks the unsound footprint can't see, A2). Conflict rate — what the current
spike measures — is a *proxy* the newer doc already demoted.

**The measurement (amend the spike):** on `scripts/graph_stress/` corpus, run mid-history-drop composes
biased to co-edited functions, signature changes, and moves. For each: (a) assert content byte-identity
vs the captured commits *including comments/formatting* (fails today → A1); (b) run the strong oracle;
(c) on a semantic break, invoke a bounded-repair hook with the enforced fence and record whether it
reaches green.

**Pre-committed kill criteria (all three, strict):**
1. **Faithfulness:** if the recompose path cannot reproduce comments/formatting without falling back to
   git-3-way, the "byte-faithful content-CRDT recompose" premise (A1) is dead → content substrate must
   be git-3-way, and "conflict-free collaboration" (A6) is retracted.
2. **Repair reach:** bounded seam-repair reaches building+passing on **< 80%** of semantic breaks →
   the "not-closed algebra made total by repair" premise (A3) fails → fall back to a *vetoing* gate or
   to "sgt is a labeling lens with no composition promise."
3. **Footprint miss rate:** of the semantic breaks, the fraction the footprint dep-DAG failed to predict
   (would have dropped the needed patch) **> 25%** → the C-cases are unsafe → restrict the product to
   recording + diff-narration and drop the toggle/cherry-pick promise.

If any of the three trips, the honest fallback is the one nobody has costed: **ship sgt as a pure
recording lens** (get-direction: distill + drift + blame + labeled graph, no recompose/composition
promise). That fallback sidesteps A1, A2, A3, A6 entirely, directly serves the owner's stated #1 ask
(recording primary / reflect what changed), and is buildable on the *existing* store without the
op-log rewrite. The corpus should explicitly hold this option open rather than assume the composition
thesis survives the spike.

---

# Round 2 verification — the two deliverables

Adversarial pass over `docs/design/wip/pl-theory-and-dsl.md` (PL theory) and
`docs/plans/wip/refactor-plan.md` (refactor plan). Grounding claims re-checked against code.

## Verified grounding (both docs are honest about the codebase)

- **`graph.js` does not exist.** Confirmed: `editor/vscode/media/` has `decision.js`, not `graph.js`;
  `color.ts:8` mis-points to `media/graph.js`; `tests/test_color_parity.py:20` compares `color.py`
  ↔ `decision.js` only — **`color.ts` is never verified against either.** CLAUDE.md's INV-2 ("byte-
  identical in three places, a test compares JS vs Python") is therefore *already false in practice* —
  the third mirror is untested and the doc pointer is stale. The refactor plan's P0 catches this exactly
  and is a real (not cosmetic) fix. **Good grounding.**
- **`_assign_lanes` is genuinely order-sensitive** (`decisions/store.py:95` — processes nodes
  `sorted by (first_landing, nid)`, incrementally builds `owner_of_key`, tie-breaks on
  `provides_of[nid]` read from the mutable `Node` graph). `build_decisions` reads `project.graph` in
  three places (`provides_of`, REVISES edges, `derives_to` lineage). The plan's R1 flags this correctly.
- **`timeframe_view`/`materialize_at` has no live consumer.** Confirmed: only a comment reference in
  `types.ts:68`; no CLI/MCP/TS dispatch. The plan's "drop it in P5" option is safe.

## PL theory — are the PROOFS sound, or hand-waving dressed as proof?

The doc's intellectual honesty is real: every FALSE-IN-GENERAL tag (footprint unsoundness B.4,
same-symbol non-conflict-freedom B.5, semantic validity C.4) converged with Round-1 A1/A2/A6 and is
correctly conceded, not hidden. Nothing is dressed as an unconditional proof. But three [PROVED] claims
have gaps, and one "Safe" is an overclaim:

- **(ii) B.1 SEC over the product of join-semilattices — the LWW component is NOT c/a/i as stated.**
  LWW-register join is commutative/associative/idempotent **only if timestamps form a total order.**
  The doc keys LWW on "a Lamport clock" (§A.1, B.1) — a Lamport clock is a *partial* order: two
  concurrent `select`/`relabel` ops can carry **equal** counters, and LWW-by-counter-alone is then
  order-dependent (not commutative). The proof silently assumes a tie-break (replica id) it never
  states. **Fix (cheap):** state the register as LWW over lexicographic `(Lamport counter, ReplicaId)`;
  then the [PROVED] holds. As written, B.1's LWW leg rests on an unstated total-order assumption.
- **(ii, cont.) B.1 overstates coverage of `regroup`.** It lists `L` as "an OR-Set of ops … with causal
  tombstones for `regroup`" and declares SEC [PROVED]. But `operation-log-ontology.md §9 RISK` and the
  refactor plan's own deferred list say concurrent `regroup`s of overlapping patches "may not have a
  well-defined join." So SEC is proven for `record`/`relabel`/`select`, **not established for `regroup`**
  — the one primitive whose join is open. The [PROVED] tag should read "PROVED except `regroup`, which
  needs causal serialization or a specified join."
- **(iii) Generalized `remap` as a relation `R ⊆ Σ_old×Σ_new` is well-defined sequentially, but NOT
  shown to be a well-defined CRDT op under concurrent split‖rename of the same symbol.** D.2 elegantly
  closes the *sequential* completeness gap (symbol-split had no home) and B.5/§A.1 handle concurrent
  **rename‖rename** (MV-register → drift prompt). But concurrent **split‖rename** of one symbol `s` is
  unaddressed: replica A emits `R={(s,s1),(s,s2)}` (split, mints ids B never saw); replica B emits
  `R={(s,s)}` with a new surface name (rename). The per-`sym_id` MV-register join is undefined across a
  set that includes ids born only on one side — do `s1`/`s2` inherit B's rename? do `requires` edges
  pointing at `s` re-point to `s1`, `s2`, or renamed-`s`? **The generalization that fixes the sequential
  hole opens a concurrent-merge hole it does not acknowledge.** Must be specified before D.2 counts as
  keeping the kernel at 5 *and* CRDT-clean.
- **(i) C.3 parseability proof is sound but strictly CONDITIONAL and mildly circular.** The structural
  induction (well-bracketed forest → grammatical string) is valid and correctly proves **syntactic**
  parseability only — part 4 openly concedes semantic validity is oracle-bounded, so it does **not**
  dress bracketing as coherence. Two caveats: (a) the proof presupposes recording yields a *gapless,
  overlap-free, independently-selectable* span partition with per-grammar mandatory-body rules across
  languages — i.e. it proves properties *of* the structured CRDT *assuming that CRDT is constructible
  with those properties*, which is the hardest unbuilt item (§G names it as such — honest, but the whole
  composition thesis rests on an object that does not exist yet). (b) The **interior-edit case (a),
  "additive & disjoint … Safe."** is an overclaim: two patches inserting different statements into one
  function interleave by `PosId` — that is parseable and convergent, but **not semantically safe**
  (insert `x=1` in p1, `return x` in p2; `PosId` order may place the return first). This is B.4's
  dynamic-dependence gap re-appearing at statement granularity; the label should be "parseable +
  convergent; semantic coherence oracle-bounded," not "Safe." Small but it is the exact spot the
  orchestrator asked to stress, and the doc slightly under-warns there.

**Net:** the PL doc is the strongest artifact in the corpus. Its corrections are correct; its
conditional recompose theorem is the right frame. The gaps are fixable specification holes, not
refutations — but B.1's total-order tie-break and the concurrent split‖rename join must be closed
before the algebra is called proven.

## Refactor plan — does P3's equivalence gate actually catch lane divergence?

**Partially. It catches divergence *on the sampled corpus*, not in general — and it has a hollow-pass
risk.** Two concrete holes:

1. **Golden-master ≠ proof for an order-sensitive fold.** P3 asserts `fold_ops(project) ==
   build_decisions(project)` over 5 corpus projects + 3 e2e scripts. Because `_assign_lanes` tie-breaks
   on an incrementally-built `owner_of_key` and on `provides_of[nid]`, a lane-weld path (a
   `shared-and-not-fresh` node whose `_rank` tie-break flips because op-log-derived `provides` differs
   from `Node.provides` on some edge case — an `ADD_ASSIGN` target, a provides-nothing fix node) can be
   **unexercised by the fixed corpus, pass P3 green, then diverge post-cutover in P4.** The gate is a
   characterization test; its completeness is bounded by corpus coverage of the union-find's tie-break
   branches. **Condition:** add property-based / randomized-history stress that permutes landing order
   and forces multi-shared-key fold nodes, not just the 5 canned projects.
2. **Hollow-pass risk: P3 measures equivalence while the `Node` store still exists.** `build_decisions`
   reads `project.graph` for `provides_of` and REVISES/DERIVES edges. If `fold_ops` in P3 is allowed to
   read `project.graph` too (to match), P3 goes green while secretly depending on the store P4 deletes —
   equivalence proven under Node-present does **not** imply equivalence under Node-deleted. **Condition:**
   P3 must require `fold_ops` to be `project.graph`-read-free (derive edges from `record.revises`/
   `regroup` provenance and provides from commit diffs) — otherwise R1's test is not load-bearing. The
   plan does not state this constraint; it must.

Beyond that, the plan's sequencing is sound: the semantic axis (P1–P4) and content axis (P5) are
correctly split, so P4 can delete the `Node` store while `materialize()` (effect-log replay) still
holds content — no phase depends on the unbuilt CRDT except P5, which is explicitly PL-blocked.

## Do either doc quietly re-break a CLAUDE.md invariant?

- **INV-1 one-projection:** respected — plan is additive-only, golden-master-gated. ✓
- **INV-2 color byte-identity:** *currently broken in the tree* (only 2 of 3 mirrors tested); the plan
  P0 fixes it. Neither doc re-breaks it. ✓ (net improvement)
- **INV-3 blame-from-log, never a text diff:** **at risk, contingent on the P5 substrate choice.** If
  P5 lands on git-cherry-pick (the contracts-spike path) rather than the structured content CRDT, blame
  becomes `git blame` relabeled through the sidecar — which is **line→owner inferred from a text diff**,
  the exact thing INV-3 forbids (this is what `git-as-substrate.md` proposed and the memory flagged).
  Only the structured-CRDT substrate keeps blame a fold over recorded spans. **The plan should state
  that the two candidate P5 substrates have different INV-3 consequences** — it currently treats INV-3
  as uniformly preserved.
- **INV-4 reads-offline:** respected by both (minting deterministic, gates offline, LLM only front/back). ✓

## Is the fallback ladder honestly shippable at each rung?

- **Rung 1 — recording-lens core (distill → ops, footprint, sym_id, drift, blame, labeled graph):**
  **honestly shippable without the unbuilt CRDT.** It labels the *current* git tree; it does not
  recompose subsets, so it needs no structured content CRDT and no `ast.unparse`. Buildable as P0–P4
  (+ P1 sym_id). Caveat: blame attribution via diff-hunk∩footprint sits right on the INV-3 line — must
  be framed as log-derived (from recorded footprints), not a live text diff, to stay honest.
- **Rung 2 — gated structured-CRDT composition (the C-cases, "the rows that justify sgt"):** **NOT
  shippable without the unbuilt multi-language block-integrity CRDT** (PL §G's highest-risk item) **and**
  an unmeasured M2 bounded-repair floor. This is the honest hierarchy: the rung that *justifies* sgt over
  git+labels is the one gated on the hardest unbuilt piece; the rung that ships now doesn't justify sgt.
  The plan correctly quarantines this as P5/PL-blocked and does **not** let P0–P4 secretly depend on it.
- **Rung 3 — degrade (non-Python / no analyzer):** git-only floor, interface gate no-op. Shippable but
  adds nothing over git for the C-cases — consistent with Round-1 workflow-gap finding.

So the lowest rung does **not** secretly depend on the unbuilt piece (verified: P4 keeps `materialize()`
for content; only P5 needs the substrate). The ladder is honest.

## Verdicts

- **PL theory (`pl-theory-and-dsl.md`): GO-WITH-CONDITIONS.** Conditions before "proven": (C1) state
  LWW total-order tie-break `(Lamport, ReplicaId)` in B.1; (C2) downgrade B.1's `regroup` coverage or
  specify its join; (C3) specify the concurrent split‖rename join for generalized `remap`, or the D.2
  generalization is not CRDT-clean; (C4) relabel interior-edit case (a) from "Safe" to "parseable +
  convergent, semantics oracle-bounded." No fatal hole — the proofs are conditional and honestly scoped.
- **Refactor plan (`refactor-plan.md`): GO-WITH-CONDITIONS.** Conditions: (C5) P3 equivalence must be
  reinforced with randomized-history / property-based stress over the `_assign_lanes` tie-breaks — the
  golden corpus alone can pass while an unexercised weld path diverges; (C6) P3 must forbid `fold_ops`
  from reading `project.graph`, or the equivalence is hollow and breaks in P4; (C7) note that the P5
  substrate choice determines whether INV-3 (blame-from-log) survives. Sequencing and grounding are
  otherwise excellent.

**Shared remaining fatal-if-ignored hole:** the composition thesis (rung 2, the C-cases the corpus says
justify sgt) rests entirely on (a) an unbuilt structured multi-language block-integrity CRDT and (b) an
unmeasured bounded-repair floor M2. Neither doc lets the *shippable* work depend on these — good — but
**no artifact yet measures M1/M2**, and the 6/30 spike still measures the wrong number (conflict rate).
GO on both is conditional on: measure M1 (parseable-subset rate, predicted ≈1 under BI) and M2 (bounded-
repair reliability) *before* committing P5, with a pre-committed M2 floor as the kill criterion — and
keeping the recording-lens rung shippable as the fallback if M2 fails.
