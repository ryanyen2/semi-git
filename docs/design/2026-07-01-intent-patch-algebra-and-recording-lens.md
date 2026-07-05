---
date: 2026-07-01
topic: sgt as an intent-patch algebra recorded through a lens — a codebase is the concurrent aggregation of intent-patches; sgt records the decision process, git holds byte-faithful content, composition recomposes (never regenerates), and the LLM is quarantined to seam repair
status: design / ADR — proposed; extends and re-frames 2026-06-30-contracts-over-git-substrate.md (keeps its model, corrects its determinism assumption and its kill criterion)
builds-on:
  - docs/design/2026-06-30-contracts-over-git-substrate.md   # contracts, provides/requires, two-altitude validity
  - docs/design/2026-06-29-git-as-substrate.md                # git holds content; decisions map to commits
  - Mimram & Di Giusto, "A Categorical Theory of Patches" (Pijul)  # conflict-as-state, commutation from stable identity
  - Batory et al., feature algebra / AHEAD                    # program = composition of features; interaction deltas
  - Schaefer et al., Delta-Oriented Programming               # add/modify/remove deltas with application-order constraints
  - Fowler, Event Sourcing                                    # state = fold over an operation log; snapshots are cache
  - Foster et al., bidirectional transformations / lenses (Boomerang)  # get/put + round-trip law = "adjust sgt from the real code"
author-note: written by Claude as a synthesis of a 2026-06-30..07-01 design dialogue. [CALL] marks a judgment; [RISK] marks where it can fail. This doc changes two things in the contracts ADR: (1) sgt → codebase is NOT deterministic in the generative direction, and (2) conflict rate is not the crux — repair reliability is.
---

# Intent-patch algebra, recorded through a lens

## 0. Thesis in one paragraph

A codebase is the **concurrent aggregation of intent-patches**, not a snapshot on a timeline. sgt's
job is to **record the user's decision process** — decompose a prompt into a set of typed
intent-patches that carry NL intent bound to byte-faithful git commits — and to let any *subset* of
those patches be recomposed into a working tree. Patches carry no order; **application order is the
dependency topo-sort**, and dependency is derived deterministically from a `provides`/`requires`
footprint. A **branch is a set of patches**, not a pointer to a different snapshot; **revert is set
difference**, not an inverse commit. Content is byte-faithful git (recompose, never regenerate), so
composition is deterministic *even though generation is not*. The LLM is quarantined to two arrows:
decomposing a prompt into ops (front, optional, has a deterministic fallback) and repairing a
**semantic** break that the footprint provably cannot see (back, oracle-gated, seam-bounded). sgt
itself authors no code.

## 1. What changed from the contracts ADR

The contracts ADR (2026-06-30) is kept in full — three axes, `provides`/`requires`, validity
reported-not-vetoed. Two corrections:

- **[CALL] sgt → codebase is not deterministic in the generative direction.** A coding agent writes
  the code; the same intent yields different code. Determinism is recovered *only* by recording
  byte-faithful commits and **recomposing** them — never by regenerating from intent. The three
  layers must not be conflated: **intent** (patches) · **content** (commits) · **codebase** (a fold
  of a selected content subset). The non-deterministic arrow is `intent → content`; everything
  downstream of a captured commit is deterministic.
- **[CALL] Conflict rate is not the crux.** Structural (textual/positional) conflict is avoided by
  layering on git/Pijul-style stable identity. The residue that matters is the **semantic** break —
  footprints commute, the tree still won't build — which is exactly what the LLM exists to repair.
  The load-bearing measurement is therefore **repair reliability** (§9), not conflict frequency.

## 2. The intent-patch (the atom)

```text
Patch:
  id:        stable identity          # survives rename / move / reformat (Pijul's lesson)
  intent:    structured NL            # the decomposed prompt; a LABEL on reality, not a source
  commit:    [CommitSha]              # byte-faithful content the agent produced (empty ⇒ PLANNED)
  provides:  set[Symbol]  ┐ DERIVED from the commit's diff (deterministic ast def/use).
  requires:  set[Symbol]  ┘ Never asserted in the DSL — reality is the source.
  deps:      set[PatchId]            # DERIVED: q depends-on p ⟺ q.requires ∩ p.provides ≠ ∅
                                     #          ∨ same-symbol write-write. Escape hatch: `after`.
```

Intent and content are bound in one atom. The only non-derivable input is the **intent
decomposition**; footprint and dependency fall out of the real commit.

## 3. The algebra (deterministic, over the content footprint)

- `commute(p, q)  ⟺  footprint(p) ∩ footprint(q) = ∅`
- `depends(q, p)  ⟺  q.requires ∩ p.provides ≠ ∅  ∨  same-symbol write-write`
- `inverse(p)     =  set difference` — toggle `p` out of the selected set; dependents orphan
  (caught by the interface gate) or semantically break (routed to repair).

