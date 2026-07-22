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


def test_build_segments_no_client_writes_deterministic(tmp_path, monkeypatch):
    def _no_client(*a, **k):
        raise RuntimeError("OPENAI_API_KEY not found")

    monkeypatch.setattr(theme_segment, "get_client", _no_client)
    runs, by_id = _four_commit_feature(tmp_path)  # also builds tree + mines
    out = theme_segment.build_segments(tmp_path)
    assert "F-A" in out
    assert all(r["source"] == "fallback" for r in out["F-A"])
