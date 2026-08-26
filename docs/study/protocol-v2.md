# Study protocol, version 2: the staged comparison

Date: 2026-08-25
Status: Draft. Supersedes `docs/study/protocol.md` (the locate-and-reverse
pipeline of 2026-08-22) once the website implements it. Until then the old
document describes what is deployed.

Related documents:
- `docs/study/protocol.md`, the superseded protocol. Its testbed, its
  counterbalancing, and most of its recording machinery carry over unchanged.
- `docs/study/2026-08-23-harvested-testbed-result.md`, how the bikecount
  repository was built and how the removal target was selected.
- `docs/study/testbed-spec.md`, the isomorphism between the two projects.

Every question, scale, and derived number named here has a matching definition
in `web/src/study/` and is collected by the website. If a measure is not in
this document, the website does not collect it, and the paper cannot claim it.

---

## 1. What changed from version 1, and why

Version 1 gave the participant an unfamiliar codebase, a defect, and an open
task: find the work behind it and reverse it, using anything on the machine,
including a live AI assistant. Pilots showed what that buys and what it costs.
It buys realism. It costs control: participants spent their timed minutes
orienting in the codebase, deciding on a strategy, and negotiating with the
assistant, and all three of those costs landed on top of the thing we wanted to
measure, which is whether the representation of history helps at each step.

Version 2 trades that realism for control. The session is a fixed sequence of
four short stages. Each stage starts from a scripted repository state, tells
the participant exactly what has happened and what to do, and asks them to do
one thing through the tool. A short quiz and three rating items follow each
stage. Nothing in a stage depends on how the previous stage went, because a
script resets the state between stages. The live assistant is gone from the
task block; the agent's work is replayed from a recording instead, so every
participant meets byte-identical changes.

What the trade costs, stated here so the paper can say it. The study no longer
measures whether people choose a good strategy, whether they find the history
view at all, or how they direct a live agent. It measures whether the
representation supports each step of a workflow we prescribe. The old RQ4
(agent collaboration telemetry) is cut with the live assistant; what remains of
it is a set of interview questions. The open-ended realism the old design had
moves to the end of the session, where the participant walks through a semantic
view of a repository they own.

## 2. Research questions and claims

sgt records history at the level of intents rather than lines. The paper's
claim is not about capability. Both git and sgt can perform every action in
this study, and the paper says so in a parity table. The claim is that the
intent-level representation helps a person at three specific moments: when
they record work an agent did, when they look for the work behind a defect,
and when they operate on history at the level the task is stated at.

| RQ | Question | Stage | Instrument |
|---|---|---|---|
| RQ1: Reading a change at save time | When an AI agent has changed several files, does intent-aligned history change how accurately a developer can say what the change touched, and what they know about the record once it is made? | S1 | Post-stage checklist scored against a measured key, time to record, confidence |
| RQ2: Locating work | Given a described defect, does intent-aligned history change how accurately and how quickly a developer finds the piece of work that caused it? | S2 | Free-text identifier scored against an accepted-strings list, time, confidence |
| RQ3: Operating at the level of the task | When the task names a piece of work ("remove what your colleague did", "put it back"), can the developer carry out the operation at that level, with what success, what collateral damage, and what foresight about the operation's reach? | S2, S3, S4 | Reach prediction (S2) against outcome report (S3), behavioural check scripts, restore fidelity, mechanism self-report |

Each RQ carries a falsifiable claim:

| Claim | How it would be falsified |
|---|---|
| C1. Intent-aligned recording makes a multi-file agent change more legible at the moment it is saved. | S1 checklist accuracy is equal or worse under sgt, with no time advantage. |
| C2. Intent-aligned history makes it cheaper and more reliable to locate the work behind a described defect. | S2 locate accuracy is equal or worse under sgt, with no time advantage. |
| C3. Intent-aligned history lets people remove and restore a piece of work at the level the task names, with less damage. | S3 outcome or collateral damage is equal or worse under sgt, or S4 restore fidelity is equal or worse, or `gain` (S3 outcome report minus S2 reach prediction, both F1) is at or below zero. |

With 12 participants we can detect large effects and nothing else. The paper
says that in the limitations section rather than hiding it behind a p-value.

