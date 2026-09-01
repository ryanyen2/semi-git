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

1. **Orient.** Arriving at a project nobody has seen before, can people say
   which piece of work in its history put which part of the product there?
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

- **Two conditions.** Plain git (the control) and sgt (the treatment). There is
  no live AI assistant in the task block, and nothing is replayed into the
  working tree: every stage starts from a committed state a script resets to, so
  every participant reads the same history. The history itself is agent work --
  both testbeds were harvested from recorded agent sessions.
- **Two projects** of the same shape under different nouns, so nobody sees the
  same project twice: two small web dashboards over public sensor data
  (`bikecount` and `footfall`), harvested from real agent work.
- **Counterbalanced order.** We vary which condition and which project each
  participant sees first, to control for learning and ordering effects.
- **Four guided stages per half.** Each stage starts from a scripted state
  (`./stage N`), tells the participant exactly what happened, and asks for one
  thing: get to know the project, find the work behind a wrong number, take it
  out, put it back. A short untimed quiz and two or three rating statements
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
| `interview-demo-easyocr.md` | The prepared repository for the closing interview, its walkthrough, and its pre-flight checklist |
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
| `study/prep-stages.sh` | Builds the scripted stage states (the `study/*` tags) into both projects |
| `study/capture-page-shots.mjs` | Regenerates the dashboard screenshots the stage cards and the practice sheets show, from a built repo |
| `study/harvest/write_answer_key.py` | Generates `answer-key.json` by measuring the two targets on a built bundle |
| `study/score_dashboard.py` | Scores one participant's repo by rendering every page against a golden snapshot |
| `check_graph_integrity.py` | Refuses to ship a bundle whose feature graph has lost symbols |
| `publish-study.sh` | Builds the four bundles and the site, deploys, and checks the live site serves what was just built |

## Current status

### Ready

- Both projects built, bundled and rehearsed in both conditions.
- The four stages of protocol v2, on the website and on the printed sheets: get
  to know the project, find the work behind a wrong number, take it out, put it
  back. Each stage carries cropped screenshots of that project's own dashboard,
  regenerated from the shipped bundle by `study/capture-page-shots.mjs`.
- Both scored checklists measured rather than written: `write_answer_key.py`
  removes each target on a copy, runs the app's own check, re-renders every page,
  and maps what moved onto the eleven options the checklist offers. It refuses to
  write a key for a target whose removal kills the app.
- The stage and check scripts (`./stage 0..4`, `./check 3`, `./check 4`), which
  reset every stage's state and print the same words in both arms.
- Setup and bundling pipeline, with the sgt version and commit recorded in each
  bundle and both editors' extensions pinned.
- Instruments: consent, the pre-study questionnaire, the per-stage ratings and
  confidence, UMUX-Lite and raw NASA-TLX per half, the closing comparison block.
- The experimenter console: live session monitoring, key upload with validation,
  per-participant scoring, and the paper figures with SVG export.
- The participant bundle: one-command setup, an editor profile isolated from the
  participant's own account, command recording from the terminal and the editor,
  and an upload pipeline that cannot lose or double-count events.
- Three agent pilots and one full author walkthrough of all four arms, which
  found the defects listed in `sgt-findings.md`. The ones on a participant's path
  are fixed.
- Tests in `web/` covering the Firestore security rules, the analysis pipeline,
  the chart components, the shipped answer key against the questions actually
  asked, and the schedule the welcome page promises. 28 of those are the security
  rules and need the Firestore emulator. Without it they skip, but the run still
  comes back red, so a red `npm test` is worth reading before assuming something
  broke. `web/README.md` says how to start the emulator, including the part where
  Homebrew's Java is not on your PATH.
- The bundle has three test files of its own, all in
  `scripts/study-bundle/tests/`: checks on the recording pipeline end to end
  (needs the emulator), on event attribution, and on the setup check.

### Not ready (needed before participant 1)

- Ethics approval and registration on OSF (Open Science Framework).
  `protocol-v2.md` is the registration text.
- Session API keys issued with spend caps and entered into the console.
- A pilot with an actual person. Every pilot so far was an AI agent or the
  study's own author, which finds defects but says nothing about whether a human
  finishes in the time given.

## Other files in this directory

- `remote-setup.md` -- how to set up a participant's laptop, API keys, and
  Claude Code.
- `testbed-spec.md` -- how the two study projects were built.
- `build-log-*.md` -- the ground truth for each project's history.
- `pilot-01-findings.md`, `pilot-02-findings.md`, `pilot-03-findings.md` --
  what the pilot sessions found.
- `sgt-findings.md` -- the running list of known sgt problems discovered during
  the study.
