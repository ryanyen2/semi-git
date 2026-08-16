# Pilot 03: the first end-to-end rehearsal

Date: 2026-08-15. Two agents, one as the experimenter and one as an experienced
engineer, run through the whole study on local emulators: real bundles, real
terminal, real git and sgt, real questionnaires.

Earlier pilots exercised the *tasks*. This one exercised the **apparatus** —
the console, the bundle, the recording, and the handover between them. Almost
everything it found lives in that seam, which is exactly where nobody had
looked.

## What actually happened

The participant finished all four scored requests with **full marks** (R1 2/2,
R4 2/2, both naming the exact commits in the answer key), scored 3/5 on the
quiz, and covered 20 of 22 episodes in the summary. So the tasks work, the
answer key is right, and the testbed is sound.

Everything below is about the machinery around that.

---

## The one that would have invalidated the study

**325 events recorded on the participant's machine. 5 delivered. Heartbeat
green.**

The uploader only ever ran inside the interactive session shell. The
participant wasn't holding that shell open — and neither will a real one who
closes a terminal, opens a second tab, or reboots. Recording kept working;
delivery stopped; and the console could not tell *a quiet participant* from *a
dead uploader*, because the only number on screen was the one that had stopped
moving.

Two conclusions, pointing opposite ways, both true:

- **The local-first design was right.** Nothing was lost. All 325 events were
  recovered afterwards — including from a half whose `work/` directory had
  already been deleted by `study-cleanup`. "The log is the record, the upload is
  a copy" earned its keep.
- **Watching was useless anyway.** The facilitator's whole job during a 45-minute
  block is noticing trouble in time to help. That was silently impossible.

*Fixed.* Uploading now rides on the participant running commands rather than on
one shell surviving two hours. The console separates *alive* from *delivering*.

## The instrument was breaking the thing it measured

The recorder ran `git add -A --intent-to-add` on the participant's repository to
fingerprint it. That stages every untracked file — including sgt's own `.sgt/`
directory — as index entries pointing at git's empty blob. Confirmed live:
`conftest.py` staged as a phantom entry in the participant's work repo.

Where that blob is absent from the object store, every subsequent
`git write-tree` fails — and sgt calls `write-tree` whenever it snapshots. A
measurement tool was capable of disabling `sgt save` and `sgt revert` for the
rest of a session.

*Fixed*, with a regression test that asserts the tool under study can still
snapshot the repository after the recorder has run.

## A correct alarm, disbelieved

The console showed a red "nothing is arriving from their machine" warning. It
was **correct**. The facilitator investigated, saw events arriving with fresh
timestamps, and concluded it was a false alarm — because the console's activity
feed mixes two sources without distinguishing them. Thirteen of the twenty-two
events were markers written by the participant's **browser** when they opened
and closed requests; only nine came from their machine, and none were their
actual work.

They said they would have dismissed a true alarm.

A correct warning that another part of the same screen quietly contradicts is
worse than no warning: it teaches the operator to distrust the signal that
works.

*Fixed.* Every line in the feed is now tagged `machine` or `page`, page lines
are dimmed, and the header counts them separately.

## Nothing told the facilitator the participant was in trouble

The participant lost most of their practice window to a crash. In the
facilitator's own words:

> "Did anything tell me they were in trouble during the crash? No. Nothing. Flat
> no."

> "When would I have intervened, for real? Honestly — I don't think I would have,
> because nothing prompted it."

> "The real trigger that got me the real information was Jordan choosing to type
> it to me directly. That is not a console behavior, that's a compliant
> participant, and I don't think I can count on that in a real study."

No error, exception, or failed-command event reaches the console at all. The
feed answers "is something happening" and not "what is happening".

**Not yet fixed.** The recorder captures exit codes; the console does not
surface failures. Highest-value remaining work.

## Four of six requests could not be scored at all

R2, R3, R5 and R6 are judged by the state of the code. The scoring screen
offered an empty box asking for a script's output, and no way to obtain a copy
of the participant's repository. The facilitator found the script's name only by
reading source, then discovered it needs a repo the console cannot give it.

*Fixed.* The bundle now sends a summary of what changed against the
`study-start` tag on every sync, with the test-suite result on a final sync, and
the console shows it above the scoring cards.

## The rehearsal was not rehearsable

The bundle always talked to the real study, so a rehearsal participant following
the printed instructions got:

> That code does not match a participant. Check you copied the whole thing from
> your study page.

— sending them to check the one thing that was never wrong. The participant
diagnosed it themselves; a real one would have stopped.

