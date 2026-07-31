# Intent ledger v3: ops-anchored rationale from conversation evidence (2026-07-31)

Status: proposed, design-level. v1 (conversation-first capture) and v2 (ops-anchored
reflection, pre-review) are in git history at this path. v3 incorporates a four-lens
review pass (adversarial / coherence / feasibility / scope — record in §8). Literature
grounding: `allinone.md` (six-query sweep, 2026-07-31). Code grounding: `main` @ d1b57a2;
all anchors below were verified against the tree by the feasibility review.

**v2 → v3 in one paragraph.** The review found two missing designs and one oversized
surface. Missing: (a) rationale anchored to op ids that are *scheduled* to churn —
`MINER_VERSION` is part of every op id (`sgt/core/op.py:40,193`) and hardening plan
2026-07-31-001 Phase 1.3 mandates a bump — so records now carry stable secondary anchors
(witnessing commit sha + footprint) and a rebind rule; (b) no liveness story — recall
could serve "current" rationale for reverted code — fixed with a read-time liveness join
against the ideal. Oversized: the schema shrinks from 12 fields / 5 relation types /
3-way confidence to 9 fields / 2 relations / boolean `confirmed`; the retention config,
share-mode config, `action` enum, and repo/feature-scoped standing decisions are cut;
seven phases collapse to three gated milestones; and the plan now builds on sgt's *own*
existing rationale pipeline (`sgt/intent/theme_segment.py`) instead of ignoring it.

## 1. The model

Three kinds of objects, in sgt's idiom of immutable facts below, rebuildable projections
above. **Evidence**: raw conversation turns ("episodes"), captured live at tool choke
points, append-only, local-only, kept indefinitely — deleting the fact while keeping the
guess is the one thing sgt never does, and the review showed any pruning heuristic
quietly cancels re-reflection (§8-P1). **Facts**: ops, exactly as today. **Rationale**:
small structured records — "these ops changed for this reason, decided by this actor,
based on this evidence" — derived by reflection, re-derivable *on the author's machine*
(teammates receive rationale as testimony, not as a projection they can rebuild; the doc
says so rather than pretending otherwise), allowed to say "unknown," pinned only by
explicit human confirmation. Retrieval builds no new graph: rationale hangs on ops and
features and traversal rides edges sgt already maintains. Liveness is also a projection:
whether a rationale is "current" is computed at read time from (supersession chain ×
subject-ops-still-in-ideal), never stored.

## 2. Literature (takes, compressed — full tables in `allinone.md`)

