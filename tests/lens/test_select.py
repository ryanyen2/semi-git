"""Tests for sgt.lens.select -- U29's closure-explanation UX (`sgt select` / `sgt why`).

The feature tree is hand-crafted rather than clustered (`tree.save` directly, bypassing
`lens.map.build_map`'s Leiden clusterer): U25's own BET-C measurement showed this repo's real
clustering is too coarse/unreliable to pin exact feature membership in a unit test, and these
tests are about `select`/`why`'s closure and explanation logic, not clustering quality. Real
mining (`get()`) still produces the ops and their genuine chain/requires edges -- only the
feature-membership partition is authored directly.
"""

from __future__ import annotations

from sgt.core.lens import get
from sgt.core.store import Store
from sgt.lens import select, tree
from sgt.store.gitbind import init_store


def _leaf(members: list[str], label: str) -> dict:
    return {"parent": None, "children": [], "members": sorted(members), "size": len(members),
            "dir": "", "label": label}


def _save_tree(repo, leaves: dict[str, list[str]]) -> None:
    """`leaves`: `{feature_id: [member symbol, ...]}`. Builds `op_leaf` with the real
    `assign_ops_to_leaves` vote over whatever's actually mined, so it stays consistent with the
    ops under test rather than being asserted by hand."""
    nodes = {fid: _leaf(members, fid) for fid, members in leaves.items()}
    ops = Store(repo).all_ops()
    result = {
        "nodes": nodes, "roots": sorted(nodes), "op_leaf": tree.assign_ops_to_leaves(nodes, ops),
        "max_depth": 0, "cannot_link_moves": [], "identity_events": [],
    }
    tree.save(repo, result)


def _op_for(repo, symbol: str):
    ops = Store(repo).all_ops()
    return next(op for op in ops if symbol in op.footprint)


def test_cross_feature_requires_chain_reports_exactly_that_chain(tmp_path):
    """`b.py::caller` (feature F-B) calls `a.py::base` (feature F-A), added in an earlier commit
    so mining records a genuine `requires` edge (not a same-op tangle). Selecting F-B alone must
    pull `base`'s op into the closure and report the exact one-hop requires chain that did it."""
    gb, _ = init_store(tmp_path)
    (tmp_path / "a.py").write_text("def base():\n    return 1\n", encoding="utf-8")
    gb.commit_all("add a.py")
    (tmp_path / "b.py").write_text(
        "from a import base\n\n\ndef caller():\n    return base() + 1\n", encoding="utf-8",
    )
    gb.commit_all("add b.py calling base")
    get(tmp_path)

    base_op = _op_for(tmp_path, "a.py::base")
    caller_op = _op_for(tmp_path, "b.py::caller")
    assert ("a.py::base", base_op.footprint["a.py::base"][1]) in caller_op.requires

    _save_tree(tmp_path, {"F-A": ["a.py::base"], "F-B": ["b.py::caller"]})

    result = select.select(tmp_path, ["F-B"])
    assert result.ok, result.message
    assert result.direct_op_count == 1
    assert result.closure_op_count == 2
    assert len(result.pulled) == 1
    assert result.pulled[0].feature_id == "F-A"
    assert result.pulled[0].op_count == 1
    assert result.pulled[0].chain == (
        {"op_id": caller_op.id, "via": None},
        {"op_id": base_op.id, "via": "requires"},
    )
    assert result.hub == {"symbol": "a.py::base", "pulled_op_count": 1}


