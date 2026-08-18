# The study, in one page

Start here. This document explains what the study is, how it works, and where
everything lives.

After this, read `running-the-study.md` for the step-by-step guide to running
sessions using the study website. `protocol.md` pins down every question, scale,
and figure. `participant-materials.md` has the facilitator's script and the
answer keys.

## What we are asking

Developers now describe their changes to an AI coding assistant in plain
sentences, but version control still records those changes as lines in files. We
built a tool called `sgt` (short for "semi-git") that records history as the
pieces of work a person meant to do — at the level of functions and classes,
not lines and diffs. The question is whether that actually helps.

We want to find out four things:

1. **Comprehension.** Do people answer questions about a project's history more
   accurately when they have sgt?
2. **History manipulation.** Do people handle history changes better — for
   example, removing one feature cleanly without breaking later work that
   depends on it?
3. **Mental model.** What understanding of the project do they end up with after
   working through it? This one is now asked by questionnaire and interview
   rather than by a recall test — see `protocol.md` §5.6 for why, and what that
   costs.
4. **AI collaboration.** Does sgt change how they work with the AI assistant?

We are not claiming sgt can do things git cannot. Both tools can handle all
three of the study's requests. We are asking how well people perform with each.

## How it works

This is a **within-subjects** study: every participant does both setups, so we
compare each person against themselves.

- **Two conditions.** Plain git (the control) and sgt (the treatment). Both
  halves include an AI coding assistant (Claude Code).
- **Two projects** of similar size and shape, so nobody sees the same project
  twice. The projects are small command-line apps — one for course registration,
  one for conference scheduling.
- **Counterbalanced order.** We vary which condition and which project each
  participant sees first, to control for learning and ordering effects.
- **Three requests per half, twenty minutes.** The participant works on a
  project they have never seen, as if the original maintainer has left and they
  are picking it up. Before the clock starts they read a page describing what
  the program does.
- **Twelve participants**, about two hours each, of which about 100 minutes is
  scheduled steps.

There were six requests per half and forty-five minutes until 2026-08-17. Pilots
ran out of time on three of them in both conditions, which is a floor rather
than a measurement, so those three were cut. `protocol.md` §2 records which
three, why, and what the study lost with them.

The two projects were built commit by commit over a scripted six-week history.
That history contains, on purpose: one commit doing two unrelated things, one
feature with later work piled on top, an experiment that was added and then
removed, and a refactor that quietly broke something. See `testbed-spec.md` for
how the projects were constructed.

## Who does what

There are three roles:

- **Participant.** Works through the three requests while thinking out loud.
  They see the handouts from `materials/`, rendered by the website.
- **Facilitator.** Sets up the session, keeps time, observes, scores, and
  conducts the debrief interview. Their guide is `participant-materials.md`.
- **Analyst.** Scores the recordings against a shared codebook. See the analysis
  section of `participant-materials.md`.

## The files

### Study documents

| File | What it contains |
|---|---|
| `README.md` | This page — start here |
| `protocol.md` | The full pre-registration: every question, scale, measure, and figure |
| `running-the-study.md` | Step-by-step guide for running sessions via the website |
| `participant-materials.md` | The facilitator's script and answer keys |
| `answer-key.json` | Ground-truth data loaded into the web console for scoring |
| `remote-setup.md` | How to run sessions on a participant's own laptop |
| `materials/` | Printed copies of what participants see: welcome, both practice sheets, both project briefs, both task sheets |
| `testbed-spec.md` | How the two study projects were built |
| `build-log-*.md` | Ground truth for each project's commit history |
| `pilot-01-findings.md` | Findings from the first pilot (sgt condition) |
| `pilot-02-findings.md` | Findings from the second pilot (git condition) |
| `pilot-03-findings.md` | Findings from the third pilot |
| `sgt-findings.md` | Running list of known tool problems discovered during testing |

### Code and scripts

The website is in `web/`. The participant's bundle (what gets installed on their
machine) is in `scripts/study-bundle/`. Other scripts:

| Script | What it does |
|---|---|
| `make-study-bundle.sh` | Builds one of the four bundles (2 conditions x 2 projects) |
| `make-practice-repo.sh` | Builds the throwaway warm-up repository for practice |
| `setup-study-session.sh` | Prepares one workspace on a machine you control |
| `score_study_repo.py` | Scores the removal request (checks whether the right code was removed) |
| `study-bundle/tests/test_telemetry.py` | End-to-end test for the recording pipeline |

## Current status

### Ready

- Both projects built and tested in both conditions. All tests pass.
- Setup and bundling pipeline, with the sgt tool version pinned and recorded,
  and both editors' extensions pinned to fixed versions.
- The closed-question key for request 1 and the rubrics for requests 2 and 3, in
  `answer-key.json`.
- All handouts, this guide, and both task sheets.
- The warm-up repository, rebuilt: sixteen commits over four modules, four
  named features, and a build-time check on every handle the practice sheets
  quote.
- Three pilots run (two sgt, one git), which found twelve tool defects and four
  study-design problems. All the defects are fixed.
- The website: consent flow, background questionnaire, the project brief, the
  practice sheets, per-request timing with an explicit pause, the three
  post-half questionnaires (NASA-TLX, UMUX-Lite, and the history and agent
  block), the end-of-session comparison block, live session monitoring, the
  scoring interface, and the three paper figures with SVG export.
- The participant bundle: one-command setup, an assistant profile isolated from
  the participant's own account and billing, prompt and command recording from
  the terminal, the editor and the assistant alike, and an upload pipeline that
  cannot lose or double-count events.
- Tests: 81 in `web/` covering the Firestore security rules, the analysis
  pipeline, and the chart components — 28 of those need the Firestore emulator
  running and skip without it. The recording pipeline has its own end-to-end
  test in `scripts/study-bundle/tests/test_telemetry.py`, which also needs the
  emulator.

### Not ready (needed before participant 1)

- Ethics approval and pre-registration on OSF (Open Science Framework).
  `protocol.md` is the pre-registration text.
- Bundles built and deployed for the real cohort. `scripts/publish-study.sh`
  does the build, the deploy, and the check that the live site is serving what
  was just built, so this is one command rather than a series of uploads and
  pasted links.
- Session API keys issued with spend caps and entered into the console.
- A pilot with an actual person. All three pilots so far used AI agents, which
  are good at finding defects but tell you nothing about whether a human can
  finish in the time given.
- A pilot on the second project (confplan). Nobody has run it yet.
- A decision on the `Sgt-Op` commit trailers, which make plain git history
  harder to read in the sgt condition than in the git condition. See the end of
  `pilot-03-findings.md`. Whatever is decided has to be disclosed in the paper.

### Recently resolved

The tool limitation that threatened the fourth research question (RQ4: AI
collaboration) is fixed. sgt can now record a plan and check the agent's work
against it. It used to match none of the plan steps even when the work
implemented the plan exactly, which would have forced us to narrow the question.
It now matches every step of a plan built as stated. See finding O11 in
`pilot-01-findings.md`.
