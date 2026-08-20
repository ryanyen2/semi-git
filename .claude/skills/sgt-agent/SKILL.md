---
name: sgt-agent
description: Read this whenever you are doing coding work in a repository that has a `.sgt/` directory or where sgt/semi-git is mentioned, even if the user never names sgt. It tells you how to orient in one cheap call, which sgt read answers which question and what each one costs in tokens, which sgt actions are yours (reading, the plan loop, and saving your own work with your own words) versus the human's (the shared-state verbs), how to show sgt output in a transcript without dumping terminal control codes, and what to do when MCP is unavailable and you only have a shell. Load it before your first sgt call, not after a surprising one.
---

# Working in an sgt repo

sgt records *symbol-level* history: each edit to a function, class, or file becomes an "op", and
related ops are grouped into "features". That gives you two things plain git cannot: the recorded
*reason* a piece of code is the way it is, and the exact consequence of removing something,
including the later work that depends on it.

Your job is to use those two things and to leave the parts that belong to the human alone. The rest
of this file is about doing that without wasting their context window or their time.

## The shape of what you are reading

Two paragraphs of model, because the reads below only make sense against it.

git stores your code as commits of line-diffs. sgt re-reads those same commits and breaks each one
into **ops** — one op per changed symbol (a function, a class): what it touched, the version it
started from, the version it produced, what it was built on. It replays the ops it holds, symbol by
symbol, to rebuild your files. The live subset of ops is the **ideal** (valid = every op's
prerequisites are in, and no symbol has two competing versions); the files on disk are just that
ideal **folded** back into text, so they never drift from the history the way a stale diff can.
Related ops are grouped into **features** — the unit you and the human actually think in.

```
git:    commit ──── commit ──── commit           lines & diffs
                       │  sgt re-reads each commit
                       ▼
ops:      ⋯ e4  e5  e6  e7  e8  e9 ⋯              one op = one symbol's edit
            └──┬──┘ └──┬──┘ └─┬──┘
features:    "drop"  "waitlist" "clash"          ops that change together
                       │  the currently-live subset = the ideal
                       ▼
        ideal ──fold──▶  the files you see on disk
```

Two things this buys you that a diff cannot, and they are the reason to reach for sgt at all: the
recorded **reason** a symbol is the way it is (`sgt_recall`), and the exact **cost of removing**
something — the count of later ops built on top that would come out with it (`sgt_show`).

`sgt log --map` draws this as one lane per feature, foundations at the top, over a shared
commit-time axis. A lane's glyphs (`▁▂▃▄▅▆▇█`, dim `·` for quiet) read as *how busy* that stretch
was and *when*; the `@n` chips are its checkpoints, and `@n` is the handle a human hands to
`sgt revert`; a `◇` is a step a plan predicted but no code fills yet.

```
          c0 ───────────────────────────── cN
 enrollment
   3f9a  waitlist promotion   ·▂▃····▅█·   @1 seed  @3 promote-on-free
   7c21  enrollment drop      ···▃▄······   @2 drop
 scheduling
   a4d0  slot-clash guard     ▂···▆······   @1 ranges_clash
   b8e1  bulk import          ·······◇      ← ◇ = planned, no code yet
```

That grid is meant to be *drawn* — live in a terminal or the workbench — not read by you as text; see
"Showing sgt output to a human" below before you ever put it in a transcript. You read the model
through the narrow tools next.

## Orient with one call

Before your first edit, run this. It works in any harness that has a shell, so it does not depend on
MCP being wired up:

```bash
sgt now --json
```

It prints what was asked for, whether there is unsaved work, what needs a human decision, what was
recently done, and the one suggested next action.

Check for a `.sgt/` directory before you rely on any of it. In a repo that was never `sgt init`ed,
`sgt now` still exits 0 and returns an empty-but-valid payload, so an empty result does not tell you
whether the repo is untracked or simply clean. If there is no `.sgt/`, use plain git and stop
reading here.

Use it rather than assembling the same picture yourself. Measured on a 290-commit repo, `sgt_now` is
about 530 tokens, and reading `sgt_now` plus `sgt_log` plus `sgt_status` together costs about 5,200.
You would spend most of that on detail you are not going to act on.

Two lines in the brief mean **stop and tell the human** rather than working around them:

- `BLOCKED: a paused git merge/cherry-pick/revert` — the tree holds conflict markers, `sgt save`
  refuses outright, and anything you record now is recorded against a half-merged tree.
- `BLOCKED: git history moved backward` — someone ran `reset --hard`, `amend`, or `branch -f`, so
  sgt's recorded state names commits that no longer exist. Every count you read is inflated until
  `sgt advanced resync` runs.

Neither is yours to fix silently. Say what you found and what fixes it.

## Which read answers which question

Reach for the narrowest one. Costs measured on that same 290-commit repo, and the shape matters
more than the exact number: the first three stay flat as history grows, the last does not.

