# Study testbed: two codebases, one episode script

Date: 2026-08-09
Status: Build blueprint for the CHI study testbed repositories.
Related: `docs/design/2026-08-09-chi-user-study-design.md`

## 1. The two applications

Both task sets use a scheduling application. Scheduling was chosen because every
computer science student knows the domain from registering for classes, the
logic interlocks naturally (conflicts depend on the time model, promotion
depends on capacity and conflicts, exports depend on everything), and the domain
avoids the "banned toy" class of applications (to-do lists, note-taking apps,
ledgers).

| | Task set A | Task set B |
|---|---|---|
| Name | coursecraft | confplan |
| Domain | Course registration for a small department | Program planning for a two-day conference |
| Location | `~/repos/sgt-study/coursecraft` | `~/repos/sgt-study/confplan` |

Both applications share the same technology stack: Python 3.11, standard-library-only
application code, JSON file persistence, and an argparse CLI (command-line
interface). pytest is the only development dependency, and each repository ships
a uv-managed `.venv` (virtual environment). Feature-tagged pytest markers drive
the scoring script. Every repository starts from an E0 seed commit (just a
README and `.gitignore`) so that sgt has a root commit to bind to.

### Isomorphism map

The two projects are **isomorphic** — structurally identical, with the domain
nouns swapped. Every noun in project A has exactly one counterpart in project B,
and the episode scripts follow the same sequence of shapes with those nouns
swapped.

| coursecraft | confplan |
|---|---|
| course | talk |
| section | session |
| instructor | speaker |
| student | attendee |
| enroll | register |
| prerequisite | series dependency (part 2 requires part 1) |
| semester week grid | two-day slot grid |
| department | track |
| timetable export | program export |

**Why an LLM cannot simply regenerate these from scratch.** The applications
themselves are ordinary. What cannot be regenerated is the accumulated
behavioral contract: the acceptance test suite pins down idiosyncratic decisions
made across episodes. For example, waitlist promotion order breaks ties by join
time then by student ID, back-to-back time slots do not count as conflicts, and
exports have exact snapshot formats. A from-scratch rewrite would fail the test
suite. The study tasks operate on the *history* of the project, and that history
only exists in the built repository.

## 2. The episode script

There are 22 episodes, following identical shapes in both repositories. "Human"
episodes are saved with terse commit messages and no plan. "Agent" episodes run
the plan loop (plan intake, edits, checkpoint, save) with fuller messages, which
is realistic for a repository whose previous maintainer worked with an AI coding
assistant. This exercises both save paths.

### Required structural landmarks

The episode script must produce certain structural patterns because the study
tasks depend on them:

- **One tangled save** mixing an unrelated fix into a feature (episode 8) — the
  provenance target for request 1.
- **One feature with a dependency chain** built on top of it (episode 11:
  waitlist, with episodes 12, 14, 15-partial, and 21 depending on it) — the
  removal target for request 2.
- **One clean fix** (episode 13) and **one co-saved feature** (episode 15)
  interleaved with the chain — these must survive the removal.
- **One abandoned experiment** removed with a real revert (episode 16) — so the
  history itself contains a revert.
- **One refactor that silently changes behavior** (episode 17) — the regression
  target for request 4.

### Full episode list

| # | Author | Shape | coursecraft version | confplan version |
|---|---|---|---|---|
| E1 | Agent | Scaffold | Models (Course, Section, Student), JSON store, CLI skeleton | Models (Talk, Session, Attendee), JSON store, CLI skeleton |
| E2 | Agent | Model feature | Rooms and weekly time-slot grid | Rooms and two-day slot grid |
| E3 | Human | Feature | Add and list courses and sections | Add and list talks and sessions |
| E4 | Agent | Feature | Student enrollment command | Attendee registration command |
| E5 | Human | Bug fix | ID reuse after delete corrupts the data store | Same shape |
| E6 | Human | Feature | Section capacity limits | Session capacity limits |
| E7 | Agent | Feature | Time-conflict detection on enrollment | Clash detection on registration |
| E8 | Human | Tangled save | Course search and filtering, plus an unrelated date-parsing fix in the same save | Talk search and filtering, plus the same unrelated fix |
| E9 | Agent | Feature | Prerequisite enforcement | Series-dependency enforcement |
| E10 | Agent | Refactor | Extract storage into a repository class | Same shape |
| E11 | Agent | Feature F | Waitlist: join when section is full | Waitlist: join when session is full |
| E12 | Agent | Depends on F | Auto-promotion when a seat frees up | Same shape |
| E13 | Human | Interleaved fix (keeper) | Back-to-back slots wrongly flagged as conflicts | Adjacent slots wrongly flagged as clashes |
| E14 | Agent | Touches F | Drop command, with cascade that triggers promotion | Unregister command, same cascade |
| E15 | Human | Tangled keeper | Timetable export (markdown and CSV), co-saved with a small waitlist message fix | Program export, same shape |
| E16 | Agent | Abandoned experiment | Priority enrollment for seniors, then `sgt revert` of it | Speaker-priority registration, then revert |
| E17 | Agent | Regression refactor | Normalize slot comparison for cross-listed sections; silently makes boundary-touching slots count as overlapping | Normalize slot comparison across rooms; same silent behavior change |
| E18 | Agent | Feature | Instructor schedule view | Speaker schedule view |
| E19 | Human | Feature | Enrollment statistics report | Registration statistics report |
| E20 | Human | Bug fix | Export escaping bug | Same shape |
| E21 | Agent | Depends on F | Waitlist notification digest built on promotion events | Same shape |
| E22 | Human | Polish | README and CLI help text | Same shape |