Not a production bug. It broke *rehearsal*, the feature whose entire purpose is
de-risking the study. **The thing built to de-risk the study had never been run
end to end by anyone but its author**, who was the one person who knew the
undocumented environment variable.

*Fixed.* A bundle records its own target at build time.

## The setup checklist can be green while the sgt half is broken

The doctor checks ten things. The OpenAI key — which is what makes plain-English
selection work, the affordance that most distinguishes the sgt condition — is
not one of them. In this run the study-wide record read `openaiApiKey: ''` and
the participant was provisioned with it; the setup script printed one warning
line among fifteen and **exited successfully**.

Only because the Anthropic key was also fake did anything show red. Had just the
OpenAI key been missing, the page would have been entirely green while the sgt
condition quietly did less than it should — and the participant would have rated
it worse, for a reason nobody could reconstruct.

**Not yet fixed.** Add a key check to the doctor for the sgt condition.

## Editing a key after creating the cohort reaches nobody

`Create 12` copies the study-wide keys into each participant at creation time.
Later edits do not propagate; each row needs **Re-issue**, and nothing says so.
The realistic sequence is exactly what happened: create cohort, notice a key is
wrong, fix it, hand out links believing it fixed.

Worse, the only recovery offered — Revoke then Re-issue — **could not be tested
at all**, so it is unverified. An operator would be attempting an untested
recovery under time pressure.

---

## sgt defects, separated from the apparatus

**`sgt show` failed 6 of the 10 times it was used**, and it is the primary read
verb the practice sheet tells people to lean on. Rejected: save ids printed by
`sgt log` (`813375d7`), git commit ids (`079fa49`, and the full sha), and a
feature name typed as displayed (`Waitlist Promotion`, stored as
`Waitlist Promotion · notify.py`). The view's own docstring promises "what is
this thing? for any id sgt ever printed."

The participant went back to plain `git blame` and `git show --stat` and
answered the question that way — *in the sgt condition*. A tool that pushes
someone back to the baseline mid-task is not a measurement problem, it is the
measurement.

*Fixed.* The cause was narrower and worse than "ids are inconsistent": **the id
column of `sgt log` is the 7-character git commit sha**, so the tool was printing
a token its own primary read verb refused. `sgt show <that id>` now answers, as a
new selection kind — `save` — covering the ops that commit carried, read from the
same partition `sgt why <sha>` uses so the two can never disagree.

Feature labels also accept a unique prefix now, so a name typed as displayed
resolves. (`Waitlist Promotion` still does not, and correctly: two features share
that prefix — `· notify.py` and `· test_promotion.py` — and guessing between them
is exactly what an inspect verb must not do.)

One thing found while fixing it: the earlier partial fix suggested
`sgt feature why <sha>`, **which is not a command** — `why` was promoted to the
top level. The test that exists to forbid dead suggestions never used a
commit-shaped token, so that whole branch was unchecked. Both the command and the
gap in the test are fixed.

`sgt revert` still does not take a save id, so `show` deliberately withholds the
revert offer there and points at the checkpoints instead. Widening a destructive
verb's inputs is a decision for the owner, not a side effect of a display fix.

**Raw git plumbing reached a participant.** `GitError: git write-tree failed
(128) ... e69de29bb2d1...` — described by an engineer with eleven years of git as
"terrifying and completely opaque". *Fixed:* one retry, then a plain-language
message naming the file and stating that nothing was lost and it was not their
fault.

**Unreproduced.** The original practice-repo crash could not be reproduced on a
pristine bundle; a fresh practice repo saves cleanly. Recorded as observed with
an exact signature, cause not isolated. The participant's own diagnosis
(sparse checkout, hardcoded empty-blob SHA) was checked and is wrong on both
counts.

---

## A threat to validity nobody had noticed

**In the sgt condition, plain git is measurably worse than in the git
condition — and that biases the comparison toward sgt.**

Every sgt-authored commit in the sgt-condition repos carries its op ids as
commit-message trailers. Measured on `079fa49`, whose actual message is the
single line `add course search`:

```
174 Sgt-Op: trailer lines
```

So `git show`, `git log` and `git log -p` all bury the real content under a
screenful of hex. The baseline repos are stripped of these, by design, so a
participant in the **git** condition sees clean history while a participant in
the **sgt** condition sees this.

That is backwards from what the design intends. The study's own framing is that
git is a strengthened baseline — good commit messages, free choice of tooling,
the agent available in both arms — so that a win for sgt is conservative. Here
the sgt arm quietly degrades the very fallback a participant reaches for. The
pilot participant used `git blame` and `git show --stat` to answer request 1
**while in the sgt condition**, and hit this.

