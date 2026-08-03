"""The alignment review queue (alignment-pipeline design `docs/plans/2026-08-01-001` §3.2-F): the
REVIEW-region (op, episode) pairs the aligner scored but could not confidently ALIGN -- vague or
confusing sessions, exactly the ones the pipeline holds for human judgment rather than guessing.

This is deliberately a SEPARATE local store from `sgt.intent.rationale`. `recall`/`for_op` (the
agent-facing readers) do not filter by `confirmed`, so writing an unconfirmed guess into the ledger
would leak it into recall. Instead a review record sits here until a human decides: `confirm_review`
promotes it into the ledger as a human-endorsed rationale (`confirmed=True`, `recorded_by="user"`)
and `reject_review` drops it. Both are append-only *tombstones* (status flips, the record stays), so
a later re-run of the aligner that re-scores the same pair never re-surfaces a decision already
made. Content-addressed by (subject ops, reason), same as the ledger, so re-recording is a no-op.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

from sgt import state
from sgt.intent import rationale
from sgt.intent.turns import capture_lock

_ARTIFACT = "intent_review"


def load_review(repo: str | Path) -> dict[str, dict]:
    """The whole local review queue, `{review-id: record}` -- empty if nothing pending."""
    return state.load_json(repo, _ARTIFACT, default={})


def _review_id(subject_ops: list[str], reason: str | None) -> str:
    """Content address over the pair's identity -- its subject ops and the episode's reason text.
    Same claim (a re-run scoring the same op against the same words) -> same id, so re-recording a
    decided pair collides onto its tombstone rather than re-queuing it."""
    payload = json.dumps([sorted(subject_ops), reason], ensure_ascii=False)
    return "rv-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def record_review(repo: str | Path, *, subject: list[dict], reason: str | None,
                  evidence: list[str], posterior: float, signals: list[dict] | None = None,
                  aligner_version: str | None = None, ts: float | None = None) -> str | None:
    """Queue one REVIEW candidate for adjudication. `subject` is the same `{op, sha, fp}` anchor list
    the ledger uses (self-contained, so `confirm_review` replays it without re-resolving a churned
    op). Idempotent by (subject ops, reason); returns the id. A no-op (returns the existing id) when
    the pair is already queued OR already decided -- a confirmed/rejected tombstone is never
    re-opened. Returns None when there is no subject to anchor to."""
    subject_ops = [s["op"] for s in subject]
    if not subject_ops:
        return None
    with capture_lock(repo):
        store = load_review(repo)
        rid = _review_id(subject_ops, reason)
        if rid in store:
            return rid  # already queued or decided -- append-only, never re-open
        store[rid] = {
            "id": rid, "subject": subject, "reason": reason, "evidence": list(evidence),
            "posterior": posterior, "signals": list(signals or []),
            "aligner_version": aligner_version, "status": "pending",
            "ts": time.time() if ts is None else ts,
        }
        state.save_json_if_changed(repo, _ARTIFACT, store)
    return rid


def pending_reviews(repo: str | Path) -> list[dict]:
    """The pending pairs awaiting a decision, oldest first."""
    return sorted((r for r in load_review(repo).values() if r["status"] == "pending"),
                  key=lambda r: r["ts"])


def confirm_review(repo: str | Path, rid: str) -> str | None:
    """Promote a pending review into the ledger as a human-endorsed rationale and tombstone it.
    Returns the new rationale id, or None if `rid` is unknown or already decided. The promoted record
    carries the human's endorsement (`confirmed=True`, `recorded_by="user"`) and the aligner's own
    score/signals, so the ledger stays honest about how the link was found *and* that a human blessed
    it.

    The promotion runs *outside* the review lock: `capture_lock` is a non-reentrant flock, and
    `record_rationale` takes it too, so holding it across the promote would deadlock. Promote first
    (idempotent by content-address, so a concurrent double-confirm is harmless), then tombstone the
    queue entry under the lock."""
    rec = load_review(repo).get(rid)
    if rec is None or rec["status"] != "pending":
        return None
    promoted = rationale.record_rationale(
        repo, subject=rec["subject"], reason=rec["reason"], actor="human",
        evidence=rec["evidence"], confirmed=True, confidence=rec["posterior"],
        signals=rec["signals"], aligner_version=rec["aligner_version"], recorded_by="user")
    with capture_lock(repo):
        store = load_review(repo)
        if store.get(rid, {}).get("status") == "pending":
            store[rid]["status"] = "confirmed"
            state.save_json_if_changed(repo, _ARTIFACT, store)
    return promoted


def reject_review(repo: str | Path, rid: str) -> bool:
    """Tombstone a pending review as rejected (nothing promoted). Returns True if it was pending and
    is now rejected, False if unknown or already decided."""
    with capture_lock(repo):
        store = load_review(repo)
        rec = store.get(rid)
        if rec is None or rec["status"] != "pending":
            return False
        rec["status"] = "rejected"
        state.save_json_if_changed(repo, _ARTIFACT, store)
    return True
