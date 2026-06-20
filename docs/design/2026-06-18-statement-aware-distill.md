---
date: 2026-06-18
topic: statement-aware distillation — the agent-agnostic on-ramp to statement-granular merge
status: design / ADR
origin: docs/plans/2026-06-18-003-feat-merge-algorithm-and-policy-plan.md (C2), merge-edge-cases.md (EC5/EC6)
---

# Statement-aware distillation

## Why this is the load-bearing piece for "works with any coding agent"

Every coding agent a user might run — Gemini, Cursor, Claude Code, Codex, an in-house "Bob" —
**edits files**, not effects. The only agent that speaks typed effects is our bespoke OpenAI
compiler (`sgt/adapter/openai_agent.py`). So the universal, agent-agnostic integration shape is
not the push path (`execute_task → effects`) but the **pull/reconcile path**: the agent edits the
working tree freely, and `sgt sync` distills the disk diff back into the effect log
(`sgt/effects/diff.py` + `sgt/agents/distill.py`). That path is what makes the
*semantic-nodes ↔ codebase* loop bidirectional and therefore robust for **any** agent.

But there was a gap that quietly defeated the merge engine for exactly the multi-user,
multi-agent flow we care about:

> `distill_file` (diff.py) emits `replace_def` (a **whole-unit** rewrite) whenever a function
> body changes. The merge engine's headline capability — *concurrent edits to different
> statements of one function commute and both land* (EC5) — is reachable **only** through
> `insert_stmt`/`replace_stmt`/`remove_stmt`. Today **no** agent path produces those ops
> (`base.py:_effect_from_flat` even raises on them). So two users, each with their own
> file-editing agent, editing different lines of the same function, would `sync` to two
> `replace_def`s, collide on the same unit, and one would be needlessly quarantined.

Statement-aware distill closes that gap **once, for every agent**, because it sits on the
reconcile path they all share. After this, the question "does sgt merge correctly when two users
ran different agents?" reduces to "did each agent's edits distill to statement ops?" — and they do.

## The identity constraint that dictates the algorithm

A statement's identity (`PosId`) **cannot be recomputed from text** — it must be *log-resident*.
`materialize._apply_stmt_ops` (model.py) seeds a function's statements from its **defining
effect's eid** (`from_source(body, rid, ctr)`), then replays the function's stmt ops. So for a
distilled `replace_stmt`/`remove_stmt` to address the *same* slot materialize will, distill must
**reconstruct the exact same live `StatementSeq`** — seed from the defining effect, replay
existing stmt ops — and emit ops against *those* PosIds. This is why distill becomes **log-aware**
for body changes (unlike the pure text-only `distill_file`). To guarantee distill and materialize
never diverge, both call **one** reconstruction: `build_statement_seq(defining, ops)` (model.py).

## The alignment algorithm (`diff_statements`)

Given the reconstructed `live_seq` (ordered `(PosId, source)` slots = the *expected* body with
identity) and the function's *actual* on-disk body statements, produce stmt ops that transform
expected → actual **while reusing PosIds for changed statements**:

1. **LCS** over normalized statement text → matched index pairs (unchanged statements keep their
   PosId, no effect).
2. **Gap pairing** between consecutive matched anchors: zip unmatched-old with unmatched-new as
   `replace_stmt` **at the old slot's PosId** (identity reuse); surplus old → `remove_stmt`;
   surplus new → `insert_stmt` positioned `between(last-kept-or-anchor, next-anchor)`.

**Why reuse PosIds for changed statements (not remove+insert)?** This is a *correctness*
requirement, not an optimization:
- If user A changes statement S and user B concurrently changes the **same** S, both must emit
  `replace_stmt` at the **same** PosId so the merge gate (`static_commute is False` for same-pos
  stmt ops) detects the conflict (EC6). Remove+insert would give B's edit a fresh PosId, the two
  would target different slots, **both would land**, and one user's work would be silently lost
  under LWW render. Identity reuse is what makes the conflict *visible*.
- If A changes S and B changes a **different** statement T, distinct PosIds commute → both land
  (EC5). Reuse preserves this too.

## Edge cases & user flows (does it hold for …?)

- **D1 — function newly added on disk.** No defining effect exists → cannot seed → keep `add_def`
  (whole unit is *correct*: brand-new code has no prior statement identity to preserve).
- **D2 — function removed on disk.** → `remove_def` (unchanged behavior).
- **D3 — signature changed (params/decorators/returns/name).** Statement ops only rewrite a
  body; a header change is a coarser semantic event. *Decision:* fall back to `replace_def` for
  the whole unit and **note** it. This resets the statement seed (prior statement identity for
  that function is dropped) — accepted, because a signature change is a major edit and rare
  relative to body edits. Detected by comparing the def with its body replaced by `pass`.
- **D4 — statement changed in place.** → `replace_stmt` at the existing PosId. *The
  conflict-detection-critical case* (see EC6 argument above). **Test.**
