---
name: sgt-workflow
description: Use when choosing between sgt's overlapping/look-alike verbs (revert vs revert --keep-dependents vs revert --session vs feature revert; merge/split/rename/move vs merge-op/split-op/transplant; land vs commit vs propose vs push), when parallel/agentic work needs a session or worktree, or when navigating the plan -> checkpoint/drift -> propose -> land review lifecycle. Applies to any task that runs `sgt` commands in this repo.
---

# sgt command selection

`sgt` is a semantic layer over git: history is a mined, content-addressed op DAG; a codebase
state is an order ideal of that DAG. Several verbs look similar but operate on different layers
or answer different questions. This skill is the decision guide — read the relevant section
before running an ambiguous verb, don't guess from the name alone.

## Two layers that share verb-ish names

- **Op-chain layer** (kernel): `revert`, `restore`, `merge-op`, `split-op`, `transplant`,
  `identity split|join`. These touch the actual op DAG / content.
- **Feature-tree layer** (metadata only, instant, reversible, content-untouched):
  `merge`, `split`, `rename`, `move`. These relabel/regroup which feature an op belongs to —
  they never touch code.

`merge-op` and `merge` are NOT the same operation. `merge-op <tip_a> <tip_b>` drafts a hollow
reconciling a chain fork (two competing op-chain tips for one symbol). `merge <survivor>
<absorbed>` unions two *feature labels*. Same for `split-op` (cuts one op into two) vs `split`
(cuts one feature into two, previewed then confirmed — `sgt feature regroup split <feature> [--apply]`, no
separate preview verb needed).

## Revert: four different shapes, pick by what you want to keep

| You want | Command | What happens |
|---|---|---|
| Drop one op and everything built on it | `sgt revert <ref>` | exact ideal edit `I \ ↑X`; refuses / surfaces a fork if the up-set is ambiguous |
| Drop a whole feature's op-set | `sgt revert <feature-id-or-label>` | same edit, grouped: resolves the feature to its op-set first |
| Drop an op but keep dependents alive | `sgt revert <ref> --keep-dependents [--repair]` | drafts a continuation hollow per *direct* reference-dependent so its symbol stays live, instead of cascading the delete through everything downstream. Bare form prints the draft — you (or an agent) must `sgt advanced fulfill <draft-id> --from-tree` then `sgt advanced commit`. `--repair` skips the manual step: hands the draft straight to the LLM repair loop, which fulfills + runs it through the same oracle gate automatically. |
| Drop everything one session/agent contributed | `sgt revert --session <name>` | resolves by *provenance* (structured attribution on landed ops), not by ref — works long after the session's scratch worktree is gone |

Default to plain `sgt revert <ref>` unless the target has dependents you need to keep working —
in that case `--keep-dependents` is the right call, not a manual patch-up after the cascade
already broke things. Use `--repair` when an LLM-fulfillable continuation is acceptable and you
want one command instead of draft → hand-edit → fulfill → commit.

Every ideal-edit verb (`revert`, `restore`) also accepts a natural-language target once no
op-id/symbol/feature-label matches exactly (`sgt revert "the caching layer"`) — it proposes
ranked candidates, re-previews each for real, and never applies without `--yes` or confirmation.
Prefer an exact ref when you have one; the NL fallback needs `OPENAI_API_KEY` and is for when you
genuinely don't know the ref.

## Sessions and worktrees: when parallel/agentic work needs one

`sgt session start <name> [--base <branch>]` makes a real `git worktree` on its own
`sgt-session/<name>` branch. Use a session — not a plain in-place edit — whenever:

- **Two features are being worked at once** (by you + an agent, two agents, or you switching
  context) and you don't want their uncommitted/unlanded ops to collide in one working tree.
- **You want early fork warnings.** `sgt session status --watch` polls for footprint overlap
  between active sessions before either lands — catches a same-symbol collision while it's cheap
  to resolve, instead of discovering a fork after `sync`.
- **You want durable per-agent attribution.** `sgt session land <name>` stamps `session=<name>`
  on the landed ops. That's what makes `sgt revert --session <name>` (above) and
  `sgt advanced review-queue` provenance filtering work later — a scratch worktree that never ran through
  a session has no such handle.

Don't spin up a session for a single quick edit in the current working tree — `sgt save` /
`sgt switch` are the lightweight porcelain for that, and a session's worktree + branch bookkeeping
is overhead a one-off change doesn't need. Reach for a session specifically when isolation or
provenance is the point: parallel feature branches, an agent you want to sandbox, or work you may
need to attribute/revert as a unit later. `sgt session gc [--force]` reaps sessions whose owning
process died — run it if `session status` shows a DEAD entry piling up scratch trees.

