# Study protocol

Date: 2026-08-22
Status: The operational protocol. This is what the study website implements.

Related documents:
- `docs/design/2026-08-21-controlled-study-redesign.md`, the design argument
- `docs/study/testbed-spec.md`, how the study repositories were built
- `docs/study/participant-materials.md`, facilitator script and answer keys

The design document argues for the study. This document pins down exactly what
is measured. Every question, every scale, and every derived number named here
has a matching definition in `web/src/study/` and is collected by the website.
If a measure is not in this document, the website does not collect it, and the
paper cannot claim it.

---

## 1. What we are testing

Version control records history as lines in files. Developers now write code by
describing what they want to an AI coding assistant. The unit of recording
(line-level diffs) and the unit of thinking (intent-level descriptions) have
come apart. sgt records history at the level of intents.

The paper's claim is not about capability. Both git and sgt can handle every
card in the session, and the paper says so in a side-by-side parity table. The
claim is about whether intent-aligned history helps people **locate and reverse
changes an agent made**, by decomposing those changes to a semantic level rather
than a line-level one. There are two sub-claims, each with its own evidence,
each designed to be falsifiable:

| Claim | How it would be falsified |
|---|---|
| C1. Intent-aligned history makes it cheaper and more reliable to locate the work behind an observed defect. | Locate accuracy on card 2 is equal or worse under sgt, with no time advantage. |
| C2. Intent-aligned history makes destructive edits safer. | Collateral damage on cards 3 and 4 is equal or worse under sgt, **or** `gain` on the reach prediction is at or below zero, meaning a participant who cannot predict what an operation reaches before running it is not being made safer by the representation. |

A third question is exploratory rather than a claim, because we have no prior
estimate to power it:

- Q4. Does the representation change how people direct and check the AI
  assistant?

We report C1 and C2 as estimates with confidence intervals, and Q4
descriptively. With only 12 participants we can detect large effects and nothing
else, and the paper will say that in the limitations section rather than hiding
it behind a p-value.

## 2. Research questions and where each is answered

| Research question (RQ) | What answers it | Instrument |
|---|---|---|
| RQ1: Locate | Whether the participant finds the responsible work, and how long it takes | Free-text identifier scored against an accepted-strings list per project, plus time |
| RQ1b: Foresight | Whether seeing the representation moves the expectation of an operation's reach toward the truth | One prediction trial on card 3, answered blind then after the reversal, scored as set F1 against a measured key |
| RQ2: Operation | Outcome and collateral damage on the reversal and the removal | Behavioral probe from `score_study_repo.py`, plus the removal rubric |
| RQ4: Agent collaboration | Telemetry on prompts, verification, and wrong turns | Claude Code hooks plus command-recording wrappers |

### The four cards

Each card is modeled on a real kind of task from the Codoban et al. taxonomy
of developer motivations for examining software history. Wording is fixed in
`web/src/study/tasks.ts` and is the participant's handout verbatim. Nothing in
it names a git or an sgt verb: the card states a goal in product terms and the
participant chooses the mechanism, because naming the verb would tell them which
tool we expect them to reach for.

| Card | Cap | What it tests | Codoban motivation | Which claims it carries |
|---|---|---|---|---|
| D1: Observe the defect | 3 min | Seeing the symptom of episode 17's regression (back-to-back rejection + green tests) | Defect understanding | Grounding for D2 and D3 |
| D2: Locate the work | 5 min | Finding which piece of work caused it | Rationale recovery | RQ1, C1 |
| D3: Reverse it | 6 min | Undoing episode 17, with a reach prediction before and after | Change impact | RQ1b, RQ2, C2 |
| W (4a/4b/4c): Remove a feature | 10 min | Removing the waitlist (episodes 11, 12, 14, 21), then restoring the drop command | Change impact | RQ2, C2 |

24 minutes of task work per half, computed from the per-card caps:
`BLOCK_CAP_MIN` in `web/src/study/tasks.ts` adds up the caps, so the number the
participant reads cannot drift from the number the timers enforce.

The four cards form a deliberate sequence. D1 prescribes the observation so that
whether someone thinks to run the program is not what the study measures. D2
opens the task: "find out which piece of work it was." D3 reverses it, with a
reach prediction wrapped around the reversal. W is a second, larger operation
on a different part of the history, staged as see-it (4a), remove it (4b),
correct the removal (4c). The stages share one clock, because 4c is a
correction to work 4b has just done and timing it separately would measure how
quickly the participant read the next card.

**Why the observe-locate-reverse pipeline.** The previous design asked three
closed questions about a tangled commit (episode 8) and measured comprehension.
That was the wrong question. The study is about whether someone can reverse what
an agent did, which requires locating the work first and then predicting what
undoing it will reach. Closed questions measure recognition. The new design
measures recall (card 2: "type the identifier") and action (card 3: do the
reversal and see what happened). Episode 17 replaced episode 8 as the target
because its defect is observable through the running program, while episode 8's
tangle is visible only in the code. The thesis, that tools should work at the
feature level, requires that tasks be observable at the feature level too.

**Why the tests are green over a broken program, and why that matters.** Episode
17 added `ranges_clash`, which uses `<` where the original `overlaps` uses `<=`,
and repointed the callers. `test_back_to_back_is_fine` still calls `overlaps`,
so the test suite passes while the running application rejects back-to-back
bookings. Card 1 runs the program, not `pytest`. A participant who sees green
tests and stops has learned the thing the block is about.

**What the cut costs, recorded here because the paper has to be able to say
it.** Until 2026-08-17 there were six requests over forty-five minutes a half.
Three were cut because pilots ran out of time on all three in both conditions.
On 2026-08-22 the remaining three-request design (R1 with closed questions, two
standalone prediction trials, R2/R3 removal) was replaced by the four-card
observe-locate-reverse pipeline. The closed questions are gone, the two
standalone prediction trials collapsed into a single trial on card 3, and
episode 17 moved from orphan to primary target. The history-surgery request was
the cleanest single demonstration of the representational difference, and an
earlier version of this document pre-committed to it as the place sgt should
look best. The study can no longer show it.

### The reach prediction on card 3

The reach prediction is the only measure in the study of whether the
representation moves an expectation **before** an operation. It costs nothing to
reset because nothing in its blind stage is executed or modified.

The trial names one piece of work in product terms, shows the same fixed list of
twelve things people do with the app, and asks which of those run through the
code that work touches, the set you would have to re-check if it were taken out.
The participant answers twice: once from the representation alone with a hard
60-second limit (announced before the clock starts), and once after doing the
reversal and running `./check.sh`. `blind`, `checked`, and
`gain = checked − blind` are all set F1 against a key that is measured rather
than written. `scripts/study/measure_reach_key.py` builds a call graph rooted at
each CLI command handler and cross-checks it against both test suites, and
refuses to write a key if the two projects disagree. The key for this trial is
four behaviours: cancel, promote, register, rooms.

F1 rather than agreement over the twelve boxes, because an empty answer agrees
with eleven of twelve on a key of size four and would score 0.67 for knowing
nothing; under F1 it scores zero.

The key carries its own version, `reachKeyVersion` in `docs/study/answer-key.json`,
currently `reach-key-v1`. It is versioned separately from the file because a key
regenerated against a rebuilt testbed can change while every question stays the
same, and two sessions scored against different keys are not comparable. The upload
refuses a key that has no reach answer for the trial, names a behaviour the trial
does not offer, or names all twelve, the three shapes that would otherwise score
every participant identically and look like a hard trial rather than a broken key
(`web/src/study/answerKey.ts`).

