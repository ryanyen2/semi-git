"""Tests for sgt.intent.theme_segment -- the LLM segmentation rung. The LLM itself is stubbed
(a fake client returning a canned SegmentPlan, or a raising one for the fallback path), so these
assertions are about the *discipline* around the call: contiguity coalescing, shown-sha
validation, total coverage, and the op-membership safety invariant -- none of which depend on a
real network. Mirrors the stubbing idiom in tests/intent/test_intent_view.py."""

from __future__ import annotations

from sgt.core.lens import get
from sgt.core.store import Store
from sgt.intent import segment, theme_segment
from sgt.intent.segment import feature_runs, overlay_persisted
from sgt.intent.theme_segment import SegmentGroup, SegmentPlan, SegmentThemer
from sgt.lens import tree
from sgt.store.gitbind import init_store


def _leaf(members, label):
    return {"parent": None, "children": [], "members": sorted(members), "size": len(members),
            "dir": "", "label": label}


def _save_tree(repo, leaves):
    nodes = {fid: _leaf(m, fid) for fid, m in leaves.items()}
    ops = Store(repo).all_ops()
    result = {"nodes": nodes, "roots": sorted(nodes),
              "op_leaf": tree.assign_ops_to_leaves(nodes, ops),
              "max_depth": 0, "cannot_link_moves": [], "identity_events": []}
    tree.save(repo, result)
    return result["op_leaf"]


def _four_commit_feature(tmp_path):
    gb, _ = init_store(tmp_path)
    for i in range(4):
        body = "".join(f"def f{j}():\n    return {j}\n\n\n" for j in range(i + 1))
        (tmp_path / "a.py").write_text(body)
        gb.commit_all(f"feat(x): step {i}")
    get(tmp_path)
    op_leaf = _save_tree(tmp_path, {"F-A": [f"a.py::f{j}" for j in range(4)]})
    runs = feature_runs(tmp_path, op_leaf)["F-A"]
    by_id = {op.id: op for op in Store(tmp_path).all_ops()}
    return runs, by_id


def test_segments_view_carries_a_chapters_captured_words(tmp_path):
    """A chapter's captured words (intent-ledger P1 zoom) reach `segments_view` -- the read the TUI
    and the VSCode extension draw -- joined from the committed prompt sidecar and sha-keyed `save -m`
    turns, so the user's own words become addressable per checkpoint ('the history answers in my own
    words'). A chapter whose commits captured nothing carries an empty list, never a guess."""
    from sgt.api import segments_view
    from sgt.intent import turns

    _four_commit_feature(tmp_path)
    segs = segments_view(tmp_path)
    assert segs, "the four-commit feature should produce at least one chapter"
    sha = segs[0]["commit_shas"][0]
    turns.record_turn(tmp_path, key=sha, key_kind="sha", actor="human", channel="cli",
                      text="make step one guard the invariant")

    chapter = next(s for s in segments_view(tmp_path) if sha in s["commit_shas"])
    assert "make step one guard the invariant" in chapter["words"]
    # every chapter carries a words list (empty where nothing was captured), never a missing key
    assert all(isinstance(s["words"], list) for s in segments_view(tmp_path))


def _themer_with(monkeypatch, tmp_path, plan_or_exc):
    themer = SegmentThemer(tmp_path)

    class Client:
        class responses:
            @staticmethod
            def parse(**kwargs):
                if isinstance(plan_or_exc, Exception):
                    raise plan_or_exc
                r = type("R", (), {})()
                r.output_parsed = plan_or_exc
                r.usage = type("U", (), {"input_tokens": 10, "output_tokens": 10})()
                return r

    themer._client = Client()
    return themer


def test_llm_grouping_coalesces_into_contiguous_chapters(tmp_path, monkeypatch):
    runs, by_id = _four_commit_feature(tmp_path)
    shas = [r.commit_sha[:8] for r in runs]
    # LLM groups runs 0,1 as "Scaffold" and 2,3 as "Finish"
    plan = SegmentPlan(segments=[
        SegmentGroup(label="Scaffold", rationale="set up", commit_shas=[shas[0], shas[1]]),
        SegmentGroup(label="Finish", rationale="complete", commit_shas=[shas[2], shas[3]]),
    ])
    themer = _themer_with(monkeypatch, tmp_path, plan)
    recs = themer.segment_feature("F-A", runs, by_id, lambda s: None)
    assert [r["label"] for r in recs] == ["Scaffold", "Finish"]
    assert [len(r["commit_shas"]) for r in recs] == [2, 2]
    assert all(r["source"] == "llm" for r in recs)


