"""Tests for sgt.loop.match -- checkpoint matching + confirm (plan U14, R18/R21)."""

from __future__ import annotations

from sgt.core.op import Attribution, make_op
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
    # Under the overlap coefficient (|a∩b| / min(|a|,|b|)) a match is "drift" only when the shared
    # entities are a small fraction of the *smaller* footprint -- i.e. the op is mostly unrelated
    # work that merely brushes the step. Here the step and the op are the same size (4) and share
    # just one entity: overlap = 1/4 = 0.25 < THRESHOLD (0.3), so the op is drift, not a match.
    _seed_session(tmp_path, "s1", set(), [("big step", ["a.py::a", "a.py::b", "a.py::c", "a.py::d"])])
    op = store.add(_op({"a.py::a": "v1", "y.py::x": "v1", "y.py::y": "v1", "y.py::z": "v1"}))

    result = match_mod.compute_checkpoint(tmp_path)

    assert result.matches == ()
    assert result.drift_op_ids == (op.id,)


def test_standalone_anchor_op_is_invisible_to_drift(tmp_path):
    """A per-entity anchor op is pure ordering metadata -- the companion of whatever save placed
    the entity, not behavioral work done outside a plan. Like residue, it is invisible to the drift
    layer even when it matches no step: reporting it as drift would make a fully-planned build
    (whose bytes live in a coarse whole-file content op the anchor merely positions) read as mostly
    unplanned. The op names an unrelated entity, so it also brushes no step -> neither match nor
    drift."""
    store = Store(tmp_path)
    _seed_session(tmp_path, "s1", set(), [("build foo", ["a.py::foo"])])
    store.add(_op({"a.py::__anchor__::Widget": "v1"}))

    result = match_mod.compute_checkpoint(tmp_path)

    assert result.matches == () and result.drift_op_ids == ()


def test_anchor_still_contributes_a_matching_edge(tmp_path):
    """The drift-exclusion above must not cost anchors their *matching* power: when a batch save
    folds an entity's bytes into a coarse op, the per-entity anchor is the finest 'this entity was
    touched here' signal, and a step naming that entity must still match it (the F5 property)."""
    store = Store(tmp_path)
    _seed_session(tmp_path, "s1", set(), [("build RGA", ["crdt.py::RGA"])])
    op = store.add(_op({"crdt.py::__anchor__::RGA": "v1"}))

    result = match_mod.compute_checkpoint(tmp_path)

    assert len(result.matches) == 1
    assert result.matches[0].op_ids == (op.id,)
    assert result.drift_op_ids == ()


# -- granularity: a file-level plan prediction vs symbol-level ops (F16) -----------------------------

def test_file_level_step_prediction_matches_symbol_level_ops_in_that_file(tmp_path):
    """A step predicted at *file* granularity (the LLM decomposer's common output -- "work in
    livehub/server.py") must match the symbol-level ops that implement it (`server.py::Server`,
    `server.py::encode_op`). The step names a file, the ops name entities *in* that file: identity
    by qualname alone can never join them, because a bare file has no qualname. The file scope is
    the join key a file-granularity prediction is written at."""
    store = Store(tmp_path)
    _seed_session(tmp_path, "s1", set(), [("build the server transport", ["livehub/server.py"])])
    op = store.add(_op({"livehub/server.py::Server": "v1", "livehub/server.py::encode_op": "v1"}))

    result = match_mod.compute_checkpoint(tmp_path)

    assert len(result.matches) == 1
    assert result.matches[0].op_ids == (op.id,)
    assert result.drift_op_ids == ()


def test_file_level_prediction_does_not_match_ops_in_other_files(tmp_path):
    """A file-level prediction is coarse but not blind: it matches ops touching *its* file, not
    ops touching a different one."""
    store = Store(tmp_path)
    _seed_session(tmp_path, "s1", set(), [("work in server", ["livehub/server.py"])])
    op = store.add(_op({"livehub/client.py::RemoteBus": "v1"}))

    result = match_mod.compute_checkpoint(tmp_path)

    assert result.matches == ()
    assert result.drift_op_ids == (op.id,)