- **D5 — statement inserted.** → `insert_stmt` between the surrounding kept PosIds; the new
  statement's own PosId is allocated at materialize time from the op's eid (so concurrent inserts
  by different replicas get distinct, deterministically-ordered ids). **Test.**
- **D6 — statement removed.** → `remove_stmt` (tombstone). **Test.**
- **D7 — statements reordered.** LCS models a move as delete+insert → the moved statement loses
  its PosId. Accepted: reordering is rare and semantically ambiguous; the rendered result is
  still correct. Documented, not silently optimal.
- **D8 — first body edit of an unmanaged function (promotion).** The function had only an
  `add_def`/`replace_def` and no stmt ops; this edit "promotes" it to statement-managed. Works
  because `build_statement_seq` seeds from the defining effect identically to materialize. **Test.**
- **D9 — already statement-managed function.** `build_statement_seq` replays prior stmt ops, so
  alignment runs against the post-ops body and new ops reference live PosIds (including those of
  previously-inserted statements). **Test (sequential edits across two syncs).**
- **D10 — multiline / nested statements.** A slot's source is the full `ast.unparse(stmt)` (a
  nested `def`, an `if`/`for` block is one slot). Alignment on whole-statement normalized text
  handles it. **Test.**
- **D11 — duplicate identical statements.** LCS may align identical statements arbitrarily;
  harmless because the statements (and thus any emitted op's text) are identical.
- **D12 — body emptied.** All statements → `remove_stmt`; `StatementSeq.render` yields `pass`.
  **Test.**
- **D13 — unparseable disk source.** Already handled upstream by `distill_file` (returns a note,
  no effects); promotion never sees it.
- **D-roundtrip — fidelity.** After sync lands the distilled stmt ops, re-materializing the
  function body must equal (normalized) what was on disk. **Test.**

## Scope of this iteration

- **Top-level functions only.** `distill_file` surfaces drift at top-level-unit granularity, so a
  method-body change currently arrives as `replace_def <ClassName>` (the whole class). Promotion
  therefore targets top-level `replace_def`s whose unit is a function. Descending `distill` into
  class bodies (so methods get statement granularity too) is a **deferred** follow-up; until then
  class/method bodies stay whole-unit (correct, just coarse — same posture as EC-atomic).
- **Deferred (with reason):** method/nested-function statement granularity (needs distill to
  descend into classes); intra-statement (sub-expression) granularity (out of scope — the unit is
  a statement); reorder-as-move detection (D7).

## Correction found while implementing: distilled edits must be their own nodes

> **Q: if `sgt sync` attaches a body edit to the function's existing owner node (the natural
> "this refines f" semantics), does a two-user same-statement clash still surface?**
> **No.** The T0 merge gate detects a concurrent conflict only *across* nodes
> (`engine.py:_concurrent_conflict` compares a node's effects to *other* active nodes, never to
> itself). The owner node's id travels in the delta, so both replicas would file their
> `replace_stmt` under the **same** node id; the clash becomes intra-node and is silently
> resolved by `StatementSeq` LWW — a user's edit lost without a conflict. Quarantining the whole
> shared node instead would discard the base function too (too coarse).

*Decision:* `run_sync` lands distilled **statement** ops as their own `FIX` node (one per edited
function), anchored `DEPENDS_ON` the owner — never as an extend. This reuses the merge engine's
already-tested node-granular conflict detection: distinct-statement edits are distinct nodes that
commute (EC5, both land); same-statement edits are distinct nodes that don't commute, so
`_concurrent_conflict` fires and the loser is quarantined against the winner (EC6, surfaced).
Non-statement edits keep the prior extend/new-node semantics. This is also semantically apt for
collaboration — each user's edit is an independently mergeable, revertible unit with its own
authorship.

*Trade-off / DEFERRED refinement:* a solo user editing one body repeatedly accrues several small
fix nodes (history, not breakage). To restore "a body edit *extends* the function's node" while
keeping merge correctness, the merge engine would need **split-on-conflict**: detect concurrent
non-commuting statement ops *within* a node and extract only the losing effect into a quarantine
node, leaving the rest active. That is per-effect (statement) granularity in the gate — the same
surgery as merge-edge-cases EC-atomic — and is out of scope for this iteration.

## What this unlocks for the multi-agent, multi-user goal

Because all of Gemini/Cursor/Claude Code/Codex/Bob edit files and converge on this one reconcile
path, after this change: a user runs *any* agent locally, `sgt sync` distills their edits to
statement ops, they `push`/`pull`, and the existing T0 merge engine gives statement-granular
correctness — distinct-statement edits both land, same-statement edits surface as a conflict —
**independent of which agent produced the edit**. The agent becomes a swappable front-end to the
same semantic log; the merge guarantees ride entirely on the log, not the agent.
