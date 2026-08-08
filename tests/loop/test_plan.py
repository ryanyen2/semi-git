"""Tests for sgt.loop.plan -- plan intake, abandonment, staleness sweep (plan U14, R18/R21)."""

from __future__ import annotations

from pathlib import Path

import pytest

from sgt.core.op import make_op
from sgt.core.order import is_valid_ideal
from sgt.core.store import Store
from sgt.loop import plan as plan_mod

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _no_client(*args, **kwargs):
    raise RuntimeError("OPENAI_API_KEY not found in environment or .env")


# -- deterministic fallback decomposition (no client) ----------------------------------------------

def test_intake_falls_back_to_numbered_list_split_without_a_client(tmp_path, monkeypatch):
    monkeypatch.setattr(plan_mod, "get_client", _no_client)
    plan_text = "1. Add the greeting method\n2. Extract formatting into a helper\n"

    session = plan_mod.intake(tmp_path, plan_text, session_id="s1")

    assert session.status == "active"
    assert [s["title"] for s in session.steps] == [
        "Add the greeting method", "Extract formatting into a helper",
    ]
    assert all(s["predicted_footprint"] == [] and s["predicted_feature"] is None for s in session.steps)


def test_intake_falls_back_to_paragraph_split_without_a_numbered_list(tmp_path, monkeypatch):
    monkeypatch.setattr(plan_mod, "get_client", _no_client)
    plan_text = "First do this thing.\n\nThen do that other thing.\n"

    session = plan_mod.intake(tmp_path, plan_text, session_id="s1")

    assert len(session.steps) == 2


def test_intake_splits_a_bulleted_list_without_a_client(tmp_path, monkeypatch):
    # a realistic newline-only plan is bulleted, not numbered; it must not collapse to one step.
    monkeypatch.setattr(plan_mod, "get_client", _no_client)
    plan_text = "- Add the greeting method\n- Extract formatting into a helper\n* Wire it into the CLI\n"

    session = plan_mod.intake(tmp_path, plan_text, session_id="s1")

    assert [s["title"] for s in session.steps] == [
        "Add the greeting method", "Extract formatting into a helper", "Wire it into the CLI",
    ]


# -- hollow lifecycle (R18: off-chain, never a phantom fork) ----------------------------------------

def test_intake_mints_a_hollow_per_step_off_chain(tmp_path, monkeypatch):
    monkeypatch.setattr(plan_mod, "get_client", _no_client)
    session = plan_mod.intake(tmp_path, "1. step one\n2. step two\n", session_id="s1")

    store = Store(tmp_path)
    assert store.all_ops() == []  # hollows never enter the main chain
    for step in session.steps:
        hollow = store.get_hollow(step["hollow_id"])
        assert hollow is not None and hollow.off_chain and hollow.kind == "planned"


def test_intake_gives_distinct_ids_to_two_steps_with_identical_empty_footprint(tmp_path, monkeypatch):
    monkeypatch.setattr(plan_mod, "get_client", _no_client)
    session = plan_mod.intake(tmp_path, "1. step one\n2. step two\n", session_id="s1")

    ids = [s["hollow_id"] for s in session.steps]
    assert len(set(ids)) == 2  # the __plan__::session::stepN sentinel disambiguates the content-address


def test_intake_hollow_referencing_a_live_symbol_never_disturbs_the_existing_ideal(tmp_path, monkeypatch):
    monkeypatch.setattr(plan_mod, "get_client", _no_client)
    store = Store(tmp_path)
    real = store.add(make_op({"a.py::foo": (None, "v1")}, {"a.py::foo": b"1"}))

    session = plan_mod.intake(tmp_path, "1. touch a.py::foo again\n", session_id="s1")

    assert session.baseline_op_ids == (real.id,)
    assert [op.id for op in store.all_ops()] == [real.id]  # the hollow never joined the chain
    assert is_valid_ideal(store.all_ops(), frozenset({real.id}))


# -- abandon / staleness sweep -----------------------------------------------------------------------

def test_abandon_deletes_pending_hollows_and_the_session_record(tmp_path, monkeypatch):
    monkeypatch.setattr(plan_mod, "get_client", _no_client)
    session = plan_mod.intake(tmp_path, "1. step one\n2. step two\n", session_id="s1")
    store = Store(tmp_path)

    assert plan_mod.abandon(tmp_path, "s1") is True

    assert plan_mod.active_sessions(tmp_path) == {}
    for step in session.steps:
        assert store.get_hollow(step["hollow_id"]) is None


def test_abandon_unknown_session_returns_false(tmp_path):
    assert plan_mod.abandon(tmp_path, "no-such-session") is False


