---
date: 2026-07-10
topic: sgt as the version-control system the user actually lives in — porcelain over git, branch-as-selection, worktree-free multi-agent, the sync protocol, the non-code substrate, and human-first provenance. Not new kernel; the lived UX derived from the existing op-ideal + feature-tree kernel.
status: design / ADR — PROPOSED. Product-shape doc, additive. Changes no kernel object; everything here is a surface, a metadata field (excluded from op ids), or a porcelain routing rule.
builds-on:
  - docs/design/2026-07-06-operation-ideal-kernel.md        # the substrate: state = order ideal of an op DAG; branch = named ideal; conflict = same-symbol fork; feature tree = orthogonal metadata partition
  - docs/design/2026-07-01-symbol-identity-scheme.md         # minted symbol-id + provenance relation R — the join key branches/sync ride on
  - docs/design/2026-07-01-SYNTHESIS-unified-direction.md    # collaboration is a CRDT over metadata, not content; footprint-disjoint work composes, same-symbol concurrency surfaces divergence
author-note: written by Claude answering the owner's six points verbatim (porcelain + `sgt git` passthrough; branch-as-selection made easy despite low-level coupling; worktree-free multi-agent; the sync protocol as the core; the non-code substrate; provenance/machine-surface with HUMAN perception as the priority). Explicit non-goal: bolting the "rebuild-GitHub-for-agents" ideas on as a Frankenstein. Each idea below is shown to be already implied by the kernel, then named. [CALL] marks a judgment; [BET] a claim only measurement closes; [UNKNOWN] a genuine open question.
---

# sgt as a version-control system

## 0. Why this document exists

U1–U12 built the *algebra* — the op DAG, the order-ideal fold, the feature tree, the oracle. What
it never designed is the **lived experience**: what a developer types, and how sgt and git coexist
on one machine without the developer maintaining two mental models and two histories by hand. Today
sgt is a *sidecar* — git for everything, sgt for a semantic layer on the side — which is the worst
posture for adoption because "keep both coherent" is a permanent tax the user pays.