def test_entity_prediction_still_matches_across_a_file_move(tmp_path):
    """The drift-tolerance the qualname join buys must survive the file-scope addition: a step
    predicted `rga.py::RGA` matches the op that actually built it in `crdt.py::RGA`. The file
    differs; identity is still the qualname. File-scope matching applies only to *bare-file*
    predictions -- an entity-level prediction never gates on the planner's file guess."""
    store = Store(tmp_path)
    _seed_session(tmp_path, "s1", set(), [("build RGA", ["rga.py::RGA"])])
    op = store.add(_op({"crdt.py::RGA": "v1"}))

    result = match_mod.compute_checkpoint(tmp_path)

    assert len(result.matches) == 1
    assert result.matches[0].op_ids == (op.id,)
    assert result.drift_op_ids == ()


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

    # The full table (not `active_sessions`, which now excludes the just-completed session).
    sessions = plan_mod._load_sessions(tmp_path)
    assert sessions["s1"]["steps"][0]["status"] == "matched"
    assert sessions["s1"]["steps"][0]["matched_op_ids"] == [op.id]
    assert store.get_hollow(hollow_id) is None  # consumed

    matches = match_mod.recorded_matches(tmp_path)
    assert matches[op.id]["session_id"] == "s1"
    assert matches[op.id]["hollow_ids"] == [hollow_id]
    assert matches[op.id]["intent"] == "do the thing"


def test_confirm_match_completing_every_step_marks_the_session_completed(tmp_path):
    """When a confirm leaves no pending step, the session is done -- it flips to `completed` and
    leaves the active review surface, so a fully-built plan stops showing up as an "unresolved"
    plan forever (nothing ever closed a session before this). A session with a step still pending
    stays active."""
    store = Store(tmp_path)
    steps = _seed_session(tmp_path, "s1", set(), [
        ("step a", ["a.py::foo"]),
        ("step b", ["b.py::bar"]),
    ])
    op_a = store.add(_op({"a.py::foo": "v1"}))
    op_b = store.add(_op({"b.py::bar": "v1"}))

    match_mod.confirm_match(tmp_path, "s1", [steps[0]["hollow_id"]], [op_a.id])
    assert "s1" in plan_mod.active_sessions(tmp_path)  # one step still pending -> still active

    match_mod.confirm_match(tmp_path, "s1", [steps[1]["hollow_id"]], [op_b.id])
    assert "s1" not in plan_mod.active_sessions(tmp_path)  # last step matched -> completed
    assert plan_mod._load_sessions(tmp_path)["s1"]["status"] == "completed"


def test_confirmed_match_never_resurfaces_as_drift(tmp_path):
    store = Store(tmp_path)
    steps = _seed_session(tmp_path, "s1", set(), [("step", ["a.py::foo"])])
    op = store.add(_op({"a.py::foo": "v1"}))
    match_mod.confirm_match(tmp_path, "s1", [steps[0]["hollow_id"]], [op.id])

    result = match_mod.compute_checkpoint(tmp_path)

    assert result.matches == () and result.drift_op_ids == ()


def test_confirm_match_reflects_a_local_rationale_record(tmp_path):
    """Intent-ledger M1 planned path: confirming a match transcribes it into a local rationale
    record -- reason from the fulfilled step, the matched op as subject, actor human, inferred
    (unconfirmed). The evidence turn only exists if the plan came through `intake`; a directly
    seeded session has none, and the record is still valid (its reason came from the step)."""
    from sgt.intent import rationale as rationale_mod

    store = Store(tmp_path)
    steps = _seed_session(tmp_path, "s1", set(), [("add the login guard", ["a.py::foo"])])
    op = store.add(_op({"a.py::foo": "v1"}))

    match_mod.confirm_match(tmp_path, "s1", [steps[0]["hollow_id"]], [op.id])

    recs = rationale_mod.for_op(tmp_path, op.id)
    assert len(recs) == 1
    assert recs[0]["reason"] == "add the login guard"
    assert recs[0]["actor"] == "human"
    assert recs[0]["confirmed"] is False
    assert recs[0]["subject"][0]["op"] == op.id


# -- structured provenance stamping (plan U22, D7) ---------------------------------------------

