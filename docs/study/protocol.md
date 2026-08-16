# Study protocol, as instrumented

Date: 2026-08-15
Status: the operational protocol. This is what the study website implements.

Parents: `docs/design/2026-08-09-chi-user-study-design.md` (the design argument),
`docs/study/testbed-spec.md` (how the repos were built),
`docs/study/participant-materials.md` (facilitator script and answer keys).

The design doc argues for the study. This document fixes it. Every question,
every scale, every derived number, and every figure named here has a matching
definition in `web/src/study/` and is collected by the site. If a measure is not
in this document, the site does not collect it, and the paper cannot claim it.

---

## 1. Thesis, restated so it can be tested

Version control records history as lines in files. Developers now author change
by describing intent to an agent. The unit of record and the unit of thought
have come apart. sgt records history at the level of intents.

The paper's claim is not capability. Git and sgt can both do all six requests,
and the paper says so in a parity table. The claim is about **what a
representation costs the person reading it**. Three sub-claims, each with its
own evidence, each falsifiable:

| Claim | Falsified if |
|---|---|
| C1. Intent-aligned history makes provenance questions cheaper and more reliably answered. | Accuracy on requests 1 and 4 is equal or worse under sgt, with no confidence-calibration gain. |
| C2. Intent-aligned history makes destructive edits safer. | Collateral damage on requests 2/3 is equal or worse under sgt. |
| C3. Intent-aligned history leaves the developer with a better working theory of the project. | Quiz and summary scores do not differ, and interviews show no difference in what people could reconstruct. |

A fourth question is exploratory, not a claim, because we have no prior estimate
to power it:

- Q4. Does the representation change how people direct and check the agent?

We report C1–C3 as estimates with intervals and Q4 descriptively. With n=12 we
can see large effects and nothing else, and the paper will say that in the
limitations rather than hide it behind a p-value.

## 2. Research questions and where each is answered

| RQ | Answered by | Instrument |
|---|---|---|
| RQ1 comprehension | Requests 1 and 4 accuracy, time, confidence | Task scoring + confidence slider |
| RQ2 operation | Requests 2/3 and 4 outcome, collateral damage | `score_study_repo.py` output, uploaded |
| RQ3 theory building | Quiz, summary task, interview | Quiz (5 items), summary rubric (22 episodes), interview |
| RQ4 agent collaboration | Telemetry: prompts, verification, wrong turns | Claude Code hooks + command shims |

### The six requests, mapped

| Request | Archetype | Codoban motivation | Serves |
|---|---|---|---|
| R1 what changed course search | provenance in a tangled commit (E8) | rationale recovery | RQ1, C1 |
| R2 take the waitlist out | entangled removal (E11 + E12/E14/E21) | change impact | RQ2, C2 |
| R3 drops still need to work | correction under time pressure (E14) | change impact | RQ2, C2 |
| R4 back-to-back enrollment broke | regression localization (E17, second save) | bug localization | RQ1+RQ2, C1+C2 |
| R5 two ways to swap | parallel alternatives, discard one | evolution | RQ4 |
| R6 clean up the tangled change | history surgery, code unchanged | evolution | RQ2 |

R5 and R6 are optional and are analyzed descriptively only. They exist because
both pilots showed people reach for them, and because R6 is the cleanest single
demonstration of the representational difference. They are never used to support
C1–C3, since not every participant reaches them and analyzing an optional task
as if it were assigned is a garden of forking paths.

## 3. Design

Within-subject, two conditions, two isomorphic projects, order and project
counterbalanced. Twelve participants, four groups of three.

| Group | First half | Second half |
|---|---|---|
| 1 | git, coursecraft | sgt, confplan |
| 2 | sgt, coursecraft | git, confplan |
| 3 | git, confplan | sgt, coursecraft |
| 4 | sgt, confplan | git, coursecraft |

