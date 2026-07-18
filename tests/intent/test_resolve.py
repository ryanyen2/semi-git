"""Tests for sgt.intent.resolve -- NL target resolution (fallback ladder's last rung).

`FakeClient`/`_FakeResponses` mirror the `tests/loop/test_plan.py` idiom: a stand-in for
`get_client(repo).responses.parse(...)` whose `.output_parsed` is scripted directly, so no
network call or API key is needed to exercise the LLM-first / `None`-on-failure contract.
"""

from __future__ import annotations

from types import SimpleNamespace

from sgt.intent import resolve as resolve_mod
from sgt.store.gitbind import init_store


def _no_client(*args, **kwargs):
    raise RuntimeError("OPENAI_API_KEY not found in environment or .env")


class _FakeResponses:
    def __init__(self, output_parsed):
        self._output_parsed = output_parsed
        self.calls = 0

    def parse(self, **kwargs):
        self.calls += 1
        return SimpleNamespace(output_parsed=self._output_parsed)


class FakeClient:
    def __init__(self, output_parsed):
        self.responses = _FakeResponses(output_parsed)


def _fixture(repo):
    """One committed, mined symbol -- just enough of a real repo for `_context` to run."""
    from sgt.core.lens import get

    gb, _ = init_store(repo)
    (repo / "a.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    gb.commit_all("add foo")
    get(repo)


def test_resolve_intent_returns_none_when_get_client_raises(tmp_path, monkeypatch):
    _fixture(tmp_path)
    monkeypatch.setattr(resolve_mod, "get_client", _no_client)

    assert resolve_mod.resolve_intent(tmp_path, "the foo logic") is None


def test_context_filters_synthetic_anchor_residue_symbols(tmp_path):
    """`_context` must not surface sgt's `__anchor__`/`__residue__` pseudo-symbols -- they're
    byte-fidelity internals a user would never name in a plain-language revert."""
    _fixture(tmp_path)
    ctx = resolve_mod._context(tmp_path, "revert")
    assert "a.py::foo" in ctx
    assert "__anchor__" not in ctx and "__residue__" not in ctx


def test_context_is_verb_aware_restore_shows_removed_not_live(tmp_path):
    """After reverting a symbol, `restore`'s context must list the *removed* op (so the LLM can
    name it to bring it back) while `revert`'s context must not -- the bug where restore only ever
    saw the live frontier and could only ever propose no-op candidates."""
    from sgt.core import verbs
    from sgt.core.lens import get

    _fixture(tmp_path)
    (tmp_path / "a.py").write_text("def foo():\n    return 1\n\n\ndef bar():\n    return 2\n", "utf-8")
    from sgt.store.gitbind import init_store

    gb, _ = init_store(tmp_path)
    gb.commit_all("add bar")
    get(tmp_path)

    preview = verbs.plan_revert(tmp_path, "a.py::bar")
    assert preview.ok
    verbs.apply(tmp_path, preview)

    revert_ctx = resolve_mod._context(tmp_path, "revert")
    restore_ctx = resolve_mod._context(tmp_path, "restore")
    # bar is now removed: it belongs to restore's vocabulary, not revert's.
    assert "a.py::bar" in restore_ctx
    assert "a.py::bar" not in revert_ctx
    # foo is still live: it stays in revert's vocabulary.
    assert "a.py::foo" in revert_ctx


def test_resolve_intent_returns_candidates_from_a_fake_client(tmp_path, monkeypatch):
    _fixture(tmp_path)
    candidate = resolve_mod.Candidate(ref="a.py::foo", kind="symbol", rationale="matches the query")
    fake = FakeClient(resolve_mod.IntentResolution(candidates=[candidate]))
    monkeypatch.setattr(resolve_mod, "get_client", lambda repo: fake)

    result = resolve_mod.resolve_intent(tmp_path, "the foo logic", verb="revert")

    assert result is not None
    assert result.candidates == [candidate]
    assert fake.responses.calls == 1


# -- U7: shared LLM-confinement guard (KTD6/R9) --------------------------------------------------


def test_resolve_intent_drops_a_candidate_naming_a_ref_never_shown(tmp_path, monkeypatch):
    """A fabricated LLM response naming a ref that never appeared in `_context`'s pool must not
    reach the caller -- confinement is enforced by `resolve_intent` itself now (U7/R9), not left
    for the caller's re-plan step to silently discover."""
    _fixture(tmp_path)
    real = resolve_mod.Candidate(ref="a.py::foo", kind="symbol", rationale="matches the query")
    invented = resolve_mod.Candidate(
        ref="nonexistent.py::phantom", kind="symbol", rationale="hallucinated",
    )
    fake = FakeClient(resolve_mod.IntentResolution(candidates=[real, invented]))
    monkeypatch.setattr(resolve_mod, "get_client", lambda repo: fake)

    result = resolve_mod.resolve_intent(tmp_path, "the foo logic", verb="revert")

    assert result is not None
    assert result.candidates == [real]