### Card 2's locate scoring

Card 2 asks the participant to type an identifier for the work that caused the
defect: a commit hash, a feature name, an id, or a description. The two
conditions name work differently (a sha under git, a feature label or id under
sgt), so a single correct string would mark one arm wrong for being right in its
own vocabulary. The answer key carries a list of accepted strings per project,
including the full sha, its 7-character prefix, the function name the regression
introduced (`ranges_clash`), the commit message, and the sgt episode label (E17).

Scoring is done after the session by comparing the typed answer against the
accepted list, not in the browser. A browser-side match would have to be lenient
enough to be worthless or strict enough to reject `f-8068d4e` for `8068d4e`. The
matching function (`locateMatches` in `web/src/analysis/pipeline.ts`) is
case-insensitive, strips punctuation, and accepts sha prefixes from 7 characters
onward. It extracts hex runs from the raw text so that `f-25e91a9` matches
`25e91a9a1d22...`.

### The behavioral probe on card 3

Tests alone cannot tell whether the participant fixed the defect. The test suite
is green before and after because `test_back_to_back_is_fine` calls a function
the application stopped using. The scoring script
(`scripts/score_study_repo.py`) now drives the CLI directly: it sets up a
scratch store with two back-to-back bookings, tries to enrol a student in both,
and checks whether both succeed. The flag is
`--expect-behaviour back-to-back-allowed`.

| | Test markers | Behavioral probe | Verdict |
|---|---|---|---|
| Unfixed | All green, zero damage | Second booking rejected | FAIL |
| Fixed | All green, zero damage | Both bookings accepted | PASS |

A study that scored only on test markers would mark both rows PASS.

## 3. Study design

This is a **within-subject** design: every participant works in both conditions,
on two isomorphic (structurally identical) projects, with condition order and
project assignment fully counterbalanced. There are 12 participants assigned to
four groups of three.

| Group | First half | Second half |
|---|---|---|
| 1 | git, coursecraft | sgt, confplan |
| 2 | sgt, coursecraft | git, confplan |
| 3 | git, confplan | sgt, coursecraft |
| 4 | sgt, confplan | git, coursecraft |

The website assigns participant IDs (P01-P12) round-robin across the four
groups, so any prefix of the cohort is still balanced. If the study stops at
eight participants, those eight still include two per group. This matters
because studies do stop early, and a cohort that is balanced only at n=12 is
unanalyzable at n=9.

The condition is never named to the participant. The website says "Setup A" and
"Setup B", and never reveals which one is ours.

### What each condition includes

A condition is a way of reading and changing history, not just a command-line
tool. Both halves offer the same three workspaces, and the participant chooses
freely among them:

| | git condition | sgt condition |
|---|---|---|
| Terminal | `git` commands | `sgt` commands, with `git` still available underneath |
| Editor | VS Code with built-in Source Control, Timeline, and GitLens 19.0.1 | VS Code with the semi-git extension |
| AI assistant | Claude Code, pinned to a specific model, identical in both halves | Same assistant, plus sgt's MCP tools and skills |

The assistant's capabilities are part of the condition, not background scenery.
Both halves tell the participant to use the assistant for anything, and both
practice sheets end by pointing out that it can plan before it acts, so most
work will involve the assistant.
In the git half the assistant already knows git well and needs nothing extra. In
the sgt half it gets the MCP server and three bundled skills (what `sgt init
--agent` installs for any user). Without these, the sgt condition would be
measured with the agent-facing half of the product turned off, and the comparison
would be "a tool the assistant knows" versus "a tool it must guess at" rather
than a comparison of two ways of recording history.

Two models are pinned per session, not one. The assistant runs on
`claude-sonnet-5`, and sgt's labeller and search run on `gpt-5.6-luna` (written
into the project's `.env` at setup time). The second matters as much as the
first: every feature name a participant reads in the sgt condition was written
by that model, so a session that silently fell back to a different model, or to
the deterministic offline names (which is what a dead API key produces), would
not be comparable with the others. Both are checked before the session starts by
making a real API call rather than just checking that a key exists.

GitLens is in the git condition deliberately. Comparing an editor extension
against a bare terminal would measure the presence of a graphical view rather
than the representation underneath it, and GitLens is how people actually read
git history in an editor. Both extensions are shipped inside the bundle at a
fixed version, so every participant runs the same software. The version is
recorded in `study.json`.

The editor runs from a dedicated profile inside the study folder, opened with
the `study-code` command. Its `git.path` and `sgt.path` settings point at the
same recording wrappers that the shell uses, so a click in a history view is
recorded exactly like a typed command and can be distinguished from one.

Everything else about the editor is held identical between conditions. Both
profiles get the same pinned Python tooling, the Python extension, Pylance,
debugpy, and python-envs, installed at setup rather than left for the editor
to offer on demand. In the first editor rehearsal the git arm ended up with 198
MB of Python tooling and the sgt arm with none, purely because one half happened
to lead the participant to open a `.py` file first. Having go-to-definition and
type inference in one arm but not the other is not a difference between two ways
of recording history. The setup check fails if anything is missing or if
anything extra was installed.

## 4. What the participant does, step by step

The study website is the participant's only interface for the study flow. Each
step writes to Firestore as values change, so closing a laptop loses nothing.

The step list is `STEPS` in `web/src/study/flow.ts`; the estimates below are its
`estimateMin` values.

| Step | What happens | Estimate | What is recorded |
|---|---|---|---|
| 1 | Welcome and code entry | -- | Claims the participant record |
| 2 | Consent | 2 min | `consent` (6 items, typed name, version) |
| 3 | Background questionnaire | 3 min | `background` |
| 4 | Setup for first half | 4 min | Live green checks from the machine's heartbeat |
| 5 | Practice for first half | 6 min | `tutorialCompletedAt` |
| 6 | The project, first half | 2 min | Nothing. No clock runs on this page |
| 7 | Task block 1 (four cards: D1, D2, D3, W) | 24 min | Per-card timings, locate answer, reach picks at both stages, notes, and confidence |
| 8 | Post-block 1: NASA-TLX, UMUX-Lite, HLAC | 5 min | Three response documents |
| 9 | Setup for second half | 3 min | Second heartbeat |
| 10 | Practice for second half | 5 min | -- |
| 11 | The project, second half | 2 min | -- |
| 12 | Task block 2 | 24 min | -- |
| 13 | Post-block 2 (same three questionnaires) | 5 min | -- |
| 14 | Comparing the two setups | 3 min | `preference` |
| 15 | Data handover and debrief | 2 min | Final sync confirmation |

Those estimates add up to 90 minutes, and the task-block rows are not typed
out: `estimateMin` for those two steps is `BLOCK_CAP_MIN`, so the schedule the
participant reads is the sum of the caps the timers enforce. The welcome page
shows the same table and the same total, and asks them to set aside an hour and
a half. `web/tests/schedule.test.ts` keeps the work inside that figure; if a
future card pushes past it, the sentence on the welcome page is what changes.

The usability and HLAC (History Legibility and Agent Collaboration) batteries
sit immediately after each half, which is where usability instruments belong:
asking someone to rate "Setup A" after they have spent the second half in
"Setup B" measures memory, not experience. The 10-item SUS (System Usability
Scale) stays replaced by the 2-item UMUX-Lite (Usability Metric for User
Experience, Lite version).

