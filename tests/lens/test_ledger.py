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

def _FS(pair):  # canonical (sorted-tuple) edge key -- see cluster.edge_key
    return tuple(sorted(pair))


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
    # The authored label register starts EMPTY -- a guessed name must not shadow the label a rebuild
    # computes (`tree.label_tree` only lets a non-empty authored label override). The provisional name
    # is only the node's display label for the pre-rebuild grid window, and it names the *symbol*:
    # labelling the lane "island.py" made two lanes from one save indistinguishable in `log` and made
    # `sgt show` answer "what is this feature" with a file path.
    assert authored.load_authored(tmp_path)[lane].label == ""
    assert persisted["nodes"][lane]["label"] == "omega"


def test_two_new_lanes_from_one_save_get_distinguishable_provisional_labels(tmp_path):
    """One save that seeds two lanes must not label them the same thing. Labelling a lane after its
    file did exactly that whenever the new symbols shared a file, and `sgt log` then printed two rows
    a participant could not tell apart -- the observed shape of it was a save reporting two `new
    feature` lanes, one shown as "coursecraft/cli.py" and the other as "coursecraft/enrollment.py",
    neither carrying the words the user had just typed."""
    gb, _ = _build_and_map(tmp_path)
    (tmp_path / "island.py").write_text(
        "def omega():\n    return 42\n\n\ndef psi():\n    return 43\n", encoding="utf-8")
    ideal = get(tmp_path)
    ops = Store(tmp_path).all_ops()

    summary = ledger.assign_at_save(tmp_path, ideal, ops)
    lanes = {summary["assigned"][s] for s in ("island.py::omega", "island.py::psi")}
    nodes = tree.load(tmp_path)["nodes"]
    labels = [nodes[l]["label"] for l in lanes]
    assert len(set(labels)) == len(labels), f"provisional labels collide: {labels}"


def test_assign_at_save_leaves_the_joined_lanes_authored_label_empty(tmp_path):
    """The existing-lane branch mirrors the new-lane fallback (above): a save-time cascade records
    *membership*, never a *name*. Seeding the authored label register from the leaf's clustered/
    provisional node label (an LLM/fallback name, or a guessed file path for a provisional lane)
    would permanently shadow every future rebuild's label -- `tree.label_tree` lets any non-empty
    authored label override the clustered proposal. So a first-time cross-lane assign must leave the
    register EMPTY; only a deliberate `sgt rename` fills it."""
    gb, result = _build_and_map(tmp_path)
    ideal, ops = _add_delta(tmp_path)

    summary = ledger.assign_at_save(tmp_path, ideal, ops)
    lane = summary["assigned"]["core.py::delta"]
    assert not summary["new_lanes"]  # delta coupled into an existing clustered lane
    aid = f"af-{lane}"
    af = authored.load_authored(tmp_path)
    assert "core.py::delta" in af[aid].live_members()  # membership IS recorded
    assert af[aid].label == ""  # ...but the name is not -- the rebuild's label must stand


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


def test_new_lane_id_is_content_addressed_not_random():
    """A new-lane fallback id is a pure function of the symbol -- NOT `uuid4`. This is the root of the
    ledger's own guarantee (module docstring): identical content -> byte-identical assignment. A
    random id broke it -- the same disconnected symbol saved twice seeded two different lanes, and
    every rebuild in between saw a fresh competing assign pin, the churn `_apply_assign_pins`
    oscillated over."""
    assert ledger._new_lane_id("island.py::omega") == ledger._new_lane_id("island.py::omega")
    assert ledger._new_lane_id("a.py::f") != ledger._new_lane_id("b.py::f")
    assert ledger._new_lane_id("island.py::omega").startswith("af-m")


def test_new_lane_id_is_stable_across_two_independent_identical_saves(tmp_path):
    """End-to-end: two independent repos with byte-identical history seed the SAME `af-` lane id for
    the same disconnected symbol. Previously the `uuid4` in `authored.create` made every save mint a
    different lane -- the source of the id churn -- so this asserts the fix at the wiring level, not
    just the helper."""
    def seed(root):
        _build_and_map(root)
        (root / "island.py").write_text("def omega():\n    return 42\n", encoding="utf-8")
        ideal = get(root)
        return ledger.assign_at_save(root, ideal, Store(root).all_ops())["assigned"]["island.py::omega"]

    assert seed(tmp_path / "clone_a") == seed(tmp_path / "clone_b")


def test_new_lane_fallback_reuses_a_surviving_authored_record(tmp_path):
    """Re-entry of a previously-minted symbol (deleted, then re-added while still disconnected):
    the content-addressed id collides with the surviving register record BY DESIGN, so the mint
    must reuse that record -- mirroring the attach path's `if aid not in af` guard. Overwriting
    instead resets the CRDT clock and silently drops the label and every member added since (a
    peer's `sgt move`, a `rename`), and sync then sees a rewrite, not a mergeable update."""
    from dataclasses import replace

    _build_and_map(tmp_path)
    symbol = "island.py::omega"
    lane_id = ledger._new_lane_id(symbol)
    prior = replace(authored.create(["island.py::extra"], "My Lane"), id=lane_id)
    authored.save_authored(tmp_path, {lane_id: prior})

    (tmp_path / "island.py").write_text("def omega():\n    return 42\n", encoding="utf-8")
    ideal = get(tmp_path)
    out = ledger.assign_at_save(tmp_path, ideal, Store(tmp_path).all_ops())

    assert out["assigned"][symbol] == lane_id
    af = authored.load_authored(tmp_path)
    assert af[lane_id].label == "My Lane"                    # survived the re-mint
    assert "island.py::extra" in af[lane_id].live_members()  # previously-added member survived
    assert symbol in af[lane_id].live_members()              # the re-entering symbol was added
