# The study, in one page

Read this first. Then `participant-materials.md` if you are running sessions,
`remote-setup.md` if the participant is on their own laptop.

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
| `participant-materials.md` | Running a session, scoring, analysis |
| `remote-setup.md` | Their laptop, API keys, Claude Code |
| `materials/` | The handouts participants read |
| `testbed-spec.md` | How the two projects were built |
| `build-log-*.md` | Ground truth for each project |
| `pilot-01-findings.md` | First pilot, sgt half |
| `pilot-02-findings.md` | Second pilot, git half |
| `sgt-findings.md` | Running list of known tool problems |

Scripts live in `scripts/` at the repository root:

| Script | Does |
|---|---|
| `setup-study-session.sh` | Prepares one workspace on a machine you control |
| `make-study-bundle.sh` | Builds a bundle for a remote participant |
| `score_study_repo.py` | Scores the removal request |

## State of play

Ready:

- Both projects, in both conditions, tests passing.
- Setup and bundling, with the tool build pinned and recorded.
- Scoring for requests 1, 2, 3 and 4.
- Handouts and this guide.
- Two pilots run, one per setup, which found twelve tool defects and four
  problems in the study design. All are fixed.

Not ready, and needed before participant 1:

- Consent form, background questionnaire, workload and usability
  questionnaires. Described, not built.
- Ethics approval, and pre-registration of the questions and measures.
- A pilot with an actual person. Both pilots so far were AI agents, which find
  defects well and tell you nothing about whether a human finishes in the time
  given.
- A pilot on the second project. Nobody has run confplan.

One known tool limit to decide about: sgt can record a plan and check work
against it, which is how we planned to study the fourth question. It currently
matches none of the steps for work that implements the plan exactly. Either that
improves, or the fourth question gets narrowed and the paper says so.
