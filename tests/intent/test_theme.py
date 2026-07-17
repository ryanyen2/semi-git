"""Tests for sgt.intent.theme -- the intent overlay's rung 2 (U4/KTD4/KTD7): LLM naming +
scope-less coalescing, cached by content-hash, with a deterministic offline fallback. `FakeClient`/
`_FakeResponses` mirror `tests/intent/test_resolve.py`'s idiom -- no network or API key needed."""

from __future__ import annotations

from types import SimpleNamespace

from sgt.core.lens import get
from sgt.intent import group, theme
from sgt.store.gitbind import init_store


class _FakeResponses:
    def __init__(self, output_parsed):
        self._output_parsed = output_parsed

    def parse(self, **kwargs):
        return SimpleNamespace(
            output_parsed=self._output_parsed, usage=SimpleNamespace(input_tokens=10, output_tokens=5),
        )


class FakeClient:
    def __init__(self, output_parsed):
        self.responses = _FakeResponses(output_parsed)


def _no_client(*args, **kwargs):
    raise RuntimeError("OPENAI_API_KEY not found in environment or .env")


def _two_scope_commits(tmp_path):
    gb, _ = init_store(tmp_path)
    (tmp_path / "a.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    gb.commit_all("fix(auth): add foo")
    (tmp_path / "b.py").write_text("def bar():\n    return 2\n", encoding="utf-8")
    gb.commit_all("fix(auth): add bar")
    get(tmp_path)


def test_no_op_ids_anywhere_in_schema_or_persisted_themes(tmp_path, monkeypatch):
    _two_scope_commits(tmp_path)
    monkeypatch.setattr(theme, "get_client", _no_client)

    themes = theme.build_themes(tmp_path)

    from sgt.core.store import Store

    real_op_ids = {op.id for op in Store(tmp_path).all_ops()}
    for t in themes.values():
        for sha in t["atom_shas"]:
            assert sha not in real_op_ids  # a commit sha, never an op-id
        assert "op_ids" not in t


def test_fallback_scope_themes_exist_with_zero_network(tmp_path, monkeypatch):
    _two_scope_commits(tmp_path)
    monkeypatch.setattr(theme, "get_client", _no_client)

    themes = theme.build_themes(tmp_path)

    assert len(themes) == 1
    (t,) = themes.values()
    assert t["label"] == "auth"
    assert t["source"] == "fallback"
    assert len(t["atom_shas"]) == 2


def test_fallback_entry_is_upgraded_once_a_client_becomes_available(tmp_path, monkeypatch):
    _two_scope_commits(tmp_path)
    monkeypatch.setattr(theme, "get_client", _no_client)
    theme.build_themes(tmp_path)

    fake = FakeClient(theme.ThemeLabel(label="Auth Bugfix", rationale="Fixes the auth flow."))
    monkeypatch.setattr(theme, "get_client", lambda repo: fake)
    themes = theme.build_themes(tmp_path)

    (t,) = themes.values()
    assert t["label"] == "Auth Bugfix"
    assert t["source"] == "llm"


def test_cache_hit_makes_zero_live_calls_on_second_build(tmp_path, monkeypatch):
    _two_scope_commits(tmp_path)
    fake = FakeClient(theme.ThemeLabel(label="Auth Bugfix", rationale="Fixes the auth flow."))
    monkeypatch.setattr(theme, "get_client", lambda repo: fake)

    first = theme.build_themes(tmp_path)

    themer = theme.IntentThemer(tmp_path)
    bundles = group.scope_bundles(group.atoms(tmp_path))
    themer.label_bundle(bundles[0])
    assert themer.calls == 0  # cache hit -- the label was already persisted as "llm"

    second = theme.build_themes(tmp_path)
    assert first == second


def test_determinism_same_partition_and_cache_yields_byte_identical_themes(tmp_path, monkeypatch):
    _two_scope_commits(tmp_path)
    monkeypatch.setattr(theme, "get_client", _no_client)

    first = theme.build_themes(tmp_path)
    second = theme.build_themes(tmp_path)
    assert first == second


def test_subset_validation_drops_a_hallucinated_sha(tmp_path, monkeypatch):
    """A scope-less atom coalescing call that names a sha never shown to it must not have that
    sha survive into the persisted group."""
    gb, _ = init_store(tmp_path)
    (tmp_path / "a.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    gb.commit_all("touch a.py")  # no conventional-commit scope -> scope-less atom
    get(tmp_path)

    real_atom = group.atoms(tmp_path)[0]
    hallucinated_group = theme.ThemeGroup(
        label="Bogus", rationale="made up", atom_shas=["ffffffff", real_atom.commit_sha[:8]],
    )
    fake = FakeClient(theme.ThemeGroups(groups=[hallucinated_group]))
    monkeypatch.setattr(theme, "get_client", lambda repo: fake)

    themer = theme.IntentThemer(tmp_path)
    result = themer.group_scopeless([real_atom])

    all_shas = {sha for g in result for sha in g.atom_shas}
    assert real_atom.commit_sha in all_shas
    assert "ffffffff" not in all_shas
    assert all(len(sha) == 40 for sha in all_shas)  # only real, full-length shas survive


def test_scopeless_atom_with_no_client_becomes_a_singleton_theme(tmp_path, monkeypatch):
    gb, _ = init_store(tmp_path)
    (tmp_path / "a.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    gb.commit_all("touch a.py")
    get(tmp_path)
    monkeypatch.setattr(theme, "get_client", _no_client)

    themes = theme.build_themes(tmp_path)

    assert len(themes) == 1
    (t,) = themes.values()
    assert t["source"] == "fallback"
    assert len(t["atom_shas"]) == 1
