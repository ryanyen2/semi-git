# Running a session

Facilitator's copy. Has the answers in it, so don't screen share it.

The study now runs from a website, and the parts of this page that were about
mechanics have moved: `running-the-study.md` is the operator's manual, and
`protocol.md` fixes every question and measure. What is still here is what the
website cannot do for you: what to say, what to watch for, and what the right
answers are.

The console does the rest. It holds the clocks, records the answers, applies the
rubrics you see below, checks the summary against the episode list, and keeps
the interview notes. The answer keys below are also in
`docs/study/answer-key.json`, which is loaded into the console once so the right
answer appears beside each request while you score it.

Participant handouts are in `materials/`, and the website renders the same text.

## The short version

- Each participant does two halves, one with git and one with sgt, on two
  different projects.
- Each half is the same six requests, worded for someone who has never seen the
  project.
- You are testing the two setups, not the person. Say so, often.
- About two hours per participant.

## Before they arrive

Normally: send their link a day early and let the website walk them through
consent, background and setup. See `running-the-study.md` §2.

On a machine you control, for an in-person session:

```bash
scripts/setup-study-session.sh p07 sgt coursecraft
```

The script makes a fresh copy, builds the test environment, refuses to hand over
a copy whose tests don't pass, and for sgt installs the exact build we are
testing and refreshes its history view. Never reuse a copy between participants.

Also check:

- Screen and audio recording works.
- The `.env` file is in `work/` for the sgt half. Plain English commands need it.
- Claude Code is logged in. See `remote-setup.md`.

## Which condition, which project

Twelve participants, four groups of three.

| Group | First half | Second half |
|---|---|---|
| 1 | git, coursecraft | sgt, confplan |
| 2 | sgt, coursecraft | git, confplan |
| 3 | git, confplan | sgt, coursecraft |
| 4 | sgt, confplan | git, coursecraft |

## Session timing

| Minutes | What |
|---|---|
| 10 | Consent, background questions |
| 10 | Practice sheet, first setup |
| 45 | First half, six requests |
| 8 | Workload questionnaire, quiz, spoken summary |
| 10 | Practice sheet, second setup |
| 45 | Second half, other project |
| 8 | Questionnaire, quiz, summary again |
| 15 | Usability questionnaire, preference, interview |

## What to say

- Call them "the first setup" and "the second setup". Never say ours.
- "We are testing the setups, not you."
- "Keep talking. Tell me what you expect before you run it."
- When they stall: "That's useful, tell me what you're thinking." Don't help
  unless something is broken.
- Call the time at halfway and at two minutes left.

## What to watch for

Write these down as they happen. This is the qualitative data.

- The moment they stop trusting a number or message the tool printed.
- Any command they run twice because the first result made no sense.
- Whether they check their work, and how. Tests, running the program, or neither.
- What they hand to the assistant, and what they insist on doing themselves.
- Where they say "I don't know what that means."
- Any point where they give up on a tool feature and do it by hand.

## Scoring

### Request 1, what changed course search

Two points. One for naming the right commit, one for seeing it holds two
unrelated pieces of work. Record their confidence.

Answer: search and a one line day parsing fix landed together, in a commit whose
message mentions only search.

| Project | git | sgt |
|---|---|---|
| coursecraft | `9f5f7e5` | `079fa49` |
| confplan | `d0711a1` | `7ede859` |

### Requests 2 and 3, remove the waitlist, keep drops

Run the scorer rather than reading the code:

```bash
python3 scripts/score_study_repo.py ~/study/p07/work \
    --baseline ~/repos/sgt-study/coursecraft \
    --expect-removed waitlist,promotion,notify \
    --expect-gone waitlist,notices
```

Record which of four happened:

- Waitlist gone, everything else passes. The target.
- Something else broke. Count the features. This is collateral damage.
- Waitlist still there. The removal didn't happen.
- Tests pass but the program won't start. Record separately.

The last one is why the scorer starts the program. In pilot 1 the participant
finished with 29 passing tests and an application that raised an error on
startup, because no test in the suite builds the command line parser.

### Request 4, back to back enrollment

Two points. One for finding the commit, one for a fix that restores back to back
enrollment and leaves the room audit working.

| Project | git | sgt |
|---|---|---|
| coursecraft | `5762524` | `25e91a9` |
| confplan | `821f9d4`, then `8049f48` | `704e7a4`, then `6ca9a53` |

Expect this: `test_back_to_back_is_fine` checks a function the app stopped
calling, so it stays green through the outage. Both pilot participants found it.
Note whether yours does.

### Request 5, two ways to swap

- One working version in the final code, the other gone.
- They can say why they kept it.
- Note how they kept the attempts apart, and how they threw one away.

### Request 6, split the tangled change

Finishable in both setups. Score three things:

- Are the two pieces of work now separate?
- Does each have a name that says what it is?
- Is the current code unchanged? A tree hash comparison or an empty diff proves
  it.

In sgt, the split usually already exists before they start, and nothing renames
a checkpoint yet, so they may not finish the naming half.

## After each half

- Workload questionnaire.
- Two minute quiz, project closed:
  - Which feature was added and then deliberately removed? The priority
    experiment.
  - Which came first: conflict detection, capacity limits, the waitlist?
    Capacity limits.
  - Did the previous maintainer work alone? No, an AI assistant helped.
- Three minute spoken summary. "Tell me the story of this project without
  looking at it. What was built, what went wrong, what was undone." Score
  against the episode list in `testbed-spec.md`.

## At the end

- Usability questionnaire for each setup.
- Which setup for which kind of request, and why.
- Interview prompts:
  - What did you trust, and what did you check?
  - Where were you lost?
  - What did the history hide, and what did it show?
  - What did you wish you could ask the history? Ask this before they compare
    the setups. Both pilot participants answered with something close to what
    sgt does, one of them from inside the git half.
- Collect the assistant transcript paths.
- Revoke the API key if you issued one. See `remote-setup.md`.

## Analysis

The console collects all of this and exports it. **Results → Compute from data**
builds the analysis from the raw event stream and gives you the three figures
plus three CSVs: one row per participant per condition for the mixed models, one
row per request, and the coded action stream. See `protocol.md` §7 for the models
and `running-the-study.md` §5 for the buttons.

Per participant, per half, you should have:

- Request 1 and 4 scores out of 2, with confidence.
- Scorer output for requests 2 and 3, including which of the four outcomes.
- Time or attempts used per request.
- Workload and usability scores.
- Quiz answers and the summary recording.
- Your notes from "what to watch for".

Then:

- Compare within participants, not between them. Everyone does both setups.
- Report effect sizes and confidence intervals. Twelve people cannot support
  claims about small differences, so say that rather than reaching for a p value.
- Pair every number with the recording that explains it. A time difference means
  nothing until you can point at what the person was doing.
- Code the recordings with two people and agree a codebook.

## Notes

- If a copy gets into a state they can't get out of, note the time, restore from
  a fresh copy, move to the next request, and mark it stopped by a tool failure.
- The `year` and `speaker` leftovers in the code are deliberate. If asked, say
  the history will tell them.
- The git copies were cleaned so nothing in them mentions sgt. The sgt copies
  keep their own commits, which is correct. See `pilot-02-findings.md`.

## Other files

- `README.md` is the one page overview. Give it to a new experimenter first.
- `remote-setup.md` covers their laptop, API keys, and Claude Code.
- `testbed-spec.md` says how the projects were built.
- `build-log-*.md` are the ground truth for each project.
- `pilot-01-findings.md` and `pilot-02-findings.md` are what the pilots found.
- `sgt-findings.md` is the running list of known problems.
