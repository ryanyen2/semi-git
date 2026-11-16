---
name: ugh-agent
description: Read this whenever you are doing coding work in a repository that has a `.sgt/` directory or where ugh is mentioned, even if the user never names ugh. It tells you how to orient in one cheap call, which ugh read answers which question and what each one costs in tokens, which ugh actions are yours (reading, the plan loop, and saving your own work with your own words) versus the human's (the shared-state verbs), how to show ugh output in a transcript without dumping terminal control codes, and what to do when MCP is unavailable and you only have a shell. Load it before your first ugh call, not after a surprising one.
---

# Working in an ugh repo

ugh records *symbol-level* history: each edit to a function, class, or file becomes an "op", and
related ops are grouped into "features". That gives you two things plain git cannot: the recorded
*reason* a piece of code is the way it is, and the exact consequence of removing something,
including the later work that depends on it.

Your job is to use those two things and to leave the parts that belong to the human alone. The rest
of this file is about doing that without wasting their context window or their time.

## The shape of what you are reading

Two paragraphs of model, because the reads below only make sense against it.

git stores your code as commits of line-diffs. ugh re-reads those same commits and breaks each one
into **ops** — one op per changed symbol (a function, a class): what it touched, the version it
started from, the version it produced, what it was built on. It replays the ops it holds, symbol by
symbol, to rebuild your files. The live subset of ops is the **ideal** (valid = every op's
prerequisites are in, and no symbol has two competing versions); the files on disk are just that
ideal **folded** back into text, so they never drift from the history the way a stale diff can.
Related ops are grouped into **features** — the unit you and the human actually think in.

```
git:    commit ──── commit ──── commit           lines & diffs
                       │  ugh re-reads each commit
                       ▼
ops:      ⋯ e4  e5  e6  e7  e8  e9 ⋯              one op = one symbol's edit
            └──┬──┘ └──┬──┘ └─┬──┘
features:    "drop"  "waitlist" "clash"          ops that change together
                       │  the currently-live subset = the ideal
                       ▼
        ideal ──fold──▶  the files you see on disk
```

Two things this buys you that a diff cannot, and they are the reason to reach for ugh at all: the
recorded **reason** a symbol is the way it is, and the exact **cost of removing** something — the
count of later ops built on top that would come out with it. `ugh_show` answers both.

`ugh log` prints this as one row per feature: its handle (what a human hands to `ugh revert`), its
label, and how many edits it got in each of the last few saves, newest to the right under the save's
short sha. Edits no feature owns get one row of their own.

```
id        feature                          3f2a1c0 9b1e4d2 c0a7e11
3f9a1b2c  waitlist promotion                     2       .       5
7c21d0e4  enrollment drop                        .       3       .
          (no feature)                           .       .       1
```

That list is for a person at a terminal, not for you to read as text; see "Showing ugh output to a
human" below before you ever put it in a transcript. You read the model through the narrow tools
next.

## Orient with one call

Before your first edit, run this. It works in any harness that has a shell, so it does not depend on
MCP being wired up:

```bash
ugh status --json
```

It reports the counts, whether there is unsaved work, and what needs a human decision: open forks,
working-tree drift, a git history rewrite.

Check for a `.sgt/` directory before you rely on any of it. If there is no `.sgt/`, use plain git
and stop reading here.

Two states mean **stop and tell the human** rather than working around them:

- a paused git merge/cherry-pick/revert — the tree holds conflict markers, `ugh save` refuses
  outright, and anything you record now is recorded against a half-merged tree.
- git history moved backward — someone ran `reset --hard`, `amend`, or `branch -f`, so ugh's
  recorded state names commits that no longer exist. Every count you read is inflated until
  `ugh check --repair` runs.

Neither is yours to fix silently. Say what you found and what fixes it.

## Which read answers which question

Reach for the narrowest one. Costs measured on that same 290-commit repo, and the shape matters
more than the exact number: they stay flat as history grows.

| You want to know | Use | Cost |
|---|---|---|
| What happened outside the plan | `ugh_drift` | ~10 tokens |
| What is this id, what would a revert cost, why is it here | `ugh_show` | ~720 tokens |
| What did this file look like back then | `ugh_show` with `at` | small |
| Which thing matches a description | `ugh_find` | small |
| What happened recently | `ugh_log` | ~1,400 tokens, capped at 30 ops |

There is no `ugh_grid` tool on purpose — the grid is one cell per feature-and-commit, meant to be
*drawn* by a UI, and it measured about 129,000 tokens on that repo and grows with history. If you
truly need the raw join, shell out to `ugh log --json` and page it yourself.

The first ugh call in a session mines the working tree and can take several seconds; later calls are
fast. That is one warm-up, not a per-call cost, so it is not a reason to avoid a second read.

## Read before you write