def test_noncontiguous_llm_grouping_is_split_into_contiguous_blocks(tmp_path, monkeypatch):
    """The LLM claims runs 0 and 2 under one label and run 1 under another -- an ill-formed,
    non-contiguous chapter. `_coalesce` must split label A into two contiguous chapters rather
    than persist a span that overlaps label B."""
    runs, by_id = _four_commit_feature(tmp_path)
    shas = [r.commit_sha[:8] for r in runs]
    plan = SegmentPlan(segments=[
        SegmentGroup(label="A", rationale="x", commit_shas=[shas[0], shas[2]]),
        SegmentGroup(label="B", rationale="y", commit_shas=[shas[1]]),
        SegmentGroup(label="A2", rationale="z", commit_shas=[shas[3]]),
    ])
    themer = _themer_with(monkeypatch, tmp_path, plan)
    recs = themer.segment_feature("F-A", runs, by_id, lambda s: None)
    labels = [r["label"] for r in recs]
    # run order is 0(A) 1(B) 2(A) 3(A2) -> contiguous coalescing yields A, B, A, A2
    assert labels == ["A", "B", "A", "A2"]


def test_invented_sha_is_ignored_run_keeps_deterministic_label(tmp_path, monkeypatch):
    runs, by_id = _four_commit_feature(tmp_path)
    plan = SegmentPlan(segments=[
        SegmentGroup(label="Bogus", rationale="x", commit_shas=["deadbeef", "cafef00d"]),
    ])
    themer = _themer_with(monkeypatch, tmp_path, plan)
    recs = themer.segment_feature("F-A", runs, by_id, lambda s: None)
    # no shown sha was claimed -> every run keeps its own commit-subject label, none says "Bogus"
    assert all(r["label"] != "Bogus" for r in recs)
    covered = [sha for r in recs for sha in r["commit_shas"]]
    assert sorted(covered) == sorted(r.commit_sha for r in runs)  # total coverage preserved


def test_llm_exception_falls_back_to_deterministic(tmp_path, monkeypatch):
    runs, by_id = _four_commit_feature(tmp_path)
    themer = _themer_with(monkeypatch, tmp_path, RuntimeError("no network"))
    recs = themer.segment_feature("F-A", runs, by_id, lambda s: None)
    assert all(r["source"] == "fallback" for r in recs)
    det = segment.segment_runs(runs)
    assert len(recs) == len(det)


def test_single_run_feature_skips_the_llm(tmp_path, monkeypatch):
    gb, _ = init_store(tmp_path)
    (tmp_path / "a.py").write_text("def foo():\n    return 1\n")
    gb.commit_all("feat(x): add foo")
    get(tmp_path)
    op_leaf = _save_tree(tmp_path, {"F-A": ["a.py::foo"]})
    runs = feature_runs(tmp_path, op_leaf)["F-A"]
    by_id = {op.id: op for op in Store(tmp_path).all_ops()}

    called = {"n": 0}

    class Client:
        class responses:
            @staticmethod
            def parse(**kwargs):
                called["n"] += 1
                raise AssertionError("should not be called for a single-run feature")

    themer = SegmentThemer(tmp_path)
    themer._client = Client()
    recs = themer.segment_feature("F-A", runs, by_id, lambda s: None)
    assert called["n"] == 0
    assert len(recs) == 1


def test_op_membership_is_preserved_through_overlay(tmp_path, monkeypatch):
    """The safety invariant: whatever the LLM labels, the op-set the segments cover equals the
    feature's ops exactly -- membership is a function of commit-shas, not the LLM's words."""
    runs, by_id = _four_commit_feature(tmp_path)
    shas = [r.commit_sha[:8] for r in runs]
    plan = SegmentPlan(segments=[
        SegmentGroup(label="All", rationale="everything", commit_shas=shas),
    ])
    themer = _themer_with(monkeypatch, tmp_path, plan)
    recs = themer.segment_feature("F-A", runs, by_id, lambda s: None)
    segs = overlay_persisted(runs, recs)
    covered = frozenset().union(*(s.op_ids for s in segs))
    assert covered == frozenset().union(*(r.op_ids for r in runs))


