---
name: ugh-agent
description: Read this whenever you are doing coding work in a repository that has a `.sgt/` directory or where ugh is mentioned, even if the user never names ugh. It tells you how to orient in one cheap call, which ugh read answers which question, how to write an address, which ugh actions are yours (reading, naming and saving your own work with your own words, the plan) versus the human's (the shared-state verbs), how to show ugh output in a transcript, and what to do when MCP is unavailable and you only have a shell. Load it before your first ugh call, not after a surprising one.
---

# Working in an ugh repo

ugh records history one definition at a time. Each save's change to one top-level definition (a
function, a class) is a **step**, printed with its address, `path::def@<short sha>`. That gives you
two things plain git cannot: the exact consequence of taking something out, including the later
work that stands on it, and the words the human typed that produced it.

Your job is to use those two things and to leave the parts that belong to the human alone.

## The shape of what you are reading

git stores your code as commits of line-diffs. ugh re-reads those same commits and breaks each one
into steps: one step for each top-level definition (with the blank lines before it), file head or
whole file whose text the commit changed. The files on disk are what the steps that are in produce. Taking steps out (`revert`) is a
new commit that un-applies them; bringing them back (`restore`) takes that commit out again.

A **name** is a label a person or an agent gave to some steps: `ugh save --as <label>`,
`ugh name <address> <label>`, `ugh rename <label> <new>`. A step has one name or none. ugh never
names anything itself, and a name never grows by itself: a later edit to a named definition carries
no name unless it was saved `--as` that name. What stands on a name is not stored; it is the
planner's answer, which `ugh_show` gives.

`ugh log` prints one line per save, newest first: short sha, subject, then `label ×n` for each name
on its steps, `n unnamed`, and `n out` when some of its steps are out.

```
c0a7e11 add the waitlist  waitlist ×3  1 unnamed
9b1e4d2 fix the drop deadline  2 unnamed  1 out
```

## Addresses

Every operand is an address, and every step ugh returns carries one:

- `<label>` — the steps with that name; `<label>@<rev>` — those steps in one save
- `<path>` — the steps on one file; `<path>::<def>` — the steps on one top-level definition
- `<path>::<def>@<rev>` — the one step on that definition in that save (refused when the save did
  not change it)
- `<rev>` — one save's steps; `<rev>..<rev>`, `<rev>..` — the saves in that range

An address reaches steps that are out as well as steps that are in. A token that is both a name and
a git rev is refused, naming both; anything else is refused with the nearest `ugh_find` hits. There
is no free-text reading, so when you only have a description, call `ugh_find` first.

## Orient with one call

Before your first edit, run this. It works in any harness that has a shell:

```bash
ugh status --json
```

It reports the counts, whether there is unsaved work, and what needs a human decision: two-version
choices a sync left (`ugh save` refuses until each is resolved), a git history rewrite, and the
plan if one is declared.

Check for a `.sgt/` directory before you rely on any of it. If there is no `.sgt/`, use plain git
and stop reading here.

One state means **stop and tell the human** rather than working around it: a paused git
merge/cherry-pick/revert — the tree holds conflict markers, `ugh save` refuses outright, and
anything you record now is recorded against a half-merged tree. Say what you found and what fixes
it.

## Which read answers which question

| You want to know | Use |
|---|---|
| What these steps are, what taking them out would take, what was asked for | `ugh_show` with an address |
| Which address matches a description | `ugh_find` |
| What happened recently | `ugh_log` (the newest 30 saves) |
| How the plan stands | `ugh_plan` with no arguments |

The first ugh call in a session mines the working tree and can take several seconds; later calls are
fast.

## Read before you write

Before editing anything unfamiliar, run `ugh_show` on the definitions you are about to touch: each
save in its answer carries `ask`, the words that produced that code, and a constraint you would
otherwise rediscover by breaking it is usually sitting right there.

Before proposing anything destructive, run `ugh_show` on the target, or `ugh_revert` with
`emit=true`. They report which steps a revert would take out and which are held because other work
stands on them (with the reason and the saves that hold each), and how big the removal set is —
what `whole=true` would take. Neither writes anything. State those numbers to the human instead of
the bare command.

## What is yours and what is theirs

Yours: reading (`ugh_show`, `ugh_find`, `ugh_log`), the plan (`ugh_plan`, see the `ugh-plan` skill),
naming steps (`ugh_name`), and **`ugh_save`**.

Saving is yours on purpose, and it comes with two obligations. First, `ugh_save` asks for your own
words (`message`), and they become the save's subject — so write the sentence you would have put in
a commit message — and `as` names the save's steps. Second, pass along the *user's* words too:
`prompt` takes the ask that drove this work, verbatim as you received it (their words, not your
paraphrase), and `claude_session_id` takes your session UUID (read `$CLAUDE_CODE_SESSION_ID` via
Bash; never `$CLAUDE_CODE_BRIDGE_SESSION_ID`, which can carry a parent's id). Together they let
`ugh show` answer with what the user actually asked for. The same two fields ride
`ugh_revert`/`ugh_restore` when the user asked for the removal or return in so many words. A save
is additive and `ugh undo` reverses it.

Theirs, and deliberately not exposed to you as tools:

- **`ugh sync`, `ugh resolve`** change *shared* state: sync merges a teammate's branch, and
  resolve decides which of two versions of a definition a merge keeps. Neither is reversed by `ugh
  undo`. When `ugh_save` refuses because a sync left a two-version choice, the error names each
  one with its `ugh resolve` command; hand those over.
- **`ugh rename`** relabels every step with a name, across everyone's work. That is a judgement a
  human makes; name your own steps with `as` and `ugh_name` instead.

When one of these is the right next step, say so and hand it over with the exact command.

## What the work was asked for

Each save in a `ugh show` answer carries `ask`: the words the human typed that produced that work.
`ask.gist` is an excerpt starting at the request, `ask.text` the whole prompt, and `ask.source` says
whose words they were -- their own typing, your relay of it, or your paraphrase.

Two rules follow from it. **Quote it, do not paraphrase it.** When you tell somebody what a piece of
work was for, their own sentence is the answer. **Read it before you edit**, because the fastest
way to undo somebody else's decision is to re-derive their intent from the code instead of reading
what they asked for.

A null `ask` is not a hint to invent one. It means this work predates the prompt hook or was saved
outside a conversation, and saying so is the honest answer.

## Showing ugh output to a human

ugh's terminal output is built for a terminal. Pasted into a transcript it wraps badly and costs
tokens.

- Reading it yourself: use `--json`, or the MCP tool, and summarize in your own words.
- Showing it to them: only output that is already narrow — `ugh show` or `ugh status`.
- Do not paste a long `ugh log` into a transcript. Point at the command instead.

When you name a command, name one that runs. An unrecognised verb exits non-zero and prints the
closest real verbs, but a wrong command in a message *to the human* costs their trust.

## When there is no MCP

In a harness with only a shell, every read above has a `--json` equivalent: `ugh show <address>
--json`, `ugh find "<words>" --json`, `ugh log --json`, `ugh status --json`, `ugh plan --json`. See
`references/cli-fallback.md` for the tool-to-command mapping.

## References

- `references/cli-fallback.md` — MCP tool to CLI command mapping, for shell-only harnesses.
- `references/costs.md` — what the reads cost and how to measure them.
- The `ugh-plan` skill — declaring names before you build.
- The `ugh-workflow` skill — choosing between ugh's look-alike verbs.