The direction of the bias is the problem: it makes git look worse exactly where
sgt is being measured against it, and it does so invisibly.

Options, none yet taken, because the testbed is frozen and sgt's own binding of
ops to commits may depend on these trailers:

1. Move the op ids to git notes (`refs/notes/sgt`), leaving messages clean.
2. Ship a repo-local git configuration in the sgt arm that renders history
   readably — but that changes the condition too, and has to be disclosed.
3. Change nothing and disclose it in the paper's threats section, with the
   number.

**Whatever is chosen, option 3 is mandatory.** This has to be checked before
participant one, and it must not be discovered by a reviewer.

## The two projects are recognisably the same puzzle

By design: the two testbeds are isomorphic, same episode script, nouns swapped,
so nobody sees the same project twice. The pilot participant's report is that
the resemblance is *strong*:

> "by half two I was pattern-matching against an already-solved puzzle rather
> than reading fresh"

The design anticipated this and counterbalances order, with order as a factor in
every model. This confirms that modelling order is **essential rather than a
formality**, and that the carry-over is likely large relative to the condition
effect on a twelve-person sample. Worth stating in the analysis plan as an
expectation rather than a caveat discovered afterwards.

---

## Two notes on method

**A confident agent report is evidence of an observation, not of its cause.**
Both agents produced detailed, plausible, wrong diagnoses. In both cases the
observation was real and valuable and the explanation did not survive ten
minutes of checking. The experimenter flagging their participant's hypothesis as
unverified is the only reason a false root cause did not enter the record.

**The best findings came from unscripted behaviour.** Spot-checking form state
before submitting; reading `install/setup.sh` instead of retrying it; running
`lsof` to see what was actually listening. None of that was asked for, and each
produced a finding the scripted path would have missed.

The most consequential finding of the whole rehearsal — the commit-trailer
asymmetry that biases the comparison toward sgt — came from neither a checklist
nor an investigation. In the participant's own words:

> "that trailer one really did just fall out of being annoyed at scrolling past
> it every time, not from looking for it"

A validity threat surfaced by irritation. No amount of test coverage finds that,
because nothing is failing: every command succeeds and the output is correct.
It is only *wrong* to a person who has to read it forty times in an hour. That
is an argument for putting real practitioners in front of the apparatus before
the study, not only in it.

**Both agents were wrong about causes, and both said so.** The experimenter
corrected their machine-alarm conclusion and then found the identical category
error a second time in their own questionnaire reasoning, unprompted. The
participant corrected their write-tree diagnosis in place rather than leaving it
standing. In a study whose evidence is partly what people report about their own
experience, that willingness to say "I concluded X and I was wrong" is worth
more than either of them being right first time.

## The experimenter's own verdict

Asked to rate "could I run twelve real sessions with this without a spreadsheet
on the side", a first-time user of the console said **6 out of 10** — and named
the reasons: keys not reaching participants already created, no way to know
something failed, and four of six requests unscoreable.

They also listed, unprompted, what should not be changed: one-click cohort
creation with the condition order spelled out per row, the answer-key upload,
the interview tab arriving pre-loaded with probes and sequencing guidance, the
questionnaire scoring, and the results pipeline correctly showing blanks rather
than nonsense statistics on a sample of one. They checked that the exported PNG
and CSV were real, well-formed files rather than buttons that merely look like
they work.

## Ranked, for whoever picks this up

1. **Decide what to do about the commit trailers**, and disclose it either way.
   This is the only item on the list that can affect what the paper is allowed
   to claim.
2. ~~Surface failures in the console~~ — done: failed commands now appear on the
   live card while they are still recent.
3. ~~Add the history-tool key to the doctor's checks~~ — done.
4. Warn on the roster when participants hold stale keys, and offer one re-issue.
5. ~~Resolve `sgt log` save ids in `sgt show`~~ — done: they are commit shas, and
   `show` now reads them as saves.
6. Verify the Revoke/Re-issue recovery path at least once, before a real session
   depends on it.
7. ~~The roster's email fields are indistinguishable from each other~~ — done:
   each is now labelled with its own participant. Worth recording *why* it still
   mattered: **no mail is ever sent from the console.** Links are handed out by
   hand, so the address is there for the record, not for delivery — and a
   misfiled one is therefore not a misdirected email but a wrong row in the
   record that consent, payment and the participant's own data are matched on
   afterwards. Quieter failure, longer-lived.
