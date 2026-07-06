---
date: 2026-07-06
topic: the operation-ideal kernel — one algebra instead of tiers. A codebase state is an ORDER IDEAL of a mined DAG of semantic operations over stable symbols; a feature is a node in a hierarchical partition of those operations. Every user verb is either an exact ideal edit, an exact tree edit, or an explicit agent-authored rewrite op — never an approximation, never a gated "rung".
status: design / ADR — PROPOSED. Replaces the tier/ladder framing of 2026-07-01 and the flat-lane framing of the experiments; unifies the 07-01 algebra and the 07-02 mining thesis into a single kernel.
supersedes:
  - "the fallback ladder — rung 0 / rung 1 / rung 2" (2026-07-01 SYNTHESIS §4)        # replaced: no tiers; the algebra is total by construction because only well-formed selections (ideals) are expressible
  - "5 authored primitives as the semantic source of truth" (2026-07-01 SYNTHESIS §3)  # replaced: ops are MINED (07-02 wins); the authored surface shrinks to plan / pin / regroup / after
  - "structured content CRDT gated on M1/M2" (2026-07-01 SYNTHESIS §5, P6)             # dissolved: same-symbol ops never commute (they chain), so subset materialization needs no text CRDT; only slot-order and imports need tiny anchored folds
  - "EICO confluence gate + quarantine as the central mutation gate" (v1 core)          # replaced: the ideal-closure law makes ill-formed states unrepresentable; the build/test oracle remains the sole semantic gate
builds-on:
  - docs/design/2026-07-02-patch-first-clustering-lens.md   # keeps: mined patch stream as truth, determinism boundary, identity-decoupled-from-detection, LLM confined to label/tie-break, oracle as ground truth
  - docs/design/2026-07-01-symbol-identity-scheme.md        # keeps: minted opaque symbol-id + provenance relation — it is the spine every chain hangs on
  - experiments/patch_clustering/                            # promotes: mine.py, identity_match.py, leiden_cluster.py, hierarchy.py, operations.py are the kernel's embryo
author-note: written by Claude after a full-corpus read, answering the owner's ask verbatim — "thinking about the history of codebases as a composable DAG of operations", no tier-based approach, hierarchical features (<10 roots), and verbs like "cherry-pick some operations to another feature lens, merge features, split features, revert some of the operations". [CALL] marks a judgment; [BET] marks a claim only measurement closes.
---

# sgt: the operation-ideal kernel

## 0. Why this document exists

The corpus converged on all the right *ingredients* — mined symbol-patches (07-02), a
commutation algebra (07-01), hierarchical lanes (`experiments/patch_clustering/hierarchy.py`),
typed operations (`operations.py`) — but assembled them as a *ladder of fallbacks* and a *pile of
coexisting mechanisms* (effect log + Node store + EICO gate + decisions fold + entity graph +
experiment pipeline). That is the "mixture of everything." This doc replaces the mixture with
**one kernel object and one law**, from which every user-facing verb is derived. There are no
tiers: instead of gating dangerous selections behind measurements, the algebra is shaped so that
**only well-defined selections are expressible**.

## 1. The thesis, in one paragraph

A codebase's history is a **DAG of semantic operations** `(O, ≤)` mined deterministically from
real edits. Each operation advances a set of **stable symbols** (functions/classes/methods with
minted, rename-surviving ids) from one version to the next, carrying the verbatim after-image of
each symbol it touches. Ops on the *same* symbol never commute — they form that symbol's
**version chain**; ops on disjoint symbols commute freely unless a reference dependency orders
them. **A codebase state is an order ideal** (a downward-closed subset) **of this DAG**, and
materialization is a total, deterministic, byte-faithful-at-entity-granularity fold: each symbol
renders as the after-image of its maximal in-ideal op. Revert is subtracting an up-set;
cherry-pick is adding a down-set; reorder is a no-op; a branch is just a named ideal; the only
conflict that can exist is a fork in one symbol's chain, and it is resolved by an explicit,
agent-authored **merge op**. On top of this substrate sits one orthogonal structure: a
**hierarchical partition of the operations into features** (< 10 roots, nested), maintained by
constrained incremental clustering with stable identity — pure metadata, so feature
merge/split/retag can never break code. sgt authors no code; the build/test oracle remains the
only semantic ground truth.