Consequences that dissolve long-standing warts:
- **No revert primitive.** "Revert a feature" = remove its patches from the in-force set; the
  dependency DAG names what must come off with it. This ends *revert-drops-the-create*
  (`memory/statement-distill-eid-lww.md` lineage) because a set has no create/extend ordering to
  overload.
- **Reorder is free and meaningless.** The set is unordered; only the dependency topo-sort matters.
- **Branch = a named subset** (a `Selection`), cheap; `branch b = a - p` is set algebra.
- **Cherry-pick = set union** of one patch into a chosen selection — no new primitive.

## 4. sgt is a lens, not a compiler [CALL]

The relationship between intent and code is a **bidirectional transformation** (bx / lens):

- **forward (`put`) — intent → code**, via the coding agent. *Generative, non-deterministic.*
  This is *planning*.
- **backward (`get`) — code → intent-graph**, via distill/reconcile. *Deterministic.* This is
  *recording* (primary).
- **consistency law** — after `reconcile`, the sgt tree must be faithful to the real code. The gap
  when it is not is **drift**, surfaced for the user to adjust (split a patch, relabel intent).

git is truth for content; the sgt tree is a derived overlay kept honest by reconciliation.
**Recording is primary; planning is the same grammar run forward with the `commit` slot empty.**

## 5. Three gates (two deterministic, one oracle)

| gate | question | mechanism | LLM |
|---|---|---|---|
| **interface** | an in-force `requires` with no in-force `provides`? | set logic over footprints | no |
| **drift** *(new)* | does the commit's real footprint match what the intent claimed? | distill vs intent | no |
| **build** | does the recomposed tree actually work? | oracle: build / typecheck / test | **semantic break → yes** |

No gate vetoes (contracts ADR R2). The **drift** gate is what makes "verify or adjust sgt based on
the real codebase" a first-class loop rather than an accident: an agent that adds an unrelated
refactor produces a footprint wider than its intent → drift flags it → the user splits/relabels.

## 6. The DSL

The DSL is the intermediate representation between a freeform prompt and the tree. Both a human and
the front-end LLM emit it; it elaborates deterministically to the DAG. `provides`/`requires` are
read-only (derived), never written by hand.

```text
contract auth  "user authentication"              # identity; usually inferred, may be declared

patch p1 on auth  "add bcrypt password hashing"    # atom: intent + (later) commit; footprint derived
patch p2 on auth  "add login rate-limiting"        #   touches auth.login too → depends-on p1 (derived)
patch p3          "add /metrics endpoint"          #   disjoint footprint → commutes with p1,p2
patch q  after p1 "…"                              # escape hatch: DECLARE a dep the footprint can't see

branch secure  = { p1, p2, p3 }                    # a branch is a SET, not a snapshot
branch minimal = secure - p2                       # set algebra
branch trial   = minimal + p_exp                   # cherry-pick = union

refine   auth "return 429 not 200 on lockout"      # delta-oriented modify → new patch on auth
rename   auth.login -> auth.authenticate           # identity-preserving remap (fixes move/rename)
move     auth/login.py::login -> auth/session.py   # identity-preserving relocation (see symbol-id doc)
decompose p1 into p1a "hashing", p1b "salting"     # split one patch's intent
merge    p1a p1b into p1 "password hashing"        # fold two patches into one (inverse of decompose)
select   auth@v2                                   # content axis: choose a version
reduce                                             # read-only minimal derivation (transitive reduction)

repair (secure) by integration                     # the ONLY LLM-authored statement:
   # oracle = build/test; region = orphaned symbols / conflict hunks;
   # forbidden = net-new top-level defs; emitted as its own patch, dep on {p1,p2,p3}.
```

- **Dependencies are derived by default, declarable when needed** (Darcs' hard lesson: automatic
  context deps + explicit user deps).
- **`repair` is a first-class patch** attributed to a synthetic `integration` contract (AHEAD's
  *interaction delta*), so the graph stays a complete explanation of the codebase and
  `secure - p1` drops p1's repairs automatically.

## 7. Pipeline — prompt/DSL → tree, and where the LLM lives

```text
 freeform prompt ─[LLM: intent decomposition, optional]→ DSL op list ─[deterministic]→ contract-DAG
   (fallback: 1 prompt = 1 patch)                                     • footprint from each commit
 hand-written DSL ───────────────────────────────────→ (same op list)• deps from overlap; place
                                                                                     │
                                                                       select a branch (a set)
                                                                                     │
                                                                       fold in dep order → git recompose
                                                                                     │
                                                                       gates: interface, drift (det) · build (oracle)
                                                                                     │
                                                                       semantic break → LLM repair → new `repair` patch
```

