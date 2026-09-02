# The capture weave: prompts as a load-bearing thread of checkpoints

Date: 2026-09-01. Written on branch `feat/intent-capture-weave`, at main feb79b2e. Every claim
about current behavior was checked against the code and this repo's own dogfood stores, not
against older design docs.

## Verdict

sgt already captures the developer's words, already mines the code they produced, and already cuts
per-feature checkpoints — but the three never meet. Capture is a Claude-Code-only hook pair; the
prompt→op join is a hidden batch command (`sgt intent align`) that did not terminate in 15 minutes
on this repo's own 30k-op store; and the checkpoint layer labels chapters exclusively from commit
subjects, so `sgt why <sha>` answers "no recorded reason" over a store holding 159 verbatim human
prompts. The fix is not a fourth layer beside checkpoint and feature. It is a **weave**: capture at
every entry point (hooks *and* MCP), reflect once per save into a durable per-commit record, derive
the prompt→op join as a pure function of what was captured (no LLM, no EM, no batch), and let the
existing checkpoint machinery — segmentation boundaries, labels, `why`, revert, resume — read the
woven thread it has been missing.

The core enabling observation: the join everyone assumes is hard (which prompt produced which op)
is only hard **in retrospect over a whole history**. At the save beat it is nearly free. The window
since the last save is small, the activity feed says which files moved after which prompt in which
session, and the ops just minted say which symbols changed in which files. Aligning inside that
window is bookkeeping, not inference. The batch aligner (`sgt.intent.align`, EM-calibrated,
unwired since 2026-08-03) was designed for the retrospective problem; the weave makes the
retrospective problem stop accumulating.

---

## Part 1. The dots, and which lines exist between them

Nine objects currently touch "what the developer said" or "why this code exists". Keys and tiers
verified against `sgt/state.py`:

| object | module | tier | key | grain |
|---|---|---|---|---|
| **Turn** — one verbatim utterance | `intent/turns.py` | local, never synced | chat-session-id / plan-id / session-name / sha | prompt |
| **Activity event** — one Edit/Write | `intent/activity.py` | local, ring buffer **capped at 200** | (session_id, ts, file) | tool call |
| **Op** + `Attribution(sha, session, agent, plan)` | `core/op.py` | committed (op store) | content address | symbol change |
| **IntentAtom** — ops sharing earliest-witnessing commit | `intent/group.py` | derived on read | commit sha | commit |
| **Run → Segment (checkpoint)** | `intent/segment.py` | `segments.json` **committed** | (feature, commit_shas) | commits |
| **Feature** | lens/`op_leaf` | committed | feature id | lane |
| **Plan session** (+ `claude_session_id`) | `loop/plan.py` | local `plan_sessions.json` | plan-id | declared step |
| **Prompt sidecar** — write-once digest | `intent/prompts.py` | **committed** | plan-id / session / sha | one string per key |
| **Rationale** — derived why per op | `intent/rationale.py` | local (M1) | record id, anchored (sha, footprint) | op cluster |

Lines that exist today:

- plan-id → turns → commits: the plan loop's high-trust path (`confirm_match` stamps the plan
  session into `Attribution.session`; `_atom_prompt` walks sha → plan → session → chat keys).
- turn → `sgt now`: `working.py` shows the latest unsaved prompt. Works, live.
- commit subject → checkpoint label: `segment.py` rung 0/1, `theme_segment.py` LLM rung.
- checkpoint → revert: `sgt revert <feature>@<n>`, deterministic op-set (KTD6).
- atom → `claude --resume <sid>`: only via plan sessions (`api.py:2742`).

Lines that do **not** exist:

- turn → op, outside a plan. The unplanned path — which is *most* prompts — has no join at all
  until someone runs the unscaling batch aligner.
- turn → checkpoint. Segmentation never reads the turn store; boundaries come from commit scope,
  novelty, and dormancy gaps only; labels come from commit subjects only.
- activity → anything. The feed is written on every Edit/Write and read only by `now_view` for a
  live "agent just did" line. Then the ring buffer eats it. **The one signal that grounds a prompt
  to files is being discarded 200 events at a time.**
- MCP → capture. An agent driving sgt through MCP records *nothing*: `tool_save` takes only
  `message`/`as_feature`; no verb carries the driving prompt or the agent's session id. The entire
  capture story assumes Claude Code hooks — Cursor and every other MCP client are invisible.