def test_sweep_stale_sessions_abandons_only_what_aged_out(tmp_path, monkeypatch):
    monkeypatch.setattr(plan_mod, "get_client", _no_client)
    plan_mod.intake(tmp_path, "1. old\n", session_id="old")
    plan_mod.intake(tmp_path, "1. fresh\n", session_id="fresh")
    table = plan_mod._load_sessions(tmp_path)
    table["old"]["last_activity_ts"] = 0.0
    table["fresh"]["last_activity_ts"] = 1000.0
    plan_mod._save_sessions(tmp_path, table)

    abandoned = plan_mod.sweep_stale_sessions(tmp_path, max_age_seconds=100, now=1000.0)

    assert abandoned == ["old"]
    assert set(plan_mod.active_sessions(tmp_path)) == {"fresh"}


# -- terminal status: active_sessions is a *review surface*, not the whole table --------------------

def test_active_sessions_returns_only_active_not_completed(tmp_path, monkeypatch):
    """`active_sessions` is the review surface -- the plans still being worked. A completed or
    abandoned plan is history and must drop out of it, so the workbench/status-bar stop rendering
    a lingering pile of finished plans (the "so many unresolved plans" clutter). The full table is
    still readable via `_load_sessions` for provenance."""
    monkeypatch.setattr(plan_mod, "get_client", _no_client)
    plan_mod.intake(tmp_path, "1. live work\n", session_id="live")
    plan_mod.intake(tmp_path, "1. finished work\n", session_id="done")
    table = plan_mod._load_sessions(tmp_path)
    table["done"]["status"] = "completed"
    plan_mod._save_sessions(tmp_path, table)

    assert set(plan_mod.active_sessions(tmp_path)) == {"live"}
    assert set(plan_mod._load_sessions(tmp_path)) == {"live", "done"}  # completed kept as history


def test_mark_done_completes_a_session_and_drops_it_from_active(tmp_path, monkeypatch):
    """An agent that finishes its plan closes it explicitly with `mark_done`: the record survives
    as `completed` history (unlike `abandon`, which deletes it), its still-pending hollows are
    cleaned up, and it leaves the active review surface."""
    monkeypatch.setattr(plan_mod, "get_client", _no_client)
    session = plan_mod.intake(tmp_path, "1. step one\n2. step two\n", session_id="s1")
    store = Store(tmp_path)

    assert plan_mod.mark_done(tmp_path, "s1") is True

    assert plan_mod.active_sessions(tmp_path) == {}
    assert plan_mod._load_sessions(tmp_path)["s1"]["status"] == "completed"
    for step in session.steps:  # pending hollows cleaned up, like abandon
        assert store.get_hollow(step["hollow_id"]) is None


def test_mark_done_unknown_session_returns_false(tmp_path):
    assert plan_mod.mark_done(tmp_path, "no-such-session") is False


def test_intake_reaps_stale_sessions_before_creating_a_new_one(tmp_path, monkeypatch):
    """Intake is the natural housekeeping beat: before minting a new plan it reaps any active
    session that has aged out past `STALE_SECONDS`, so a walked-away half-done plan doesn't linger
    forever. A fresh session is untouched."""
    monkeypatch.setattr(plan_mod, "get_client", _no_client)
    plan_mod.intake(tmp_path, "1. ancient\n", session_id="ancient")
    table = plan_mod._load_sessions(tmp_path)
    table["ancient"]["last_activity_ts"] = 0.0  # far in the past
    plan_mod._save_sessions(tmp_path, table)

    plan_mod.intake(tmp_path, "1. brand new\n", session_id="new")

    assert set(plan_mod.active_sessions(tmp_path)) == {"new"}  # ancient reaped on intake


# -- real LLM, grounded in a real repo (plan U14's own verification requirement) --------------------

def test_intake_grounds_predicted_feature_in_a_real_feature_id_via_live_llm(tmp_path):
    """Not mocked -- when a key is available (this project's own `.env` or the environment) it is
    exercised for real against a real fixture repo's own feature tree, so `predicted_feature`
    names an id `sgt map` actually produced, never a hallucinated one. Skipped only on a clean
    checkout with no key at all, where the LLM path cannot run and the rationale assertion below
    would fail against the deterministic fallback."""
    import os

    from sgt.api import map_view
    from sgt.config import load_env
    from sgt.core.lens import get
    from sgt.lens.map import build_map
    from tests.laws import corpus

    load_env(_REPO_ROOT)  # populate OPENAI_API_KEY from this project's own .env, once
    if not os.environ.get("OPENAI_API_KEY"):
        pytest.skip("requires a live OPENAI_API_KEY (.env or environment); LLM path cannot run")

    repo = corpus.CORPUS["class_with_methods"].build(tmp_path / "repo")
    get(repo)
    build_map(repo)
    tree = map_view(repo)
    real_feature_ids = {n["id"] for n in tree["nodes"] if n["kind"] == "feature"}
    assert real_feature_ids  # sanity: the fixture actually produced a feature

    plan_text = (
        "1. Add a farewell method to Service, mirroring how label() formats name.\n"
        "2. Extract the uppercase formatting in _format into a shared string utility.\n"
    )
    session = plan_mod.intake(repo, plan_text)

    assert session.status == "active"
    assert len(session.steps) == 2
    assert any(s["rationale"] for s in session.steps)  # evidence the LLM path ran, not the fallback
    for step in session.steps:
        if step["predicted_feature"] is not None:
            assert step["predicted_feature"] in real_feature_ids