def _n_commit_feature(tmp_path, n):
    gb, _ = init_store(tmp_path)
    for i in range(n):
        body = "".join(f"def f{j}():\n    return {j}\n\n\n" for j in range(i + 1))
        (tmp_path / "a.py").write_text(body)
        gb.commit_all(f"feat(x): step {i}")
    get(tmp_path)
    op_leaf = _save_tree(tmp_path, {"F-A": [f"a.py::f{j}" for j in range(n)]})
    runs = feature_runs(tmp_path, op_leaf)["F-A"]
    by_id = {op.id: op for op in Store(tmp_path).all_ops()}
    return runs, by_id


class _RecordingClient:
    """A fake LLM client that records the sha prefixes it was shown per call and groups every shown
    commit into one 'Tail' chapter -- so a test can assert exactly which runs the incremental
    windowing sent to the model."""
    def __init__(self):
        self.shown: list[list[str]] = []

    class _Usage:
        input_tokens = output_tokens = 1

    @property
    def responses(self):
        return self

    def parse(self, **kwargs):
        import re
        prefixes = re.findall(r"\[\d+\] ([0-9a-f]{8}) \|", kwargs["input"])
        self.shown.append(prefixes)
        plan = SegmentPlan(segments=[SegmentGroup(label="Tail", rationale="r", commit_shas=prefixes)])
        r = type("R", (), {})()
        r.output_parsed = plan
        r.usage = self._Usage()
        return r


def test_tail_recut_freezes_all_but_last_chapter_and_windows_the_llm(tmp_path, monkeypatch):
    """§3.4 incremental tail re-cut: given a feature's previous persisted record, every chapter but
    the last freezes verbatim; only the last persisted chapter's runs + newer commits are sent to
    the LLM and re-cut. So a rebuild after new commits re-pays only O(new work), and every
    `pin_key`/`@n` below the tail survives by construction."""
    runs, by_id = _n_commit_feature(tmp_path, 6)
    shas = [r.commit_sha for r in runs]
    # A previous build named runs 0,1 "Scaffold" and 2,3 "Finish"; runs 4,5 landed since.
    record = [
        {"commit_shas": shas[0:2], "label": "Scaffold", "rationale": "", "source": "llm"},
        {"commit_shas": shas[2:4], "label": "Finish", "rationale": "", "source": "llm"},
    ]
    client = _RecordingClient()
    themer = SegmentThemer(tmp_path)
    themer._client = client
    recs = themer.segment_features([("F-A", runs)], by_id, lambda s: None, [record])[0]

    # The LLM saw only the window (last frozen chapter's runs 2,3 + new runs 4,5), never 0,1.
    assert len(client.shown) == 1
    assert client.shown[0] == [s[:8] for s in shas[2:6]]
    # The frozen prefix chapter is spliced back verbatim -- its pin_key (first sha) is untouched.
    assert recs[0]["commit_shas"] == shas[0:2] and recs[0]["label"] == "Scaffold"
    # Total coverage preserved: every run lands in exactly one spliced chapter.
    covered = [sha for r in recs for sha in r["commit_shas"]]
    assert sorted(covered) == sorted(shas)


def test_no_prior_record_windows_the_whole_timeline(tmp_path, monkeypatch):
    """With no persisted record (`prior_records=None`, a first build or `--recut`), the window is
    the whole feature -- byte-identical to the pre-incremental behavior."""
    runs, by_id = _n_commit_feature(tmp_path, 4)
    client = _RecordingClient()
    themer = SegmentThemer(tmp_path)
    themer._client = client
    themer.segment_features([("F-A", runs)], by_id, lambda s: None)

    assert client.shown == [[r.commit_sha[:8] for r in runs]]  # all four commits, one call


def test_build_segments_no_client_writes_deterministic(tmp_path, monkeypatch):
    def _no_client(*a, **k):
        raise RuntimeError("OPENAI_API_KEY not found")

    monkeypatch.setattr(theme_segment, "get_client", _no_client)
    runs, by_id = _four_commit_feature(tmp_path)  # also builds tree + mines
    out = theme_segment.build_segments(tmp_path)
    assert "F-A" in out
    assert all(r["source"] == "fallback" for r in out["F-A"])