Drawn as a picture, the two time-grains are the point:

```
prompt-time (wall clock, fine)     P1───P2──────P3────────P4──────P5
                                    │    │       │          │      (no code)
activity (session, file, ts)        e e ee e    eee e      e ee
                                    └─┬──┘└─┬─┘ └─┬──┘     └─┬─┘
                                      │     │     │          │        ← the missing weave
commit-time (save beat, coarse)    ──────c1──────────c2──────────c3──
ops (footprint symbols)              {o1,o2,o3}    {o4..o9}     {o10}
checkpoints (per feature)          [====seg A====][=====seg B======]
```

Everything above the line is captured and thrown away or never joined; everything below the line
is durable and wordless. The weave is the vertical stitching.

---

## Part 2. The grain mismatch, stated precisely

A commit and a prompt are **independent** granularities; neither refines the other:

- **N:1** — ten prompts, one save. A human works with an agent all morning, saves once. The commit
  subject can name at most one intent; nine die. Today the atom (= commit) is the *smallest*
  recorded intent (KTD2), so nothing below it can ever be said.
- **1:M** — one prompt, many saves. "Implement the plan" and the agent checkpoints per step; one
  utterance explains eight commits. The plan loop models this; the unplanned path does not.
- **M:N** — interleaved: prompt 2 touches files A,B; prompt 3 touches B,C; one save witnesses both.

The resolution is a derived object this doc calls a **stint**, deliberately *not* a new
user-facing noun and *not* a new stored layer:

> **stint** = (turn T, the activity events with T's session_id in [T.ts, next turn of that
> session), the ops of the closing save whose footprint files intersect those events' files).

Op membership in a stint is a **deterministic function of captured evidence** — same discipline as
the segmentation safety invariant (`segment.py`: the boundary heuristic "never emits an op-id").
No LLM, no EM, no similarity score. An op whose files no stint touched falls into the **residual
stint** — "(no words captured)" — which is what keeps the weave honest: hand-typed edits and
un-prompted agent work are never attributed to the nearest prompt just because it was nearby in
time. File-grounding via activity is precisely what licenses the time window.