| zone | who | LLM |
|---|---|---|
| **front** — prompt → DSL ops | intent decomposition | LLM, *optional*; deterministic fallback. Authors *ops*, never code. |
| **generate** — intent → commit | the coding agent | the agent (not sgt); non-deterministic. |
| **middle** — DSL → DAG → recompose | footprint, deps, git compose | never. |
| **back** — semantic break → building tree | bounded repair | LLM, *required here only*; oracle-gated, seam-bounded, attributed. |

The one rule, restated precisely: **the front-end authors ops, the coding agent authors features,
the back-end repairs seams; sgt itself authors nothing.**

## 8. What this unlocks that plain git cannot

Toggle a mid-history feature off; reorder features; cherry-pick a feature onto a different base;
switch one feature's version while holding the rest — all well-defined because patches have stable
identity + derived dependency + set-valued branches, and because recompose operates on captured
byte-faithful content rather than regenerating.

## 8.5 Use cases → DSL & ID derivation

The grammar and the identity scheme are not designed in the abstract; they fall out of the space of
prompts a user actually types. Running example: one small web service that grows auth (hashing,
rate-limit, lockout), a metrics endpoint, and logging.

### 8.5.1 The prompt taxonomy

| # | prompt (freeform) | kind | plain git? | op it elaborates to |
|---|---|---|---|---|
| A1 | "add bcrypt password hashing" | additive | yes | `patch` |
| A2 | "add login rate-limiting" | additive, *depends on A1* | yes | `patch` (dep derived) |
| A3 | "add a /metrics endpoint" | additive, *disjoint* | yes | `patch` (commutes) |
| B1 | "return 429 not 200 on lockout" | **refine** | yes | `refine` |
| C1 | "build **without** rate-limiting" | **toggle off** | ✗ not cleanly | `branch = all - p2` |
| C2 | "cherry-pick metrics onto minimal" | **cherry-pick** | partial (conflicts) | `branch + p3` |
| C3 | "**revert** the whole auth feature" | **remove subgraph** | ✗ (revert drops creates) | `all - {auth patches}` |
| C4 | "switch metrics to v2" | **version select** | ✗ (manual branches) | `select metrics@v2` |
| C5 | "what if rate-limit came before hashing?" | **reorder** | ✗ (rebase, conflicts) | *no-op — set is unordered* |
| D1 | "rename `login` → `authenticate`" | **rename** | yes, but breaks our map | `rename` |
| D2 | "move auth helpers to `auth/utils.py`" | **move** | yes, but breaks our map | `move` |
| E1 | "add auth: hashing, rate-limit, lockout" | **bundled** → 3 patches | 1 commit | front-LLM `decompose` |
| E2 | "fix empty-pw bug **and** add logging" | **bundled, unrelated** → 2 | 1 commit | front-LLM split |
| F1 | "try it two ways: JWT and sessions" | **fork** | branches | two `branch`es on a base |

**[CALL] The rows that justify sgt are C1–C5** (and D1–D2, which make the C-cases *survive*
refactors). A/B/E are recording and decomposition; they are table stakes, not the thesis.

### 8.5.2 Three workflows

- **W1 — linear recording (the 90% path).** Prompt → agent codes → commit → distill footprint →
  sgt *emits* `patch …`. The DSL is output here, the `get` (code→graph) direction; the user never
  writes it.
- **W2 — retroactive reorganization.** The recording came out messy (E2 did two things). User runs
  `decompose` / `merge` / relabel / `rename` — editing the **intent layer only**; content commits
  don't move. This is where a hand-authored DSL earns its keep.
- **W3 — composition / what-if (the showcase).** `branch minimal = all - p2` → fold in dep order →
  git recompose in a scratch worktree → gates → semantic break → `repair`. The `put` direction run
  on a *subset*.

Elaboration trace for E1 (the case that must work):

```text
"add auth: hashing, rate-limit, lockout-after-5"
 └─[front LLM, optional]→  patch p1 on auth "bcrypt hashing"
                           patch p2 on auth "login rate-limiting"
                           patch p3 on auth "lockout after 5 tries"
 └─[agent generates]────→  commits (cardinality is a policy knob — see 8.5.5)
 └─[distill, det.]──────→  p1 provides{hash_pw}; p2 requires{login} provides{limiter};
                           p3 requires{login,limiter}
 └─[derive deps]────────→  p3 → p2 → p1   (topo order for the fold)
```

### 8.5.3 What the taxonomy demands of the DSL

The taxonomy ranks the ops and surfaces exactly one gap in §6:

- **Load-bearing (C):** `branch = set ± patch`, `select @version`. The product.
- **Identity-preserving (D):** `rename`, `move` — *not conveniences.* They are the mechanism that
  keeps the join key stable (§8.5.4); without them a refactor reads as delete+create and every
  downstream C-op breaks.
