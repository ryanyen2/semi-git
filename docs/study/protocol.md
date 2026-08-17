# Study protocol

Date: 2026-08-15
Status: The operational protocol. This is what the study website implements.

Related documents:
- `docs/design/2026-08-09-chi-user-study-design.md` — the design argument
- `docs/study/testbed-spec.md` — how the study repositories were built
- `docs/study/participant-materials.md` — facilitator script and answer keys

The design document argues for the study. This document pins down exactly what
is measured. Every question, every scale, and every derived number named here
has a matching definition in `web/src/study/` and is collected by the website.
If a measure is not in this document, the website does not collect it, and the
paper cannot claim it.

---

## 1. What we are testing

Version control records history as lines in files. Developers now write code by
describing what they want to an AI coding assistant. The unit of recording
(line-level diffs) and the unit of thinking (intent-level descriptions) have
come apart. sgt records history at the level of intents.

The paper's claim is not about capability. Both git and sgt can handle all six
requests, and the paper says so in a side-by-side parity table. The claim is
about **what a particular representation costs the person reading it**. There are
three sub-claims, each with its own evidence, each designed to be falsifiable:

| Claim | How it would be falsified |
|---|---|
| C1. Intent-aligned history makes provenance questions cheaper and more reliably answered. | Accuracy on requests 1 and 4 is equal or worse under sgt, with no gain in confidence calibration. |
| C2. Intent-aligned history makes destructive edits safer. | Collateral damage on requests 2 and 3 is equal or worse under sgt. |
| C3. Intent-aligned history leaves the developer with a better working theory of the project. | Quiz and summary scores do not differ, and interviews show no difference in what people could reconstruct. |

A fourth question is exploratory rather than a claim, because we have no prior
estimate to power it:

- Q4. Does the representation change how people direct and check the AI
  assistant?

We report C1–C3 as estimates with confidence intervals, and Q4 descriptively.
With only 12 participants we can detect large effects and nothing else, and
the paper will say that in the limitations section rather than hiding it behind
a p-value.

## 2. Research questions and where each is answered

| Research question (RQ) | What answers it | Instrument |
|---|---|---|
| RQ1: Comprehension | Accuracy, time, and confidence on requests 1 and 4 | Task scoring plus confidence slider |
| RQ2: Operation | Outcome and collateral damage on requests 2, 3, and 4 | Output of `score_study_repo.py`, uploaded |
| RQ3: Theory building | Quiz, summary task, and interview | Quiz (5 items), summary rubric (22 episodes), interview |
| RQ4: Agent collaboration | Telemetry on prompts, verification, and wrong turns | Claude Code hooks plus command-recording wrappers |

### The six requests, explained

Each request is modeled on a real kind of task from the Codoban et al. taxonomy
of developer motivations for examining software history.

| Request | What it tests | Codoban motivation | Which claims it serves |
|---|---|---|---|
| R1: What changed course search? | Provenance in a tangled commit (episode 8) | Rationale recovery | RQ1, C1 |
| R2: Take the waitlist out | Entangled removal (episodes 11, 12, 14, 21) | Change impact | RQ2, C2 |
| R3: Drops still need to work | Correction under time pressure (episode 14) | Change impact | RQ2, C2 |
| R4: Back-to-back enrollment broke | Regression localization (episode 17, second save) | Bug localization | RQ1+RQ2, C1+C2 |
| R5: Two ways to swap | Parallel alternatives, discard one | Evolution | RQ4 |
| R6: Clean up the tangled change | History surgery, code stays the same | Evolution | RQ2 |

Requests 5 and 6 are optional and analyzed descriptively only. They exist
because both pilots showed that participants reach for these kinds of tasks, and
because R6 is the cleanest single demonstration of the representational
difference. They are never used to support claims C1–C3, since not every
participant reaches them, and analyzing an optional task as if it were assigned
would create a garden of forking paths.

## 3. Study design

This is a **within-subject** design: every participant works in both conditions,
on two isomorphic (structurally identical) projects, with condition order and
project assignment fully counterbalanced. There are 12 participants assigned to
four groups of three.

