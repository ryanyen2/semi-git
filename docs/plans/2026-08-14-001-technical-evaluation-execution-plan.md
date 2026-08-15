# Technical evaluation: execution plan

Date: 2026-08-14
Status: ready to execute. Commit this file before running anything; the commit hash is the
pre-registration timestamp for every decision in it.
Strategy behind it: `docs/design/2026-08-14-technical-evaluation-plan.md`
Study it protects: `docs/design/2026-08-09-chi-user-study-design.md`

## How to read this document

Each work package (WP) is a card a subagent can run without further context: preconditions, steps,
artifacts with exact paths, validity checks the runner must pass before declaring success, and
stop-and-ask triggers that end the run and escalate to a human. Decision gates (G1..G6) are
zoom-out points where a human reads the ledger and decides; no agent proceeds past a gate alone.

The two claims being protected, in order of importance:

1. **Validity.** The record participants see is accurate, and not only because we built it.
   (Phase 1: WP-V1..V4. This is what lets the user study mean anything.)
2. **Agentic usefulness.** The record does not hurt an agent on ordinary work and helps on
   history work. (Phase 2: WP-A1..A2. Two sentences in the CHI paper; a section in an ICSE follow-up.)

## Rules that hold for every WP (anti score-hacking)

These are not suggestions. A result produced in violation of any of them is discarded.

- R1. **Frozen system.** Everything runs against sgt v0.1.0, MINER_VERSION 8, token-Jaccard
  threshold 0.80 (`sgt/core/identity.py`), shipped defaults, no flags tuned per run. If a bug
  found during evaluation forces a mining change, MINER_VERSION bumps, the fix is logged in the
  ledger, and every completed WP re-runs. No silent re-runs.
- R2. **Primary metrics are declared here, before data.** Each WP names one primary metric.
  Everything else is secondary or exploratory and is labeled that way in the paper. No promoting
  a secondary metric to headline after seeing numbers.
- R3. **No post-hoc exclusion.** Every repo, session, task, or run excluded after selection is
  logged in the ledger with the reason and counted in the paper ("we excluded N for reasons X").
  Selection scripts use fixed seeds committed with this plan.
- R4. **Both error directions, always.** Wherever precision and recall exist, report both plus
  the raw counts. Never report F1 alone. ICSE reviewers read F1-only as concealment.
- R5. **Writer and referee are different agents.** The agent that runs a WP never verifies it.
  A separate referee pass (see "Referee protocol") recomputes every headline number from raw
  artifacts before the WP is marked done.
- R6. **Failed and boring runs are results.** Log every run including crashes and ties. "All 10
  seeds tied" goes in the paper as written.
- R7. **Ledger.** Every WP appends to `docs/eval/ledger.md`: date, command, sgt commit, input
  hashes, artifact paths, headline numbers, anomalies. The ledger is append-only.
- R8. **No tuning on evaluation data.** If any pipeline piece needs tuning (e.g., the transcript
  segmenter), tune it on the `~/repos/sgt-study/_trial*` repos only, freeze, then touch the
  evaluation data. Record the freeze in the ledger.

## Artifacts directory

All outputs under `docs/eval/`:

```
docs/eval/
  ledger.md               append-only run log
  v1-census/              per-episode comparison tables, discrepancy list
  v2-transcripts/         extracted ground truth, per-repo metrics, labeled sample
  v3-corpus/              repo list, per-repo results, failure taxonomy
  v3b-acb/                AtomicCommitBench adaptation + results
  v4-robustness/          sequence logs, violations
  a1-agents/              per-run results, transcripts, scorer output
  a2-plan-arm/            declared-vs-inferred comparison
```

Raw agent transcripts and repo snapshots that are too big for git go under
`~/repos/sgt-eval-artifacts/` with their sha256 recorded in the ledger.

---

## Phase 0. Freeze (half a day, one agent)

WP-0 steps:

1. Record in the ledger: sgt commit hash, `sgt --version`, MINER_VERSION, Python version,
   Claude Code version, model ID that agents will use, OS.
