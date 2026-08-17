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

Participant handouts live in `materials/`. The website renders the same text.

## Quick overview

- Each participant completes two halves: one with git and one with sgt, on two
  different projects.
- Each half has the same six requests, worded for someone who has never seen
  the project before.
- You are testing the two setups, not the person. Say this out loud, and say it
  often.
- A session takes about two hours per participant.

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
| 10 | Practice sheet and first setup |
| 45 | First half: six requests |
| 8 | Workload questionnaire, quiz, spoken summary |
| 10 | Practice sheet and second setup |
| 45 | Second half: six requests on the other project |
| 8 | Workload questionnaire, quiz, spoken summary |
| 15 | Usability questionnaire, preference ranking, interview |

## What to say to the participant

- Call them "the first setup" and "the second setup". Never say "ours" or
  imply that one is better.
- "We are testing the setups, not you."
- "Keep talking. Tell me what you expect before you run it."
- When they stall: "That's useful, tell me what you're thinking." Do not help
  unless something is actually broken.
- Call the time at the halfway mark and again at two minutes left.

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

Two points. One for identifying the correct commit, one for noticing it
contains two unrelated pieces of work. Record the participant's stated
confidence.

**Answer:** a search change and a one-line day-parsing fix landed together in
a single commit whose message mentions only search.

| Project | git commit | sgt commit |
|---|---|---|
| coursecraft | `9f5f7e5` | `079fa49` |
| confplan | `d0711a1` | `7ede859` |

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

### Request 4: back-to-back enrollment

Two points. One for finding the commit that broke it, one for a fix that
restores back-to-back enrollment while keeping the room audit working.

| Project | git commit(s) | sgt commit(s) |
|---|---|---|
| coursecraft | `5762524` | `25e91a9` |
| confplan | `821f9d4`, then `8049f48` | `704e7a4`, then `6ca9a53` |

**Expect this:** `test_back_to_back_is_fine` checks a function the app stopped
calling, so it stays green throughout the outage. Both pilot participants found
this. Note whether yours does too.

### Request 5: two ways to swap

Score three things:

- One working version is in the final code, and the other is gone.
- The participant can explain why they kept the one they kept.
- Note how they kept the two attempts apart, and how they threw one away.

### Request 6: split the tangled change

This is finishable in both conditions. Score three things:

- Are the two pieces of work now in separate units?
- Does each have a name that says what it is?
- Is the current code unchanged? A tree-hash comparison or an empty diff
  proves it.

In the sgt condition, the split usually already exists before they start, and
nothing renames a checkpoint yet, so they may not finish the naming half.

## After each half

Run these three activities immediately after the participant finishes:

1. **Workload questionnaire** (NASA-TLX, administered by the console).
2. **Two-minute quiz** (project closed, from memory):
   - Which feature was added and then deliberately removed? *Answer: the
     priority experiment.*
   - Which came first: conflict detection, capacity limits, or the waitlist?
     *Answer: capacity limits.*
   - Did the previous maintainer work alone? *Answer: no, an AI assistant
     helped.*
3. **Three-minute spoken summary.** Ask: "Tell me the story of this project
   without looking at it. What was built, what went wrong, what was undone."
   Score their answer against the episode list in `testbed-spec.md`.

## At the end of the session

- Administer the usability questionnaire (SUS) for each setup.
- Ask which setup they would prefer for which kind of request, and why.
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

- Request 1 and 4 scores (out of 2), with stated confidence.
- Scorer output for requests 2 and 3, including which of the four outcomes.
- Time or number of attempts per request.
- Workload and usability scores.
- Quiz answers and the summary recording.
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
- The git copies were cleaned so nothing in them mentions sgt. The sgt copies
  keep their own commits, which is correct. See `pilot-02-findings.md` for
  why.

## Other files in this directory

- `README.md` — the one-page overview. Give this to a new experimenter first.
- `remote-setup.md` — how to set up a participant's laptop, API keys, and
  Claude Code.
- `testbed-spec.md` — how the two study projects were built.
- `build-log-*.md` — the ground truth for each project's history.
- `pilot-01-findings.md` and `pilot-02-findings.md` — what the pilot sessions
  found.
- `sgt-findings.md` — the running list of known sgt problems discovered during
  the study.
