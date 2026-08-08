---
name: sgt-plan
description: Use when you (an AI coding agent) are about to implement a multi-step task in an sgt-tracked repo and want your intent recorded as a plan that the sgt graph can check your work against. Teaches the draft -> work -> checkpoint -> done loop over the MCP tools (sgt_plan_intake, sgt_checkpoint, sgt_drift, sgt_plan_done, sgt_plan_adopt), and how session ownership works when several agents share one repo. This is the on-ramp: the agent drafts the plan, not the human.
---

# Drafting and closing an sgt plan

In sgt, a *plan* is your stated intent recorded before you do the work: a list of predicted
steps, each an off-chain "hollow" op that never touches the real op DAG. As you edit code, sgt
mines real ops and matches them back to your predicted steps. This is how the graph shows whether
what you built matches what you intended, and what happened outside any plan.

You draft the plan. The human does not write it. Your job is to record intent up front, then let
sgt reconcile it against the code you actually produce. The materialized code is always the source
of truth: a mismatch between plan and code is information, never an error to "fix" by rewriting the
plan.

## The loop

1. **Draft** — call `sgt_plan_intake` with your plan text and an explicit `session_id` you own
   (see ownership below). Decompose the task into concrete steps, one per unit of work, each named
   at the granularity you'll build it (a `file::Symbol`, or a bare `file` when a step spans a whole
   module). Intake mines the working tree first, so its baseline reflects current reality.
   Pass `$CLAUDE_CODE_SESSION_ID` (read it via Bash) as `claude_session_id`. Never pass
   `$CLAUDE_CODE_BRIDGE_SESSION_ID` — in a nested run it can carry a *parent* session's id. It carries three jobs at once, which is why the wrong one breaks things quietly:
   the `UserPromptSubmit` hook keys every captured prompt by this same id, so it is what joins the
   user's own words to the commits this plan produces; it is the id `claude --resume <uuid>` accepts
   if the plan stalls part-way; and it is the identity the ownership check reads, so a parent's id
   would make you claim a plan under someone else's name. All three appear to work and none of them
   do. If the variable is genuinely unset, omit it rather than guessing — an unowned plan is
   claimable by anyone, which is the safe degrade.

2. **Work** — implement the steps in the working tree (or a `sgt session` worktree). Edit code
   normally; you do not touch the plan while working. When a step's work is finished, record it
   with `sgt_save` and pass `message` — your own words about what this work was. They become the
   save's subject, the recorded intent, and the name of any feature born from it, so writing the
   sentence you would have written in a commit is the whole of the obligation.

3. **Checkpoint** — call `sgt_checkpoint` (no args) for a read-only preview of which mined ops
   overlap which pending steps, plus any drift. When a group is right, call it again with
   `confirm: [{hollow_ids, op_ids}]` and your `claude_session_id` to record it. The step flips to
   `matched`. Nothing is confirmed unless you name the group, so preview freely — the preview is the
   cheap call, and reading it before confirming is how you avoid crediting a step with work that
   happens to touch the same file.

4. **Done** — a session closes itself the moment its last step is confirmed. Call `sgt_plan_done`
   with your `session_id` (and your own `claude_session_id`, so closing someone else's plan is
   refused rather than silently honored) only for the leftover case: steps you ended up building
   differently than predicted, which will never match. `plan_done` closes the session so it stops
   showing as active; the record stays as completed history. (To discard a plan entirely, that is
   `sgt plan abandon` on the CLI, not `plan_done`.)

**If you were interrupted and are picking the work back up**, call `sgt_plan_intake` again with the
same `session_id`. That is a resume, not a new plan: the original baseline and creation time are
kept, so everything you built before the interruption stays attributed to this plan instead of
reappearing as drift, and the steps are re-decomposed from whatever you now intend to do. Steps the
new plan no longer has are cleaned up.

**Working without a plan.** This loop is worth the ceremony for multi-step work, and most work is
not that. If you are just doing what was asked, skip it: edit, then `sgt_save` with your own words.
`sgt_now` tells you where things stand at any point — including `working_on`, the user's own prompt
verbatim, which is what to read when picking work back up rather than inferring the task from a
diff. `sgt_show` reads a file as it was at a past point without checking anything out.

Between steps 3 and 4, `sgt_drift` lists ops no active plan predicted. Drift is not a chore to
resolve; it is a read-only diff of what happened outside your stated plan. Read it to confirm no
unplanned change crept in, then land the work as usual.

## Session ownership when agents work concurrently

Several agents can have live plans at once. Each session is owned by exactly one agent, and this is
now *enforced* rather than advisory — worth knowing, because the failure it prevents is silent.
Closing or abandoning a plan unlinks its still-pending predictions, so an agent that closed a
sibling's plan used to leave that sibling working against steps that could no longer match, with
nothing anywhere explaining why.

- **Pick a distinct `session_id`** at intake that identifies your task (a stable slug, e.g. your
  agent/task name), and pass your `claude_session_id` so the check knows who you are. Never reuse
  another agent's id.
- **Confirm and close only your own session.** `sgt_plan_done` and `sgt_checkpoint`'s `confirm`
  refuse when the plan belongs to a different Claude session, and the error names the owner — a
  refusal, not a courtesy. Match computation is also scoped per session (each only matches ops mined
  since its own baseline against its own steps), so a confirm in your session never disturbs
  another's.
- **If you need to take over a stalled plan, adopt it.** When an agent stops mid-plan its session
  stays owned and no one else may close it. `sgt_plan_adopt` transfers ownership to you without
  losing anything: the steps, their confirmed matches, and the pending predictions all survive, so
  you continue where that agent stopped. Do not re-intake someone else's plan to get around the
  refusal — and note that re-taking *your own* id is a supported resume, which keeps the original
  baseline and re-decomposes only the steps.
- Concurrent sessions all appear in the graph as separate active plans. Finishing them one by one
  is the expected shape: each agent drafts, builds, checkpoints, and closes its own.

## Writing good steps

- One step per coherent unit of work; name the entity or file it will touch.
- Prefer `file::Symbol` granularity when you know the symbol (`crdt.py::RGA`), a bare `file`
  (`server.py`) when the step is whole-module. The matcher joins on the qualname (file is treated
  as a guess that may drift) or on file scope for a bare-file step.
- Do not predict test names or incidental helpers you can't foresee; those surface as drift, which
  is fine.

## Related

- `sgt-agent` — orienting in an sgt repo before you start, what each read costs, and the line between
  your actions and the human's (in short: you never run `sgt save`; checkpointing makes your work
  visible without it).
- `sgt-workflow` — choosing between the look-alike verbs once you need one.