The site assigns P01–P12 round-robin across the four groups, so any prefix of
the cohort is still balanced. If the study stops at eight participants, those
eight are still two per group. This matters more than it sounds: studies stop
early, and a cohort that is balanced only at n=12 is unanalyzable at n=9.

Condition is never named to the participant. The site says "Setup A" and
"Setup B", assigned per half, and never says which one is ours.

## 4. What the participant does, step by step

The site is the participant's only surface. Each step writes to Firestore on
change, so a closed laptop loses nothing.

| # | Step | Cap | Writes |
|---|---|---|---|
| 1 | Welcome and code entry | — | claims the participant record |
| 2 | Consent | — | `consent` (6 items, typed name, version) |
| 3 | Background questionnaire | 5 min | `background` |
| 4 | Setup, half 1 | — | live green check from the machine's own heartbeat |
| 5 | Tutorial, half 1 | 10 min | `tutorialCompletedAt` |
| 6 | Task block 1 (R1–R6) | 45 min | per-request timings, answers, confidence |
| 7 | Post-block 1: TLX, SUS, HLAC, quiz, summary | 12 min | four response docs |
| 8 | Setup, half 2 | — | second heartbeat |
| 9 | Tutorial, half 2 | 10 min | |
| 10 | Task block 2 | 45 min | |
| 11 | Post-block 2, same four | 12 min | |
| 12 | Preference and closing | 8 min | `preference` |
| 13 | Data handover and debrief | — | final sync confirmation |

About 125 minutes with breaks, against the design doc's 120. The extra time is
the SUS and HLAC batteries moved to immediately after each half, which is where
usability instruments belong: asking someone to rate setup A after they have
spent 45 minutes in setup B measures memory, not experience.

### Timers

Each request card has its own cap and its own clock. The clock starts when the
participant opens the card and stops when they mark it done or the cap expires.
It is visible to them. A visible countdown is part of the task: the design doc
inherits time caps from Ko et al., and a hidden cap turns "ran out of time" into
"gave up", which are different data.

Pausing is explicit and recorded. Facilitator interruptions, tool breakage, and
breaks all produce a paused interval with a reason, and the analysis uses active
time. Pilot 1 lost a request to a tool failure with no record of how long the
recovery took.

## 5. Instruments, in full

Item wording is fixed here because the paper must report it and because a
questionnaire edited between participants is a questionnaire with no scale.
Every battery carries a version string, stored with each response.

### 5.1 Consent (`consent-v1`)

Six checkboxes, all but the last required, plus typed name and date.

1. I have read the information sheet and had my questions answered.
2. I agree to my screen and voice being recorded for this session.
3. I agree that the commands I run and the messages I send to the AI assistant are recorded.
4. I understand my data will be de-identified and reported in aggregate.
5. I understand I can stop at any time, without a reason, and still be paid.
6. *(optional)* I agree to short anonymized quotes from my session appearing in a publication.

### 5.2 Background (`background-v1`)

Covariates, not screening. Screening happened at recruitment.

| Item | Type |
|---|---|
| Years writing code seriously | integer |
| Years using git | integer |
| Frequency of use, per verb: `log`, `blame`, `bisect`, `revert`, `reset`, `rebase -i`, `reflog`, `cherry-pick` | never / rarely / sometimes / often |
| Agent tools used in agent mode | multi-select |
| How often you work with an AI coding assistant | daily … never |
| Share of code you shipped last month that an assistant wrote | 0–100 slider |
| Primary languages | free text |
| Have you used sgt or semi-git before? | yes / no |

The eight-verb grid gives a **git expertise composite** (0–24, never=0 … often=3)
that goes into the models as a covariate. Self-rated expertise on a single
1–7 scale correlates with confidence, not skill, which is why it is not asked.
The last item is an exclusion check, not a covariate.

### 5.3 NASA-TLX, raw (`tlx-v1`), after each half