## 3. Study design

Within-subject. Every participant works in both conditions, on two isomorphic
projects (`bikecount` and `footfall`), with condition order and project
assignment fully counterbalanced. 12 participants in four groups of three,
assigned round-robin so any prefix of the cohort is balanced. The conditions
are named "Setup A" and "Setup B" to the participant, in order of use, and
authorship is never revealed. The group table, the round-robin rule, and the
reasoning are unchanged from version 1 (`web/src/study/flow.ts`).

### What each condition includes

| | git condition | sgt condition |
|---|---|---|
| Terminal | `git` commands | `sgt` commands, with `git` still available underneath |
| Editor | VS Code with Source Control, the Source Control Graph, Timeline, and blame in the gutter | VS Code with the semi-git extension (pinned version) |
| AI assistant | None during the task block | None during the task block |

The editor is the primary surface. Practice sheets are editor-first, and every
stage can be completed from the editor alone. The terminal stays available
because forbidding it would measure compliance rather than preference, and
which surface a participant reaches for is itself recorded.

The git condition's history view is Visual Studio Code's own, not GitLens. An
earlier draft pinned GitLens 19.0.1; it is out, for a reason that is a condition
problem rather than a preference. GitLens 19 opens on an account: its Launchpad
asks from the status bar for a GitHub connection, and `gitlens.ai.enabled` is on
by default, which places an AI panel -- explain changes, generate a commit
message, review changes -- inside a task block this protocol gives no assistant
in either arm. Time spent dismissing a sign-up would be charged to git, and an
assistant one arm has and the other does not is not a difference between two
ways of recording history.

The cost is real, and stating it runs against sgt's interest, so it is stated
here: GitLens searches history better than the Timeline does, and stage 2 is the
locate stage. The git condition is therefore weaker at locating than a git user
with their usual extensions would be, and any stage 2 result should be read with
that in mind. What it buys is a comparison against what every Visual Studio Code
has out of the box, with no account and no assistant in either arm. The setup
check confirms the editor is new enough to have the view (`editor_extension`),
because unlike an extension it cannot be installed if it turns out to be
missing.

There is no live assistant in the task block. In version 1 the assistant was
part of the condition. Here every stage must start from an identical state for
every participant, and a live agent cannot guarantee that. The change the
"agent" makes in stage 1 is a replayed recording of a real agent session, so
what the participant reads is genuine agent output, held constant. One model
still runs per session: sgt's labeller (pinned, checked with a real API call
before the session), because every feature name a participant reads in the sgt
condition came from it.

### Scripted states, and why stages are independent

Each workspace ships a `stage` script. `./stage 1` through `./stage 4` reset
the repository to a fixed state for that stage, no matter what the participant
did before. The first line of every stage card is the stage command. The two
operating stages also ship a check script (`./check 3`, `./check 4`) that
prints the same words in both arms.

Resetting between stages is what makes the design controlled. A participant
who fails stage 3 still starts stage 4 from the same state as everyone else,
so every stage's measure is a clean paired comparison, and one bad five
minutes cannot spoil the other three. The reset is announced on the card
("this puts the project exactly where this stage starts, and it undoes
anything left over from the last stage") so nobody wonders where their edits
went.

Stage 3 needs one extra piece of grounding. Its task is to remove the work
found in stage 2, but a participant who failed to find it must not fail stage
3 for that reason, because locating was already measured. So `./stage 3`
prints the identity of the target in that arm's own vocabulary (the commit
hash under git, the named piece of work under sgt). Both arms therefore start
stage 3 knowing exactly what to remove, and the stage measures only the
operation.

## 4. The four stages

Each stage has a 4-minute working cap with a visible countdown, followed by an
untimed quiz and three rating items (about one minute). The caps come to 16
minutes of timed work per half; with the quizzes, a task block is about 20
minutes. Wording is fixed in `web/src/study/tasks.ts`; the footfall wording is
the bikecount wording with the nouns swapped per the isomorphism map. Nothing
on a card names a git or sgt verb; the participant chooses the mechanism
inside the tool they were given.

