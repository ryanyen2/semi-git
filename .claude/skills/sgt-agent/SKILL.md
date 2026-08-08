---
name: sgt-agent
description: Read this whenever you are doing coding work in a repository that has a `.sgt/` directory or where sgt/semi-git is mentioned, even if the user never names sgt. It tells you how to orient in one cheap call, which sgt read answers which question and what each one costs in tokens, which sgt actions are yours versus the human's (sgt save makes commits, so it is theirs), how to show sgt output in a transcript without dumping terminal control codes, and what to do when MCP is unavailable and you only have a shell. Load it before your first sgt call, not after a surprising one.
---

# Working in an sgt repo

sgt records *symbol-level* history: each edit to a function, class, or file becomes an "op", and
related ops are grouped into "features". That gives you two things plain git cannot: the recorded
*reason* a piece of code is the way it is, and the exact consequence of removing something,
including the later work that depends on it.

Your job is to use those two things and to leave the parts that belong to the human alone. The rest
of this file is about doing that without wasting their context window or their time.

## Orient with one call

Before your first edit, run this. It works in any harness that has a shell, so it does not depend on
MCP being wired up:

```bash
python -m scripts.sgt_brief
```

It prints a short block: whether anything blocks sgt, whether there is unsaved work, what needs a
human decision, what was recently done, and the one suggested next action. Exit status 2 means the
repo is not sgt-tracked, so use plain git and stop reading here.

Use it rather than assembling the same picture yourself. Measured on a 290-commit repo, the brief is
about 80 tokens; the three reads it replaces (`sgt_now` + `sgt_log` + `sgt_status`) total about
5,200. You would spend most of that on detail you are not going to act on.

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
| Where am I, is anyone mid-something | `scripts/sgt_brief` | ~80 tokens, flat |
| What needs a human right now | `sgt_now` | ~530 tokens, flat |
| What is this id, what would a revert cost | `sgt_show` | ~720 tokens |
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

Yours: reading (`sgt_show`, `sgt_recall`, `sgt_log`, `sgt_now`, `sgt_drift`), and the plan loop
(`sgt_plan_intake` → work → `sgt_checkpoint` → `sgt_plan_done`). See the `sgt-plan` skill for that
loop and for how ownership works when several agents share a repo.

Theirs, and deliberately not exposed to you as tools:

- **`sgt save`** makes a git commit. Your edits are absorbed into sgt's state by `sgt_checkpoint`'s
  mine-on-contact, so you never need to save to make your work visible. Do not shell out to
  `sgt save` to get around this; committing on someone's behalf is the thing being avoided, not an
  implementation detail.
- **`sgt land`, `sgt sync`, `sgt propose land`, `sgt resolve`** change shared state and are gated
  behind an interactive confirmation in the terminal. Running them through a shell skips that gate.
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
human's `sgt save`. `scripts/sgt_brief` works unchanged. See `references/cli-fallback.md` for the
tool-to-command mapping.

## References

- `references/cli-fallback.md` — MCP tool to CLI command mapping, for shell-only harnesses.
- `references/costs.md` — the measurements above, how they were taken, and how to re-take them when
  they go stale.
- The `sgt-plan` skill — the plan loop and session ownership.
- The `sgt-workflow` skill — choosing between sgt's look-alike verbs (four shapes of revert, feature
  verbs versus op verbs, land versus propose).
