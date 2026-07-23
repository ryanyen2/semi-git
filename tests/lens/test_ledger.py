"""The save-time ownership ledger's local-move core (plan U5) and its save-time wiring (U6).

`local_move_assign` runs leidenalg's own local-moving phase over a bounded boundary (the new
symbols + their 1-hop owned, non-hub neighbours), with every owned neighbour frozen, so only the
new symbols move. These tests pin the cascade's algorithmic behaviour and, above all, its
determinism -- a save-time assignment must produce identical lanes for identical content.

The `assign_at_save` block (U6) pins the wiring: a genuinely-new symbol lands a durable lane
(assign pin + authored CRDT) at save time, is visible in the persisted `op_leaf` grid_view reads
without a map rebuild, and -- the crux -- survives a full recluster in that same lane.
"""

from __future__ import annotations

from sgt.core.lens import get
from sgt.core.store import Store
from sgt.lens import authored, ledger, tree
from sgt.lens import map as lensmap
from sgt.lens.pins import load_pins
from sgt.store.gitbind import init_store

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


# -- U6: assign_at_save (the save-time wiring) -------------------------------------------------


def _build_and_map(repo):
    """A two-file repo, mined + mapped so a persisted tree exists for the cascade to build on."""
    gb, _ = init_store(repo)
    (repo / "core.py").write_text(
        "def alpha():\n    return 1\n\n\ndef beta():\n    return alpha() + 1\n", encoding="utf-8")
    gb.commit_all("core: alpha, beta")
    (repo / "util.py").write_text("def gamma():\n    return 2\n", encoding="utf-8")
    gb.commit_all("util: gamma")
    get(repo)
    return gb, lensmap.build_map(repo)


def _add_delta(repo):
    """Add `delta` to core.py calling owned symbols -- a genuinely-new symbol that couples in."""
    (repo / "core.py").write_text(
        "def alpha():\n    return 1\n\n\ndef beta():\n    return alpha() + 1\n\n\n"
        "def delta():\n    return alpha() + beta()\n", encoding="utf-8")
    ideal = get(repo)
    return ideal, Store(repo).all_ops()


def test_assign_at_save_is_a_noop_without_a_previous_tree(tmp_path):
    """No tree built yet -> the first full build owns the initial clustering; the cascade defers."""
    gb, _ = init_store(tmp_path)
    (tmp_path / "core.py").write_text("def alpha():\n    return 1\n", encoding="utf-8")
    gb.commit_all("core")
    ideal = get(tmp_path)
    assert ledger.assign_at_save(tmp_path, ideal, Store(tmp_path).all_ops()) is None


def test_assign_at_save_is_a_noop_when_only_owned_symbols_change(tmp_path):
    """A save that only edits already-owned symbols assigns nothing -- the common modify-only case
    pays no cascade cost (chain-continuation is a free dict lookup)."""
    gb, _ = _build_and_map(tmp_path)
    (tmp_path / "core.py").write_text(
        "def alpha():\n    return 99\n\n\ndef beta():\n    return alpha() + 2\n", encoding="utf-8")
    ideal = get(tmp_path)
    assert ledger.assign_at_save(tmp_path, ideal, Store(tmp_path).all_ops()) == {
        "assigned": {}, "new_lanes": []}


def test_assign_at_save_attaches_a_new_symbol_durably_and_visibly(tmp_path):
    """A new symbol coupling into an owned lane attaches to it: an assign pin + an authored member
    are written, and the new op is in the persisted `op_leaf` (grid-visible) with no rebuild."""
    gb, result = _build_and_map(tmp_path)
    owned_lanes = {nid for nid, nd in result["nodes"].items() if not nd["children"]}
    ideal, ops = _add_delta(tmp_path)

    summary = ledger.assign_at_save(tmp_path, ideal, ops)
    new_sym = "core.py::delta"
    assert new_sym in summary["assigned"]
    lane = summary["assigned"][new_sym]

    assert load_pins(tmp_path).assign[new_sym] == lane  # durable local pin
    af = authored.load_authored(tmp_path)
    aid = lane if lane in summary["new_lanes"] else f"af-{lane}"
    assert new_sym in af[aid].live_members()  # authored CRDT membership
    new_op = ideal.frontier(ops)[new_sym]
    assert tree.load(tmp_path)["op_leaf"][new_op] == lane  # grid-visible immediately
    # delta coupled to core.py's owned symbols, so it joined an existing lane, not a new one
    assert lane in owned_lanes and not summary["new_lanes"]


def test_ledger_assignment_survives_a_full_recluster(tmp_path):
    """THE crux (R2): once the cascade pins a new symbol to a lane, a from-scratch `force_rebuild`
    recluster keeps it there -- lanes are stable, never silently re-derived elsewhere."""
    gb, _ = _build_and_map(tmp_path)
    ideal, ops = _add_delta(tmp_path)
    lane = ledger.assign_at_save(tmp_path, ideal, ops)["assigned"]["core.py::delta"]

    rebuilt = tree.build(tmp_path, ops, ideal, force_rebuild=True)
    assert tree.leaf_member_index(rebuilt["nodes"])["core.py::delta"] == lane


def test_assign_at_save_seeds_a_new_lane_for_a_disconnected_symbol(tmp_path):
    """A new symbol in a brand-new file coupling to nothing owned seeds a fresh `af-` lane: a real
    leaf node holding it, an assign pin, an authored feature, and grid visibility."""
    gb, _ = _build_and_map(tmp_path)
    (tmp_path / "island.py").write_text("def omega():\n    return 42\n", encoding="utf-8")
    ideal = get(tmp_path)
    ops = Store(tmp_path).all_ops()

    summary = ledger.assign_at_save(tmp_path, ideal, ops)
    new_sym = "island.py::omega"
    lane = summary["assigned"][new_sym]
    assert lane in summary["new_lanes"] and lane.startswith("af-")

    persisted = tree.load(tmp_path)
    assert persisted["nodes"][lane]["members"] == [new_sym]  # a real leaf node
    assert load_pins(tmp_path).assign[new_sym] == lane
    assert new_sym in authored.load_authored(tmp_path)[lane].live_members()
    assert persisted["op_leaf"][ideal.frontier(ops)[new_sym]] == lane


def test_assign_at_save_is_idempotent(tmp_path):
    """Re-running the cascade never duplicates: after the first save patched the symbol into its
    lane, it is owned, so a second call is a no-op and the durable state is byte-stable."""
    gb, _ = _build_and_map(tmp_path)
    ideal, ops = _add_delta(tmp_path)

    first = ledger.assign_at_save(tmp_path, ideal, ops)
    assert first["assigned"]  # delta was assigned
    af1, pins1 = authored.load_authored(tmp_path), load_pins(tmp_path).assign

    second = ledger.assign_at_save(tmp_path, ideal, ops)
    assert second == {"assigned": {}, "new_lanes": []}  # delta now owned -> nothing new
    assert authored.load_authored(tmp_path) == af1  # no fresh tags, no new lanes
    assert load_pins(tmp_path).assign == pins1


def test_dual_claims_detects_a_symbol_live_in_two_features():
    """Pure detection: a symbol live in >1 authored feature (the cross-clone dual-lane case sync
    surfaces as a conflict) is reported; a single-claim symbol is not."""
    a = authored.create(["s.py::shared", "s.py::a_only"], "Lane A")
    b = authored.create(["s.py::shared", "s.py::b_only"], "Lane B")
    claims = ledger.dual_claims({a.id: a, b.id: b})
    assert claims == [("s.py::shared", sorted([a.id, b.id]))]
