"""U2 — entity graph assembly, transitive reduction, components, projection shape."""

from __future__ import annotations

from types import SimpleNamespace

from sgt.api import entity_graph_view
from sgt.entities import Entity
from sgt.entities.graph import build_entity_graph, owning_nodes


def _edges(g, type_=None):
    return {(e.src, e.dst) for e in g.edges if type_ is None or e.type == type_}


def _reduced(g, type_=None):
    return {(e.src, e.dst) for e in g.reduced_edges if type_ is None or e.type == type_}


def test_same_file_call_and_containment():
    src = (
        "def callee():\n"
        "    return 1\n"
        "def caller():\n"
        "    return callee()\n"
        "class C:\n"
        "    def meth(self):\n"
        "        return 2\n"
    )
    g = build_entity_graph({"m.py": src})
    assert ("m.py::caller", "m.py::callee") in _edges(g, "calls")
    assert ("m.py::C", "m.py::C.meth") in _edges(g, "contains")


def test_cross_file_reference_is_an_import_edge():
    cb = {
        "util.py": "def helper():\n    return 1\n",
        "main.py": "from util import helper\n\ndef run():\n    return helper()\n",
    }
    g = build_entity_graph(cb)
    assert ("main.py::run", "util.py::helper") in _edges(g, "imports")


def test_transitive_reduction_drops_implied_edge():
    src = (
        "def C():\n    return 1\n"
        "def B():\n    return C()\n"
        "def A():\n    B()\n    return C()\n"
    )
    g = build_entity_graph({"m.py": src})
    # Full graph keeps the direct A->C; reduced drops it (A->B->C implies it).
    assert ("m.py::A", "m.py::C") in _edges(g, "calls")
    assert ("m.py::A", "m.py::C") not in _reduced(g, "calls")
    assert ("m.py::A", "m.py::B") in _reduced(g, "calls")
    assert ("m.py::B", "m.py::C") in _reduced(g, "calls")


def test_cycle_edges_are_preserved_in_reduction():
    src = "def A():\n    return B()\ndef B():\n    return A()\n"
    g = build_entity_graph({"m.py": src})
    # Reduction is only defined on a DAG; a 2-cycle is an SCC and both edges stay.
    assert ("m.py::A", "m.py::B") in _reduced(g, "calls")
    assert ("m.py::B", "m.py::A") in _reduced(g, "calls")


def test_unresolved_reference_produces_no_edge():
    g = build_entity_graph({"m.py": "def f():\n    return undefined_thing()\n"})
    assert _edges(g, "calls") == set()


def test_two_independent_clusters_are_two_components():
    cb = {
        "a.py": "def a1():\n    return a2()\ndef a2():\n    return 1\n",
        "b.py": "def b1():\n    return b2()\ndef b2():\n    return 1\n",
    }
    g = build_entity_graph(cb)
    assert len(g.components) == 2


def test_entity_graph_view_shape_is_stable(tmp_path):
    (tmp_path / "m.py").write_text(
        "def callee():\n    return 1\ndef caller():\n    return callee()\n",
        encoding="utf-8",
    )
    project = SimpleNamespace(repo=tmp_path)
    v1 = entity_graph_view(project)
    v2 = entity_graph_view(project)
    assert v1 == v2  # deterministic
    assert set(v1) == {"entities", "edges", "reduced_edges", "components", "clusters", "count"}
    assert v1["count"] == 2
    assert v1["clusters"] == []  # bare repo: no owned features, so no capability clusters
    caller = next(e for e in v1["entities"] if e["name"] == "caller")
    assert "m.py::callee" in caller["depends_on"]
    # Bare repo has no effect log, so every entity is unowned (dim).
    assert "node_id" in caller and caller["node_id"] is None


def test_owning_nodes_plurality_and_unowned():
    ent = Entity(
        id="m.py::f", name="f", file="m.py", kind="function",
        start_line=1, end_line=10, container=None,
    )
    # A owns 7 lines, B owns 3 -> A wins.
    spans = {"m.py": [
        {"start": 1, "end": 7, "node_id": "A"},
        {"start": 8, "end": 10, "node_id": "B"},
    ]}
    assert owning_nodes([ent], spans) == {"m.py::f": "A"}
    # No blame for the file (untracked / TS) -> None.
    assert owning_nodes([ent], {})["m.py::f"] is None
    # Only unattributed lines -> None.
    assert owning_nodes([ent], {"m.py": [{"start": 1, "end": 10, "node_id": None}]})["m.py::f"] is None
