# Phase one status report

Phase one is blocked on one decision that only you can make, and everything else that was
blocking it is now fixed. The decision is whether to change how sgt walks git history, because
the change forces every measurement taken so far to be taken again. The rest of this report says
what we tested, what we found, what we fixed, and what is left.

## What phase one is

Phase one is the technical evaluation described in
`docs/plans/2026-08-14-001-technical-evaluation-execution-plan.md`. We run sgt against 18 small
test repositories we wrote ourselves and against 29 real repositories downloaded from GitHub, and
we check whether sgt does what the paper says it does. The plan sets rules before any data is
collected, so that we cannot pick a favourable number after the fact. One rule matters for
everything below. If a bug we find forces a change to how sgt reads git history, then the version
number of the miner goes up, and every measurement already taken has to be taken again.

## What we tested

We tested three things, and the first one is the repository's own test suite. We ran the full
suite with test ordering fixed, and the result is recorded in the section below on what is
currently green.

Second, we tested whether the two warnings sgt prints before a revert can actually be seen by
somebody who asks for them. A revert removes recorded work, and sgt is supposed to warn when the
removal leaves other code referring to something that is no longer there, or when the removal
overlaps a line that a later edit changed. The paper argues that naming the affected function
before the revert runs is the honest limit of what sgt should do on the developer's behalf.

Third, we tested the tools that produce the numbers, which are a random operation harness and an
aggregator that pools its output. A tool that measures wrongly is worse than no measurement,
because the number still looks like a number.

## What we found and fixed in sgt

The two warnings were unreachable, then reachable but invisible, and both are now fixed and
covered by tests. First, both warnings sat after an early return, so the ordinary case of removing
a function outright could never produce either of them. Second, once that was fixed, the dry run
still carried neither warning, so a developer or an agent asking what a revert would do was told
nothing, and only the report printed after the change arrived carried the answer. Third, a revert
preview claimed that nothing depended on the target while the same reply listed a locked
prerequisite, which is a plain contradiction inside one message.

We also found that the warning about references to removed code fires far less often than we
expected, and the reason is a design choice rather than a defect. sgt only warns when something
was removed outright. Rolling a function back to an earlier version removes nothing, so there is
nothing to warn about, and most reverts in a real history are rollbacks of that kind. Measured on
two real repositories, reverts that remove a function warn 7 times in 33 attempts, and reverts
drawn at random warn once in 60. The paper already describes the warning correctly, so no
correction was needed there.

Separately, we qualified two sentences in the design section that stated a mechanism without
saying what it costs. sgt records which other functions a new piece of code refers to, but it only
records a reference when the name resolves to exactly one function in the whole codebase. On a
codebase that reuses short names across files, the reference goes unrecorded, and one repository
in our sample records no references at all.

## What we found and fixed in the measurement tools

Four defects in the harness and the aggregator were the same confusion, which is treating a zero
as a missing value. Asking the harness to replay zero operations ran all forty of them and
reported itself as a replay, and the fault was in two separate places, so fixing the first one
changed nothing. Replaying a run on a real repository was impossible, because the run recorded the
temporary working copy rather than the repository it was cloned from, and replay then looked the
name up in the list of test repositories and crashed. The aggregator could not tell which runs came
from real repositories, for the same reason. The aggregator also read a truthful zero as missing
and substituted forty, which would have reported the one correct run as cut short.

All four are fixed, and each fix was checked by running it rather than by reading it. Replaying
zero operations now runs zero and finishes in seconds instead of two minutes. Replaying three
operations runs the same three operations as the original run, in the same order. A fresh run on a
real repository now replays from its own record with the same operations and the same failure
count. The aggregator now separates real repositories from test repositories with no help from any
other file.

None of the four touch sgt itself, so no measurement has to be repeated on their account.

## The one issue that needs your decision

sgt loses commits when it reads a repository's history, and it reports the reading as complete.
sgt reads history in ten second chunks so that the tool stays responsive, and it records where it
stopped as a single commit. A single commit cannot describe where you are in a branching history,
so when the ten seconds run out at the wrong moment, commits on side branches are stepped over and
never read. On one repository we mined twice, the second reading was missing 49 operations from one
commit, and 8 commits carrying about 30 files of Python were missing from both readings. Both
readings reported themselves complete.

A second problem has the same cause. When a function is renamed and the two sides of the rename
fall in different chunks, sgt records the dependency against the old name, which no operation ever
writes. On another repository, two readings of the same commit produced the same number of
operations but 108 different identifiers, and the number of operations that can ever be composed
differed between them.

The fix is clear and we have not applied it. Where sgt stopped has to be recorded as the set of
commits still to read rather than as one commit, and completeness has to be checked against the
number of commits git reports. Rename identity has to be resolved against the stored record rather
than per reading. Both change how sgt reads history, so under the plan's own rule the miner version
goes up and every completed measurement is repeated. Repeating them invalidates the corpus results,
two sections of the paper, and both of the repositories prepared for the user study.

The plan also has a hard stop that we have reached. Two of the 10,237 operations in the completed
sweep failed to recover, the plan says any recovery failure stops the work for a human decision,
and both failures come from the same part of sgt that the fix above would change.

## Also waiting on you

- Whether to commit the evaluation records. The `docs/eval` directory is not tracked by git and
  holds about 6.7 MB. Without it, the sweep figures in the paper cannot be reproduced by anybody.
- Whether to unstage the 54 files currently sitting in the git index from earlier sittings, and
  whether to drop the retained stash entry.
- Whether to add a test dependency so that the VS Code extension's confirm dialog can be tested.
  Nothing currently checks that clicking the button reaches the command.

One plan rule cannot be met as things stand. The plan requires that the agent which runs a work
package is not the agent that checks it, and this session is told not to use subagents, so no
independent check has been made.

## What is currently green

Recorded here after the run finished, in the next revision of this file.

## Next step

Decide whether to apply the history reading fix. If you say yes, sgt changes, the miner version
goes up, and we re-run the corpus sweep and rebuild the study repositories before anything else
happens. If you say no, we record both problems as stated limits of the tool, and phase one
finishes on the numbers we already have.
