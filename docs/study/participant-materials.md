# Running a session

This is the facilitator's copy. It contains the answers, so do not screen-share it.

The study now runs from a website. The mechanical steps (setting up machines,
managing participants, uploading data) have moved to `running-the-study.md`.
The research design, measures and statistical models are in `protocol.md`.

What remains here is what the website cannot do for you: what to say to each
participant, what to watch during the session, and the answer key for scoring.

The web console handles clocks, records questionnaire answers, applies the
scoring rubrics listed below, and stores your interview notes. The answer keys
below also live in `docs/study/answer-key.json`, which the console loads so
the correct answer appears next to each card while you score.

Participant handouts live in `materials/`, as printed copies. The wording itself
lives in `web/src/study/content.ts` (welcome, project briefs, practice sheets)
and `web/src/study/tasks.ts` (the task cards), which is what the website renders
and what the participant actually reads. If the two ever disagree, the code is
right and the copy in `materials/` needs updating.

## Quick overview

- Each participant completes two halves: one with git and one with sgt, on two
  different projects.
- Each half has the same four cards, worded for someone who has never seen the
  project before, and 24 minutes to work through them. The cards walk from
  observing a defect, to locating its cause, to reversing it with a reach
  prediction, to removing a whole feature.
- Before the cards, and with no clock running, they read a page describing
  what the program is for. Let them take as long as they want on it.
- You are testing the two setups, not the person. Say this out loud, and say it
  often.
- A session takes about 90 minutes per participant. The exact per-step estimates
  are in `web/src/study/flow.ts`.

## Before the participant arrives

**Remote sessions (the default):** send the participant their link one day
early and let the website walk them through consent, background questions and
setup. See `running-the-study.md` section 2 for details.

**In-person sessions on a machine you control:**

```bash
scripts/setup-study-session.sh p07 sgt coursecraft
```

This script creates a fresh copy of the project, builds the test environment,
and refuses to hand over a copy whose tests do not pass. For the sgt condition
it also installs the exact sgt build being tested and refreshes the history
view. Never reuse a copy between participants.

Before the session, also check:

- Screen and audio recording works.
- The `.env` file is present in `work/` for the sgt half (plain-English
  commands need it).
- Claude Code is logged in. See `remote-setup.md` for instructions.

## Counterbalancing: which condition, which project

Twelve participants, divided into four groups of three. Each group gets a
different ordering of conditions and projects, so every combination appears
and ordering effects wash out.

| Group | First half | Second half |
|---|---|---|
| 1 | git, coursecraft | sgt, confplan |
| 2 | sgt, coursecraft | git, confplan |
| 3 | git, confplan | sgt, coursecraft |
| 4 | sgt, confplan | git, coursecraft |

## Session timing

| Minutes | What happens |
|---|---|
| 5 | Consent and background questions |
| 10 | First setup and practice |
| 2 | Reading about the first project. No clock |
| 24 | First half: four cards |
| 5 | Three questionnaires: workload, usability, the history |
| 8 | Second setup and practice |
| 2 | Reading about the second project. No clock |
| 24 | Second half: the same four cards on the other project |
| 5 | The same three questionnaires |
| 5 | Comparing the two setups, then the interview |

These are rounded groupings. The exact per-step estimates are in `flow.ts`.

The two reading pages are not timed and are not a formality. Pilots used to meet
the codebase for the first time with a countdown already running and spent a
third of the first card working out what the program was for.

## What to say to the participant

- Call them "the first setup" and "the second setup". Never say "ours" or
  imply that one is better.
- "We are testing the setups, not you."
- "Keep talking. Tell me what you expect before you run it."
- When they stall: "That's useful, tell me what you're thinking." Do not help
  unless something is actually broken.
- Call the time at the halfway mark and at two minutes left for each card. The
  four cards have different caps (3, 5, 6, and 10 minutes), so check which card
  is up before calling.

