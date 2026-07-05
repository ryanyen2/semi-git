---
date: 2026-06-29
topic: git-as-substrate — stop tracking AST state; snapshot git, map decisions to commits, and let the LLM repair only the breaks a compose operation produces
status: design / ADR — proposed, not yet committed
supersedes-decisions:
  - effect-log-primary (the append-only typed-AST effect log is the source of truth; the tree is materialized by replay + ast.unparse)
  - statement-crdt (statement identity is a log-resident Logoot PosId reconstructed by build_statement_seq; blame is line→node from the log)
  - eico-gate (the confluence gate validates the re-materialized AST against typed invariants)
origin: this conversation (2026-06-29); builds on docs/design/2026-06-19-graph-only-agent-driven-sgt.md and docs/design/2026-06-18-effect-log-primary-redesign.md
---

# git as substrate

## Why this doc exists

Three observed problems, debugged in this session against the live code, turn out to be one root
cause:

1. **Direct edits are fragile.** Moving a function, adding a blank line, or adding a comment
   reads as drift or churns the feature→code map.
2. **Revert is confusing.** Reverting a *revision* removes the whole function instead of
   restoring its previous version.
3. **Granularity is coarse.** Almost everything is function-level; the statement-level path is
   gated to a narrow case and the rest of the world (methods, classes, signature changes,
   module-level code, non-Python) falls back to whole-unit or quarantine.

This doc argues these are symptoms of **the substrate**, proposes inverting it onto git, and is
explicit about what that costs. It is a proposal to decide, not a plan to execute — no code moves
until the **Open questions** are resolved and a follow-up implementation doc supersedes this one.

## Root cause: sgt hand-rolled a worse VCS

sgt's source of truth is an **append-only log of typed AST effects** (`sgt/effects/model.py`).
The working tree is reconstructed by replaying effects and calling `ast.unparse()`
(`sgt/effects/model.py:483`). That choice produces every symptom above:

- **The materialized tree is `ast.unparse`'d, so it drops every comment and normalizes all
  formatting.** The moment sgt materializes, it can no longer be byte-faithful to what a human
  typed. Comments and blank lines are *drift by construction* — there is no patch that fixes #1
  while the substrate is an AST projection of the text.
- **Statement blame zips log-resident slots against AST body nodes positionally**
  (`sgt/effects/attribute.py:166-176`), so anything that shifts line ranges without being an AST
  node misaligns.
- **A node bundles "create + same-lane revisions."** Revert sets a lane to `OFF`
  (`sgt/lifecycle/algebra.py:74-92`) and `OFF` lanes contribute nothing on replay
  (`sgt/project.py:242`). So reverting a revision that *extended* the original node drops the
  create too. The mechanism is correct; the granularity is wrong.
- **Statement-level distill is gated to top-level functions with unchanged signatures and no
  methods** (`sgt/effects/stmt_distill.py:153-169`). Everything else degrades.

The effect log is, in effect, a Python-only, lossy, coarse reimplementation of what git already
does perfectly: content-addressable snapshots, diff, and merge. We built a VCS to sit on top of a
VCS.

## Thesis

> **git is the substrate. A decision maps to one or more commits. HEAD is a composition — the
> tree produced by cherry-picking the selected decisions' commits. sgt's job is to maintain the
> semantic DAG over commits, drive the git actions each verb implies, and detect when a compose
> operation *breaks* (merge conflict, or a tree that no longer builds) so it can dispatch the
> LLM to repair exactly that break — nothing else.**

The boundary from the graph-only doc still holds, sharpened: sgt does not author features. The
LLM is invoked only to **repair the fallout of a graph operation** — resolve a cherry-pick
conflict, fix a tree that the compose left non-building — which is the same category as
"reconstruct," not "invent." (See **Tension 1** — this is a real softening that must be decided,
not assumed.)

## What each verb becomes

| sgt verb | today (effect log) | git-as-substrate |
|---|---|---|
| `plan` | `PLANNED` nodes, no effects | a branch/ref + intent metadata; still `PLANNED` semantically |
| `checkpoint` | distill diff → typed effects | `git commit`; tag commit with `Decision-Id` |
| `reconcile`/sync | reverse-differ → effects → confluence gate | `git add -A` the working tree as the decision's commit; no AST modeling |
| `revert` | lane → `OFF` in frontier | recompose HEAD from the decision subset minus this decision |
| `switch` | frontier selection swap | compose tree from a different decision subset |
| compose / HEAD | replay in-force entries + unparse | cherry-pick/merge the selected commits onto a base |