The session has been shortened three times. Requests went from 45 minutes a half
to 20 (2026-08-17), the per-half questionnaire block from 12 minutes to 6
(§5.6), and two pages with no clock on them were added, which took the session
from about 125 minutes to 113. Prediction trials then put it at 129. On
2026-08-22 the task block dropped from 28 to 24 minutes (the closed questions
and standalone prediction trials were replaced by the observe-locate-reverse
pipeline, §2), the questionnaire block dropped from 6 to 5, and step estimates
were tightened across the board, bringing the session to 90 minutes.

### The project, before any clock exists

Step 6 and step 11 are a plain-language description of what the program is for,
who uses it, and what it refuses to do. Product terms only: no file names, no
function names, no module layout, and nothing about how any of it was built.
All of that is what the cards are about, and handing it over here would give
away the answer to card 2 on the way past. Nothing on the page starts, opens,
or patches a card.

It exists because pilots met the codebase for the first time with a countdown
already running, and spent the first third of a request working out what the
program was for. That is not what the study measures. It is also not evenly
distributed: whichever project a participant sees second is cheaper to orient
in, because the two are the same shape under different nouns, so leaving
orientation inside the timed block puts an order effect straight into the
primary measure.

### The warm-up project

The practice steps run against a throwaway repository that
`scripts/make-practice-repo.sh` builds, deliberately not one of the two study
projects, because practice on a study project would teach part of the answer to
card 2 and we would never know how much.

It is sixteen commits over `cart.py`, `discount.py`, `receipt.py`,
`shipping.py`, and four test files, and it clusters into four features: The
Cart, Discounts, Receipts, and Shipping. It used to be a single `cart.py`, which
clustered into exactly one feature, so a practice sheet that says "take one
feature out without disturbing the others" ran against a repository that had no
others, and the map the participant met had one row in it. The history now
contains everything the sheets claim: a commit that quietly does two things, a
regression with its later fix, a feature added and then dropped, and a commit
that touches no code at all.

The four feature names are pinned by the build script (`sgt feature rename`,
durable in `.sgt/pins/pins.json`) so the sgt practice sheet can quote them
literally. Without pinning, the names come from a model call, which makes them
neither stable between builds nor present at all when the key is missing, and a
missing key is what shipped once, giving the practice repository a feature
called `add_item apply_discount…`. The script re-checks every handle the sheet
quotes and warns on stderr if one stops resolving.

Both practice sheets are editor-first: they open with `study-code` and the
graphical history view (GitLens in the git half, the semi-git sidebar and
workbench in the sgt half) before any terminal command, and both end by
pointing out that the assistant can plan before it acts. Pilots read a sheet
made entirely of terminal commands, then met the requests inside an editor they
had been given but never shown, and several never opened the history view at
all, which turns "does this representation help" into "did you find the panel".

### The prescribed scripts

Three shell scripts ship with each study workspace and are run at prescribed
moments during the task block. They exist so that whether someone thinks to run
the program or check their work is not part of what the study measures. Both
conditions run the same scripts.

`show-the-problem.sh` (card 1) sets up a scratch store, creates two
back-to-back bookings, tries to enrol a person in both, runs the room audit,
then runs the test suite for the conflicts and rooms markers. It works on a
scratch copy of the data, so a participant can run it as many times as they
like. The suite passes. That is not a mistake in the script.

`check.sh` (card 3, card 4c) repeats the two back-to-back cases from card 1,
so the participant sees the defect gone (or still there) in the same words they
first saw it. It then runs the whole suite by feature area and prints each
area's result in colour: green for pass, red for fail, dim for "nothing left
under this name." It starts the program last, because a pilot finished with 29
passing tests and an application that would not start. It reports but does not
score, and says so, because a participant reading a green line as "correct"
would stop looking.

`show-the-waitlist.sh` (card 4a) walks through the waitlist: fills a seat,
queues two people, frees the seat, shows the auto-fill, shows the notice.

### Timers

Each card has its own time cap and its own clock. D1 is capped at 3 minutes,
D2 at 5, D3 at 6, and W at 10. That is 24 minutes a half (`BLOCK_CAP_MIN` in
`tasks.ts`), and the participant also sees elapsed time against that figure for
the half as a whole.

Card 3 (D3) runs a special sequence for the reach prediction. Before the
participant changes anything, a 60-second blind stage appears: tick which of the
twelve behaviours you think the reversal will affect. This is the only hard cap
in the study that submits on expiry rather than merely closing: it is what makes
`blind` mean "from the representation alone", so the card offers no pause and
the participant is told before the clock starts that whatever is ticked when it
ends is their answer. After the blind stage, the card returns to open work: do
the reversal, run `./check.sh`. The checked stage then reopens the same twelve
behaviours with the blind picks already ticked, so it is a revision rather than
a fresh answer. That anchoring makes `gain` harder to earn, which is the
direction to be wrong in.

The clock starts when the participant opens the card and stops when they mark it
done or the time cap expires. The timer is visible to them. A visible countdown
is part of the task design: the time caps come from Ko et al., and a hidden cap
would turn "ran out of time" into "gave up", which are different kinds of data.

Pausing is explicit and recorded. Facilitator interruptions, tool breakage, and
breaks all produce a paused interval with a recorded reason, and the analysis
uses active time only. Pilot 1 lost a request to a tool failure with no record
of how long the recovery took.

## 5. Instruments

Item wording is fixed in this document because the paper must report it exactly
and because a questionnaire edited between participants is a questionnaire with
no consistent scale. Every battery carries a version string, stored with each
response.

### 5.1 Consent (`consent-v1`)

Six checkboxes (all but the last are required), plus a typed name and date.

1. I have read the information sheet and had my questions answered.
2. I agree to my screen and voice being recorded for this session.
3. I agree that the commands I run and the messages I send to the AI assistant are recorded.
4. I understand my data will be de-identified and reported in aggregate.
5. I understand I can stop at any time, without a reason, and still be paid.
6. *(optional)* I agree to short anonymized quotes from my session appearing in a publication.

### 5.2 Background (`background-v1`)

These are covariates (factors to control for), not screening criteria. Screening
happened at recruitment.

| Item | Format |
|---|---|
| Years writing code seriously | Integer |
| Years using git | Integer |
| How often you use each command: `log`, `blame`, `bisect`, `revert`, `reset`, `rebase -i`, `reflog`, `cherry-pick` | Never / Rarely / Sometimes / Often |
| AI coding tools you have used in agent mode | Multi-select |
| How often you work with an AI coding assistant | Daily … Never |
| Share of code you shipped last month that an assistant wrote | 0-100 slider |
| Primary programming languages | Free text |
| Have you used sgt or semi-git before? | Yes / No |

The eight-command frequency grid produces a **git expertise composite score**
(0-24, where never=0 and often=3) that goes into the statistical models as a
covariate. We do not ask for self-rated expertise on a single 1-7 scale, because
that correlates with confidence rather than actual skill. The last item is an
exclusion check, not a covariate.

### 5.3 NASA-TLX (Task Load Index), raw (`tlx-v3`), after each half

Six subscales, unweighted (this is "Raw TLX", following Hart's 2006
retrospective), using the instrument's original 21-point scale from 0 to 100
rather than the seven-point scale used elsewhere in this study.

