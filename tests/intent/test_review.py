"""Tests for sgt.intent.review -- the alignment review queue (alignment-pipeline §3.2-F). REVIEW
region (op, episode) pairs the aligner could not confidently ALIGN are held here for human
adjudication, kept OUT of intent_rationale so an unconfirmed guess never leaks into recall()/for_op.
A `--confirm` promotes one into the ledger (confirmed, recorded_by="user"); a `--reject` tombstones
it. Both statuses are append-only tombstones so a re-run of the aligner never re-surfaces a decided
pair. Same local-tier fixture idiom as tests/intent/test_rationale.py."""

from __future__ import annotations

from sgt.intent import rationale, review
from sgt.store.gitbind import init_store

_SUBJECT = [{"op": "o1", "sha": "shaX", "fp": "fp1"}]


def test_record_review_is_pending_and_idempotent(tmp_path):
    init_store(tmp_path)
    a = review.record_review(tmp_path, subject=_SUBJECT, reason="make search better",
                             evidence=["t1"], posterior=0.6, signals=[{"name": "topic", "value": 1.0}],
                             aligner_version="1", ts=1.0)
    b = review.record_review(tmp_path, subject=_SUBJECT, reason="make search better",
                             evidence=["t2"], posterior=0.6, signals=[], aligner_version="1", ts=2.0)
    assert a == b  # same (subject, reason) -> same id, no duplicate
    pending = review.pending_reviews(tmp_path)
    assert len(pending) == 1
    assert pending[0]["reason"] == "make search better"
    assert pending[0]["status"] == "pending"


def test_confirm_promotes_to_rationale_and_tombstones(tmp_path):
    init_store(tmp_path)
    rid = review.record_review(tmp_path, subject=_SUBJECT, reason="make search better",
                               evidence=["t1"], posterior=0.62,
                               signals=[{"name": "topic", "value": 1.0}], aligner_version="1", ts=1.0)
    promoted = review.confirm_review(tmp_path, rid)
    assert promoted is not None

    recs = rationale.for_op(tmp_path, "o1")
    assert len(recs) == 1
    assert recs[0]["reason"] == "make search better"
    assert recs[0]["confirmed"] is True
    assert recs[0]["recorded_by"] == "user"
    assert recs[0]["confidence"] == 0.62

    assert review.pending_reviews(tmp_path) == []  # decided -> off the queue
    # A re-run of the aligner writing the same pair does NOT re-surface it as pending (tombstone).
    review.record_review(tmp_path, subject=_SUBJECT, reason="make search better", evidence=["t9"],
                         posterior=0.62, signals=[], aligner_version="1", ts=9.0)
    assert review.pending_reviews(tmp_path) == []


def test_reject_tombstones_without_promoting(tmp_path):
    init_store(tmp_path)
    rid = review.record_review(tmp_path, subject=_SUBJECT, reason="fix the thing", evidence=["t1"],
                               posterior=0.6, signals=[], aligner_version="1", ts=1.0)
    assert review.reject_review(tmp_path, rid) is True
    assert review.pending_reviews(tmp_path) == []
    assert rationale.load_rationale(tmp_path) == {}  # nothing promoted
    # re-recording the rejected pair does not bring it back
    review.record_review(tmp_path, subject=_SUBJECT, reason="fix the thing", evidence=["t9"],
                         posterior=0.6, signals=[], aligner_version="1", ts=9.0)
    assert review.pending_reviews(tmp_path) == []


def test_confirm_of_unknown_or_decided_id_is_a_no_op(tmp_path):
    init_store(tmp_path)
    assert review.confirm_review(tmp_path, "r-nope") is None
    rid = review.record_review(tmp_path, subject=_SUBJECT, reason="r", evidence=[], posterior=0.6,
                               signals=[], aligner_version="1", ts=1.0)
    review.reject_review(tmp_path, rid)
    assert review.confirm_review(tmp_path, rid) is None  # already decided
