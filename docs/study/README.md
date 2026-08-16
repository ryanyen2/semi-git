# The study, in one page

Read this first. Then `running-the-study.md`, which is the operator's manual for
the website the study now runs on. `protocol.md` fixes every question, scale and
figure; `participant-materials.md` keeps the facilitator's script and the answer
keys.

## What we are asking

Developers now describe changes to an AI assistant in sentences, but version
control still records them as lines in files. We built a tool, `sgt`, that
records history as the pieces of work a person meant to do. The question is
whether that helps.

Four things we want to know:

- Do people answer questions about a project's history better?
- Do people change history better, e.g., removing one feature without breaking
  what came after?
- What understanding of the project do they end up with?
- Does it change how they work with the AI assistant?

We are not claiming sgt does anything git cannot. Both can do all six requests.
We are asking how well people do with each.

## How it works

- Two setups: plain git, and sgt. Both halves have an AI assistant.
- Two projects of similar size and shape, so nobody sees the same one twice.
- Every participant does both setups, in an order we vary.
- Six requests per half, on a project they have never seen, written up by a
  maintainer who has left.
- Twelve participants, about two hours each.

The projects are small command line apps for course registration and conference
scheduling. They were built commit by commit over a scripted six week history
that contains, on purpose: one commit doing two unrelated things, one feature
with later work piled on top of it, an experiment that was added and then
removed, and a refactor that quietly broke something.

## Who does what

- **Participant.** Works the six requests, talks out loud. Gets `materials/`.
- **Facilitator.** Sets up, keeps time, watches, scores, interviews. Gets
  `participant-materials.md`.
- **Analyst.** Scores the recordings against a shared codebook. See the analysis
  section of `participant-materials.md`.

## The files

| File | For |
|---|---|
| `README.md` | This page |
| `protocol.md` | Every question, scale, measure and figure, fixed |
| `running-the-study.md` | The operator's manual for the website |
| `participant-materials.md` | Facilitator's script and the answer keys |
| `answer-key.json` | Loaded into the console; ground truth for scoring |
| `remote-setup.md` | Their laptop, keys, Claude Code |
| `materials/` | The handouts, also rendered by the website |
| `testbed-spec.md` | How the two projects were built |
| `build-log-*.md` | Ground truth for each project |
| `pilot-01-findings.md` | First pilot, sgt half |
| `pilot-02-findings.md` | Second pilot, git half |
| `sgt-findings.md` | Running list of known tool problems |

The website is in `web/`. The participant's bundle is in
`scripts/study-bundle/`. Scripts live in `scripts/`:

| Script | Does |
|---|---|
| `make-study-bundle.sh` | Builds one of the four bundles |
| `make-practice-repo.sh` | Builds the throwaway warm-up repository |
| `setup-study-session.sh` | Prepares one workspace on a machine you control |
| `score_study_repo.py` | Scores the removal request |
| `study-bundle/tests/test_telemetry.py` | Checks the recording path end to end |

## State of play

Ready:

- Both projects, in both conditions, tests passing.
- Setup and bundling, with the tool build pinned and recorded.
- Scoring for requests 1, 2, 3 and 4.
- Handouts, this guide, and the confplan task sheet.
- Two pilots run, one per setup, which found twelve tool defects and four
  problems in the study design. All are fixed.
- The website: consent, background, per-request timing, all four
  questionnaires, the quiz, the summary task, live monitoring, scoring, and the
  three paper figures with SVG export.
- The participant bundle: one command to set up, an assistant profile isolated
  from the participant's own account and billing, prompt and command recording,
  and an upload path that cannot lose or double-count anything.
- Tests: 57 in `web/` covering the security rules, the analysis and the
  figures, and 29 covering the recording path end to end.

Not ready, and needed before participant 1:

- Ethics approval, and pre-registration on OSF. `protocol.md` is the
  pre-registration text.
- Bundles built and uploaded, and their links pasted into the console.
- Session API keys issued, capped, and entered into the console.
- A pilot with an actual person. Both pilots so far were AI agents, which find
  defects well and tell you nothing about whether a human finishes in the time
  given.
- A pilot on the second project. Nobody has run confplan.

The tool limit that threatened the fourth question is fixed. sgt can record a
plan and check work against it, and it used to match none of the steps for work
that implemented the plan exactly, which would have forced the question to be
narrowed. It now matches every step of a plan built as stated. See O11 in
`pilot-01-findings.md`.
