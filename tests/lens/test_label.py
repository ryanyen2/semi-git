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


def test_fallback_sourced_entry_is_retried_once_its_backoff_window_expires(tmp_path, monkeypatch):
    monkeypatch.setattr(label_mod, "get_client", _no_client)
    labeler = label_mod.Labeler(tmp_path)
    members = ["sgt/core/op.py::Op"]
    labeler.label(members)  # no client -> cached as fallback, with a retry window
    labeler.save()
    key = label_mod._key(members)
    assert labeler.cache[key]["source"] == "fallback"

    fake_out = label_mod.FeatureLabel(label="Op Model", rationale="Defines the op type.")
    monkeypatch.setattr(label_mod, "get_client", lambda repo: _FakeClient(fake_out))
    # A fresh Labeler with the key now available, but still inside the backoff window: it must
    # serve the cached fallback rather than pay the call again.
    inside = label_mod.Labeler(tmp_path)
    assert inside.label(members).label == labeler.cache[key]["label"]
    assert inside.calls == 0

    # Once the window has passed, the very next read earns a real label.
    expired = label_mod.Labeler(tmp_path)
    expired.cache[key]["retry_after"] = 0
    out = expired.label(members)

    assert out == fake_out
    assert expired.calls == 1
    assert expired.cache[key]["source"] == "llm"


def test_fallback_entry_is_not_retried_on_every_read(tmp_path, monkeypatch):
    """The read-path regression this backoff exists for: a repo with a broken credential used to
    re-pay one failing LLM call per terse feature on every single refresh."""
    attempts = {"n": 0}

    def _counting_no_client(*args, **kwargs):
        attempts["n"] += 1
        raise RuntimeError("No LLM credential found: set OPENAI_API_KEY")

    monkeypatch.setattr(label_mod, "get_client", _counting_no_client)
    members = ["sgt/core/op.py::Op"]

    first = label_mod.Labeler(tmp_path)
    first.label(members)
    first.save()
    assert attempts["n"] == 1

    for _ in range(5):  # five more "refreshes" -- none may touch the client
        again = label_mod.Labeler(tmp_path)
        again.label(members)
        again.save()

    assert attempts["n"] == 1


def test_missing_credential_costs_one_attempt_for_a_whole_batch(tmp_path, monkeypatch):
    """`label_many` must not re-fail client construction once per batch: a keyless repo with many
    features paid that failure `ceil(features / MAX_BATCH)` times per read."""
    attempts = {"n": 0}

    def _counting_no_client(*args, **kwargs):
        attempts["n"] += 1
        raise RuntimeError("No LLM credential found: set OPENAI_API_KEY")

    monkeypatch.setattr(label_mod, "get_client", _counting_no_client)
    labeler = label_mod.Labeler(tmp_path)
    entries = [
        labeler.leaf_request(f"f-{i}", [f"pkg/mod{i}.py::sym{i}"], {})
        for i in range(label_mod.MAX_BATCH * 3)
    ]

    outs = labeler.label_many(entries)

    assert len(outs) == len(entries)
    assert all(o is not None for o in outs)
    assert attempts["n"] == 1
    assert all(labeler.cache[f"f-{i}"]["source"] == "fallback" for i in range(len(entries)))
    # Each fallback carries its own retry window and the leaf drift anchor a real label would set,
    # so a later retry compares membership rather than treating the feature as brand new.
    assert all(labeler.cache[f"f-{i}"]["retry_after"] > 0 for i in range(len(entries)))
    assert labeler.cache["f-0"]["gen_members"] == ["pkg/mod0.py::sym0"]


def test_relabel_ignores_both_the_cache_and_the_backoff(tmp_path, monkeypatch):
    """`sgt log --rebuild` is the user's "name everything again" escape hatch, so it must not wait
    out a backoff window nor reuse an existing LLM label."""
    monkeypatch.setattr(label_mod, "get_client", _no_client)
    members = ["sgt/core/op.py::Op"]
    cold = label_mod.Labeler(tmp_path)
    cold.label(members)
    cold.save()

    fake_out = label_mod.FeatureLabel(label="Op Model", rationale="Defines the op type.")
    monkeypatch.setattr(label_mod, "get_client", lambda repo: _FakeClient(fake_out))
    forced = label_mod.Labeler(tmp_path, relabel=True)

    out = forced.label(members)

    assert out == fake_out
    assert forced.calls == 1
    assert forced.cache[label_mod._key(members)]["source"] == "llm"


def test_backoff_grows_with_consecutive_failures(tmp_path, monkeypatch):
    monkeypatch.setattr(label_mod, "get_client", _no_client)
    members = ["sgt/core/op.py::Op"]
    key = label_mod._key(members)

    first = label_mod.Labeler(tmp_path)
    first.label(members)
    first.save()
    assert first.cache[key]["attempts"] == 1

    second = label_mod.Labeler(tmp_path)
    second.cache[key]["retry_after"] = 0  # window expired; the retry fails again
    second.label(members)
    second.save()

    assert second.cache[key]["attempts"] == 2
    assert second.cache[key]["retry_after"] > first.cache[key]["retry_after"]


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


def testfallback_label_is_deterministic_and_derived_from_dominant_dir():
    members = ["sgt/core/op.py::Op", "sgt/core/ideal.py::Ideal"]
    first = label_mod.fallback_label(members)
    second = label_mod.fallback_label(members)
    assert first == second
    assert "sgt/core" in first.rationale