The stages follow one workflow in order: record what an agent just did, find
the work behind a defect, take that work out, put it back. Codoban et al.'s
taxonomy covers the last three (rationale recovery and change impact); the
first is the moment their taxonomy predates, where the developer is the
reviewer of a machine's work rather than the author.

### Stage 1. Record what the agent did (RQ1, C1)

`./stage 1` pins the history to the point just before the last harvested
session landed and replays recorded agent work into the working tree,
uncommitted. The replayed state contains exactly two distinguishable jobs: the
change the story says was requested (the harvest's `rounding` session) and one
smaller unrequested piece of work replayed with it, so that "how many separate
jobs was this" has a right answer the tool did or did not surface. Both are
real agent output from the harvest that built the testbed; the pairing is
selected and verified by a build-time gate in `scripts/study/`, not by hand,
and if the gate picks a different pairing, the card wording in
`web/src/study/tasks.ts` is what changes.

The card: "You asked the coding assistant to make a change. It has finished,
and nothing is recorded in the history yet. Look at what it changed, then
record all of it the way this setup records work." The git arm stages and
commits (writing its own message); the sgt arm saves (sgt words the record).

The quiz, answered after recording:

- Which parts of the dashboard did the work change? Checkbox list of the ten
  page behaviours, scored as set F1 against a measured key.
- What else is in the same piece of work, if you can tell? Free text, never
  scored, kept for the interview.
- In one sentence, what does the history now say happened? Free text, same.

**What this stage does not ask, and why.** An earlier draft asked how many
separate jobs the change was, scored against a key of two. Running the stage in
both arms showed the question does not separate them, and the reason is a real
property of the tool rather than a fixable defect: sgt groups work into pieces
*once it is in the history*. An uncommitted change has not been grouped yet, so
sgt files it against whichever features the touched code already belongs to and
reports "1 edit in 1 feature" for an eleven-file change. Both arms therefore
answer a job-count question from the diff, which is the thing the study is
supposed to be comparing against.

What sgt does answer at this moment, and git has no equivalent for, is what the
change *joined*: the save names the piece of work the edits landed in, what
else is in it, and what removing it would now cost. That is what the free-text
question and the stage's rating items ask about, and it is the honest form of
C1. If S1 shows no difference, the paper reports that the representation did
not make an unrecorded change more legible in four minutes.

What this stage measures is the legibility of an unreviewed multi-file change
at the moment of recording. The git arm reads a diff; the sgt arm reads the
same diff plus sgt's decomposition of it. The commit message the git arm
writes and the record sgt produces are both kept and reported descriptively;
neither is graded, because version 1's pilots showed that grading prose under
time pressure measures the graders.

### Stage 2. Find the work behind the defect (RQ2, C2; reach prediction for C3)

`./stage 2` resets to the full committed history. The card tells the
participant what is wrong and why, in product terms: a colleague changed how
the dashboard's averages are computed, so that days on the project's
unusual-days list no longer count toward any average; the published report was
written when every day counted, so the dashboard and the report now disagree;
the committee wants every day counted again. The reset script prints the two
disagreeing numbers. The participant's job is only to find the work: "Find the
piece of work that changed the averages. Put its name in the box: a commit
hash, a named piece of work, an id, whatever your setup calls it." The target
is the testbed's `event-exclusion` work, the same target the v1 design used,
with its measured keys carried over.

Telling them what the change did is deliberate. Version 1 made them discover
the defect; the discovery cost the same minutes in both arms and was not the
claim. Here the detective work is removed and what remains is the thing RQ2
names, which is whether the representation makes the responsible work findable.

After the identifier box, the reach prediction: "Which parts of the dashboard
run through the code that work touches, the ones you would re-check if it were
taken out?" The same ten-behaviour checklist as stage 1, scored as set F1
against a key measured by `scripts/study/measure_reach_key.py`. Answered
before anything is operated on, so it reflects the representation alone. It is
untimed within the stage, unlike version 1's hard 60 seconds, because version
1 worried that extra time let people reason from general knowledge of
software; in this design the card has already told them what the change does,
so what the checklist can reveal is only what the representation shows about
where that change lives.

Scoring of the identifier is unchanged from version 1: free text, compared
after the session against an accepted-strings list per project (full sha,
7-character prefix, the function name, the commit message, the sgt feature
label and id), with the mechanical matching in
`web/src/analysis/pipeline.ts`.

### Stage 3. Take it out (RQ3, C3)

`./stage 3` resets to the same committed history and prints the target's
identity in the arm's own vocabulary, so both arms start knowing what to
remove (see section 3). The card: "The committee never approved the change.
Take that piece of work out. Everything else has to keep working. When you are
done, run `./check 3`."

**What the two arms actually cost, measured on the built testbed** (2026-08-25,
`bikecount`; the run is reproducible with `scripts/study/build_stages.sh` and
the task scripts):

| | git arm | sgt arm |
|---|---|---|
| Remove the work (S3) | three `git revert`s; the second conflicts in `bikecount/pages/monthly.py` and `bikecount/pages/overview.py`, and the third is blocked until they are resolved | one `sgt revert "Event-Day Handling"`, applied clean |
| The conflict itself | keep the later date-window wording, drop the `events` import: a judgement, not a mechanical choice. Taking either side wholesale leaves an app that does not start | none |
| Put it back (S4) | undo the three reverts | one `sgt restore "Event-Day Handling"`, tree byte-identical to the original |
| After either | every page renders; both arms' removed states render byte-identical pages, which the build gate checks and refuses to ship without | same |

The target was not chosen for that result. `select_target.py` measured every
landed session first, and the single-commit candidate the earlier draft used
(`event-exclusion`) reverts *cleanly* in git, so it was dropped for failing the
gate's own "differentiates" criterion. The work this stage removes is the
three-commit `Event-Day Handling` theme, which is also the target the version 1
answer key already accepted by all three of its commit shas.

The two arms then do the thing the study is about. Under sgt the removal is
one operation on the named work. Under git it is a revert of the commit that
holds it, plus whatever the state of the file demands: later sessions landed
in the same code, which is the normal case the tool exists for. Whatever
asymmetry exists between the arms on this target is a measured property, not
an assertion: the selection gate (`scripts/study/harvest/select_target.py`)
tried the candidate removals for real in both arms before the target was
chosen, the criteria are published with the result, and the parity table
shows both arms can complete the task.

The quiz, answered after `./check 3`:

- Which parts of the dashboard changed when you removed it? The same
  ten-behaviour checklist, scored F1. `gain` is this score minus the stage 2
  prediction score, per participant per condition.
- How did the removal go? One of: it applied cleanly; I resolved conflicts; I
  edited files by hand; I could not finish. Self-report of mechanism, reported
  descriptively next to the scored outcome.

Scored outcome, from the machine rather than the participant: the check script
and the facilitator's scorer compare the rendered pages against fixed
snapshots. Success means the headline number is back to its original value;
collateral damage counts pages that moved and tests that fail outside the
target. `snap.py` from the harvest tooling renders every page to diffable
text, so "which pages moved" is measured, not judged.

### Stage 4. Put it back (RQ3, C3)

`./stage 4` resets to the removed state, identical for everyone whether or not
their own stage 3 succeeded. The card: "The committee has changed its mind.
Put the work back exactly as it was, and check the dashboard matches what it
showed before the removal. Run `./check 4` when you are done."

Under git the natural route is reverting the revert; under sgt it is restore.
Fidelity is scored by the machine: the rendered pages must match the
pre-removal snapshots exactly. The quiz is one mechanism question (how did you
put it back) and one unscored sentence (how would you convince a colleague the
work is back).

## 5. The session, step by step

The website is the participant's interface for the whole flow. Steps and
estimates live in `web/src/study/flow.ts`; the table the participant reads is
computed from the same list the timers enforce.

| Step | What happens | Estimate |
|---|---|---|
| 1 | Welcome and code entry | -- |
| 2 | Consent | 2 |
| 3 | Background questionnaire | 2 |
| 4 | Setup for the first half: the bundle, the checks, and the start of the participant's own-repository build (section 7) | 6 |
| 5 | Practice with the first setup, on the study project itself | 5 |
| 6 | Stages 1 to 4, first half | 20 |
| 7 | After the half: UMUX-Lite and raw NASA-TLX | 3 |
| 8 | Setup for the second half, quick because the tooling is already there | 2 |
| 9 | Practice with the second setup | 4 |
| 10 | Stages 1 to 4, second half | 20 |
| 11 | After the half, again | 2 |
| 12 | Comparing the two setups | 3 |
| 13 | Your own repository, and the interview | 15 |
| 14 | Data handover and debrief | 2 |

That is 85 minutes of scheduled work; the page asks people to set aside an
hour and a half. `web/tests/schedule.test.ts` keeps the sum inside the
promise.

Setup is front-loaded: the shared tooling and the first bundle land in the
first setup step, so the second is only the second project's folder and its
checks. Practice splits five and four minutes across the halves rather than
ten up front, so the second tool is taught immediately before it is used.
Practice happens on the study project itself, at the state `./stage 0` puts it
in: the history one piece of work short of `study/full`, so the warm-up cannot
show the change stage 1 then presents as new. Version 1 practised on a
throwaway shopping-cart repository built by `scripts/make-practice-repo.sh`,
and that repository is gone. Two reasons. Its sheets quoted ids out of it
verbatim (`git show 44da4ad`, `sgt show "The Cart@2"`), and a participant who
typed one while standing in the study project -- which is where the session
shell opens -- got `unknown revision`. And the ten minutes bought no
familiarity with the codebase four timed stages are about. The sheets now also
carry a tour of that codebase, and quote no ids at all: every command either
needs none, or says to take one from what the previous command printed.

Each sheet teaches exactly the four actions the stages need: read a change in
the history view, record work, find work, remove and restore it.

There is no project brief step and no memorising. Each stage card carries the
two sentences of context it needs, which is the point of the design: the
participant is told what the program is, what just happened, and what to do,
one stage at a time.

## 6. Instruments

Wording is fixed in `web/src/study/instruments.ts` and versioned; the
per-stage items live with the stages in `web/src/study/tasks.ts`. Dropped from version 1: the 12-item HLAC battery, whose items are replaced by
the per-stage ratings below, asked in the minute after the experience they ask
about instead of ten minutes later. NASA-TLX was dropped in an earlier draft of
this version and is back (section 6.2): it is a published instrument with a
scale behind it, and the two study-written checks that replaced it were not.

### 6.1 Per-stage ratings

Statements on the 7-point agree/disagree scale, answered with the quiz,
immediately after the stage. Each stage's set is one comparison family.

The two reading stages ask two statements each, of the same shape both times:
did you understand the change, and did you understand what it reaches. The two
operating stages ask three, the last of them reverse-keyed as the guard
against straight-lining. An earlier draft of this version asked three
everywhere, and two of stage 2's asked, in different words, what the
confidence item directly above them already asked.

Stage 1 (serves C1):
- "I understand the changes the assistant made to this codebase."
- "I understand the downstream effects of those changes on this codebase."

Stage 2 (serves C2):
- "I understand why my colleague made this change."
- "I understand what else in the project this change affects."

Stage 3 (serves C3):
- "Before I ran it, I knew what the removal was going to change."
- "The result is what I intended."
- (reverse) "I was worried that I had broken something else."

Stage 4 (serves C3):
- "The project is back exactly as it was before the removal."
- "I could tell from the project's history that the work was back."
- (reverse) "I would want to re-check everything by hand before I trusted it."

The stage 4 reverse item is the honesty valve of this design. If sgt wins the
scored fidelity measure while losing this item, people got the right result
without trusting it, and that is a finding about the tool, not noise.

Quizzes with a right answer also carry a confidence rating on the same 7-point
scale, so calibration (confidence minus proportion correct) is computable per
stage. It was a 0 to 100 slider until this revision; stored answers carry
`confidenceScale` so the two are never averaged together.

### 6.2 Per-half battery

Two published instruments and nothing else, in one step:

- UMUX-Lite, unchanged from version 1 (`umux-lite-v1`), on its published seven
  points, reported raw on 0-100.
- Raw NASA-TLX, six unweighted subscales on the instrument's own 21-point
  scale, pointed at the four stages just finished.

The two study-written checks an earlier draft of this version carried -- task
realism on five points, and a five-option time-pressure select -- are gone.
Neither had a scale behind it or a corpus to compare a number against, and the
second asked, less precisely, what TLX's temporal-demand subscale asks. The
cost is that "the cap bound harder in one condition" is no longer asked
directly; temporal demand, which is asked per half on a published scale,
carries it instead.

### 6.3 Consent, background, preference

Consent gains one block for the own-repository walkthrough (section 7):
processing their repository on the study machine, the fact that the labelling
model receives code excerpts over the network, and the option to decline and
use a prepared public repository instead. Declining costs the participant
nothing and is recorded.

Background is unchanged. The closing preference block (`preference-v3`) asks,
for each of the four jobs the stages exercised (recording the assistant's
work, finding a piece of work, removing one, putting one back), "which setup
would you rather use for this", on the −2 to +2 scale with "no real
difference" as its own kept category, then the overall item, then "would you
put the second setup on a repository you own" (the discriminant item), then
one open box. Recoding to the sgt-positive direction and the midpoint rules
are unchanged from version 1.

### 6.4 The interview

Semi-structured, about ten minutes inside the 15-minute step, audio recorded,
run over the participant's own repository (section 7). The guide, with the
usual freedom to follow up:

1. Walk me through what you see. What are these groups? Do the names match
   what you would call the work?
2. Pick a piece of work you remember doing. Does what the view shows match
   what it actually was? What is missing or wrong?
3. In the session you removed a colleague's work and put it back. Where in
   this repository would you want that, and what would you be afraid of?
4. Today, when an assistant changes several files and you save it, what do
   you actually know about what you saved? What would you want to know?
5. Would you keep this view of your repository? What would it have to do
   before you trusted it?

Analysis of the recordings is reflexive thematic analysis, with the same
two-coder arrangement as version 1 (codebook agreed first, 25% double-coded,
disagreements resolved by discussion).

## 7. The participant's own repository

At screening, participants are asked to bring a Python repository they work
on, with at least 40 commits. Any size; private is fine. During the setup
step, with consent given, the facilitator starts `sgt init` and the history
backfill on it in the background. By the interview step the repository has a
semantic view, and the interview runs over it.

The point is the thing the controlled stages cannot give. The stages show
whether the representation helps on a codebase we built; the walkthrough shows
what the representation looks like on code the participant owns, where they
know the ground truth and we do not. Their corrections ("that is not one piece
of work", "that name is wrong", "where is the refactor") are the data.

Fallbacks, in order: if the participant declines the consent block, or brings
no repository, or the build fails or is not finished by step 12, the interview
runs over a prepared public repository instead, and the record says which
happened. Nothing quantitative is computed from anyone's own repository; the
walkthrough is interview material only, and no content from it leaves the
session except the participant's recorded words.

## 8. What the machine records

The recording machinery is version 1's with the assistant hooks removed:
PATH wrappers for `git`, `sgt`, `pytest`, `python`, the same wrappers reached
through the editor's `git.path` and `sgt.path` settings, a repository snapshot
at each stage boundary, and the heartbeat. Every command carries its surface
label (`terminal` or `editor`). The exclusion rules (background polling
flagged `auto`, nested commands not recorded, sync daemon not recorded) are
unchanged, as is the action taxonomy, minus the three assistant categories
(`prompt`, `agent_edit`, and assistant-attributed events), which cannot occur.

Derived measures, computed per stage and condition:

- Time to completion, against the cap, active time only; pauses are explicit
  and recorded with reasons.
- Surface mix: editor against terminal events.
- Time to first history operation.
- Wrong turns: a history operation followed within 120 seconds by a recover
  action.
- Quiz scores and `gain`, from `web/src/analysis/pipeline.ts`.
- Collateral damage and fidelity, from the check scripts and page snapshots.

## 9. Ground truth and keys

`docs/study/answer-key.json` carries, per project and versioned:

- Stage 1: the behaviour set the replayed change touches, measured by
  rendering every page before and after the replay and diffing
  (`snap.py`); and the job count, verified by the build gate against the
  recorded session's operation graph.
- Stage 2: the accepted-strings list for the identifier, and the reach set
  from `measure_reach_key.py`, which builds a call graph from each page
  handler and refuses to write a key the two projects disagree on.
- Stages 3 and 4: the page snapshots before removal, after a correct removal,
  and after a correct restore, which the scorer compares against.

Key versions are separate from question versions, for the same reason as
version 1: a key regenerated against a rebuilt testbed changes while the
questions stay the same, and sessions scored against different keys are not
comparable.

The upload refuses a key with no reach answer, a reach answer naming zero or
all ten behaviours, or a stage 1 behaviour set with the same degenerate
shapes (`web/src/study/answerKey.ts`).

## 10. Analysis plan

The unit of comparison is the within-participant difference (sgt minus git),
unchanged from version 1, with the same handling of incomplete participants
(a participant with one half contributes no difference and is dropped from
paired comparisons, not imputed).

### Tiers

| Tier | Measures |
|---|---|
| Primary | S1 quiz F1 and job-count accuracy; S2 locate accuracy (binary) and time; `gain`; S3 outcome (binary) and collateral damage; S4 fidelity (binary) |
| Secondary | The four per-stage rating triplets, UMUX-Lite, the preference block |
| Descriptive | Telemetry (surface mix, time to first operation, wrong turns), mechanism self-reports, calibration, the stage 1 records themselves (commit messages against sgt records), interview themes |

Every outcome is reported as a paired mean difference with a 95% bootstrap
confidence interval, 10,000 resamples over participants, fixed seed. Wilcoxon
signed-rank with matched-pairs rank-biserial effect sizes where a test is
informative, exact McNemar for the binary outcomes, always alongside the
intervals and never instead of them. Rating items are shown as full
distributions; composites may be treated parametrically, single ordinal items
are not.

Comparison families, named in advance: the four stage triplets and UMUX-Lite.
All five are reported whether or not they moved, with no multiple-comparison
correction, for version 1's reason: at this sample size the real risk is
selective reporting, and a correction answers the wrong problem.

### Pre-commitments

1. Stage 3 is where sgt should show its clearest advantage, because removing
   work that later work has landed on is the job the representation is for,
   and the selection gate measured that the git-arm revert conflicts on this
   target. A null result on stage 3 counts against C3, not as something to
   set aside. The selection criteria are published with the result.
2. Stage 1 is the novel measure and the least protected by precedent. If S1
   shows no difference, the paper reports that the decomposition did not make
   the change more legible in four minutes, and does not soften it.
3. Predicted dissociation: scored fidelity on stage 4 and the trust item (the
   stage 4 reverse-keyed statement) may point different ways. Either way it
   is reported.
4. The discriminant preference item ("on a repository you own") should
   produce a mixed result. Uniform agreement with the overall preference is
   evidence of demand characteristics and is reported as such.

This document is the pre-registration text for the redesigned study.
Research questions, wording, keys, exclusion rules, and planned comparisons
are fixed before the first participant of the new design.

## 11. Honest limits

- Twelve participants detect large effects only. Everything is reported as
  intervals.
- The stages are guided. The study says nothing about whether people find the
  right strategy unprompted, discover defects, or direct a live agent; it
  measures whether the representation supports four prescribed steps. That is
  the cost of the control, and the paper states it as the scope of the claims.
- Participants have years of git experience against ten minutes of sgt. The
  asymmetry cuts against sgt, so a positive result is conservative and a null
  result says nothing about sgt's ceiling.
- A novelty effect runs the other way, mitigated by neutral naming and never
  revealing authorship, and stated as a limitation.
- Letter and order are perfectly confounded within a participant, as in
  version 1: "Setup B" always means "the one I used second". Counterbalancing
  fixes this at the group level only.
- The removal target was selected by a gate whose criteria include the git
  revert conflicting. The paper publishes the criteria and the parity table
  rather than presenting the target as arbitrary.
- The behaviour checklists supply the answer space. Recognising which of ten
  listed behaviours a change touches is easier than asking, unprompted, what
  might break; `gain` is a difference within a supplied set.
- Four stages, one workflow, two small synthesized codebases, 4-minute caps.
  First-contact evidence only; the companion field deployment remains the
  design document's recommendation.
- The own-repository walkthrough is interview material, not a measure. It is
  subject to whatever repository people bring, and its fallback path (a
  prepared public repository) produces different material than the primary
  path. The record says which path each interview took.
