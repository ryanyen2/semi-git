"""Tests for sgt.loop.match -- checkpoint matching + confirm (plan U14, R18/R21)."""

from __future__ import annotations

from sgt.core.op import make_op
from sgt.core.order import is_valid_ideal
from sgt.core.store import Store
from sgt.loop import match as match_mod
from sgt.loop import plan as plan_mod


def _hollow_step(store, session_id, i, title, predicted_footprint):
    footprint = {sym: (None, plan_mod._PENDING) for sym in predicted_footprint}
    footprint[f"__plan__::{session_id}::step{i}"] = (None, plan_mod._PENDING)
    hollow = make_op(footprint, {}, kind="planned", off_chain=True, intent=title)
    store.add_hollow(hollow)
    return {
        "hollow_id": hollow.id, "title": title, "predicted_footprint": list(predicted_footprint),
        "predicted_feature": None, "rationale": "", "status": "pending", "matched_op_ids": [],
    }


def _seed_session(repo, session_id, baseline_ids, step_specs, status="active"):
    store = Store(repo)
    steps = [_hollow_step(store, session_id, i, title, fp) for i, (title, fp) in enumerate(step_specs)]
    table = plan_mod._load_sessions(repo)
    table[session_id] = {
        "plan_text": "test plan", "created_ts": 0.0, "last_activity_ts": 0.0, "status": status,
        "baseline_op_ids": sorted(baseline_ids), "steps": steps,
    }
    plan_mod._save_sessions(repo, table)
    return steps


def _op(footprint, images=None):
    images = images or {sym: bytes(sym, "utf-8") for sym in footprint}
    return make_op({sym: (None, ver) for sym, ver in footprint.items()}, images)


# -- compute_checkpoint (pure) -----------------------------------------------------------------------

def test_compute_checkpoint_groups_two_steps_matched_by_one_op(tmp_path):
    """A 2:1 shape: one real commit's footprint overlaps two distinct steps' predictions enough
    to union-find them into a single group."""
    store = Store(tmp_path)
    _seed_session(tmp_path, "s1", set(), [
        ("step touches a and b", ["a.py::foo", "a.py::bar"]),
        ("step touches b and c", ["a.py::bar", "a.py::baz"]),
    ])
    op = store.add(_op({"a.py::foo": "v1", "a.py::bar": "v1", "a.py::baz": "v1"}))

    result = match_mod.compute_checkpoint(tmp_path)

    assert len(result.matches) == 1
    group = result.matches[0]
    assert group.session_id == "s1"
    assert len(group.hollow_ids) == 2
    assert group.op_ids == (op.id,)
    assert result.drift_op_ids == ()


def test_compute_checkpoint_flags_unpredicted_op_as_drift(tmp_path):
    store = Store(tmp_path)
    _seed_session(tmp_path, "s1", set(), [("step touches x", ["x.py::foo"])])
    unrelated = store.add(_op({"y.py::bar": "v1"}))

    result = match_mod.compute_checkpoint(tmp_path)

    assert result.matches == ()
    assert result.drift_op_ids == (unrelated.id,)


def test_low_overlap_below_threshold_is_drift_not_a_match(tmp_path):
    store = Store(tmp_path)
    _seed_session(tmp_path, "s1", set(), [("big step", ["a.py::a", "a.py::b", "a.py::c"])])
    # intersection={a.py::a}; union={a,b,c,z} -> jaccard = 1/4 = 0.25, below THRESHOLD (0.3)
    op = store.add(_op({"a.py::a": "v1", "z.py::z": "v1"}))

    result = match_mod.compute_checkpoint(tmp_path)

    assert result.matches == ()
    assert result.drift_op_ids == (op.id,)


def test_ops_already_in_baseline_are_ignored_entirely(tmp_path):
    store = Store(tmp_path)
    pre_existing = store.add(_op({"a.py::foo": "v1"}))
    _seed_session(tmp_path, "s1", {pre_existing.id}, [("step", ["a.py::foo"])])

    result = match_mod.compute_checkpoint(tmp_path)

    assert result.matches == () and result.drift_op_ids == ()


def test_non_active_session_is_ignored(tmp_path):
    store = Store(tmp_path)
    _seed_session(tmp_path, "s1", set(), [("step", ["a.py::foo"])], status="completed")
    store.add(_op({"a.py::foo": "v1"}))

    result = match_mod.compute_checkpoint(tmp_path)

    assert result.matches == () and result.drift_op_ids == ()


def test_hollow_ops_never_enter_all_ops_ideal_stays_valid(tmp_path):
    """R18: a hollow predicting an edit to an existing live symbol must never touch the ideal
    algebra -- no phantom fork, no change to `is_valid_ideal`."""
    store = Store(tmp_path)
    real = store.add(_op({"a.py::foo": "v1"}))
    _seed_session(tmp_path, "s1", {real.id}, [("edit foo again", ["a.py::foo"])])

    assert [op.id for op in store.all_ops()] == [real.id]
    assert is_valid_ideal(store.all_ops(), frozenset({real.id}))


# -- confirm_match (the only writer) -----------------------------------------------------------------

def test_confirm_match_marks_step_matched_records_intent_and_deletes_hollow(tmp_path):
    store = Store(tmp_path)
    steps = _seed_session(tmp_path, "s1", set(), [("do the thing", ["a.py::foo"])])
    op = store.add(_op({"a.py::foo": "v1"}))
    hollow_id = steps[0]["hollow_id"]

    match_mod.confirm_match(tmp_path, "s1", [hollow_id], [op.id])

    sessions = plan_mod.active_sessions(tmp_path)
    assert sessions["s1"]["steps"][0]["status"] == "matched"
    assert sessions["s1"]["steps"][0]["matched_op_ids"] == [op.id]
    assert store.get_hollow(hollow_id) is None  # consumed

    matches = match_mod.recorded_matches(tmp_path)
    assert matches[op.id]["session_id"] == "s1"
    assert matches[op.id]["hollow_ids"] == [hollow_id]
    assert matches[op.id]["intent"] == "do the thing"


def test_confirmed_match_never_resurfaces_as_drift(tmp_path):
    store = Store(tmp_path)
    steps = _seed_session(tmp_path, "s1", set(), [("step", ["a.py::foo"])])
    op = store.add(_op({"a.py::foo": "v1"}))
    match_mod.confirm_match(tmp_path, "s1", [steps[0]["hollow_id"]], [op.id])

    result = match_mod.compute_checkpoint(tmp_path)

    assert result.matches == () and result.drift_op_ids == ()