## Landing work: land vs commit vs propose vs push

Four verbs move work into a more-shared place; they are not interchangeable:

- **`sgt advanced commit [--message ...]`** — commits a *staged rewrite candidate* (the output of
  `fulfill`, or a completed repair). Local, no branch/network involved. Gated on the oracle
  (refuses unless the staged candidate's build/test verdict is `pass`, or you supply
  `--override pass --reason "..."`).
- **`sgt land <branch>`** — advances a *shared branch record* by compare-and-swap: unions your
  HEAD's ops onto the branch tip, gates oracle-green, retries on a lost race. This is the
  direct/no-review path — appropriate for your own branch, or once you're sure the change should
  go straight in.
- **`sgt propose create/status/land/render/publish`** — a *reviewable* base+Δ object over a ref.
  Use this instead of a direct `land` when: you want someone else (or CI) to look at the diff
  first, the change spans multiple features and a reviewer might want to accept only some of them
  (`propose land <id> --subset <feature>...`), or you want a GitHub PR out of it
  (`propose render --github` / `propose publish`). `propose land` is the same CAS-gated advance as
  bare `land`, just scoped to the proposal's Δ (or a subset of it) instead of your whole HEAD.
- **`sgt push [remote] [branch]`** — a plain non-forcing `git push`. Use it when the remote
  branch is yours alone and there's nothing sgt-specific to reconcile; on rejection it points you
  at `sgt sync`, never forces.

Rule of thumb: no shared branch involved → `commit`. Shared branch, no review needed → `land`.
Shared branch, review or partial-acceptance needed → `propose`. Plain git remote, no fork/CAS
concerns → `push`.

## The plan -> checkpoint/drift -> propose lifecycle

This is the agentic loop for turning a stated intent into landed, verified work:

1. **`sgt plan intake "<text>"`** decomposes a stated plan into predicted hollow ops, off-chain —
   it never touches the ideal algebra itself, just records what you expect to happen.
2. Do the work (hand-edit, or let an agent edit) in the working tree or a session.
3. **`sgt save`** records the work and folds plan-matching into that same beat (U12): every
   *unambiguous* single-step match auto-confirms, and anything ambiguous (two steps tangled in one
   op cluster) is reported for you to settle with
   `sgt save --resolve-plan --confirm-hollow <id> --confirm-op <id>`. There is no separate
   `checkpoint` verb any more. Auto-confirm only ever fires where exactly one pending step claims
   the ops, so it cannot silently pick between two candidates.
4. **Plan drift** — ops mined that *no* active plan predicted — is reported in that same `sgt save`
   output, and an agent can read it on its own via the `sgt_drift` MCP tool. It is not
   `sgt log --summary`: that reports *working-tree* drift (files on disk differing from the
   recorded state), a different question that kept the same word. Read plan drift before
   landing if you want to know whether unplanned changes crept in.
5. Once the work is checkpointed (or you're intentionally landing drift too), move to landing:
   `sgt advanced commit` (staged rewrite), `sgt land <branch>` (direct shared advance), or
   `sgt propose create` → `propose land`/`publish` (reviewed advance) — pick per the "Landing
   work" section above.

`plan`/`checkpoint`/`drift` answer "did what happened match what was intended, and what wasn't
predicted." `propose` answers "is this ready to share, and should it be reviewed before it lands."
They compose: a plan's checkpointed work is exactly what you'd wrap in a proposal before landing
it somewhere shared.

## Quick don't-confuse-these list

- `sgt advanced preview <verb> <args>` previews merge/rename/move/revert side-effect-free. For split,
  there's no separate preview verb — bare `sgt feature regroup split <feature>` (no `--apply`) already previews;
  `--apply` confirms it.
- `sgt feature select <feature>...` / `sgt feature why <op>` are *explanation* verbs (closure/attribution), not
  a way to switch what's materialized in the working tree — they don't change state.
- `sgt advanced migrate [feature-ids|ops-v3] [--apply]` is a one-time op-store schema migration, not a
  daily-loop verb — dry-run by default, run it only when a plan doc or error message tells you to.
- `sgt advanced fulfill <draft-id> --from-tree` stages a drafted hollow's image; it does not commit.
  `sgt advanced unstage` abandons a staged candidate without committing it. `sgt advanced commit` is the only verb
  that actually commits one.

## Related

- `sgt-agent` — how to operate in an sgt repo at all: orienting cheaply, which read costs what, and
  which of these verbs are the human's rather than yours. Read that first if you are an agent.
- `sgt-plan` — recording intent before you build, and session ownership across concurrent agents.