- **Recording / reorg:** `patch`, `refine`, `decompose`, and the **gap → `merge`** (fold two patches
  into one; the inverse of `decompose`). Added to §6.
- **Free / meaningless:** reorder (C5) needs *no op* — the set is unordered, so the answer to "what
  if the order were different" is "it already is order-independent."

### 8.5.4 What the taxonomy demands of ID — three tiers

"ID" is three problems with three answers:

| tier | what | derivation | must survive | design |
|---|---|---|---|---|
| **patch id** | branch=set, revert=remove, dep edges | **minted, opaque** | relabel, amend/rebase, split | `p_<short>` in commit trailer `Patch-Id:`; split mints new ids + provenance. |
| **symbol id** | the `provides`/`requires` join key | **canonical id + surface name** | rename, move, reformat (RISK-C) | surface `file::qualname` for display; canonical minted id is the real key; `rename`/`move` remap it. |
| **contract id** | grouping, `on auth` | minted or inferred | contract merge/split | minted; `on <name>` is a mutable lookup. |

The realization that ties D-cases to the algebra: **`rename`/`move` exist *because of* the
symbol-id problem.** The two laws are set ops over symbols, so their honesty is entirely a function
of a stable join key. A rename that isn't recorded as a `rename` patch makes `q.requires` stop
intersecting `p.provides` → the dependency silently vanishes → C-cases compose against a lie. The
full mechanism (canonical id, remap, detect-via-drift) is specified in
`docs/design/2026-07-01-symbol-identity-scheme.md`, which resolves RISK-C.

### 8.5.5 The knob that outranks syntax

**Decision→commit cardinality.** If a bundled prompt (E1) is *one* commit, footprint-splitting
inside a single commit becomes its own hard problem; if it's *three*, distillation is clean but
demands commit discipline agents may not have. [CALL] The likely answer is **many commits per
patch, one patch per intent**, with `decompose` reconciling the two — but this policy call ranks
*above* DSL syntax and should be fixed first.

## 9. Risks / where this can fail

- **[RISK-A] Semantic-repair reliability is the real bet.** When a recomposed subset builds-breaks
  semantically, can a *bounded* repair (seam-only, no net-new top-level defs) reach a building tree,
  and how often? This — not conflict frequency — is what the spike must measure. Kill criterion:
  pre-commit a floor on bounded-repair success below which the "not-closed algebra made total by
  repair" premise fails.
- **[RISK-B] Drift can be pervasive.** Non-deterministic agents routinely touch more than the intent
  claims. If drift is constant, the intent→content binding is weak and the graph is noisy. Measure
  drift rate; if high, tighten agent prompts or make patches coarser.
- **[RISK-C] Stable identity is load-bearing and unbuilt.** `file::qualname` is position-derived and
  breaks on move/rename (`memory/refactor-rename-distill-limitation.md`). Reorder/cherry-pick are
  only as clean as symbol identity is stable. **Now specified** in
  `docs/design/2026-07-01-symbol-identity-scheme.md`: a minted canonical id is the join key,
  `file::qualname` is demoted to a surface lookup, and `rename`/`move` patches remap it — created
  via the drift gate so identity maintenance is a confirm-loop, not a trusted heuristic. Still
  unbuilt, but no longer unspecified.
- **[RISK-D] The LLM boundary is asserted, not enforced.** "Repair the seam, never originate a
  feature" needs the deterministic check (reject net-new top-level defs) to be real, or R10 rots.

## 10. Decision

**Proposed.** Recording-primary, lens-shaped, set-valued branches, deterministic recompose over
byte-faithful content, LLM quarantined to front decomposition and back seam-repair. This supersedes
the contracts ADR's determinism assumption (§1) and re-points the spike from conflict rate to
**bounded-repair reliability** (§9). No machinery is deleted; the algebra and lens are additive over
the current store until the repair-reliability number is in.

## Open questions

- **Drift resolution policy.** When the drift gate fires, is the default to auto-split the patch, to
  relabel intent, or to ask? Shapes the recording UX.
- **Identity scheme.** ~~Content-hash of a symbol's body, a stable minted id in a trailer, or a
  Pijul-style change-hash + position?~~ **Resolved** in `docs/design/2026-07-01-symbol-identity-scheme.md`:
  minted canonical id in a commit trailer + sidecar, `file::qualname` as surface lookup, remapped by
  `rename`/`move`. Remaining sub-question: symbol *split* (one id spawning two) needs a provenance
  edge — deferred there under §7.
- **Repair attribution across toggles.** A repair depends on the pair it reconciles; does toggling
  one side retire the repair, or degrade it to a note? (Contracts ADR left this open too.)
- **Bounded-repair enforceability.** Is "no net-new top-level defs" the right deterministic fence,
  or too strict (a repair may legitimately need a small adapter)? Decide before implementing §7 back.
