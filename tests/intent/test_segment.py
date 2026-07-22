"""Tests for sgt.intent.segment -- the deterministic feature-scoped segmentation (rungs 0/1).
No LLM/network anywhere; every assertion is pure, rebuildable derivation from the mined store +
git history + a hand-authored feature tree, same idiom as tests/intent/test_group.py."""

from __future__ import annotations

from sgt.core.lens import get
from sgt.core.store import Store
from sgt.intent import segment
from sgt.lens import tree
from sgt.store.gitbind import init_store


def _leaf(members: list[str], label: str) -> dict:
    return {"parent": None, "children": [], "members": sorted(members), "size": len(members),
            "dir": "", "label": label}


def _save_tree(repo, leaves: dict[str, list[str]]) -> dict[str, str]:
    nodes = {fid: _leaf(members, fid) for fid, members in leaves.items()}
    ops = Store(repo).all_ops()
    result = {"nodes": nodes, "roots": sorted(nodes),
              "op_leaf": tree.assign_ops_to_leaves(nodes, ops),
              "max_depth": 0, "cannot_link_moves": [], "identity_events": []}
    tree.save(repo, result)
    return result["op_leaf"]


# -- rung 0: feature_runs -------------------------------------------------------------------------


def test_ops_of_a_feature_in_one_commit_form_one_run(tmp_path):
    gb, _ = init_store(tmp_path)
    (tmp_path / "a.py").write_text("def foo():\n    return 1\n\n\ndef bar():\n    return 2\n")
    gb.commit_all("feat(x): add foo and bar")
    get(tmp_path)

    op_leaf = _save_tree(tmp_path, {"F-A": ["a.py::foo", "a.py::bar"]})
    runs = segment.feature_runs(tmp_path, op_leaf)
    assert list(runs) == ["F-A"]
    assert len(runs["F-A"]) == 1
    assert runs["F-A"][0].scope == "x"


def test_runs_are_ordered_by_commit_index(tmp_path):
    gb, _ = init_store(tmp_path)
    (tmp_path / "a.py").write_text("def foo():\n    return 1\n")
    gb.commit_all("feat(x): add foo")
    (tmp_path / "a.py").write_text("def foo():\n    return 1\n\n\ndef baz():\n    return 3\n")
    gb.commit_all("feat(x): add baz")
    get(tmp_path)

    op_leaf = _save_tree(tmp_path, {"F-A": ["a.py::foo", "a.py::baz"]})
    runs = segment.feature_runs(tmp_path, op_leaf)["F-A"]
    assert [r.commit_index for r in runs] == sorted(r.commit_index for r in runs)
    assert len(runs) == 2


def test_op_with_unknown_feature_is_skipped_from_runs(tmp_path):
    gb, _ = init_store(tmp_path)
    (tmp_path / "a.py").write_text("def foo():\n    return 1\n")
    gb.commit_all("add foo")
    get(tmp_path)
    # empty op_leaf -> no feature known -> no runs, never an error
    assert segment.feature_runs(tmp_path, {}) == {}


# -- novelty --------------------------------------------------------------------------------------


def test_creating_a_symbol_is_high_novelty(tmp_path):
    gb, _ = init_store(tmp_path)
    (tmp_path / "a.py").write_text("def foo():\n    return 1\n")
    gb.commit_all("feat(x): add foo")
    get(tmp_path)

    op_leaf = _save_tree(tmp_path, {"F-A": ["a.py::foo"]})
    run = segment.feature_runs(tmp_path, op_leaf)["F-A"][0]
    assert run.novelty == 1.0  # every content touch is a create


def test_modifying_in_place_is_low_novelty(tmp_path):
    gb, _ = init_store(tmp_path)
    (tmp_path / "a.py").write_text("def foo(x):\n    return 1\n")
    gb.commit_all("feat(x): add foo")
    (tmp_path / "a.py").write_text("def foo(x, y):\n    return 1\n")  # rename/add-param: a tweak
    gb.commit_all("feat(x): add a param to foo")
    get(tmp_path)

    op_leaf = _save_tree(tmp_path, {"F-A": ["a.py::foo"]})
    runs = segment.feature_runs(tmp_path, op_leaf)["F-A"]
    modify_run = runs[-1]
    assert modify_run.novelty == 0.0  # no create/remove, only in-place modification


