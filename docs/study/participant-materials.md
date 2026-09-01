# Running a session

This is the facilitator's copy. It contains the answers, so do not screen-share it.

The study runs from a website. The mechanical steps — setting up machines,
managing participants, uploading data — are in `running-the-study.md`. The
research design, every measure and the analysis plan are in `protocol-v2.md`.

What remains here is what the website cannot do for you: what to say to each
participant, what to watch during the session, and what the answers are.

The web console handles the clocks, records every questionnaire answer, scores
the two checklists against the answer key, and stores your interview notes. The
key itself is `docs/study/answer-key.json`, which the console loads so the
correct answer appears beside each stage while you score.

Participant handouts live in `materials/`, as printed copies. The wording lives
in `web/src/study/content.ts` (welcome, project tour, practice sheets) and
`web/src/study/tasks.ts` (the four stages), which is what the website renders and
what the participant actually reads. If the two disagree, the code is right and
`materials/` needs regenerating — `npm run gen:materials` in `web/`, and
`npm test` fails until you do.

## Quick overview

- Each participant completes two halves: one with git and one with sgt, on two
  different projects (`bikecount` and `footfall`).
- Each half is the same four stages. Every stage begins with `./stage N`, which
  resets the project, so a stage that goes badly cannot spoil the next one.
- The stages are: get to know the project (5 min), find the work behind a wrong
  number (4), take that work out (4), put it back (4). A short untimed quiz and
  two or three rating statements follow each stage.
- Before the stages, practice on the same project with no clock running. Let
  them take the time.
- You are testing the two setups, not the person. Say this out loud, and often.
- A session is about 90 minutes. The per-step estimates are computed in
  `web/src/study/flow.ts` and shown on the welcome page.

## Before the participant arrives

**Remote sessions (the default):** send the participant their link a day early
and let the website walk them through consent, the pre-study questionnaire and
setup. `running-the-study.md` section 2 has the detail.

**In-person, on a machine you control:**

```bash
scripts/setup-study-session.sh p07 sgt footfall
```

This creates a fresh copy of the project, installs the exact sgt build being
tested, refreshes the history view, and refuses to hand over a copy whose
dashboard does not render. Never reuse a copy between participants.

Also check, before the session:

- Screen and audio recording works.
- `study-doctor` passes in the participant's session shell.
- The dashboard starts: `python3 -m <project>.server`, then
  <http://localhost:8000>.
- For the closing interview: either the participant's own repository has been
  built (setup does this in the background when they tick the consent line), or
  the prepared repository is warm. See `interview-demo-easyocr.md` for its
  pre-flight checklist.

## Counterbalancing: which condition, which project

Twelve participants in four groups of three, assigned round-robin so that any
prefix of the cohort is balanced. The authority is `groupForOrdinal` in
`web/src/study/flow.ts`; this table is a copy of it.

| Group | First half | Second half |
|---|---|---|
| 1 | git, bikecount | sgt, footfall |
| 2 | sgt, bikecount | git, footfall |
| 3 | git, footfall | sgt, bikecount |
| 4 | sgt, footfall | git, bikecount |

The participant only ever hears "Setup A" and "Setup B", in the order they use
them. Never say which is which, and never say "ours".

## Session timing

| Minutes | What happens |
|---|---|
| 4 | Consent and the pre-study questionnaire |
| 6 | First setup |
| 4 | Practice with the first setup. No clock |
| 21 | First half: the four stages, with their quizzes |
| 2 | UMUX-Lite and NASA-TLX for the first setup |
| 2 | Second project's setup |
| 3 | Practice with the second setup. No clock |
| 21 | Second half: the same four stages on the other project |
| 2 | The same two questionnaires |
| 3 | Comparing the two setups |
| 15 | Repository walkthrough and interview |
| 2 | Send the data and clean up |

Rounded groupings. The exact numbers are in `flow.ts`, and the welcome page is
generated from them.

## What to say to the participant

- "Setup A" and "Setup B". Never "ours", never a hint that one is newer.
- "We are testing the setups, not you."
- "Keep talking. Tell me what you expect before you run it."
- When they stall: "That's useful, tell me what you're thinking." Do not help
  unless something is actually broken.
- The stages have different caps — 5, 4, 4, 4 — so check which stage is up
  before calling the time. The website shows the countdown; call the halfway
  mark and two minutes left.
- When the clock runs out, move to the questions. The questions are untimed and
  the participant should not rush them.

## What to watch for (qualitative observations)

Write these down as they happen. This is your qualitative data.

- The moment they stop trusting a number or a message the tool printed.
- Any command they run twice because the first result did not make sense.
- Whether they check their work, and how: the dashboard, the check script, the
  history, or not at all.