| You want to know | Use | Cost |
|---|---|---|
| Why is this code the way it is | `sgt_recall` | ~10 tokens + matches |
| What happened outside the plan | `sgt_drift` | ~10 tokens |
| Where am I, is anyone mid-something | `sgt_now` | ~530 tokens, flat |
| What needs a human right now | `sgt_now` | ~530 tokens, flat |
| What is this id, what would a revert cost | `sgt_show` | ~720 tokens |
| What did this file look like back then | `sgt_show` with `at` | small |
| What happened recently | `sgt_log` | ~1,400 tokens, capped at 30 ops |
| Scalars: coverage, oracle, working-tree drift | `sgt_status` | ~3,300 tokens |

`sgt_status` is the one to think twice about: four times the brief for a question you rarely have.
There is no `sgt_grid` tool on purpose — the grid is one cell per feature-and-commit, meant to be
*drawn* by a UI, and it measured about 129,000 tokens on that repo and grows with history. If you
truly need the raw join, shell out to `sgt log --json` and page it yourself.

The first sgt call in a session mines the working tree and can take several seconds; later calls are
fast. That is one warm-up, not a per-call cost, so it is not a reason to avoid a second read.

## Read before you write

sgt's best feature for you is `sgt_recall`: pass the symbols you are about to touch and it returns
the recorded reasons behind them, plus intents someone stated but never landed. A constraint you
would otherwise rediscover by breaking it is usually sitting right there. Do this before editing
anything unfamiliar.

Before proposing anything destructive, run `sgt_show` on the target. It reports how many edits a
revert would remove and how many of those are *later work built on top* — the number that decides
whether a revert is a small correction or a demolition. It writes nothing and never calls an LLM, so
run it freely. Then state that number to the human instead of the bare command.

## What is yours and what is theirs

Yours: reading (`sgt_show`, `sgt_recall`, `sgt_log`, `sgt_now`, `sgt_drift`), the plan loop
(`sgt_plan_intake` → work → `sgt_checkpoint` → `sgt_plan_done`), and **`sgt_save`**. See the
`sgt-plan` skill for the loop and for how ownership works when several agents share a repo.

Saving is yours on purpose, and it comes with one obligation. `sgt_save` asks for your own words, and
they become the save's subject, the recorded intent, and the name of any feature born from the work —
so write the sentence you would have put in a commit message. That sentence is the thing only you
have at that moment, and it is what makes the history answer "why" later. The verb is additive and
`sgt undo` reverses it, which is what makes it safe to hand over; the alternative was a human
relaying every save by hand at a terminal, which is the loop sgt exists to remove.

Theirs, and deliberately not exposed to you as tools:

- **`sgt land`, `sgt sync`, `sgt propose land`, `sgt resolve`** change *shared* state and are gated
  behind an interactive confirmation in the terminal. Running them through a shell skips that gate —
  and unlike a save, these are not reversed by `sgt undo`.
- **The feature verbs** (`sgt feature regroup merge|split|move`, `sgt feature rename`) set labels
  and regroupings that permanently override sgt's generated ones. That is a judgement a human makes
  looking at the graph.

When one of these is the right next step, say so and hand it over with the exact command. That is
more useful than doing it, because they can see the consequence in the terminal that you cannot.

## Showing sgt output to a human

sgt's terminal output is built for a terminal: ANSI colour, box drawing, wide aligned columns. Pasted
into a transcript it renders as noise and costs tokens for the escape codes.

- Reading it yourself: use `--json`, or the MCP tool, and summarize in your own words.
- Showing it to them: only output that is already narrow — `sgt show` (plain text, no colour, so no
  flag needed), or `sgt now --no-color` / `sgt log --summary --no-color`.
- Never paste `sgt log --map` or the rail into a transcript. They are wide, tall, and meant to be
  read live in a terminal. Point at the command instead and let them run it.

When you name a command, name one that runs. sgt's verbs were reorganised: several moved under
`sgt advanced` or `sgt feature`, and a few became `sgt log` modes. An unrecognised verb exits
non-zero and prints the command that replaced it, so a wrong guess is recoverable — but it still
costs a round trip, and a wrong command in a message *to the human* costs their trust. `--no-color`
is not universal either: `sgt now` and `sgt log` take it, `sgt show` does not need it.

## When there is no MCP

In a harness with only a shell (Codex, a bare CLI runner), every read above has a `--json`
equivalent: `sgt show <sel> --json`, `sgt now --json`, `sgt log --json`, `sgt log --summary --json`.
The plan loop is `sgt plan intake|status|resume|adopt|done|abandon`, and matching happens on the
human's `sgt save`. See `references/cli-fallback.md` for the tool-to-command mapping.

## References

- `references/cli-fallback.md` — MCP tool to CLI command mapping, for shell-only harnesses.
- `references/costs.md` — the measurements above, how they were taken, and how to re-take them when
  they go stale.
- The `sgt-plan` skill — the plan loop and session ownership.
- The `sgt-workflow` skill — choosing between sgt's look-alike verbs (four shapes of revert, feature
  verbs versus op verbs, land versus propose).
