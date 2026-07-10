"""Tests for sgt.lens.verbs -- the feature verbs (plan U13, R16): merge/split/rename/move over
the feature tree, plus `revert <feature>`'s bridge into the kernel's ideal-edit algebra.

Each verb is a metadata-only patch of `tree.json` + one durable pin; the byte-neutrality property
below is the R16 guarantee that `code(I)` never moves -- these verbs touch no op, ever.
"""

from __future__ import annotations

import pytest

from sgt import api
from sgt.core import order
from sgt.core.fold import code
from sgt.core.lens import current_ideal, get
from sgt.core.store import Store
from sgt.lens import map as lensmap
from sgt.lens import tree, verbs
from sgt.lens.pins import Pins, load_pins, save_pins
from tests.laws import corpus


def _split_into_two(repo):
    """Force a fixture's single leaf into two, so merge/move have something to operate on."""
    result = lensmap.build_map(repo)
    fid = next(iter(result["nodes"]))
    preview = verbs.plan_split(repo, fid)
    assert preview.ok, preview.message
    return verbs.apply_split(repo, preview, confirm=True)


# -- split ------------------------------------------------------------------------------------


def test_split_preview_mutates_nothing_until_confirmed(tmp_path):
    repo = corpus.CORPUS["mixed_coverage"].build(tmp_path / "repo")
    get(repo)
    before = lensmap.build_map(repo)
    fid = next(iter(before["nodes"]))

    preview = verbs.plan_split(repo, fid)
    assert preview.ok
    assert preview.groups is not None and len(preview.groups) == 2
    assert tree.load(repo) == before  # plan_* is pure; nothing written yet

    with pytest.raises(verbs.VerbError):
        verbs.apply_split(repo, preview)  # confirm defaults to False -- refused, not a silent no-op
    assert tree.load(repo) == before

    after = verbs.apply_split(repo, preview, confirm=True)
    leaves = sorted(nid for nid, nd in after["nodes"].items() if not nd["children"])
    assert len(leaves) == 2
    assert fid in leaves  # the "keep" half reuses the original id
    all_members = sorted(m for nid in leaves for m in after["nodes"][nid]["members"])
    assert all_members == sorted(m for g in preview.groups for m in g)


# -- merge ------------------------------------------------------------------------------------


def test_merge_unions_op_sets_and_keeps_the_survivor_id(tmp_path):
    repo = corpus.CORPUS["mixed_coverage"].build(tmp_path / "repo")
    get(repo)
    split_result = _split_into_two(repo)
    survivor, absorbed = sorted(nid for nid, nd in split_result["nodes"].items() if not nd["children"])
    survivor_ops = {op for op, leaf in split_result["op_leaf"].items() if leaf == survivor}
    absorbed_ops = {op for op, leaf in split_result["op_leaf"].items() if leaf == absorbed}
    assert absorbed_ops  # the split must have actually moved at least one op

    preview = verbs.plan_merge(repo, survivor, absorbed)
    assert preview.ok
    assert preview.op_count == len(survivor_ops | absorbed_ops)

    merged = verbs.apply_merge(repo, preview)
    assert survivor in merged["nodes"]
    assert absorbed not in merged["nodes"]
    assert {op for op, leaf in merged["op_leaf"].items() if leaf == survivor} == survivor_ops | absorbed_ops


def test_merge_refuses_a_feature_into_itself_or_an_unknown_id(tmp_path):
    repo = corpus.CORPUS["mixed_coverage"].build(tmp_path / "repo")
    get(repo)
    result = lensmap.build_map(repo)
    fid = next(iter(result["nodes"]))

    assert not verbs.plan_merge(repo, fid, fid).ok
    assert not verbs.plan_merge(repo, fid, "F999").ok


# -- move -------------------------------------------------------------------------------------