2. Tag the sgt commit `eval-freeze-1`.
3. Snapshot the four study repos (`coursecraft`, `confplan`, `baseline-*`) and record tag
   `study-start` shas and sha256 of tarballs.
4. Verify `scripts/score_study_repo.py` runs green on both study repos as shipped. If it does
   not, STOP: the scorer is the instrument for A1 and half of V1; fix it first.

Done when: ledger has the freeze entry and the scorer ran green twice.

---

## Phase 1. Validity: the record is trustworthy

### WP-V1. Census of the study repos against the build logs

**Question.** Is the record the participants will see correct, item by item?
**Primary metric.** Episode-level agreement: for each of the 44 scripted episodes across the two
active repos (28 + 26 commits including bookkeeping), does sgt's feature graph file that
episode's edits the way the build log says the work happened? Report agreed / disagreed / partial
with a per-item note. This is a census, not a sample: every item, every disagreement listed.

**Preconditions.** `~/repos/sgt-study/coursecraft` at `study-start` = `e7b2fb0`, confplan at its
tag; `sgt log --json` works in both (verified 2026-08-14); build logs
`docs/study/build-log-coursecraft.md` and the confplan build log are the ground truth.

**Steps.**
1. For each repo, dump `sgt log --json`, `sgt show --json` per feature, and the feature→op→symbol
   mapping. Save raw JSON to `docs/eval/v1-census/`.