The administration follows Lee et al., "NASA-Task Load Index in CHI: A
Comprehensive Review and Subscale Meta-Analysis with Implementation Guidelines"
(ACM Transactions on Computer-Human Interaction, 2026, DOI
[10.1145/3837858](https://doi.org/10.1145/3837858)), which reviews how the
instrument is actually administered in this venue and turns the recurring
mistakes into named guidelines. Each choice below is stated here rather than
left in the code, because the absence of exactly these statements is what makes
two papers that both report "we used TLX" incomparable.

The six subscales, with the question each is asked as and the words at the two
ends of its line:

| Subscale | Question | Anchors |
|---|---|---|
| Mental demand | How mentally demanding was the task? | Very low … Very high |
| Physical demand | How physically demanding was the task? | Very low … Very high |
| Temporal demand | How hurried or rushed was the pace of the task? | Very low … Very high |
| Performance | How successful were you in accomplishing what you were asked to do? | Failure … Perfect |
| Effort | How hard did you have to work to accomplish your level of performance? | Very low … Very high |
| Frustration | How insecure, discouraged, irritated, stressed and annoyed were you? | Very low … Very high |

Each also carries its published definition underneath, which is a guideline in
its own right and is covered below.

**Weighting: none.** Raw TLX, unweighted, is what is reported. Stating which of
the two procedures was used is itself a guideline, because a paper that reports
"TLX" without saying cannot be compared with either kind, and that is why this
paragraph exists rather than being left implicit. The pairwise-weighting
procedure is scoped to single-task studies, where the weights describe what made
that one task heavy. This study compares two conditions inside one person, so
weights would be collected twice and would themselves differ between the two
administrations, turning a difference in workload into a difference in workload
plus a difference in what the participant thought mattered.

**Scale: 21 tick marks, 20 intervals, no number.** Each subscale is drawn as the
instrument's own line: twenty-one tick marks with twenty intervals between them,
bipolar text anchors at the two ends, and no number shown to the participant
anywhere. The ticks at 0, 50 and 100 are drawn as landmarks and left unlabelled,
so the midpoint is findable without becoming a neutral option. Answering is a
discrete click on a tick, not a drag: the guidelines are explicit that dragging
must not be the primary input, because a drag is a magnitude judgement where TLX
asks for a mark in an interval. It was a range slider with a numeric readout
beside it until 2026-08-17. A visible readout also turns the answer into a
number the participant then reasons about ("I said 60 last time"), and TLX is
meant to be answered on first instinct; and a slider thumb sits somewhere from
the moment it renders, so an unanswered scale looked answered. Twenty-one
discrete targets have no default position, so an unanswered scale is simply
empty. The recorded value is unchanged, 0 to 100 in steps of five, which is
those twenty-one tick marks numbered, and `tlxScore` is unchanged with it, so
responses collected before and after the change are on the same scale.

**Not harmonized to seven points.** The rest of this study's self-report runs on
seven points, and TLX deliberately does not. A coarser scale does not merely
blur TLX, it changes its shape: five- and seven-point administrations distort
the factor structure, with frustration migrating onto the physical subscale and
effort splitting across two. The guidelines anticipate the consistency argument
("make it match the rest of the battery") and reject it.

**Name, question, and definition, all three.** Each subscale is shown with its
published name, its question, and its full published definition. Displaying both
the title and the complete description is a named guideline, and omitting the
description is one of the failures the review found most often. The name alone
is not enough here in particular: the six correlate strongly in interactive
work, and a participant reading only "Mental demand" answers several of them
alike.

**Performance direction, and where it is reversed.** Performance is presented
**failure-to-perfect** (failure at the low end, perfect at the high end), which
is the direction its anchors are read in, and it is reversed exactly once,
before analysis, so that a higher number consistently means higher workload on
all six subscales. Collecting it reversed is permitted on the condition that the
transformation happens before analysis *or reporting* and is documented; this
paragraph is that documentation. Its two anchors are styled differently from the
other five subscales' on the page, which is what the guidelines' reference
implementation does, because marking this scale in the wrong direction is the
instrument's best-documented failure.

Until 2026-08-17 it was presented perfect-to-failure *and* reversed in scoring,
which meant a participant who felt they had performed perfectly was contributing
the maximum possible workload score. A second, quieter version of the same
defect survived that fix: only the aggregate was reversed, inside `tlxScore`, so
the stored per-subscale value for Performance still ran the opposite way from
the other five. Nothing had drawn a per-subscale figure yet. The first one drawn
would have shown the condition people performed best in carrying the highest
performance workload, and it would have looked plausible. The reversal now lives
in one function, `tlxSubscales` in `web/src/lib/stats.ts`; `tlxScore` is defined
in terms of it, the analysis pipeline carries the six subscales beside the
aggregate on every half, and anything that reports a subscale must come through
that function rather than reading the stored response. The item anchors are
asserted in `web/tests/analysis.test.ts` alongside the arithmetic, because
either half of the convention alone is only half correct.

**Physical demand** is retained even though it provides little information for
desk work. Dropping a subscale would make the total incomparable with every
other paper that reports Raw TLX. Its help text used to end "For desk work this
is usually low, and that is a normal answer." That clause is gone: it was meant
kindly, and it told the participant what to answer before they answered, on the
one subscale where a floor is the expected result, so it manufactured the very
reading it was reassuring them about.

**Reporting.** Scores are reported on 0-100, per subscale and as the raw average
of the six, and are called **workload** rather than "cognitive load". TLX spans
physical and temporal demand as well as mental effort, and the narrower term
claims something the instrument does not measure.

The questionnaire names the specific cards, not the session as a whole.
NASA-TLX measures the workload of a bounded task and produces something
different when pointed at an hour of mixed activity.

### 5.4 UMUX-Lite (Usability Metric for User Experience, Lite) (`umux-lite-v1`), after each half

Two items on their published seven-point scale, replacing the ten-item SUS
(System Usability Scale) that this study used until 2026-08-17.

| # | Statement |
|---|---|
| U1 | This setup's capabilities meet my requirements. |
| U2 | This setup is easy to use. |

The referent is filled in as "this setup" rather than the generic "this system."
Both halves run the same assistant in the same editor on the same kind of
project. An unqualified "system" would be interpreted differently by different
participants, and the difference between the halves is the entire measurement.

Scores are reported raw on a 0-100 scale using the published formula. They are
**not** converted to a SUS-equivalent score, because that regression was fitted
to particular data sets, and a within-participant difference gains nothing from
the transformation while inheriting its error. No claim is made that any
absolute score is high or low, these instruments carry no norms for this kind
of work and are not ratio measures, so only the difference between conditions is
interpreted.

The swap from SUS costs nothing this study was using. SUS is designed for
benchmarking against its average of 68, and this study never benchmarks, it
reports a paired difference. Ten items administered twice per session bought one
number that two items buy equally well, and the eight extra rows were the ones
most likely to be answered carelessly (straight-lined).

### 5.5 HLAC: History Legibility and Agent Collaboration (`hlac-v4`), after each half

Twelve items on a 7-point Likert-type scale from strongly disagree (1) to
strongly agree (7), organized in three labeled blocks in a fixed order, followed
by two manipulation checks on their own five-point scales. The twelve appear
as Figure 1 in the paper; the two checks are reported separately and are not
outcomes. In the exported data they are the `check_` columns, kept apart from
`hlac_` for the same reason they are kept out of the figure, and because
averaging a five-point item into a block of seven-point ones, or plotting it on
that block's axis, makes a check look like a finding and a neutral answer look
like a negative one.

These are **Likert-type items grouped into ad-hoc composites, not a validated
psychometric scale**, and they are reported as such: item by item, with the
block mean given as a summary rather than as a construct score. No
internal-consistency coefficient (like Cronbach's alpha) is reported for a
three-to-four-item block at this sample size, because such a coefficient would
make an ad-hoc block look like a validated instrument.

Seven points were chosen because reliability and validity fall off below five
points and gain little above seven, and because it matches the published
instrument beside it (UMUX-Lite).

**Block 1: Finding your way around**

| # | Short label (for figures) | Statement | Serves |
|---|---|---|---|
| Q1 | Found when it changed | I could find when a behavior changed. | C1 |
| Q2 | Found why it changed | I could find out why a change was made. | C1 |
| Q3 | Saw the whole piece of work | When I found a change, I could see what larger piece of work it belonged to. | C1 |
| Q11 | *(reverse-keyed)* Guessed at names | I had to guess at names or IDs to find what I was looking for. | C1 |

**Block 2: Changing things**

| # | Short label (for figures) | Statement | Serves |
|---|---|---|---|
| Q4 | Knew what else it would touch | Before I changed anything, I could tell what else would be affected. | C2 |
| Q5 | Removed a feature safely | I could take a feature out without worrying about breaking what came after it. | C2 |
| Q6 | Recovered from mistakes | When something went wrong, I could get back to a good state. | C2 |
| Q12 | *(reverse-keyed)* Surprised by the result | A change did something I had not expected. | C2 |

**Block 3: Working with the assistant**

| # | Short label (for figures) | Statement | Serves |
|---|---|---|---|
| Q8 | Directed the assistant precisely | I could point the assistant at exactly the part of the history I meant. | Q4 |
| Q9 | Checked the assistant's work | I could check what the assistant did against what I asked for. | Q4 |
| Q14 | *(reverse-keyed)* Accepted unreviewed work | I accepted changes from the assistant that I had not really reviewed. | Q4 |
| Q10 | *(reverse-keyed)* Fought the tool | I spent effort fighting the tool rather than doing the task. | Guard item |

Block 3 used to be block 4, with a "What you came away with" block (Q7, Q13)
between it and "Changing things." Those two items measured C3, which the study no
longer carries. They were removed in hlac-v4 on 2026-08-22 because the claim
they served was dropped: the study is about locating and reversing work, not
about what theory of the project a participant walks away with.

Items are grouped under headings in a fixed order rather than randomized. An
undifferentiated column of nearly identical rows gets answered by pattern; the
headings are the cheapest guard against that, and they only work when the items
that belong together sit together. The guard against straight-lining (answering
every item the same) is the four reverse-keyed items instead.

**Q12 and Q14 are honesty valves.** If one condition wins on everything
*including* "nothing surprised me" and "I reviewed everything," suspect
acquiescence bias. If a condition wins on finding and changing while *losing*
Q14, people accepted more unreviewed work because the tool made accepting
easy, the data reads as credible, and the story is a cost paid knowingly.
Either pattern is reported.

**Two manipulation checks: about the cards themselves**

These are not opinions about the setup, and they are the last thing in the
block.

| # | Statement or question | Scale | Checks |
|---|---|---|---|
| M1 | These requests were realistic. I can see this situation happening in real development. | 5-point agreement | Task realism |
| M2 | How much time pressure did you feel? *(about the clock specifically, not about how hard the work was)* | 5 fully labeled options, "Too much. I could not cope, regardless of difficulty" … "None at all" | Whether the time cap bound |

The paper claims that the two projects are isomorphic and that the cards are
the kind of thing that happens in real work. Both claims are made by
construction and, until now, defended by argument alone, which is the first
thing a reviewer pushes on. M1 turns the second one into something measured,
per half, for the cost of one row.

M2 matters more than it looks. Every card in this study is capped (§4,
Timers), so "the cap bound harder in one condition" is a live alternative
explanation for any difference in what people got done. Asked directly, it
becomes a number that can be checked rather than a threat to be argued away in
the discussion. It is deliberately fully labeled rather than anchored at the
ends only, because the middle of a time-pressure scale is where the interesting
answer sits and "3 out of 5" does not say what the participant meant by it.

Both have precedent in the closest published work on history tools: the Azurite
evaluation asked participants whether its tasks were plausible, and the Replay
evaluation asked both a plausibility question and a time-pressure question.

Neither check is one of the five comparison families in §7, and neither is on the
seven-point scale, so neither belongs in Figure 1's diverging bars or in any
block mean.

### 5.6 The recall quiz and the summary task, both removed

A five-item recall quiz (`quiz-v1`, two-minute cap) and a three-minute "tell the
story of this project" summary (`summary-v1`, scored against the 22-episode
ground truth for coverage, causal links, and misconceptions) used to run after
each half with the project closed. Both were removed on 2026-08-17. They are
recorded here rather than deleted, because the paper has to be able to say why
they were tried and abandoned.

They cost twelve minutes a session and asked the participant to write, from
memory, with the project closed, immediately after a block they had usually just
run out of time on. What came back was short, hedged, and graded by hand against
a rubric, and the two conditions differed less on it than the graders differed
from each other. A measure whose between-coder variance exceeds its
between-condition variance is not measuring the condition.

Removing them also removed the only reason the answer key had to ship a
`quizAnswers` block, and the only step in the flow that had to be locked against
editing after submission.

### 5.7 Preference and closing (`preference-v2`), end of session

A reminder of which letter was which, seven comparative judgements over jobs the
participant actually did, one multi-select of reasons, two discriminant
scenarios, an overall comparison, two adoption items, one multi-select on cost,
and one optional free-text box. The two setups are labeled A and B throughout.

**The scale: five points, not three.** Every comparative item in this block
offers the same five options, in this order:

> A, clearly · A, slightly · No real difference · B, slightly · B, clearly

The midpoint stays. The paper's argument is about a tradeoff, and a block that
cannot record "these were the same here" cannot describe one. What changed is
either side of it: with twelve participants, the distance between "leaned that
way" and "chose that one" is most of the result, and a three-option select threw
it away. The options are symmetric and neither setup is named first. The
comparable published instruments put the new tool in the stem ("I found Gitless
to be easier to use than Git"), which anchors on it and leaves disagreement
ambiguous between "the other one won" and "no difference".

Responses are recoded to −2 … +2 in the **sgt-positive** direction, using
whichever letter sgt was for that participant, and reported item by item with
its own n. **The midpoint is a substantive category, reported as itself and
never dropped as missing.** That is pre-committed here rather than decided
later: dropping "no real difference" after seeing how many people chose it is a
forking path, and at n=12 it is a path that could produce a majority out of four
people.

**Which letter was which.** The block opens with a line, not a question: "Setup
A was the one you used first. Setup B was the one you used second." Every
comparable published study named its two tools outright. This one cannot, because
naming them tells the participant which one is ours, and the participant is being
asked to compare two labels they last saw an hour ago. The price of that choice
is recorded in §9: letter and order are perfectly confounded within a
participant, recency favors B, and counterbalancing fixes that at the group level
only.

**The jobs you just did.** The five-point comparison for each of:

- Finding which piece of work caused an observed problem *(C1)*
- Seeing what else came along with a change *(C1)*
- Taking one piece of work out without breaking the rest *(C2)*
- Putting back part of what you took out, after the fact *(C2)*
- Being confident the result was what you intended *(C2)*
- Getting back to a good state when something went wrong *(C2)*
- Checking what the AI assistant had actually done *(Q4)*

Each names a job in outcome terms and never in tool terms, "taking one piece of
work out without breaking the rest", not "reverting a feature". A question
phrased as a mechanism that only one setup has is not a comparison, it is a
leading question with a forced answer. The job-level comparison is the most
defensible preference measure available, because a single overall preference at
n=12 is one bit times twelve.

The last of the seven asks about checking the assistant's work only. It used to
ask about "directing the AI assistant, and checking what it did", and directing
is near-identical across the two arms (same assistant, same prompts), so half
of the wording was noise. The two halves are also known to come apart:
Vaithilingam, Zhang and Glassman (CHI 2022 Extended Abstracts,
[10.1145/3491101.3519665](https://dl.acm.org/doi/10.1145/3491101.3519665)) found
23 of 24 participants calling Copilot more helpful while only 10 of 24 felt more
confident in its output. Our own HLAC block already splits directing (Q8) from
checking (Q9), so collapsing them here disagreed with our own battery.

**Reasons.** One multi-select, "What made the difference, wherever you felt
one?", replacing five required free-text "Why?" boxes. The v1 block ran eighteen
items and sat at the end of a two-hour session; by the third box the answers
were "same reason as above". The options are the reasons pilots gave, in their
words, and they include the two that would be evidence against us (already
knowing the commands, and being able to predict exactly what the tool would do),
plus "nothing much, they felt about the same". An option list with no losing
options is a leading question wearing a checkbox. "Undoing was easy" and "I
trusted what the undo had done" are two separate options, for the same reason
the item above asks only about checking: an undo can be easy to perform and hard
to believe, and one option covering both would record the two as one.

**Where each one would earn its keep.** Two discriminant scenarios, on the same
five-point scale:

| Scenario | What we expect |
|---|---|
| A repository you have never seen and will not see again | Plain git |
| A codebase you will own for the next year | sgt |

The first is a job that does not reward reading history carefully, and a
participant who picks the same setup for it as for the second is evidence of
demand characteristics rather than of preference. That reading is reported
either way, and it only works if at least one scenario is one we expect to lose.

A third scenario, "a production hotfix under time pressure", was cut on
2026-08-17. This document had carried it with its expected result listed as
"open", which is the problem rather than a caveat: an item nobody can be wrong
about cannot discriminate, and it was spending a question to collect a shrug.
These two are also the weakest items in the block and are kept deliberately few:
each asks a person to forecast from twenty-four minutes of use, which the seven
job items do not.

**Overall.** Which setup would you rather work in, on the same five points, then
"I would want Setup A on my own projects" and the same for Setup B, each on the
7-point agreement scale. Asking about both separately rather than as one choice
lets a participant want both, or neither, which a comparison cannot record.

**Cost.** A multi-select: "What would put you off using the one you preferred?"
Every tool study collects reasons to adopt. This one collects the price, because
the finding is a tradeoff and a tradeoff with no cost recorded reads as
advocacy. The options include "having to piece together what happened from the
messages", which is a cost of plain git and not of sgt: a cost list that only
lists sgt's costs cannot be answered honestly by a participant who preferred
plain git, and their answer is the one this item most needs.

**One open box, optional.** "Anything you wanted to ask the project history and
could not?", answered from what they remember wanting, not from what either
setup offered.

### 5.8 Interview

Conducted by the facilitator using the dashboard, with timestamped notes
organized around fixed probes:

- What did you trust, and what did you check?
- Where were you lost?
- What did the history hide, and what did it show?
- What did you wish you could ask the history?
- How did you decide what to hand to the assistant?

The fourth probe is asked **before** the participant is invited to compare the
two setups. Both pilots answered it with something close to what sgt does, one
of them while in the git half, and that finding is worth protecting from
contamination.

## 6. What the machine records

The study bundle records events to a local append-only log first and uploads
them second. The local file is the record of truth; uploading is idempotent
(safe to repeat) and automatically retried. Nothing is lost if the network
drops, and running the sync twice cannot double-count because every event
carries a content-addressed ID.

### Recording sources

| Source | How it is captured | What it provides |
|---|---|---|
| Claude Code prompts | `UserPromptSubmit` hook | The exact prompt, timestamp, and session ID |
| Agent tool calls | `PreToolUse` / `PostToolUse` / `PostToolUseFailure` hooks | Tool name, command, file, and success/failure |
| Agent turn boundaries | `SessionStart` / `Stop` / `SessionEnd` hooks | Turn latency and turns per request |
| Participant's own commands | PATH wrappers for `git`, `sgt`, `pytest`, `python` | Command arguments, exit code, and duration |
| Editor actions | Same wrappers, reached through the `git.path` and `sgt.path` editor settings | Arguments of every read or change made through a view |
| Repository state | `git rev-parse` plus test run at each card boundary | Tree hash and whether tests pass |
| Heartbeat | 30-second ping | Liveness indicator on the experimenter's monitor |

Every command carries a label for the surface it came from: `terminal`,
`editor`, or `agent`. The launchers set this label (rather than the analysis
inferring it later) because `git log` typed in a shell and `git log` run by a
history view are the same string, and the difference between them is exactly why
the editor is part of this study.

**What is excluded from the participant's record.** Four categories of
instrument activity are kept out:

1. An editor's own periodic background polling and extension initialization are
   recorded but flagged `auto`, and the analysis drops them.
2. The git commands that the sync daemon runs to check for changes are not
   recorded at all.
3. The question the setup check asks the assistant (to prove the API key works)
   is not recorded at all.
4. A command run by another recorded command is also not recorded, and the
   wrapper skips starting Python so the nested call has zero overhead.

Point 4 matters more in one condition than the other: `sgt` shells out to git
about five times per command. Without this exclusion, a two-minute editor session
produced 136 git calls against 28 sgt calls, which would read as "they mostly
used git" in the condition where they mostly used sgt. It also removed a
one-sided timing penalty, since only the tool that spawns subprocesses was paying
for the instrumentation overhead.

None of this was true for the first pilot. 450 of its 476 command events were
the sync daemon checking the repository every twenty seconds, and one of its two
prompts was the setup check's own "Reply with exactly: ok." A session cannot be
re-run, so an instrument that records itself is not something the analysis can
fix afterwards.

Hooks run with `async: true`, so the recording cannot make the participant's
assistant feel slow, and a broken hook cannot block a session.

### The action taxonomy

Raw events are classified into nine categories. Everything downstream,
including the n-gram analysis, works on this alphabet.

Two special rules exist because the editor reaches the same command-line
interface through commands a terminal rarely uses:

1. A grouped command is classified under its verb, not the group. So
   `feature regroup split` is counted as an operation, not an inspection.
2. A report-only call is classified as an inspection. This includes anything
   named `preview` and anything the extension requested with `--json` (which is
   how the extension reads data). Hovering a feature in the VS Code workbench
   fires `advanced preview revert <feature> --json` several times per second;
   counting those as reverts would report that somebody operated on history when
   they merely moved a mouse across a list. Identical editor reads within two
   seconds of each other are also collapsed to one event for the same reason.

| Category | Includes |
|---|---|
| `orient` | `git log`, `git status`, `sgt log`, `sgt now`, `sgt status` with no specific target |
| `inspect` | `git show/blame/diff/bisect`, `sgt show/why/recall/diff` targeted at a specific item |
| `search` | `grep`, `rg`, `find`, `ls`, reading a source file |
| `prompt` | A message sent to the AI assistant |
| `agent_edit` | `Edit`, `Write`, or `NotebookEdit` performed by the assistant |
| `manual_edit` | A tracked file changed with no agent edit accounting for it |
| `history_op` | `git revert/reset/rebase/cherry-pick/checkout`, `sgt revert/restore/save/split/merge` |
| `verify` | `pytest`, running the app, `git diff` after an edit, `sgt drift` |
| `recover` | `git reflog`, `git reset --hard`, `sgt restore` after a failed operation, re-unpacking the bundle |

`manual_edit` is inferred (not directly observed) and is marked as inferred
wherever it appears. `verify` deliberately requires that the action happen
*after an edit*: `git diff` before a change is orientation, the same command
after a change is checking. Any taxonomy that ignores position mislabels
roughly a third of diffs, which was visible in the pilot logs.

### Derived measures

Computed per card, per condition:

- Number of prompts sent, and mean prompt length
- **Prompt specificity**: does the prompt name a commit SHA, an intent ID, a
  file, or a test? A four-level ordinal scale. This is the concrete form of
  "directs the agent precisely" and is the measure most likely to show the
  representational difference, because sgt gives people nameable things to
  point at.
- **Verification ratio**: count of `verify` events divided by count of
  (`history_op` + edit) events
- **Time to first history operation**: how long the participant spends
  orienting before acting
- **Wrong turns**: a `history_op` followed within 120 seconds by a `recover`
  event
- **Collateral damage**: tests failing outside the target feature, reported by
  the scoring script
- **Calibration**, on the reach prediction: stated confidence minus proportion
  correct, both on 0-1. Positive is overconfidence. Null unless both the
  confidence rating and the scored answers are present, because a missing
  confidence is not a confident zero. It is computed at both stages, and the
  pair is the failure this study is most afraid of finding in its own tool:
  `blindConfidence` high over a low `blind` score is a participant believing a
  label that was wrong.
- **Reach prediction** (card 3): `blind`, `checked`, and
  `gain = checked − blind`, each the F1 between the set of behaviours ticked and
  the set the key names. F1 rather than agreement over the twelve boxes, because
  an empty answer agrees with eight of twelve on a key of size four and would
  score 0.67 for knowing nothing; under F1 it scores zero. Also recorded per
  stage: how many boxes were ticked, the confidence, and the active
  milliseconds, so a blind answer given in four seconds is distinguishable from
  one that used the minute. Computed by `reachMetricsFor` in
  `web/src/analysis/pipeline.ts`.
- **Action n-grams**: bigram (two-step) and trigram (three-step) sequences over
  the category alphabet

### N-gram comparison method

Bigram and trigram frequencies are compared between conditions using the
**weighted log-odds ratio with an informative Dirichlet prior** (Monroe,
Colaresi, and Quinn, 2008), with the pooled corpus as the prior. Raw frequency
differences and plain lift both over-report rare sequences, which with only
twelve participants mostly means they report noise. Monroe's z-scores shrink
rare sequences toward zero, which is the standard fix. Sequences appearing
fewer than five times across the entire corpus are dropped before scoring, and
the number dropped is printed on the figure rather than left implicit.

## 7. Analysis plan

### The unit of comparison

Every participant works in both conditions in one sitting, each condition on a
different (but structurally identical) project, with condition order and project
assignment fully crossed. Every outcome is therefore a **within-participant
difference** (sgt minus git), which removes between-person variation in
programming experience, variation that at n=12 would otherwise swamp any effect
the conditions produced. A participant who completes only one half contributes no
difference and is dropped from paired comparisons rather than imputed.

### Which measures carry which weight

Stated in advance so that a lower-tier measure that happens to move cannot be
reported as if it were the main finding.

| Tier | Measures |
|---|---|
| Primary | D2 locate accuracy (binary), the D3 behavioral probe (binary), the D3 reach prediction's `blind` and `gain`, the W removal rubric, and collateral damage from `score_study_repo.py` |
| Secondary | Self-report: NASA-TLX, UMUX-Lite, the HLAC battery, the end-of-session preference block |
| Descriptive | Telemetry: surfaces used, action sequences, prompt specificity, calibration, wrong turns, time to first history operation |

`gain` is primary and is the measure the prediction was added for, so the
direction that would falsify the claim is pre-committed here: `gain` at or below
zero under sgt, or `blind` accuracy low while `blindConfidence` is high, which
is the failure of a confidently wrong inferred label. Neither is reported as a
surprise if it happens.

Self-report is not primary and is treated as such throughout. It is the tier
most exposed to demand characteristics, the participant knows one setup is
ours, whatever we call it on screen.

The two manipulation checks at the end of the HLAC block (§5.5) are in no tier,
because they are not outcomes. They are reported as paired differences like
everything else, but a difference on them is a problem with the design rather
than a result about the tools: if the cards read as less realistic in one
condition, or if the time cap bound harder in one condition, that is the first
thing the discussion has to deal with rather than the last.

### The estimate is the headline, not the statistical test

Every outcome is reported as a paired mean difference with a **95% bootstrap
confidence interval** (CI), resampling participants rather than individual
observations, using 10,000 resamples computed from a fixed random seed so that a
figure and its caption cannot drift apart. Intervals are plotted, not just listed
in a table.

With only a dozen pairs, a p-value invites a binary reading (significant or not)
that the data cannot support. The confidence interval communicates the same
information and honestly shows how little it pins down. Where a formal test is
informative, we use the **Wilcoxon signed-rank test** on paired differences with
**matched-pairs rank-biserial** effect sizes, and an **exact McNemar test** for
per-card binary outcomes (located / not located, damage / no damage). These are
reported alongside the intervals, never in place of them. Using nonparametric
tests is not a concession here, at these sample sizes they are frequently the
more powerful choice.

Questionnaire responses are additionally shown as **full distributions per
item**. A cluster at the midpoint and a split between the extremes have the same
mean and are not the same result.

Composite scores summed across several items (the TLX average, the UMUX-Lite
score, a rubric total) can reasonably be treated with parametric methods.
Individual ordinal items do not get a metric model; this is the one point the
ordinal-data methodology literature does not divide on.

### The preference block, and what its midpoint means

Each comparative item in the closing block (§5.7) is recoded to −2 … +2 in the
sgt-positive direction, using whichever letter sgt was for that participant, and
reported item by item with its own n and its full distribution. The overall
comparison is one item among the ten, not a summary of them: an overall
preference that disagrees with the seven job items is itself the interesting
result.

"No real difference" is reported as its own category. It is never dropped as
missing, never redistributed across the two sides, and never treated as a
failure to answer. Twelve people choosing the midpoint on an item is a finding
about that job, and it is the finding the paper's tradeoff argument most needs.

### Families of comparison, and what we do not correct for

Five families of comparison are named in advance: the three HLAC blocks,
NASA-TLX, and UMUX-Lite. All five are reported whether or not they moved. No
post-hoc multiple-comparison correction is applied across them. At this sample
size the real risk is selective reporting (only showing the families that moved),
and a correction addresses the wrong problem while making an already
underpowered study unable to answer anything at all.

### Qualitative material

Think-aloud recordings are treated as protocol data and analyzed alongside a
reflexive thematic analysis of the closing interviews. Navigation behavior is
hand-coded in both conditions using the same coding scheme, so that navigation
remains a property of the person rather than of the instrumented tool. Two
coders work from a codebook agreed before coding starts, with 25% double-coded
and disagreements resolved by negotiated agreement following McDonald et al.
The recording that explains a quantitative result is reported beside it.

### Pre-registration, and the limits of the claims

Research questions, conditions, rubric wording, exclusion rules, and planned
comparisons are registered before the first participant. This document is that
pre-registration text.

The study is **powered for large effects only**, and we say so directly rather
than treating a wide interval as evidence of absence. Workload and usability
scores carry no norms for this kind of work and are not ratio measures, so we
interpret only the difference between conditions on the task the instrument
names. No claim is made that any absolute score is high or low.

Two results are pre-committed as expected:

1. The removal card (W) is where sgt should show its clearest advantage, because
   taking one piece of work out of a history that other work has landed on top
   of is the job the representation is for. This is a primary measure, not a
   descriptive one, so a null result here counts against C2 rather than being
   set aside.
2. The discriminant scenarios at the end of the session should produce **mixed**
   results. If a participant picks the same setup for a repository they will
   never see again *and* for a codebase they will own for a year, that is
   evidence of demand characteristics (answering how they think we want) and is
   reported as such rather than as a preference.

Two **dissociations** are predicted as well, and named here so that finding one
is a result rather than a discovery made while reading the data:

3. **Confidence may come apart from preference.** "Being confident the result
   was what you intended" and "which setup would you rather work in" can point
   different ways, and if they do, that is a finding with precedent rather than
   noise: Vaithilingam et al. (§5.7) found 23 of 24 participants calling a tool
   more helpful while only 10 of 24 felt more confident in what it produced. A
   study that collects only an overall preference cannot see this, which is part
   of why the block asks about seven jobs.
4. **The two self-report blocks may disagree with each other, and that is
   informative.** Five of the seven job comparisons have a near-twin in the HLAC
   battery: finding when something changed, seeing what came with it, removing
   safely, recovering, and checking the assistant's work. HLAC is answered once
   per half, in absolute terms, about the half just finished; the preference
   block is answered once, in relative terms, about both. Crossing them:
   agreement is convergent validity for an ad-hoc battery that has none
   otherwise, and disagreement is a result about how people compare against how
   they rate in the moment. Both are reported.

   The threat on that crossing runs one way and is stated with it. The second
   half's HLAC is answered immediately before the comparison block, and the
   first half's about thirty minutes before it (§4), so a participant can
   align a comparison to the rating they remember giving rather than to what
   happened. Agreement between the two blocks is therefore the weaker evidence,
   and disagreement, which recall-alignment cannot manufacture, is the stronger.

An earlier version of this document pre-committed to request 6, the history
surgery task, as the clearest demonstration. That request no longer exists (§2).
The prediction is not quietly transferred to a different request: the removal
card (W) is named here as the expectation from 2026-08-22 onward, and the paper
reports the change of pre-commitment along with its date.

## 8. The three figures

The figure budget for a CHI (Conference on Human Factors in Computing Systems)
results section is three. These three, in this order, carry claims C1 and C2
and question Q4.

### Figure 1. What the two setups felt like

Diverging stacked Likert bars for the twelve seven-point HLAC items, grouped
by their three blocks with reverse-keyed items marked, one panel per condition,
response counts printed inside the segments. A right-hand panel shows paired
mean differences with 95% bootstrap confidence intervals (CIs). Items are shown
individually; the block mean appears as a summary line, not as a construct
score, and no reliability coefficient is reported for blocks this short.

The two manipulation checks that close the HLAC block (§5.5) are **not** in this
figure. They are on five points, not seven, so a diverging bar chart that
included them would put two scales in one axis, and they are checks on the
design rather than perceptions of the setups.

This figure goes first because it is the only one a reviewer can read in five
seconds, and because perception is the claim most people will test against their
own intuition.

### Figure 2. What people actually managed to do

A paired within-subject estimation plot with five panels: D2 locate accuracy
(binary: found it or did not), D3 behavioral probe (binary: back-to-back
accepted or not), D3 reach prediction gain, W removal (rubric points), and
collateral damage. For each: every participant's two scores are joined by a
line, with condition on the x-axis, and beneath it the paired mean difference
with its bootstrap CI on a floating axis (Gardner-Altman style).

`gain` is `checked − blind`, and it is the panel the prediction was added for,
the only place the study asks whether the representation moved an expectation
rather than whether it was available to be read.

The panel list lives in `web/src/analysis/figures.ts` and both the dashboard and
the paper export read it. They used to hold a list each, and the test that
renders the publishable figure was rendering panels the app did not ship.

Showing twelve individual slopes is the honest way to plot n=12. A bar chart of
two group means hides whether one person moved a lot or twelve people moved a
little, and at this sample size that distinction is the whole question. Collateral
damage is on its own panel with an inverted axis so that "up is better" holds
consistently across the figure.

### Figure 3. How the work was done

Two stacked panels sharing a legend:

- **(a) Where the time went.** One horizontal strip per participant-half, with
  the x-axis showing proportion of that half's active time and segments colored
  by action category, grouped by condition. Above each group: a stacked area
  chart showing category share across normalized time. The individual strips
  show the distribution; the area chart shows the shape.
- **(b) What the sequences looked like.** A dot plot of the most discriminating
  action bigrams by Monroe z-score, with sgt-leaning sequences on the right and
  git-leaning sequences on the left, annotated with occurrence counts.

Figure 3 is the figure that makes the paper more than a benchmark. Panel (a)
shows *when* the two groups diverged, panel (b) shows *what pattern* differed,
and together they are the mechanism behind Figures 1 and 2. This is also the
figure that answers the reviewer question that sinks tool papers: "did they just
use the AI more?"

Every figure exports to SVG with fonts as text, an ACM-column-width preset
(3.33 inches for single-column, 7.0 inches for double-column), and no
rasterized layers.

## 9. Honest limits, stated up front

- Twelve participants can detect large effects only. Results are reported as
  intervals, never as significance claims.
- Participants have years of git experience against ten minutes of sgt. This
  asymmetry cuts against sgt, so a positive result is conservative and a null
  result is uninformative about sgt's ceiling.
- A novelty effect runs the other way and cannot be fully removed. It is
  mitigated by neutral naming and by never revealing authorship, and it is stated
  as a limitation.
- **Letter and order are perfectly confounded within a participant.** The setups
  are called A and B, and the letters are assigned by order, so "Setup B" always
  means "the one I used second" for the person answering. Recency favors it in
  an end-of-session comparison. Counterbalancing fixes this at the group level
  and cannot fix it inside a person, so no single participant's comparison can
  be read as free of it. We use letters at all because every comparable study
  named its two tools outright and this one cannot: naming them tells the
  participant which one is ours, which is the larger threat. The comparison
  block opens by restating which letter was which (§5.7); that is a reminder,
  not a remedy.
- Agent variance is part of the condition, not controlled away. The model
  version is pinned, and every transcript is kept.
- Two small synthesized codebases and 24-minute halves. Real theory building
  happens over weeks. The lab study answers the question of first contact only,
  which is why the design document recommends a field deployment as the
  companion study.
- Four cards covering two task archetypes: locating the work behind a defect
  (D1-D3) and removing a feature cleanly (W). A claim about "history work"
  generally rests on two archetypes out of the six Codoban et al. name. The
  reach prediction sits inside change impact rather than adding a third
  archetype: it asks about the judgement a removal depends on without asking for
  the removal. The six cut requests (three on 2026-08-17, three more reshaped on
  2026-08-22) were cut because pilots could not finish them or because they
  measured the wrong thing (comprehension rather than locate-and-reverse), and
  both are reported rather than left as unexplained changes of design. Whether
  the remaining caps bound harder in one condition than the other is no longer
  left to argument: it is asked directly after each half (§5.5, M2).
- The reach prediction supplies the twelve behaviours. That is what makes it
  scoreable and comparable across participants, and it is also a ceiling on what
  it shows: recognising which of a given list a piece of work reaches is easier
  than asking, unprompted, what else might break. A participant who would never
  have thought of the timetable at all still ticks it when it is on the page. The
  measure is of a supplied set, and `gain` is a difference within that set.
- Card 2's locate scoring uses free-text matching against an accepted-strings
  list, done after the session by the experimenter rather than automatically in
  the browser. This introduces inter-rater variance on borderline answers (a
  description that is close but not on the list). The matching function handles
  the mechanical cases (sha prefixes, case differences, punctuation), but a
  judgment call on "I think it was the slot comparison thing" remains a judgment
  call. Both raters score independently and disagreements are resolved by
  discussion.