Each verb really is "a series of git actions," as the substrate should make literal.

## The semantic layer survives; the VCS layer is deleted

What we keep is the part that was always sgt's actual contribution:

- **The decision DAG** (decisions, lanes, frontier, builds-on) — but its nodes now *point at
  commits* instead of owning effects.
- **The decision→commit mapping.** Natural home is commit trailers (`Decision-Id: <id>`) or git
  notes, so the mapping survives rebase and is inspectable with plain git.
- **The UI surfaces and the one-projection rule** (`sgt/api.py`) — they re-read a projection that
  is now derived from `git log` + the decision sidecar instead of from the effect log.

What we delete: `sgt/effects/model.py`'s replay/materialize, `build_statement_seq` and the
statement CRDT, the reverse differ (`diff.py`), AST line→node blame (`attribute.py`), and the
EICO/AST invariant gate.

## What composition and the gate become

- **Compose** = base tree + cherry-pick (or octopus-merge) the selected decisions' commits.
- **Conflict = the signal.** A git conflict, or a merged tree that fails `build/typecheck/test`,
  is exactly the "break" sgt exists to catch. This replaces invariant-confluence with a coarser
  but **honest and language-agnostic** gate: *did it merge, and does it build?*
- **Repair** = on a break, sgt hands the LLM the conflict hunks + the two decisions' intents and
  asks for a resolution commit attributed to a synthetic "merge-repair" decision.

## Blame becomes commit-mapped

Line→node blame from the log is replaced by `git blame` re-labeled through the decision sidecar:
*which commit last touched this line → which decision owns that commit.* Cheaper,
language-agnostic, and survives formatting. It loses the "AST node" precision — but that precision
is exactly what the unparse round-trip made fragile, so this is plausibly a net win (see
**Tension 2**).

## Tensions to decide deliberately (not drift into)

**1. This softens "the one rule."** The graph-only doc forbids reintroducing a code-authoring
path. LLM-driven conflict resolution **is** authoring, even when scoped to repairing a compose
break. Defensible — it repairs an operation's fallout rather than inventing a feature, and a human
resolving a merge conflict isn't "writing the feature" either — but it is a genuine shift from
"sgt uses an LLM only to reason about the graph." We must state the new boundary precisely:
*the LLM may write code only to make a compose result merge and build; it may never originate a
decision's logic.*

**2. We trade deterministic AST blame for textual blame.** Today's line→node-from-the-log is a
crown jewel — and the source of #1's fragility. Dropping it is a real capability loss for a real
fragility fix. Decide whether semantic-node blame is a product promise or an implementation detail.

**3. Identity across rebase.** Commit trailers/notes must survive rebase, cherry-pick, and
amend, or the decision→commit map rots. Needs a concrete strategy (and probably a `--rebase`-safe
re-tagging hook) before any code.

## Open questions

- **Decision → commit cardinality.** One commit per decision (clean revert, forces commit
  discipline) vs. many commits per decision (matches how agents actually work, harder to
  compose). Likely many, mapped by trailer.
- **Where does the working tree live during compose?** A scratch worktree per compose, or compose
  on a detached HEAD? (`git worktree` is the obvious tool.)
- **Repair attribution.** Does a merge-repair commit belong to a new decision, to the
  later-applied decision, or to a distinct "integration" lane? This shapes how revert behaves
  afterward.
- **Migration.** Do we replay the existing effect log into a synthetic commit history once, or
  cut over only new repos and leave existing `.sgt` stores read-only?
- **Non-Python today.** The whole point is language-agnosticism — but the current UI/blame assume
  Python units. What degrades gracefully vs. breaks?

## Decision

**Proposed.** This doc records the direction and the cost. Next step on acceptance: a thin
vertical spike — one decision = one commit, compose a two-decision subset via cherry-pick, force
a conflict, stub the repair hook — to validate the model before committing to the deletion of the
effect-log machinery. No production code changes until that spike and a superseding
implementation doc.
