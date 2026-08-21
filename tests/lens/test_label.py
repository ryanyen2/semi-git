"""Tests for sgt.lens.label -- LLM labeling with a deterministic offline fallback (plan U12 D6)."""

from __future__ import annotations

from collections import Counter
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


def test_a_capitalised_joining_word_is_lowercased_on_both_llm_paths(tmp_path, monkeypatch):
    # `Catalog And Search` was the second subsystem in the study's confplan tree. The prompts ask
    # for lowercase joining words, but the model answers each cluster independently, so this is
    # enforced in code as well: a name a reader can tell was typed by a machine costs the tree its
    # credibility on the first screen. The batch path is checked too, because that is the one every
    # multi-feature repo takes.
    fake_out = label_mod.FeatureLabel(label="Catalog And Search", rationale="Spans lookup.")
    monkeypatch.setattr(label_mod, "get_client", lambda repo: _FakeClient(fake_out))
    labeler = label_mod.Labeler(tmp_path)

    solo = labeler.label_super(["Talk Search", "Speaker View"], ["confplan"])
    assert solo.label == "Catalog and Search"

    batched = labeler.label_many([
        labeler.leaf_request("f-0001", ["confplan/cli.py::cmd_search"], {}),
        labeler.leaf_request("f-0002", ["confplan/cli.py::cmd_speakers"], {}),
    ])
    assert [r.label for r in batched] == ["Catalog and Search"] * 2


def test_the_first_and_last_word_of_a_title_keep_their_capital():
    # Chicago capitalises both ends of a title whatever the word is, and a label is a title.
    assert label_mod._normalise_title_case("The Waitlist") == "The Waitlist"
    assert label_mod._normalise_title_case("What to Look For") == "What to Look For"
    assert label_mod._normalise_title_case("Rooms And Slots And Days") == "Rooms and Slots and Days"
    # A commit subject used verbatim is not title-cased, so nothing in it should move.
    assert label_mod._normalise_title_case("add talk search") == "add talk search"


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


def test_leaf_prompt_never_offers_a_fold_artifact_as_an_entity():
    """A residue/anchor member is a verbatim byte-gap between named entities, not a name -- which is
    exactly what `_clean_symbol_name` is for, and the fallback path already respects it. The LLM path
    did not: it split each member on `::` and handed `__residue__::cmd_waitlist_join` to the model
    under "the entities are the ground truth for what the code IS". In pilot 1's confplan that named
    a leaf of README + `build_parser` + `main` + `pytest.ini` "Waitlist Queue" -- a name whose only
    support was an internal sentinel, sitting next to the feature that really is the waitlist.
    """
    prompt = label_mod._leaf_prompt(
        ["README.md", "cli.py::__residue__::cmd_waitlist_join", "cli.py::__anchor__::main",
         "cli.py::build_parser", "cli.py::main", "pytest.ini"],
        ["waitlist join and show commands", "README and help text"], "rework×20",
    )
    entities = next(l for l in prompt.splitlines() if l.startswith("Entities:"))
    assert "__residue__" not in entities and "__anchor__" not in entities
    assert "waitlist" not in entities            # the artifact's host name is not this leaf's content
    assert "build_parser" in entities and "main" in entities
    # A leaf made of nothing but artifacts has no entity line to offer at all.
    only_artifacts = label_mod._leaf_prompt(["a.py::__residue__::x", "a.py::__anchor__::x"])
    assert "Entities:" not in only_artifacts and "Files: a.py" in only_artifacts


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


def test_a_subsystem_keeps_its_name_when_a_save_adds_a_feature_to_it(tmp_path, monkeypatch):
    """A subsystem has no stable id to key on -- its node id is a positional DFS counter -- so its
    cache key is content (child labels + files) and ANY membership change lands on a new key. That
    made every ordinary `sgt save` rename the subsystem it touched: the pilot's five subsystems all
    changed names between two reads of one repo. The entry is re-found by member drift instead, the
    same graded reuse leaves get, with the member-set match standing in for the id."""
    calls: list[int] = []

    class _CountingResponses:
        def parse(self, **kwargs):
            n = kwargs["input"].count("=== Group ")
            calls.append(n)
            idx = len(calls)
            batch = label_mod._FeatureLabelBatch(items=[
                label_mod._BatchItem(index=i, label=f"Subsystem {idx}", rationale="r")
                for i in range(n)
            ])
            return _FakeResponse(output_parsed=batch, usage=_FakeUsage(1, 1))

    class _CountingClient:
        def __init__(self):
            self.responses = _CountingResponses()

    monkeypatch.setattr(label_mod, "get_client", lambda repo: _CountingClient())
    labeler = label_mod.Labeler(tmp_path)

    def name_for(kids):
        return labeler.label_many([labeler.super_request(kids, ["confplan/cli.py"])])[0].label

    first = name_for(["Waitlist Queue", "Seat Notices", "Queue Promotion"])
    # One save adds a fourth feature under the subsystem -> a different key, same subsystem.
    assert name_for(["Waitlist Queue", "Seat Notices", "Queue Promotion", "Agenda Export"]) == first
    assert calls == [1]  # named once, then re-found without paying for it again