A stint may span saves (1:M: its turn stays open until the session's next turn), and a save may
close many stints (N:1). Both grains keep their own identity; the stint is the join table.

---

## Part 3. Fifteen cases the weave must survive

Each case names what happens today and what the design must do. The first two are fixed on this
branch; the rest drive Part 4.

1. **The injected non-prompt.** `UserPromptSubmit` fires for task notifications, system reminders,
   and slash-command markup, and `_record` stored them all as `actor="human"`. Dogfood: 137 of 294
   turns in this repo's store were `<task-notification>` blobs; `sgt now` would report one as
   "working on". **Fixed:** leading-wrapper guard in `_record` + tests; store GC'd (139 pruned).

2. **The cross-repo edit.** PostToolUse fires with cwd = the session's repo, but the edited file
   can be anywhere. Dogfood: `semi-git-render`'s edits sat in this repo's activity feed. A stint
   built on those would ground prompts to files sgt will never mine. **Fixed:** events outside the
   repo root are skipped + test.

3. **Ten prompts, one save (N:1).** Today: one atom, one subject, nine intents lost. Weave: the
   save closes ten stints; checkpoint labels and `why` can name each; segmentation may cut *inside*
   the commit's run only in label space (op membership stays per-run — see Part 4d).

4. **One prompt, eight saves (1:M).** Today: only the plan loop survives this. Weave: an open
   stint persists across saves until its session speaks again; each closing save claims the ops
   its files ground. The plan path is untouched and still wins where it exists (source precedence:
   plan step > stint > nothing, exactly `working.py`'s existing rule).

5. **The question that produced no code.** "How does the fold work?" — no activity follows before
   the next turn. The stint has no events, grounds no ops, labels nothing. It remains a turn
   (evidence for future reflection), but the weave must give it weight zero everywhere. Without
   file-grounding this case alone would poison every time-window join.

6. **Two agent windows, one repo.** Turns and events interleave in wall-clock but carry distinct
   session_ids (`capture_lock` already anticipates exactly this race). Stints are derived
   **per-session**; a global time window would braid two conversations into nonsense.

7. **The hand-typed edit.** No turn, no PostToolUse event (the human typed in their editor). Ops
   land in the residual stint; `why` honestly answers "no recorded reason". A *mixed* save (agent
   stint + hand edits to an untouched-by-agent file) splits correctly for free, because grounding
   is per-file, not per-window.

8. **The correction chain.** "add auth" → "no, sessions not JWTs" → save. Two stints, second
   reworks the first's files. They are one episode: segmentation must not cut between them (the
   turn-boundary signal is *available* to the boundary scorer, not a mandatory cut), and the second
   stint's rationale supersedes the first's (`rationale.py` supersedence already models this).

9. **The abandoned prompt.** Agent starts, user interrupts, work is reverted or never saved. The
   stint's events ground no surviving ops. It must not leak onto later ops that touch the same
   files — a stint closes **at its session's next turn or the next save, whichever first**; it
   never reopens. Parallels `intent open` (stated but never landed).

10. **History rewrite.** Rebase/amend/squash re-keys commits; a sha-keyed record orphans. Turn keys
    (chat ids) survive rewrites; the per-save capture manifest (Part 4b) is sha-keyed and must be
    remapped on `resync` exactly as checkpoints already handle `stale_shas` — diminished and
    visible, never silently dropped.

11. **Re-mining / miner version bump.** Op ids re-mint. Stints must anchor ops as
    (sha, footprint-symbols) — the anchor `rationale.py` already chose — never bare op-ids.
    Derived-on-read makes this nearly free: re-derive against the new store.

12. **The multi-feature prompt.** One stint's ops scatter across three lanes. Checkpoints are
    feature-scoped, so three features each get a chapter carrying the same words — correct, the
    prompt genuinely spanned them (`feature_span` already models cross-lane claims for themes).
    Labels dedupe per lane by the existing dominance rule, not globally.

13. **Resume staleness.** `claude --resume <sid>` assumes the transcript still exists; compaction,
    deletion, or a fork on resume can invalidate it. The context pack (Part 4f) must therefore be
    self-sufficient — the manifest's verbatim words are the durable copy of the conversation, and
    the resume handle is an *optional accelerator*, never the payload.

14. **The non-Claude agent.** Cursor/Codex over MCP: no hooks exist, so MCP-carried capture
    (Part 4a) is the only channel, and the relaying agent — not a harness — asserts "this is what
    the user said". That assertion gets its own channel (`"agent"`), trust-tiered below `"hook"`
    (verbatim by construction) and above `"note"` (paraphrase by construction). Downstream
    weighting reads the channel; nothing pretends an agent's relay is a harness capture.

15. **The privacy seam.** Turns are local-never-committed by design; but `segments.json` and
    `themes.json` are **committed=True** — they travel on `refs/sgt/state`. The moment a
    checkpoint label derives from a verbatim prompt, private words leak into shared state. The
    weave therefore marks every label with its source (already the pattern: `source` field in
    segment records), and **prompt-derived labels stay in a local overlay** unless the user
    explicitly shares (the existing write-once `intent_prompts` sidecar is the sanctioned export,
    and stays opt-in per key). Local overlay wins on read in this clone; teammates see the
    subject-derived label until sharing happens.

---

## Part 4. The architecture

Six moves. No new user-facing noun, no fourth layer: every move lands inside an existing object.

### 4a. Capture at every entry (kills the hook monopoly)

- Hooks stay the gold channel (verbatim, harness-witnessed). Hygiene guards shipped on this branch.
- **Every mutating MCP verb grows two optional args**: `prompt` (the user's ask, verbatim as the
  agent received it) and `claude_session_id` (already established precedent on `plan_intake` /
  `checkpoint` / `plan_done`). `tool_save` is the critical one. Recorded as channel `"agent"`,
  keyed by the session id (chat key) or, absent one, the save's commit sha.
- The `sgt-agent` skill instructs: pass the user's words with your save. An agent that forgets
  costs us nothing we have today.
- Dedup across channels is free: turns are content-addressed on (key, actor, channel, text), and
  a hook-captured turn beats an agent-relayed duplicate at read time by channel tier.

### 4b. Reflect at the save beat: the capture manifest (kills the batch aligner *and* the ring-buffer loss)

At the end of `porcelain._save` (and `tool_save` through it), harvest the window since the last
save: all turns and all activity events per session, plus the (sha, footprint) anchors of the ops
just minted. Persist as one **capture manifest** keyed by the new commit sha —
`.sgt/local/manifests.json`, local tier, compact codec, same `capture_lock`.

- This is the durable copy. After it, the activity ring buffer may trim freely (its cap becomes a
  liveness detail, not data loss), and stints are rebuildable forever (case 11).
- Cost: one bounded read-modify-write on data already in memory at the hottest verb — no mining, no
  network, no LLM. This is what "build the why layer while using the agent" means concretely: the
  reflection happens as a side effect of the save the agent was already making.
- `sgt intent align` demotes to a historical-backfill tool for pre-weave commits, clearly labeled;
  it never runs on manifested history. (Evidence it cannot be the primary path: the dry run on
  this repo was killed unfinished at 15 minutes, 98% CPU.)

### 4c. The stint, derived on read (kills guessing)

A pure function `stints(manifest, ops_of_sha) -> [Stint]` in `sgt/intent/` (Part 2's definition;
cases 5–9 are its unit tests). Not stored — derived from the manifest, cacheable exactly like
`intent_view`'s atoms. Residual stint always present, possibly empty.

### 4d. The weave into checkpoints (kills the wordless chapter)

Two touches to the existing segmentation, both inside its declared safety invariant:

- **Boundary signal.** `segment_runs` gains `W_WORDS`: adjacent runs whose dominant stints belong
  to different episodes (different turns, no correction-chain link) add boundary weight; same
  episode subtracts it (case 8). Like scope/gap/novelty it only moves *where to cut*, never which
  ops belong — op membership stays a function of (feature_id, runs).
- **Labels.** Where a segment's dominant stint covers ≥ the same 0.6 dominance the subject-label
  rule already uses, the checkpoint label prefers the stint's words (clipped by `clip_label`) over
  the commit subject; the LLM rung receives stint words as context instead of subjects alone.
  Source-marked, local-overlay first (case 15).

`sgt intent list/show` then reads: chapter, words that caused it, glyphs unchanged. N:1 (case 3)
is handled in label space: a run whose save closed several stints shows its top words with a
`(+n more asks)` affordance in `show`, while remaining one run structurally.

### 4e. Why, read from the weave (kills "no recorded reason")

`why_view`/`_commit_why` gain a third source beside plan matches and M1 rationale records: the
stint that grounds the op. At save time, each non-residual stint also writes a standard rationale
record (actor `human`, `confirmed=False`, evidence = its turn ids) — so `sgt intent review`,
supersedence, and `edit` keep working unchanged on weave-produced records. The EM aligner's
REVIEW-region queue is unnecessary for manifested saves: grounding is evidence, not similarity.

### 4f. The context pack (delivers go-back / resume / edit-from-here)

One new API view, `checkpoint_context(feature@n)`, surfaced through `sgt intent show <feature>@<n>
--context`, an MCP tool, and the workbench's Back-to-here:

1. **Words** — the checkpoint's stints' verbatim turns, in order (the durable conversation copy).
2. **Shape** — its ops' footprint symbols, and what *later* segments touched of them (so an edit
   from here knows what it would disturb — reuses the revert-preview dependency machinery).
