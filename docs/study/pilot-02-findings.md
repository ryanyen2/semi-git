# Pilot 2: the git condition

Date: 2026-08-13. One participant, called P2 here, working in the git condition
on coursecraft. Same setup and same requests as pilot 1, which ran the sgt
condition. Both participants were agents rather than people.

Read this next to `pilot-01-findings.md`. Pilot 1 was mostly about defects in
sgt. This one is about whether the study design works, and it found four things
that needed fixing. All four are fixed.

The commit ids quoted from P2's session are the ones that existed during the
session. The git-condition copies were rewritten afterwards, so those ids no
longer resolve. The current ids are in `participant-materials.md`.

## What happened

P2 finished all six requests, including both optional ones, inside the time
budgets. The final state passed 35 tests with a clean working tree.

Scored with `scripts/score_study_repo.py` at the end of request 3: no collateral
damage, all three target features removed, the program starts, and the waitlist
and notices commands are gone. A clean pass, and the first scored result in the
study.

## Two things the design got wrong

### Request 6 is not out of reach in the git condition

The protocol said that rewriting history properly needs an interactive rebase,
that there wouldn't be time, and that any honest attempt should count. P2 did it.
They scripted the rebase todo, split the commit in two, and then checked their
work by comparing tree hashes before and after rather than by eye. The trees were
identical, the suite stayed green, and each half passes on its own so the history
is still bisectable.

That result is better than the sgt condition managed. In pilot 1 sgt had already
split the same commit on its own, which is the more impressive half, but the
participant could not give the two halves clear names because nothing renames a
checkpoint, so they stopped.

Request 6 needs a real rubric for both conditions, and we should expect the git
condition to complete it.

### The projects for the git condition were contaminated, and are now clean

The copies used for the git condition carried commits written by sgt, e.g.,
`sgt revert f-10462e17@2` and `sgt undo: restore prior ideal`. P2 noticed within
a minute of opening the log and wrote "didn't expect tooling exhaust in the
history". A participant in the git condition should not be able to tell that
another tool was involved, and the messages mean nothing read as plain git
history, so the condition looked worse than a real project would.

Cleaning them up turned up three more problems in the same copies.

- coursecraft contained "section capacity limits" twice, with an undo between
  them. The undo was dated a month after the story and authored by the
  researcher. P0 saw the duplicate in the sgt condition and assumed it was real
  history.
- The first commit in each copy was authored by the researcher under a real name
  and university email, and dated after every other commit.
- The `study-start` tag pointed at a line of history that still held the sgt
  commits, so `git log --all` would have shown them even after the rewrite.

All four are fixed. The two sgt messages are plain ones now, the duplicate and
the undo are dropped so capacity is added once, the first commit belongs to the
project's own author on the day the project starts, and the tag points at the
frozen state. The final code is byte for byte unchanged in both copies, both
still pass 38 tests, and no commit reachable from any ref mentions sgt.

The copies for the sgt condition were left alone, because a maintainer who used
sgt would really have those commits.

Every commit id in the git-condition copies changed. The answer keys were
re-derived, and the ids quoted from P2's session below are the pre-cleanup ones.

### The request 1 ticket had a false premise, and both participants spent time on it

The ticket said course search "started showing times differently". Search did not
exist before the commit in question, so there was no earlier format to change.
Both participants noticed and wrote a paragraph about it, in different
conditions, which is budget spent on our wording rather than on the task.

P2 wrote that search "didn't start showing times differently, it didn't exist
before that commit". P0 wrote that they could not tell whether the student meant
a new format or capitalised day names, and noted that both readings point at the
same commit anyway.

The ticket now says the format is unfamiliar rather than changed. The task is
still to find that one commit did two unrelated things.

## The comprehension rubric works

Scored P2's answer against the key: 2 out of 2. They named the right commit for
their condition and identified both pieces of work and that only one of them is
mentioned in the message. They recorded their confidence as asked. Nothing about
the rubric needed interpretation, so it can be used as written.

## What the two conditions looked like side by side

Both participants got request 1 right. How they got there differed.

P0 used sgt and went from an English sentence to the answer in four commands,
because the tool attributes the day parsing fix to a save whose message only
mentions search. P2 had no such index, so they guessed at the mechanism, guessed
wrong, then grepped the code to find where day parsing lives and asked git about
that file. The tell was that a commit called "add course search" appears in the
history of the slot parser.

P2 was clear about the risk in their own approach:

> If I'd worked top-down from commit subjects, as you do when you're new and
> trust the log, I'd have reported "two separate changes" and gone hunting for a
> second one that doesn't exist.

On requests 2 and 3 the git condition was clean and quick, and the sgt condition
was not, but that comparison is not usable. The sgt participant spent most of
their budget repairing a file the tool had corrupted, which is now fixed. The
comparison has to be run again on the fixed build.

One thing worth noting for the analysis. P2 chose to hand edit rather than revert
commits, because they read the log carefully enough to see that drop and waitlist
promotion had landed in one commit, so a revert would have taken drop out too.
They only avoided the trap by reading the subject lines closely. Their comment
was that if the message had said "drop command" they would have reverted it and
walked into it.

Both participants independently found the same false green test in request 4.
`test_back_to_back_is_fine` checks a function the application stopped calling, so
it passed throughout the outage it was supposed to catch.

## What P2 asked for, unprompted

Their answer to what would have helped is close to what sgt claims to do:

> Mostly one thing: ask history a question about a symbol, not a file. "What
> happened to `overlaps`, who calls it, and when did that change?" would have
> turned request 4 from a hunch into a lookup, and would have caught the
> false-green test immediately.

They also asked for a mark on commits that contain unrelated pieces of work, and
for some link between a commit and the tests covering the code it changed.

A participant in the baseline condition describing the treatment condition's
pitch is useful evidence, and it is worth collecting deliberately. Add a question
to the interview asking what they wished the history could answer, before they
have seen the other setup.

## Notes on method

Both pilots were agents. They find defects well, and they cannot tell us what we
most need from piloting, which is whether a person finishes in the time given and
where a person gives up. The time budgets in these two runs mean very little.

P2 recorded one moment worth keeping, because it is the failure the study is
about:

> I wasted a step in request 4 "verifying" the room audit against a store where
> `room add` had silently failed on a missing argument, so the audit had nothing
> to inspect and cheerfully printed "no room clashes". I caught it, but that is
> exactly how a fake green happens, and it's the same failure mode as the
> orphaned test I'd just found.