- Where they say "I don't know what that means."
- Any point where they give up on a tool feature and do it by hand.
- In stage 1, which surface they reach for first, and whether they ever open
  the dashboard beside the history. Stage 1 has no scored output, so these notes
  are its data.

## Scoring guide

One thing is scored against the key: the piece of work the participant names in
stage 2, and your reading of it is the authority. Stages 3 and 4 are scored from
the repository afterwards. Stages 1 and 3 ask nothing with a right answer.
Nothing needs grading by hand.

### Stage 1 (s1): get to know the project

**Nothing to score.** No task output, no quiz, no key. The card carries the
project's whole history as a map — one row per piece of work, its files, and what
it puts on the dashboard — and asks the participant to put that beside their
setup's view of the history and work out what their setup calls each row. What
comes out is three rating statements, the time, and whatever they say while doing
it.

So this is the stage where your notes are the data. Worth writing down:

- Which they open first, the dashboard or the history, and whether they ever have
  both on screen at once.
- Whether they work down the map or jump around it.
- In the sgt arm: whether they talk about the work using the lane and checkpoint
  names their install rolled, or keep using the map's words. Either is a finding.
- Any row they cannot place, and what they tried.

The map names no sgt feature or checkpoint, deliberately: the same card goes to
both arms, and putting one arm's vocabulary on it would answer the stage for that
arm. The labels themselves are stable — the bundle ships its mined graph frozen
and every `./stage N` restores it, so every participant in the sgt arm sees the
same feature and checkpoint names, and you can read them once at pre-flight if
you want to follow along. Nothing on the card is withheld: the answers are
printed on it.

### Stage 2 (s2): find the work behind the wrong number

Two things are recorded.

**The recognition question** — "which of these is the work you found" — is
unscored. It is there for the participant who found the work but could not write
a handle for it.

**The checklist** — what that work affects — is scored as set F1 against the key.

You score the locate itself, from what they say aloud and from the recognition
answer. The target, in each arm's own words:

| | Accepted |
|---|---|
| footfall | the group `Event Day Handling`; or any of the three commits it spans; or their subjects |
| bikecount | the group `Event Day Handling`; or any of the three commits it spans; or their subjects |

`answer-key.json` carries the full accepted-strings list for each project,
including the git arm's own shas — the two arms are separate builds, so the sha a
git participant reads is not the sha the sgt repo holds. The console shows the
list beside their answer.

The measured reach, which stages 2 and 3 are both scored against:

| | Parts of the dashboard the event-day work reaches |
|---|---|
| footfall | busiest day, last-fortnight chart, both hour-of-day charts, busiest hour, month-by-month chart, event marks, by-year table, north–south comparison (9 of 11) |
| bikecount | the same, minus the east–west comparison (8 of 11) |

Nine and eight of eleven, so ticking everything scores about 0.86. Report that
baseline with the result; it is in `protocol-v2.md` section 11.

### Stage 3 (s3): take that work out

`./stage 3` names the work in that arm's own vocabulary, so a participant who
failed stage 2 does not fail this one for the same reason.

The participant verifies with `./check 3`, which prints the same words in both
arms and does not mark them. You score from the repository afterwards:

```bash
python3 scripts/study/score_dashboard.py ~/study/p07/work \
    --expect ~/repos/sgt-study/footfall/.study/removed-pages \
    --target-pages hourly,monthly,overview,sides,yearly
```

Three results come back separately, and they must stay separate:

1. **runs** — whether the app starts at all. A repository that will not render is
   a different kind of outcome, not a wrong answer.
2. **target** — whether the pages the removal was supposed to reach now match.
3. **collateral** — whether every other page is untouched. This is the one the
   study is really about.

The target pages are `hourly, monthly, overview, sides, yearly` for footfall and
`hourly, monthly, overview, years` for bikecount (bikecount routes its by-year
page at `/years`). Both lists are in `answer-key.json` under `s3.markers`.

The quiz is the same checklist again, asked as what changed when the work came
out. `gain` — this answer minus the stage 2 prediction, both F1 against the same
measured set — is the primary measure for C3.

### Stage 4 (s4): put it back

`./stage 4` puts everyone into the removed state, whether or not their own
removal worked, so this stage is a clean paired comparison.

There is no quiz. Three rating statements only, and the reverse-keyed one — "I
would want to re-check everything by hand before I trusted it" — is the honesty
valve of the whole design. If a participant restores correctly and still says
they would re-check, that is a finding, not noise.

Scored from the repository: every page has to match the pre-removal snapshot
exactly.

