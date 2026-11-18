---
name: ugh-workflow
description: Use when choosing between ugh's look-alike verbs (revert by name, save, file, definition, one step or a range; revert vs undo; name vs rename; sync vs push) or when writing an address for one. Applies to any task that runs `ugh` commands in this repo.
---

# ugh command selection

`ugh` is a layer over git: history is mined into steps, each one save's change to one top-level
definition (with the blank lines before it), a file's head or a whole file, and the code is what
the steps that are in produce. A save that appends a definition after another is one step, on the
new one. Several verbs look similar but
answer different questions. This skill is the decision guide — read the relevant section before
running an ambiguous verb, don't guess from the name alone.

## Addresses

Every verb that acts on steps takes one address, and every step ugh prints carries its address:

| Address | The steps it names |
|---|---|
| `<label>` | the steps named label |
| `<label>@<rev>` | those steps in one save |
| `<path>` | the steps on one file |
| `<path>::<def>` | the steps on one top-level definition |
| `<path>::<def>@<rev>` | the one step on that definition in that save; refused when the save did not change it |
| `<rev>` | one save's steps (anything `git rev-parse` takes) |
| `<rev>..<rev>`, `<rev>..` | the saves in that range (`<rev>..` runs through HEAD) |

An address reaches steps that are out as well as steps that are in. A token that is both a label
and a rev or path is refused, naming both. Anything else is refused with the nearest `ugh find`
hits; there is no free-text or fuzzy reading. `ugh find "<words>"` is how you turn a description
into an address.

## Two layers that share verb-ish names

- **Code layer**: `revert`, `restore`, `undo`. These change the code with a new commit.
- **Names layer** (metadata only, no code change, `ugh undo` reverses it): `ugh name <address>
  <label>` gives a label to the steps at an address, which is also how you merge steps into an
  existing name, split a name, or move steps between names; `ugh rename <label> <new>` relabels
  every step with a label, and an existing label absorbs them. `ugh save --as <label>` names the
  steps of that save. A name never grows by itself: a later edit is unnamed unless saved `--as` it.

## Revert: pick by what you name

| You want | Command | What happens |
|---|---|---|
| Take one save's work out | `ugh revert <sha>` | the save's steps un-applied as a new edit; what other work stands on is held, with the reason and the saves holding it |
| Take one step out | `ugh revert <path::def@sha>` | the same, for one step |
| Take everything named something out | `ugh revert <label>` | the same, for the steps with that name |
| Take the work and everything standing on it out | `ugh revert <address> --whole` | the removal set the preview names; never cuts another save in two |
| Bring back what a revert took out | `ugh restore <address>` | takes out the edit that took it out; edits made since combine, same-line conflicts are held with both versions and nothing is written for them |

Every revert and restore is a commit whose `Ugh-Out:` trailer names the ops of the steps it took
out, so what is out travels with git history (a clone or a rebase knows). The project's check
(`.sgt/oracle.json`, or the parser when none is configured) must pass before anything is written;
a failure inside a definition being changed holds that step, and a failure anywhere else applies
nothing. `ugh show <address>` gives the same answer without changing anything.

`ugh undo` reverses the last thing you did, from the operation log: a save, a revert or a restore
is undone by taking out all of its commit's steps (so work committed since stays), a `name` or
`rename` by restoring the names before it.

## Moving work: sync vs push

- **`ugh sync [remote] [branch]`** — fetches a teammate's work and folds it in by op-set union. A
  same-symbol fork is surfaced, not merged textually; `ugh resolve <symbol>` is the guided fix.
  Names merge per step: one side's rename is taken, and a step both sides renamed differently keeps
  ours and is printed with both names.
- **`ugh push [remote] [branch]`** — a plain non-forcing `git push`. On rejection it points you
  at `ugh sync`, never forces.

## The plan

`ugh plan <label>...` declares names before their edits exist, with HEAD as the baseline. Save each
piece `--as` its declared name; `ugh plan` reports each name with the saves since the baseline that
carry it (or missing) and the steps since the baseline that carry no declared name; `ugh plan
--done` clears it. `ugh status` shows the same report under what needs attention. See `ugh-plan`.

## Quick don't-confuse-these list

- `ugh show <address>` and `ugh find "<words>"` are read-only: one explains an address you hold, the
  other lists addresses matching a description.
- `ugh status` reports working-tree drift (files on disk differing from the recorded state), forks,
  untracked files and the plan.
- `ugh check` verifies the op store; `ugh check --repair` re-derives the current ref's record from
  git history.

## Related

- `ugh-agent` — how to operate in an ugh repo at all: orienting cheaply, which read costs what, and
  which of these verbs are the human's rather than yours. Read that first if you are an agent.
- `ugh-plan` — declaring names before you build, and reading the report.
