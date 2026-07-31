"""Derived rationale records (intent-ledger M1, design doc
`docs/plans/2026-07-31-002-intent-ledger-architecture.md` §4.2-§4.3): the *reflection* layer
over the raw `sgt.intent.turns` evidence and the op store. Where a turn is one thing the user
said, a rationale is the derived answer to "why do these ops exist" -- `{subject ops, reason,
actor, evidence, confirmed}` -- produced by transcribing what the workflow already aligned.

Reflection is inference, not recording: even with the real conversation in hand, mapping messy
turns to the ops that landed is a guess. So every record is honest about its footing -- `confirmed`
distinguishes a human-endorsed record from an inferred one, an empty `reason` (`open`/no evidence)
means "unknown" rather than a fabricated why, and `evidence` points back at the turns the guess
rests on so it stays auditable.

**M1 is local-tier.** Records live in `.sgt/local/rationale.json` (never synced), exactly like
`turns`. The committed, team-shared tier -- with its CRDT merge, stable sha+footprint anchors, and
read-time liveness join -- is M2 work, gated on the state-model rework in the workflow-hardening
plan (2026-07-31-001 Phase 1.2); shipping the committed artifact before that lands would drop it
into the very merge surface that plan is evacuating. Until then this is the reader for `sgt why`
and the label feed, proving the bet with zero sync surface.

**The planned path (M1).** For plan-loop work, `sgt.loop.match.confirm_match` has already aligned a
cluster of real ops to the plan steps that predicted them (`plan_matches.json`). Reflection there
is transcription: read the matched steps' rationale/title as the reason, the plan-intake turns as
evidence, the ops' provenance sha + footprint as anchors, and emit one record. The unplanned path
(segmenting a tangled session's conversation and aligning it to op-clusters) is the M3 research bet
-- deliberately not here.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

from sgt import state
from sgt.intent.turns import turns_for

_ARTIFACT = "intent_rationale"
REFLECTOR_VERSION = "1"


def load_rationale(repo: str | Path) -> dict[str, dict]:
    """The whole local rationale store, `{rationale-id: record}` -- empty if none derived yet."""
    return state.load_json(repo, _ARTIFACT, default={})


def _fp_digest(footprint) -> str:
    """A stable digest of an op's footprint (its symbol set) -- the secondary anchor a future
    committed-tier rebind (M2) uses when an op id churns under a miner bump. Equal footprints
    digest equally, so a re-mined op is re-findable by its shape."""
    return hashlib.sha256(json.dumps(sorted(footprint), ensure_ascii=False).encode("utf-8")).hexdigest()[:16]


def _rationale_id(subject_ops: list[str], reason: str | None, actor: str) -> str:
    """Content address over a record's identifying fields, so re-reflecting the same alignment is a
    no-op. `evidence`/`ts`/`confirmed` are excluded: the same (subject, reason, actor) is the same
    claim regardless of when it was derived or which turns happened to be cited."""
    payload = json.dumps([sorted(subject_ops), reason, actor], ensure_ascii=False)
    return "r-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def record_rationale(repo: str | Path, *, subject: list[dict], reason: str | None, actor: str,
                     evidence: list[str], confirmed: bool = False, open: bool = False,
                     predicted_fp: str | None = None, relations: list[dict] | None = None,
                     ts: float | None = None, recorded_by: str = "reflector") -> str | None:
    """Write one rationale record. `subject` is a list of `{op, sha, fp}` anchors (empty for an
    `open` unfulfilled-intent record). Idempotent by (subject ops, reason, actor); returns the id
    (fresh or existing), or `None` when there is nothing to say (no subject and not an open record).
    Does not overwrite an existing id -- a correction supersedes via a new record, never a mutation
    (append-only, so a future committed-tier merge stays a conflict-free union)."""
    subject_ops = [s["op"] for s in subject]
    relations = relations or []
    if not subject_ops and not open and not relations:
        return None  # nothing to say: no ops, not an open intent, not a closing/supersession
    store = load_rationale(repo)
    rid = _rationale_id(subject_ops, reason, actor)
    if rid in store:
        return rid
    store[rid] = {
        "id": rid, "subject": subject, "predicted_fp": predicted_fp, "open": open,
        "reason": reason, "actor": actor, "confirmed": confirmed,
        "evidence": list(evidence), "relations": list(relations),
        "ts": time.time() if ts is None else ts,
        "recorded_by": recorded_by, "reflector_version": REFLECTOR_VERSION,
    }
    state.save_json_if_changed(repo, _ARTIFACT, store)
    return rid


def _subject_for(repo: str | Path, op_ids) -> list[dict]:
    """Build `{op, sha, fp}` anchors for `op_ids` -- the op's first provenance sha (a stable anchor
    across miner bumps, unlike the id) and its footprint digest. Ops absent from the store (e.g. a
    still-hollow prediction) are skipped."""
    from sgt.core.store import Store

    store = Store(repo)
    subject = []
    for op_id in op_ids:
        op = store.get(op_id)
        if op is None:
            continue
        sha = min(op.provenance) if op.provenance else None
        subject.append({"op": op_id, "sha": sha, "fp": _fp_digest(op.footprint)})
    return subject


def reflect_planned_match(repo: str | Path, session_id: str, op_ids: list[str]) -> str | None:
    """Transcribe a just-confirmed plan match into one local rationale record (the M1 planned path).
    Reason = the matched steps' rationale (falling back to their titles); evidence = the plan-intake
    turns; actor = human (a plan is the user's own intent); `confirmed=False` (inferred -- a human
    endorses it later via a correction). A no-op when the session record or its matched ops are
    gone. Called at the end of `confirm_match`, where op<->step alignment is known and free."""
    sessions = state.load_json(repo, "plan_sessions", default={})
    record = sessions.get(session_id)
    if record is None:
        return None
    wanted = set(op_ids)
    reasons = [
        (step.get("rationale") or step.get("title") or "").strip()
        for step in record.get("steps", [])
        if wanted & set(step.get("matched_op_ids", []))
    ]
    reason = "; ".join(r for r in reasons if r) or None
    subject = _subject_for(repo, op_ids)
    if not subject:
        return None
    evidence = [t["id"] for t in turns_for(repo, session_id, key_kind="plan")]
    return record_rationale(repo, subject=subject, reason=reason, actor="human",
                            evidence=evidence, confirmed=False)


def reflect_open_intents(repo: str | Path, session_id: str) -> list[str]:
    """When a plan session closes (`mark_done`/`abandon`) with steps still unfulfilled, record each
    still-pending step as an `open` intent -- rather than letting its hollow op be deleted silently.
    These resurface via `sgt intent open` and (M2) recall, and retire when a later op fulfills them
    or a human runs `sgt intent done`. A no-op for an unknown session. Called *before* the pending
    hollows are unlinked, so the step's prediction is still readable."""
    sessions = state.load_json(repo, "plan_sessions", default={})
    record = sessions.get(session_id)
    if record is None:
        return []
    evidence = [t["id"] for t in turns_for(repo, session_id, key_kind="plan")]
    out = []
    for step in record.get("steps", []):
        if step.get("status") != "pending":
            continue
        reason = (step.get("rationale") or step.get("title") or "").strip() or None
        predicted = step.get("predicted_footprint") or []
        rid = record_rationale(
            repo, subject=[], reason=reason, actor="human", evidence=evidence,
            open=True, predicted_fp=_fp_digest(predicted) if predicted else None)
        if rid:
            out.append(rid)
    return out


