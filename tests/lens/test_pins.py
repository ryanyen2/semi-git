"""Tests for sgt.lens.pins -- must-link/cannot-link/assign constraints (plan U12 D3/D4)."""

from __future__ import annotations

from sgt.core.lens import get
from sgt.core.store import Store
from sgt.lens import pins as pins_mod
from sgt.lens import tree
from sgt.store.gitbind import init_store


def test_load_pins_on_missing_file_is_empty_not_none(tmp_path):
    p = pins_mod.load_pins(tmp_path)
    assert p == pins_mod.Pins()


def test_save_then_load_pins_round_trips(tmp_path):
    p = pins_mod.Pins(
        assign={"a.py::foo": "F1"},
        must_link=frozenset({("a.py::bar", "a.py::foo")}),  # already sorted, per Pins's convention
        cannot_link=frozenset({("a.py::bar", "b.py::baz")}),
    )
    pins_mod.save_pins(tmp_path, p)
    loaded = pins_mod.load_pins(tmp_path)
    assert loaded == p


def test_find_contradictions_never_raises_and_is_empty_for_clean_pins():
    p = pins_mod.Pins(
        must_link=frozenset({("a", "b")}), cannot_link=frozenset({("c", "d")}),
        assign={"a": "F1", "b": "F1"},
    )
    assert pins_mod.find_contradictions(p) == []


def test_find_contradictions_same_pair_must_and_cannot_link():
    p = pins_mod.Pins(must_link=frozenset({("a", "b")}), cannot_link=frozenset({("a", "b")}))
    contras = pins_mod.find_contradictions(p)
    assert len(contras) == 1
    assert contras[0].kind == "must_and_cannot_link"


def test_find_contradictions_assign_conflict_within_must_link_group():
    p = pins_mod.Pins(must_link=frozenset({("a", "b")}), assign={"a": "F1", "b": "F2"})
    contras = pins_mod.find_contradictions(p)
    assert len(contras) == 1
    assert contras[0].kind == "assign_conflict_in_must_link_group"


def test_find_contradictions_cannot_link_within_must_link_group_transitively():
    # a-b and b-c must-link (closure: {a,b,c}); a-c is cannot-link -- contradictory.
    p = pins_mod.Pins(
        must_link=frozenset({("a", "b"), ("b", "c")}), cannot_link=frozenset({("a", "c")}),
    )
    contras = pins_mod.find_contradictions(p)
    assert len(contras) == 1
    assert contras[0].kind == "cannot_link_within_must_link_group"


def test_apply_must_link_contracts_group_into_one_synthetic_vertex():
    nodes = ["a", "b", "c"]
    fused = {("a", "c"): 1.0, ("b", "c"): 2.0}
    p = pins_mod.Pins(must_link=frozenset({("a", "b")}))

    new_nodes, new_edges, expansion = pins_mod.apply_must_link(nodes, fused, p)

    assert len(new_nodes) == 2  # {a,b} contracted into one, plus c
    synthetic = next(n for n in new_nodes if n != "c")
    assert expansion[synthetic] == frozenset({"a", "b"})
    # both a-c and b-c edges land on the synthetic vertex, summed
    assert new_edges[tuple(sorted((synthetic, "c")))] == 3.0


def test_apply_must_link_ignores_group_members_not_in_this_node_set():
    nodes = ["a", "c"]  # "b" (a's must-link partner) isn't in this graph at all
    fused = {("a", "c"): 1.0}
    p = pins_mod.Pins(must_link=frozenset({("a", "b")}))

    new_nodes, new_edges, expansion = pins_mod.apply_must_link(nodes, fused, p)

    assert sorted(new_nodes) == ["a", "c"]  # no contraction -- "b" absent, group has < 2 present
    assert expansion == {}


def test_expand_members_reverses_contraction_through_nested_children():
    root = {
        "members": ["\x00pin::a", "z"], "children": [
            {"members": ["\x00pin::a"], "children": []},
            {"members": ["z"], "children": []},
        ],
    }
    expansion = {"\x00pin::a": frozenset({"a", "b"})}
    pins_mod._expand_members(root, expansion)
    assert root["members"] == ["a", "b", "z"]
    assert root["children"][0]["members"] == ["a", "b"]
    assert root["children"][1]["members"] == ["z"]


def test_enforce_cannot_link_moves_later_member_to_next_best_other_leaf():
    nodes = {
        "N0": {"children": [], "members": ["a", "b"]},
        "N1": {"children": [], "members": ["c"]},
        "N2": {"children": [], "members": ["d"]},
    }
    p = pins_mod.Pins(cannot_link=frozenset({("a", "b")}))
    # "b" is more coupled to N2 ("d") than N1 ("c")
    adj = {"b": [("c", 1.0), ("d", 5.0)]}

    moves = pins_mod.enforce_cannot_link(nodes, p, adj)

    assert nodes["N0"]["members"] == ["a"]
    assert nodes["N2"]["members"] == ["b", "d"]
    assert nodes["N1"]["members"] == ["c"]
    assert len(moves) == 1


def test_enforce_cannot_link_is_a_noop_when_pair_already_in_different_leaves():
    nodes = {
        "N0": {"children": [], "members": ["a"]},
        "N1": {"children": [], "members": ["b"]},
    }
    p = pins_mod.Pins(cannot_link=frozenset({("a", "b")}))
    moves = pins_mod.enforce_cannot_link(nodes, p, adj={})
    assert moves == []
    assert nodes["N0"]["members"] == ["a"]
    assert nodes["N1"]["members"] == ["b"]


def _grow_repo(repo_path):
    """A repo whose two files' top-level functions call each other -- coupled enough that
    Leiden alone might keep them together anyway; the point is they *must* stay together even as
    unrelated commits pile on, which only a real pin structurally guarantees."""
    gb, _ = init_store(repo_path)
    (repo_path / "a.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    (repo_path / "b.py").write_text("def bar():\n    return 2\n", encoding="utf-8")
    gb.commit_all("feat: add foo and bar")
    return gb


def test_assign_pin_keeps_two_members_in_the_same_leaf_across_ten_reclusters(tmp_path):
    repo = tmp_path / "repo"
    gb = _grow_repo(repo)
    p = pins_mod.Pins(assign={"a.py::foo": "F-pinned", "b.py::bar": "F-pinned"})
    pins_mod.save_pins(repo, p)

    for i in range(10):
        (repo / f"filler{i}.py").write_text(f"def filler{i}():\n    return {i}\n", encoding="utf-8")
        gb.commit_all(f"chore: add unrelated filler {i}")

        ideal = get(repo)
        ops = Store(repo).all_ops()
        result = tree.build(repo, ops, ideal)

        member_leaf = {m: nid for nid, nd in result["nodes"].items() if not nd["children"] for m in nd["members"]}
        assert member_leaf["a.py::foo"] == member_leaf["b.py::bar"], f"split apart after filler {i}"