# -- resume: re-taking a session id you own ------------------------------------------------------

def test_re_intake_of_an_active_session_keeps_its_baseline(tmp_path, monkeypatch):
    """An interrupted agent restarts and calls intake again with the id it owns -- the skill tells
    it to pick a stable one. Minting a fresh baseline there silently reclassified everything it had
    already built as drift: the work was still in the store, but no longer attributable to the plan
    that produced it."""
    monkeypatch.setattr(plan_mod, "get_client", _no_client)
    store = Store(tmp_path)
    first = plan_mod.intake(tmp_path, "1. step one\n2. step two\n", session_id="mine")

    # Work lands after intake -- exactly what a baseline exists to exclude.
    store.add(make_op({"a.py::foo": (None, "v1")}, {"a.py::foo": b"def foo(): pass\n"}))

    resumed = plan_mod.intake(tmp_path, "1. step one\n2. step two\n", session_id="mine")

    assert resumed.baseline_op_ids == first.baseline_op_ids
    assert resumed.created_ts == first.created_ts


def test_re_intake_does_not_leave_the_superseded_hollows_behind(tmp_path, monkeypatch):
    """A step the resumed plan no longer has still had a hollow on disk with nothing pointing at
    it, which kept matching against work until the 7-day sweep noticed. (A step the new plan still
    has at the same position keeps its hollow: the id is content-addressed over a per-index plan
    sentinel, so re-creating it is the same file, not a leak.)"""
    monkeypatch.setattr(plan_mod, "get_client", _no_client)
    store = Store(tmp_path)
    first = plan_mod.intake(tmp_path, "1. step one\n2. step two\n", session_id="mine")
    dropped = first.steps[1]["hollow_id"]  # the resumed plan has no second step

    resumed = plan_mod.intake(tmp_path, "1. a different step\n", session_id="mine")

    assert len(resumed.steps) == 1
    assert not (store.hollow_dir / dropped).is_file()
    assert (store.hollow_dir / resumed.steps[0]["hollow_id"]).is_file()


def test_re_intake_keeps_a_claude_session_id_it_cannot_restate(tmp_path, monkeypatch):
    """The captured id is the entire resume affordance; a resume that cannot read its own must not
    erase the one the original intake recorded."""
    monkeypatch.setattr(plan_mod, "get_client", _no_client)
    plan_mod.intake(tmp_path, "1. step one\n", session_id="mine", claude_session_id="chat-1")

    plan_mod.intake(tmp_path, "1. step one\n", session_id="mine")

    assert plan_mod._load_sessions(tmp_path)["mine"]["claude_session_id"] == "chat-1"


def test_a_fresh_session_id_still_takes_a_current_baseline(tmp_path, monkeypatch):
    monkeypatch.setattr(plan_mod, "get_client", _no_client)
    store = Store(tmp_path)
    store.add(make_op({"a.py::foo": (None, "v1")}, {"a.py::foo": b"def foo(): pass\n"}))

    session = plan_mod.intake(tmp_path, "1. step one\n", session_id="brand-new")

    assert len(session.baseline_op_ids) == 1  # the already-present op is not this plan's work


def test_sweep_never_closes_a_session_the_save_just_asked_to_resolve(tmp_path, monkeypatch):
    """`sgt save` printed "run `sgt save --resolve-plan` to settle this" and, in the same beat,
    auto-closed the session as walked-away -- so the advertised next command answered "not a known
    pending hollow id". The user has not walked away from a plan they were invited to finish one
    line earlier, however long it has been quiet."""
    monkeypatch.setattr(plan_mod, "get_client", _no_client)
    store = Store(tmp_path)
    session = plan_mod.intake(tmp_path, "1. touch foo\n", session_id="ambiguous")

    # Make it look walked-away, which is what the sweep reaps.
    table = plan_mod._load_sessions(tmp_path)
    table["ambiguous"]["last_activity_ts"] = 0.0
    plan_mod._save_sessions(tmp_path, table)

    closed = plan_mod.sweep_built_sessions(tmp_path, exclude=frozenset({"ambiguous"}))

    assert "ambiguous" not in closed
    assert plan_mod.active_sessions(tmp_path).get("ambiguous") is not None
    # The hollow the resolve command needs is still there.
    assert (store.hollow_dir / session.steps[0]["hollow_id"]).is_file()