def test_move_retags_op_leaf_and_writes_the_assign_pin(tmp_path):
    repo = corpus.CORPUS["mixed_coverage"].build(tmp_path / "repo")
    get(repo)
    split_result = _split_into_two(repo)
    source, target = sorted(nid for nid, nd in split_result["nodes"].items() if not nd["children"])
    op_refs = [op for op, leaf in split_result["op_leaf"].items() if leaf == source][:1]
    assert op_refs

    preview = verbs.plan_move(repo, op_refs, target)
    assert preview.ok
    result = verbs.apply_move(repo, preview)
    assert all(result["op_leaf"][op_id] == target for op_id in op_refs)

    ops_by_id = {op.id: op for op in Store(repo).all_ops()}
    moved_symbols = {sym for op_id in op_refs for sym in ops_by_id[op_id].footprint}
    member_leaf = {m: nid for nid, nd in result["nodes"].items() if not nd["children"] for m in nd["members"]}
    pins = load_pins(repo)
    for sym in moved_symbols:
        if sym in member_leaf:  # a footprint symbol not tracked as a member has nothing to pin
            assert pins.assign[sym] == target
            assert member_leaf[sym] == target


def test_move_refuses_an_unresolvable_op_ref(tmp_path):
    repo = corpus.CORPUS["mixed_coverage"].build(tmp_path / "repo")
    get(repo)
    result = lensmap.build_map(repo)
    fid = next(iter(result["nodes"]))

    preview = verbs.plan_move(repo, ["not-a-real-op-id"], fid)
    assert not preview.ok


# -- byte-neutrality (R16) ---------------------------------------------------------------------


def test_feature_verbs_never_change_materialized_bytes(tmp_path):
    repo = corpus.CORPUS["mixed_coverage"].build(tmp_path / "repo")
    ideal = get(repo)
    ops = Store(repo).all_ops()
    before = code(ideal, ops)

    def current_bytes() -> dict[str, bytes]:
        return code(current_ideal(repo), Store(repo).all_ops())

    result = lensmap.build_map(repo)
    assert current_bytes() == before

    fid = next(iter(result["nodes"]))
    rename_preview = verbs.plan_rename(repo, fid, "Renamed Feature")
    verbs.apply_rename(repo, rename_preview)
    assert current_bytes() == before

    split_preview = verbs.plan_split(repo, fid)
    split_result = verbs.apply_split(repo, split_preview, confirm=True)
    assert current_bytes() == before

    source, target = sorted(nid for nid, nd in split_result["nodes"].items() if not nd["children"])
    op_ref = next(op for op, leaf in split_result["op_leaf"].items() if leaf == source)
    move_preview = verbs.plan_move(repo, [op_ref], target)
    verbs.apply_move(repo, move_preview)
    assert current_bytes() == before

    merge_preview = verbs.plan_merge(repo, target, source)
    verbs.apply_merge(repo, merge_preview)
    assert current_bytes() == before


# -- blame ------------------------------------------------------------------------------------


def test_blame_returns_the_feature_of_the_maximal_in_ideal_op(tmp_path):
    repo = corpus.CORPUS["mixed_coverage"].build(tmp_path / "repo")
    ideal = get(repo)
    ops = Store(repo).all_ops()
    result = lensmap.build_map(repo)

    tip = ideal.frontier(ops)["pkg.py::compute"]
    expected_feature = result["op_leaf"][tip]

    view = api.blame_view(repo, "pkg.py")
    span = next(s for s in view["spans"] if s["symbol"] == "pkg.py::compute")
    assert span["feature_id"] == expected_feature
    assert span["label"] == result["nodes"][expected_feature]["label"]


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


def test_feature_verb_preview_view_merge_reports_both_features_as_affected(tmp_path):
    repo = corpus.CORPUS["mixed_coverage"].build(tmp_path / "repo")
    get(repo)
    split_result = _split_into_two(repo)
    survivor, absorbed = sorted(nid for nid, nd in split_result["nodes"].items() if not nd["children"])

    preview = api.feature_verb_preview_view(repo, "merge", survivor, absorbed)
    assert preview["ok"]
    assert preview["affected_features"] == [survivor, absorbed]
    assert tree.load(repo) == split_result  # pure -- nothing written by a preview


