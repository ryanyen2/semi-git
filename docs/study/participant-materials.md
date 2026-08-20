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
the correct answer appears next to each request while you score.

Participant handouts live in `materials/`, as printed copies. The wording itself
lives in `web/src/study/content.ts` (welcome, project briefs, practice sheets)
and `web/src/study/tasks.ts` (the requests), which is what the website renders
and what the participant actually reads. If the two ever disagree, the code is
right and the copy in `materials/` needs updating.

## Quick overview

- Each participant completes two halves: one with git and one with sgt, on two
  different projects.
- Each half has the same three requests, worded for someone who has never seen
  the project before, and twenty minutes to work through them.
- Before the requests, and with no clock running, they read a page describing
  what the program is for. Let them take as long as they want on it.
- You are testing the two setups, not the person. Say this out loud, and say it
  often.
- A session takes about two hours per participant, of which 113 minutes is
  scheduled steps (`TOTAL_ESTIMATE_MIN` in `web/src/study/flow.ts` — if you
  change a step's estimate, this number moves with it).

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
| 10 | Consent and background questions |
| 20 | First setup, then the practice sheet |
| 5 | Reading about the first project. No clock |
| 20 | First half: three requests |
| 6 | Three questionnaires: workload, usability, the history |
| 15 | Second setup, then the practice sheet again |
| 5 | Reading about the second project. No clock |
| 20 | Second half: three requests on the other project |
| 6 | The same three questionnaires |
| 8 | Comparing the two setups, then the interview |

The two reading pages are not timed and are not a formality. Pilots used to meet
the codebase for the first time with a countdown already running and spent a
third of the first request working out what the program was for.

## What to say to the participant

- Call them "the first setup" and "the second setup". Never say "ours" or
  imply that one is better.
- "We are testing the setups, not you."
- "Keep talking. Tell me what you expect before you run it."
- When they stall: "That's useful, tell me what you're thinking." Do not help
  unless something is actually broken.
- Call the time at the halfway mark and again at two minutes left. Request 1 is
  only five minutes long, so those are nearly the same moment: call it once, at
  two minutes left.

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

### Request 1: what changed course/talk search?

**Nothing to score by hand.** The request asks three multiple-choice questions
and one confidence rating, and the console scores the three against
`requestKeys.r1.choices` in the answer key. If the scoring panel shows them
unscored, the answer key is not loaded — load it under **Setup** rather than
grading them yourself.

The correct answers are q1 "one piece of work", q2 "the week of 6 July", q3
"a change to how day names are read when a slot is parsed".

**What happened:** a search change and a one-line day-parsing fix landed
together in a single commit whose message mentions only search, on 2026-07-10,
which is the Friday of the week beginning 6 July.

| Project | git commit | sgt commit |
|---|---|---|
| coursecraft | `9f5f7e5` | `079fa49` |
| confplan | `d0711a1` | `7ede859` |

The commit ids are here for your own orientation and for the interview. Do not
read them out; the participant is answering from a fixed option list and does
not need a commit id to answer.

### Requests 2 and 3: remove the waitlist, keep drops

Run the automated scorer rather than reading the code by hand:

```bash
python3 scripts/score_study_repo.py ~/study/p07/work \
    --baseline ~/repos/sgt-study/coursecraft \
    --expect-removed waitlist,promotion,notify \
    --expect-gone waitlist,notices
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
still passing and the app still starting (1), and dropping working again with no
promotion (1).

### Requests that no longer exist

Three requests were cut on 2026-08-17 because pilots ran out of time on all
three in both conditions: a back-to-back regression request against episode 17,
a build-two-alternatives request, and a history-surgery request. If you have an
older copy of this page or an older task sheet, it has scoring guidance for
requests 4, 5, and 6. Do not use it. `protocol.md` §2 has the reasoning and what
the cut costs.

Episode 17's regression is still in both repositories. Nothing in the session
asks anyone to repair it, so expect it to be there, untouched, at the end.

## After each half

Three questionnaires, all administered by the console, immediately after the
participant finishes: **workload** (NASA-TLX), **usability** (UMUX-Lite, two
items), and **the history** (the fourteen HLAC items, then two questions about
the requests themselves). Six minutes in total. Nothing here is scored by you.

Two things to leave alone while they answer. The workload scales are clicked on
a line of tick marks with no number anywhere, and one of the six runs the other
way from the rest — "Failure" on the left, "Perfect" on the right. That is the
published instrument, not a bug, and it is marked on the page. If someone asks,
point at the two words at the ends of the line and say nothing else; telling
them which end is the good one is telling them what to answer.

The two questions at the end — whether the requests felt realistic, and how much
time pressure they felt — are checks on our design, not on the setups. They are
the only place the study can find out whether the time caps bit harder in one
half than the other, so it matters that the answer is theirs. Do not apologize
for the clock before they answer it.

There used to be a five-question quiz and a three-minute spoken summary here as
well. Both were removed on 2026-08-17: they cost twelve minutes a session and
asked for written recall immediately after a block the participant had usually
just run out of time on, and the two coders differed on the results more than
the two conditions did. See `protocol.md` §5.6.

## At the end of the session

- The console administers the comparison block: seven comparisons over jobs they
  actually did, why, two "where would each earn its keep" scenarios, an overall
  comparison, and what would put them off. Each comparison offers five options —
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
- One row per request.
- The coded action stream.

See `protocol.md` section 7 for the statistical models and
`running-the-study.md` section 5 for the export buttons.

For each participant per half, you should have:

- Request 1: how many of the three closed questions were right, out of 3, and
  the confidence rating beside them.
- Scorer output for requests 2 and 3, including which of the four outcomes, and
  the rubric points that follow from it.
- Time per request, and whether the cap was hit.
- Workload, usability, and history scores.
- Your qualitative notes from "what to watch for" above.

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
  time, restore from a fresh copy, move to the next request, and mark it as
  stopped by a tool failure.
- The `year` and `speaker` leftovers in the code are deliberate. If a
  participant asks, say the history will tell them.
- **Back-to-back enrollment is broken in both projects, and the participant's
  materials say it is not.** Episode 17 made boundary-touching slots count as a
  clash, and the request that used to repair it was cut. The project brief says
  "a section that ends at 10:30 and one that starts at 10:30 do not clash", and
  request 2 says back-to-back sections "are legal and must stay legal". Both are
  true of the test suite and neither is true of the running application, because
  `test_back_to_back_is_fine` checks a function the app stopped calling. A
  participant who tries it during request 2 will hit the contradiction. This
  needs a decision before participant 1 — repair episode 17 in the testbed, or
  reword the brief — and it is not the facilitator's to make mid-session. Until
  it is made, if it comes up, say the history will tell them.
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