def retire_open(repo: str | Path, rid: str, reason: str = "marked done") -> str | None:
    """Retire an open intent (`sgt intent done`): write a closing record that supersedes it, so it
    leaves the open surface. `None` if `rid` is not a *live* open intent (unknown, not open, or
    already retired) -- so a second retire is a no-op. The open record is kept as history
    (append-only); only its standing changes."""
    if rid not in {r["id"] for r in open_intents(repo)}:
        return None
    return record_rationale(repo, subject=[], reason=reason, actor="user", evidence=[],
                            open=False, relations=[{"type": "supersedes", "target": rid}])


def _superseded_ids(records: list[dict]) -> set[str]:
    """The ids any record in `records` supersedes -- those are historical, not the current why."""
    return {
        rel["target"] for r in records for rel in r.get("relations", [])
        if rel.get("type") == "supersedes"
    }


def for_op(repo: str | Path, op_id: str) -> list[dict]:
    """Every rationale record whose subject includes `op_id`, live (unsuperseded) first, then by
    recency. Each record gains a `superseded` flag. The read-time liveness join against the current
    ideal (demote rationale whose code was reverted) is M2 -- here supersession is the only
    liveness signal, and M1 mints none, so in practice all records read live."""
    recs = [r for r in load_rationale(repo).values() if any(s["op"] == op_id for s in r["subject"])]
    superseded = _superseded_ids(recs)
    for r in recs:
        r["superseded"] = r["id"] in superseded
    return sorted(recs, key=lambda r: (r["superseded"], -r["ts"]))


def open_intents(repo: str | Path) -> list[dict]:
    """Unfulfilled-intent records (`open=True`) not yet retired by a superseding record -- surfaced
    by `sgt intent open` and recall, retired by `sgt intent done` or a later overlap match."""
    recs = list(load_rationale(repo).values())
    superseded = _superseded_ids(recs)
    return sorted(
        (r for r in recs if r.get("open") and r["id"] not in superseded),
        key=lambda r: -r["ts"],
    )
