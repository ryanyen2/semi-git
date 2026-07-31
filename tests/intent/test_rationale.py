"""Tests for sgt.intent.rationale -- the intent ledger's derived reflection layer (M1): local
rationale records, idempotent by (subject, reason, actor), the unfulfilled-intent (`open`) surface,
and supersession as the historical/live signal `for_op` reads. The confirm_match -> reflect
planned path is covered end-to-end in tests/loop/test_match.py."""

from __future__ import annotations

from sgt.intent import rationale
from sgt.store.gitbind import init_store


def test_record_and_read_for_op(tmp_path):
    init_store(tmp_path)
    rid = rationale.record_rationale(
        tmp_path, subject=[{"op": "o1", "sha": "shaX", "fp": "fp1"}],
        reason="because the old guard leaked sessions", actor="human", evidence=["t1"], ts=1.0)
    assert rid is not None

    recs = rationale.for_op(tmp_path, "o1")
    assert len(recs) == 1
    assert recs[0]["reason"] == "because the old guard leaked sessions"
    assert recs[0]["confirmed"] is False
    assert recs[0]["superseded"] is False


def test_record_is_idempotent_by_subject_reason_actor(tmp_path):
    init_store(tmp_path)
    a = rationale.record_rationale(tmp_path, subject=[{"op": "o1", "sha": None, "fp": "f"}],
                                   reason="r", actor="human", evidence=["t1"], ts=1.0)
    b = rationale.record_rationale(tmp_path, subject=[{"op": "o1", "sha": None, "fp": "f"}],
                                   reason="r", actor="human", evidence=["t2"], ts=2.0)
    assert a == b  # same claim -> same id, no duplicate
    assert len(rationale.load_rationale(tmp_path)) == 1


def test_empty_subject_non_open_is_a_no_op(tmp_path):
    init_store(tmp_path)
    assert rationale.record_rationale(tmp_path, subject=[], reason="r", actor="human",
                                      evidence=[]) is None


def test_open_intent_record_allows_empty_subject(tmp_path):
    init_store(tmp_path)
    rid = rationale.record_rationale(tmp_path, subject=[], reason="wanted rate limiting",
                                     actor="human", evidence=[], open=True,
                                     predicted_fp="fp-rl", ts=1.0)
    assert rid is not None

    opens = rationale.open_intents(tmp_path)
    assert len(opens) == 1
    assert opens[0]["open"] is True
    assert opens[0]["predicted_fp"] == "fp-rl"


def test_supersession_splits_live_from_historical(tmp_path):
    init_store(tmp_path)
    old = rationale.record_rationale(tmp_path, subject=[{"op": "o1", "sha": None, "fp": "f"}],
                                     reason="use in-memory cache", actor="human", evidence=[], ts=1.0)
    rationale.record_rationale(tmp_path, subject=[{"op": "o1", "sha": None, "fp": "f"}],
                               reason="switched to redis for SSO", actor="human", evidence=[],
                               confirmed=True, ts=2.0,
                               relations=[{"type": "supersedes", "target": old}])

    recs = rationale.for_op(tmp_path, "o1")
    live = [r for r in recs if not r["superseded"]]
    historical = [r for r in recs if r["superseded"]]
    assert len(live) == 1 and live[0]["reason"] == "switched to redis for SSO"
    assert len(historical) == 1 and historical[0]["reason"] == "use in-memory cache"
    assert rationale.for_op(tmp_path, "o1")[0]["superseded"] is False  # live sorts first


def test_retired_open_intent_drops_from_open_list(tmp_path):
    init_store(tmp_path)
    opened = rationale.record_rationale(tmp_path, subject=[], reason="add rate limiting",
                                        actor="human", evidence=[], open=True, ts=1.0)
    # A later record fulfilling it supersedes the open one -> it leaves the open surface.
    rationale.record_rationale(tmp_path, subject=[{"op": "o9", "sha": None, "fp": "f"}],
                               reason="rate limiting added", actor="human", evidence=[], ts=2.0,
                               relations=[{"type": "supersedes", "target": opened}])
    assert rationale.open_intents(tmp_path) == []


def test_retire_open_is_idempotent(tmp_path):
    init_store(tmp_path)
    rid = rationale.record_rationale(tmp_path, subject=[], reason="wanted X", actor="human",
                                     evidence=[], open=True, ts=1.0)
    assert rationale.retire_open(tmp_path, rid) is not None
    assert rationale.open_intents(tmp_path) == []
    assert rationale.retire_open(tmp_path, rid) is None  # already retired -> no-op
    assert rationale.retire_open(tmp_path, "r-nonexistent") is None


def test_reflect_open_intents_records_pending_steps(tmp_path):
    """A closing session's still-pending steps become open intents (before their hollows vanish),
    carrying the step's reason and predicted footprint."""
    from sgt.loop import plan as plan_mod

    init_store(tmp_path)
    plan_mod._save_sessions(tmp_path, {"s1": {
        "plan_text": "p", "created_ts": 0.0, "last_activity_ts": 0.0, "status": "active",
        "baseline_op_ids": [], "steps": [
            {"hollow_id": "h1", "title": "add rate limiting", "predicted_footprint": ["a.py::rl"],
             "predicted_feature": None, "rationale": "", "status": "pending", "matched_op_ids": []},
            {"hollow_id": "h2", "title": "done step", "predicted_footprint": ["a.py::x"],
             "predicted_feature": None, "rationale": "", "status": "matched", "matched_op_ids": ["o1"]},
        ],
    }})

    ids = rationale.reflect_open_intents(tmp_path, "s1")

    assert len(ids) == 1  # only the pending step, not the matched one
    opens = rationale.open_intents(tmp_path)
    assert len(opens) == 1
    assert opens[0]["reason"] == "add rate limiting"
    assert opens[0]["predicted_fp"] is not None


def test_mark_done_reflects_open_intents_through_the_plan_verb(tmp_path):
    """Closing a session via the real `plan.mark_done` records its unfinished step as an open intent
    -- the wiring, not just the helper."""
    from sgt.loop import plan as plan_mod

    init_store(tmp_path)
    plan_mod._save_sessions(tmp_path, {"s1": {
        "plan_text": "p", "created_ts": 0.0, "last_activity_ts": 0.0, "status": "active",
        "baseline_op_ids": [], "steps": [
            {"hollow_id": "h1", "title": "the bit never finished", "predicted_footprint": ["a.py::z"],
             "predicted_feature": None, "rationale": "", "status": "pending", "matched_op_ids": []},
        ],
    }})

    assert plan_mod.mark_done(tmp_path, "s1") is True
    opens = rationale.open_intents(tmp_path)
    assert len(opens) == 1
    assert opens[0]["reason"] == "the bit never finished"
