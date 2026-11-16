"""Tests for ugh.lens.verbs -- `feature rename` (plan U13, R16) over the feature tree, plus
`revert <feature>`'s bridge into the kernel's ideal-edit algebra."""

from __future__ import annotations

from ugh import api
from ugh.core import order
from ugh.core.lens import get
from ugh.core.store import Store
from ugh.lens import map as lensmap
from ugh.lens import tree, verbs
from ugh.lens.pins import Pins, load_pins, save_pins
from tests.laws import corpus


# -- authored-id handle ------------------------------------------------------------------------


def test_authored_id_for_is_idempotent_on_an_already_authored_id():
    # A *cluster* feature id gets the deterministic af- handle so a reorg verb updates one feature.
    assert verbs._authored_id_for("N42") == "af-N42"
    # But an id that is ALREADY an authored feature (af-<uuid>) is its own authored id. Wrapping it a
    # second time (af-af-<uuid>) mints a phantom lane no pin/tree references: `ledger.assign_at_save`
    # pins the symbol to `af-<uuid>` but adds the member under `af-af-<uuid>`, so the assign pin and
    # the carried-across-sync CRDT permanently disagree. The handle must be idempotent.
    aid = "af-b1f33996a55e480aae1e9f4b1a9b812b"
    assert verbs._authored_id_for(aid) == aid


# -- resolve + revert --------------------------------------------------------------------------


def test_resolve_feature_matches_by_id_and_by_label(tmp_path):
    repo = corpus.CORPUS["mixed_coverage"].build(tmp_path / "repo")
    get(repo)
    result = lensmap.build_map(repo)
    fid = next(iter(result["nodes"]))
    label = result["nodes"][fid]["label"]

    by_id = verbs.resolve_feature(repo, fid)
    by_label = verbs.resolve_feature(repo, label)
    assert by_id is not None and by_label is not None
    assert by_id[1] == by_label[1] == fid
    assert verbs.resolve_feature(repo, "no-such-feature") is None


def test_plan_revert_feature_removes_the_grouped_upset(tmp_path):
    repo = corpus.CORPUS["mixed_coverage"].build(tmp_path / "repo")
    ideal = get(repo)
    ops = Store(repo).all_ops()
    result = lensmap.build_map(repo)
    fid = next(iter(result["nodes"]))

    preview = verbs.plan_revert_feature(repo, fid)
    assert preview.ok
    assert preview.verb == "revert"
    assert preview.target == fid

    expected_removed: set[str] = set()
    for op_id, leaf in result["op_leaf"].items():
        if leaf == fid:
            expected_removed |= order.upset_in(op_id, ideal.op_ids, ops)
    assert preview.removed == frozenset(expected_removed)
    assert order.is_valid_ideal(ops, preview.after_ids)


def test_plan_revert_feature_refuses_on_an_unresolvable_ref(tmp_path):
    repo = corpus.CORPUS["mixed_coverage"].build(tmp_path / "repo")
    get(repo)
    lensmap.build_map(repo)

    preview = verbs.plan_revert_feature(repo, "no-such-feature")
    assert not preview.ok
    assert preview.after_ids == preview.before_ids


# -- labels pin ---------------------------------------------------------------------------------


def test_labels_pin_round_trips_and_overrides_the_fallback_label(tmp_path):
    repo = corpus.CORPUS["linear_history"].build(tmp_path / "repo")
    ideal = get(repo)
    ops = Store(repo).all_ops()
    result = tree.build(repo, ops, ideal)
    fid = next(iter(result["nodes"]))

    save_pins(repo, Pins(labels={fid: "Hand-Picked Label"}))
    reloaded = load_pins(repo)
    assert reloaded.labels == {fid: "Hand-Picked Label"}

    tree.label_tree(result, repo, pins=reloaded)
    assert result["nodes"][fid]["label"] == "Hand-Picked Label"


# -- feature_verb_preview_view (the feature-map webview's hover-preview primitive) -------------