| Group | First half | Second half |
|---|---|---|
| 1 | git, coursecraft | sgt, confplan |
| 2 | sgt, coursecraft | git, confplan |
| 3 | git, confplan | sgt, coursecraft |
| 4 | sgt, confplan | git, coursecraft |

The website assigns participant IDs (P01–P12) round-robin across the four
groups, so any prefix of the cohort is still balanced. If the study stops at
eight participants, those eight still include two per group. This matters
because studies do stop early, and a cohort that is balanced only at n=12 is
unanalyzable at n=9.

The condition is never named to the participant. The website says "Setup A" and
"Setup B", and never reveals which one is ours.

### What each condition includes

A condition is a way of reading and changing history, not just a command-line
tool. Both halves offer the same three workspaces, and the participant chooses
freely among them:

| | git condition | sgt condition |
|---|---|---|
| Terminal | `git` commands | `sgt` commands, with `git` still available underneath |
| Editor | VS Code with built-in Source Control, Timeline, and GitLens 19.0.1 | VS Code with the semi-git extension |
| AI assistant | Claude Code, pinned to a specific model, identical in both halves | Same assistant, plus sgt's MCP tools and skills |

The assistant's capabilities are part of the condition, not background scenery.
Both halves tell the participant to use the assistant for anything, and request 5
explicitly asks for two implementations, so most work will involve the assistant.
In the git half the assistant already knows git well and needs nothing extra. In
the sgt half it gets the MCP server and three bundled skills (what `sgt init
--agent` installs for any user). Without these, the sgt condition would be
measured with the agent-facing half of the product turned off, and the comparison
would be "a tool the assistant knows" versus "a tool it must guess at" rather
than a comparison of two ways of recording history.

Two models are pinned per session, not one. The assistant runs on
`claude-sonnet-5`, and sgt's labeller and search run on `gpt-5.6-luna` (written
into the project's `.env` at setup time). The second matters as much as the
first: every feature name a participant reads in the sgt condition was written
by that model, so a session that silently fell back to a different model — or to
the deterministic offline names, which is what a dead API key produces — would
not be comparable with the others. Both are checked before the session starts by
making a real API call rather than just checking that a key exists.

GitLens is in the git condition deliberately. Comparing an editor extension
against a bare terminal would measure the presence of a graphical view rather
than the representation underneath it, and GitLens is how people actually read
git history in an editor. Both extensions are shipped inside the bundle at a
fixed version, so every participant runs the same software. The version is
recorded in `study.json`.

The editor runs from a dedicated profile inside the study folder, opened with
the `study-code` command. Its `git.path` and `sgt.path` settings point at the
same recording wrappers that the shell uses, so a click in a history view is
recorded exactly like a typed command and can be distinguished from one.

Everything else about the editor is held identical between conditions. Both
profiles get the same pinned Python tooling — the Python extension, Pylance,
debugpy, and python-envs — installed at setup rather than left for the editor
to offer on demand. In the first editor rehearsal the git arm ended up with 198
MB of Python tooling and the sgt arm with none, purely because one half happened
to lead the participant to open a `.py` file first. Having go-to-definition and
type inference in one arm but not the other is not a difference between two ways
of recording history. The setup check fails if anything is missing or if
anything extra was installed.

## 4. What the participant does, step by step

The study website is the participant's only interface for the study flow. Each
step writes to Firestore as values change, so closing a laptop loses nothing.

| Step | What happens | Time cap | What is recorded |
|---|---|---|---|
| 1 | Welcome and code entry | — | Claims the participant record |
| 2 | Consent | — | `consent` (6 items, typed name, version) |
| 3 | Background questionnaire | 5 min | `background` |
| 4 | Setup for first half | — | Live green checks from the machine's heartbeat |
| 5 | Tutorial for first half | 10 min | `tutorialCompletedAt` |
| 6 | Task block 1 (requests R1–R6) | 45 min | Per-request timings, answers, and confidence |
| 7 | Post-block 1 questionnaires: NASA-TLX, UMUX-Lite, HLAC, quiz, summary | 12 min | Four response documents |
| 8 | Setup for second half | — | Second heartbeat |
| 9 | Tutorial for second half | 10 min | — |
| 10 | Task block 2 | 45 min | — |
| 11 | Post-block 2 (same four questionnaires) | 12 min | — |
| 12 | Preference and closing questions | 8 min | `preference` |
| 13 | Data handover and debrief | — | Final sync confirmation |

