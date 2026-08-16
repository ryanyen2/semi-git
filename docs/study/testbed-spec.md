# Study testbed spec: two codebases, one episode script

Date: 2026-08-09
Status: build blueprint for the CHI study testbeds
Parent doc: `docs/design/2026-08-09-chi-user-study-design.md`

## 1. The two applications

Both task sets use a scheduling application. Scheduling was chosen because every CS student knows the domain from registering for classes, the logic interlocks (conflicts depend on the time model, promotion depends on capacity and conflicts, exports depend on everything), and the domain is not in the banned toy class (todo list, notes, ledger).

| | Task set A | Task set B |
|---|---|---|
| Name | coursecraft | confplan |
| Domain | course registration for a small department | program planning for a two-day conference |
| Location | `~/repos/sgt-study/coursecraft` | `~/repos/sgt-study/confplan` |

Stack for both: Python 3.11, stdlib-only application code, JSON file persistence, argparse CLI. pytest is the single dev dependency (each repo ships a uv-managed `.venv`), because feature-tagged markers drive the scoring script. Every repo starts from an E0 seed commit (README plus `.gitignore`) so sgt has a root to bind to.

Isomorphism map. Every noun in A has exactly one counterpart in B, and the episode scripts are the same sequence of shapes with the nouns swapped.

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

Why an LLM cannot one-shot this away. The apps themselves are ordinary. What cannot be regenerated from scratch is the accumulated behavioral contract: the acceptance suite pins idiosyncratic decisions made across episodes, e.g., waitlist promotion order breaks ties by join time then by student id, back-to-back slots do not conflict, exports have exact snapshot formats. A from-scratch rewrite fails the suite. The study tasks operate on the history, and the history only exists in the built repo.

## 2. The episode script

22 episodes, identical shapes in both repos. "Human" episodes are saved with terse messages and no plan. "Agent" episodes run the plan loop (plan intake, edits, checkpoint, save) with fuller messages, which is realistic for a repo whose previous maintainer worked with an agent, and it exercises both save paths.

Structural roles the script must produce, because the study subtasks depend on them:

- One tangled save mixing an unrelated fix into a feature (E8), the provenance target.
- One feature with a dependency chain built on top of it (E11 waitlist, with E12, E14, E15-part, E21 on top), the removal target.
- One clean fix (E13) and one co-saved feature (E15) interleaved with the chain, which must survive the removal.
- One abandoned experiment removed with a real revert (E16), so the history itself contains a revert.
- One refactor that silently changes behavior (E17), the regression target.

| # | Author | Shape | coursecraft instantiation | confplan instantiation |
|---|---|---|---|---|
| E1 | agent | scaffold | models (Course, Section, Student), JSON store, CLI skeleton | models (Talk, Session, Attendee), JSON store, CLI skeleton |
| E2 | agent | model feature | rooms and weekly time-slot grid | rooms and two-day slot grid |
| E3 | human | feature | add and list courses and sections | add and list talks and sessions |
| E4 | agent | feature | student enrollment command | attendee registration command |
| E5 | human | bugfix | id reuse after delete corrupts store | same shape |
| E6 | human | feature | section capacity limits | session capacity limits |
| E7 | agent | feature | time-conflict detection on enroll | clash detection on register |
| E8 | human | tangled save | course search/filter, plus an unrelated date-parsing fix in the same save | talk search/filter, plus the same unrelated fix |
| E9 | agent | feature | prerequisite enforcement | series-dependency enforcement |
| E10 | agent | refactor | extract storage into a repository class | same shape |
| E11 | agent | feature F | waitlist: join when section is full | waitlist: join when session is full |
| E12 | agent | dependent of F | auto-promotion when a seat frees | same shape |
| E13 | human | interleaved keeper fix | back-to-back slots wrongly flagged as conflicts | adjacent slots wrongly flagged as clashes |
| E14 | agent | feature touching F | drop command, with cascade that triggers promotion | unregister command, same cascade |
| E15 | human | tangled keeper | timetable export (markdown and csv), co-saved with a small waitlist message fix | program export, same shape |
| E16 | agent | abandoned experiment | priority enrollment for seniors, then `sgt revert` of it | speaker-priority registration, then revert |
| E17 | agent | regression refactor | normalize slot comparison for cross-listed sections; silently makes boundary-touching slots count as overlapping | normalize slot comparison across rooms; same silent change |
| E18 | agent | feature | instructor schedule view | speaker schedule view |
| E19 | human | feature | enrollment statistics report | registration statistics report |
| E20 | human | bugfix | export escaping bug | same shape |
| E21 | agent | dependent of F | waitlist notification digest built on promotion events | same shape |
| E22 | human | polish | README and CLI help text | same shape |

