---
title: "refactor: architectural options inspired by jj, Darcs, Pijul, and lazy merging"
type: refactor
status: proposed
date: 2026-07-18
origin:
  - why-semantic-git.html
---

# refactor: architectural options inspired by jj, Darcs, Pijul, and lazy merging

## Summary

Writing `why-semantic-git.html` forced a close, honest comparison between sgt's kernel and four
other systems that solved adjacent problems: jj (change-id, operation log), Darcs (patch
commutation, conflictors), Pijul (categorical pushout over a line graph), and the lazy-merging
paper (proven lattice join over abstract cells). Three of those systems back their conflict
handling with either a formal proof or a persisted log sgt does not have. This doc turns that
comparison into six concrete architectural options for sgt's own kernel, each grounded in the
current code (file and line cited) and in the specific mechanism it's borrowed from. None of these
are decided. Each section ends with the tradeoff stated in plain language and the pitfall that
would bite if we did it carelessly. You pick which ones, if any, are worth the churn.

This is not a patch list. Two of the options (D1, D2) touch a core data structure — the sync base
model and the identity model, respectively — and would need their own migration the way the
`MINER_VERSION` 2→3 bump did.

---

## Current-state audit

| area | current mechanism | file:line | already-known limitation |
|---|---|---|---|
| conflict detection | fork = two ops claiming the same `(symbol, before_version)` | `order.py:149-183` | correct but coarse — whole-symbol grain, not line grain |
| conflict resolution | `_grounded` fixpoint excludes both fork tips + their upset; a human runs a rewrite verb to fix it | `order.py:267-338`, `rewrite.py:177-220` | no proof of minimality, unlike Pijul's pushout |
| identity | tiered detection: exact hash → structural hash → fuzzy Jaccard ≥0.80 | `identity.py:16-19,99-211` | per-call union-find, "not persisted across separate calls" (`mine.py:17-20`, flagged in the code itself as future work) |
| resolution provenance | recorded as a free-text `intent` string on the hollow op | `rewrite.py:204-205,27-30` | not queryable or structured |
| sync base recovery | trailers → committed ideal record → full remine → naive union | `ingest.py:242-271` | "resolve just keeps union semantics" when no base is found (`ingest.py:249-251`) — a silent degrade, not a refusal |
| sync topology | pairwise, one remote ref at a time, no shared log | `ingest.py:1-13,78` | no equivalent of jj's whole-repo operation log |
| locking | local, per-mutation `flock` on `.sgt/local/lock` | `store.py:38,126-184` | fine for one machine, says nothing about two people syncing at once |

---

## D1. Sync base recovery: heuristic ladder with a silent union fallback, vs. an append-only operation log

**What sgt does now.** When sgt needs a three-way base for a sync, it tries commit trailers, then
a committed `.sgt/ideal.json` record, then a full remine of the base commit. If none of those
produce a verified base, `resolve.py` does not refuse. It falls back to a plain union of both
sides' op-ids and runs grounding on that (`ingest.py:249-251`). This is documented in the code as
intentional degrade-gracefully behavior, not a bug, but it means the "no verified base" case is
silent. Nothing in the CLI output distinguishes a real three-way merge from an unverified union
unless you go looking.

**What jj does instead.** jj keeps an append-only operation log, one ordered history of every
change to the repo's own view, not just to file content. Any clone can walk that log to reconstruct
an exact prior state without needing trailers or heuristics.

**The option.** Add an append-only sgt-native log (most likely a dedicated git ref, notes-style,
so it rides the same transport §9 of the essay already relies on) recording every land/sync event
and the ideal it produced. Base recovery becomes a log lookup instead of a three-tier guess, and
the "no verified base" case becomes something the log can name explicitly rather than something
resolve.py quietly papers over.

**Tradeoff, plainly put.** This fixes a real, already-documented gap, and it removes a silent
failure mode. It also adds a second thing that has to sync and, in principle, a second thing that
can itself get out of order across clones — the log needs its own append-only merge rule, though
that's the same CRDT pattern sgt already uses for declared edges and feature aliases, so the
pattern isn't new to the codebase. The honest cost is engineering effort and a migration, not new
research risk.

**Lower-risk half-step.** If a full log feels like too much change right now, the full-remine
tier of the existing ladder can be cached by commit SHA, since a commit's tree is immutable and
the remine result is therefore stable forever. That doesn't fix the silent-union case, but it
removes the repeated cost of the current fallback and ships as a pure cache, no schema change.

---

## D2. Identity: per-call fuzzy detection, vs. a persisted carried id

