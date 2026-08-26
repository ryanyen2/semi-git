# The study, in one page

Start here. This document explains what the study is, how it works, and where
everything lives.

After this, read `running-the-study.md` for the step-by-step guide to running
sessions using the study website. `protocol-v2.md` pins down every question,
scale, and figure; `protocol.md` is the superseded version 1 design, kept for
the record. `participant-materials.md` has the facilitator's script and the
answer keys.

## What we are asking

Developers now describe their changes to an AI coding assistant in plain
sentences, but version control still records those changes as lines in files. We
built a tool called `sgt` (short for "semi-git") that records history as the
pieces of work a person meant to do, at the level of functions and classes,
not lines and diffs. The question is whether that actually helps when someone
has to reverse what an agent did.

We want to find out three things (protocol v2):

1. **Read the record.** When an assistant has changed several files, do people
   know what they are recording when they save it?
2. **Locate.** Can people find the piece of work behind a described defect?
3. **Operate.** Can people take that work out and put it back at the level the
   task names, predicting what else it touches before they act?

We are not claiming sgt can do things git cannot. Both tools can handle every
stage in the session, and the paper says so. We are asking whether people
perform the same steps faster, more safely, or with better foresight under
each tool.

## How it works

This is a **within-subjects** study: every participant does both setups, so we
compare each person against themselves.

- **Two conditions.** Plain git (the control) and sgt (the treatment). There
  is no live AI assistant in the task block: the assistant's work is replayed
  from a recording, so every participant reads identical changes.
- **Two projects** of the same shape under different nouns, so nobody sees the
  same project twice: two small web dashboards over public sensor data
  (`bikecount` and `footfall`), harvested from real agent work.
- **Counterbalanced order.** We vary which condition and which project each
  participant sees first, to control for learning and ordering effects.
- **Four guided stages per half.** Each stage starts from a scripted state
  (`./stage N`), tells the participant exactly what happened, and asks for one
  thing: record the assistant's changes, find the work behind a wrong number,
  take it out, put it back. A short untimed quiz and three rating statements
  follow each stage.
- **A closing interview over the participant's own repository**, with the
  semantic view built for it during the session.
- **Twelve participants**, about 90 minutes each (`TOTAL_ESTIMATE_MIN` in
  `web/src/study/flow.ts`, which the printed sheets are generated from).

The two projects were built commit by commit over a scripted six-week history.
That history contains, on purpose: one commit doing two unrelated things, one
feature with later work piled on top, an experiment that was added and then
removed, and a refactor that quietly broke something. See `testbed-spec.md` for
how the projects were constructed.

## Who does what

There are three roles:

- **Participant.** Works through the four cards while thinking out loud.
  They see the handouts from `materials/`, rendered by the website.
- **Facilitator.** Sets up the session, keeps time, observes, scores, and
  conducts the debrief interview. Their guide is `participant-materials.md`.
- **Analyst.** Scores the recordings against a shared codebook. See the analysis
  section of `participant-materials.md`.

## The files

### Study documents

| File | What it contains |
|---|---|
| `README.md` | This page, start here |
| `protocol-v2.md` | The full protocol: every question, scale, measure, and figure |
| `protocol.md` | The superseded version 1 protocol (locate-and-reverse), kept for the record |
| `running-the-study.md` | Step-by-step guide for running sessions via the website |
| `participant-materials.md` | The facilitator's script and answer keys |
| `answer-key.json` | Ground-truth data loaded into the web console for scoring |
| `remote-setup.md` | How to run sessions on a participant's own laptop |
| `materials/` | Printed copies of what participants see: the welcome, and one practice sheet and one task sheet per condition-and-project pair (four of each, because both now quote that project's own files and that arm's own commands). Generated from `web/src/study/`, so edit the copy there and run `npm run gen:materials` in `web/`; `npm test` fails if a file here has drifted |
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
| `setup-study-session.sh` | Prepares one workspace on a machine you control |
| `score_study_repo.py` | Scores the removal cards (checks removal, collateral damage, and the behavioral probe via `--expect-behaviour`) |
| `study-bundle/tests/test_telemetry.py` | End-to-end test for the recording pipeline (needs the emulator) |
| `study-bundle/tests/test_shim.py` | Checks that each recorded event is attributed to whoever caused it |
| `study-bundle/tests/test_doctor.py` | Checks that the setup check runs the session's environment, not the machine's |
| `study/measure_reach_key.py` | Measures the answer for the prediction trial by running the behaviours |

## Current status

### Ready

- Both projects built and tested in both conditions. All tests pass.
- Setup and bundling pipeline, with the sgt tool version pinned and recorded,
  and both editors' extensions pinned to fixed versions.
- The locate answers for the defect card and the rubrics for the removal cards,
  in `answer-key.json`.
- All handouts, this guide, and both task sheets.
- The warm-up repository, rebuilt: sixteen commits over four modules, four
  named features, and a build-time check on every handle the practice sheets
  quote.
- Three pilots run (two sgt, one git), which found twelve tool defects and four
  study-design problems. All the defects are fixed.
- The website: consent flow, background questionnaire, the project brief, the
  practice sheets, four task cards per half with per-card timing (observation,
  locate, reversal with a prediction trial, staged removal), the three
  post-half questionnaires (NASA-TLX, UMUX-Lite, and the twelve-item HLAC
  block), the end-of-session comparison block, live session monitoring, the
  scoring interface, and the three paper figures with SVG export.
- The participant bundle: one-command setup, an assistant profile isolated from
  the participant's own account and billing, prompt and command recording from
  the terminal, the editor and the assistant alike, and an upload pipeline that
  cannot lose or double-count events.
- Tests in `web/` covering the Firestore security rules, the analysis pipeline,
  the chart components, and the schedule the welcome page promises. 28 of those
  are the security rules and need the Firestore emulator. Without it they skip,
  but the run still comes back red, so a red `npm test` is worth reading before
  assuming something broke. `web/README.md` says how to start the emulator,
  including the part where Homebrew's Java is not on your PATH.
- The bundle has three test files of its own, all in
  `scripts/study-bundle/tests/`: 36 checks on the recording pipeline end to end
  (needs the emulator), 13 on event attribution, and 8 on the setup check.

### Not ready (needed before participant 1)

- Ethics approval and registration on OSF (Open Science Framework).
  `protocol.md` is the registration text.
- Bundles built and deployed for the real cohort. `scripts/publish-study.sh`
  does the build, the deploy, and the check that the live site is serving what
  was just built, so this is one command rather than a series of uploads and
  pasted links.
- Session API keys issued with spend caps and entered into the console.
- A pilot with an actual person. All three pilots so far used AI agents, which
  are good at finding defects but tell you nothing about whether a human can
  finish in the time given.
- A pilot on the second project (confplan). Nobody has run it yet.

## Other files in this directory

- `remote-setup.md` -- how to set up a participant's laptop, API keys, and
  Claude Code.
- `testbed-spec.md` -- how the two study projects were built.
- `build-log-*.md` -- the ground truth for each project's history.
- `pilot-01-findings.md`, `pilot-02-findings.md`, `pilot-03-findings.md` --
  what the pilot sessions found.
- `sgt-findings.md` -- the running list of known sgt problems discovered during
  the study.
