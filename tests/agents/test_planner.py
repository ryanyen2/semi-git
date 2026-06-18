"""U5 tests: the decomposition planner, with a mocked OpenAI client."""

import json
import types

import pytest

import sgt.agents.planner as planner
from sgt.agents.planner import PlannerError, decompose


def _fake_client(payload: dict):
    """A stand-in client whose chat.completions.create returns `payload` as JSON."""
    msg = types.SimpleNamespace(content=json.dumps(payload))
    choice = types.SimpleNamespace(message=msg)
    resp = types.SimpleNamespace(choices=[choice])
    completions = types.SimpleNamespace(create=lambda **kw: resp)
    chat = types.SimpleNamespace(completions=completions)
    return types.SimpleNamespace(chat=chat)


def _patch(monkeypatch, payload):
    monkeypatch.setattr(planner, "get_client", lambda repo_path=".": _fake_client(payload))
    monkeypatch.setattr(planner, "get_model", lambda: "test-model")


def test_multi_part_intent_yields_multiple_subtasks(monkeypatch):
    _patch(monkeypatch, {"subtasks": [
        {"key": "validate", "intent": "add validate(email)", "provides": ["validate"], "needs": [], "depends_on": []},
        {"key": "register", "intent": "add register(email) calling validate",
         "provides": ["register"], "needs": ["validate"], "depends_on": []},
    ]})
    g = decompose("email stuff", {})
    assert len(g) == 2
    # needs/provides inference puts register after validate
    layers = g.layers()
    assert [t.key for t in layers[0]] == ["validate"]
    assert [t.key for t in layers[1]] == ["register"]


def test_atomic_intent_yields_single_subtask(monkeypatch):
    _patch(monkeypatch, {"subtasks": [
        {"key": "only", "intent": "add clamp(n)", "provides": ["clamp"], "needs": [], "depends_on": []},
    ]})
    g = decompose("add clamp", {})
    assert len(g) == 1


def test_empty_decomposition_raises(monkeypatch):
    _patch(monkeypatch, {"subtasks": []})
    with pytest.raises(PlannerError):
        decompose("x", {})


def test_malformed_payload_raises_planner_error(monkeypatch):
    def boom(**kw):
        raise RuntimeError("api down")
    fake = types.SimpleNamespace(chat=types.SimpleNamespace(
        completions=types.SimpleNamespace(create=boom)))
    monkeypatch.setattr(planner, "get_client", lambda repo_path=".": fake)
    monkeypatch.setattr(planner, "get_model", lambda: "m")
    with pytest.raises(PlannerError):
        decompose("x", {})


def test_duplicate_keys_are_deduped(monkeypatch):
    _patch(monkeypatch, {"subtasks": [
        {"key": "a", "intent": "one", "provides": [], "needs": [], "depends_on": []},
        {"key": "a", "intent": "two", "provides": [], "needs": [], "depends_on": []},
    ]})
    g = decompose("x", {})
    assert len(g) == 2  # second `a` was renamed, not dropped