def test_confirm_match_stamps_the_session_onto_the_matched_ops_provenance(tmp_path):
    """After confirmation the matched op's structured provenance carries the fulfilling session
    for each of its witnessing shas -- the immutable op payload (and its id) is untouched."""
    store = Store(tmp_path)
    steps = _seed_session(tmp_path, "s1", set(), [("do the thing", ["a.py::foo"])])
    op = store.add(make_op({"a.py::foo": (None, "v1")}, {"a.py::foo": b"body"}, provenance=("shaX",)))

    match_mod.confirm_match(tmp_path, "s1", [steps[0]["hollow_id"]], [op.id])

    stored = store.get(op.id)
    assert stored.id == op.id
    assert stored.attribution == (Attribution(sha="shaX", session="s1"),)


def test_stamp_drift_stamps_the_session_onto_named_ops(tmp_path):
    """`stamp_drift` is the sibling writer for drift ops a caller explicitly names -- same
    mechanism, no checkpoint required."""
    store = Store(tmp_path)
    op = store.add(make_op({"a.py::bar": (None, "v1")}, {"a.py::bar": b"body"}, provenance=("shaY",)))

    match_mod.stamp_drift(tmp_path, "s2", [op.id])

    assert store.get(op.id).attribution == (Attribution(sha="shaY", session="s2"),)


# -- session_coverage / auto-close (file-level "looks built", not a name-exact match) --------------

def test_session_coverage_flags_work_that_landed_under_a_different_name(tmp_path):
    """The core of auto-close: a step predicts `bus.py::publish`, the real op lands
    `bus.py::publish_to_all`. The name-exact matcher never joins them, but the work is in the
    predicted file -- so coverage recognises it as built."""
    store = Store(tmp_path)
    _seed_session(tmp_path, "s1", set(), [("write publish", ["bus.py::publish"])])
    store.add(_op({"bus.py::publish_to_all": "v1"}))

    assert match_mod.compute_checkpoint(tmp_path).matches == ()  # exact matcher misses (name differs)
    cov = match_mod.session_coverage(tmp_path)["s1"]
    assert cov["fully_built"] is True
    assert cov["pending"][0]["covered"] is True


def test_session_coverage_uncovered_when_predicted_file_untouched(tmp_path):
    """The wrong-file case (planned `connection.py`, built in `server.py`): coverage stays False
    and the reason names the predicted file that never appeared -- the "why isn't this matched?"
    explanation."""
    store = Store(tmp_path)
    _seed_session(tmp_path, "s1", set(), [("define Connection", ["connection.py"])])
    store.add(_op({"server.py::Connection": "v1"}))

    cov = match_mod.session_coverage(tmp_path)["s1"]
    assert cov["fully_built"] is False
    step = cov["pending"][0]
    assert step["covered"] is False
    assert "connection.py" in step["reason"]


def test_session_coverage_ignores_pre_baseline_ops(tmp_path):
    """Only work since the plan's baseline is evidence -- a file that already carried its op at
    intake does not prove the (later-planned) step was built."""
    store = Store(tmp_path)
    pre = store.add(_op({"bus.py::old": "v1"}))
    _seed_session(tmp_path, "s1", {pre.id}, [("write publish", ["bus.py::publish"])])

    assert match_mod.session_coverage(tmp_path)["s1"]["pending"][0]["covered"] is False


def test_sweep_built_sessions_closes_stalled_but_leaves_active(tmp_path):
    """Auto-close reaps a walked-away-but-built plan yet never races one still being built: both
    sessions are fully file-covered, but only the quiet (stalled) one is closed."""
    store = Store(tmp_path)
    _seed_session(tmp_path, "stalled", set(), [("s", ["bus.py::publish"])])   # last_activity_ts = 0.0
    _seed_session(tmp_path, "active", set(), [("s", ["crdt.py::rga"])])
    store.add(_op({"bus.py::publish_impl": "v1"}))
    store.add(_op({"crdt.py::rga_impl": "v1"}))
    table = plan_mod._load_sessions(tmp_path)
    table["active"]["last_activity_ts"] = 10_000.0  # recent as of `now` below -> protected
    plan_mod._save_sessions(tmp_path, table)

    closed = plan_mod.sweep_built_sessions(tmp_path, now=10_000.0)  # stalled quiet 10000 > STALLED_SECONDS

    assert closed == ["stalled"]
    active = plan_mod.active_sessions(tmp_path)
    assert "stalled" not in active   # mark_done -> completed -> off the review surface
    assert "active" in active        # still building, untouched