**What sgt does now.** A symbol's identity is detected, not carried. Mining runs a tiered matcher
per call — exact hash, then structural (AST-modulo-formatting) hash, then fuzzy token-Jaccard
above 0.80 with a size guard — through a union-find that is explicitly scoped to one `mine()` call
and not persisted across calls (`mine.py:17-20` says so directly, and flags a persistent registry
as future work).

**What jj does instead.** jj mints a change-id once, at commit creation, and that id survives
every rebase and rewrite because it was never re-derived. It's carried, not detected, so there is
no matching step at all, and therefore no threshold that can misfire.

**The option.** Build the persistent identity registry mine.py already gestures at: a store-level
table mapping canonical symbol-id to its detection history, populated the first time a symbol is
seen and consulted (not re-derived) on every later commit. This is close to the "symbol identity
scheme" already sketched in an earlier design note in this repo, so this option is less "new idea"
than "the natural next step of something already scoped."

**Tradeoff, plainly put.** A registry removes the token-Jaccard threshold as a live failure mode
for anything sgt has already seen once, which is the majority case in a repo it's tracked for a
while. It does not remove fuzzy matching entirely — every symbol sgt has never seen before (a fresh
clone's first mine, a symbol copy-pasted in from outside, history mined before the registry
existed) still needs a first-contact detection step, so this is additive, not a replacement. The
real cost is that the registry becomes another piece of `.sgt/` state that has to be synced and
merged across clones, and getting that merge rule wrong is exactly the kind of cross-replica bug
that's hard to notice until two people's registries disagree about the same symbol.

**Pitfall.** jj's change-id has no matching problem because it's never re-derived by content at
all — it's opaque metadata stamped once. If sgt's registry is instead a cache of detection
*results* that still falls back to fuzzy matching whenever the cache misses or two clones disagree,
it inherits all of fuzzy matching's edge cases plus a new cache-invalidation problem on top. The
registry is worth building only if it's the authority, not a memo.

---

## D3. Fork granularity: whole-symbol collision, vs. a Darcs-style commutation pre-check

**What sgt does now.** Two ops fork the instant they claim the same `(symbol, before_version)`,
regardless of whether the actual edited regions inside that symbol overlap. §10 of the essay walks
exactly this case: an edit to the top of a function and an edit to the bottom fork in sgt even
though no line-based system would blink.

**What Darcs does instead.** Darcs checks whether two patches commute, meaning whether applying
them in either order produces the same result. If they commute, there's no conflict. Commutation
is decided by comparing what each patch actually touches, not by comparing identities.

**The option.** Before declaring a fork, run a lightweight region-overlap check on the two
candidate ops' actual diffs against the shared before-version. If the changed regions don't
overlap, auto-produce a merged op instead of forking.

**Tradeoff, plainly put.** This would measurably cut the granularity cost from §10, which is real
and already documented as a known cost of sgt's design. It's also the one option on this list that
reopens a door sgt deliberately closed. The whole point of choosing a symbol as the unit of
identity, laid out in §2 and §5 of the essay, was to get a cheap, robust "is this the same thing"
answer without doing line-level bookkeeping. A region-overlap pre-check puts line-level reasoning
back in, just scoped to conflict avoidance instead of identity. Darcs and Pijul can do this safely
because their whole formal apparatus, patch commutation and the categorical pushout, was built
around exactly this question and has been stress-tested for over a decade between the two of them.
An ad hoc overlap heuristic bolted onto sgt would not carry that backing. Two edits can touch
disjoint line ranges and still be semantically related, one changing a constant the other's new
branch depends on, and a heuristic auto-merge would produce a function that looks coherent and
runs fine at merge time while being wrong in a way no fork would have caught.

**Recommendation if you want a smaller bite.** D4 below is a narrower, safer version of this same
idea, worth reading before deciding on this one.

---

## D4. A bounded "safe join" tier for provably commutative op kinds, inspired by lazy merging's lattice

**What sgt does now.** Every op is metadata for sgt's own CRDT layer (attribution, declared edges,
feature aliases), which already lattice-joins cleanly. But op *content* itself has no join. Two
ops on the same before-version either both make it into the valid set or both get excluded. There
is no middle tier.

**What lazy merging does instead.** The paper defines a join over abstract cells that produces a
correct combined result from partial, possibly conflicting inputs, without needing a total order
first, and without claiming that join is a general solution to merging real source code. It's
explicit that its guarantee only covers the abstract cell model, not arbitrary semantic content.

**The option.** Rather than D3's general overlap check, define a join only for `kind` pairings
that can be shown never to conflict by construction, for example a pure rename (`kind="move"`)
against an unrelated pure `rework` of the same symbol's body, where the rename touches only the
symbol's identity metadata and the rework touches only content. Auto-join those specific pairings.
Leave every other kind pairing, including two `rework`s on the same before-version, forking exactly
as today.