## 2. The two axes, cleanly separated

Everything in sgt is one of exactly two structures. The mixture came from letting them blur.

| axis | object | who writes it | can it break code? |
|---|---|---|---|
| **operation axis** (what happened) | the op DAG `(O, ≤)` + per-symbol chains | mined from edits; append-only | materialization is exact; only the *oracle* judges semantics |
| **feature axis** (how it's organized) | a hierarchical partition `T` of `O` | clustering + user pins + LLM labels | **never** — retagging ops moves no content |

The 07-02 doc's determinism boundary survives intact: the operation axis is deterministic; the
feature axis is *stable, not deterministic*, and nothing on the operation axis ever depends on it.
A user verb like "revert the retry feature" resolves the feature to its op-set (feature axis) and
then executes on the op DAG (operation axis) — the clustering's opinion is never in the
materialization path.

## 3. The kernel — objects and the one law

### 3.1 Symbols (Σ)
A symbol is a minted, opaque id for a code entity (function / class / method / module-level
residue block), joined across commits by the tiered matcher (exact surface → content hash →
structural hash → fuzzy, already built in `experiments/patch_clustering/identity_match.py`), with
a provenance relation for split/merge (1:n, n:1). Rename and move are non-events for identity.
Two **pseudo-symbols per file** complete the space (§3.5): its *layout* (slot order) and its
*import set*. Nothing else is versioned.

### 3.2 Operations (O)
An operation is the atom of history:

```
Op {
  id            content-addressed hash (payload + parents)         # collision-free, replica-free
  footprint     { sym → (before_version → after_version) }          # which chains it advances
  images        { sym → verbatim after-bytes | ⊥ (removed) }        # whole-entity, parseable by construction
  requires      { sym-refs its images use }                         # mined def/use — drives ≤ and imports
  kind          add | extend | rework | prune | move | merge        # derived from footprint shape, never stored free-text
  provenance    { git shas, session/plan id, author }               # commits are WITNESSES, not structure
  intent?       label + rationale (LLM/plan/human; advisory)        # the only non-derivable field
}
```

Ops are **mined, not authored** (07-02 wins over 07-01): a git commit or a working-tree delta is
diffed at entity granularity, then **untangled** into ops by def-use connectivity over the changed
symbols (ClusterChanges-style) — so one tangled commit becomes several ops, and `commit ↔ op` is
many-to-many, mediated by provenance. A *planned* op is the same object with `images = ∅` (hollow)
— plan and history live in one DAG (§7).

### 3.3 The order (≤)
`≤` is the transitive closure of three edge sources, all deterministic:

1. **chain edges** — same symbol: `before_version → after_version` succession. Ops touching one
   symbol are totally ordered along its chain. *This is the move that dissolves the CRDT problem:
   same-symbol ops never commute, so no merge-of-text is ever computed by sgt.*
2. **reference edges** — op B's images use a symbol whose defining/advancing op is A ⇒ `A ≤ B`.
   Mined def/use is unsound in Python (~70 % recall, PyCG) — accepted: the oracle backstops, and
3. **declared edges** — `sgt after <op> <op>`: the human/agent escape hatch for edges the
   analyzer cannot see (registries, config coupling, dynamic dispatch).

### 3.4 States are ideals — the one law
> **THE LAW.** A codebase state is a downward-closed set `I ⊆ O` in which every symbol's chain
> restricted to `I` has a unique maximal element. `code(I)` = splice, for each symbol, the
> after-image of that maximal op. Every sgt verb must map ideals to ideals.

Consequences, each of which used to be a separate mechanism:

- `code(I)` is **total and deterministic** — every ideal materializes; there is no "quarantine",
  no confluence gate, no gated rung. Ill-formed selections are *unrepresentable*, not vetoed.
- **revert(X)** = `I \ ↑X` (subtract the up-set: X and everything that builds on it). Exact,
  lossless, previewable. The up-set *is* the old "dependency closure", derived from ≤.
- **restore / cherry-pick(X)** = `I ∪ ↓X` (add the down-set: X and everything it builds on).
- **reorder** = no-op (a state is a set); **branch** = a named ideal; **diff of two states** =
  symmetric set difference of ideals — semantic diff for free.
- **version-select** = truncate one symbol's chain inside `I` (pin an older op as maximal) —
  "try the previous take of `slugify` while keeping everything else".
- **blame** = `sym → max-op-in-I → feature`. One lookup, exact at entity granularity.
- The only possible conflict is **chain divergence**: two ops with the same `before_version`
  (concurrent edits, or restoring an alternative). An ideal containing both tips is invalid until
  a **merge op** (both tips as parents, agent-authored image, oracle-gated) or a **pin** (drop one
  tip from `I`) closes it. This is the honest form of the 3-way-merge impossibility: surfaced as
  an explicit fork in one chain, never a silent interleave.

### 3.5 Splicing files (the residue, handled once)
Whole-entity images make every fold **parseable by construction** — the 07-01 "subset
unparseable" crisis cannot arise. Two file-level facts remain, each a tiny anchored fold, *not* a
text CRDT:

- **layout**: a file's top-level slot order. Ops record anchored insertions ("`bar` after `foo`");
  the fold linearizes anchors deterministically. Additions at different anchors commute — so two
  features adding functions to one file stay independently revertable.
- **imports**: derived, not versioned — a file's import block is the union of `requires` of the
  in-ideal symbols it hosts. Revert a feature and its imports vanish with it, by construction.

Module-level statement groups (constants, `__main__` blocks) are residue symbols with ordinary
chains. [CALL] intra-entity granularity is deliberately NOT modeled: two logical changes landed in
one op on one symbol cannot be separated *algebraically* later — the answer is `sgt split-op`,
which asks the agent to author the intermediate image (an explicit rewrite, oracle-gated), not a
finer patch algebra. **Exact by algebra, or explicit by rewrite — never approximate.**

## 4. The feature lens — a hierarchy of < 10, not a wall of 40

### 4.1 What a feature is
A feature is a node in a tree `T` whose leaves partition **the operations** (not the symbols —
symbols move between features exactly when the ops that advance them are retagged; a symbol's
*current* feature is the feature of its maximal in-ideal op). A feature's symbol-set, activity,
status, and dependencies are all *derived* projections of its op-set at the current ideal.

### 4.2 One maintenance algorithm (no cold-start/incremental split)
Promote the experiment pipeline into the kernel with three changes:

1. **Cluster ops via their footprints** over the fused coupling graph (structural ⊕ untangled
   co-change, hub-stripped) — the 07-02 recipe, unchanged.
2. **Target-arity recursion** replaces fixed resolution: at each level, binary-search the CPM
   resolution γ until the partition has 5–9 children (the "<10 roots" requirement is a *property
   of the algorithm*, not a hope), then recurse into each child's induced subgraph; stop when a
   node is below `MAX_LEAF` or a split yields one-dominant-child-plus-dust (the experiment's
   STOP-SPLIT / NO-ORPHAN rules survive verbatim).
3. **Identity by member-overlap matching across runs** (Greene), so re-clustering renames nothing:
   `birth / death / merge / split / continuation` are named, reviewable events carrying labels and
   rationale forward. The detector may shuffle; identity does not.

### 4.3 User curation = constraints, permanently
Every manual regroup is recorded as a **pin** — a must-link / cannot-link / "these ops are feature
F" constraint the clusterer must respect on every future run. The tree is therefore *jointly
authored*: clustering proposes, pins dispose, and a pinned region never flickers. Plans enter the
same way — a plan's decomposition is a set of must-link priors (07-02 W1), applied at the moment
intent is richest and then corrected by reality.

### 4.4 Feature verbs are metadata, therefore always safe
`merge F G` (union op-sets, keep survivor id), `split F` (partition op-set — clusterer proposes
the cut, user confirms), `rename F`, `move <ops> to G` (the "cherry-pick onto another feature
lens" verb: retag, one pin written). None touch content; all are instant and reversible. The
*content-level* cousins compose from §3: "carry feature F into branch-ideal J" = `J ∪ ↓ops(F)`;
"transplant F's *behavior pattern* onto G" is not algebra — it drafts hollow planned ops under G
for the agent to implement (§7).

## 5. The verb surface — everything a user does, derived

| user says | kernel action | exact? |
|---|---|---|
| "what is this codebase?" | render `T` roots (5–9 nodes), drill down | — |
| "remove the retry logic from sync" | resolve ops → preview `I \ ↑X` diff → oracle → commit | ✔ algebra |
| "bring back the old slugify" | version-select: pin older op maximal on that chain | ✔ algebra |
| "cherry-pick auth-rate-limit into release" | `J ∪ ↓ops(auth-rate-limit)` | ✔ algebra |
| "these two features are really one" | feature merge (metadata + pin) | ✔ metadata |
| "this feature is doing two jobs" | feature split (clusterer proposes cut; pin) | ✔ metadata |
| "move these 3 ops under caching" | retag + pin | ✔ metadata |
| "undo half of what that op did" | `split-op`: agent authors intermediate image, oracle-gated | ✖ explicit rewrite |
| "make G cache like F does" | hollow planned ops under G; agent implements; checkpoint matches | ✖ explicit rewrite |
| "merge our concurrent work" | union op-sets; forks surface per chain; pin or merge-op each | ✔ surfaced, ✖ resolution |

The pattern: **read and reorganize are free; exact plug-in/out is algebra; anything that needs new
bytes is an explicit, attributed, oracle-gated rewrite op.** No verb is "gated on M2".

## 6. Bidirectionality — the lens, stated as laws

`get` (record): edits → entity diff → untangle → ops appended → identity matched → tree updated →
labels for dirty nodes only. Total, deterministic given pins, works with **zero cooperation**
(any git history mines cold — adoption is `sgt init` on an existing repo).

`put` (materialize): ideal edit → `code(I)` → working tree → git commit (witness, with op-id
trailers) → oracle.

The round-trip laws are the *test suite*, not prose:
- **put∘get**: mining a tree that `code(I)` just wrote yields zero new ops (fixed point).
- **get∘put**: `code(get(edits))` reproduces the edited bytes at entity granularity (formatting
  inside untouched entities is byte-exact because images are verbatim — the `ast.unparse`
  formatting-loss bug class dies here).
- **idempotence / locality / coverage** invariants from 07-02 §6.3 carry over unchanged.

## 7. The agentic loop — plan mode is a first-class citizen

- **Plan** (Claude Code plan mode, via MCP or hooks): sgt receives the plan text, drafts **hollow
  ops** (footprint predicted, images empty) under predicted features. The user reviews the plan
  *as graph nodes* — this is where "notice what changes might happen" lives.
- **Implement**: the agent codes with its own tools. Optional session hooks stream working-tree
  checkpoints; without hooks, the next commit is the signal (graceful degrade, 07-02 [CALL] kept).
- **Checkpoint**: mined real ops are **matched to hollow ops** by footprint overlap — fulfilled
  plans solidify (keeping their rationale, the one non-derivable thing); unpredicted ops surface
  as *drift*, reviewable and retaggable in one gesture. Unfulfilled hollow ops remain as visible
  planned work.
- **Iterate / undo**: because every op is in the DAG within seconds of landing, "actually, drop
  the telemetry half of that" is a revert of two ops, not an archaeology session.

The LLM's confinement is unchanged from 07-02: label + tie-break in the dirty region, decompose
plans, author merge/split-op images **only** as explicit rewrite ops — never in the fold, never in
the order, never in identity.

## 8. Git — substrate and witness, nothing more

- Git remains the byte store, transport, and audit log. Every `put` commits; trailers map commit ↔
  ops; `.sgt/` holds the op store (content-addressed, append-only), pins, and the tree.
- Any foreign commit (a teammate without sgt, a rebase, a hotfix on GitHub) is just **mined** on
  next contact — sgt can never be locked out of its own repo, and adoption ⊂ sync (one code path).
- Collaboration = **set union of op stores** (content-addressed ids make this trivial and
  replica-free) + tree reconciliation by identity matching + pins. Footprint-disjoint work merges
  with zero interaction; same-symbol concurrency surfaces as chain forks (§3.4). The old
  replica/Lamport merge engine is retired.

## 9. What this deletes (the honest list)

| retired | replaced by |
|---|---|
| typed effect log (`add_def`/`replace_def`/…) as source of truth | mined ops with verbatim images |
| `Node` store + statuses + EICO commute gate + quarantine | the ideal law (§3.4) + oracle |
| `merge/engine.py` replica log, Lamport clocks | content-addressed op union + chain forks |
| decisions fold (`build_decisions`) + lane assignment | feature tree over ops |
| statement-slot LWW / `build_statement_seq` | whole-entity chains (+ `split-op` for finer cuts) |
| the fallback ladder & M1/M2 gating | totality by construction; bets move to §11 |
| flat 40-lane clustering | target-arity recursive hierarchy (§4.2) |

Kept and promoted: `entities/extract.py` (+ residue symbols), `entities/graph.py`,
`store/gitbind.py`, the whole `experiments/patch_clustering/` pipeline, the oracle discipline, the
`sgt.api` one-projection rule, OKLCH identity-color, MCP surface.

## 10. The build plan — one kernel, five phases (each shippable, none a "tier")

**P0 — Freeze the ground (≈ days).** Golden corpus: this repo's own history + 2 external repos.
Property harness for the round-trip laws (§6) — written *first*, red. Promote
`mine.py`/`identity_match.py` into `sgt/core/` with residue symbols + untangling; measure identity
churn and untangle quality on the corpus.

**P1 — The kernel (the heart).** `sgt/core/{op.py, order.py, ideal.py, fold.py}`: content-addressed
op store; chain + reference + declared edges; ideal validity; `code(I)` splice with layout/import
folds. `sgt log` (op DAG), `sgt state` (current ideal), `sgt diff <ideal> <ideal>`. Round-trip
laws go green here. *No feature tree yet — the kernel is useful naked.*

**P2 — The verbs.** `revert` / `restore` / `cherry-pick` / `pin` / `after` / `merge-op` as ideal
edits with `--emit` previews; oracle hook on every materialization; chain-fork surfacing. Delete
the EICO gate, quarantine, Node store, effect log **in this phase**, flipping the CLI onto the
kernel behind the golden corpus.

**P3 — The lens.** Promote `hierarchy.py`+`operations.py`+`label.py`: op-footprint clustering,
target-arity recursion, Greene identity, pins. Feature verbs (`merge`/`split`/`rename`/`move`).
`sgt map` replaces `sgt graph`; blame/status derive from the kernel. Surfaces (TUI, VS Code) read
the new `sgt.api` projection — same schema discipline, new nouns.

**P4 — The loop.** Hollow ops; plan-mode intake (MCP + hooks); checkpoint-matching with drift
review; `split-op`; rationale carry. This is the Claude-Code-native workflow shipping end-to-end.

**P5 — Together.** Op-store union sync; tree reconciliation; fork-resolution UX. (Last because
content-addressed ids make it mostly mechanical once P1 is right.)

## 11. What is decided vs. what is a bet

**Decided (construction, not measurement):** ideals always materialize parseably; revert/restore/
cherry-pick are exact; feature verbs cannot break code; reorder is a no-op; conflicts only as
chain forks; imports derived; < 10 roots by target-arity.

**Bets (the §10 P0 harness measures all four before P3):**
- **[BET-A] Untangling quality.** Def-use partitioning splits real tangled commits into ops humans
  agree with. *Metric:* precision vs. hand-untangled sample; miss rate folds into a "retag" cost.
- **[BET-B] Reference-edge recall is livable.** Up-sets (revert closure) rarely miss real
  dependents; the oracle catches the rest. *Metric:* oracle-caught-miss rate ≤ ~10 %, with
  `after` as the manual patch.
- **[BET-C] Hierarchy matches human feature boundaries** at every level, not just leaves.
  *Metric:* MoJoFM vs. hand-labeled maps on 3 repos; pin-rate as the UX proxy.
- **[BET-D] Chain granularity is right.** Whole-entity versioning rarely forces `split-op`.
  *Metric:* fraction of revert requests needing sub-entity cuts.

If BET-A/B fail, the kernel still ships with coarser ops and louder oracle use — the *algebra*
is unaffected. If BET-C fails, the tree leans harder on pins and plans — the *kernel* is
unaffected. That is the difference between a ladder of products and one product with measured
knobs.

---

*One object (the op DAG), one law (states are ideals), one overlay (the feature tree), one gate
(the oracle), one escape hatch (explicit rewrite ops). Everything the corpus reached for —
patch-theory commutation, bidirectional lenses, CRDT-free convergence, hierarchical maps,
plan-mode awareness — falls out of those five sentences.*