# -- rung 1: segment_runs -------------------------------------------------------------------------


def test_low_novelty_tweak_merges_into_previous_segment(tmp_path):
    gb, _ = init_store(tmp_path)
    (tmp_path / "a.py").write_text("def foo(x):\n    return 1\n")
    gb.commit_all("feat(x): add foo")
    (tmp_path / "a.py").write_text("def foo(x, y):\n    return 1\n")
    gb.commit_all("feat(x): tweak foo signature")
    get(tmp_path)

    op_leaf = _save_tree(tmp_path, {"F-A": ["a.py::foo"]})
    runs = segment.feature_runs(tmp_path, op_leaf)["F-A"]
    segs = segment.segment_runs(runs)
    assert len(segs) == 1  # the tweak did not open a new chapter
    # the whole feature's ops are in the one segment (total partition)
    assert segs[0].op_ids == frozenset().union(*(r.op_ids for r in runs))


def test_scope_change_forces_a_boundary(tmp_path):
    gb, _ = init_store(tmp_path)
    (tmp_path / "a.py").write_text("def foo(x):\n    return 1\n")
    gb.commit_all("feat(x): add foo")
    (tmp_path / "a.py").write_text("def foo(x, y):\n    return 1\n")
    gb.commit_all("fix(y): tweak foo under a different scope")
    get(tmp_path)

    op_leaf = _save_tree(tmp_path, {"F-A": ["a.py::foo"]})
    runs = segment.feature_runs(tmp_path, op_leaf)["F-A"]
    segs = segment.segment_runs(runs)
    assert len(segs) == 2  # scope shift cuts even though the second run is a low-novelty tweak


def test_every_run_lands_in_exactly_one_segment(tmp_path):
    gb, _ = init_store(tmp_path)
    for i in range(4):
        (tmp_path / "a.py").write_text("".join(f"def f{j}():\n    return {j}\n\n\n" for j in range(i + 1)))
        gb.commit_all(f"feat(x): step {i}")
    get(tmp_path)

    op_leaf = _save_tree(tmp_path, {"F-A": [f"a.py::f{j}" for j in range(4)]})
    runs = segment.feature_runs(tmp_path, op_leaf)["F-A"]
    segs = segment.segment_runs(runs)
    covered = frozenset().union(*(s.op_ids for s in segs))
    assert covered == frozenset().union(*(r.op_ids for r in runs))
    # no op counted twice
    assert sum(s.op_count for s in segs) == len(covered)


def test_segmentation_is_deterministic(tmp_path):
    gb, _ = init_store(tmp_path)
    (tmp_path / "a.py").write_text("def foo():\n    return 1\n")
    gb.commit_all("feat(x): add foo")
    (tmp_path / "b.py").write_text("def bar():\n    return 2\n")
    gb.commit_all("fix(y): add bar")
    get(tmp_path)

    op_leaf = _save_tree(tmp_path, {"F-A": ["a.py::foo", "b.py::bar"]})
    first = segment.deterministic_segments(tmp_path, op_leaf)
    second = segment.deterministic_segments(tmp_path, op_leaf)
    assert first == second


def test_empty_feature_yields_no_segments(tmp_path):
    assert segment.segment_runs([]) == []


# -- resolve_checkpoint (the <feature>@<n> rewind handle) -----------------------------------------


def _feature_with_two_segments(tmp_path):
    gb, _ = init_store(tmp_path)
    (tmp_path / "a.py").write_text("def foo():\n    return 1\n")
    gb.commit_all("feat(x): add foo")
    (tmp_path / "b.py").write_text("def bar():\n    return 2\n")
    gb.commit_all("fix(y): add bar")  # scope change -> two segments
    get(tmp_path)
    op_leaf = _save_tree(tmp_path, {"F-A": ["a.py::foo", "b.py::bar"]})
    # write a label so label-matching can be exercised
    nodes = {"F-A": _leaf(["a.py::foo", "b.py::bar"], "My Feature")}
    ops = Store(tmp_path).all_ops()
    tree.save(tmp_path, {"nodes": nodes, "roots": ["F-A"],
                         "op_leaf": tree.assign_ops_to_leaves(nodes, ops),
                         "max_depth": 0, "cannot_link_moves": [], "identity_events": []})
    return segment.deterministic_segments(tmp_path, op_leaf)["F-A"]


