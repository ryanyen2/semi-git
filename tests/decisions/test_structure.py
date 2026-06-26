"""The deterministic structural summary — defines/uses/used_by from the entity call graph."""

from sgt.decisions.structure import decision_structure, resolve_footprint, structure_phrase


class _Node:
    def __init__(self, provides=(), needs=()):
        self.provides = list(provides)
        self.needs = list(needs)


# m.py: user() calls base(); helper() calls user(). So the entity graph carries those edges.
_EG = {
    "entities": [
        {"id": "m.py::base", "name": "base", "depends_on": []},
        {"id": "m.py::user", "name": "user", "depends_on": ["m.py::base"]},
        {"id": "m.py::helper", "name": "helper", "depends_on": ["m.py::user"]},
    ]
}
_IDS = {e["id"] for e in _EG["entities"]}


def test_landed_structure_reports_defines_uses_used_by():
    owned = resolve_footprint(["m.py::user"], _IDS)
    s = decision_structure(_Node(), _EG, owned)
    assert s == {"defines": ["user"], "uses": ["base"], "used_by": ["helper"]}


def test_self_internal_edges_do_not_leak_into_uses_or_used_by():
    # A decision owning both user and base: base is internal, so it's neither a `use` nor a dependent.
    owned = resolve_footprint(["m.py::user", "m.py::base"], _IDS)
    s = decision_structure(_Node(), _EG, owned)
    assert s["defines"] == ["base", "user"]
    assert "base" not in s["uses"]          # base is owned, not an external use
    assert s["used_by"] == ["helper"]


def test_planned_falls_back_to_provides_needs():
    # No footprint resolves (empty owned) -> describe from the planner's declared provides/needs.
    s = decision_structure(_Node(provides=["retrieve_from_graph"], needs=["query_kg"]), _EG, set())
    assert s == {"defines": ["retrieve_from_graph"], "uses": ["query_kg"], "used_by": []}


def test_structure_phrase_is_a_readable_line():
    s = {"defines": ["Bm25Index", "query"], "uses": ["tokenize"], "used_by": ["search"]}
    assert structure_phrase(s) == "Defines Bm25Index, query · uses tokenize · used by search"
    assert structure_phrase({"defines": [], "uses": [], "used_by": []}) == ""
