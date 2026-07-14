---
date: 2026-07-10
topic: collaboration and review — the sync protocol done rigorously (multi-agent on one machine, async teammates over git, live sync later) and the proposal/PR object (op-subset + oracle verdict + feature delta + provenance) as sgt's review surface. The "biggest hidden iceberg" doc.
status: design / ADR — PROPOSED. Companion to 2026-07-10-sgt-as-version-control.md §3/§4/§7; this doc IS the "PR/review object — its own design doc" that one deferred. Additive: no kernel object changes; two metadata-schema obligations are named honestly (§4.4, §4.5).
builds-on:
  - docs/design/2026-07-06-operation-ideal-kernel.md         # state = order ideal; only conflict = same-symbol chain fork; collaboration = op-store union (§8)
  - docs/design/2026-07-10-sgt-as-version-control.md         # porcelain, branch-as-selection, worktree-free multi-agent; deferred the PR object and live sync to here
  - docs/design/2026-07-01-symbol-identity-scheme.md          # deterministic symbol versions — the fact that makes union meaningful (§4.1)
  - sgt/core/sync.py                                          # U15: the built async sync — this doc audits it against the protocol laws and lists the gaps
author-note: written by Claude after auditing the U15 implementation and reading prior art (Pijul's travelling resolutions, jj's first-class conflicts, GitButler's parallel-branch metadata, Cursor Origin's deterministic review, git's zero-infrastructure remote). The owner's constraint, verbatim: robust and workable across foreign workflows — "instead of a very narrow interpretation... that breaks when other user workflows come in." So the spine of this doc is §7, a workflow matrix where every row is a way real users WILL hold the tool wrong, and the protocol must survive each one. [CALL] marks a judgment; [BET] a claim only measurement closes; [UNKNOWN] a genuine open question; [GAP] a defect in what is already built.
---

# Collaboration and review: the sync protocol and the proposal object

## 0. Why this document exists

Everything designed so far is single-writer. The moment two humans each run agents and want to
combine — or one human runs three agents at once — sgt needs what git has and sgt doesn't: a wire
format, a fetch/push analog, a conflict story that survives *other people's* workflows, and a
review object. The parent doc called this the biggest hidden iceberg and deferred it. This doc is
the dive.

Two deliverables, deliberately in one doc because they share one substrate:

1. **The sync protocol** — layered: same-machine multi-agent (SYNC-2), async over git (SYNC-1,
   partly built as U15), live remote (SYNC-3, deferred). One conflict model at every layer.
2. **The proposal object** — sgt's PR. Not a diff someone eyeballs: an op-subset + its oracle
   verdict + its feature-map delta + its provenance, with review verbs derived from the algebra.

The organizing principle, inherited and extended:

> **Collaboration adds no kernel object and no second conflict model. Every layer is: union the
> op store (free), reconcile derived structure (deterministic), reconcile metadata (must be a
> semilattice), and surface same-symbol forks (the one conflict, observed sooner or later
> depending on the layer's latency).**

## 1. Prior art, read for structure — not for features

The parent doc refuses feature-bolting; same rule here. What each system teaches *structurally*:

- **Pijul / patch theory.** Merge is a pushout; independent patches commute; and — the part worth
  stealing — **a conflict resolution is itself a patch that travels**: resolve once, and the
  resolution applies everywhere those two patches meet ("conflicts never come back"). sgt's
  `merge-op` is already this object. The protocol must make merge-ops *travel* like any op, so a
  fork resolved by one teammate is resolved for all (§5.3).
- **Jujutsu.** Conflicts are **first-class committed objects, not errors** — work continues on
  top of a conflicted state, and resolution is just another commit. This is the single most
  important UX posture for multi-agent: a fork must be a *state you can hold*, not an abort
  (§5.2). U15 currently aborts [GAP].
- **git.** The remote is **zero-infrastructure**: a dumb content store + refs + a compare-and-swap
  on ref update. No server logic. sgt inherits this for free by riding git transport and must not
  give it up — a protocol that needs a smart server loses git's deployment story (§3).
- **GitButler.** Parallel virtual branches over one working directory — metadata over text. It
  validates the demand for worktree-free parallelism, and its admitted limit (can't isolate
  agents mutating overlapping files) is exactly the case sgt's symbol chains make precise.
- **Cursor Origin.** Review reframed as **deterministic verification claims** rather than diff
  reading. sgt has the stronger primitive (an oracle keyed to the exact op-set) but no surface.
  §6 is that surface.

What none of them have, and sgt does: a *semantic* unit (the op on a stable symbol) as the thing
exchanged, so "what changed" survives transport with its meaning, provenance, and verdict intact.

## 2. The math: a lattice of layers, each with an explicit convergence obligation

Collaboration correctness is not one theorem; it is one obligation per layer. Stating them
separately is what keeps the protocol honest when a layer's implementation cuts a corner.

| layer | object | algebraic shape | convergence obligation |
|---|---|---|---|
| L0 | mining | pure function of (bytes, commit path) | **replica determinism**: same input mined anywhere → identical op bytes → identical content address (§2.1) |
| L1 | op store | grow-only set (G-Set), per-op provenance a G-Set | union; automatic, conflict-free, idempotent |
| L2 | order (≤) | derived function of L1 contents + declared edges | chain/reference edges converge because L1 does; **declared edges need retraction semantics** (§4.5) |
| L3 | selections (ideals, branches) | order ideals of L2; join **may not exist** | a same-symbol fork is precisely the witness that the join of two ideals does not exist; surfaced, never averaged (§2.2) |
| L4 | metadata (pins, feature tree, labels, review verdicts) | must be ACI semilattices (associative, commutative, idempotent) | **currently violated** by latest-wins-is-file-order (§4.3) and replica-local feature ids (§4.4) |

### 2.1 The determinism theorem (L0) — why union means anything at all

Union of op stores is only meaningful if two replicas independently mining the same history
produce **byte-identical ops**. This holds today, and it is load-bearing enough to state and test
as a law:

- A symbol's chain version is `sha1(surface_id : content_hash)` (`sgt/core/mine.py:96`) — no
  randomness, no wall clock, no replica id.
- An op's id is a content address over `(footprint, images, requires, kind, miner_version)`
  (`sgt/core/op.py:99`) — `intent` and `provenance` excluded, so enrichment never perturbs
  identity.
- Identity matching (tiers, thresholds) is deterministic given the same before/after snapshot.

**LAW-0 (replica determinism).** For any commit sequence C, `mine(C)` produces the same op set on
any machine running the same miner version. *Test: mine the golden corpus on two clones, assert
byte-equal stores.*

Two honest caveats, each with a policy, because each WILL happen in the wild:

- **Path-dependence.** Mining is deterministic in the *commit path*, not the end state. A branch
  mined commit-by-commit and the same net change squash-merged produce *different* op sets
  (different intermediate versions). Policy [CALL]: **ops in `.sgt/ops/` are the durable truth
  and travel in-tree**, so a squash on the git side does not force a re-mine — the original
  fine-grained ops survive the squash because they are files in the tree (§3.1). Re-mining is
  only for histories that never had sgt. When both representations exist anyway (one side mined
  a squash the other has fine ops for), the coarse op and the fine chain fork on first contact —
  surfaced like any fork, resolved by pinning the fine chain [CALL].
- **Miner-version skew.** `miner_version` is inside `compute_id`, so two replicas on different
  sgt versions mint different ids for the same edit — by design (a miner fix must not silently
  alias old ops). Consequence: **version skew is a protocol event, not silent corruption.** Sync
  must compare miner versions and refuse-with-instructions on mismatch rather than uniting
  incompatible stores [CALL]. Cheap now; painful to retrofit.

### 2.2 The fork is the non-existence of a join (L3)

The set of valid states (fork-free ideals) is **not closed under union** — that is not a defect,
it is the honest algebraic form of "concurrent edits to one thing require a decision." When ideals
I and J both contain ops advancing the same `(symbol, before_version)`:

- `I ∪ J` is downward-closed but has two maximal ops on one chain — the join of I and J *in the
  lattice of valid states does not exist*.
- A **merge-op** (both tips as parents, agent-authored image, oracle-gated) creates the missing
  upper bound: after it lands, the join exists and equals `I ∪ J ∪ {merge-op}`.
- A **pin** chooses an ideal below the failed join (drop one tip's up-set).

Two convergence consequences worth naming because they answer "what if two people resolve the
same fork concurrently":

- **Identical resolutions auto-converge.** If two resolvers author the *same* resolution image,
  content addressing makes them the *same op* — no fork-of-merges. This is not a trick; it is the
  reward for content-addressed ids.
- **Different resolutions fork again — and that is fine.** Two distinct merge-ops over the same
  tips are just a new fork one level up; the chain is append-only so this terminates the way
  git's recursive merges do, except each level is explicit and attributed. No livelock: each
  round strictly extends the chain.

And the Pijul property, inherited: **a merge-op is an op**, so it travels through sync like any
other. A fork resolved once is resolved for everyone who unions that store. Conflicts never come
back.

### 2.3 The oracle is part of the merge protocol — the honest limit of "conflict-free"

Footprint-disjoint union composes *algebraically* with zero interaction. It does **not** follow
that the result *works*: two ops can each pass the oracle alone and jointly break the build
(A renames a behavior B's new caller depended on; both touch disjoint symbols). Patch algebra
cannot see this and no VCS can — git ships it silently; sgt must not pretend otherwise.

**LAW-G (green-to-green).** A shared selection (branch tip, landed proposal) only advances to
states whose *exact op-set* has an oracle pass. The oracle verdict is keyed to the op-set hash,
so "tested" is a property of the state, not of a branch name at some moment.

This turns the industry's "merge queue + CI" from a bolted-on process into a typing rule of the
protocol: **union is the merge; the oracle is the merge's completion.** A union that is fork-free
but red is a held state — visible, materialized if asked, not yet the branch tip.

### 2.4 Causality without clocks

A G-Set converges on membership, not on a display order, and `Date.now()` is banned from the
kernel. The parent doc left "causal order metadata" as an unknown. Resolution [CALL]: **the git
commit DAG is already the causal broadcast log.** Every op carries witnessing commit SHAs in
provenance; commits are causally ordered by parentage; therefore "render a timeline," "what did
K do since I last synced," and "which op came first for tie-breaking" all derive from the witness
DAG — no Lamport clock, no vector clock, no new kernel field. Where a *total* order is needed
(deterministic metadata tie-breaks, §4.3), use `(witness-DAG topological order, op-id)` —
deterministic, replica-independent, wall-clock-free.

## 3. SYNC-0: the wire format and transport — git, stated as a spec

There is no new server. The protocol is a discipline over what already rides git:

### 3.1 What travels, and where

| artifact | location | why there |
|---|---|---|
| ops (incl. merge-ops) | `.sgt/ops/<id>` — in-tree, committed | survives squash/rebase/format-patch/tarball; any git transport moves it; content-addressed so duplicates self-dedupe |
| pins, declared edges, feature tree | `.sgt/pins/`, `.sgt/declared.json`, `.sgt/tree.json` — in-tree | shared curation must travel with the code it curates |
| a ref's committed ideal | `Sgt-Op:` trailers on the commit **and** `.sgt/ideal.json` in-tree [CALL: add the file] | trailers die under squash/rebase (GitHub's default!); the in-tree record is the recovery source. Redundancy is deliberate: trailers make commits self-describing, the file makes trees self-describing |
| proposals + review verdicts | `refs/sgt/proposals/<id>` + `.sgt/proposals/<id>.json` (§6.5) | refs are git's native "shareable pointer"; the JSON is the object |
| local-only: drafts, oracle cache, session scratch | `.sgt/local/` — gitignored | never travels; a replica's private state is nobody's business |

Every travelling file gets a `schema: <n>` header field from day one [CALL]. Forward-compat is
free now and impossible later.

### 3.2 The verbs (fetch/push analog)

- **`sgt sync [remote] [branch]`** — fetch + union + reconcile + surface forks (built, U15;
  audited in §5.1). The `git fetch && git merge` analog, minus the textual merge.
- **`sgt push [remote] [branch]`** [CALL: new] — `put` (fold, commit, trailers) then `git push`.
  On remote-moved rejection: **never force**; the remedy is `sgt sync` then push — exactly git's
  contract, so every hosting platform's protection rules keep working.
- **A bare git repo is a complete sgt remote.** GitHub, GitLab, a filesystem path, a USB stick.
  No sgt on the server, ever, for SYNC-0/1/2. [CALL] This is a hard constraint, not a nice-to-
  have: it is the whole deployment story, and it is what Cursor Origin gave up.

## 4. The reconciliation obligations — where the current metadata story falls short

The op store unions for free. The metadata does not, and "mostly works" metadata is exactly the
narrow interpretation the owner warned about. Four obligations:

### 4.1 Provenance — already correct

Per-op provenance is a G-Set of witnesses; `Store.add_bytes` unions on content-address collision
(R8). Nothing to do. (U15 even re-unions explicitly to undo `-X ours` drops — keep that.)

### 4.2 The ideal of a shared ref — correct once the in-tree record exists

Union of trailer sets, recovered from `.sgt/ideal.json` when history was rewritten (§3.1).

### 4.3 Pins — [GAP] latest-wins is not commutative today

`sgt/lens/pins.py:61` is explicit: *"latest-wins is just whatever the file holds."* That is
file-order, i.e., **sync-order**: A-syncs-B and B-syncs-A can end with different pin sets on a
contradiction. Violates L4's semilattice obligation. Fix [CALL]: contradicting pins reconcile by
a deterministic, replica-free total order — `(witness-DAG topo order of the pin's introducing
commit, then lexicographic pin hash)` — and the loser is *reported* (as today), never silently
dropped. Same fix for label edits. This is a small diff with an outsized property: **sync becomes
order-independent**, which is the property tests can actually assert (§8).

### 4.4 Feature identity — [GAP] replica-local ids poison every cross-replica reference

Today `reconcile_tree` Greene-matches the unioned clustering against *our* last tree, so *our*
feature ids stay stable — per-replica. Two teammates therefore hold **permanently different ids
for the same feature** (A keeps A's, B keeps B's), and anything that *references* a feature id —
pins ("these ops are feature F"), proposals ("this moves features X and Y"), review approvals —
does not survive transport. This quietly caps the whole review story.

Fix [CALL], metadata-only, no kernel change: **mint feature ids at birth and ship them.**

- A feature id is minted when a cluster is first born, as `f-<hash of its founding op-id set's
  minimum op id>` — deterministic, so two replicas that witness the same birth mint the same id.
- Birth/merge/split events already exist (Greene); record them in `.sgt/tree.json` so identity
  *events* travel, not just the current partition.
- Concurrent independent births that Greene would call the same feature (high member overlap on
  first contact) reconcile by keeping the id whose founding min-op is earlier in the witness DAG;
  the other id is recorded as an alias, so references through it still resolve. Aliases are a
  G-Set — they only grow, they never dangle.

[BET] Alias chains stay short in practice (features are born on one machine far more often than
simultaneously on two). If measurement says otherwise, feature references fall back to op-sets —
always correct, just uglier.

### 4.5 Declared edges — retraction needs OR-Set semantics

`sgt after A B` edges union today, with cycle detection at fold time. But a plain G-Set cannot
express *retracting* a declared edge (the current cycle handling just folds without the offending
edges and reports them — right instinct, no durable fix). [CALL]: declared edges become an
**OR-Set** (add with unique tag; remove kills observed tags), the standard CRDT answer, stored in
`.sgt/declared.json`. A cycle formed by *concurrent* declarations is then resolved by an explicit
retraction that travels, instead of being re-reported at every fold forever.

## 5. The protocol, layer by layer

### 5.1 SYNC-1: async over git — built (U15), audited here

What U15 gets right and must keep: absorb-local-reality-first (`lens.get` before anything);
idempotent no-op when already up to date; provenance re-union; declared-cycle tolerance; pin
contradictions *reported*; Greene-stable tree; `Sgt-Op` trailers on the merge commit.

The audit findings — each a [GAP] with a concrete fix, ordered by how badly its absence breaks a
real workflow:

1. **Foreign remote work is invisible.** `sync.py:118` reads theirs' ideal from the *tip commit's
   trailers*. A teammate who committed with plain git (no sgt) has no trailers and no new op
   files — their actual work silently contributes nothing to the union, while the merge commit
   still lands. This breaks the single most common mixed-team workflow. Fix: when the fetched
   range contains commits without trailers, **mine them** (`mine(ours_merge_base..theirs)`) into
   the union before reconciling — the ADR's "adoption ⊂ sync, one code path" (kernel §6), which
   the implementation dropped on the remote side. LAW-0 guarantees the mined ops are the same
   ones the teammate's sgt *would* have minted, so a later adoption by that teammate self-dedupes.
2. **Fork ⇒ abort-everything is the wrong posture** (jj's lesson, §1). One forked symbol out of
   200 clean ops currently aborts the entire merge — in a 5-agent workflow this makes sync
   effectively unavailable. Fix: **divergence is a state, not an error.** Union everything;
   the branch ideal advances by the fork-free part (`union minus each fork's up-sets beyond the
   pinned tip`); open forks are recorded in `.sgt/forks.json` (in-tree — a fork is shared state!)
   with both tips, footprints, provenance, and the remedy; `sgt forks` lists them; `sgt merge-op`
   / `sgt pin` close them. Work continues around a fork exactly as jj users work atop conflicts.
3. **Trailer-only ideal recovery** — covered by §3.1's `.sgt/ideal.json` [CALL]. Without it, one
   GitHub squash-merge of an sgt branch orphans the recorded ideal.
4. **`-X ours` as a union device is fragile.** It works because sgt overwrites every contested
   path afterwards — but the set of "paths sgt overwrites" and "paths `-X ours` touched" is kept
   equal only by vigilance. [CALL] Replace with an explicit tree-construction (take ours; add
   theirs' op files; write reconciled metadata; fold source) so no git resolution strategy is
   ever load-bearing. Mechanical, removes a whole class of silent-drop bugs.
5. **No miner-version handshake** (§2.1). Compare `miner_version` before uniting; refuse with
   instructions on mismatch.

### 5.2 SYNC-2: same-machine multi-writer — the multi-agent case, designed

The parent doc settled *isolation* (ephemeral materialization per session; worktrees as private
scratch, never as the merge mechanism). This section is the *coordination* those sessions share.
Three primitives, all filesystem-level, no daemon required [CALL]:

- **Lock-free op append.** The store is content-addressed files written temp-then-rename —
  concurrent writers are safe by construction (colliding writes are byte-identical by LAW-0's
  logic). Sessions may append drafts freely; appending is never the serialization point.
- **Branch advance is a CAS.** A named ideal's tip record is updated compare-and-swap (git's own
  ref-lock discipline, reused). **`sgt land`** [CALL: new verb] = "advance branch B by my op-set
  Δ": re-read B's current ideal, check `B ∪ Δ` fork-free, run the oracle on the exact result
  (LAW-G), CAS the record. On CAS failure (another agent landed first): re-union against the new
  tip — cheap, because Δ is ops, not text; only a genuine same-symbol fork with the interleaved
  landing needs a decision. **This is a merge queue, falling out of the algebra** — no queue
  infrastructure, just CAS + re-union, and "rebase before retry" costs nothing because reorder
  is a no-op on sets.
- **Early fork warning.** Sessions watch `.sgt/ops/` (fs-events). When a new op's footprint
  intersects symbols a session holds drafts on, warn *now* — "`validate_user` was just advanced
  by session-3's landing; your draft will fork" — the pre-conflict signal worktree-based
  isolation structurally cannot give, because worktrees hide concurrent work until merge. This
  is sgt's differentiator in the multi-agent case and it costs a file watcher.

Sessions are otherwise exactly the plan-mode loop that exists: base-ideal snapshot, scratch
materialization, drafts distilled to ops, land. An agent session and a human session are the same
object with different provenance.

### 5.3 SYNC-3: live remote — deferred, but shaped

Live sync = SYNC-2's three primitives over a transport that isn't a shared filesystem: op append
→ append/broadcast relay; branch CAS → CAS at the relay; fork warning → the same footprint-
intersection check on arrival. **No new conflict model at any latency** — that sentence is the
design. Deliberately deferred behind SYNC-1 hardening + SYNC-2 [CALL]; the relay choice
(tiny purpose-built vs existing pub/sub) stays [UNKNOWN], and merge-ops travelling (§2.2) already
guarantee that whatever the transport, resolutions propagate.

## 6. The proposal object — sgt's PR

### 6.1 Definition

```
Proposal {
  id             hash of (base, ops)                       # content-addressed like everything else
  base           frontier snapshot: op-id set of the target ideal at proposal time
  ops            Δ: op-id set such that base ∪ Δ is downward-closed
  target         branch name (advisory; base is the real anchor)
  # everything below is derived or metadata — never part of what "the change" is
  feature_delta  per feature node: ops added, symbols advanced, births/splits   (derived)
  verdict        oracle result keyed to hash(base ∪ Δ), with runner identity    (claim, §6.4)
  provenance     sessions, plans, prompts, agents, drift set                     (from ops)
  narrative      intent rollup, human-editable                                   (metadata)
  reviews        [{who, verdict, scope: proposal | feature | op-set, note}]      (metadata, G-Set)
}
```

A proposal is **pure metadata plus a set of op ids** — no new kernel object, no copied content.
The invariant that makes it well-formed is exactly the ideal law: `base ∪ Δ` must be an ideal.

### 6.2 What reviewing IS in sgt

The reviewer's questions, each answered by a derived projection rather than by reading a diff:

| question | answer surface |
|---|---|
| what does this change *do*? | feature delta: "advances F3 (retry logic): 6 ops, 4 symbols; births F9 (backoff)" + intent rollup |
| does it work? | oracle verdict for the exact op-set `base ∪ Δ` — not "CI was green on some commit" |
| will it conflict with the target? | fork check `base_now ∪ Δ` recomputed live; zero forks = lands clean, *by construction, not by hope* |
| where did it come from? | provenance: which sessions/plans/agents; which ops were planned vs drift |
| what would I be trusting? | per-op: planned+fulfilled (intent declared before code) vs drift (unplanned); agent vs human authored |
| show me the code anyway | materialized diff of `code(base)` vs `code(base ∪ Δ)`, feature-grouped, `derived`-flagged files collapsed |

The trust question is the one that matters at agent scale (parent doc §7: "the bottleneck is
deciding which work to trust"). The proposal object is where the provenance substrate finally
faces the human.

### 6.3 Review verbs the algebra gives us that git structurally cannot

- **Partial acceptance is exact.** "Land the retry fix, hold the telemetry" = land any
  downward-closed `Δ' ⊆ Δ`; sgt computes the closure and shows what holding X drags along
  (the §2-of-parent-doc closure explainer, reused verbatim). The remainder `Δ \ Δ'` *stays a
  valid proposal* against the new base. In git this is interactive-rebase surgery; here it is
  set arithmetic.
- **Rebase is re-union.** Target moved? Recompute `base_now ∪ Δ`. Fork-free → nothing to do
  (no textual rebase, ever); forked → the fork surfaces with its remedy. A proposal never "goes
  stale" in the git sense — staleness is precisely "a fork appeared," a first-class object.
- **Stacking is the partial order.** Proposal Q atop proposal P = Q's Δ has ops requiring P's
  ops; the dependency is *in* `≤`, not in a branch-parent pointer. Land P → Q's base check
  updates mechanically. Restacking — the operation Graphite/GitButler exist to make bearable —
  is a **no-op**, because reorder of a set is a no-op.
- **Review granularity = feature or op, not file.** Approvals attach to op-sets, so "approved
  except the schema change" is a recorded object with exact meaning, and a later push of new ops
  into the proposal visibly *extends* Δ (approvals don't silently cover ops that arrived after
  they were given — the approved op-set is pinned by ids).

### 6.4 Verdicts are claims — the trust model, stated early

An oracle verdict travels as **a claim**: `{op-set hash, result, runner, environment fingerprint}`.
Claims are attributable and *reproducible* (any replica can re-run the oracle on the same op-set
and must get the same verdict, or the discrepancy is itself a finding — flaky test, env drift).
[CALL] Verification-by-rerun is the launch trust model: a reviewer's machine (or CI) re-runs
before landing; the author's claim is advisory. Signing claims (and provenance generally) is
real future work for org-scale trust and is explicitly [UNKNOWN]/deferred — but the claim shape
above is chosen so a signature slots in without schema surgery.

### 6.5 Where a proposal lives, and the GitHub interop story

Native: `refs/sgt/proposals/<id>` pointing at the proposal's head commit, plus
`.sgt/proposals/<id>.json`. Push/fetch of proposals = git push/fetch of refs. A bare repo is a
complete proposal host (§3.2's constraint honored).

Interop [CALL — this is the adoption hinge]: **`sgt propose --github` emits a normal GitHub PR**:
a branch whose commits are the proposal's ops folded, and a generated PR body rendering §6.2's
table — feature delta, oracle claim, provenance summary, collapsed-derived-files note. A
teammate with no sgt reviews a normal PR; an sgt teammate opens the same proposal natively and
gets the review verbs. Review comments left on GitHub are *not* round-tripped into review
metadata at launch [CALL: honest scope cut]; the PR body carries a footer linking the proposal id
so tooling can close the loop later. sgt must be adoptable one-user-at-a-time inside a git team,
or it will not be adopted at all.

### 6.6 Landing

`sgt land <proposal>` = §5.2's land with Δ = the proposal's ops: fork-free against live target ∪
oracle green on the exact result (LAW-G) ∪ review policy satisfied (policy is repo config:
none / N approvals / feature-owner approval — plain data, not mechanism). Landing is the same
verb for an agent's session output and a teammate's month-long proposal, because both are just
op-sets. That uniformity is the point.

## 7. The workflow matrix — robustness against how people will actually hold it

Each row: a real workflow sgt does not control, what breaks in a narrow design, and which section
carries the load. This table is the doc's contract with the owner's constraint.

| # | workflow | what breaks if designed narrowly | load-bearing piece |
|---|---|---|---|
| 1 | one human, 3 local agent sessions, one repo | agents block on each other's syncs; forks abort everything | SYNC-2 CAS + re-union (§5.2); divergence-as-state (§5.1.2); early fork warning |
| 2 | two sgt users, async via GitHub | pin/tree reconcile order-dependent → replicas drift apart silently | ACI metadata (§4.3), global feature ids (§4.4) |
| 3 | sgt user + plain-git teammate | teammate's commits invisible to the union (today's behavior!) | mine-on-contact in sync (§5.1.1) + LAW-0 dedupe |
| 4 | repo policy: squash-merge all PRs (GitHub default) | trailers destroyed → recorded ideals orphaned; fine-grained ops seemingly lost | in-tree ops survive squash (§3.1); `.sgt/ideal.json` recovery (§5.1.3) |
| 5 | hotfix committed on GitHub web UI directly to main | foreign commit at the tip; naive sync corrupts or ignores it | same as 3 — adoption ⊂ sync, one code path |
| 6 | fork-based OSS contribution (contributor has no push access) | proposal refs can't reach upstream | proposal = branch + generated PR (§6.5); ops ride the contributor's branch in-tree |
| 7 | reviewer has no sgt | review surface unusable → sgt team can't ship to a mixed org | GitHub interop rendering (§6.5) |
| 8 | two teammates resolve the same fork concurrently | merge-of-merges livelock or silent LWW | §2.2: identical → auto-dedupe; different → explicit fork one level up |
| 9 | binary / opaque-file (tier-2 residue) concurrent edits | pretending semantics exist where they don't | residue fork = whole-file fork; merge-op degenerates honestly to pick-a-side or hand-authored bytes [CALL] |
| 10 | teammates on different sgt versions | silently incompatible op ids poison the shared store | miner-version handshake (§5.1.5) |
| 11 | monorepo, selection touching others' features | sync forces materializing everyone's everything | ideals are local (L3); sync moves ops, never selections — LAW-L below |
| 12 | CI as the oracle runner (not a laptop) | verdicts unattributable, unreproducible | claims with runner identity + rerun-to-trust (§6.4) |

Rows 2–5 are not exotic: they are the *default* GitHub-team experience. That they include two
[GAP]s in shipped code is exactly why this doc audits rather than assumes.

## 8. The laws — what the test suite asserts

Property tests, in the spirit of the kernel's round-trip laws (these are the exit criteria for
SYNC-1 hardening):

- **LAW-0 (replica determinism).** Same history, same miner → byte-identical op stores. (§2.1)
- **LAW-U (order independence).** For replicas {A, B, C} and any two sync schedules delivering
  the same op sets: identical final op stores, orders, fork sets, pin sets, feature trees
  (ids included). *This is the law §4.3/§4.4 currently break.*
- **LAW-I (idempotence).** `sync` twice = `sync` once. (Holds today; keep it held.)
- **LAW-F (fork completeness & soundness).** Sync reports a fork **iff** the union has two
  maximal ops on one chain. No hidden conflicts; no phantom conflicts.
- **LAW-R (resolutions travel).** A fork resolved on any replica is, after sync, resolved on
  every replica — and never reopens. (Pijul's property; falls out of merge-ops being ops.)
- **LAW-G (green-to-green).** A shared tip only ever points at op-sets with an oracle pass.
- **LAW-L (locality).** Sync changes no replica's HEAD selection unless it asked to land/track;
  moving ops moves no one's checkout.

## 9. Sequencing

Ordered by "how many matrix rows it unblocks per unit risk":

1. **SYNC-1 hardening** — mine-on-contact (§5.1.1), divergence-as-state (§5.1.2),
   `.sgt/ideal.json` (§3.1), explicit tree construction (§5.1.4), version handshake (§5.1.5).
   Unblocks rows 3, 4, 5, 10; prerequisite for everything below.
2. **Metadata semilattices** — ACI pins (§4.3), birth-minted feature ids + aliases (§4.4),
   OR-Set declared edges (§4.5). Unblocks row 2; prerequisite for cross-replica review
   references. LAW-U goes green here.
3. **SYNC-2** — land/CAS, shared-store discipline, early fork warning (§5.2). Unblocks row 1,
   the multi-agent case this whole product is aimed at.
4. **The proposal object + GitHub rendering** (§6). Unblocks rows 6, 7, 12 — and is the
   differentiated surface. Depends on 2 (feature ids in feature_delta) and 3 (`land`).
5. **Native review surface** in the rail/TUI — proposals, forks, trust view. UX over 4.
6. **SYNC-3** — deferred until 1–5 are lived-in; transport [UNKNOWN] stands.

## 10. Known unknowns

- Live-relay transport choice, and whether SYNC-3 is even needed before org-scale (§5.3).
- Signing (claims, provenance) for orgs that can't trust rerun-locally (§6.4).
- Review-comment round-trip from GitHub into review metadata (§6.5 scope cut).
- [BET §4.4] feature-id alias chains stay short — measure on the golden corpus with synthetic
  concurrent births.
- Whether divergence-as-state needs *limits* (a fork open for a month is a smell — expiry?
  escalation? [UNKNOWN], punt to lived experience).

## 11. What this does NOT change

- **No new kernel object, still.** A proposal is op-ids + metadata; a fork record is derived
  state made durable; a claim is metadata. The one law is untouched; `compute_id` inputs are
  untouched.
- **The conflict model does not grow.** Same-symbol chain fork, at every layer, at every
  latency — surfaced earlier at lower latency, never redefined. Plus the honest admission that
  the oracle — not the algebra — is the last word on whether a union *works* (§2.3), which was
  already the kernel's posture.
- **git remains the only server.** Every protocol layer through SYNC-2, and the entire proposal
  lifecycle, runs against a bare git remote. Anything requiring a smart server lives strictly in
  SYNC-3's deferred future.
- The two metadata-schema changes (§4.3 tie-break, §4.4 feature ids) are named as such and gated
  by LAW-U — they are the *price* of order-independent sync, paid in metadata, not in kernel.