Before editing anything unfamiliar, run `ugh_show` on the symbols you are about to touch: its
`asked` attribute carries the words that produced that code, and a constraint you would otherwise
rediscover by breaking it is usually sitting right there.

Before proposing anything destructive, run `ugh_show` on the target. It reports how many edits a
revert would remove and how many of those are *later work built on top* — the number that decides
whether a revert is a small correction or a demolition. It writes nothing and never calls an LLM, so
run it freely. Then state that number to the human instead of the bare command.

## What is yours and what is theirs

Yours: reading (`ugh_show`, `ugh_find`, `ugh_log`, `ugh_drift`), the plan loop
(`ugh_plan_intake` → work → `ugh_checkpoint` → `ugh_plan_done`), and **`ugh_save`**. See the
`ugh-plan` skill for the loop and for how ownership works when several agents share a repo.

Saving is yours on purpose, and it comes with two obligations. First, `ugh_save` asks for your own
words (`message`), and they become the save's subject, the recorded intent, and the name of any
feature born from the work — so write the sentence you would have put in a commit message. Second,
pass along the *user's* words too: `prompt` takes the ask that drove this work, verbatim as you
received it (their words, not your paraphrase), and `claude_session_id` takes your session UUID
(read `$CLAUDE_CODE_SESSION_ID` via Bash; never `$CLAUDE_CODE_BRIDGE_SESSION_ID`, which can carry a
parent's id). Together they let `ugh show` and the checkpoint history answer with what the user
actually asked for — and let the conversation that produced a checkpoint be found and resumed from
it later. The same two fields ride `ugh_revert`/`ugh_restore` when the user asked for the removal
or return in so many words. The verb is additive and `ugh undo` reverses it, which is what makes it
safe to hand over; the alternative was a human relaying every save by hand at a terminal, which is
the loop ugh exists to remove.

Theirs, and deliberately not exposed to you as tools:

- **`ugh sync`, `ugh resolve`** change *shared* state and are gated behind an interactive
  confirmation in the terminal. Running them through a shell skips that gate — and unlike a save,
  these are not reversed by `ugh undo`.
- **`ugh feature rename`** sets a label that permanently overrides ugh's generated one. That is a
  judgement a human makes looking at the graph.

When one of these is the right next step, say so and hand it over with the exact command. That is
more useful than doing it, because they can see the consequence in the terminal that you cannot.

## What the work was asked for

Every answer from `ugh show` carries an `asked` attribute: the words the human typed that produced
that work. `asked.top.gist` is an excerpt starting at the request (a real prompt opens with
throat-clearing and carries its reasoning after the ask, so the excerpt is the part worth quoting),
`asked.top.source` says whose words they were -- their own typing, your relay of it, or your
paraphrase -- and `asked.count` says how many asks there were. `asked: true` returns every one in
full (`asked.asks[].text`).

Two rules follow from it. **Quote it, do not paraphrase it.** When you tell somebody what a piece of
work was for, their own sentence is the answer, and a paraphrase of a paraphrase is how a record
stops being trustworthy. **Read it before you edit from a chapter**, because the fastest way to
undo somebody else's decision is to re-derive their intent from the code instead of reading what
they asked for.

An empty `asked` is not a hint to invent one. It means this work predates the prompt hook or was
saved outside a conversation, and saying so is the honest answer.

## Showing ugh output to a human

ugh's terminal output is built for a terminal: wide aligned columns. Pasted into a transcript it
wraps badly and costs tokens.

- Reading it yourself: use `--json`, or the MCP tool, and summarize in your own words.
- Showing it to them: only output that is already narrow — `ugh show` or `ugh status`.
- Never paste `ugh log` into a transcript. It is wide and tall, and meant to be read in a terminal.
  Point at the command instead and let them run it.

When you name a command, name one that runs. An unrecognised verb exits non-zero and prints the
closest real verbs, so a wrong guess is recoverable — but it still costs a round trip, and a wrong
command in a message *to the human* costs their trust.

## When there is no MCP

In a harness with only a shell (Codex, a bare CLI runner), every read above has a `--json`
equivalent: `ugh show <sel> --json`, `ugh find "<words>" --json`, `ugh log --json`,
`ugh status --json`.
The plan loop is `ugh plan intake|status|resume|adopt|done|abandon`, and matching happens on the
human's `ugh save`. See `references/cli-fallback.md` for the tool-to-command mapping.

## References

- `references/cli-fallback.md` — MCP tool to CLI command mapping, for shell-only harnesses.
- `references/costs.md` — the measurements above, how they were taken, and how to re-take them when
  they go stale.
- The `ugh-plan` skill — the plan loop and session ownership.
- The `ugh-workflow` skill — choosing between ugh's look-alike verbs (the shapes of revert, revert
  versus undo, sync versus push).