Every episode ends with all existing tests passing. The acceptance test suite
grows alongside the episodes, and each test carries a feature tag (a pytest
marker), so removal tasks can be scored automatically: after removing feature F,
tests tagged `waitlist`, `promotion`, and `notify` are expected to fail or be
absent, and every other tag must still pass.

## 3. Study tasks and what commands they exercise

There are four scored tasks per block, plus one stretch task if time allows.
Each block takes about 45 minutes. The participant-facing wording lives in the
task sheets (a separate document); the descriptions below are internal
shorthand. No task names a specific git or sgt command.

| Task | Time cap | What it targets | How the git condition approaches it | How the sgt condition approaches it |
|---|---|---|---|---|
| S1: Provenance | 7 min | Episode 8's tangle: "search behavior changed around when date entry got lenient; what work changed search and what else came along?" | `git log`, `git blame`, diff reading | `sgt show` (accepts the save ID that `sgt log` prints), `sgt log`, `sgt why` |
| S2: Entangled removal | 15 min | Remove the waitlist (episode 11) and everything built on it; keep the episode 13 fix, the episode 15 export, and everything else passing | Revert or rebase across interleaved commits | `sgt show f-waitlist` (shows impact count), then `sgt revert` |
| S3: Selective restore | (within S2 cap) | Bring back the plain join-waitlist functionality without promotion or notifications | Cherry-pick archaeology | `sgt restore <selection>` |
| S4: Regression repair | 10 min | Episode 17 made back-to-back sections conflict; restore the old boundary behavior while keeping the rest of the refactor | `git bisect` or log reading, then a manual partial revert | `sgt intent` rewind, `sgt revert f@n`, or symbol-level restore with `at` |
| S5: Plan and fork | 12 min | Add "swap section" two ways (transactional swap vs. drop-then-enroll with a hold); try both, keep one | Git branches | Agent plan loop plus `sgt session start/land`, `sgt resolve` if versions compete |
| S6: Stretch — history edit | If time | Split the episode 8 tangle into two cleanly named pieces | Interactive rebase | `sgt feature regroup split`, `sgt feature rename` |

**Coverage check against the study goals:** planning (S5 plan loop), reverting
(S2, plus episode 16 inside the history), restoring (S3, S4), forking (S5
sessions/resolve), and editing history (S6). Comprehension (S1) carries RQ1.

## 4. Construction rules

- **Build in episode order.** No retrofitting, because sgt's graph must record
  the real sequence of development.
- **Agent episodes use Claude Code** driving real sgt plan/save flows in the
  repository. Human episodes are plain edits plus `sgt save -m` with a terse
  message.
- **Save messages are written before building** (in the answer key), in each
  author's voice. They become the recorded intents, and request 1's
  answerability depends on them.
- **The two repositories are built independently** from the shared episode
  shapes, not text-transformed from each other, so the code differs naturally
  the way two real projects would.
- Each repository gets a gitignored `.env` holding the OpenAI API key for sgt's
  labeling, with mode 600, never committed, never echoed.
- **After each episode:** run the test suite, then `sgt log --summary --json` to
  confirm the episode landed as one coherent unit. Deviations are fixed before
  the next episode, because later episodes depend on earlier grouping.
- **Tag every test** with its feature marker as it is written. The scoring
  script reads markers, runs the suite, and prints counts of kept, removed, and
  collateral tests.
- **Freeze at the end:** tag `study-start` in git, archive a tarball of each
  repository including the `.sgt` directory, and record the sgt commit hash the
  repositories were built with.

## 5. Ground truth to record while building

Capture these details at build time for the answer keys:

- **Episode 8:** the exact save ID, its label, both concerns inside it, and the
  two-sentence correct answer for task S1.
- **Episode 11 chain:** the feature ID of F, the full set of dependents as
  `sgt show` reports it, and the expected impact numbers that participants should
  discover in task S2.
- **Episode 17:** the episode ID, the symbol whose semantics changed, the
  one-line cause, and the minimal correct repair for task S4.
- **Tag-to-test map:** the mapping from feature tags to test files, and the
  expected pass/fail sets after tasks S2 and S3.