def test_a_subsystem_that_became_something_else_is_renamed(tmp_path, monkeypatch):
    """The drift budget is a budget, not a freeze: past `TAU_LABEL` the subsystem is relabeled, so a
    stale name can't outlive the thing it named."""
    outs = iter(["Waitlist Queue", "Room Scheduling"])

    class _R:
        def parse(self, **kwargs):
            n = kwargs["input"].count("=== Group ")
            label = next(outs)
            return _FakeResponse(
                output_parsed=label_mod._FeatureLabelBatch(items=[
                    label_mod._BatchItem(index=i, label=label, rationale="r") for i in range(n)]),
                usage=_FakeUsage(1, 1))

    class _C:
        def __init__(self):
            self.responses = _R()

    monkeypatch.setattr(label_mod, "get_client", lambda repo: _C())
    labeler = label_mod.Labeler(tmp_path)
    name = lambda kids: labeler.label_many([labeler.super_request(kids, ["a.py"])])[0].label

    assert name(["Waitlist Queue", "Seat Notices"]) == "Waitlist Queue"
    assert name(["Room Grid", "Slot Matching", "Two Day Agenda"]) == "Room Scheduling"


def test_two_sibling_subsystems_cannot_both_inherit_one_name(tmp_path, monkeypatch):
    """Adoption is one-to-one. Without that guard a split subsystem would hand the same name to both
    halves, and two rows on the map would read as the same thing."""
    seen: list[str] = []

    class _R:
        def parse(self, **kwargs):
            n = kwargs["input"].count("=== Group ")
            seen.append("call")
            return _FakeResponse(
                output_parsed=label_mod._FeatureLabelBatch(items=[
                    label_mod._BatchItem(index=i, label=f"Named {len(seen)}", rationale="r")
                    for i in range(n)]),
                usage=_FakeUsage(1, 1))

    class _C:
        def __init__(self):
            self.responses = _R()

    monkeypatch.setattr(label_mod, "get_client", lambda repo: _C())
    labeler = label_mod.Labeler(tmp_path)
    name = lambda kids: labeler.label_many([labeler.super_request(kids, ["a.py"])])[0].label

    original = name(["A", "B", "C", "D"])
    half_one = name(["A", "B", "C"])   # adopts the original entry
    half_two = name(["A", "B", "D"])   # would adopt the same one -- must relabel instead
    assert half_one == original and half_two != original


def test_a_failed_relabel_keeps_the_name_the_feature_already_earned(tmp_path, monkeypatch):
    """A transient LLM failure must not replace a real name with a symbol list. It used to: the
    entry was overwritten with `fallback_label(members)` outright, so one refresh on a spent
    credential renamed every feature that happened to be up for relabeling."""
    fake = label_mod.FeatureLabel(label="Waitlist Queue", rationale="Queues attendees.")
    monkeypatch.setattr(label_mod, "get_client", lambda repo: _FakeClient(fake))
    labeler = label_mod.Labeler(tmp_path)
    fid = "f-0003"
    assert labeler.label_many([labeler.leaf_request(fid, ["a", "b", "c", "d"])])[0].label == "Waitlist Queue"

    # Drift past the budget forces a relabel, and the relabel fails (the credential ran dry).
    class _Dry:
        class responses:
            @staticmethod
            def parse(**kwargs):
                raise RuntimeError("Error code: 429 - insufficient_quota, credit_balance_exhausted")

    labeler._client = _Dry()
    out = labeler.label_many([labeler.leaf_request(fid, ["w", "x", "y", "z"])])[0]

    assert out.label == "Waitlist Queue"                    # the earned name stands
    assert labeler.cache[fid]["source"] == "fallback"        # ...but the retry backoff still runs
    assert labeler.cache[fid]["carried"] == "llm"


