"""Tests for sgt.lens.label -- LLM labeling with a deterministic offline fallback (plan U12 D6)."""

from __future__ import annotations

from dataclasses import dataclass

from sgt.lens import label as label_mod


@dataclass
class _FakeUsage:
    input_tokens: int
    output_tokens: int


@dataclass
class _FakeResponse:
    output_parsed: label_mod.FeatureLabel
    usage: _FakeUsage


class _FakeResponses:
    def __init__(self, out: label_mod.FeatureLabel):
        self._out = out
        self.calls = 0

    def parse(self, **kwargs):
        self.calls += 1
        return _FakeResponse(output_parsed=self._out, usage=_FakeUsage(10, 5))


class _FakeClient:
    def __init__(self, out: label_mod.FeatureLabel):
        self.responses = _FakeResponses(out)


def _no_client(*args, **kwargs):
    raise RuntimeError("OPENAI_API_KEY not found in environment or .env")


def test_label_falls_back_deterministically_with_no_client(tmp_path, monkeypatch):
    monkeypatch.setattr(label_mod, "get_client", _no_client)
    labeler = label_mod.Labeler(tmp_path)

    out = labeler.label(["sgt/core/op.py::Op", "sgt/core/ideal.py::Ideal"])

    assert isinstance(out, label_mod.FeatureLabel)
    assert labeler.calls == 0
    key = label_mod._key(["sgt/core/op.py::Op", "sgt/core/ideal.py::Ideal"])
    assert labeler.cache[key]["source"] == "fallback"


def test_label_calls_llm_and_caches_as_llm_sourced(tmp_path, monkeypatch):
    fake_out = label_mod.FeatureLabel(label="Op Model", rationale="Defines the op type.")
    monkeypatch.setattr(label_mod, "get_client", lambda repo: _FakeClient(fake_out))
    labeler = label_mod.Labeler(tmp_path)

    out = labeler.label(["sgt/core/op.py::Op"])

    assert out == fake_out
    assert labeler.calls == 1
    assert labeler.tokens_in == 10 and labeler.tokens_out == 5
    key = label_mod._key(["sgt/core/op.py::Op"])
    assert labeler.cache[key]["source"] == "llm"


def test_llm_sourced_cache_hit_never_calls_client_again(tmp_path, monkeypatch):
    fake_out = label_mod.FeatureLabel(label="Op Model", rationale="Defines the op type.")
    client = _FakeClient(fake_out)
    monkeypatch.setattr(label_mod, "get_client", lambda repo: client)
    labeler = label_mod.Labeler(tmp_path)
    members = ["sgt/core/op.py::Op"]

    labeler.label(members)
    labeler.label(members)  # second call, same members -- must hit cache, not the client again

    assert client.responses.calls == 1
    assert labeler.calls == 1


def test_fallback_sourced_entry_is_retried_once_a_client_is_available(tmp_path, monkeypatch):
    monkeypatch.setattr(label_mod, "get_client", _no_client)
    labeler = label_mod.Labeler(tmp_path)
    members = ["sgt/core/op.py::Op"]
    labeler.label(members)  # no client -> cached as fallback
    key = label_mod._key(members)
    assert labeler.cache[key]["source"] == "fallback"

    fake_out = label_mod.FeatureLabel(label="Op Model", rationale="Defines the op type.")
    monkeypatch.setattr(label_mod, "get_client", lambda repo: _FakeClient(fake_out))
    labeler._client = None  # simulate a fresh Labeler picking up the now-available key

    out = labeler.label(members)

    assert out == fake_out
    assert labeler.calls == 1
    assert labeler.cache[key]["source"] == "llm"


def test_save_and_reload_preserves_cache(tmp_path, monkeypatch):
    fake_out = label_mod.FeatureLabel(label="Op Model", rationale="Defines the op type.")
    monkeypatch.setattr(label_mod, "get_client", lambda repo: _FakeClient(fake_out))
    labeler = label_mod.Labeler(tmp_path)
    labeler.label(["sgt/core/op.py::Op"])
    labeler.save()

    reloaded = label_mod.Labeler(tmp_path)
    assert reloaded.cache == labeler.cache
    assert (tmp_path / ".sgt" / "local" / "label_cache.json").is_file()


def test_label_super_names_a_subsystem_from_child_labels(tmp_path, monkeypatch):
    fake_out = label_mod.FeatureLabel(label="Kernel Core", rationale="Spans the op/ideal/fold model.")
    monkeypatch.setattr(label_mod, "get_client", lambda repo: _FakeClient(fake_out))
    labeler = label_mod.Labeler(tmp_path)

    out = labeler.label_super(["Op Model", "Ideal Algebra"], ["sgt/core"])

    assert out == fake_out
    assert labeler.calls == 1


def test_fallback_label_is_deterministic_and_derived_from_dominant_dir():
    members = ["sgt/core/op.py::Op", "sgt/core/ideal.py::Ideal"]
    first = label_mod._fallback_label(members)
    second = label_mod._fallback_label(members)
    assert first == second
    assert "sgt/core" in first.rationale