2. Build the comparison table: episode (from the build log commit map) × sgt feature(s) its ops
   landed in. Flag: split (one episode scattered across features), lump (episodes merged),
   mislabel (feature name misleading vs. the episode's actual work), miss (ops absent).
3. The tangle checks, separately and explicitly: E8 and E15 in each repo. The build log says
   exactly which function belongs to which concern (e.g. E8 = `cli.cmd_search` vs. the
   lowercase-day fix in `slots.parse_slot`). Does sgt separate them? This is the paper's central
   mechanism shown on its own testbed; report it as 4 named cases, not a rate.
4. Task-answer derivability: for each of the six requests × 2 repos, can the ground-truth answer
   (from the build log "Subtask ground truth" section) actually be read off the sgt record?
   Write down the exact command sequence that surfaces it. 12 checks, all must pass.
5. Cross-check (human×agent): a second, independent agent re-derives the comparison table from
   the same raw JSON without seeing the first agent's table. Diff the two tables. A human
   adjudicates every diff line. This is the census's own reliability check.

**Validity checks before declaring done.**
- The known blemishes list in the build log (over-claiming plan records for E7/E9/E17, the E16
  revert leftover, finding 4) must appear in the discrepancy list. If the census "found" fewer
  problems than the build log already admits, the census is broken, not the tool.
- Raw JSON re-dumped at the end must hash identical to the start (nothing mutated the repos).

**Stop-and-ask triggers.**
- Any of the 12 task-answer checks fails → STOP. That is a task validity problem for the user
  study itself; the human decides whether to fix the task or the tool before any session runs.
- Episode agreement below 80% → STOP, this is a mining bug hunt, not an evaluation.

**Effort.** 1 agent-day + referee pass.

### WP-V2. Grouping accuracy against real agent transcripts

**Question.** On histories not built for this study, does sgt's inferred grouping match the
requests a developer actually made?
**Primary metric.** Pairwise F1 over symbol-edit pairs: same request (ground truth) vs. same
feature (sgt), with precision and recall reported per R4, plus ARI as secondary. Split-error
rate (one request scattered across features) is the named secondary, because merging consecutive
requests is often correct and splitting one request is the real failure.

**Data.** Three author repos with real Claude Code transcripts under `~/.claude/projects/`:
- `-Users-r4yen-repos-semi-git`: 24 top-level sessions (the 131-file figure includes subagent
  transcripts in `<session-id>/subagents/`; treat those as folded into the parent, below).
- `-Users-r4yen-Desktop-Research-CoDoc-repos-CodeNav`: 15 sessions. Requires `sgt init` on
  CodeNav first — this is the repo sgt has never seen, which is the point.
- `-Users-r4yen-repos-uist2026`: 7 sessions, same treatment.
Three repos, one of which sgt was developed in and two it never touched, is the honest scope:
say in the paper these are the authors' own repos, a case study at scale, not a random sample.

**Steps.**
1. Extractor: parse each top-level session `.jsonl`. Segment at user turns whose
   `message.content` is a string (human-typed requests; 21 of 525 user-type entries in the
   sampled session — most user-type entries are tool results, do not segment on those).
   Within a segment, collect `Edit`/`Write` tool calls (path, old, new). Fold subagent
   transcripts into the segment that spawned them. Output: request-id → list of file edits.
2. Map edits to symbols with sgt's own miner (same vocabulary as the system under test).
   Edits that map to no mined symbol (docs, JSON, etc.) are logged and excluded with a rate; if
   the match rate is below 70% for a repo, STOP and diagnose the extractor before computing any
   accuracy number.
3. Tune nothing here. If the segmenter needs heuristics (e.g., continuation phrases), develop
   them on `_trial*` repos per R8, freeze, then run.
4. Compute pairwise precision / recall / F1 and ARI per repo, and pooled.
5. Instrument ceiling (human×agent cross-check, pre-registered sampling): with a seeded RNG
   (seed 20260814), draw 100 request pairs per repo *before* looking at sgt's output. Two
   coders label "same piece of work or not": the human author, and an independent LLM judge
   given a written codebook (draft the codebook first, commit it to `docs/eval/v2-transcripts/`).
   Report agreement (Cohen's kappa) between human and judge; adjudicate disagreements; the
   adjudicated labels give the ceiling: how often the request boundary itself matches human
   judgment. sgt's F1 is read against that ceiling in the paper, per the McDonald et al. norms
   the study design already cites.
6. Error taxonomy: sample 30 wrong pairs (seeded), code them (split across features / lumped /
   identity break / extractor artifact). Extractor artifacts feed back into step 2's log, not
   into sgt's score — but the taxonomy table in the paper includes them so the reader sees the
   instrument's error share.

**Validity checks.** Referee recomputes F1 from the raw pair table; the pair table row count
must equal C(n,2) over the edit set minus logged exclusions; the seeded sample must be
reproducible from the seed.

**Stop-and-ask.** Match rate < 70% (step 2); human–judge kappa < 0.6 (the ground truth itself
is then too noisy to headline — escalate, likely demote V2 to secondary evidence); `sgt init`
on CodeNav fails (that becomes a V3-style robustness finding and V2 proceeds on two repos).

**Effort.** 2–3 agent-days + half a day of human labeling + referee pass. Highest-value WP in
the plan; start it first.

### WP-V3. External corpus: repos nobody here built

**Question.** Does the machinery work outside author repos at all?
**Primary metrics.** (a) init completion rate, (b) reconstruction rate — fraction of files whose
recorded ops regenerate disk bytes exactly, the corpus-scale version of the paper's "56 files"
number, (c) symbol-level coverage distribution. Time and memory are secondary.

**Selection protocol (fixed before running, per R3).** GitHub search, language:Python, stars
100–5000, pushed within 12 months, not a fork, license permits analysis. Order by the seeded
shuffle (seed 20260814) of the first 200 hits; take the first 30 that clone successfully; log
every skip. Add 5 repos whose recent history is agent-heavy (search for Co-Authored-By: Claude
trailers or aider/copilot markers in recent commits); report these separately.

**Steps per repo.** Clone, pin HEAD, `sgt init` under a 30-minute cap, `sgt log --summary`,
`sgt advanced fsck`, reconstruction check, one scripted edit + `sgt save`. Everything JSON to
`docs/eval/v3-corpus/<repo>/`.

**Output.** Distribution plots + a failure taxonomy (à la the "Beyond pip Install" reporting
style): every crash and cap-out classified. A bad distribution is a finding, not a failure of
the WP.

**Validity checks.** Referee re-runs 3 randomly chosen repos end-to-end and must reproduce the
numbers. Any repo whose numbers changed between runs → flag nondeterminism, which is itself a
LAW-0 violation worth reporting.

**Stop-and-ask.** >30% init failure → stop and fix before continuing the sweep (then re-run the
whole sweep under R1).

**Effort.** 1 day of scripting + unattended compute + referee pass. Parallelizes trivially.

### WP-V3b. AtomicCommitBench adaptation (external grouping validity)

**Question.** On a benchmark we did not build (Lin, Zhou, Li 2026, arXiv 2607.03332; 800 real
consecutive-commit episodes, 10 Python projects), how does sgt's grouping compare to published
agent baselines (0.03–0.46 ARI)?
**Primary metric.** ARI on their episodes, their definition.

**Adaptation, stated exactly (this paragraph goes in the paper).** Their task gives an agent a
squashed patch to decompose retrospectively. sgt does not consume patches; it mines history. So:
for each episode, construct the repo state with the episode's commits squashed into one, run
`sgt init`, and score sgt's feature partition of that squashed commit's ops against the original
commit boundaries. This tests the untangling machinery under their metric; it is not the same
task their agents ran, and the comparison row is labeled accordingly. If their harness or data
turns out not to support this cleanly, STOP and report why rather than forcing it — a
documented incompatibility is publishable; a bent comparison is not.

**Secondary use.** Their synthetic-vs-real gap (+0.333 ARI) is the citation for why this plan
contains no home-made synthetic tangling.

**Effort.** 2 agent-days including reading their harness. Run only after V2, and only if V2's
number is worth defending externally (gate G2).

### WP-V4. Robustness under random operation sequences

**Question.** Do the safety properties hold off the happy path? (This is the "it will not eat
your work" evidence, the thing an ICSE reviewer who has used research VCS tools will look for
first.)
**Primary metric.** Violations per 1,000 operations, by oracle, with recoverability (every
committed blob still reachable) required to be zero.

**Steps.** Generator over {save, revert, revert --keep-dependents, restore, undo, redo, feature
merge/split/rename, two-clone sync}, sequences of 20–50 ops, seeded, run against (a) the
`tests/laws/corpus.py` synthetic repos and (b) 5 repos sampled from V3. After every op, check
the existing law oracles (LAW-0/U/I/F/R/G/L in `tests/laws/`) plus round-trip and
recoverability. Known xfails (LAW-U pins, LAW-R) are expected and excluded from the count but
reported as designed-open. 10,000 ops minimum. Every violation gets a minimized repro committed
to `docs/eval/v4-robustness/`.

**Reporting.** Count and classify; each class either fixed (with test, under R1 re-run rules) or
reported open, the way `docs/study/sgt-findings.md` already does. Finding 4 (revert leaves file
on disk) is already known-open; the harness should rediscover it, which doubles as the harness's
own sanity check — if it cannot, the generator is not reaching the interesting states.

**Stop-and-ask.** Any recoverability violation → STOP immediately, human decides; that is the
one class that cannot ship as "open" in a paper about version control.

**Effort.** 2–3 agent-days + fixing time (budget it; it will find things).

### Gate G1 (after V1+V2, the zoom-out)

Human reads the ledger and decides:
- V1 census clean or honestly annotated, 12/12 task answers derivable, V2 F1 respectable
  against its ceiling → the user study may run; the validity section of the paper is writable.
- V2 split-error concentrated in one mechanism → decide fix-and-bump (re-runs everything) vs.
  report-as-limitation. Fixing is only worth it before the user study repos are frozen for
  sessions; after freeze, report.

### Gate G2 (after V3/V3b/V4)

Decide the paper's external-validity paragraph honestly from distributions, and whether V3b's
comparison row is defensible enough to include. Also the ICSE fork decision: if V3+V4 produced
rich material, that is the seed of the systems paper, and CHI gets the summary.

---

## Phase 2. Agentic usefulness

### WP-A1. History tasks, agent arms, on the study repos

**Question.** Does the record help an agent on the work it was designed for?
**Primary metric.** Collateral damage (tests newly failing outside the target feature), from
`scripts/score_study_repo.py`, exactly as the human study scores it — one table, humans and
agents side by side. Success, turns, tokens, wall clock are secondary. Candidate-set size — how
many commits/features the agent examined before acting, counted from transcripts — is the
mechanism measure (the Risse & Böhme-style explanation of *why* any difference exists).

**Design.** 2 arms × 2 repos × 6 requests × 5 seeds = 120 runs minimum (10 seeds for the
headline requests S2/S4 if budget allows). Arm G: repo + git + shell. Arm S: same + sgt MCP +
the three skills. Same pinned model, same prompt text (the participant-facing request wording
from `docs/study/participant-materials.md`, verbatim — no sgt-flavored hints in either arm),
same turn/time caps as the human sessions. Runs are headless; transcripts archived.

**Anti-hacking specifics.** Prompts committed before the first run. No prompt iteration after
seeing results; if a prompt is broken (agent misparses the task), fix once, log, restart the
whole grid. Report every seed. Ties are reported as ties (R6).

**Validity checks.** Referee re-scores 10 random runs from archived repo states; scorer output
must match. Spot-check 5 transcripts for arm contamination (arm G somehow invoking sgt — the
binary must not be on PATH in arm G's environment; verify in setup).

**Stop-and-ask.** Score variance across seeds so high that 5 seeds cannot separate anything →
escalate before burning budget on more grid; the answer may be "report the variance itself"
(citable precedent exists).

**Effort.** 1–2 days setup, unattended runs, 1 day analysis + referee.

### WP-A2. Declared vs. inferred (answers the paper's own open question)

Arm S+ on the same grid (or its S2/S4 subset): the agent additionally runs the plan loop
(`sgt_plan_intake` → checkpoints → `plan_done`). Measure grouping accuracy of the resulting
record with V2's metrics, declared vs. inferred. This turns Section 7.6's closing question —
which source should be authoritative — into a number. Secondary: does drift detection catch
injected plan deviations (run 5 sessions where the prompt deliberately diverges from the
declared plan mid-task).

**Effort.** 1 day on top of A1. Optional for CHI; the single strongest addition if aiming past
"accept" to "award".

### Gate G3 (after A1/A2)

Human decides how much of Phase 2 enters the CHI paper. Default: one table and two sentences
(non-inferiority on ordinary work was NOT run in this phase — that is the ICSE paper's WP;
say "agents with the record did no worse and examined K× fewer candidates" only if the data
says exactly that). Resist the pull to make CHI an agent-benchmark paper; the user study is
the headline, per the strategy doc.

---

## Referee protocol (applies to every WP)

A fresh agent, given only: this plan, the WP's artifacts directory, and the ledger entry. It
must (1) recompute every headline number from raw artifacts with independent code, (2) check the
WP's validity checklist item by item, (3) check R1–R8 compliance, (4) append a signed referee
entry to the ledger: numbers reproduced yes/no, discrepancies, verdict. A WP without a green
referee entry does not exist for paper-writing purposes. Referee disagreements go to the human,
never resolved agent-to-agent.

## Schedule and parallelism

Week 1: WP-0 → WP-V1 ∥ WP-V2 (independent; V1 needs the study repos, V2 needs transcripts) →
G1. WP-V3 scripting starts in parallel, compute runs unattended.
Week 2: WP-V4 ∥ WP-V3b (if G2-approved) ∥ WP-A1 setup. A1 grid runs unattended.
Week 3: A1 analysis, A2, G3, write the evaluation section.

Phase 1 alone (through G1) is sufficient for the CHI submission's validity section. Everything
after is additive.

## What the paper says if things go badly

Pre-committing to the honest sentences, so nobody is tempted to bend a number later:
- V2 F1 mediocre against a high ceiling → "the inferred grouping recovers request structure
  imperfectly (F1 = x against a ceiling of y); the census shows the study repos' records were
  individually verified, so the user study stands on verified records while the inference
  quality on unscripted histories is an open limitation."
- V3 coverage poor outside app-shaped repos → scope statement, matching Discussion 7.1.
- A1 ties → "agents performed comparably with either record; the benefit we measure is human."
Each of these keeps the paper honest and alive. None of them requires deleting a WP from the
ledger, which is the point of the ledger.