## What to watch for (qualitative observations)

Write these down as they happen. This is your qualitative data.

- The moment they stop trusting a number or message the tool printed.
- Any command they run twice because the first result did not make sense.
- Whether they check their work, and how (tests, running the program, or
  neither).
- What they hand to the AI assistant versus what they insist on doing
  themselves.
- Where they say "I don't know what that means."
- Any point where they give up on a tool feature and fall back to doing it
  by hand.

## Scoring guide

### Card 1 (d1): observe the defect

Nothing to score. The participant runs `./show-the-problem.sh` and writes down
what they see. Their notes are recorded for qualitative analysis.

Watch what they focus on: the enrollment rejection, the room audit message, or
the green tests. A participant who notices all three, and especially one who
notices that the tests pass over a broken program, is telling you something
about their model of the codebase.

### Card 2 (d2): locate the work

The participant types what they think caused the defect into a free-text box.
Do not score this live. The analysis pipeline scores it post-session against
the accepted-strings list in `requestKeys.d2.locate` in the answer key.

Accepted answers include the commit sha, the feature name (under sgt),
"ranges_clash", "slot comparison", "normalize slot comparison", "E17", and
several others. The match is case-insensitive, strips punctuation, and accepts
sha prefixes from 7+ characters.

**What happened.** Episode 17 ("normalize slot comparison") added a function
`ranges_clash` that uses `<` where the original `overlaps` uses `<=`, then
repointed callers. Back-to-back slots that share an endpoint are now rejected.
The test `test_back_to_back_is_fine` still passes because it calls `overlaps`
directly, not through the app.

| Project | Commit | sgt feature |
|---|---|---|
| coursecraft | `25e91a9` | E17 (normalize slot comparison) |
| confplan | `704e7a4` | E17 (normalize slot comparison) |

These are here for your own orientation. Do not read them out.

### Card 3 (d3): reverse it + reach prediction

Three things are measured.

**1. Behavioral probe.** Did the reversal fix the defect? After the participant
finishes, run:

```bash
python3 scripts/score_study_repo.py ~/study/p07/work \
    --baseline ~/repos/sgt-study/coursecraft \
    --expect-behaviour back-to-back-allowed
```

This checks whether back-to-back enrollment now works by driving the CLI, not
just whether tests pass. Tests alone do not catch the fix, because the orphaned
test (`test_back_to_back_is_fine`) calls `overlaps` directly while the app calls
`ranges_clash`.

**2. Reach prediction.** Scored automatically by the analysis pipeline. The key
says the reversal reaches four of twelve behaviors: cancel, promote, register,
rooms. `blind` and `checked` are both F1 against this key. `gain = checked -
blind` is the primary measure.

**3. Collateral damage.** Tests failing outside the target area. The scorer
reports the count.

### Card 4 (w1/w2/w3): remove the waitlist, keep drops

Three stages on one card with one 10-minute clock: see the waitlist in action,
remove it, then make sure drops still work without promotion.

Run the scorer after the participant finishes:

```bash
python3 scripts/score_study_repo.py ~/study/p07/work \
    --baseline ~/repos/sgt-study/coursecraft \
    --expect-removed waitlist,promotion,notify \
    --expect-gone waitlist,notices \
    --expect-behaviour back-to-back-allowed
```

Record which of these four outcomes happened:

1. **Waitlist gone, everything else passes.** This is the target outcome.
2. **Something else broke.** Count the broken features. This is collateral
   damage.
3. **Waitlist still there.** The removal did not happen.
4. **Tests pass but the program will not start.** Record this separately.

The fourth outcome is why the scorer starts the program. In pilot 1 the
participant finished with 29 passing tests and an application that raised an
error on startup, because no test in the suite exercises the command-line
parser.

The rubric in the answer key is three points: waitlist gone (1), everything else
still passing and the app still starting (1), and drops working with no
promotion (1).

## After each half