```bash
python3 scripts/study/score_dashboard.py ~/study/p07/work \
    --expect <the original snapshot>
```

`./check 4` tells the participant whether the by-year page reads its
excluded-days number again; that is necessary and not sufficient, which is why
the scorer compares every page.

## After each half

Two published instruments, both administered by the console, immediately after
the stages: **UMUX-Lite** and **raw NASA-TLX**, pointed at the four stages just
finished. About two minutes. Nothing here is scored by you.

Two things to leave alone while they answer. The workload scales are marked on a
rule of tick marks with no number anywhere, and one of the six runs the other way
from the rest — "Failure" on the left, "Perfect" on the right. That is the
published instrument, and the page marks those two words in a different colour
for exactly this reason. If someone asks, point at the two words at the ends and
say nothing else; telling them which end is the good one is telling them what to
answer.

Do not apologise for the clock before they answer temporal demand.

## At the end of the session

- The console administers the comparison block: one comparison per job the
  stages exercised (getting to know a project, finding a piece of work, removing
  one, putting one back), then an overall comparison, then "would you put the
  second setup on a repository you own", then one open box. Each comparison
  offers five options — A clearly, A slightly, no real difference, B slightly, B
  clearly. "No real difference" is a real answer and we want it where it is true,
  so do not nudge anyone off it.
- The interview runs over the participant's own repository, or over the prepared
  one (`interview-demo-easyocr.md`). The guide is ten questions in four groups,
  in `protocol-v2.md` section 6.4 and in the console's **Interview** tab. Ask
  them in the order given — the last one asks what *version control* should let
  you act on, and it only tells us something while sgt's own framing for that is
  still off the table.
- Note on the roster which interview path was taken. The website cannot know.
- Watch them run `study-sync --final` and then `study-cleanup`.

## Analysis

The console collects everything and exports it. **Results → Compute from data**
builds the analysis from the raw event stream: the figures, plus one row per
participant per condition, one row per stage, and the coded action stream.

For each participant per half you should have:

- Stage 1: three ratings (also rolled up as one mean, reverse-keyed item
  flipped), time, and whether the cap was hit. No checklist and no key.
- Stage 2: locate correct or not (your reading is the authority), the
  recognition choice, confidence in the task, two ratings, time.
- Stage 3: runs / target / collateral from the scorer, confidence in the task,
  three ratings, time.
- Stage 4: fidelity from the scorer, three ratings, time.
- UMUX-Lite and NASA-TLX per half.
- Your qualitative notes.

When analysing:

- Compare within participants, not between them. Everyone does both setups.
- Report effect sizes and intervals. Twelve participants cannot support claims
  about small differences; say that rather than reaching for a p-value.
- Report the tick-everything baseline beside every checklist mean.
- Pair every number with the recording that explains it. A time difference means
  nothing until you can point at what the person was doing.
- Code the recordings with two people and agree the codebook first.

The statistical models are `protocol-v2.md` section 10; the export buttons are
`running-the-study.md` section 5.

## Notes

- If a copy reaches a state the participant cannot recover from, note the time,
  run `./stage N` for the next stage, and mark the stage as stopped by a tool
  failure. Every stage resets, so nothing is lost beyond that stage.
- The `quiet sensor` note on the two-sensor page is deliberate, and so is the
  partial-year caveat under the by-year table. If a participant asks, say the
  history will tell them.
- The git copies were rendered with every mention of sgt stripped. The sgt copies
  keep their own commits, which is correct. `pilot-02-findings.md` says why.
- One sgt defect is worth knowing before it happens on screen: reverting a
  *feature* rather than the named group usually leaves the dashboard dead, and
  says `✓ revert applied` while doing it (`sgt-findings.md`, finding 85). `sgt
  undo` recovers exactly, and it is in the stage's own tips. Stage 3 names the
  group, so this is off the guided path — but note it if someone wanders there.

## Other files in this directory

- `README.md` — the one-page overview. Give this to a new experimenter first.
- `protocol-v2.md` — the protocol and the pre-registration text.
- `running-the-study.md` — the mechanical steps, console and bundles.
- `remote-setup.md` — setting up a participant's laptop and API keys.
- `interview-demo-easyocr.md` — the prepared repository for the closing
  interview, and its pre-flight checklist.
- `testbed-spec.md` — how the two study projects were built.
- `build-log-*.md` — the ground truth for each project's history.
- `pilot-01-findings.md`, `pilot-02-findings.md`, `pilot-03-findings.md` — what
  the pilot sessions found. They describe earlier designs of the task block,
  because that is what was piloted.
- `sgt-findings.md` — the running list of known sgt problems found during the
  study.
