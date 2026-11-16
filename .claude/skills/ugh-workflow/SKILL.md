---
name: ugh-workflow
description: Use when choosing between ugh's look-alike verbs (revert by op, symbol, feature, checkpoint or theme; revert vs undo; sync vs push; feature rename vs revert) or when navigating the plan -> save -> drift loop. Applies to any task that runs `ugh` commands in this repo.
---

# ugh command selection

`ugh` is a semantic layer over git: history is a mined, content-addressed op DAG; a codebase
state is an order ideal of that DAG. Several verbs look similar but operate on different layers
or answer different questions. This skill is the decision guide — read the relevant section
before running an ambiguous verb, don't guess from the name alone.

## Two layers that share verb-ish names

- **Op-chain layer** (kernel): `revert`, `restore`, `undo`. These touch the actual op DAG /
  content.
- **Feature-tree layer** (metadata only, instant, reversible, content-untouched):
  `ugh feature rename <feature> <label>`. It relabels a feature and never touches code.

## Revert: pick by what you name

| You want | Command | What happens |
|---|---|---|
| Drop one op and everything built on it | `ugh revert <ref>` | exact ideal edit `I \ ↑X`; refuses / surfaces a fork if the up-set is ambiguous |
| Drop a whole feature's op-set | `ugh revert <feature-id-or-label>` | same edit, grouped: resolves the feature to its op-set first |
| Drop one checkpoint of a feature | `ugh revert <feature>@<n>` | the ops of that chapter, as `ugh log` numbers them |
| Drop work that ran across several features | `ugh revert "<theme label>"` | the ◆ rows `ugh log` draws, resolved by label or `theme-` id |

Every ideal-edit verb (`revert`, `restore`) also accepts a natural-language target once no
op-id/symbol/feature-label matches exactly (`ugh revert "the caching layer"`) — it proposes
ranked candidates, re-previews each for real, and never applies without `--yes` or confirmation.
Prefer an exact ref when you have one; the NL fallback needs `OPENAI_API_KEY` and is for when you
genuinely don't know the ref. `ugh find "<words>"` is the read-only way to turn a description
into a ref first.

`ugh undo` is not a revert: it inverts the last mutating operation (a save, a revert, a restore, a
rename) exactly, from the operation log.

## Moving work: sync vs push

- **`ugh sync [remote] [branch]`** — fetches a teammate's work and folds it in by op-set union. A
  same-symbol fork is surfaced, not merged textually; `ugh resolve <symbol>` is the guided fix.
- **`ugh push [remote] [branch]`** — a plain non-forcing `git push`. On rejection it points you
  at `ugh sync`, never forces.

## The plan -> save -> drift loop

This is the agentic loop for turning a stated intent into recorded, attributed work:

1. **`ugh plan intake "<text>"`** decomposes a stated plan into predicted hollow ops, off-chain —
   it never touches the ideal algebra itself, just records what you expect to happen.
2. Do the work (hand-edit, or let an agent edit) in the working tree.
3. **`ugh save`** records the work and folds plan-matching into that same beat (U12): every
   *unambiguous* single-step match auto-confirms, and anything ambiguous (two steps tangled in one
   op cluster) is reported for you to settle with
   `ugh save --resolve-plan --confirm-hollow <id> --confirm-op <id>`. Auto-confirm only ever fires
   where exactly one pending step claims the ops, so it cannot silently pick between two candidates.
4. **Plan drift** — ops mined that *no* active plan predicted — is reported in that same `ugh save`
   output, and an agent can read it on its own via the `ugh_drift` MCP tool. It is not
   `ugh status`: that reports *working-tree* drift (files on disk differing from the
   recorded state), a different question that kept the same word.

## Quick don't-confuse-these list

- `ugh show <sel>` and `ugh find "<words>"` are read-only: one explains an id you hold, the other
  ranks features, saves and symbols against a description.
- `ugh check` verifies the op store; `ugh check --repair` re-derives the current ref after a
  `reset --hard`, `amend` or `branch -f` dropped commits the record still names.

## Related

- `ugh-agent` — how to operate in an ugh repo at all: orienting cheaply, which read costs what, and
  which of these verbs are the human's rather than yours. Read that first if you are an agent.
- `ugh-plan` — recording intent before you build, and plan ownership across concurrent agents.
