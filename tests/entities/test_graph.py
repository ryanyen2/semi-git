"""U2 — entity graph assembly."""

from __future__ import annotations

from ugh.entities.graph import build_entity_graph


def _edges(g, type_=None):
    return {(e.src, e.dst) for e in g.edges if type_ is None or e.type == type_}


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


def test_unresolved_reference_produces_no_edge():
    g = build_entity_graph({"m.py": "def f():\n    return undefined_thing()\n"})
    assert _edges(g, "calls") == set()