def testfallback_label_never_leaks_residue_ids_or_null_bytes():
    """The offline path is the common case when no key is configured, so it must be *readable*: a
    cluster of pure fold artifacts (residue/anchor, embedded NULs from byte-native addressing) must
    never surface as a raw `__residue__::\\x00HEAD\\x00` id -- it reads as `(structural)` glue,
    tagged by where it lives."""
    members = ["sgt/intent/__init__.py::__residue__::\x00HEAD\x00",
               "sgt/intent/resolve.py::__residue__::Candidate",
               "sgt/intent/x.py::__anchor__::a"]
    lbl = label_mod.fallback_label(members).label
    assert "__residue__" not in lbl and "__anchor__" not in lbl and "\x00" not in lbl
    assert "structural" in lbl and "sgt/intent" in lbl


def testfallback_label_names_kind_not_first_file():
    # a whole-file doc/config cluster reads as its KIND, not the first filename (the "why is
    # README.md a feature" fix) -- a 91-file docs group shouldn't masquerade as one code feature
    lbl = label_mod.fallback_label(
        ["docs/guide/getting-started.md", "docs/guide/workflows.md", "docs/guide/x.md"]).label
    assert lbl.startswith("docs & config") and "getting-started" not in lbl
    # real symbols win over any residue members mixed in, and over doc files
    assert label_mod.fallback_label(
        ["a.py::__anchor__::z", "a.py::foo", "a.py::bar", "README.md"]).label == "bar foo"


def test_clean_symbol_name_distinguishes_names_from_artifacts():
    assert label_mod._clean_symbol_name("sgt/a.py::foo") == "foo"
    assert label_mod._clean_symbol_name("README.md") == "README.md"
    assert label_mod._clean_symbol_name("a.py::__residue__::\x00HEAD\x00") is None
    assert label_mod._clean_symbol_name("a.py::__anchor__::x") is None


def test_weighted_jaccard_grades_by_op_mass_not_symbol_count():
    # a shared heavy symbol keeps two sets similar; dropping it is expensive
    w = {"a": 10.0, "b": 1.0, "c": 1.0}
    assert label_mod._weighted_jaccard({"a", "b"}, {"a", "c"}, w) == 10.0 / 12.0  # keep the heavy one
    assert label_mod._weighted_jaccard({"b"}, {"c"}, w) == 0.0                     # disjoint light ones
    assert label_mod._weighted_jaccard(set(), set(), w) == 1.0                     # two empties identical
    assert label_mod._weighted_jaccard({"x"}, {"x"}, {}) == 1.0                    # unit-weight default


def test_graded_reuse_ship_of_theseus_relabels_within_bounded_swaps(tmp_path, monkeypatch):
    """Graded leaf-label reuse anchors drift at the GENERATION member set, not the previous
    snapshot (plan §3.2). A chain of single-member swaps -- each step individually similar to the
    one before -- composes to unbounded drift; anchoring at generation forces a relabel within a
    bounded number of swaps (⌈1/(1−τ)⌉ = 2 for τ=0.5 on this constant-size chain) and each relabel
    resets the anchor. Were reuse graded against the previous snapshot instead, no single swap in
    the chain would ever cross the threshold and the label would survive total replacement."""
    calls: list[int] = []

    class _CountingResponses:
        def parse(self, **kwargs):
            n = kwargs["input"].count("=== Group ")
            calls.append(n)
            idx = len(calls)
            batch = label_mod._FeatureLabelBatch(items=[
                label_mod._BatchItem(index=i, label=f"Label {idx}", rationale="r") for i in range(n)
            ])
            return _FakeResponse(output_parsed=batch, usage=_FakeUsage(1, 1))

    class _CountingClient:
        def __init__(self):
            self.responses = _CountingResponses()

    monkeypatch.setattr(label_mod, "get_client", lambda repo: _CountingClient())
    labeler = label_mod.Labeler(tmp_path)
    fid = "f-0001"

    def name_for(members):
        entry = labeler.leaf_request(fid, sorted(members))  # unit weights
        return labeler.label_many([entry])[0].label

    first = name_for(["a", "b", "c", "d"])                 # generation -> LLM call #1
    assert name_for(["b", "c", "d", "e"]) == first          # J vs gen = 3/5 = 0.6 >= 0.5 -> reuse
    relabel = name_for(["c", "d", "e", "f"])                # J vs gen = 2/6 = 0.33 < 0.5 -> relabel
    assert relabel != first
    assert name_for(["d", "e", "f", "g"]) == relabel        # J vs reset anchor = 3/5 = 0.6 -> reuse
    assert calls == [1, 1]  # exactly two live calls: the generation and the one forced relabel


def test_graded_reuse_lazily_adopts_legacy_member_hash_entry(tmp_path, monkeypatch):
    """A pre-graded build keyed leaf labels by member hash. The first graded lookup for that
    feature id finds no id-keyed entry, adopts the legacy member-hash entry as the generation
    point (no LLM call), and re-keys it -- "first hit under the new rule re-keys them" (§3.2)."""
    members = ["sgt/a.py::foo", "sgt/a.py::bar"]
    legacy_key = label_mod._key(members)
    # simulate a cache written by the old member-hash scheme (no gen_members)
    labeler = label_mod.Labeler(tmp_path)
    labeler.cache[legacy_key] = {"label": "Adopted", "rationale": "r", "source": "llm"}
    monkeypatch.setattr(label_mod, "get_client", _no_client)  # any LLM call would fail loudly

    fid = "f-0002"
    out = labeler.label_many([labeler.leaf_request(fid, members)])[0]

    assert out.label == "Adopted"
    assert labeler.calls == 0  # adopted without paying
    assert labeler.cache[fid]["source"] == "llm"
    assert labeler.cache[fid]["gen_members"] == sorted(members)


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
