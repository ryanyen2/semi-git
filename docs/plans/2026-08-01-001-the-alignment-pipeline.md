# The alignment pipeline: conversation ↔ code as the signal that builds sgt's history

**Status: design, v2. Supersedes v1 of this file (git history) and the M1/M2/M3 structure of
`2026-07-31-002-intent-ledger-architecture.md` §6. v1 → v2: a five-persona review (2026-08-02,
§9) plus a deep literature pass (traceability/untangling, conversation NLP, statistical
alignment — sources inline) found v1's ALIGN design under-specified and its user model idealized.
Direction set by user 2026-08-02: the fallback ladder is dead — alignment is a staged inference
pipeline with calibrated confidence and abstention; the input model is messy-by-default; the
learning story must connect explicitly to the stability↔plasticity regime. The frame v1 got right
survives unchanged: conversation↔code is not a fourth product, it is a new signal into the
construction pipeline sgt already is.**

## 0. The corrections this doc encodes

Two designs got the *shape* wrong (002 shipped alignment as a peer object with management verbs;
the deleted untangle draft played word games with nouns). v1 of this doc fixed the shape and then
got two *contents* wrong:

1. **An idealized input model.** v1 claimed "the testbed sessions showed most turns name their
   symbols outright." The only captured corpus (`~/repos/sgt-testbed/.sgt/local/turns.json`) holds
   22 turns; 10 of its 14 hook-channel turns are stress fixtures ("post-fix concurrent 3", "same
   words twice", a 1MB string) and the ~4 contentful ones are clean scripted feature requests on a
   toy repo whose CLI commands happen to be its function names. That is a best-case sample of
   four sentences, and the broader evidence points the other way: 44% of commit messages in
   curated OSS projects lack the why or the what (Tian et al., ICSE'22, n=1,649, κ=0.91), 14% are
   empty and 75% under 16 words at SourceForge scale (Dyer et al., Boa). Real turns are
   backchannels ("continue", "ok"), corrections ("no, not like that"), deixis ("fix that thing",
   "make it faster"), compound asks, and agent-interleaved concerns. §1 characterizes the input
   honestly; every downstream stage is designed for that input, not the demo transcript.
2. **The ladder.** Fallback-on-failure (rung 0, else rung 1, else rung 2) is a caching strategy,
   not an inference architecture. It cannot *combine* signals (a weak lexical hit plus a strong
   temporal hit is worth more than either rung alone), it has no calibration (a "mid confidence"
   label with no error rate attached), no abstention semantics (what happens below the bar is
   undefined), and no way to learn from corrections. §3.2 replaces it with the staged pipeline the
   record-linkage and untangling literatures converged on: candidate generation → multi-signal
   scoring with learned reliabilities → calibrated three-region decision → gated LLM adjudication.
   The rungs survive as *signals inside* that pipeline; the ladder as control flow dies.

## 1. The input, characterized honestly

The alignment problem: given verbatim turns (captured live at tool boundaries) and fine-grained
ops (per-symbol change units), emit weighted edges (utterance(s) ↔ op) with calibrated confidence.
What actually arrives on each side:

**The words.** A minority of turns are clean statements of intent that name their objects. The
rest: backchannels and acks that carry no alignment signal; corrections that *invert* the previous
instruction's alignment (misread as fresh intent, they mint a phantom concern and poison the
grouping); pronoun/deixis references whose antecedent is a prior turn or the thing on screen, not
any token in the text; compound turns carrying two or three intents; commit messages like "done"
and "wip". Agent-authored notes paraphrase the user (channel `note`, already weighted below `hook`
— `sgt/intent/turns.py`). Low-information turns are evidence to *withhold*, not force-align.

**The ops.** Mining already untangles commits into near-atomic ops (4850/4879 ops on this repo
touch exactly one symbol — `cluster.py:commit_edges` docstring), so the alignment target is fine:
the op, not the commit. But sessions tangle: an agent interleaves planned work, drive-by fixes,
and uninvited refactors. Untangling precision decays nonlinearly with concern count — 79% at 2
concerns falling to 55–63% at 4 (Herzig & Zeller, MSR'13) — so alignment confidence must be
*conditioned on how tangled the session looks*, never one global threshold.

**Why this is still winnable.** Two structural facts:

1. **Capture at the boundary is the moat.** Post-hoc conversation↔commit recovery fails (DevGPT,
   MSR'24) — and DevGPT's own granularity ceiling is conversation↔artifact; *no public dataset
   aligns at turn↔op granularity at all*. sgt records live what everyone else guesses post-hoc.
   This also means there is no benchmark to import: §6 builds the evaluation corpus from our own
   dogfooding, and that build is a first-class deliverable, not an assumption.
2. **Cheap signals carry most of the load — measured, not hoped.** EpiceaUntangler (SANER'15),
   the closest prior art to live capture (IDE-event untangling), found 3 of its 13 voters —
   time-difference, intervening-event count, same-class — perform within 3% of the full model
   (95% same-developer, 88% cross-developer). Temporal + structural proximity is a *legitimate,
   calibratable* signal precisely when the words are junk. Its documented failure mode is ours
   too: surfacing every noisy fine-grained inference overloads the reader — filter before
   projecting.

## 2. The user's mental model (the whole contract)

> **sgt keeps history per feature. What I say while coding becomes that history's words. Every
> save shows me exactly where my words and changes landed. When something confuses me later, the
> history answers in my own words. When sgt names something wrong, I correct it — and it learns.**

Three honesty amendments to v1's version of this contract:

1. **The projection never bluffs.** When no words were captured, or none aligned above the bar,
   surfaces say so ("no words captured") instead of printing the temporally-nearest turn. A
   confidently-wrong echo at the highest-frequency surface teaches distrust faster than silence
   (§3.4 specifies the echo's states).
2. **"It learns" is bidirectional.** Renames and relabels are label ground truth *and* negative
   alignment evidence: a correction that contradicts the aligned words demotes the edges that
   produced the wrong name (§3.3). v1's version only learned names — a wrong utterance↔op edge
   kept feeding every rebuild with no lever against it.
3. **Confusing sessions are the target, not the remainder.** The LLM adjudicator (§3.2-G) exists
   for tangled sessions — the exact place "the history answers in my own words" matters most. It
   is gated by its own misattribution evaluation, and by nothing else (v1 parked it behind the
   sharing milestone; that coupling is severed).

The interaction surface is unchanged from v1: save echo, `sgt log` lanes/chapters with zoom,
`sgt why <sel>`, and the existing correction verbs. Nothing to maintain, no queue to groom.

## 3. The pipeline

```
       CAPTURE                ALIGN                        LEARN                     PROJECT
  turns (verbatim,   →   staged inference:      →   aligned words improve   →   save echo · log/zoom
  local, keyed by        type→resolve→segment       construction: edges,        why · recall · residual
  chat/plan/sha)         →candidates→score           cuts, names — under
                         →decide→adjudicate          the stability regime
```

One direction of flow. Everything user-visible is a projection off the right end; everything the
user says enters at the left end; nothing in the middle is a surface.

### 3.1 CAPTURE — built, keep as-is

`sgt/intent/turns.py`: verbatim, local-only, content-addressed, channel-tagged (`hook`/`cli`/
`note`), keyed by chat-session / plan / sha, idempotent, lock-guarded. Zero user burden. The
channel and actor tags are load-bearing downstream: `hook` is the user's own voice, `note` is
agent paraphrase (weighted below), `cli` is harvested workflow text. Nothing here changes.

### 3.2 ALIGN — a staged inference pipeline

Seven stages. A–D are deterministic and cheap (they run at capture/save time); E–F are the
statistical core (cheap arithmetic, no model calls); G is the bounded LLM stage. Each stage names
its mechanism, its cheap tier vs. model tier, and the literature it is built on — these are
internalized mechanisms, not decoration.

**A. Type the turn.** Four classes: *intent-bearing*, *backchannel/ack*, *question*,
*correction/repair candidate*. This is the collapse the dialogue-act literature actually supports
— SWBD-DAMSL's 200+ labels were collapsed to ~42 because the tail is unlearnable, and a handful of
classes carry most mass; ISO 24617-2's lesson is dimensions, not one flat taxonomy. Cheap tier
(ships first): cue lexicons (negation markers, ack tokens), turn length, edit-distance and lexical
overlap against the immediately-preceding user turn — the same rule-features that hit 92% on the
analogous query-reformulation task with a decision tree (Huang, CIKM'09). Backchannels and
low-information turns ("done", "wip", "continue") are typed out of alignment entirely — recorded,
never force-aligned. Compound turns (negation + new content: "no wait, also handle the null
case") are flagged for G rather than mis-typed.

**Correction handling is the highest-leverage cheap stage.** A correction misread as fresh intent
poisons everything downstream. Typology from conversational repair (Dingemanse & Enfield): *open*
repair ("no, not like that" — no located target) invalidates the most recent alignment of the
active episode; *restricted* repair ("no, the parser one") and *candidate-offer* repair ("you mean
the JSON parser?") locate their target via stage B, then invalidate/re-attach that specific edge.
Detection of repair turns is tractable with modest features (94.6 F1 on a curated corpus —
EMNLP'25 "Mm, Wat?"; expect less on wild data, which is fine: the cheap filter routes candidates,
G adjudicates the ambiguous ones).

**B. Resolve references.** Before any lexical matching, resolve pronouns/deixis/ellipsis
("make it faster" → make *the parser from turn 12* faster). Two candidate pools, resolved against
their union (the two lineages of the reference-resolution literature): (i) the *conversation
chain* — entities mentioned in the current episode, recency-weighted, because later references
compress and get harder (PhotoBook, ACL'19: resolution degrades ~20pp from first mention to
later positions); (ii) the *workspace focus* — the open file, symbols touched by ops minted since
the last save, the current diff (the Bolt "put-that-there" principle: deixis resolves against
what is co-present, not just prior text). Short low-content references ("this", "that file",
"it") prefer the workspace pool. Discourse deixis ("revert that" pointing at the *previous
action*, not a symbol) resolves against episode/op-level chunks, not entity names. Cheap tier:
most-recent-compatible-entity heuristic. Model tier (G): LLM rewrite that must *cite its source
turn(s)* — the rewrite-as-edit framing (RUN, EMNLP'20) is what makes resolution auditable; a
rewrite without provenance is inadmissible as alignment evidence. Base-rate note: most turns need
no rewriting (Utterance ReWriter, ACL'19: EM 98% on no-op cases vs 56% on true rewrites), so a
cheap "needs resolution?" gate keeps this stage nearly free.

**C. Segment the session into episodes.** An episode = a contiguous-ish stretch of one concern —
the unit alignment actually attaches to (a turn rarely explains one op; it explains an episode
whose ops share a concern). Two mechanisms, composed: (i) *boundary scoring* — TextTiling's
depth-score formula (Hearst, CompLing'97: a boundary is a coherence *valley* relative to both
neighboring peaks, robust to gradual drift — coding episodes trail off, they don't stop) over a
cheap coherence signal: symbol/file overlap between consecutive turns' resolved references, plus
time gaps. The upgrade path swaps in a learned coherence scorer trained *self-supervised* —
adjacent pairs as positives, cross-session pairs as negatives (Xing & Carenini, SIGDIAL'21:
Pk 26.8 vs 40.5 for lexical TextTiling, with zero labeled boundaries) — behind the same
depth-score decision layer. (ii) *Interleaving* — sessions braid concerns, so episodes are not
strictly contiguous: each new turn either attaches to an open episode or starts one, an online
reply-to *pointer* formulation (conversation disentanglement: Kummerfeld et al., ACL'19; Yu &
Joty, EMNLP'20 — ~73 F1 link prediction with features that are exactly ours: time gap, lexical/
symbol overlap with each open episode, and a mention-memory of the files/symbols each episode has
recently touched). Repair turns never open episodes — they re-attach (stage A). Episode identity
across re-derivation uses Hungarian matching against the previous assignment — the same
label-switching fix `tree.match_identities` (Greene, θ=0.5) already applies to features.

**D. Generate candidates (blocking).** For each episode, the candidate op set — recall-first,
precision comes later: ops minted inside (or temporally adjacent to) the episode's span, ops whose
footprint overlaps the episode's resolved symbol/file mentions, ops one `requires`-hop from those.
An *ensemble* of cheap generators, unioned — a single "best" retriever starves the scorer of the
true link (the RRF lesson from LLM-reranker trace recovery, 2026; FRLink's filter-before-score,
IST'16). This is the entity-resolution architecture: blocking is a distinct, recall-oriented
stage whose only job is to not drop true pairs (Magellan/Ditto line).

**E. Score with learned signal reliabilities.** Each signal is a *labeling function* voting
match/no-match/abstain per (utterance-or-episode, op) candidate pair — not a rung to fall through:

- *key containment* (built): plan-match transcription (`match.py:confirm_match` →
  `reflect_planned_match`), `save -m` sha-keyed turns, session-task keys. Note honestly: even
  this "highest-confidence" signal is heuristic — plan matching is an overlap-coefficient
  threshold (0.3, `match.py:40`) over union-found n:m groups, with an ambiguous band already
  surfaced at save (`--resolve-plan`).
- *temporal containment*: op minted while episode active; graded by distance. (EALink's ablation
  warning, ASE'23: never *calibrate* with naive time-window negatives — "continue" lands 20
  minutes after the work it refers to; temporally-distant true pairs are common.)
- *symbol mention*: the resolved references (post-B) against the op's footprint. The extractor is
  specified, because this is where the false-positive profile lives: `_symbol_matches`
  (`rationale.py:198`) joins *structured* `path::name` strings — it cannot read prose. A prose
  mention counts as a symbol mention only when qualified: file + name co-mention, a dotted/`::`
  path, or a unique-in-repo identifier. Bare common tokens ("add", "list", "done" — ordinary
  English verbs that are function names in every CLI repo) contribute temporal-grade evidence
  only.
- *structural adjacency*: candidate op shares file/`requires`-chain with an already-aligned op of
  the same episode (pure structural signals top out well under 50% at op granularity — UTANGO,
  FSE'22 — a real signal to fuse, never a gate).
- *(later)* embedding similarity between resolved turn text and op content; *(G)* LLM judgment.

Combination is Fellegi–Sunter record linkage (JASA 1969), the exact formalism for this problem:
each signal i has m_i = P(fires | true match) and u_i = P(fires | non-match); a pair's score is
the sum of log-likelihood ratios Σ log(m_i/u_i). The m/u reliabilities are *learned without any
labels* from the agreement/disagreement structure of the signals over the unlabeled candidate
pool — EM (Winkler) or a closed-form label-model fit (Snorkel, VLDB'18; FlyingSquid, ICML'20).
That property — zero ground truth required — is what makes this fit sgt's cold start. Two
required disciplines: (i) *correlation correction* — symbol-mention and LLM-judgment read the
same text; naively summing their weights overstates confidence (Snorkel's structure-learning
failure mode); (ii) *complexity conditioning* — the session's estimated concern count (from C's
episode structure) discounts all scores per the Herzig–Zeller decay curve.

**F. Decide with abstention.** Fellegi–Sunter's decision rule is three-region, and that structure
is the point: **align** (score above the upper bar → edge written), **review band** (between bars
→ no edge; the pair feeds G's queue and §6's evaluation sampling), **no-align** (below → nothing,
and the turn contributes to the residual, §3.4). Thresholds are not hand-picked constants: at
cold start they come from the label-model's fitted posteriors; once corrections accumulate
(§3.3), they are set by selective prediction with a risk target (Geifman & El-Yaniv, NeurIPS'17)
/ split-conformal quantiles (distribution-free, recomputed cheaply as the calibration set grows)
— i.e., "at most X% of accepted alignments wrong" is a parameter we *set*, not an accident we
discover. Calibration of the score→probability map starts with temperature scaling (most
data-efficient — Guo et al., ICML'17), isotonic only if volume ever justifies it.

**G. Adjudicate with an LLM — bounded, gated, last.** Invoked per *ambiguous episode decision*,
never per pair (cost discipline: LinkAnchor, 2025 — agentic adjudication beats trained linkers,
Hit@1 0.863 vs 0.539, at ~$0.01/23s *per issue*; per-pair invocation is prohibitive at op scale).
Trigger: review-band mass in an episode, a concern-count estimate ≥ 2, or a user zoom on an
unaligned op. Form: multi-perspective — one pass over explicit structure (ops, footprints,
`requires`), one over implicit semantics (resolved turn text), a reviewer synthesis — which beats
single zero-shot calls by ~17% (ColaUntangle, TOSEM'25); the prompt frames the job as *per-op
intent inference*, not pairwise classification (Atomizer, ICSE'26). `null` is always allowed;
outputs land in the same E/F scoring as one more (high-m, learned-u) signal — the LLM is a
labeling function too, not an oracle. Cached like every LLM pass. **Its gate is its own
misattribution evaluation (§6) — nothing else**; error analysis says LLM untanglers over-split
~2:1, so the correction path stays merge-friendly.

**Output and store.** Weighted edges materialize in the existing rationale store discipline
(append-only, content-addressed, sha+fp anchors, supersession — `rationale.py`), renamed
internally "alignment table", with a required schema extension v1 hand-waved: records gain
`confidence: float`, `signals: [{name, value}]`, and `aligner_version`. Today's record has only
boolean `confirmed` (`rationale.py:87`), and `_rationale_id` excludes score-bearing fields while
`record_rationale` never overwrites — so a re-scored alignment would silently no-op.
**Re-scoring therefore always supersedes**: new record + `supersedes` relation, never identity
reuse. `confirmed` survives as the human-endorsement pin, orthogonal to `confidence`. Consumers
are the pipeline and the projections — never a management CLI.

### 3.3 LEARN — three injection points, one learning loop

Each injection point plugs into machinery that already has a socket. Each has an explicit gate —
including cuts, which v1 left ungated.

1. **Feature edges (cluster signal family #6).** Symbols co-aligned under one episode get a fused
   edge (`cluster._fuse`), `SIGNALS_VERSION` 2→3 (the version-bump recluster mechanism,
   `cluster.py:51`). Same discipline as the five existing families: per-group 1/(n−1) scaling,
   capped (an episode aligning >`MAX_FOOTPRINT`-scale symbol sets contributes nothing, like
   mass-edits), weight scaled by calibrated confidence, correlation-damped against the co-commit
   family (an aligned episode and its witnessing commits overlap heavily — double-counting the
   same evidence as two families inflates it). Only **align-region** edges feed; review-band and
   temporal-only evidence never reach the graph. *Gate:* correction-frequency against
   authored/pins ground truth (`authored.py`, `pins.py`) — the signal earns default-on by
   reducing how often users must correct. *Rollback:* the signal ships behind a flag inside
   SIGNALS_VERSION 3, so reverting is a flag flip + recluster, not a version war.
2. **Checkpoint cuts.** A confirmed plan session whose aligned ops fall inside a feature's runs
   is a boundary candidate: `W_SESSION` joins `W_SCOPE`/`W_GAP`/`W_NOVELTY` in the boundary
   scorer (`sgt/intent/segment.py:44-46`) — noting this is plumbing, not a constant-add: the
   scorer consumes commit-shaped `Run` data today, so session/alignment data must reach `Run`
   construction first. An episode switch is a weaker candidate. *Gate (new in v2):* chat-session
   switches are far more frequent than scope changes or 12-commit gaps, and `SEAM_BONUS` is
   explicitly PROVISIONAL in code (`segment.py:55` — its flicker sweep was deferred). So cuts get
   the same treatment as edges: a boundary-stability metric (flicker rate across rebuilds +
   chapter-relabel frequency), and the SEAM_BONUS sweep runs *with* the new signal present,
   before `W_SESSION` carries default weight. A bad cut moves the revert unit; it does not get to
   ship on vibes.
3. **Names.** Ladder of authority (unchanged): user pin → aligned confirmed words → LLM over
   aligned words + entities → commit subject. Concretely: `label.py:_leaf_prompt` gains an
   `Aligned intents:` line beside `Commit intents:`, and `theme_segment.label_prompt_for` extends
   from its two existing sources (committed prompts, sha-keyed `save -m` turns) to alignment-
   reason text. **The TAU_LABEL wall, resolved (v1 missed it):** label reuse is keyed on
   member-set drift only (`label.py:_cache_lookup` — weighted-Jaccard vs the generation-time
   member set, budget `TAU_LABEL`), so for a feature whose membership is stable, an enriched
   prompt is *never re-sent* — the user's words would never reach exactly the features they
   already know by a wrong name. Decision: the label cache's generation anchor extends to include
   a digest of the aligned-words evidence; when aligned words drift past their own budget
   (`TAU_WORDS`, swept like TAU_LABEL), the label is re-earned. Same regime, new anchor — the
   honest version of v1's "same anchor prices" claim.

**The learning loop (this is the continuous-learning story, and the rigidity↔consistency
tradeoff made explicit).** Two feedback speeds, deliberately different:

- **Fast, local, structural: corrections as constraints.** User corrections already have formal
  homes: `pins.py` realizes must-link as graph contraction and cannot-link as post-hoc
  enforcement; `authored.py` is the CRDT feature object; renames/relabels are label ground truth
  (witness-ordered LWW). The constrained-clustering literature endorses exactly this — with one
  amendment: prefer *soft, confidence-weighted* constraint costs over hard infeasibility as
  constraints accumulate over a long history (PCKMeans vs COP-KMeans, Basu et al.'04 /
  Wagstaff'01 — hard constraint sets eventually contradict; the current hard contraction is fine
  at today's pin volume, revisit at scale). Discipline: a rename is *label* evidence only — it
  never implies a link constraint. **New in v2 — corrections flow backward into alignment:** a
  rename/relabel or an authored-membership move that contradicts the aligned words writes
  negative alignment evidence (superseding records demoting the contradicted edges). That closes
  the loop v1 left open (alignment errors had no correction path at all) without any new verb:
  the existing correction surfaces are the interface; the zoom/echo word-projections additionally
  carry a dismiss affordance that writes the same superseding negative record — the alignment
  analogue of `sgt intent edit`, without the namespace.
- **Slow, global, statistical: corrections as calibration.** Each correction (and each
  confirmation implicit in *not* correcting a surfaced alignment — used cautiously) updates the
  per-signal m/u reliabilities as a Bayesian nudge against the label-model prior, and grows the
  calibration set that sets F's thresholds. **Never** treat the correction stream as an unbiased
  accuracy sample: users correct what they *notice*, which correlates with salience — it is
  informative missingness (the unbiased-learning-to-rank lesson, Joachims'17), fit for constraint
  injection and regularized reliability updates, unfit for headline accuracy claims.

**Stability↔plasticity, stated as what it actually is.** The tension "history must read
consistently across rebuilds, yet must incorporate new evidence" is the evolutionary-clustering
objective (Chakrabarti, Kumar & Tomkins, KDD'06): maximize snapshot quality minus cp × history
cost. sgt already implements instances of this: `STABILITY_ALPHA` is cp for clustering (anchor
vertices ≙ history-cost term — `cluster.py:56`), `SEAM_BONUS` is boundary hysteresis, `TAU_LABEL`
is a name-drift budget, Greene θ carries identity. v1 claimed these were "already tuned"; the
code says otherwise — STABILITY_ALPHA and SEAM_BONUS are marked PROVISIONAL with their sweeps
deferred; only TAU_LABEL was swept. So v2 states the honest program: (i) conversation-derived
signals enter under the same *objective*, and the deferred sweeps become part of P3's gate — the
constants get tuned with the new signal present, not asserted; (ii) the design direction for
those sweeps is *evidence-mass-proportional protection* (the ART/EWC principle: protect
consolidated structure in proportion to the evidence that pinned it down): a one-commit feature
should be cheap to reshape or rename; a 50-commit, user-pinned feature should demand strong new
evidence — i.e., per-cluster effective cp driven by op count, cumulative alignment confidence,
and pin status, unifying temporal smoothness and rename-resistance under one principle instead of
three unrelated constants. That is the rigidity↔consistency dial, named, measurable, and owned by
the P3 evaluation.

### 3.4 PROJECT — read-only; every surface has an honest empty state

- **Save echo (P1 deliverable), states specified** (v1 designed only the happy path):
  `porcelain.py` already prints the feature allocation (`→ {label} ({handle})`, ~`porcelain.py:
  415`); the echo adds the words and, when available, the chapter. States: (a) rung-0/key-
  contained words (plan step, session task, `save -m`) print plainly, length-capped; (b) words
  from scored alignment print only after P2's gate passes, marked as inferred; (c) no qualifying
  words → `· no words captured` — explicitly, never the temporally-nearest turn; (d) agent-
  authored `-m` text is marked by channel (the paraphrase-laundering concern lands at the trust
  surface too); (e) multi-feature saves keep the existing per-feature lines; words print once per
  save. **Chapter honesty:** chapter assignments are persisted only by on-demand `build_segments`
  (`theme_segment.py:290`), so the echo prints `@ chapter` only when the persisted segmentation
  covers the commit — no segmentation runs in the save path, and no stale chapter is guessed.
- **Log/zoom (new render, existing data):** `intent_view` (`api.py:1938`) already computes words,
  reasons, and `claude --resume` handles per atom; the TUI renders none of it. Chapters in the
  zoom carry their words — with the dismiss affordance (§3.3) on each word-attribution.
- **`sgt why <sel>`:** stays, extended: today's resolver takes op-ids and symbols only
  (`verbs.py:resolve_target`) — the sha selector the contract promises is *new P1 work* (map a
  commit sha to its atoms' ops via provenance), replacing the sha-addressed read that dies with
  `sgt intent show`.
- **MCP `sgt_recall` — kept, with a stated contract** (v1 never mentioned it; it is a live,
  shipped, agent-facing surface — `mcp/server.py:tool_recall`). It remains a named projection of
  the alignment table: symbol-scoped live rationale + open intents, called internally at plan
  intake. Contract changes with the pipeline: it serves rung-0/confirmed records and align-region
  edges *above a confidence floor* only — review-band and temporal-only edges never become
  constraint testimony steering an agent (`recall()` today has no confidence filter —
  `rationale.py:213` — the extension in §3.2's schema makes the filter possible). 002's read-time
  liveness discipline (demote rationale whose code was reverted) is restated here as a kept
  invariant and rides the same read path.
- **The residual — two kinds, only one of which is "needs attention"** (v1 conflated them):
  *plan-derived open intents* (unfinished steps from `abandon`/sweep — they carry a
  `predicted_fp`, so overlap-retire works) surface in `sgt log --summary` ("what needs
  attention", `inspect.py:504`) as stated-but-never-landed, auto-retired by later footprint
  overlap (a landed op-set covering the predicted footprint supersedes the open record), by
  supersession, or by age (default 30 days, stated in the summary line, adjustable later if real
  usage argues). *Chat-derived unaligned turns* (typed intent-bearing but below the align bar)
  are **not** "never landed" — under rungs 0–1 they are dominated by alignment misses — so they
  never enter the needs-attention list; they are the review band: G's queue and §6's sampling
  pool, visible only under zoom. One dismissal affordance survives on the summary surface (a
  dismiss on a residual line writing the superseding record — the function of `sgt intent done`
  without the groomed queue); auto-retire ships *before* the open/done verbs die (§5 P1
  precondition). Reflexion's lesson (persisted episodic residual) without a queue the user owns.

## 4. Kill list — explicit, amended

- The name **"intent ledger"** and the concept of a user-facing metadata object.
- **`sgt intent` namespace**, with every disposition named (v1 left orphans): `list`/`show` fold
  into log/zoom (sha-addressability moves to `sgt why <sha>` — §3.4); `build` remains as the
  segmentation write-path verb, renamed out of the namespace (`sgt checkpoint build`, exact name
  bikesheddable at P1); `relabel` folds into `sgt feature rename` accepting `<feature>@<n>`
  checkpoint refs; checkpoint `revert` already rides `plan_revert_op_set` and moves with
  relabel's surface; `open`/`done`/`edit` die — replaced by auto-retire + the dismiss affordance
  (which preserves `edit`'s function: a superseding, confirmed, human record); `record` survives
  only as the hidden hook entry-point string (installed hooks reference it verbatim).
- **Themes** (`theme.py` overlay as a surface): superseded by checkpoints — themes grouped
  cross-feature by commit-time topic, a coarser cut of the same intent axis checkpoints now
  serve feature-scoped; revert-by-group demotes to `sgt advanced` until deleted.
- **M1/M2/M3 as milestones.** M1's *data* survives (capture, key-containment reflection, store);
  its *surface* dies. M2's content is split: committed-tier promotion stays gated on hardening
  001 Phase 1.2 (in flight on `main`); recall is kept (§3.4). M3(a) is dissolved into §3.2-G;
  M3(b) is promoted to Learn-point 1.
- The three-axes / three-nouns framing of the deleted untangle draft.
- **v1's ladder framing** and the claim it rested on ("most turns name their symbols outright").

Migration note: existing `open: true` records written by 002's shipped verbs are adopted by the
auto-retire machinery (they carry `predicted_fp` and retire by the same overlap/age rules); no
record is stranded unretirable.

## 5. Staged delivery — each stage independently shippable, each with a gate

- **P1 — Legibility, honestly scoped.** Save echo (all states from §3.4, including `no words
  captured`), log-zoom words, summary residual (plan-derived only), `sgt why <sha>` selector,
  auto-retire (overlap + age) — a stated precondition for deleting `open/done/edit` — and the §4
  surface kills/renames. Scope honesty (v1 overclaimed): P1 delivers the contract for the
  plan-loop and `save -m` paths; *chat* words reach surfaces at P2 — the chat path's P1
  deliverable is its truthful empty state, not its words. No new inference. Coordinated with the
  hardening work landing on `main` (001 Phase 1.2 series) to avoid churning the same files.
- **P2 — The alignment core (stages A–F).** Deterministic typing/resolution/segmentation +
  candidate generation + FS/label-model scoring + calibrated three-region decision. **Corpus
  precondition (new):** the current testbed corpus (22 turns, mostly fixtures) cannot gate
  anything. P2 begins with a capture-first dogfooding period — sgt developing sgt, plus the
  testbed driven through real (not scripted) sessions — until the corpus holds on the order of
  hundreds of intent-bearing turns across ≥ 3 repos. *Gate:* on author-judged samples (§6):
  alignment precision in the align region, coverage (fraction of intent-bearing turns receiving
  any align-region edge), and calibration error; the pass bars are set from the first corpus
  batch and written into this doc before P3 starts — a gate without a number is not a gate.
- **P3 — Learning.** In order of evaluability: names (cheapest — judged against current
  diff-derived labels; includes the TAU_WORDS anchor decision from §3.3), cuts (`W_SESSION`
  behind the boundary-stability metric + the SEAM_BONUS sweep), edges (SIGNALS_VERSION 3,
  flag-guarded, default-weight low, judged by correction-frequency against authored/pins; flag
  flip as rollback). The stability sweeps (α, SEAM_BONUS — provisional in code today) run here
  with the new signal present.
- **P4 — Adjudication (decoupled from sharing — v1 coupled them for no stated reason).**
  §3.2-G behind its misattribution gate (§6), triggered by review-band mass / concern-count /
  user zoom. Independent of any storage work.
- **P5 — Sharing.** Committed-tier alignment store post-001-Phase-1.2 (unchanged gating), reasons
  reviewed before first push. Recall's committed-tier reads inherit the same confidence floor.

## 6. Evaluation — corpora, metrics, and what the numbers may mean

No public dataset exists at turn↔op granularity (DevGPT stops at conversation↔artifact), so the
corpus is ours to build and the numbers are ours to earn:

- **Corpus.** Dogfooding capture (P2 precondition) with periodic author-judged samples: for a
  sampled (episode, op) decision, the author records the true alignment. Weak-supervision
  agreement (signals concurring) bootstraps candidate labels but never substitutes for the judged
  sample. The correction stream is *not* an accuracy sample (salience bias — §3.3); it feeds
  constraints and calibration only.
- **Metrics.** Per-region: precision in the align region (the number that gates LEARN), review-
  band volume (the cost of abstention), coverage over intent-bearing turns (the product claim),
  expected calibration error (the honesty of the confidence itself), and — for G — misattribution
  rate on tangled-session samples, split by estimated concern count. For cuts: boundary flicker
  across rebuilds + relabel frequency. For edges: user-correction frequency against authored/pins.
- **Priors from the literature, used as expectations, not results:** cheap-tier ceiling ≈ 88%
  (Epicea cross-developer); decay to 55–63% as concern count reaches 4 (Herzig–Zeller); learned
  semantic scorers swing >10 points on language cleanliness alone (T-BERT: MAP .99 on
  clean-docstring projects vs .86 on noisy ones — a "fix that thing" session lives at the low
  end); LLM adjudication 69–93% on cleaner benchmarks than ours, over-splitting ~2:1
  (ColaUntangle). If our judged numbers land far above these, suspect the corpus before
  celebrating.
- **Follow-up flagged:** SWE-chat (arXiv 2604.20779) stores agent-session logs on repo branches
  keyed to checkpoints — the closest published analog to sgt's capture; fetch and absorb its
  method before P2's corpus design finalizes.

## 7. Related work, internalized

Ordered by what each contributes to this design, not by proximity of topic. **Alignment as record
linkage:** Fellegi–Sunter '69 (three-region decision, per-signal reliabilities) + Snorkel/
FlyingSquid (reliabilities learned without labels) + selective prediction/conformal (thresholds
with guarantees) form §3.2 E–F; entity-resolution pipelines (Magellan/Ditto) give the
blocking/scoring split (D). **Conversation side:** disentanglement (Kummerfeld; Yu & Joty) gives
the online pointer/episode model; TextTiling + Xing & Carenini give boundary scoring with a
label-free upgrade path; SWBD-DAMSL/ISO 24617-2 justify the 4-way turn typing; conversational
repair (Dingemanse & Enfield; "Mm, Wat?" EMNLP'25) and query-reformulation detection (Huang '09)
ground stage A's correction handling; IUR (Utterance ReWriter; RUN) and reference-resolution
(PhotoBook; put-that-there; CODI-CRAC discourse deixis) ground stage B; intent induction (DSTC11
Track 2; Deep Aligned Clustering's Hungarian stability trick) grounds C's episode identity.
**Code side:** tangled-change untangling (Herzig & Zeller's decay curve; EpiceaUntangler's
cheap-voter result and overload warning; Flexeme; UTANGO's structural ceiling; SmartCommit) plus
LLM untangling (ColaUntangle's multi-perspective consultation and over-split bias; Atomizer's
intent-oriented framing) shape E's signal set and G's form; trace-link recovery (FRLink;
DeepLink; T-BERT's transfer recipe and cleanliness spread; EALink's correlation term and
negative-sampling caution; LinkAnchor's per-decision agentic pattern; RRF fusion) shapes D and
the cost discipline. **Memory systems** (Generative Agents; A-MEM; Structurally Aligned
Subtask-Level Memory '26) validate the two-layer turns→alignment shape keyed to code-structure
units; Meta-Manager (CHI'24) remains the closest projection-surface prior art — a side database
queried by a tool, where sgt's same data *constructs* the history users navigate. **Learning:**
evolutionary clustering (KDD'06) formalizes the stability objective; ART/EWC give
evidence-proportional protection; PCKMeans gives soft constraints; unbiased-implicit-feedback
(Joachims '17) and machine teaching (Simard '17) discipline the corrections loop. Capture-at-the-
boundary over post-hoc recovery: DevGPT (MSR'24), EpiceaUntangler (2015, pre-LLM).

## 8. Open questions

1. TAU_WORDS (the aligned-words drift budget for names, §3.3): anchor at the generation-time
   words like TAU_LABEL anchors at generation-time members? Sweep alongside the α/SEAM_BONUS
   sweeps in P3.
2. Save-echo quiet mode for agent-driven saves (an agent saving 20× prints 20 lines into its own
   transcript). Lean unchanged from v1: keep — the transcript is where provenance should be
   visible to the *next* reader; revisit with P1 telemetry.
3. Concern-count estimation (the complexity conditioner in E): from episode structure alone, or
   also op-graph shape (footprint dispersion across features)? Decide during P2 with corpus data.
4. Does the negative-evidence writeback (rename contradicting aligned words) need a strength
   ladder (full demotion vs. down-weight), or is supersession binary enough at P3's volumes?
5. The correlation structure between the alignment edge family and the co-commit edge family
   (both witness "these changed together"): fixed damping factor, or estimated from the label
   model's correlation learning?

## 9. Review record (2026-08-02, five parallel reviewers + literature pass)

Five personas (coherence, feasibility, product-lens, scope-guardian, adversarial) on v1; 25
findings, 22 actionable after synthesis; four independently corroborated by 2–3 personas.
Incorporated: sgt_recall kept with confidence-floor contract (scope+product+adversarial — §3.4);
save-echo states incl. explicit no-words (product+adversarial — §3.4); testbed claim retracted,
corpus precondition added (product+adversarial — §0/§5); auto-retire as P1 precondition + dismiss
affordance survives (feasibility+product — §3.4/§5); confidence schema extension + supersede-on-
rescore (feasibility — §3.2); alignment correction path, bidirectional learning (adversarial —
§2/§3.3); prose→symbol extractor specified (adversarial — §3.2-E); cuts gate + provisional-
constant honesty (adversarial — §3.3); TAU_LABEL wall resolved via TAU_WORDS (product — §3.3);
rung-2 decoupled from sharing (product — §5); residual split into plan-derived vs unaligned
(adversarial — §3.4); names staging clarified (coherence — §5 P3/P4); overlap-retire and age
defaults defined inline (coherence — §3.4); `sgt why <sha>` as new work, relabel/build/revert
dispositions (feasibility — §3.4/§4); label_prompt_for baseline corrected (feasibility — §3.3).
Literature pass: three deep-dive tracks (SE traceability/untangling; conversation NLP;
statistical alignment/weak supervision/continual learning) — mechanisms internalized into
§3.2/§3.3/§6 rather than cited as a wall; `allinone.md` remains the raw search log.