**Tradeoff, plainly put.** This is D3's idea with the scope cut down to cases the `kind` field
already tags, which makes it much cheaper to reason about and much smaller in surface area. It's
also only as good as the proof behind each declared-safe pairing. Doing this for one or two kind
pairs where the argument is genuinely airtight is a reasonable, bounded addition. Doing it for many
pairs on the strength of "seems fine in practice" is the worst version of D3's pitfall wearing a
smaller costume, a set of ad hoc safety claims with none of Pijul's or lazy merging's proof behind
any of them.

---

## D5. Resolution provenance: free-text intent string, vs. a structured field

**What sgt does now.** When `merge-op` resolves a fork, the identity of the tip it's resolving
*against* is recorded only in the hollow op's `intent` string, a free-text advisory field
(`rewrite.py:204-205`). It's already excluded from the content hash the same way `attribution` and
`derived` are, so recording more there costs nothing in the id computation.

**What Darcs does instead.** A conflict's resolution is itself a first-class patch, a conflictor,
that stays in history as structured data. Later tooling, or a person, can query what resolved what.

**The option.** Add a small structured field, something like `resolves: frozenset[op_id]`, to the
hollow op, alongside the existing `intent` string rather than replacing it. This turns "which
resolution touched which fork" from a grep-the-intent-string question into a query, which matters
for anything downstream that wants to reason about resolution history, an audit tool, a `sgt log`
view, a metric on how often forks recur on the same symbol.

**Tradeoff, plainly put.** This is the lowest-risk item on this list. It's an additive schema
field on a type (`Op`) that already excludes several fields from its content hash by design, so it
doesn't touch fork detection, grounding, or sync semantics at all. The cost is a `MINER_VERSION`
bump and the migration that comes with it, the same mechanical cost as any other schema addition,
not a new kind of risk.

---

## D6. Locking scope: local per-mutation flock, vs. jj's whole-repo operation log as a coordination point

**What sgt does now.** `Store._locked()` takes a local `flock` scoped to one mutation
(`store.py:126-184`), explicitly not a verb-wide lock. Two people syncing at the same moment don't
coordinate through anything shared. §9 of the essay names this directly as a real gap next to jj.

**What jj does instead.** jj's operation log gives every clone a single ordered view of repo-level
changes to reason from, which is what makes its handling of concurrent operations more consistent
than sgt's pairwise git-ref sync.

**The option.** This is the same log from D1, looked at from the coordination angle instead of the
base-recovery angle. If D1's log gets built, it becomes a natural place to also detect "someone
else landed since I last synced" before a `land` attempt, rather than relying purely on git's
ref-update CAS to reject the loser after the fact.

**Tradeoff, plainly put.** This isn't a separate build, it's a second reason to do D1. Worth noting
as a factor in that decision rather than deciding on its own.

---

## If you want an order to think about these in

Roughly cheapest-and-safest to most-invasive:

1. **D5** — additive field, no semantics change, ships behind the next version bump regardless.
2. **D1's cache half-step** — pure performance, no schema change, no new failure mode.
3. **D2** — the registry mine.py already flags as planned work; mostly a matter of building what's
   already scoped.
4. **D1's full log** — real gap, real fix, real migration; also strengthens D6 for free.
5. **D4** — a narrow, provable safe-join tier, worth it only if you're comfortable with the proof
   burden on each kind pairing you add.
6. **D3** — the biggest idea and the one that walks back a deliberate earlier choice (§2, §5, §10
   of the essay). Worth doing only if the granularity cost in practice turns out to be bigger than
   the essay's example suggests.

None of these are required to keep sgt working the way it does today. They're what the comparison
against jj, Darcs, Pijul, and lazy merging surfaced as the places where another system's answer is
more rigorous than sgt's, and what it would concretely cost to borrow that rigor.

---

## Sources

`why-semantic-git.html` (this repository), particularly §5 (identity), §6-7 (fork and grounding),
§9 (sync), §10 (granularity cost). Mimram & Di Giusto, "A Categorical Theory of Patches"
(arXiv:1311.3903). Jujutsu (jj) documentation, github.com/jj-vcs/jj. Darcs manual, darcs.net.
Pijul manual, pijul.org/manual. Current sgt source: `sgt/core/order.py`, `sgt/core/op.py`,
`sgt/core/mine.py`, `sgt/core/identity.py`, `sgt/core/rewrite.py`, `sgt/core/sync/ingest.py`,
`sgt/core/sync/resolve.py`, `sgt/core/store.py`.