def test_reference_independent_features_select_independently(tmp_path):
    """Three features with no `requires` edge between them: selecting one must pull in nothing
    from the others (design doc S2 point 3 -- clustering co-membership, or mere presence in the
    same tree, must never force inclusion; only a real edge does)."""
    gb, _ = init_store(tmp_path)
    (tmp_path / "a.py").write_text("def base():\n    return 1\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("def other():\n    return 2\n", encoding="utf-8")
    (tmp_path / "c.py").write_text("def standalone():\n    return 3\n", encoding="utf-8")
    gb.commit_all("add three unrelated files")
    get(tmp_path)

    _save_tree(tmp_path, {
        "F-A": ["a.py::base"], "F-B": ["b.py::other"], "F-C": ["c.py::standalone"],
    })

    result = select.select(tmp_path, ["F-A"])
    assert result.ok, result.message
    assert result.direct_op_count == 1
    assert result.closure_op_count == 1  # nothing pulled in
    assert result.pulled == ()
    assert result.hub is None


def test_hub_symbol_diagnosed_not_a_giant_silent_closure(tmp_path):
    """Three callers in feature F-B all require the same F-A symbol -- the diagnosis must name
    that one hub symbol, and the closure must stay exactly as large as the real dependency (one
    pulled-in op), not balloon or get swallowed silently."""
    gb, _ = init_store(tmp_path)
    (tmp_path / "a.py").write_text("def base():\n    return 1\n", encoding="utf-8")
    gb.commit_all("add a.py")
    (tmp_path / "b.py").write_text(
        "from a import base\n\n\n"
        "def caller1():\n    return base() + 1\n\n\n"
        "def caller2():\n    return base() + 2\n\n\n"
        "def caller3():\n    return base() + 3\n",
        encoding="utf-8",
    )
    gb.commit_all("add three callers of base")
    get(tmp_path)

    base_op = _op_for(tmp_path, "a.py::base")
    _save_tree(tmp_path, {
        "F-A": ["a.py::base"],
        "F-B": ["b.py::caller1", "b.py::caller2", "b.py::caller3"],
    })

    result = select.select(tmp_path, ["F-B"])
    assert result.ok, result.message
    assert result.direct_op_count == 3
    assert result.closure_op_count == 4  # 3 direct + exactly the one hub op, not more
    assert len(result.pulled) == 1
    assert result.pulled[0].feature_id == "F-A"
    assert result.pulled[0].op_count == 1
    assert result.hub == {"symbol": "a.py::base", "pulled_op_count": 1}
    assert base_op.id in {hop["op_id"] for hop in result.pulled[0].chain}


def test_why_reports_the_plurality_vote_with_no_target_feature(tmp_path):
    gb, _ = init_store(tmp_path)
    (tmp_path / "a.py").write_text("def base():\n    return 1\n", encoding="utf-8")
    gb.commit_all("add a.py")
    get(tmp_path)

    base_op = _op_for(tmp_path, "a.py::base")
    _save_tree(tmp_path, {"F-A": ["a.py::base"]})

    result = select.why(tmp_path, base_op.id)
    assert result.ok, result.message
    assert result.feature_id == "F-A"
    assert result.votes == ({"feature_id": "F-A", "count": 1},)


def test_why_for_feature_traces_the_pull_chain(tmp_path):
    gb, _ = init_store(tmp_path)
    (tmp_path / "a.py").write_text("def base():\n    return 1\n", encoding="utf-8")
    gb.commit_all("add a.py")
    (tmp_path / "b.py").write_text(
        "from a import base\n\n\ndef caller():\n    return base() + 1\n", encoding="utf-8",
    )
    gb.commit_all("add b.py calling base")
    get(tmp_path)

    base_op = _op_for(tmp_path, "a.py::base")
    caller_op = _op_for(tmp_path, "b.py::caller")
    _save_tree(tmp_path, {"F-A": ["a.py::base"], "F-B": ["b.py::caller"]})

    result = select.why(tmp_path, base_op.id, for_feature="F-B")
    assert result.ok, result.message
    assert result.feature_id == "F-A"
    assert result.for_feature == "F-B"
    assert result.chain == (
        {"op_id": caller_op.id, "via": None},
        {"op_id": base_op.id, "via": "requires"},
    )


def test_why_for_feature_refuses_when_op_is_not_in_that_closure(tmp_path):
    gb, _ = init_store(tmp_path)
    (tmp_path / "a.py").write_text("def base():\n    return 1\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("def other():\n    return 2\n", encoding="utf-8")
    gb.commit_all("add two unrelated files")
    get(tmp_path)

    other_op = _op_for(tmp_path, "b.py::other")
    _save_tree(tmp_path, {"F-A": ["a.py::base"], "F-B": ["b.py::other"]})

    result = select.why(tmp_path, other_op.id, for_feature="F-A")
    assert not result.ok
    assert "not part of" in result.message