The session takes about 125 minutes with breaks, compared to the design
document's original 120. The extra 5 minutes come from moving the usability and
HLAC (History Legibility and Agent Collaboration) batteries to immediately after
each half, which is where usability instruments belong: asking someone to rate
"Setup A" after they have spent 45 minutes in "Setup B" measures memory, not
experience. Replacing the 10-item SUS (System Usability Scale) with the 2-item
UMUX-Lite (Usability Metric for User Experience, Lite version) gave two minutes
back and spent four of them on the new HLAC items.

### Timers

Each request card has its own time cap and its own clock. The clock starts when
the participant opens the card and stops when they mark it done or the time cap
expires. The timer is visible to them. A visible countdown is part of the task
design: the time caps come from Ko et al., and a hidden cap would turn "ran out
of time" into "gave up", which are different kinds of data.

Pausing is explicit and recorded. Facilitator interruptions, tool breakage, and
breaks all produce a paused interval with a recorded reason, and the analysis
uses active time only. Pilot 1 lost a request to a tool failure with no record
of how long the recovery took.

## 5. Instruments

Item wording is fixed in this document because the paper must report it exactly
and because a questionnaire edited between participants is a questionnaire with
no consistent scale. Every battery carries a version string, stored with each
response.

### 5.1 Consent (`consent-v1`)

Six checkboxes (all but the last are required), plus a typed name and date.

1. I have read the information sheet and had my questions answered.
2. I agree to my screen and voice being recorded for this session.
3. I agree that the commands I run and the messages I send to the AI assistant are recorded.
4. I understand my data will be de-identified and reported in aggregate.
5. I understand I can stop at any time, without a reason, and still be paid.
6. *(optional)* I agree to short anonymized quotes from my session appearing in a publication.

### 5.2 Background (`background-v1`)

These are covariates (factors to control for), not screening criteria. Screening
happened at recruitment.

| Item | Format |
|---|---|
| Years writing code seriously | Integer |
| Years using git | Integer |
| How often you use each command: `log`, `blame`, `bisect`, `revert`, `reset`, `rebase -i`, `reflog`, `cherry-pick` | Never / Rarely / Sometimes / Often |
| AI coding tools you have used in agent mode | Multi-select |
| How often you work with an AI coding assistant | Daily … Never |
| Share of code you shipped last month that an assistant wrote | 0–100 slider |
| Primary programming languages | Free text |
| Have you used sgt or semi-git before? | Yes / No |

The eight-command frequency grid produces a **git expertise composite score**
(0–24, where never=0 and often=3) that goes into the statistical models as a
covariate. We do not ask for self-rated expertise on a single 1–7 scale, because
that correlates with confidence rather than actual skill. The last item is an
exclusion check, not a covariate.

### 5.3 NASA-TLX (Task Load Index), raw (`tlx-v2`), after each half

Six subscales, unweighted (this is "Raw TLX", following Hart's 2006
retrospective), using the instrument's original 21-point scale from 0 to 100
rather than the seven-point scale used elsewhere in this study. A coarser scale
does not merely blur TLX — it changes its shape: frustration migrates onto the
physical subscale and effort splits across two. Three implementation choices are
stated here because their absence is the recurring source of incomparability
between papers that all report "we used TLX."

The six subscales are: Mental demand, Physical demand, Temporal demand,
Performance, Effort, and Frustration.

Each subscale is shown with its full published definition, not only its name.
The six correlate strongly enough in interactive work that a participant reading
only a short label tends to answer several of them alike.

Performance is presented **failure-to-perfect** (failure at the low end, perfect
at the high end) and reversed exactly once in the `tlxScore` computation, so
that a higher number consistently means higher workload on all six subscales.
Until 2026-08-17 it was presented perfect-to-failure *and* reversed in scoring,
which meant a participant who felt they had performed perfectly was contributing
the maximum possible workload score. The item anchors are asserted in
`web/tests/analysis.test.ts` alongside the arithmetic, because either half of
the convention alone is only half correct.

