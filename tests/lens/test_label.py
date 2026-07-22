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
        if kwargs.get("text_format") is label_mod._FeatureLabelBatch:
            n = kwargs["input"].count("=== Group ")
            batch = label_mod._FeatureLabelBatch(items=[
                label_mod._BatchItem(index=i, label=self._out.label, rationale=self._out.rationale)
                for i in range(n)
            ])
            return _FakeResponse(output_parsed=batch, usage=_FakeUsage(10, 5))
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


def test_fallback_label_never_leaks_residue_ids_or_null_bytes():
    """The offline path is the common case when no key is configured, so it must be *readable*: a
    cluster of pure fold artifacts (residue/anchor, embedded NULs from byte-native addressing) must
    never surface as a raw `__residue__::\\x00HEAD\\x00` id -- it reads as `(structural)` glue,
    tagged by where it lives."""
    members = ["sgt/intent/__init__.py::__residue__::\x00HEAD\x00",
               "sgt/intent/resolve.py::__residue__::Candidate",
               "sgt/intent/x.py::__anchor__::a"]
    lbl = label_mod._fallback_label(members).label
    assert "__residue__" not in lbl and "__anchor__" not in lbl and "\x00" not in lbl
    assert "structural" in lbl and "sgt/intent" in lbl


def test_fallback_label_names_kind_not_first_file():
    # a whole-file doc/config cluster reads as its KIND, not the first filename (the "why is
    # README.md a feature" fix) -- a 91-file docs group shouldn't masquerade as one code feature
    lbl = label_mod._fallback_label(
        ["docs/guide/getting-started.md", "docs/guide/workflows.md", "docs/guide/x.md"]).label
    assert lbl.startswith("docs & config") and "getting-started" not in lbl
    # real symbols win over any residue members mixed in, and over doc files
    assert label_mod._fallback_label(
        ["a.py::__anchor__::z", "a.py::foo", "a.py::bar", "README.md"]).label == "bar foo"


def test_clean_symbol_name_distinguishes_names_from_artifacts():
    assert label_mod._clean_symbol_name("sgt/a.py::foo") == "foo"
    assert label_mod._clean_symbol_name("README.md") == "README.md"
    assert label_mod._clean_symbol_name("a.py::__residue__::\x00HEAD\x00") is None
    assert label_mod._clean_symbol_name("a.py::__anchor__::x") is None


def test_build_map_persists_label_cache_so_reruns_dont_re_call_the_llm(tmp_path, monkeypatch):
    """Regression: `build_map` used to label the tree but never `save()` the labeler, so the
    member-hash cache was rebuilt cold on every run -- a second `sgt map` re-called the (non-
    deterministic) LLM for unchanged clusters and relabeled stable features. With the cache
    persisted, an unchanged cluster hits the cache on the next build and makes zero new calls."""
    from sgt.core.lens import get
    from sgt.lens.map import build_map
    from sgt.store.gitbind import init_store

    fake_out = label_mod.FeatureLabel(label="Retrieval Pipeline", rationale="Embeds and retrieves.")
    client = _FakeClient(fake_out)
    monkeypatch.setattr(label_mod, "get_client", lambda repo: client)

    gb, _ = init_store(tmp_path)
    (tmp_path / "r.py").write_text("def embed(x):\n    return x\n\n\ndef search(q):\n    return q\n", encoding="utf-8")
    gb.commit_all("add r.py")
    get(tmp_path)

    build_map(tmp_path)
    assert (tmp_path / ".sgt" / "local" / "label_cache.json").is_file()
    calls_after_first = client.responses.calls
    assert calls_after_first > 0

    build_map(tmp_path)  # unchanged clusters -> every label served from the persisted cache
    assert client.responses.calls == calls_after_first


def test_build_map_rerun_with_no_changes_does_not_touch_label_cache_mtime(tmp_path, monkeypatch):
    """A `sgt map` rerun with nothing new must not bump `label_cache.json`'s mtime -- it's a
    `.sgt/**/*.json` path a client's file watcher invalidates its cache on, so an unconditional
    rewrite on every no-op read makes a client's own refresh retrigger another refresh, forever."""
    from sgt import state
    from sgt.core.lens import get
    from sgt.lens.map import build_map
    from sgt.store.gitbind import init_store

    fake_out = label_mod.FeatureLabel(label="Retrieval Pipeline", rationale="Embeds and retrieves.")
    monkeypatch.setattr(label_mod, "get_client", lambda repo: _FakeClient(fake_out))

    gb, _ = init_store(tmp_path)
    (tmp_path / "r.py").write_text("def embed(x):\n    return x\n\n\ndef search(q):\n    return q\n", encoding="utf-8")
    gb.commit_all("add r.py")
    get(tmp_path)

    build_map(tmp_path)  # settles the cache (first call is always a real write)
    mtime_before = state.path(tmp_path, "label_cache").stat().st_mtime_ns

    build_map(tmp_path)  # no new ops -- must not rewrite the cache file

    mtime_after = state.path(tmp_path, "label_cache").stat().st_mtime_ns
    assert mtime_after == mtime_before, "save() rewrote label_cache.json with no new state"