3. **Why** — its rationale records, badged as everywhere else.
4. **Handles** — `claude --resume <sid>` for every session id its stints carry, best-effort
   (case 13), plus the plan-id if one governed it.

That is "prepare context to edit from that checkpoint": an agent (or human) reads one pack and has
the ask, the code, the reasons, and the door back into the original conversation. Restore/revert
stay the state-movement verbs they already are; the pack is the narrative that rides along.

---

## Part 5. What this deliberately does not do

- **No free text in `Op`** (KTD1 stands). Attribution/provenance untouched; the weave lives in
  local manifests and derived views.
- **No committed verbatim prompts.** The local/committed split of M1 stands; sharing remains the
  explicit write-once sidecar, per key, opt-in (case 15).
- **No LLM in the join.** LLMs keep doing what they do today — labeling and consolidation — never
  membership.
- **No new noun for users.** "Checkpoint" and "feature" remain the vocabulary; stints and
  manifests are internal words that never appear in porcelain output.
- **No always-on daemon.** Capture is hook/MCP entry points; reflection is the save beat.

## Part 6. Decisions being made here (flagged, reversible-by-phase)

1. Manifest is **local-tier** in this milestone; a shared tier is M2, gated on the same
   state-model work `rationale.py` already defers to.
2. Agent-relayed prompts are channel `"agent"`, trusted below hooks — not rejected, not equated.
3. The stint window closes at next-turn-or-save (case 9); no reopening.
4. `W_WORDS` ships behind the same explicit-constant discipline as the other weights, default
   conservative (boundary-preserving, like `SEAM_BONUS`), tuned by the flicker sweep when it runs.
