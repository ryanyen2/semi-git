"""Tests for sgt.loop.plan -- plan intake, abandonment, staleness sweep (plan U14, R18/R21)."""

from __future__ import annotations

from pathlib import Path

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


# -- real LLM, grounded in a real repo (plan U14's own verification requirement) --------------------

def test_intake_grounds_predicted_feature_in_a_real_feature_id_via_live_llm(tmp_path):
    """Not mocked, not skipped -- the key (this project's own `.env`, already verified working) is
    exercised for real against a real fixture repo's own feature tree, so `predicted_feature`
    names an id `sgt map` actually produced, never a hallucinated one."""
    from sgt.api import map_view
    from sgt.config import load_env
    from sgt.core.lens import get
    from sgt.lens.map import build_map
    from tests.laws import corpus

    load_env(_REPO_ROOT)  # populate OPENAI_API_KEY from this project's own .env, once

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