def test_rename_survives_a_build_map_recluster(tmp_path):
    repo = corpus.CORPUS["linear_history"].build(tmp_path / "repo")
    get(repo)
    result = lensmap.build_map(repo)
    fid = next(iter(result["nodes"]))

    preview = verbs.plan_rename(repo, fid, "Renamed Across Reclusters")
    assert preview.ok
    verbs.apply_rename(repo, preview)

    reclustered = lensmap.build_map(repo)  # nothing changed store-side -- Greene keeps the id
    assert fid in reclustered["nodes"]
    assert reclustered["nodes"][fid]["label"] == "Renamed Across Reclusters"


def test_rename_resolves_the_short_id_prefix_the_ui_prints(tmp_path):
    """The tree/map/save-hint print an abbreviated feature id (e.g. `1a2131ff`), and `ugh revert`
    resolves it via `resolve_feature`'s prefix match. `plan_rename` must accept the same handle --
    otherwise the very id the tool prints reads as 'not found' under rename's exact-key lookup."""
    repo = corpus.CORPUS["linear_history"].build(tmp_path / "repo")
    get(repo)
    result = lensmap.build_map(repo)
    fid = next(iter(result["nodes"]))
    assert fid.startswith("f-")
    short = fid[len("f-"):][:8]  # the bare hex the graph prints

    preview = verbs.plan_rename(repo, short, "Named By Short Handle")
    assert preview.ok, preview.message
    assert preview.feature_id == fid  # canonicalized back to the full id
    verbs.apply_rename(repo, preview)
    assert lensmap.build_map(repo)["nodes"][fid]["label"] == "Named By Short Handle"


# -- U7: authored feature overlays (clustering demoted to a seed) ------------------------------


def test_map_view_authored_label_overrides_the_cluster_leaf(tmp_path):
    """R3 authority inversion: where a user has authored a feature over a leaf's symbols, `ugh map`
    (map_view) shows that authored feature's label/id, not the clustered one; un-authored leaves
    keep their cluster labels untouched."""
    from ugh.lens import authored

    repo = corpus.CORPUS["mixed_coverage"].build(tmp_path / "repo")
    get(repo)
    result = lensmap.build_map(repo)

    before = api.map_view(repo)  # no authored features yet -> pure cluster projection
    leaf_rows = [r for r in before["nodes"] if r["kind"] == "feature"]
    assert leaf_rows
    for r in leaf_rows:
        assert r.get("authored_id") is None
        assert r["label"] == result["nodes"][r["id"]]["label"]

    target = leaf_rows[0]
    members = result["nodes"][target["id"]]["members"]
    feat = authored.create(members, "Authored Wins")
    authored.save_authored(repo, {feat.id: feat})

    after = api.map_view(repo)
    trow = next(r for r in after["nodes"] if r["id"] == target["id"])
    assert trow["label"] == "Authored Wins"  # authored label wins over the cluster proposal
    assert trow["authored_id"] == feat.id
    for r in after["nodes"]:  # every un-authored leaf keeps its cluster label
        if r["kind"] == "feature" and r["id"] != target["id"]:
            assert r.get("authored_id") is None
            assert r["label"] == result["nodes"][r["id"]]["label"]


def test_rename_also_writes_an_authored_feature(tmp_path):
    """A reorg verb writes an authored-feature op *in addition to* its pin (R3): renaming a cluster
    feature is an authoring act, recorded as first-class merged state, while the labels pin that
    keeps the rename stable across a recluster is still written."""
    from ugh.lens import authored

    repo = corpus.CORPUS["mixed_coverage"].build(tmp_path / "repo")
    get(repo)
    result = lensmap.build_map(repo)
    fid = next(iter(result["nodes"]))

    verbs.apply_rename(repo, verbs.plan_rename(repo, fid, "Named By User"))

    af = authored.load_authored(repo)
    feat = af[f"af-{fid}"]
    assert feat.label == "Named By User"
    assert feat.live_members() == frozenset(result["nodes"][fid]["members"])
    assert load_pins(repo).labels[fid] == "Named By User"  # the pin is still written