def test_feature_verb_preview_view_rename_reports_the_one_feature_affected(tmp_path):
    repo = corpus.CORPUS["mixed_coverage"].build(tmp_path / "repo")
    get(repo)
    result = lensmap.build_map(repo)
    fid = next(iter(result["nodes"]))

    preview = api.feature_verb_preview_view(repo, "rename", fid, "New Label")
    assert preview["ok"]
    assert preview["new_label"] == "New Label"
    assert preview["affected_features"] == [fid]
    assert tree.load(repo)["nodes"][fid]["label"] != "New Label"  # not applied


def test_feature_verb_preview_view_split_previews_the_fresh_id_split_would_mint(tmp_path):
    repo = corpus.CORPUS["mixed_coverage"].build(tmp_path / "repo")
    get(repo)
    before = lensmap.build_map(repo)
    fid = next(iter(before["nodes"]))

    preview = api.feature_verb_preview_view(repo, "split", fid)
    assert preview["ok"]
    assert preview["feature_id"] == fid
    assert tree.load(repo) == before  # pure -- nothing written by a preview

    applied = verbs.apply_split(repo, verbs.plan_split(repo, fid), confirm=True)
    new_id = next(nid for nid in applied["nodes"] if nid not in before["nodes"])
    assert preview["affected_features"] == [fid, new_id]  # exactly what apply_split just minted


def test_feature_verb_preview_view_move_reports_source_and_target_affected(tmp_path):
    repo = corpus.CORPUS["mixed_coverage"].build(tmp_path / "repo")
    get(repo)
    split_result = _split_into_two(repo)
    source, target = sorted(nid for nid, nd in split_result["nodes"].items() if not nd["children"])
    op_ref = next(op for op, leaf in split_result["op_leaf"].items() if leaf == source)

    preview = api.feature_verb_preview_view(repo, "move", op_ref, target)
    assert preview["ok"]
    assert preview["affected_features"] == sorted([source, target])


def test_feature_verb_preview_view_revert_reports_every_affected_feature(tmp_path):
    repo = corpus.CORPUS["mixed_coverage"].build(tmp_path / "repo")
    get(repo)
    result = lensmap.build_map(repo)
    fid = next(iter(result["nodes"]))

    preview = api.feature_verb_preview_view(repo, "revert", fid)
    assert preview["ok"]
    op_leaf = result["op_leaf"]
    expected = sorted({op_leaf[op] for op in preview["removed"] if op in op_leaf})
    assert preview["affected_features"] == expected
    assert fid in preview["affected_features"]


def test_feature_verb_preview_view_revert_ripples_across_a_second_feature(tmp_path):
    """The exact ask behind this view: a revert's real impact can span more than the one feature
    named -- every feature whose ops sit in the upset closure being removed lights up, not just
    the reverted one."""
    repo = corpus.CORPUS["linear_history"].build(tmp_path / "repo")
    get(repo)
    split_result = _split_into_two(repo)
    reverted, other = sorted(nid for nid, nd in split_result["nodes"].items() if not nd["children"])

    ops = Store(repo).all_ops()
    ideal = current_ideal(repo)
    reverted_ops = {op for op, leaf in split_result["op_leaf"].items() if leaf == reverted}
    upset_union: set[str] = set()
    for op_id in reverted_ops:
        upset_union |= order.upset_in(op_id, ideal.op_ids, ops)
    if not any(split_result["op_leaf"].get(op_id) == other for op_id in upset_union):
        pytest.skip("this fixture's split didn't produce a cross-feature upset -- nothing to ripple")

    preview = api.feature_verb_preview_view(repo, "revert", reverted)
    assert preview["ok"]
    assert other in preview["affected_features"]
    assert reverted in preview["affected_features"]


def test_feature_verb_preview_view_reports_an_error_for_an_unknown_verb_or_bad_arity(tmp_path):
    repo = corpus.CORPUS["mixed_coverage"].build(tmp_path / "repo")
    get(repo)
    lensmap.build_map(repo)

    assert "error" in api.feature_verb_preview_view(repo, "not-a-verb")
    assert "error" in api.feature_verb_preview_view(repo, "merge", "only-one-arg")


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
