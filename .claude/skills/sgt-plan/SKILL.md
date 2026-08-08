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
   Also pass `claude_session_id`: read `$CLAUDE_CODE_SESSION_ID` via Bash. Use that variable and
   not `$CLAUDE_CODE_BRIDGE_SESSION_ID`, which in a nested run can carry a *parent* session's id.
   That distinction matters twice over. It is the id the user resumes this exact conversation with
   (`claude --resume <uuid>`) if the plan stalls, and it is the identity the ownership check reads —
   so a parent's id would make you claim a plan under someone else's name, which is exactly the
   confusion the check exists to prevent. If the variable is genuinely unset, omit it rather than
   guessing; an unowned plan is claimable by anyone, which is the safe degrade.

2. **Work** — implement the steps in the working tree (or a `sgt session` worktree). Edit code
   normally; you do not touch the plan while working.

3. **Checkpoint** — call `sgt_checkpoint` (no args) for a read-only preview of which mined ops
   overlap which pending steps, plus any drift. When a group is right, call it again with
   `confirm: [{hollow_ids, op_ids}]` and your `claude_session_id` to record it. The step flips to
   `matched`. Nothing is confirmed unless you name the group, so preview freely — the preview is the
   cheap call, and reading it before confirming is how you avoid crediting a step with work that
   happens to touch the same file.

4. **Done** — a session closes itself the moment its last step is confirmed. Call `sgt_plan_done`
   with your `session_id` only for the leftover case: steps you ended up building differently than
   predicted, which will never match. `plan_done` closes the session so it stops showing as active;
   the record stays as completed history. (To discard a plan entirely, that is `sgt plan abandon`
   on the CLI, not `plan_done`.)

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
  agent/task name), and pass your `claude_session_id` so the check knows who you are.
- **Confirm and close only your own session.** `sgt_plan_done` and `sgt_checkpoint`'s `confirm`
  refuse when the plan belongs to a different Claude session, and the error names the owner. Match
  computation is also scoped per session (each only matches ops mined since its own baseline against
  its own steps), so a confirm in your session never disturbs another's.
- **If you need to take over a stalled plan, adopt it.** When an agent stops mid-plan, its session
  stays owned and no one else may close it. `sgt_plan_adopt` transfers ownership to you without
  losing anything: the steps, their confirmed matches, and the pending predictions all survive, so
  you continue where that agent stopped. Do not re-intake the same plan instead — that mints a
  second set of predictions for work already done, and both then show as unmatched.
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