- **F1 Untangling pivoted from structure to intent** (UTANGO FSE'22 → Atomizer, TOSEM
  untangler '25-26). We differ in evidence, not kind: they reconstruct intent from diffs
  post-hoc on any repo; we reflect over the actual conversation plus live plan-match
  alignments, on sgt repos only. Stronger signal, narrower reach.
- **F2 Post-hoc conversation↔commit linking fails** (DevGPT, MSR'24): capture evidence at
  the tool boundary. Cuts both ways: DevGPT found intent smeared across turns — which is
  why reflection must segment and align, not assume one-turn-one-change.
- **F3 Memory systems converged on two layers** (Generative Agents; A-MEM; MemGPT):
  immutable episodic log below, derived consolidation above, re-runnable because the log
  is kept. v3 keeps the log unconditionally for exactly this reason.
- **F4 SE agent memory should key to code-structure units** (Structurally Aligned
  Subtask-Level Memory '26): rationale keys to ops/features; retrieval is link-following,
  not embedding search.
- **F5 Fulfillment is a spectrum** (MSR gap studies; Reflexion): open intents are
  first-class records, with the known cost of occasional nagging.

Closest prior art: Meta-Manager (CHI'24) — prompt provenance anchored to code ranges,
but editor-local, single-user, non-versioned, record-only. We are repo-native, synced,
reflective, and feed rationale back into labeling/clustering.

## 3. What sgt already has (the rails — review-verified)

- **Attribution spine.** `Attribution {sha, session, agent, plan}`, identity-excluded,
  union-merged (`sgt/core/op.py:118,218`); per-atom prompt resolution via three-key
  fallback (`_atom_prompt`, `sgt/api.py:1865`).
- **The existing rationale pipeline (v2 blind spot).** `sgt/intent/theme.py` +
  `theme_segment.py` already produce LLM label+rationale records per feature
  (`ThemeGroup`/`SegmentGroup` with `label`, `rationale`, content-hash caching,
  deterministic fallback, `source: llm|fallback`), committed and sync-safe
  (`intent_themes`/`intent_segments`, `sgt/state.py:109-117`), and already feed recorded
  prompts into checkpoint naming (`theme_segment.py:66`). Goal 1 (user-faithful labels)
  is validated by *feeding evidence into this pipeline*, not by building a parallel one.
- **Plan machinery — the alignment source.** `sgt_plan_intake` decomposes plan text into
  hollow ops (`sgt/loop/plan.py:187,209`); confirm matches ops to steps and stamps
  attribution (`sgt/loop/match.py:277,300`). Caveats the review surfaced: the rationale
  text and `claude_session_id` live in `plan_sessions.json`, not `plan_matches.json`, and
  `abandon()`/the 7-day sweep **delete** them (`plan.py:254,42,199`) — so durable episode
  capture at intake is load-bearing for everything downstream, and ships first.
- **Per-record committed stores + union sync.** `intent_prompts` single-dict G-Set merge
  (`sgt/intent/prompts.py:53`, `sgt/core/sync/resolve.py:123`); claims/proposals/reviews
  as the per-record-file alternative (`state.py:302+`, `_union_*` in
  `sync/materialize.py:56-97`). v3 uses the single-dict pattern (§4.6).
- **Dependency graphs.** Op `requires` chains; entity edges (`sgt/entities/graph.py`);
  feature-level rollup exists only over the *fused* graph (`feature_edges`,
  `sgt/lens/tree.py:602`) — recall v1 uses it as-is (§4.4).
- **Clustering + projection discipline.** Fused signals (`tree.py:576`; template
  `scope_edges`, `cluster.py:115`), prior anchors (`cluster.py:270`), `SIGNALS_VERSION`
  (`cluster.py:51`); Greene identity-carry (`tree.py:814`, THETA=0.5 at `tree.py:57`).
- **Tiering.** Committed artifacts travel; `.sgt/local/` never does (`state.py:77-198`).
  Note: hardening plan 001 Phase 1.2 moves committed state (explicitly including
  `.sgt/intent/`) into `refs/sgt/state` — v3's committed tier sequences **after** that
  (§6), not after 001 Phase 0 as v2 wrongly claimed.

## 4. Architecture

### 4.1 Evidence layer: turns (raw, local-only, kept)

**Naming (implementation, M1).** This unit is a **turn** in code (`sgt/intent/turns.py`,
artifact `intent_turns`), not "episode" — `episode` is already taken by
`sgt.tui.graph.episodes`, the history-rollup projection that groups co-commit clusters for
the TUI/editor, and reusing it would collide in exactly this domain. The prose below still
says "episode/turn" interchangeably for continuity with v1/v2; the code says "turn."

One turn = one captured utterance:
`{id (content hash over key_kind|key|actor|channel|text), key, key_kind: plan|session|sha,
seq, actor: human|agent, channel: hook|note|cli, text, ts}`. A flat `key`+`key_kind` pair
(not a nested dict) mirrors how `_atom_prompt` already joins by a single string key across
three namespaces. Stored as one local JSON dict `{id: record}` at `.sgt/local/turns.json` —
the `suggestions` content-addressed-dict discipline, not a directory of files (v2's shape
matched nothing). Never synced, ever — evidence has no share mode, hence no `merge`.
`channel` matters downstream: `hook` turns are verbatim human input; `note` turns are the
agent's *paraphrase* of the human and are weighted accordingly (§4.3, §8-D7).
Content-addressing (excluding seq/ts) makes re-capture idempotent, so a retried verb or
re-run hook never double-records.

**Zero user burden — harvest the workflow, never add a ritual (user directive, 2026-07-31).**
The load-bearing constraint: capture must never ask the user to type anything they would not
already type. Decades of empty commit messages (and this repo's own three-questions UX
history, which had to *reduce* save-time friction) say a "type your rationale here" prompt
goes unused. So intent is reconstructed from what the workflow *already produces* — the
prompts the user already gives the agent, the plan they already wrote, the commit message
they already type — not from any new input field. This kills v2's `sgt save --why`
outright; a `--why` flag is exactly the burden being rejected.

Capture points, all zero-burden (existing choke points; live capture, not transcript mining
— F2):
- **`sgt_plan_intake`** — the plan text the user already handed the agent (`plan.py:235`).
  Shipped M1 s1. Captured before the 7-day sweep can delete `plan_sessions` (feasibility F3).
- **`sgt session start --task`** — the task string, only if the workflow already passes one
  (`session.py:143`); no new prompt. Shipped M1 s1.
- **`sgt save -m <msg>`** — the commit message the user already writes, harvested as a turn
  keyed by the witness commit sha (reachable from the new ops' provenance). The default
  "sgt save" placeholder is *not* captured — only the user's real words. Shipped M1 s2
  (`porcelain.py` `_save`).
- **`UserPromptSubmit` hook (Claude Code)** — the primary agent-workflow human channel and
  the most faithful of all: it records each prompt *verbatim as the user types it to the
  agent*, their normal conversation, keyed by the `claude_session_id` already stored at
  intake (`plan.py:227`). Zero per-use burden; the one-time setup is itself zero-burden if
  `sgt` auto-installs its own hook at `init`. Later slice.
- **`sgt_checkpoint` note — reframed.** The agent contributes *timing/alignment* (which ops
  it just did, when), not a re-transcription of the user's words; the hook is the
  authoritative verbatim user voice. This also resolves D7 (agent paraphrase laundering
  into user voice) — with the hook as ground truth, a note is a `channel: note` alignment
  signal, weighted below hook turns, never mistaken for the user speaking. Later slice.

`sgt intent edit` survives only as an *optional correction* a user makes when they want to
fix a wrong guess — never a capture the system depends on for coverage.

**Capture liberally; keep everything.** v2's "hindsight relevance" retention (prune
uncited turns) died in review: citation is decided by the *current* reflector, so a weak
early reflector's misses would delete exactly the evidence a better later reflector needs
— the retention default silently cancelled the rebuildability premise (§8-P1). Evidence
is small text; there is no knob (`intent.retain` is cut). If volume ever hurts, a manual
`sgt intent gc --older-than` can exist later; nothing prunes by default.

**Honest scope note (§8-P3).** In a solo human loop with no agent, the hook never fires, so
coverage rests on the save-message harvest — real but only as rich as the user's commit
messages. In an agent loop the hook makes coverage dense and verbatim. Either way every
surface must degrade to "no recorded reason" gracefully rather than assume density; nothing
nags the user to write more.

### 4.2 Rationale records (the constrained schema)

The derived unit. Typed and controlled-vocabulary — enough structure to constrain LLM
generation and give humans clean fields to correct; deliberately short of controlled
English or logic programming (retrieve-and-traverse, not reason-and-conclude; an
entailment layer is a separate, unscheduled research track).

```json
{
  "id": "r-<content hash>",
  "subject": [
    {"op": "<op-id>", "sha": "<witnessing commit>", "fp": "<footprint digest>"}
  ],
  "predicted_fp": null,
  "open": false,
  "reason": "<short text, user's words where evidence has them; null = unknown>",
  "actor": "user" | "agent" | "mixed",
  "confirmed": false,
  "evidence": ["e-...", "..."],
  "relations": [{"type": "supersedes" | "fulfills", "target": "r-..."}],
  "ts": "...", "recorded_by": "...", "reflector_version": "1"
}
```

What was cut and why (§8-scope): the `action` enum (no consumer anywhere in the design),
`revises`/`depends-on`/`answers` relations (unused; `depends-on` also duplicated the
dependency graph this plan promises not to rebuild), the 3-way `confidence` enum
(`reason: null` *is* unknown; `confirmed` is boolean), and `scope: feature|repo` standing
decisions (a policy memo is a fourth goal not among the stated three, and it anchored to
the least stable identity in the system — parked with the entailment track).

Design points, each closing a review finding:

- **Stable anchors (§8-D1, the critical one).** Op ids embed `MINER_VERSION` and
  `requires`, so hardening Phase 1.3 *will* invalidate every stored op id. Each subject
  entry therefore also carries the witnessing commit `sha` (stable across miner bumps —
  commits don't re-hash when the miner changes) and a footprint digest. Read joins prefer
  `op` id; on miss (miner bump, requires-churn) they rebind via `sha`+`fp` against the
  re-mined store and rewrite the fast path. Feature membership is **not stored** — it is
  derived at read time from current `op_leaf`, so feature splits/merges/renames never
  dangle a record.
- **`reason: null` is first-class.** Hand edits and imports get honest unknowns, never
  diff-guesses. Surfaces show gaps as gaps.
- **`actor` with defined semantics (coherence #4, D7).** `user` = every cited evidence
  turn is `channel: hook|cli` human input; `agent` = the agent's own uninstructed choice;
  `mixed` = cited evidence includes both. Instructions that reach us only via checkpoint
  `note` are agent paraphrase: reflection may still emit `actor: user` but must cite the
  note, and labeling weights hook-channel evidence above note-channel (an agent
  systematically framing its choices as "per your request" should not fully launder them
  into the user's voice).
- **Append-only, superseded, never edited (coherence #6 wording fix).** Record *objects*
  never change; their *standing* does, via `supersedes`. Human correction
  (`sgt intent edit`) writes a new record with `confirmed: true` superseding the old.
- **Pinning, precisely (D5).** `confirmed` pins a record against *automatic*
  re-reflection only. A human may always supersede a confirmed record with another
  confirmed record — that is the un-pin path, and it dissolves the "earlier rubber-stamp
  permanently beats later evidence" asymmetry. The residual risk stands and is accepted:
  confirming a record endorses all its fields as displayed, including a possibly-wrong
  subject alignment; `intent edit` therefore always displays the full record before
  confirming.
- **Fork tie-break (D4).** Union merge can yield two unsuperseded tails (A edits, B
  re-reflects, both supersede r1). Read-time rule: `confirmed` beats unconfirmed; two
  confirmed → recency wins and `sgt why` shows both with a fork marker. No new sync
  machinery; the ambiguity is resolved at read, surfaced, and fixable by one more edit.
- **Open intents (coherence #2, feasibility F6).** No hollow-op retagging — hollows keep
  their existing delete-on-consume lifecycle. Instead, when `plan_done`/`abandon` leaves
  steps unfinished, reflection writes rationale records with `open: true`,
  `subject: []`, `predicted_fp` from the hollow, and `reason` from the step. Retirement =
  a superseding record with `open: false` and `fulfills` linking the fulfilling record,
  written by retroactive overlap match or by `sgt intent done <id>` (the escape hatch for
  nag-about-finished-work).

### 4.3 Reflection (the hard center, priced as such)

Reflection turns (session ops + session evidence) into rationale records. Triggers:
`land`, `plan_done`, `abandon`, and the stale-session sweep — "session close" is defined
as *whichever of these fires first*, which also covers crashed/walked-away sessions
(coherence #1; abandoned sessions get reflected from whatever evidence exists rather
than silently losing it). Reflection is batched, never blocks a verb, and degrades to
`reason: null` on budget rather than guessing.

Re-runnability, stated honestly: inferred records are rebuildable **on the machine that
holds the evidence** (the author's). Teammates receive them as testimony. Confirmed
records are pinned per §4.2. Identity across rebuilds carries by evidence-overlap Greene
matching; re-reflection that contradicts an old inferred record supersedes it.

Two paths, different difficulty:

- **Planned path — cheap, but gated, not "free" (P2).** For plan-loop sessions,
  `plan_matches` + `plan_sessions` give step↔ops alignment and step rationale text;
  reflection is mostly transcription. But it inherits every fuzzy-match error (the
  repo's own audit shows checkpoint op-sets exceeding their labels, 001-F16), and a
  wrong reason is worse than a missing one — it reads as confident testimony under the
  session owner's name. So M1 measures plan-match alignment precision on sampled real
  sessions **before** any rationale leaves the local tier (§6), and `sgt why` always
  displays the inferred/confirmed badge and `recorded_by` with an explicit
  "inferred by reflection" phrasing, not as the human's own words.
- **Unplanned path — the tangle (gated to M3).** A messy session mixes planned work,
  drive-by fixes, uninvited refactors. Mechanics: cluster the session's ops by leaf +
  `requires` adjacency; segment the conversation; align segments↔clusters with an LLM
  constrained to the schema and permitted `null`. This is conversation-side untangling —
  the same problem shape Atomizer solves diff-side — and it ships only behind its own
  misattribution-rate evaluation.

### 4.4 Retrieval: rationale over the existing graphs, with liveness

No new index: feature → member ops → subject joins over `op_leaf` + the anchor rule.

**Liveness join (D2 — the second critical fix).** "Current why" is *not* just the
unsuperseded tail. At read time, a record's subject ops are checked against the current
ideal (ancestry − exclusions): if all subject ops are excluded/absent, the record is
demoted to **historical** — shown in `sgt why` as "reason for code since removed," and
*never* served by recall as a live constraint. This closes the failure where an agent
re-honors "use in-memory cache per user request" a week after the user reverted the
cache. Because liveness is computed at read time, resurrection (001-F20/F25 paths)
automatically revives the rationale with the code — no write-side bookkeeping, no new
reflection trigger for reverts.

- **`sgt why <op|feature|symbol>`**: the existing verb (`sgt/cli/select.py:60`,
  `why_view` at `api.py:276`) currently answers "which feature owns this op and why" —
  the rationale display **extends** that view with a rationale section (append, not
  replace; feasibility F5 decision made explicit). Order: live rationale (badge, actor,
  recorded_by), then historical (superseded or code-removed), then evidence pointers
  (`sgt intent show -v` prints cited turns, author's machine only). Gaps stated: "no
  recorded reason (5 ops)".
- **`sgt intent open`**: open-intent records with predicted footprints; `sgt intent done`
  retires.
- **MCP `sgt_recall`** (beside `sgt_plan_intake`, `server.py:235`): input = planned
  footprint / features. Walk: the features themselves + one hop over `feature_edges`
  (the existing *fused* rollup, `tree.py:602` — accepted v1 tradeoff: path/co-commit
  glue admits some irrelevant neighbors; a structural-only rollup is a later
  optimization, feasibility F4 decision made explicit) + op `requires` chains. Output:
  live rationale per feature (historical explicitly marked "overturned/removed" so dead
  constraints are not re-honored), open intents overlapping the footprint, and — same
  clone only, since `plan_sessions.json` is local and sweepable (feasibility FYI) —
  `claude_session_id`s for `claude --resume`. Ranking: footprint-overlap × recency ×
  open-status. `sgt_plan_intake` calls it internally against cached tree state so intake
  latency stays bounded.

### 4.5 Labeling and clustering feedback

- **Labeling first, through the existing pipeline (scope #1).** M1 feeds rationale
  `reason` text (and, where absent, raw high-signal evidence) into
  `theme_segment.py`'s existing prompt as an added input — features get called what the
  user called them, with zero new LLM plumbing and zero clustering risk. This is the
  cheapest visible payoff and the first validation of goal 1.
- **`intent_edges` (M3, second bet).** Pairs of symbols whose ops share a live rationale
  get weight `α · c / max(1, n−1)` (c: confirmed=1.0, inferred<1; n = subject footprint
  size — the 1/(n−1) guard keeps huge agent footprints from swamping the graph, same
  rationale as hub suppression at `cluster.py:210`). Fused at `tree.py:576` behind
  `intent.cluster_signal`, `SIGNALS_VERSION` bump, off by default. Evaluation: authored
  features and pins are the user's explicit corrections — measure whether the signal
  reduces their frequency and check partition agreement against authored boundaries.
  Anchor-family injection (`_augment_with_prior`) from plan-match rationale is a
  follow-on if edges prove out.

### 4.6 Storage, sync, sharing

- **Format**: one committed JSON dict, `.sgt/intent/rationale.json`, id → record,
  registered in `state._ARTIFACTS` exactly like `intent_prompts` and union-merged with a
  `rationale.merge` mirroring `prompts.merge` (`prompts.py:53`, wired at
  `resolve.py:123`'s neighbor). Not one-file-per-record (v2's shape matched no existing
  discipline — feasibility F1 / scope #2; the single-dict pattern does the same G-Set
  job with actually-zero new plumbing). Supersession *adds* records; if the dict ever
  gets heavy, compaction of fully-superseded inferred chains is a later, author-local
  concern.
- **Tier and sequencing (D3 / feasibility F2)**: the *evidence* layer and *local-tier*
  rationale (M1) can ship any time — they live in `.sgt/local/`, outside every merge
  surface. The *committed* rationale artifact ships only **after hardening plan 001
  Phase 1.2** (state moves to `refs/sgt/state`), so it is born in the post-migration
  home and never joins the in-tree merge surface 001 is evacuating. Storage goes through
  the state-registry helpers so its physical home is 001's decision, not this doc's.
- **Sharing**: no `intent.share` knob (scope #3). Evidence never syncs, period.
  Rationale, once committed-tier, syncs like every other committed artifact. The
  public-repo question returns when the repo actually has a public remote or a second
  contributor; until then a config surface is speculation. One real residual, kept from
  v2: a short `reason` can leak sensitive *meaning* even with no secret string — the
  M1→M2 promotion step includes a review-before-first-push of accumulated reasons.

## 5. Edge-case ledger

| Case | Mechanism | Residual honesty |
|---|---|---|
| Clarification with no code of its own shapes everything after | Liberal keep-everything capture; reflection cites it for the ops it explains | Depends on the answer passing a capture point; a purely verbal aside is still lost |
| One session tangles plan + drive-by fix + refactor | M3 tangle reflection: cluster ops, segment talk, align, `null` allowed | The hard problem; gated, own misattribution metric; will sometimes misattribute |
| Ops with no conversation (hand edits, imports) | `reason: null`; surfaced as gaps | Coverage honestly partial, especially solo-human loops (§4.1 scope note) |
| Reason lives outside the repo ("client asked") | `sgt intent edit` → confirmed record | Only as good as someone writing it |
| Reasons expire conversationally (A→B for X; X overturned) | Supersession chains; recall marks overturned | Reflector-proposed supersessions are inferred-strength only |
| Reasons expire **by revert** (code removed, nothing said) | Read-time liveness join demotes to historical; resurrection revives automatically | Liveness is per-record all-ops-gone; partial reverts leave the record live |
| Miner bump / requires-churn changes every op id | `sha`+`fp` secondary anchors, read-time rebind | Rebind is heuristic if one commit minted several same-footprint ops (rare) |
| Concurrent supersessions fork the chain | Read-time tie-break (confirmed > inferred, then recency), fork shown in `why` | A fork can stand until a human notices the marker |
| Agent's own choices vs user's asks | `actor` semantics + `channel` weighting (hook > note) | Note-channel "per your request" can still partially launder agent framing |
| Unfulfilled work resurfacing | `open: true` records; overlap-retire; `sgt intent done` | Occasional nagging about finished work |
| Teammate receives rationale they can't rebuild | Stated model: testimony, not projection; badges + `recorded_by` always shown | Humans anchor on reason text; badges mitigate, don't cure (§8-P2) |

## 6. Milestones (three, each gated)

- **M1 — validate the bet, entirely local.** Zero-burden turn capture (§4.1: plan intake +
  session task **[s1, shipped]**, save-message harvest **[s2, shipped]**, then the
  `UserPromptSubmit` hook + reframed checkpoint note **[s3]**), keep-everything + planned-path
  reflection to *local-tier* rationale + `sgt why` rationale section + theme_segment label
  feed + `sgt intent done/open` (+ optional `intent edit` correction).
  **Gate:** measured plan-match alignment precision on sampled real sessions (the P2
  number v2 never asked for), and a label-quality judgment against current
  diff-derived labels. If precision is poor, stop here — nothing has been shared, and
  the evidence store still pays for future reflectors.
- **M2 — share and recall.** Requires M1 gate **and** hardening 001 Phase 1.2 landed.
  Promote rationale to the committed tier (born in `refs/sgt/state`), reasons reviewed
  before first push; MCP `sgt_recall` with liveness join + open intents +
  `plan_intake` integration. **Gate:** recall precision/usefulness in real agent
  sessions (does retrieved rationale change agent behavior for the better).
- **M3 — the two research bets, independently gated.** (a) Tangle reflection, gated on
  misattribution rate over sampled messy sessions. (b) `intent_edges` clustering signal,
  gated on the authored/pins evaluation. Either can fail without dragging the other
  down; M1/M2 value stands regardless.

Sequencing note: v2 said "after 001 Phase 0," which the review showed guards nothing
relevant; the true constraint is Phase 1.2 for the committed tier (§4.6), while M1 has
no sequencing dependency at all.

## 7. Open questions

1. **Rebind ambiguity.** When one commit minted several ops with identical footprint
   digests, `sha`+`fp` rebind is ambiguous — accept first-match with a logged warning,
   or leave the record historical until a human confirms? (Leaning: log + historical;
   never guess silently.)
2. **Reflector budget.** Batched at session close; needs a token ceiling and
   degrade-to-`null`. Number TBD with M1 data.
3. **α and c weights for `intent_edges`** — M3(b) evaluation, same method as the
   temporal-prior tuning (docs/plans/2026-07-28-001).
4. **Reflector-proposed supersessions.** Auto-proposing `supersedes` risks false
   overturns; v3 stance: reflector proposals are unconfirmed-strength, recall treats
   them as softer than confirmed, humans create the binding ones. Revisit with M2 data.
5. **Entailment track.** "Changing A forces B by recorded rule" — logic over rationale
   remains a separate research project, together with the parked standing-decisions
   scope. **In-repo prior art (found during M1):** the pre-kernel architecture already
   shipped a controlled-English "intent DSL" (`ADD/EXTEND/REPLACE/REMOVE … USING …
   BECAUSE …` → a typed `ParsedIntent` with an `Alternative{option, why, source ∈
   transcript|plan|distilled|user, confidence ∈ low|high}` sidecar — near-identical to
   v3's inferred/confirmed rationale), then **deleted it** in the operation-ideal-kernel
   pivot (commits `66b92bf`, `b14b5d2`; design docs under `docs/design/2026-06-25-*`).
   The team built controlled-English and removed it — evidence *for* v3's "typed schema,
   not a DSL" call, and a caution that `intent`/`plan`/`frontier`/`decision`/`episode` are
   all now live with unrelated meanings (hence `turn`, §4.1).

## 8. Review record (2026-07-31, four parallel reviewers)

- **Adversarial** — P1 retention-vs-rebuildability circularity (fixed: keep-everything,
  knob cut); P2 planned-path precision unmeasured + testimony-anchoring risk (fixed: M1
  gate + local-until-gated + badge phrasing); P3 evidence density = agent-first feature
  (fixed: stated in §4.1); P4 minimal version doesn't need the cathedral (fixed:
  milestone collapse); **D1 op-id churn under miner bumps (critical** — fixed: sha+fp
  anchors + rebind + derived feature membership); **D2 revert liveness (critical** —
  fixed: read-time liveness join); D3 merge-surface collision (fixed: committed tier
  after 001-1.2); D4 supersession forks (fixed: read-time tie-break + fork marker); D5
  confirmation laundering (fixed: pin = automatic-rebuild-only, confirmed-supersedes-
  confirmed, full-record display; residual accepted); D6 standing-decision anchors
  (fixed by cutting the scope field); D7 actor self-reporting (mitigated: `channel`
  field + weighting; residual accepted).
- **Coherence** — session close undefined (fixed: defined as first-of triggers);
  unfulfilled-record schema gap (fixed: `open`/`predicted_fp` fields); unused relation
  types (cut); `mixed` undefined (fixed: defined); grace window unspecified (moot:
  retention cut); "immutable" ambiguity (fixed: append-only wording); cited-evidence
  pruning bug (moot: retention cut).
- **Feasibility** — all 25+ code anchors verified real; storage pattern mismatch
  (fixed: single-dict artifact); 001-sequencing wrong phase (fixed: 1.2); plan_sessions
  deletion window makes Phase-0 capture load-bearing (fixed: stated in §3, capture
  ships first); no structural-only feature-edge rollup (fixed: fused-edges v1 decision
  stated); `sgt why` verb collision (fixed: extend-not-replace stated); hollow retagging
  was new machinery in disguise (fixed: open-intent records instead);
  `claude_session_id` same-clone-only (stated).
- **Scope** — existing theme/segment pipeline ignored (fixed: M1 builds on it); storage
  discipline (fixed); share config speculative (cut); standing decisions speculative
  (cut); dead relation types and `action` enum (cut); 3-way confidence (collapsed to
  boolean); seven ungated phases (collapsed to three gated milestones); goal-1
  machinery imbalance (addressed: labeling moved into M1).