The industry is converging on the same realization from the other direction (Cursor Origin, Zed,
GitButler, GitLab×Anthropic all published variants of "git's data model was built for humans, not
agents" in mid-2026). The temptation is to read their feature lists and bolt them onto sgt. **That
is the thing this doc refuses to do.** sgt already has the substrate those products are
*speculating toward* — a semantic op store, symbol-stable blame, a feature partition, an oracle
keyed to an exact op-set. The job is not to import their features; it is to name the surface that
the substrate we already built implies.

The organizing principle for the whole doc:

> **Everything the user does is a rendering of, or an edit to, two objects that already exist: the
> op-ideal (what's selected) and the feature tree (how it's organized). We add surfaces and
> metadata. We do not add kernel objects.**

## 1. Porcelain over git — the user lives in `sgt`

**Decision [CALL]:** sgt is the *primary* command surface. A user should be able to do a full
feature's worth of work — and hand a plain git repo to a teammate who's never heard of sgt —
without typing `git` directly. git stays underneath as the interop format and the escape hatch,
never hidden.

The escape hatch is explicit and total: **`sgt git <args...>` passes straight through to git**
(the `jj git` / `gh` precedent). Anything sgt does not natively wrap is still one keystroke away,
inside the tool the user already has open. The user never has to leave sgt to reach git; they just
prefix.

The routing rule that keeps this honest — **sgt owns the working-tree write path.** git commands
split into three classes:

| class | examples | routing | why |
|---|---|---|---|
| **tree-mutating** | `checkout`, `reset`, `merge`, `rebase`, `stash pop`, `cherry-pick` | **native sgt verb** (`sgt select`/`revert`/`restore`/`sync`…) — the raw git form is *intercepted and re-routed*, or refused with the sgt equivalent named | a raw `git checkout` rewrites the tree behind sgt's back → the op store drifts. sgt must be the one that materializes. |
| **read / inspect** | `log`, `show`, `diff`, `status`, `blame` | native sgt verb is the default (semantic); `sgt git log` gives the raw git view verbatim | sgt's versions are *better* here (semantic, symbol-stable) but the raw view is a legitimate escape |
| **plumbing / config** | `remote add`, `config`, `tag`, `bisect`, `reflog`, `fsck` | **passthrough** via `sgt git …` (no native wrap needed) | these don't mutate the tree in ways sgt tracks; passthrough is safe and cheap |

The out-of-band detector already built into `gitbind` (commits with no `Sgt-Node-Id`/`Sgt-Op`
trailer are flagged so the graph never silently drifts) is the safety net for the case where a user
*does* reach around sgt with a raw tree-mutating git command: sgt notices on next contact and
offers to re-mine rather than corrupting the ideal. [CALL] We keep that net; the porcelain reduces
how often it fires, it doesn't replace it.

**Non-goal:** re-implementing git. Passthrough is the answer for the long tail. We wrap only the
verbs where the sgt-native version is genuinely different (tree materialization, semantic
inspection, branch-as-selection, sync).

## 2. A branch is a named selection — and selection must be *easy*

The kernel already says: **a branch is a named order-ideal** — a downward-closed, fork-free set of
ops (`docs/design/2026-07-06 §1`). Not a commit pointer; a *selection*. This is the right model and
it gives us branch/switch/diff as set operations for free.

The owner's real concern is the one that matters: **selecting is hard today because the
dependencies are low-level and features are coupled.** An ideal must be downward-closed, so
selecting "feature X" drags in everything X's ops `requires` — and because `requires` edges are
fine-grained reference dependencies, the induced closure can be large and surprising. If selection
means "hand-pick ops from a DAG of thousands," no human will ever do it.

**Decision [CALL]: the user selects at the feature-tree layer, never the raw op layer. sgt computes
the closure and *explains* it.** Concretely:

1. **The unit of selection is a feature node (`F<n>`), not an op.** The feature tree (< 10 roots,
   nested) is the selection UI. "Give me a branch with F3 and F7" is the gesture.
2. **sgt computes the downward-closure automatically** over `requires` edges (the existing
   `order._grounded` logic) and **materializes it.** The user never enumerates ops.
3. **sgt reports the *induced* set and separates two kinds of coupling:**
   - **True semantic coupling** — a real `requires` reference edge. You literally cannot have the
     caller without the callee. This inclusion is honest; surface it plainly ("selecting F3 also
     pulled in 4 ops from F1 because `handler` calls `validate`").
   - **Incidental coupling** — two ops share a feature only because clustering (co-change / same
     file / same scope) grouped them, with *no* `requires` edge between them. This must **not**
     force inclusion. Closure follows `requires` only, never clustering edges.
4. **"Why is this op in my selection?" is a first-class query** — a trace back through the
   `requires` chain to the feature the user actually asked for. This is the thing that makes a
   large closure legible instead of scary.
5. **When a closure is *too* coupled to be useful, that is a signal, not a dead end.** An
   over-large induced set means either the feature decomposition is wrong (route to `split`) or a
   dependency is too coarse / a symbol identity is wrong (route to `identity split`, or an
   agent-authored rewrite op that breaks the reference). The UX turns coupling into an actionable
   diagnosis.

[BET] The feature layer is a coarse-enough handle that closure sizes become human-scale. If it
isn't — if real features are so entangled that every selection pulls in half the repo — that is
itself the most important finding, and it says the clustering (BET-C, currently 63.9% MoJoFM) needs
to improve before branch-as-selection is usable. **This is measurable and should be measured before
we ship the selection UI.**

## 3. Multi-agent without worktrees

The industry uses worktrees because concurrent agents editing shared files collide at the *text*
level. sgt's substrate removes the reason: **work is tracked as ops on stable symbols, and
footprint-disjoint ops compose with no conflict by construction** (`is_fork_free` over the union;
proven in the unified-direction synthesis). The only thing that can conflict is a **fork in one
symbol's chain** — two ops advancing the same `(symbol, before_version)` — and that is surfaced
explicitly and resolved by an agent-authored **`merge-op`**, never by silent LWW and never by
`<<<<<<<` text markers.

So the merge *mechanism* is op-set union + fork-surfacing, not a three-way text merge. That is our
own merge algorithm, and it is conflict-free wherever the work is disjoint. **Decision [CALL]:
worktrees are not required for correctness.** Two agents produce two op-sets; sgt unions them; the
result is a well-formed ideal unless they touched the same symbol, in which case the fork is a first
-class object with a named remedy.

**The one honest caveat, stated plainly [CALL]:** an agent still needs *somewhere* to write scratch
bytes while it is mid-edit, before its work is distilled into ops. Two options, and we should pick
deliberately rather than let it happen by accident:

- **(a) ephemeral materialization per session** — each agent's session materializes *its own*
  ideal into a scratch tree, edits there, and its diff is distilled back to ops. This is
  worktree-*shaped* but it is an implementation detail of a session, not the merge mechanism.
- **(b) serialized distill on one tree** — agents share a tree but their edits are distilled one at
  a time; correctness rides on the op-set union regardless of interleaving.

[CALL] Prefer (a) for genuine parallel agents (clean isolation, and the sgt harness already exposes
`isolation: "worktree"` for exactly this), (b) for a single interactive session. Either way the
*result* that merges is the op-set, so worktrees — if used at all — are a private isolation
convenience, not a load-bearing part of the model. This is the precise sense in which the owner is
right that "we don't need worktrees": we don't need them *for merge*. We may still use them *for
scratch isolation*, and that's fine.

## 4. The collaboration / sync protocol — the core of a VCS

This is the load-bearing section, because a VCS that only works in one repo is not a VCS. We want
**both** async collaboration (teammates combine work after the fact) **and** synchronous
collaboration (multiple agents/humans on one codebase at once).

The deep structural win, stated once: **the op store is a grow-only set of immutable,
content-addressed ops. Union of two op stores is therefore conflict-free by construction — it *is*
a CRDT (a G-Set) at the content layer, for free.** Everything else is a small, bounded reconcile on
top. This is why collaboration is not a bolt-on: it is what a content-addressed immutable op store
*already is*.

The layers, and how each reconciles:

| layer | object | CRDT type | how it reconciles |
|---|---|---|---|
| **content** | the op store | grow-only set (G-Set) | pure union; immutable + content-addressed → automatic, conflict-free |
| **structure** | per-symbol version chains | — | union is clean *unless* two chains fork the same `(symbol, before)` → the **only** true conflict; surfaced, resolved by `merge-op`/`pin` |
| **organization** | feature tree, pins, labels | LWW / OR-set over metadata | already reconciled by `sgt sync` (U15) — retagging moves no content, so it can never break code |
| **selection** | each participant's HEAD ideal | per-participant, local | your HEAD is your chosen ideal; nobody's selection is authoritative over anyone else's |

**Async** is already partly built: `sgt sync [remote] [branch]` (U15) fetches a teammate's work,
unions the op store, reconciles pins / declared edges / the feature tree, and surfaces any
same-symbol fork with the `merge-op`/`pin` remedy — instead of a textual merge. [CALL] This is the
fetch/merge analog and it rides git transport (ops live in commits + trailers, so a plain `git
push`/`fetch` moves them). Keep it. The async story is *mostly done*; what remains is UX polish and
the fork-resolution surface (§7).

**Synchronous** (multiple agents live on one codebase) is the new part, and it is the *same
substrate at lower latency*:

- Minting an op = appending to a shared grow-only op log. Because the log is a G-Set, a live
  append from any participant is safe to apply in any order.
- Each participant re-materializes their own HEAD ideal as new ops arrive. Footprint-disjoint
  arrivals just extend the ideal; a same-symbol arrival raises a fork the participant sees
  immediately rather than at merge time.
- Feature-tree edits (label/pin/regroup) stream as metadata CRDT ops and converge.

So live sync = **a shared op log with pub/sub + local re-materialization + immediate fork
surfacing.** No new conflict model; the conflict model is the one we already have (same-symbol
fork), just observed sooner. [UNKNOWN — the genuine open questions, not to be hand-waved]:

- **Transport.** Async rides git remotes. Live sync needs a low-latency append/broadcast channel.
  Build our own tiny relay? Ride an existing pub/sub? This is unbuilt and should stay explicitly
  deferred until async + the fork-resolution UX are solid.
- **Live fork UX.** When two agents fork a symbol *in real time*, who is prompted, when, and does
  work block or continue on divergent tips until someone lands a `merge-op`? The kernel permits
  divergent tips; the *experience* of resolving them live is undesigned.
- **Ordering / causality metadata.** A G-Set converges on *membership* but not on *causal order*
  for display. We likely need a per-op logical clock (the shelved-substrate notes gestured at
  this) so a live timeline renders deterministically. `Date.now()` is unavailable in the kernel by
  design — causality must be logical, not wall-clock.

## 5. The non-code substrate — gitignore, tmp, weird files

The kernel is AST-native for Python/TS *entities* and treats everything else as byte-faithful
**residue** (whole-file images, proven by the 2026-07-08 byte-fidelity audit). A real repo is
*mostly* not-entities — lockfiles, configs, JSON, binaries, generated output, junk. We need an
explicit, configurable boundary rather than letting mining decide implicitly.

**Decision [CALL]: three tiers of file treatment, made explicit.**

1. **Entity files** — parsed into symbol ops (functions/classes/methods). Full semantic
   versioning, feature clustering, sub-file blame. (Python/TS today; more grammars later.)
2. **Opaque tracked files** — configs, lockfiles, small binaries, generated artifacts. Mined as
   **whole-file residue ops**: they version, but coarsely (whole-file replace, no sub-entity ops,
   no clustering into features). This already works today via the residue path — we just name it as
   a deliberate tier and don't pretend to give it semantics it doesn't have.
3. **Ignored files** — never mined, never in the op store, pure working-tree. Governed by
   **`.gitignore` (respected) + an `.sgtignore`** for sgt-specific exclusions. `.sgt/local/`
   (drafts, staged, oracle cache) is already gitignored; this generalizes the rule. tmp folders,
   `node_modules`, build output → tier 3.

Two sub-points worth calling out:

- **Generated / derived files** (lockfiles regenerated from a manifest, compiled assets) are tier 2
  but should carry a **`derived` flag** so a review/PR surface can *collapse* them — a human
  reviewing an agent's PR does not want 4000 lines of regenerated lockfile in their face. [CALL]
  This is a small metadata flag, not a new mechanism; it rides the same `intent`/provenance slot.
- **The boundary is a project decision, not a guess.** `.sgtignore` + an explicit "opaque vs
  entity" rule (by extension / by grammar availability) means the user *sees* which tier a file is
  in and can move it, rather than mining silently choosing. [UNKNOWN] the exact default extension
  map — decide empirically against a few real repos.

## 6. Provenance and surfaces — human perception is the priority

The owner's explicit reprioritization: we *can* push toward first-class provenance and a
machine-first surface, but **the priority is that a human can perceive and manipulate** the ops,
features, and their rationale. Machine-first is a means; human legibility is the end. This section
follows that ordering.

What already exists: `Op.intent` (advisory rationale, **excluded from the op id**), `Op.provenance`
(witnessing commit SHAs, appendable, **excluded from the id**), and plan sessions that bind declared
intent to real ops with drift detection. The kernel-safety property we exploit: **because `intent`
and `provenance` are excluded from `compute_id`, we can enrich them freely without perturbing op
identity or the fold.** Provenance is a pure additive metadata story — zero kernel disturbance.

**Decision [CALL]: enrich provenance into a structured, always-present record on the reconcile
path, and render it everywhere a human looks.**

- **Structure the slot.** On `checkpoint`/`distill`/`drift`, every landed op carries
  `{session, agent, prompt_ref, declared_feature, rejected?}` instead of one nullable string. The
  drift set — ops that arrived with *no* declared plan — is exactly the work that today has no
  "why"; structured provenance gives even unplanned agent work an attributable origin.
- **Render it for humans first.** The VS Code rail, the TUI, and `sgt blame`/`sgt log` show *why*
  each op exists (which session, which intent, which agent), and let the human **act** on it:
  re-point an op to a different feature, revert by intent, split a feature along a provenance
  boundary. Perception → manipulation, both human-facing.
- **The machine surface follows, it doesn't lead.** Keep `sgt.api` as the typed projection and MCP
  riding it; over time move toward the typed contract being what verbs *call* rather than what they
  *also emit* (the "machine-first" inversion). [CALL] But this is sequenced *after* human
  legibility, per the owner. We do not invert the whole CLI now.

The payoff, named against the industry framing: this turns the plan-mode spine we already have into
the "capture the decision context — prompt, state, alternatives considered" audit trail that Cursor
Origin et al. point at — *without a kernel change, and human-legible first.* The one genuinely new
capture is **rejected alternatives** (the roads not taken), which the op model does not record
today because an op only encodes what *happened*. [UNKNOWN] whether we capture that from the agent
transcript, or ask the agent to declare it — deferred, but flagged as the highest-value provenance
we don't yet have.

## 7. Known unknowns and unknown unknowns

Consolidated so they don't hide in the prose:

**Known unknowns (we know we must answer):**
- Live-sync transport + causal-order metadata (§4).
- Live fork-resolution UX — block vs diverge-then-merge (§4).
- Does feature-layer selection keep closures human-scale, or does coupling defeat it? Measure
  before shipping selection (§2, tied to BET-C).
- The opaque-vs-entity default file map and the `derived` collapse rule (§5).
- How `rejected alternatives` provenance is captured (§6).

**Unknown unknowns / structural blind spots (surfaced because we've lived in the kernel):**
- **Migration / onboarding.** An existing large git repo's history is generally *not* a valid ideal
  (sgt's own 67-commit history has ~440 forked chains). Every real adopter hits the genesis-horizon
  wall. If `init --horizon` isn't a smooth on-ramp, adoption dies at step one. This is an adoption
  blocker disguised as a footnote — promote it.
- **Trust at agent scale.** When one human oversees dozens of agent PRs/day, the bottleneck is
  *deciding which work to trust*, not merging. We have the provenance substrate to win here but no
  surface framed around it.
- **The review / PR object.** Not designed. In sgt a "PR" should be *an op-subset + its oracle
  verdict + its feature-map delta + its provenance* — reviewable by intent, not by eyeballing a
  diff. Every ingredient exists; the surface doesn't. (Deliberately out of scope for this doc;
  flagged as the obvious next design.)

## 8. What this does NOT change

To keep the anti-Frankenstein promise auditable:

- **No new kernel object.** State is still an order ideal; conflict is still a same-symbol fork;
  the feature tree is still an orthogonal metadata partition; the oracle is still the sole semantic
  ground truth.
- **No perturbation of op identity.** Every provenance/branch/collab addition rides fields already
  *excluded* from `compute_id` (`intent`, `provenance`) or is pure metadata (feature tree, pins,
  selection). The fold is untouched.
- **git stays real underneath.** Porcelain routes and wraps; it does not replace. A teammate with
  plain git can still read the repo.

Everything in this doc is a **surface, a routing rule, or an excluded-from-id metadata field.** If a
proposed step ever requires changing a kernel object, that is the signal to stop and re-open the
kernel ADR, not to smuggle it through here.

## 9. Rough sequencing (non-committal)

Ordered by "unlocks the most UX per unit risk," not a schedule:

1. **Porcelain + `sgt git` passthrough** (§1) — smallest, highest daily-friction win; makes sgt the
   place the user lives.
2. **Structured provenance on the reconcile path + human rendering** (§6) — additive, no kernel
   risk, directly answers "why did this code come to be."
3. **The three-tier file boundary + `.sgtignore`** (§5) — required before real repos are usable at
   all.
4. **Branch-as-selection at the feature layer, with closure explanation** (§2) — gated on a
   closure-size measurement; this is where the clustering quality (BET-C) gets a real product test.
5. **Async sync UX + fork-resolution surface** (§4) — mostly built (`sgt sync`), needs the
   resolution experience.
6. **Live sync** (§4) — deferred behind transport + live-fork-UX unknowns.
7. **The PR/review object** (§7) — its own design doc.
</content>
</invoke>