Physical demand is retained even though it provides little information for desk
work. Dropping a subscale would make the total incomparable with every other
paper that reports Raw TLX.

The questionnaire names the specific requests, not the session as a whole.
NASA-TLX measures the workload of a bounded task and produces something
different when pointed at an hour of mixed activity.

### 5.4 UMUX-Lite (Usability Metric for User Experience, Lite) (`umux-lite-v1`), after each half

Two items on their published seven-point scale, replacing the ten-item SUS
(System Usability Scale) that this study used until 2026-08-17.

| # | Statement |
|---|---|
| U1 | This setup's capabilities meet my requirements. |
| U2 | This setup is easy to use. |

The referent is filled in as "this setup" rather than the generic "this system."
Both halves run the same assistant in the same editor on the same kind of
project. An unqualified "system" would be interpreted differently by different
participants, and the difference between the halves is the entire measurement.

Scores are reported raw on a 0–100 scale using the published formula. They are
**not** converted to a SUS-equivalent score, because that regression was fitted
to particular data sets, and a within-participant difference gains nothing from
the transformation while inheriting its error. No claim is made that any
absolute score is high or low — these instruments carry no norms for this kind
of work and are not ratio measures, so only the difference between conditions is
interpreted.

The swap from SUS costs nothing this study was using. SUS is designed for
benchmarking against its average of 68, and this study never benchmarks — it
reports a paired difference. Ten items administered twice per session bought one
number that two items buy equally well, and the eight extra rows were the ones
most likely to be answered carelessly (straight-lined).

### 5.5 HLAC: History Legibility and Agent Collaboration (`hlac-v2`), after each half

Fourteen items on a 7-point Likert-type scale from strongly disagree (1) to
strongly agree (7), organized in four labeled blocks in a fixed order. This
battery appears as Figure 1 in the paper.