5. Labels from prompts: local overlay first; committed `segments.json` keeps subject-derived
   labels until the user shares words for a key.

## Part 7. Phasing

- **P0 — capture hygiene** (this branch, shipped): injection guard, repo-scope guard, store GC,
  tests.
- **P1 — carry + manifest**: MCP `prompt`/`claude_session_id` on mutating verbs; manifest write in
  `_save`; skill update. Pure additive; nothing reads manifests yet.
- **P2 — stints + why**: derivation, rationale emission at save, `why`/`show` read-back. First
  user-visible payoff: `sgt why <sha>` answers with the user's own sentence.
- **P3 — checkpoint weave**: `W_WORDS`, label preference + local overlay, `intent list/show`
  words.
- **P4 — context pack**: `checkpoint_context` view, `--context`, MCP tool, workbench wiring.

- **P5 — the ask, and where it shows** (2026-09-02): the excerpt rule (`sgt.intent.gist`), the
  `asked` attribute on `sgt show`, and the read surfaces in the terminal map and the editor.

Each phase lands independently green; P2 is where the batch aligner demotes.

## Part 8. P5, and what P1–P4 got wrong about reading

P1–P4 captured, joined and delivered the words. What they got wrong was what to *show* of them.
Every surface printed the prompt's first **line** -- the chapter's name, the recorded reason,
`sgt now`'s current task, the context pack, the timeline tooltip -- which is the ask only when the
prompt was typed like a commit message. The dogfood turn store says they are not. Of 164 real
prompts, the median opens with throat-clearing ("so i think we shoudl proably…"), carries its
reasoning before or after the request, runs several asks together with commas, and a long one has
no line break in it at all -- so "the first line" *is* the paragraph, and a 60-character chapter
name built from it was 40 characters of throat-clearing. The words were captured, joined and
delivered, and still said nothing.

So P5 adds one rule and applies it everywhere: **an excerpt starts at the ask.** Strip the
conversational opening from each clause; take the first clause that then begins with a verb someone
would type at a coding agent; clip on a word boundary. Nothing else about the words changes --
typos, casing and grammar stay exactly as typed, because the whole claim a recorded reason makes is
that nobody rewrote it. Deterministic and offline, for the reason `show` is: this runs in read
paths a cautious user repeats, and a name that came back different on the second read would be
worse than a clumsy one.

Three things fall out of it:

- **`asked` is an attribute, not a verb.** `sgt intent show <cp>` and `sgt intent edit` exist and
  are the right shape for an agent or for a deliberate correction, but they are not how a person
  arrives: a reader holding a commit or a checkpoint runs `sgt show`, so that is where the words
  are, in the same class as `symbols` and `saves`. `sgt show <sel> --asked` reads the conversation
  in full, and it is the only place the verbatim paragraph is printed, because it is the only place
  somebody asked for it. `why` keeps the recorded reasoning; `show` says what was asked. Two
  different questions, so two answers rather than one view deriving both.
- **Provenance travels with the words.** Every ask carries `source` -- "you, in a Claude Code
  chat", "you, relayed by the assistant", "the assistant's note", "your save message" -- computed
  once and rendered identically by the CLI, the terminal map and the webview, because "whose words
  are these" is the one thing two surfaces must not answer differently. The trust tier from §4a is
  visible in words rather than implied by a field name.
- **An accelerator that fails is worse than none.** `claude --resume <id>` is now offered only when
  that transcript is on this machine (`stint.resumable`). A handle printed beside real words and
  failing when typed teaches the reader that the whole line is decoration -- and it would have been
  printed for every replayed study session, which is what forced the check.

The excerpt is a display concern only. The LLM labeler still reads the whole prompt
(`label_prompt_for`), because it can extract an ask better than a heuristic can, and the store
still holds every prompt verbatim and unpruned. What the heuristic is for is the line a reader is
given for free, before anybody clicks.