Three questionnaires, all administered by the console, immediately after the
participant finishes: **workload** (NASA-TLX), **usability** (UMUX-Lite, two
items), and **the history** (the twelve HLAC items, then two questions about
the cards themselves). Five minutes in total. Nothing here is scored by you.

Two things to leave alone while they answer. The workload scales are clicked on
a line of tick marks with no number anywhere, and one of the six runs the other
way from the rest, "Failure" on the left, "Perfect" on the right. That is the
published instrument, not a bug, and it is marked on the page. If someone asks,
point at the two words at the ends of the line and say nothing else; telling
them which end is the good one is telling them what to answer.

The two questions at the end, whether the cards felt realistic and how much
time pressure they felt, are checks on our design, not on the setups. They are
the only place the study can find out whether the time caps bit harder in one
half than the other, so it matters that the answer is theirs. Do not apologize
for the clock before they answer it.

## At the end of the session

- The console administers the comparison block: seven comparisons over jobs they
  actually did, why, two "where would each earn its keep" scenarios, an overall
  comparison, and what would put them off. Each comparison offers five options,
  A clearly, A slightly, no real difference, B slightly, B clearly. "No real
  difference" is a real answer and we want it where it is true, so do not nudge
  anyone off it.
- Interview prompts:
  - "What did you trust, and what did you check?"
  - "Where were you lost?"
  - "What did the history hide, and what did it show?"
  - "What did you wish you could ask the history?" Ask this *before* they
    compare the two setups. Both pilot participants answered with something
    close to what sgt does, one of them from inside the git half.
- Collect the AI assistant transcript paths.
- Revoke the API key if you issued one. See `remote-setup.md` for steps.

## Analysis

The console collects all data and exports it. Use **Results > Compute from
data** to build the analysis from the raw event stream. This produces three
figures and three CSV files:

- One row per participant per condition (for the mixed-effects models).
- One row per card.
- The coded action stream.

See `protocol.md` section 7 for the statistical models and
`running-the-study.md` section 5 for the export buttons.

For each participant per half, you should have:

- Card 1 (d1): observation notes (qualitative only).
- Card 2 (d2): locate answer, scored correct or incorrect against the key.
- Card 3 (d3): behavioral probe result (back-to-back works or not), collateral
  damage count, reach prediction scores (blind, checked, gain).
- Card 4 (w1/w2/w3): scorer output, which of the four outcomes, rubric points.
- Time per card, and whether the cap was hit.
- Workload, usability, and history scores.
- Your qualitative observation notes.

When analysing:

- Compare within participants, not between them. Every participant does both
  setups.
- Report effect sizes and confidence intervals. With twelve participants you
  cannot support claims about small differences. Say that directly rather
  than reaching for a p-value.
- Pair every number with the recording that explains it. A time difference
  means nothing until you can point to what the person was actually doing.
- Code the recordings with two people and agree on a codebook first.

## Notes

- If a copy gets into a state the participant cannot recover from, note the
  time, restore from a fresh copy, move to the next card, and mark it as
  stopped by a tool failure.
- The `year` and `speaker` leftovers in the code are deliberate. If a
  participant asks, say the history will tell them.
- The git copies were cleaned so nothing in them mentions sgt. The sgt copies
  keep their own commits, which is correct. See `pilot-02-findings.md` for
  why.

## Other files in this directory

- `README.md` — the one-page overview. Give this to a new experimenter first.
- `remote-setup.md` — how to set up a participant's laptop, API keys, and
  Claude Code.
- `testbed-spec.md` — how the two study projects were built.
- `build-log-*.md` — the ground truth for each project's history.
- `pilot-01-findings.md`, `pilot-02-findings.md`, `pilot-03-findings.md` — what
  the pilot sessions found. They describe the six-request study, because that is
  what was piloted.
- `sgt-findings.md` — the running list of known sgt problems discovered during
  the study.
