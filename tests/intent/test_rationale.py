"""Tests for ugh.intent.rationale -- the intent ledger's derived reflection layer (M1): local
rationale records, idempotent by (subject, reason, actor), the unfulfilled-intent (`open`) surface,
and supersession as the historical/live signal `for_op` reads. The confirm_match -> reflect
planned path is covered end-to-end in tests/loop/test_match.py."""

from __future__ import annotations

from ugh.intent import rationale
from ugh.store.gitbind import init_store


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


def test_score_fields_are_not_part_of_identity(tmp_path):
    # Re-scoring the same claim (same subject/reason/actor) is the SAME id -- confidence/signals do
    # not identify a record. Without a supersedes relation the re-score therefore no-ops, which is
    # exactly why re-scoring must supersede (next test).
    init_store(tmp_path)
    a = rationale.record_rationale(tmp_path, subject=[{"op": "o1", "sha": None, "fp": "f"}],
                                   reason="r", actor="human", evidence=[], confidence=0.5)
    b = rationale.record_rationale(tmp_path, subject=[{"op": "o1", "sha": None, "fp": "f"}],
                                   reason="r", actor="human", evidence=[], confidence=0.9)
    assert a == b
    assert len(rationale.load_rationale(tmp_path)) == 1
    assert rationale.load_rationale(tmp_path)[a]["confidence"] == 0.5  # first write stands


def test_retired_open_intent_drops_from_open_list(tmp_path):
    init_store(tmp_path)
    opened = rationale.record_rationale(tmp_path, subject=[], reason="add rate limiting",
                                        actor="human", evidence=[], open=True, ts=1.0)
    # A later record fulfilling it supersedes the open one -> it leaves the open surface.
    rationale.record_rationale(tmp_path, subject=[{"op": "o9", "sha": None, "fp": "f"}],
                               reason="rate limiting added", actor="human", evidence=[], ts=2.0,
                               relations=[{"type": "supersedes", "target": opened}])
    assert rationale.open_intents(tmp_path) == []


def test_retiring_two_open_intents_retires_both(tmp_path):
    """Closing records are identical in (subject, reason, actor) across retires -- only their
    `supersedes` target differs. Relations must therefore be identity, or the second retire
    collides onto the first closing record's id and silently no-ops (testbed 2026-07-31)."""
    init_store(tmp_path)
    r1 = rationale.record_rationale(tmp_path, subject=[], reason="intent one", actor="human",
                                    evidence=[], open=True, ts=1.0)
    r2 = rationale.record_rationale(tmp_path, subject=[], reason="intent two", actor="human",
                                    evidence=[], open=True, ts=2.0)

    assert rationale.retire_open(tmp_path, r1) is not None
    assert rationale.retire_open(tmp_path, r2) is not None
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
    from ugh.loop import plan as plan_mod

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


def _one_pending_session(tmp_path, plan_mod, title="the bit never finished"):
    plan_mod._save_sessions(tmp_path, {"s1": {
        "plan_text": "p", "created_ts": 0.0, "last_activity_ts": 0.0, "status": "active",
        "baseline_op_ids": [], "steps": [
            {"hollow_id": "h1", "title": title, "predicted_footprint": ["a.py::z"],
             "predicted_feature": None, "rationale": "", "status": "pending", "matched_op_ids": []},
        ],
    }})


def test_mark_done_does_not_mint_open_intents(tmp_path):
    """`mark_done` asserts the work IS finished (done differently than predicted), so its pending
    steps must NOT become open intents -- on the testbed every such record was already-landed noise
    filling `ugh intent open` and recall."""
    from ugh.loop import plan as plan_mod

    init_store(tmp_path)
    _one_pending_session(tmp_path, plan_mod)

    assert plan_mod.mark_done(tmp_path, "s1") is True
    assert rationale.open_intents(tmp_path) == []


def test_abandon_reflects_open_intents_through_the_plan_verb(tmp_path):
    """Walking away (`plan.abandon`) is the genuinely-unfulfilled close: its pending steps DO
    become open intents -- the wiring, not just the helper."""
    from ugh.loop import plan as plan_mod

    init_store(tmp_path)
    _one_pending_session(tmp_path, plan_mod)

    assert plan_mod.abandon(tmp_path, "s1") is True
    opens = rationale.open_intents(tmp_path)
    assert len(opens) == 1
    assert opens[0]["reason"] == "the bit never finished"


def test_auto_retire_ages_out_a_stale_open_intent(tmp_path):
    """Age-retire (intent-ledger P1): an open intent no one has acted on in over `max_age_days` is
    no longer 'what needs attention' -- it is superseded automatically, keeping the residual
    drainable without an open/done queue to groom."""
    rid = rationale.record_rationale(tmp_path, subject=[], reason="wire the backoff", actor="human",
                                     evidence=[], open=True, predicted_symbols=[], ts=100.0)
    assert rid in {r["id"] for r in rationale.open_intents(tmp_path)}
    retired = rationale.auto_retire_open(tmp_path, now=100.0 + 40 * 86400)
    assert rid in retired
    assert rid not in {r["id"] for r in rationale.open_intents(tmp_path)}  # left the open surface
    assert rid in rationale.load_rationale(tmp_path)  # history is kept; only its standing changed


def test_auto_retire_keeps_a_fresh_open_intent(tmp_path):
    """A recent open intent is left alone -- age-retire only fires past the threshold, and with no
    predicted symbols there is nothing to overlap-retire against."""
    rid = rationale.record_rationale(tmp_path, subject=[], reason="still pending", actor="human",
                                     evidence=[], open=True, predicted_symbols=[], ts=100.0)
    assert rationale.auto_retire_open(tmp_path, now=100.0 + 3 * 86400) == []
    assert rid in {r["id"] for r in rationale.open_intents(tmp_path)}


def test_auto_retire_overlap_retires_when_predicted_symbols_landed(tmp_path):
    """Overlap-retire: when every predicted symbol of a stated-but-never-landed intent is now live
    in the ideal, the work landed (in a plan or out), so the intent is fulfilled and retired -- even
    though it never went through `confirm_match`."""
    from ugh.core.lens import get
    from ugh.store.gitbind import init_store as _init

    gb, _ = _init(tmp_path)
    (tmp_path / "a.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    gb.commit_all("add foo")
    get(tmp_path)  # a.py::foo is now live in the ideal

    rid = rationale.record_rationale(tmp_path, subject=[], reason="build foo", actor="human",
                                     evidence=[], open=True, predicted_symbols=["a.py::foo"], ts=100.0)
    retired = rationale.auto_retire_open(tmp_path, now=100.0)  # fresh (not aged) -> overlap only
    assert rid in retired


def test_auto_retire_overlap_needs_full_coverage(tmp_path):
    """A partial footprint match is too weak a signal to silently close a stated intent: overlap
    retires only when *every* predicted symbol is live, so a half-landed step stays on the residual
    (residual honesty is worth an occasional un-retired item over a wrong-retired one)."""
    from ugh.core.lens import get
    from ugh.store.gitbind import init_store as _init

    gb, _ = _init(tmp_path)
    (tmp_path / "a.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    gb.commit_all("add foo")
    get(tmp_path)

    rid = rationale.record_rationale(tmp_path, subject=[], reason="build foo and bar", actor="human",
                                     evidence=[], open=True,
                                     predicted_symbols=["a.py::foo", "a.py::bar"], ts=100.0)
    assert rationale.auto_retire_open(tmp_path, now=100.0) == []  # a.py::bar never landed
    assert rid in {r["id"] for r in rationale.open_intents(tmp_path)}
