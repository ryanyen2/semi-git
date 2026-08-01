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
    filling `sgt intent open` and recall."""
    from sgt.loop import plan as plan_mod

    init_store(tmp_path)
    _one_pending_session(tmp_path, plan_mod)

    assert plan_mod.mark_done(tmp_path, "s1") is True
    assert rationale.open_intents(tmp_path) == []


def test_abandon_reflects_open_intents_through_the_plan_verb(tmp_path):
    """Walking away (`plan.abandon`) is the genuinely-unfulfilled close: its pending steps DO
    become open intents -- the wiring, not just the helper."""
    from sgt.loop import plan as plan_mod

    init_store(tmp_path)
    _one_pending_session(tmp_path, plan_mod)

    assert plan_mod.abandon(tmp_path, "s1") is True
    opens = rationale.open_intents(tmp_path)
    assert len(opens) == 1
    assert opens[0]["reason"] == "the bit never finished"


def test_edit_supersedes_with_a_confirmed_user_record(tmp_path):
    init_store(tmp_path)
    old = rationale.record_rationale(tmp_path, subject=[{"op": "o1", "sha": None, "fp": "f"}],
                                     reason="inferred guess", actor="human", evidence=["t1"], ts=1.0)

    new = rationale.edit_rationale(tmp_path, old[:10], "actually: unblock the SSO migration")

    assert new is not None
    recs = rationale.for_op(tmp_path, "o1")
    live = [r for r in recs if not r["superseded"]]
    assert live[0]["reason"] == "actually: unblock the SSO migration"
    assert live[0]["confirmed"] is True and live[0]["actor"] == "human"
    assert live[0]["recorded_by"] == "user"  # the writer distinction lives here, not in actor
    assert rationale.edit_rationale(tmp_path, "r-nope", "x") is None  # unknown prefix -> no guess


def test_recall_matches_rationale_by_symbol_overlap_and_lists_open(tmp_path):
    from sgt.core.op import make_op
    from sgt.core.store import Store

    init_store(tmp_path)
    op = Store(tmp_path).add(make_op({"a.py::foo": (None, "v1")}, {"a.py::foo": b"x"}))
    rationale.record_rationale(tmp_path, subject=[{"op": op.id, "sha": None, "fp": "f"}],
                               reason="foo guards the session", actor="human", evidence=[], ts=1.0)
    rationale.record_rationale(tmp_path, subject=[], reason="add rate limiting", actor="human",
                               evidence=[], open=True, ts=2.0)

    hit = rationale.recall(tmp_path, ["a.py::foo"])
    assert hit["rationale"][0]["reason"] == "foo guards the session"
    assert hit["rationale"][0]["symbols"] == ["a.py::foo"]
    assert hit["open_intents"][0]["reason"] == "add rate limiting"
    assert rationale.recall(tmp_path, ["z.py::none"])["rationale"] == []  # no overlap -> no noise


def test_recall_matches_symbols_leniently_by_path_suffix(tmp_path):
    """Agents name the basename form (`__main__.py::add`) while the miner stores the
    repo-relative form (`pkg/__main__.py::add`) -- recall must join the two (testbed 2026-07-31:
    the exact-set join silently returned nothing for a real agent query). Symbol names still
    match exactly: `add` never answers for `radd`."""
    from sgt.core.op import make_op
    from sgt.core.store import Store

    init_store(tmp_path)
    op = Store(tmp_path).add(make_op({"pkg/__main__.py::add": (None, "v1")},
                                     {"pkg/__main__.py::add": b"x"}))
    rationale.record_rationale(tmp_path, subject=[{"op": op.id, "sha": None, "fp": "f"}],
                               reason="add parses the priority flag", actor="human",
                               evidence=[], ts=1.0)

    hit = rationale.recall(tmp_path, ["__main__.py::add"])
    assert hit["rationale"][0]["reason"] == "add parses the priority flag"
    assert hit["rationale"][0]["symbols"] == ["pkg/__main__.py::add"]  # reports the mined form
    assert rationale.recall(tmp_path, ["__main__.py::list_tasks"])["rationale"] == []
    assert rationale.recall(tmp_path, ["ain__.py::add"])["rationale"] == []  # not a path suffix


def test_why_view_aggregates_symbol_rationale_across_ops(tmp_path):
    """A symbol query resolves to ONE op (usually the latest), but the recorded "why" for the
    symbol lives on every op that touched it -- `why_view` must surface the older ops' rationale
    too (testbed 2026-07-31: the first feature's reasoning went invisible the moment a second
    commit touched the same function)."""
    from sgt.api import why_view
    from sgt.core.lens import get
    from sgt.core.store import Store
    from sgt.lens import tree
    from sgt.store.gitbind import init_store as _init

    gb, _ = _init(tmp_path)
    (tmp_path / "a.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    gb.commit_all("v1")
    (tmp_path / "a.py").write_text("def foo():\n    return 2\n", encoding="utf-8")
    gb.commit_all("v2")
    get(tmp_path)

    # `select.why` needs a feature tree to attribute against; hand-craft the minimal one
    # (same rationale as tests/lens/test_select.py -- this test is about the rationale join,
    # not clustering quality).
    nodes = {"F-A": {"parent": None, "children": [], "members": ["a.py::foo"], "size": 1,
                     "dir": "", "label": "Foo"}}
    all_ops = Store(tmp_path).all_ops()
    tree.save(tmp_path, {"nodes": nodes, "roots": ["F-A"],
                         "op_leaf": tree.assign_ops_to_leaves(nodes, all_ops),
                         "max_depth": 0, "cannot_link_moves": [], "identity_events": []})

    ops = sorted(Store(tmp_path).all_ops(), key=lambda o: min(o.provenance))
    older = ops[0]
    rationale.record_rationale(tmp_path, subject=[{"op": older.id, "sha": None, "fp": "f"}],
                               reason="v1 guards the invariant", actor="human", evidence=[], ts=1.0)

    view = why_view(tmp_path, "a.py::foo")
    assert view["ok"]
    assert "v1 guards the invariant" in [r["reason"] for r in view["rationale"]]