Six standard subscales on the standard 21-point 0–100 scale, unweighted (raw
TLX, per Hart's 2006 retrospective). Physical demand is retained even though it
is uninformative for desk work, because dropping a subscale makes the total
non-comparable with every other paper that reports raw TLX.

Mental demand · Physical demand · Temporal demand · Performance · Effort · Frustration.

Performance is reverse-scored (anchored "perfect" to "failure") before totalling.

### 5.4 SUS (`sus-v1`), after each half

The ten standard items, with "the system" replaced by "this setup". Scored the
standard way to 0–100. Reported as a number, not plotted; SUS is a benchmark
against the 68 average, and a ten-item Likert figure of it would waste the
figure budget on an instrument that was never designed to be read item-wise.

### 5.5 History legibility and agent collaboration (`hlac-v1`), after each half

Ten items, 7-point, strongly disagree (1) to strongly agree (7). This is the
battery in Figure 1. Items were written to the claims, one construct per item,
with one reverse-coded item to catch straight-lining.

| # | Short label (figure) | Statement |
|---|---|---|
| Q1 | Found when it changed | I could find when a behavior changed. |
| Q2 | Found why it changed | I could find out why a change was made. |
| Q3 | Saw the whole piece of work | When I found a change, I could see what larger piece of work it belonged to. |
| Q4 | Knew what else it would touch | Before I changed anything, I could tell what else would be affected. |
| Q5 | Removed a feature safely | I could take a feature out without worrying about breaking what came after it. |
| Q6 | Recovered from mistakes | When something went wrong, I could get back to a good state. |
| Q7 | Clear picture of the project | I ended up with a clear picture of how this project got to where it is. |
| Q8 | Directed the assistant precisely | I could point the assistant at exactly the part of the history I meant. |
| Q9 | Checked the assistant's work | I could check what the assistant did against what I asked for. |
| Q10 | *(reverse)* Fought the tool | I spent effort fighting the tool rather than doing the task. |

Q1–Q3 serve C1, Q4–Q6 serve C2, Q7 serves C3, Q8–Q9 serve Q4, Q10 is the
attention check and the workload cross-reference.

### 5.6 Quiz (`quiz-v1`), after each half, project closed

Five items, two minutes. Answer keys are per project and live in
`web/src/study/answerKeys.ts`, generated from the build logs.

1. Which feature was added and then deliberately removed?
   *coursecraft*: senior priority enrollment (E16). *confplan*: speaker-priority registration.
2. Which came first: conflict detection, capacity limits, or the waitlist?
   Capacity limits (E6 before E7 before E11), both projects.
3. Did the previous maintainer work alone?
   No. An AI assistant did part of the work.
4. Name one change that was later corrected, and say what the correction was.
   Accepts any of: E5 id reuse, E13 back-to-back, E20 export escaping, E16 revert.
5. Which single change touched the most unrelated concerns?
   E8, the search commit that also carried the day-parsing fix.

Each item is scored 0 or 1 by the facilitator against the key, and each carries
a confidence slider. Item 4 accepts several answers on purpose: a quiz that only
accepts the answer we were fishing for measures our fishing.

### 5.7 Summary task (`summary-v1`), after each half

Three minutes, project closed, typed and spoken. Prompt:

> Without looking at the project, tell the story of it. What was built, in what
> order, what went wrong, and what was undone?

Scored later in the dashboard against the 22-episode ground truth as three
numbers:

- **Coverage.** Episodes mentioned, 0–22. Checkbox grid, one row per episode.
- **Causal links.** Correctly stated because-relations between episodes, counted.
  ("The waitlist stopped being needed once the registrar took it over" is one.)
- **Misconceptions.** Confidently stated claims that are false, counted.

Coverage alone rewards listing. The three together separate "remembered a list"
from "built a theory", which is the RQ3 distinction and the reason Naur is cited
at all. Two coders, 25 percent double-coded, negotiated agreement per McDonald
et al.

### 5.8 Preference and closing (`preference-v1`), end of session

Forced choice per archetype, with the two setups named A and B:

- Finding when and why something changed
- Taking a feature out without breaking things
- Finding what caused a regression
- Working with the AI assistant

Each: Setup A / Setup B / no real difference, plus a free-text why. Then overall
preference, and "would you want this on your own projects" per setup on a 7-point
scale. The archetype-level forced choice is the most defensible preference
measure here, because a single overall preference on n=12 is one bit times
twelve.

### 5.9 Interview

Facilitator-side, in the dashboard, timestamped notes against fixed probes:

- What did you trust, and what did you check?
- Where were you lost?
- What did the history hide, and what did it show?
- What did you wish you could ask the history?
- How did you decide what to hand to the assistant?

The fourth probe is asked **before** the participant compares the setups. Both
pilots answered it with something close to what sgt does, one of them from
inside the git half, and that is a finding worth protecting from contamination.

## 6. What the machine records

The bundle records to a local append-only log first and uploads second. The
local file is the record of truth; upload is idempotent and retried. Nothing is
lost if the network drops, and re-running the sync twice cannot double-count,
because every event carries a content-addressed id.

### Sources

| Source | Mechanism | Gives |
|---|---|---|
| Claude Code prompts | `UserPromptSubmit` hook | verbatim prompt, timestamp, session |
| Agent tool calls | `PreToolUse` / `PostToolUse` / `PostToolUseFailure` hooks | tool name, command, file, success |
| Agent turn boundaries | `SessionStart` / `Stop` / `SessionEnd` hooks | turn latency, turns per request |
| Participant's own commands | PATH shims for `git`, `sgt`, `pytest`, `python` | argv, exit code, duration |
| Repo state | `git rev-parse` + test run at each request boundary | tree hash, tests passing |
| Heartbeat | 30-second ping | liveness on the experimenter's screen |

Hooks run with `async: true`, so telemetry cannot make the participant's
assistant feel slow, and a broken hook cannot block a session.

### The action taxonomy

Raw events are classified into nine categories. Everything downstream, including
the n-gram analysis, works on this alphabet.

| Category | Includes |
|---|---|
| `orient` | `git log`, `git status`, `sgt log`, `sgt now`, `sgt status` with no target |
| `inspect` | `git show/blame/diff/bisect`, `sgt show/why/recall/diff` against a named thing |
| `search` | `grep`, `rg`, `find`, `ls`, reading a source file |
| `prompt` | a message sent to the assistant |
| `agent_edit` | `Edit`, `Write`, `NotebookEdit` by the assistant |
| `manual_edit` | a tracked file changed with no agent edit accounting for it |
| `history_op` | `git revert/reset/rebase/cherry-pick/checkout`, `sgt revert/restore/save/split/merge` |
| `verify` | `pytest`, running the app, `git diff` after an edit, `sgt drift` |
| `recover` | `git reflog`, `git reset --hard`, `sgt restore` after a failed op, bundle re-unpack |

`manual_edit` is inferred, not observed, and is marked as inferred wherever it
appears. `verify` deliberately requires *after an edit*: `git diff` before a
change is orientation, the same command after one is checking. Any taxonomy that
ignores position mislabels roughly a third of diffs, which was visible in the
pilot logs.

### Derived measures

Per request, per condition:

- prompts sent, and mean prompt length
- **prompt specificity**: does the prompt name a commit sha, an intent id, a
  file, or a test? Four-level ordinal. This is the concrete form of "directs the
  agent precisely" and it is the measure most likely to show the representational
  difference, because sgt gives people nameable things to point at.
- **verification ratio**: `verify` events per (`history_op` + edit) event
- **time to first history operation**: orientation cost before acting
- **wrong turns**: a `history_op` followed within 120 seconds by a `recover`
- **collateral damage**: tests failing outside the target feature, from the scorer
- **action n-grams**: bigrams and trigrams over the category alphabet

### N-gram comparison

Bigram and trigram frequencies are compared between conditions with the
**weighted log-odds ratio with an informative Dirichlet prior** (Monroe, Colaresi
and Quinn 2008), with the pooled corpus as the prior. Raw frequency differences
and plain lift both over-report rare sequences, which on twelve participants
means they mostly report noise. Monroe's z-scores shrink rare sequences toward
zero and are the standard fix. Sequences with fewer than five occurrences across
the corpus are dropped before scoring, and the drop count is printed on the
figure rather than left implicit.

## 7. Analysis

- Primary model per measure: linear mixed model, fixed effects condition, order
  and project, random intercept per participant. Estimates, 95 percent CIs,
  standardized effect sizes. Where residuals misbehave, Wilcoxon signed-rank on
  participant means, or aligned rank transform for the factorial parts.
- Counts (collateral damage, wrong turns) get the matching generalized model or
  are reported descriptively with paired tests.
- All intervals from a **studentized bootstrap**, 10,000 resamples, resampling
  participants, not observations. The site computes these and shows the resample
  count on every figure.
- No p-value theater. Every quantitative difference in the paper is paired with
  the coded recording that explains it.
- Pre-register RQs, measures, and models on OSF before participant 1. This
  document is the pre-registration text.

## 8. The three figures

The figure budget for a CHI results section is three. These three, in this
order, carry C1 through C3 and Q4.

### Figure 1. What the two setups felt like

Diverging stacked Likert bars for the ten HLAC items, one panel per condition,
counts printed inside the segments, plus a right-hand panel of paired mean
differences with 95 percent studentized-bootstrap CIs. The reference is the
standard form used for this kind of within-subject perception battery.

It goes first because it is the only figure a reviewer can read in five seconds,
and because perception is the claim most people will test against their own
intuition.

### Figure 2. What people actually managed to do

Paired within-subject estimation plot, four archetypes across the top (R1, R2+R3,
R4, and collateral damage). Per archetype: every participant's two scores joined
by a line, condition on the x-axis, and beneath it the paired mean difference
with its bootstrap CI on a floating axis, Gardner-Altman style.

Twelve slopes shown individually is the honest way to plot n=12. A bar chart of
two means hides whether one person moved a lot or twelve moved a little, and on
this sample size that is the whole question. Collateral damage is on its own
panel with an inverted axis so that "up is better" holds across the figure.

### Figure 3. How the work was done

Two stacked panels sharing a legend.

- **(a) Where the time went.** One horizontal strip per participant-half, x is
  proportion of that half's active time, segments colored by action category,
  grouped by condition. Above each group, the aggregate: a stacked area of
  category share across normalized time. Individual strips give the
  distribution, the area gives the shape.
- **(b) What the sequences looked like.** Dot plot of the top discriminating
  action bigrams by Monroe z-score, sgt-leaning to the right, git-leaning to the
  left, with occurrence counts.

Figure 3 is the figure that makes the paper more than a benchmark. Panel (a)
shows *when* the two groups diverged, panel (b) shows *what pattern* differed,
and together they are the mechanism behind Figures 1 and 2. It is also the
figure that answers the reviewer question that sinks tool papers, which is "did
they just use the AI more".

Every figure exports to SVG with fonts as text, an ACM-column-width preset
(3.33 in single, 7.0 in double), and no rasterized layers.

## 9. Honest limits, stated up front

- Twelve participants sees large effects only. Reported as intervals, never as
  significance.
- Years of git against ten minutes of sgt. The asymmetry cuts against sgt, so a
  positive result is conservative and a null result is uninformative about the
  ceiling.
- Novelty effect runs the other way and cannot be fully removed. Mitigated by
  neutral naming and by never revealing authorship, and stated as a limit.
- Agent variance is part of the condition, not controlled away. The model
  version is pinned and every transcript is kept.
- Two small synthesized codebases and 45-minute halves. Theory building happens
  over weeks. The lab study answers first contact only, which is why the design
  doc recommends a field deployment as the companion.