def test_a_spent_credential_says_so_instead_of_renaming_the_graph_in_silence(tmp_path, monkeypatch, capsys):
    """The warning gate matched auth/permission/401/credential wording only, and a spent key answers
    `429 insufficient_quota`, which matched none of it -- so the study fixture's labels turned into
    symbol lists on every refresh with an empty stderr. Any failure warns now."""
    class _Dry:
        class responses:
            @staticmethod
            def parse(**kwargs):
                raise RuntimeError("Error code: 429 - {'type': 'insufficient_quota', "
                                   "'code': 'credit_balance_exhausted'}")

    monkeypatch.setattr(label_mod, "get_client", lambda repo: _Dry())
    labeler = label_mod.Labeler(tmp_path)
    labeler.label_many([labeler.leaf_request("f-0004", ["sgt/a.py::foo"])])

    err = capsys.readouterr().err
    assert "insufficient_quota" in err and "out of credit" in err


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

    # Two episodes of comparable weight, deliberately: a cluster dominated by ONE commit is now
    # named from that commit's own subject with no LLM call at all (`label.subject_label`), which
    # would leave this test asserting cache behavior on a path that never reaches the cache.
    gb, _ = init_store(tmp_path)
    (tmp_path / "r.py").write_text("def embed(x):\n    return x\n", encoding="utf-8")
    gb.commit_all("add the embedding step")
    (tmp_path / "r.py").write_text(
        "def embed(x):\n    return x\n\n\ndef search(q):\n    return q\n", encoding="utf-8")
    gb.commit_all("add the retrieval step")
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


# -- naming a feature with the developer's own words ----------------------------------------------

def test_a_cluster_dominated_by_one_commit_is_named_after_it():
    """The developer already said what the work was. Paraphrasing it hands them back a summary of
    something they wrote -- a repo whose author wrote "Add 'done <index>' command to mark a task
    complete" was showing that feature as "Task Command Additions"."""
    subject = "Add 'done <index>' command to mark a task complete"
    out = label_mod.subject_label([subject, "unrelated"], {subject: 9, "unrelated": 1})

    assert out is not None
    assert out.label == subject


def test_a_cluster_spanning_several_episodes_defers_to_a_synthesized_name():
    """Naming a many-episode cluster after one of them would be worse than a summary, so the
    dominance gate is what keeps quoting honest rather than merely literal."""
    counts = {"add the parser": 5, "add the renderer": 5}
    assert label_mod.subject_label(list(counts), counts) is None


def test_a_subject_that_names_a_moment_is_not_a_feature_name():
    for subject in ("wip", "fix tests", "typo", "sgt save"):
        assert label_mod.subject_label([subject], {subject: 9}) is None, subject


def test_subject_label_truncates_a_long_subject_at_a_word_boundary():
    # A hard 57-character slice cut the confplan fixture's longest subject mid-word, and the feature
    # went out to participants as "...cross-track sessions and ro…" -- a fragment that reads as a
    # typo in the name rather than as an elision, on the one feature a reach task asks about.
    subject = "normalize slot comparison for cross-track sessions and rooms sharing a slot"
    out = label_mod.subject_label([subject], {subject: 9})
    assert out is not None
    assert out.label == "normalize slot comparison for cross-track sessions and…"
    assert len(out.label) <= 60
    assert not out.label[:-1].endswith(" ")   # no space stranded before the ellipsis

    # A first word longer than the budget has no boundary to cut at, so the hard slice stands rather
    # than the label collapsing to a bare ellipsis.
    long_word = "a" * 80
    out = label_mod.subject_label([long_word], {long_word: 9})
    assert out is not None and out.label == "a" * 57 + "…"
def test_conventional_commit_prefixes_are_dropped_from_the_name():
    """The type/scope is metadata about the commit, not a name for the work; repeated across every
    feature it crowds out the words that distinguish them."""
    out = label_mod.subject_label(["feat(cli): revert frontier"], {"feat(cli): revert frontier": 5})

    assert out is not None
    assert out.label == "revert frontier"


