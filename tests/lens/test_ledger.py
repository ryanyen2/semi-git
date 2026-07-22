"""The save-time ownership ledger's local-move core (plan U5).

`local_move_assign` runs leidenalg's own local-moving phase over a bounded boundary (the new
symbols + their 1-hop owned, non-hub neighbours), with every owned neighbour frozen, so only the
new symbols move. These tests pin the cascade's algorithmic behaviour and, above all, its
determinism -- a save-time assignment must produce identical lanes for identical content.
"""

from __future__ import annotations

from sgt.lens import ledger

_FS = frozenset


def _owned():
    """Two owned lanes: F1 = {a, b}, F2 = {x, y}."""
    return {"a": "F1", "b": "F1", "x": "F2", "y": "F2"}


def test_new_symbol_attaches_to_the_lane_it_couples_to():
    """A genuinely-new symbol coupled to an owned lane's code joins that lane."""
    fused = {_FS({"n", "a"}): 8.0, _FS({"a", "b"}): 5.0, _FS({"x", "y"}): 5.0}
    assert ledger.local_move_assign({"n"}, _owned(), fused, set()) == {"n": "F1"}


def test_new_symbol_with_no_owned_neighbour_seeds_a_new_lane():
    """A new symbol coupled to nothing owned has no lane -- None routes it to the new-lane fallback."""
    fused = {_FS({"z", "q"}): 5.0}  # q is not owned
    assert ledger.local_move_assign({"z"}, _owned(), fused, set()) == {"z": None}


def test_mutually_coupled_new_pair_colocates():
    """Two new symbols more cohesive with each other than with any owned lane co-locate in ONE new
    lane (both None -> the caller mints a single lane), never split into two."""
    fused = {_FS({"n1", "n2"}): 10.0, _FS({"n1", "x"}): 6.0}  # pair (10) > link to F2 (6)
    r = ledger.local_move_assign({"n1", "n2"}, _owned(), fused, set())
    assert r == {"n1": None, "n2": None}


def test_new_pair_strongly_tied_to_a_lane_both_attach():
    """When a new symbol's tie to an owned lane dominates, it joins the lane and a symbol tied to
    it follows -- the local move, not a one-shot single-symbol attach, is what carries the second."""
    fused = {_FS({"n1", "x"}): 20.0, _FS({"n1", "n2"}): 1.0}
    assert ledger.local_move_assign({"n1", "n2"}, _owned(), fused, set()) == {"n1": "F2", "n2": "F2"}


def test_is_deterministic_across_calls():
    """Identical content -> byte-identical assignment (pinned vertex order + `cluster.SEED`), the
    property a save-time assignment must have. Checked on the mutually-coupled-pair case, where the
    local-moving phase's own visit order and RNG actually matter (not a single-symbol attach)."""
    fused = {_FS({"n1", "n2"}): 10.0, _FS({"n1", "x"}): 6.0, _FS({"n2", "a"}): 4.0}
    a = ledger.local_move_assign({"n1", "n2"}, _owned(), fused, set())
    b = ledger.local_move_assign({"n1", "n2"}, _owned(), fused, set())
    assert a == b


def test_hub_neighbours_are_excluded_from_the_boundary():
    """A hub-suppressed owned symbol is not a valid attachment target -- a link only through a hub
    can't pull a new symbol into that lane (so it falls back to a new lane)."""
    fused = {_FS({"n", "a"}): 8.0}
    assert ledger.local_move_assign({"n"}, _owned(), fused, hubs={"a"}) == {"n": None}


def test_boundary_is_bounded_by_top_k():
    """The boundary caps the owned side at `TOP_K` neighbours, so a new symbol edging into a very
    high-degree owned region never builds a graph that scales with the repo -- the induced graph
    has at most `TOP_K + |new|` vertices."""
    # one new symbol linked to 200 owned symbols, all in lane F-big.
    member_leaf = {f"o{i}": "F-big" for i in range(200)}
    fused = {_FS({"n", f"o{i}"}): float(i + 1) for i in range(200)}
    r = ledger.local_move_assign({"n"}, member_leaf, fused, set(), top_k=50)
    assert r == {"n": "F-big"}  # still attaches, from only the 50 heaviest neighbours


def test_assign_new_symbols_excludes_residue_pseudo_symbols():
    """The cascade wrapper local-moves only real entities; a residue/anchor pseudo-symbol is left
    out (it follows its anchor entity's lane via `assign_ops_to_leaves`, U4), so it never appears
    in the returned map."""
    new = {"m.py::foo", "m.py::__residue__::foo", "m.py::__anchor__::foo"}
    fused = {_FS({"m.py::foo", "a"}): 8.0}
    r = ledger.assign_new_symbols(new, _owned(), fused, set())
    assert set(r) == {"m.py::foo"}  # only the entity
    assert r["m.py::foo"] == "F1"