Every episode ends green on the tests that exist at that point. The acceptance suite grows with the episodes and each test carries a feature tag (a pytest marker), so removal tasks can be scored automatically: after removing F, tests tagged `waitlist`, `promotion`, `notify` are expected to fail or be gone, and every other tag must stay green.

## 3. Study subtasks and verb coverage

Four scored subtasks per block, one stretch subtask if time remains. About 45 minutes per block. The participant-facing wording lives in the task sheets (separate doc); phrasing below is the internal shorthand. No subtask names a git or sgt verb.

| Subtask | Cap | Targets | Baseline mechanism | sgt mechanism |
|---|---|---|---|---|
| S1 provenance | 7 min | E8 tangle: "search behavior changed around when date entry got lenient; what work changed search and what else rode along?" | log, blame, diff reading | `sgt show` (accepts the save id `sgt log` prints), `sgt log`, `sgt why` |
| S2 entangled removal | 15 min | remove waitlist (E11) and its machinery; keep E13 fix, E15 export, everything else green | revert/rebase across interleaved commits | `sgt show f-waitlist` (impact count), `sgt revert` |
| S3 selective restore | (in S2 cap) | bring back plain join-waitlist without promotion or notifications | cherry-pick archaeology | `sgt restore <sel>` |
| S4 regression repair | 10 min | E17: back-to-back sections now conflict; restore old boundary behavior, keep the rest of the refactor | bisect or log reading, manual partial revert | `sgt intent` rewind, `sgt revert f@n`, or symbol restore with `at` |
| S5 plan and fork | 12 min | add "swap section" two ways (transactional swap vs drop-then-enroll with a hold); try both, keep one | branches | agent plan loop plus `sgt session start/land`, `sgt resolve` if versions compete |
| S6 stretch: history edit | if time | split the E8 tangle into two cleanly named pieces | interactive rebase | `sgt feature regroup split`, `sgt feature rename` |

Coverage check against the goal: planning (S5 plan loop), reverting (S2, and E16 inside the history), restoring (S3, S4), forking (S5 sessions/resolve), editing history (S6). Comprehension (S1) carries RQ1.

## 4. Construction rules

- Build order is the episode order. No retrofitting, because sgt's graph must record the real sequence.
- The agent for agent episodes is Claude Code (me) driving real sgt plan/save flows in the repo. Human episodes are plain edits plus `sgt save -m` with a terse message.
- Save messages are written before building (in the answer key), in each author's voice. They become the recorded intents, and S1's answerability depends on them.
- The two repos are built independently from the shared shapes, not text-transformed from each other, so the code differs naturally the way two real projects do.
- Each repo gets a gitignored `.env` holding the OpenAI key for sgt's labeling, mode 600, never committed, never echoed.
- After each episode: run the test suite, then `sgt log --summary --json` to confirm the episode landed as one coherent unit. Deviations get fixed before the next episode, because later episodes depend on earlier grouping.
- Tag every test with its feature marker as it is written. The scoring script reads markers, runs the suite, and prints kept/removed/collateral counts.
- Freeze at the end: tag `study-start` in git, archive a tarball of each repo including `.sgt`, and record the sgt commit hash the repos were built with.

## 5. Ground truth to record while building

For the answer keys, capture at build time:

- E8: the exact save id, its label, both concerns inside it, and the two-sentence correct answer for S1.
- E11 chain: the feature id of F, the full dependent set as `sgt show` reports it, and the expected impact numbers participants should discover in S2.
- E17: the episode id, the symbol whose semantics changed, the one-line cause, and the minimal correct repair for S4.
- The tag-to-test map and the expected pass/fail sets after S2 and after S3.