def test_naming_from_own_words_never_calls_the_client(tmp_path, monkeypatch):
    """The point is not only legibility: a name the developer already wrote needs no network, so
    this path cannot be slow, rate-limited, or non-reproducible."""
    from sgt.lens import tree as tree_mod

    calls = {"n": 0}

    class _CountingLabeler:
        def leaf_request(self, *a, **k):
            calls["n"] += 1
            return ("k", "p", [], {})

        def super_request(self, *a, **k):
            calls["n"] += 1
            return ("k", "p", [], None)

        def label_many(self, entries):
            return [label_mod.FeatureLabel(label="LLM", rationale="llm") for _ in entries]

    subject = "Add due-date support to add/list commands"
    result = {
        "nodes": {"f-1": {"children": [], "members": ["a.py::foo"], "label": "", "why": ""}},
        "roots": ["f-1"],
        "op_leaf": {},
    }
    tree_mod.label_tree(
        result, tmp_path, labeler=_CountingLabeler(),
        subjects_by_leaf={"f-1": [subject]},
        subject_counts_by_leaf={"f-1": {subject: 4}},
    )

    assert result["nodes"]["f-1"]["label"] == subject
    assert calls["n"] == 0


def test_every_op_votes_on_its_feature_name(tmp_path, monkeypatch):
    """The dominance gate asks whether one commit carries most of a feature's mass, so the mass has
    to be every op's. Counting `op.provenance` meant only re-mined ops voted: in a repo built
    through `sgt save`, 4 of 370 ops had provenance, one of them was the seed commit's, and a
    71-op feature was confidently named `init repo` on a sample of one."""
    from sgt.core.lens import get, put
    from sgt.core.store import Store
    from sgt.lens import tree as tree_mod
    from sgt.store.gitbind import init_store

    monkeypatch.setattr(label_mod, "get_client", _no_client)
    repo = tmp_path / "repo"
    gb, _ = init_store(repo)
    (repo / "a.py").write_text("def alpha():\n    return 1\n", encoding="utf-8")
    gb.commit_all("init repo")
    get(repo)

    (repo / "b.py").write_text("def beta():\n    return 2\n", encoding="utf-8")
    ideal = get(repo)
    put(repo, ideal, "add the beta helper")

    ops = Store(repo).all_ops()
    assert [o for o in ops if not o.provenance], "fixture must contain pending (saved) ops"
    built = tree_mod.build(repo, ops, ideal)
    _subjects, _kinds, counts = tree_mod.label_context(repo, ops, built)

    ops_per_leaf = Counter(built["op_leaf"].values())
    for leaf, n in ops_per_leaf.items():
        assert sum(counts.get(leaf, {}).values()) == n, leaf


def test_both_tree_build_paths_name_a_feature_the_same_way(tmp_path, monkeypatch):
    """`lens/map.py` assembled the naming context and passed it; `lens/reconcile.py` called
    `label_tree` without it and silently skipped the developer's own words. The same feature could
    therefore be named from its commit subject on one path and from a synthesized summary on the
    other -- surfaces disagreeing, which is the failure the naming work exists to end. `label_tree`
    derives the context itself now, so a caller cannot forget it."""
    from sgt.core.lens import get
    from sgt.lens import tree as tree_mod
    from sgt.store.gitbind import init_store

    monkeypatch.setattr(label_mod, "get_client", _no_client)
    gb, _ = init_store(tmp_path)
    (tmp_path / "a.py").write_text("def alpha():\n    return 1\n", encoding="utf-8")
    gb.commit_all("Add the alpha helper")
    ideal = get(tmp_path)

    from sgt.core import opindex
    ops = opindex.index_ops(tmp_path)

    # The `map.py` shape (context derived) and the `reconcile.py` shape (ops passed, nothing else).
    built = tree_mod.build(tmp_path, ops, ideal)
    tree_mod.label_tree(built, tmp_path, ops=ops)
    via_map = {nid: nd["label"] for nid, nd in built["nodes"].items() if not nd["children"]}

    rebuilt = tree_mod.build(tmp_path, ops, ideal)
    tree_mod.label_tree(rebuilt, tmp_path, pins=None, ops=ops)
    via_reconcile = {nid: nd["label"] for nid, nd in rebuilt["nodes"].items() if not nd["children"]}

    assert via_map == via_reconcile
    # And it is the developer's own subject, not a structural fallback.
    assert "Add the alpha helper" in set(via_map.values())
