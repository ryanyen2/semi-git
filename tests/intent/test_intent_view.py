"""Tests for sgt.api.intent_view -- the intent overlay's canonical projection (plan U6): every
commit-keyed atom (recomputed on read) plus every persisted, LLM-named theme, each carrying its
dependency-graph-backed tier and cross-feature span. Additive to compose_view (R21)."""

from __future__ import annotations

from sgt.api import compose_view, intent_view
from sgt.core.lens import get
from sgt.intent import prompts, theme
from sgt.store.gitbind import init_store


def test_shape_emits_themes_and_atoms_with_documented_keys_sorted(tmp_path, monkeypatch):
    def _no_client(*args, **kwargs):
        raise RuntimeError("OPENAI_API_KEY not found in environment or .env")

    monkeypatch.setattr(theme, "get_client", _no_client)
    gb, _ = init_store(tmp_path)
    (tmp_path / "a.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    gb.commit_all("fix(auth): add foo")
    get(tmp_path)
    theme.build_themes(tmp_path)

    v = intent_view(tmp_path)
    assert set(v) == {"themes", "atoms"}
    (t,) = v["themes"]
    assert set(t) == {
        "theme_id", "label", "rationale", "source", "atom_shas", "op_ids", "feature_span", "tier",
    }
    (a,) = v["atoms"]
    assert set(a) == {"commit_sha", "subject", "op_ids", "feature_span", "tier", "prompt"}
    assert v["atoms"] == sorted(v["atoms"], key=lambda x: x["commit_sha"])
    assert v["themes"] == sorted(v["themes"], key=lambda x: x["theme_id"])


def test_additive_existing_compose_view_keys_unchanged_intent_added(tmp_path):
    init_store(tmp_path)

    v = compose_view(tmp_path)

    assert "intent" in v
    assert set(v["intent"]) == {"themes", "atoms"}
    # every pre-existing key is still present and untouched
    for key in ("map", "history", "status", "forks", "plan", "drift", "sessions", "trust",
                "oracle_verdict", "proposals"):
        assert key in v


def test_empty_repo_has_no_themes_but_still_reports_atoms(tmp_path):
    gb, _ = init_store(tmp_path)
    (tmp_path / "a.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    gb.commit_all("add foo")
    get(tmp_path)

    v = intent_view(tmp_path)
    assert v["themes"] == []
    assert len(v["atoms"]) == 1  # atoms derive straight from the store -- no build step needed


def test_no_ops_at_all_returns_empty_lists(tmp_path):
    init_store(tmp_path)
    v = intent_view(tmp_path)
    assert v == {"themes": [], "atoms": []}


def test_cross_feature_theme_reports_both_features_with_correct_tier(tmp_path, monkeypatch):
    def _no_client(*args, **kwargs):
        raise RuntimeError("OPENAI_API_KEY not found in environment or .env")

    monkeypatch.setattr(theme, "get_client", _no_client)
    from sgt.core.store import Store
    from sgt.lens import tree

    gb, _ = init_store(tmp_path)
    (tmp_path / "a.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("def bar():\n    return 2\n", encoding="utf-8")
    gb.commit_all("fix(x): touch two unrelated files")
    get(tmp_path)

    nodes = {
        "F-A": {"parent": None, "children": [], "members": ["a.py::foo"], "size": 1, "dir": "", "label": "F-A"},
        "F-B": {"parent": None, "children": [], "members": ["b.py::bar"], "size": 1, "dir": "", "label": "F-B"},
    }
    ops = Store(tmp_path).all_ops()
    result = {
        "nodes": nodes, "roots": sorted(nodes), "op_leaf": tree.assign_ops_to_leaves(nodes, ops),
        "max_depth": 0, "cannot_link_moves": [], "identity_events": [],
    }
    tree.save(tmp_path, result)
    theme.build_themes(tmp_path)

    v = intent_view(tmp_path)
    (t,) = v["themes"]
    assert t["feature_span"] == ["F-A", "F-B"]
    assert t["tier"] == "co-changed"  # same commit, cross-feature, no dependency edge


def test_atom_prompt_surfaces_a_prompt_recorded_directly_by_commit_sha(tmp_path):
    gb, _ = init_store(tmp_path)
    (tmp_path / "a.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    sha = gb.commit_all("add foo")
    get(tmp_path)
    prompts.record_prompt(tmp_path, sha, "fix the login bug")

    v = intent_view(tmp_path)
    (atom,) = v["atoms"]
    assert atom["commit_sha"] == sha
    assert atom["prompt"] == "fix the login bug"


def test_atom_prompt_is_none_when_nothing_was_recorded(tmp_path):
    gb, _ = init_store(tmp_path)
    (tmp_path / "a.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    gb.commit_all("add foo")
    get(tmp_path)

    v = intent_view(tmp_path)
    (atom,) = v["atoms"]
    assert atom["prompt"] is None