These are **Likert-type items grouped into ad-hoc composites, not a validated
psychometric scale**, and they are reported as such: item by item, with the
block mean given as a summary rather than as a construct score. No
internal-consistency coefficient (like Cronbach's alpha) is reported for a
two-to-four-item block at this sample size, because such a coefficient would
make an ad-hoc block look like a validated instrument.

Seven points were chosen because reliability and validity fall off below five
points and gain little above seven, and because it matches the published
instrument beside it (UMUX-Lite).

**Block 1: Finding your way around**

| # | Short label (for figures) | Statement | Serves |
|---|---|---|---|
| Q1 | Found when it changed | I could find when a behavior changed. | C1 |
| Q2 | Found why it changed | I could find out why a change was made. | C1 |
| Q3 | Saw the whole piece of work | When I found a change, I could see what larger piece of work it belonged to. | C1 |
| Q11 | *(reverse-keyed)* Guessed at names | I had to guess at names or IDs to find what I was looking for. | C1 |

**Block 2: Changing things**

| # | Short label (for figures) | Statement | Serves |
|---|---|---|---|
| Q4 | Knew what else it would touch | Before I changed anything, I could tell what else would be affected. | C2 |
| Q5 | Removed a feature safely | I could take a feature out without worrying about breaking what came after it. | C2 |
| Q6 | Recovered from mistakes | When something went wrong, I could get back to a good state. | C2 |
| Q12 | *(reverse-keyed)* Surprised by the result | A change did something I had not expected. | C2 |

**Block 3: What you came away with**

| # | Short label (for figures) | Statement | Serves |
|---|---|---|---|
| Q7 | Clear picture of the project | I ended up with a clear picture of how this project got to where it is. | C3 |
| Q13 | Would get back up to speed | If I came back to this project in a month, what is recorded would get me back up to speed. | C3 |

**Block 4: Working with the assistant**

| # | Short label (for figures) | Statement | Serves |
|---|---|---|---|
| Q8 | Directed the assistant precisely | I could point the assistant at exactly the part of the history I meant. | Q4 |
| Q9 | Checked the assistant's work | I could check what the assistant did against what I asked for. | Q4 |
| Q14 | *(reverse-keyed)* Accepted unreviewed work | I accepted changes from the assistant that I had not really reviewed. | Q4 |
| Q10 | *(reverse-keyed)* Fought the tool | I spent effort fighting the tool rather than doing the task. | Guard item |

Items are grouped under headings in a fixed order rather than randomized. An
undifferentiated column of nearly identical rows gets answered by pattern; the
headings are the cheapest guard against that, and they only work when the items
that belong together sit together. The guard against straight-lining (answering
every item the same) is the four reverse-keyed items instead.

**Q12 and Q14 are honesty valves.** If one condition wins on everything
*including* "nothing surprised me" and "I reviewed everything," suspect
acquiescence bias. If a condition wins on finding and changing while *losing*
Q14 — people accepted more unreviewed work because the tool made accepting
easy — the data reads as credible, and the story is a cost paid knowingly.
Either pattern is reported.

### 5.6 Quiz (`quiz-v1`), after each half, with the project closed

Five items, two-minute time cap. Answer keys are specific to each project and
are stored in `web/src/study/answerKeys.ts`, generated from the build logs.

1. Which feature was added and then deliberately removed?
   *coursecraft*: senior priority enrollment (episode 16). *confplan*: speaker-priority registration.
2. Which came first: conflict detection, capacity limits, or the waitlist?
   Capacity limits (episode 6, before episode 7, before episode 11), in both projects.
3. Did the previous maintainer work alone?
   No. An AI assistant did part of the work.
4. Name one change that was later corrected, and say what the correction was.
   Accepts any of: episode 5's ID reuse, episode 13's back-to-back fix, episode 20's export escaping, or episode 16's revert.
5. Which single change touched the most unrelated concerns?
   Episode 8 — the search commit that also carried the date-parsing fix.

Each item is scored 0 or 1 by the facilitator against the answer key, and each
carries a confidence slider. Item 4 intentionally accepts several answers: a
quiz that only accepts the answer we were fishing for measures our fishing, not
the participant's understanding.

### 5.7 Summary task (`summary-v1`), after each half

Three minutes, project closed, typed and spoken aloud. The prompt:

> Without looking at the project, tell the story of it. What was built, in what
> order, what went wrong, and what was undone?

Scored later in the analysis dashboard against the 22-episode ground truth as
three numbers:

- **Coverage.** How many episodes were mentioned, out of 22. Scored using a
  checkbox grid, one row per episode.
- **Causal links.** The number of correctly stated because-relationships between
  episodes. For example: "The waitlist stopped being needed once the registrar
  took it over" counts as one.
- **Misconceptions.** Confidently stated claims that are factually false,
  counted.

Coverage alone rewards listing things. The three measures together separate
"remembered a list" from "built a working theory," which is the RQ3 distinction
and the reason Naur's "programming as theory building" is cited. Two coders
score the summaries, with 25% double-coded and agreement negotiated following
McDonald et al.

### 5.8 Preference and closing (`preference-v1`), end of session

Forced choice for each task archetype, with the two setups labeled A and B:

- Finding when and why something changed
- Taking a feature out without breaking things
- Finding what caused a regression
- Working with the AI assistant

For each: Setup A / Setup B / No real difference, plus a free-text explanation of
why. Then overall preference, and "would you want this on your own projects" per
setup on a 7-point scale. The archetype-level forced choice is the most
defensible preference measure here, because a single overall preference with
n=12 is just one bit times twelve.

### 5.9 Interview

Conducted by the facilitator using the dashboard, with timestamped notes
organized around fixed probes:

- What did you trust, and what did you check?
- Where were you lost?
- What did the history hide, and what did it show?
- What did you wish you could ask the history?
- How did you decide what to hand to the assistant?

The fourth probe is asked **before** the participant is invited to compare the
two setups. Both pilots answered it with something close to what sgt does — one
of them while in the git half — and that finding is worth protecting from
contamination.

## 6. What the machine records

The study bundle records events to a local append-only log first and uploads
them second. The local file is the record of truth; uploading is idempotent
(safe to repeat) and automatically retried. Nothing is lost if the network
drops, and running the sync twice cannot double-count because every event
carries a content-addressed ID.

### Recording sources

| Source | How it is captured | What it provides |
|---|---|---|
| Claude Code prompts | `UserPromptSubmit` hook | The exact prompt, timestamp, and session ID |
| Agent tool calls | `PreToolUse` / `PostToolUse` / `PostToolUseFailure` hooks | Tool name, command, file, and success/failure |
| Agent turn boundaries | `SessionStart` / `Stop` / `SessionEnd` hooks | Turn latency and turns per request |
| Participant's own commands | PATH wrappers for `git`, `sgt`, `pytest`, `python` | Command arguments, exit code, and duration |
| Editor actions | Same wrappers, reached through the `git.path` and `sgt.path` editor settings | Arguments of every read or change made through a view |
| Repository state | `git rev-parse` plus test run at each request boundary | Tree hash and whether tests pass |
| Heartbeat | 30-second ping | Liveness indicator on the experimenter's monitor |

Every command carries a label for the surface it came from: `terminal`,
`editor`, or `agent`. The launchers set this label (rather than the analysis
inferring it later) because `git log` typed in a shell and `git log` run by a
history view are the same string, and the difference between them is exactly why
the editor is part of this study.

**What is excluded from the participant's record.** Four categories of
instrument activity are kept out:

1. An editor's own periodic background polling and extension initialization are
   recorded but flagged `auto`, and the analysis drops them.
2. The git commands that the sync daemon runs to check for changes are not
   recorded at all.
3. The question the setup check asks the assistant (to prove the API key works)
   is not recorded at all.
4. A command run by another recorded command is also not recorded, and the
   wrapper skips starting Python so the nested call has zero overhead.

Point 4 matters more in one condition than the other: `sgt` shells out to git
about five times per command. Without this exclusion, a two-minute editor session
produced 136 git calls against 28 sgt calls, which would read as "they mostly
used git" in the condition where they mostly used sgt. It also removed a
one-sided timing penalty, since only the tool that spawns subprocesses was paying
for the instrumentation overhead.

None of this was true for the first pilot. 450 of its 476 command events were
the sync daemon checking the repository every twenty seconds, and one of its two
prompts was the setup check's own "Reply with exactly: ok." A session cannot be
re-run, so an instrument that records itself is not something the analysis can
fix afterwards.

Hooks run with `async: true`, so the recording cannot make the participant's
assistant feel slow, and a broken hook cannot block a session.

### The action taxonomy

Raw events are classified into nine categories. Everything downstream —
including the n-gram analysis — works on this alphabet.

Two special rules exist because the editor reaches the same command-line
interface through commands a terminal rarely uses:

1. A grouped command is classified under its verb, not the group. So
   `feature regroup split` is counted as an operation, not an inspection.
2. A report-only call is classified as an inspection. This includes anything
   named `preview` and anything the extension requested with `--json` (which is
   how the extension reads data). Hovering a feature in the VS Code workbench
   fires `advanced preview revert <feature> --json` several times per second;
   counting those as reverts would report that somebody operated on history when
   they merely moved a mouse across a list. Identical editor reads within two
   seconds of each other are also collapsed to one event for the same reason.

| Category | Includes |
|---|---|
| `orient` | `git log`, `git status`, `sgt log`, `sgt now`, `sgt status` with no specific target |
| `inspect` | `git show/blame/diff/bisect`, `sgt show/why/recall/diff` targeted at a specific item |
| `search` | `grep`, `rg`, `find`, `ls`, reading a source file |
| `prompt` | A message sent to the AI assistant |
| `agent_edit` | `Edit`, `Write`, or `NotebookEdit` performed by the assistant |
| `manual_edit` | A tracked file changed with no agent edit accounting for it |
| `history_op` | `git revert/reset/rebase/cherry-pick/checkout`, `sgt revert/restore/save/split/merge` |
| `verify` | `pytest`, running the app, `git diff` after an edit, `sgt drift` |
| `recover` | `git reflog`, `git reset --hard`, `sgt restore` after a failed operation, re-unpacking the bundle |

`manual_edit` is inferred (not directly observed) and is marked as inferred
wherever it appears. `verify` deliberately requires that the action happen
*after an edit*: `git diff` before a change is orientation, the same command
after a change is checking. Any taxonomy that ignores position mislabels
roughly a third of diffs, which was visible in the pilot logs.

### Derived measures

Computed per request, per condition:

- Number of prompts sent, and mean prompt length
- **Prompt specificity**: does the prompt name a commit SHA, an intent ID, a
  file, or a test? A four-level ordinal scale. This is the concrete form of
  "directs the agent precisely" and is the measure most likely to show the
  representational difference, because sgt gives people nameable things to
  point at.
- **Verification ratio**: count of `verify` events divided by count of
  (`history_op` + edit) events
- **Time to first history operation**: how long the participant spends
  orienting before acting
- **Wrong turns**: a `history_op` followed within 120 seconds by a `recover`
  event
- **Collateral damage**: tests failing outside the target feature, reported by
  the scoring script
- **Action n-grams**: bigram (two-step) and trigram (three-step) sequences over
  the category alphabet

### N-gram comparison method

Bigram and trigram frequencies are compared between conditions using the
**weighted log-odds ratio with an informative Dirichlet prior** (Monroe,
Colaresi, and Quinn, 2008), with the pooled corpus as the prior. Raw frequency
differences and plain lift both over-report rare sequences, which with only
twelve participants mostly means they report noise. Monroe's z-scores shrink
rare sequences toward zero, which is the standard fix. Sequences appearing
fewer than five times across the entire corpus are dropped before scoring, and
the number dropped is printed on the figure rather than left implicit.

## 7. Analysis plan

### The unit of comparison

Every participant works in both conditions in one sitting, each condition on a
different (but structurally identical) project, with condition order and project
assignment fully crossed. Every outcome is therefore a **within-participant
difference** (sgt minus git), which removes between-person variation in
programming experience — variation that at n=12 would otherwise swamp any effect
the conditions produced. A participant who completes only one half contributes no
difference and is dropped from paired comparisons rather than imputed.

### Which measures carry which weight

Stated in advance so that a tertiary measure that happens to move cannot be
reported as if it were the main finding.

| Tier | Measures |
|---|---|
| Primary | Request scoring on R1–R4 (rubric points) and collateral damage from `score_study_repo.py` |
| Secondary | Quiz and summary task — what the person could reconstruct with the project closed |
| Tertiary | Self-report: NASA-TLX, UMUX-Lite, the HLAC battery |
| Descriptive | Telemetry: surfaces used, action sequences, prompt specificity, wrong turns, time to first history operation |

Self-report is tertiary and is treated as such throughout. It is the tier most
exposed to demand characteristics — the participant knows one setup is ours,
whatever we call it on screen.

### The estimate is the headline, not the statistical test

Every outcome is reported as a paired mean difference with a **95% bootstrap
confidence interval** (CI), resampling participants rather than individual
observations, using 10,000 resamples computed from a fixed random seed so that a
figure and its caption cannot drift apart. Intervals are plotted, not just listed
in a table.

With only a dozen pairs, a p-value invites a binary reading (significant or not)
that the data cannot support. The confidence interval communicates the same
information and honestly shows how little it pins down. Where a formal test is
informative, we use the **Wilcoxon signed-rank test** on paired differences with
**matched-pairs rank-biserial** effect sizes, and an **exact McNemar test** for
per-request binary outcomes (solved / not solved, damage / no damage). These are
reported alongside the intervals, never in place of them. Using nonparametric
tests is not a concession here — at these sample sizes they are frequently the
more powerful choice.

Questionnaire responses are additionally shown as **full distributions per
item**. A cluster at the midpoint and a split between the extremes have the same
mean and are not the same result.

Composite scores summed across several items — the TLX average, the UMUX-Lite
score, a rubric total — can reasonably be treated with parametric methods.
Individual ordinal items do not get a metric model; this is the one point the
ordinal-data methodology literature does not divide on.

### Families of comparison, and what we do not correct for

Six families of comparison are named in advance: the four HLAC blocks, NASA-TLX,
and UMUX-Lite. All six are reported whether or not they moved. No post-hoc
multiple-comparison correction is applied across them. At this sample size the
real risk is selective reporting (only showing the families that moved), and a
correction addresses the wrong problem while making an already underpowered study
unable to answer anything at all.

### Qualitative material

Think-aloud recordings are treated as protocol data and analyzed alongside a
reflexive thematic analysis of the closing interviews. Navigation behavior is
hand-coded in both conditions using the same coding scheme, so that navigation
remains a property of the person rather than of the instrumented tool. The
recording that explains a quantitative result is reported beside it.

### Pre-registration, and the limits of the claims

Research questions, conditions, rubric wording, exclusion rules, and planned
comparisons are registered before the first participant. This document is that
pre-registration text.

The study is **powered for large effects only**, and we say so directly rather
than treating a wide interval as evidence of absence. Workload and usability
scores carry no norms for this kind of work and are not ratio measures, so we
interpret only the difference between conditions on the task the instrument
names. No claim is made that any absolute score is high or low.

Two results are pre-committed as expected:

1. Request 6 is where sgt should show its clearest advantage, yet it is
   nonetheless optional and descriptive only.
2. The discriminant scenarios at the end of the session should produce **mixed**
   results. If a participant picks the same setup for fixing a typo in a repo
   they will never see again *and* for a codebase they will own for a year, that
   is evidence of demand characteristics (answering how they think we want) and
   is reported as such rather than as a preference.

## 8. The three figures

The figure budget for a CHI (Conference on Human Factors in Computing Systems)
results section is three. These three, in this order, carry claims C1 through C3
and question Q4.

### Figure 1. What the two setups felt like

Diverging stacked Likert bars for the fourteen HLAC items, grouped by their four
blocks with reverse-keyed items marked, one panel per condition, response counts
printed inside the segments. A right-hand panel shows paired mean differences
with 95% bootstrap confidence intervals (CIs). Items are shown individually; the
block mean appears as a summary line, not as a construct score, and no
reliability coefficient is reported for blocks this short.

This figure goes first because it is the only one a reviewer can read in five
seconds, and because perception is the claim most people will test against their
own intuition.

### Figure 2. What people actually managed to do

A paired within-subject estimation plot with four archetypes across the top (R1,
R2+R3, R4, and collateral damage). For each archetype: every participant's two
scores are joined by a line, with condition on the x-axis, and beneath it the
paired mean difference with its bootstrap CI on a floating axis (Gardner-Altman
style).

Showing twelve individual slopes is the honest way to plot n=12. A bar chart of
two group means hides whether one person moved a lot or twelve people moved a
little, and at this sample size that distinction is the whole question. Collateral
damage is on its own panel with an inverted axis so that "up is better" holds
consistently across the figure.

### Figure 3. How the work was done

Two stacked panels sharing a legend:

- **(a) Where the time went.** One horizontal strip per participant-half, with
  the x-axis showing proportion of that half's active time and segments colored
  by action category, grouped by condition. Above each group: a stacked area
  chart showing category share across normalized time. The individual strips
  show the distribution; the area chart shows the shape.
- **(b) What the sequences looked like.** A dot plot of the most discriminating
  action bigrams by Monroe z-score, with sgt-leaning sequences on the right and
  git-leaning sequences on the left, annotated with occurrence counts.

Figure 3 is the figure that makes the paper more than a benchmark. Panel (a)
shows *when* the two groups diverged, panel (b) shows *what pattern* differed,
and together they are the mechanism behind Figures 1 and 2. This is also the
figure that answers the reviewer question that sinks tool papers: "did they just
use the AI more?"

Every figure exports to SVG with fonts as text, an ACM-column-width preset
(3.33 inches for single-column, 7.0 inches for double-column), and no
rasterized layers.

## 9. Honest limits, stated up front

- Twelve participants can detect large effects only. Results are reported as
  intervals, never as significance claims.
- Participants have years of git experience against ten minutes of sgt. This
  asymmetry cuts against sgt, so a positive result is conservative and a null
  result is uninformative about sgt's ceiling.
- A novelty effect runs the other way and cannot be fully removed. It is
  mitigated by neutral naming and by never revealing authorship, and it is stated
  as a limitation.
- Agent variance is part of the condition, not controlled away. The model
  version is pinned, and every transcript is kept.
- Two small synthesized codebases and 45-minute halves. Real theory building
  happens over weeks. The lab study answers the question of first contact only,
  which is why the design document recommends a field deployment as the
  companion study.