def test_no_change_rebuild_preserves_an_llm_single_run_tail_chapter(tmp_path, monkeypatch):
    """Idempotence: a second build with zero new commits must leave the persisted record
    byte-identical. The incremental tail re-cut previously re-derived the window, and a
    single-run window skips the LLM -- demoting the tail chapter's LLM label to the raw
    commit subject ("fallback") on a pure no-op rebuild."""
    runs, by_id = _four_commit_feature(tmp_path)
    shas = [r.commit_sha for r in runs]
    record = [
        {"commit_shas": shas[:3], "label": "Scaffold The Thing", "rationale": "r", "source": "llm"},
        {"commit_shas": shas[3:], "label": "Polish The Thing", "rationale": "r", "source": "llm"},
    ]
    themer = _themer_with(monkeypatch, tmp_path, RuntimeError("no network"))
    recs = themer.segment_feature("F-A", runs, by_id, lambda s: None, record=record)
    assert recs == record


def test_no_change_rebuild_preserves_an_llm_multi_run_tail_chapter(tmp_path, monkeypatch):
    """Same idempotence for a multi-run tail: an unchanged window must splice the persisted
    chapter through verbatim rather than re-deriving it (an offline rebuild demotes it to
    fallback; an online one re-pays an LLM call for a cut it already has)."""
    runs, by_id = _four_commit_feature(tmp_path)
    shas = [r.commit_sha for r in runs]
    record = [
        {"commit_shas": shas[:2], "label": "Scaffold The Thing", "rationale": "r", "source": "llm"},
        {"commit_shas": shas[2:], "label": "Polish The Thing", "rationale": "r", "source": "llm"},
    ]
    themer = _themer_with(monkeypatch, tmp_path, RuntimeError("no network"))
    recs = themer.segment_feature("F-A", runs, by_id, lambda s: None, record=record)
    assert recs == record


def test_fallback_sourced_tail_chapter_still_gets_the_llm_retry(tmp_path, monkeypatch):
    """The verbatim splice is gated on source == "llm": an unchanged but fallback-sourced tail
    (a record persisted offline) must still re-enter the live window so a now-available client
    upgrades it -- the module's retry-on-fallback policy, same gate as the cache-hit path."""
    runs, by_id = _four_commit_feature(tmp_path)
    shas = [r.commit_sha for r in runs]
    shas8 = [s[:8] for s in shas]
    record = [
        {"commit_shas": shas[:2], "label": "Scaffold The Thing", "rationale": "r", "source": "llm"},
        {"commit_shas": shas[2:], "label": "feat(x): step 2", "rationale": "one commit", "source": "fallback"},
    ]
    plan = SegmentPlan(segments=[
        SegmentGroup(label="Polish", rationale="p", commit_shas=[shas8[2], shas8[3]]),
    ])
    themer = _themer_with(monkeypatch, tmp_path, plan)
    recs = themer.segment_feature("F-A", runs, by_id, lambda s: None, record=record)
    assert recs[0] == record[0]                          # frozen prefix untouched
    assert all(r["source"] == "llm" for r in recs[1:])   # tail was retried, not spliced
    assert [r["label"] for r in recs[1:]] == ["Polish"]


def test_resolve_recut_resolution_branches():
    """`--recut <spec>` resolution: exact leaf id, unique id prefix (bare hex or `f-`-prefixed),
    case-insensitive unique label; ambiguity or no match silently resolves to nothing (the
    incremental path stays in effect); non-leaf nodes are never recut targets."""
    nodes = {
        "f-abc111": _leaf([], "x") | {"label": "Login Flow"},
        "f-abd222": _leaf([], "x") | {"label": "Search"},
        "f-xyz333": _leaf([], "x") | {"label": "search"},
        "n-parent": _leaf([], "x") | {"label": "Parent", "children": ["f-abc111"]},
    }
    resolve = theme_segment._resolve_recut
    assert resolve(None, nodes) == set()
    assert resolve("f-abc111", nodes) == {"f-abc111"}   # exact id
    assert resolve("abc", nodes) == {"f-abc111"}        # unique bare-hex prefix
    assert resolve("ab", nodes) == set()                # ambiguous prefix -> nothing
    assert resolve("login flow", nodes) == {"f-abc111"} # case-insensitive label
    assert resolve("search", nodes) == set()            # ambiguous label -> nothing
    assert resolve("nope", nodes) == set()              # no match
    assert resolve("n-parent", nodes) == set()          # a non-leaf is never a recut target