def test_resolve_checkpoint_by_feature_id_and_index(tmp_path):
    segs = _feature_with_two_segments(tmp_path)
    resolved = segment.resolve_checkpoint(tmp_path, "F-A@1")
    assert resolved is not None
    op_ids, label = resolved
    assert op_ids == segs[1].op_ids
    assert "My Feature@1" in label


def test_resolve_checkpoint_by_label(tmp_path):
    _feature_with_two_segments(tmp_path)
    resolved = segment.resolve_checkpoint(tmp_path, "my feature@0")
    assert resolved is not None


def test_resolve_checkpoint_bad_specs_return_none(tmp_path):
    _feature_with_two_segments(tmp_path)
    assert segment.resolve_checkpoint(tmp_path, "F-A") is None          # no @n
    assert segment.resolve_checkpoint(tmp_path, "F-A@9") is None        # index out of range
    assert segment.resolve_checkpoint(tmp_path, "nope@0") is None       # unknown feature
    assert segment.resolve_checkpoint(tmp_path, "F-A@x") is None        # non-numeric index


def test_apply_label_pins_overrides_and_marks_user_source(tmp_path):
    segs = _feature_with_two_segments(tmp_path)
    key = segment.pin_key(segs[0])
    pinned = segment.apply_label_pins(segs, {key: "My Custom Intent"})
    assert pinned[0].label == "My Custom Intent"
    assert pinned[0].source == "user"
    # op membership is untouched by a relabel -- only the label/source change
    assert pinned[0].op_ids == segs[0].op_ids
    # the unpinned segment is unchanged
    assert pinned[1].label == segs[1].label


def test_apply_label_pins_ignores_unmatched_key(tmp_path):
    segs = _feature_with_two_segments(tmp_path)
    pinned = segment.apply_label_pins(segs, {"deadbeef" * 5: "nope"})
    assert [s.label for s in pinned] == [s.label for s in segs]
    assert all(s.source != "user" for s in pinned)


def test_verb_preview_view_resolves_a_checkpoint(tmp_path):
    """The VS Code hover-preview path: `verb_preview_view('revert', '<feature>@<n>')` must resolve
    to the segment's deterministic op-set (not silently fall through to an unresolvable plan)."""
    from sgt.api import verb_preview_view

    segs = _feature_with_two_segments(tmp_path)
    v = verb_preview_view(tmp_path, "revert", "F-A@1")
    assert v["ok"] is True
    # the removed set is the segment's upset closure -> at least the segment's own ops
    assert segs[1].op_ids <= frozenset(v["removed"])


def test_soft_cap_bounds_segment_count(tmp_path):
    """A feature with many distinct-scope commits would cut into many segments; the MAX_SEGMENTS
    soft cap keeps the offline view readable."""
    gb, _ = init_store(tmp_path)
    members = []
    for i in range(segment.MAX_SEGMENTS + 4):
        sym = f"f{i}"
        members.append(f"a.py::{sym}")
        body = "".join(f"def f{j}():\n    return {j}\n\n\n" for j in range(i + 1))
        (tmp_path / "a.py").write_text(body)
        gb.commit_all(f"feat(s{i}): add {sym}")  # every commit a fresh scope -> a seam each
    get(tmp_path)

    op_leaf = _save_tree(tmp_path, {"F-A": members})
    runs = segment.feature_runs(tmp_path, op_leaf)["F-A"]
    segs = segment.segment_runs(runs)
    assert len(segs) <= segment.MAX_SEGMENTS
    # still a total partition after capping
    assert frozenset().union(*(s.op_ids for s in segs)) == frozenset().union(*(r.op_ids for r in runs))
