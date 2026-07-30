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


# -- split ------------------------------------------------------------------------------------


def test_split_preview_mutates_nothing_until_confirmed(tmp_path):
    repo = corpus.CORPUS["linear_history"].build(tmp_path / "repo")  # has a natural 2-way split
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
    repo = corpus.CORPUS["linear_history"].build(tmp_path / "repo")  # has a natural 2-way split
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
    repo = corpus.CORPUS["linear_history"].build(tmp_path / "repo")  # has a natural 2-way split
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
    repo = corpus.CORPUS["linear_history"].build(tmp_path / "repo")  # has a natural 2-way split
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


# -- U11: revert <lane> --to <commit> (the timeline-scrub truncation edit) ----------------------


def _lane_commit_spans(repo, result):
    """Per leaf feature: the sorted `(op_id, commit_index)` of its ops that carry a commit index
    (i.e. appear in `history()` -- the axis `--to` scrubs along)."""
    from sgt.api import history_view

    ci = {o["id"]: o["commit_index"] for o in history_view(repo, full=True)["ops"]}
    spans: dict[str, list[tuple[str, int]]] = {}
    for op_id, leaf in result["op_leaf"].items():
        if op_id in ci:
            spans.setdefault(leaf, []).append((op_id, ci[op_id]))
    for leaf in spans:
        spans[leaf].sort(key=lambda t: t[1])
    return spans


def _pick_spanning_lane(spans):
    """The first leaf whose ops span >=2 distinct commit indices, and the earliest cut that leaves
    at least one op strictly after it -- so a truncation there is non-trivial."""
    for leaf, pairs in spans.items():
        idxs = sorted({ci for _, ci in pairs})
        if len(idxs) >= 2:
            return leaf, idxs[0]
    return None, None


def test_plan_revert_lane_to_commit_truncates_only_post_commit_ops(tmp_path):
    repo = corpus.CORPUS["linear_history"].build(tmp_path / "repo")
    ideal = get(repo)
    ops = Store(repo).all_ops()
    result = lensmap.build_map(repo)

    spans = _lane_commit_spans(repo, result)
    lane, cut = _pick_spanning_lane(spans)
    assert lane is not None, "linear_history should yield a lane spanning >=2 commits"

    preview = verbs.plan_revert_lane_to_commit(repo, lane, cut)
    assert preview.ok and preview.verb == "revert"
    # commit-index notation `@c<N>`, distinct from the checkpoint `@<seg_index>` the graph shows
    assert preview.target == f"{lane}@c{cut}"

    seed = {op for op, ci in spans[lane] if ci > cut}
    assert seed  # the cut actually leaves post-commit ops to remove
    expected: set[str] = set()
    for op in seed:
        expected |= order.upset_in(op, ideal.op_ids, ops)
    assert preview.removed == frozenset(expected)

    kept_lane_ops = {op for op, ci in spans[lane] if ci <= cut}
    assert kept_lane_ops <= preview.after_ids  # the lane's shape at/before the cut survives
    assert order.is_valid_ideal(ops, preview.after_ids)


def test_plan_revert_lane_to_commit_is_a_no_op_past_the_last_commit(tmp_path):
    repo = corpus.CORPUS["linear_history"].build(tmp_path / "repo")
    ideal = get(repo)
    result = lensmap.build_map(repo)

    spans = _lane_commit_spans(repo, result)
    lane = next(iter(spans))
    last = max(ci for _, ci in spans[lane])

    preview = verbs.plan_revert_lane_to_commit(repo, lane, last)
    assert preview.ok  # a no-op is a successful (empty) edit, not a failure
    assert preview.removed == frozenset()
    assert preview.after_ids == preview.before_ids == ideal.op_ids
    assert "no change" in preview.message
    # the empty-seed message names the commit indices the lane *does* have ops at, so the user
    # knows which N to pass instead of guessing (Problem 4: "the number that user can revert back")
    assert "its ops are at commit" in preview.message
    assert str(last) in preview.message


def test_plan_revert_lane_to_commit_refuses_an_unresolvable_ref(tmp_path):
    repo = corpus.CORPUS["mixed_coverage"].build(tmp_path / "repo")
    get(repo)
    lensmap.build_map(repo)

    preview = verbs.plan_revert_lane_to_commit(repo, "no-such-lane", 0)
    assert not preview.ok
    assert preview.after_ids == preview.before_ids


def test_plan_revert_lane_to_commit_keep_guards_a_second_lane(tmp_path):
    """`--keep <other>` never lets a truncation silently strand another lane. If the truncation's
    up-set would sweep the kept lane's ops, keeping them would leave those ops without their removed
    dependency -- so `_validated` refuses rather than dropping. If the kept lane isn't swept at all,
    naming it is an exact no-op. Both branches are correct; this fixture exercises whichever holds."""
    repo = corpus.CORPUS["linear_history"].build(tmp_path / "repo")
    get(repo)
    result = lensmap.build_map(repo)

    spans = _lane_commit_spans(repo, result)
    lane, cut = _pick_spanning_lane(spans)
    assert lane is not None
    others = [leaf for leaf in spans if leaf != lane]
    if not others:
        pytest.skip("linear_history clustered into a single lane -- no second lane to keep")
    other = others[0]

    base = verbs.plan_revert_lane_to_commit(repo, lane, cut)
    swept = set(base.removed) & {op for op, _ in spans[other]}

    kept = verbs.plan_revert_lane_to_commit(repo, lane, cut, keep=(other,))
    if swept:
        assert not kept.ok  # keeping a swept lane would strand it -> refuse, never silently drop
        assert kept.after_ids == kept.before_ids
    else:
        assert kept.ok and kept.removed == base.removed  # not swept -> keep is a no-op


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
    repo = corpus.CORPUS["linear_history"].build(tmp_path / "repo")  # has a natural 2-way split
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
    repo = corpus.CORPUS["linear_history"].build(tmp_path / "repo")  # has a natural 2-way split
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
    repo = corpus.CORPUS["linear_history"].build(tmp_path / "repo")  # has a natural 2-way split
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


def test_project_feature_preview_merge_carries_summary_and_metadata_so_what(tmp_path):
    """The consequence pane's projection for a metadata verb: no code rail (`files`/`fallout`
    empty, `reversible` True), a human `summary`, and a so-what that says code is untouched."""
    from sgt.api import _project_feature_preview

    repo = corpus.CORPUS["linear_history"].build(tmp_path / "repo")
    get(repo)
    split_result = _split_into_two(repo)
    survivor, absorbed = sorted(nid for nid, nd in split_result["nodes"].items() if not nd["children"])

    preview = verbs.plan_merge(repo, survivor, absorbed)
    assert preview.ok
    projected = _project_feature_preview(repo, "merge", preview)

    assert projected["ok"] and projected["verb"] == "merge"
    assert projected["reversible"] is True
    assert projected["files"] == {} and projected["fallout"] == [] and projected["carry_count"] == 0
    assert projected["summary"]  # non-empty human summary lines
    assert f"{preview.op_count} op(s)" in projected["summary"][1]
    assert "metadata only, code untouched" in projected["so_what"]
    assert tree.load(repo) == split_result  # projection is pure -- nothing written


def test_project_feature_preview_split_summary_names_both_groups(tmp_path):
    from sgt.api import _project_feature_preview

    repo = corpus.CORPUS["linear_history"].build(tmp_path / "repo")
    get(repo)
    before = lensmap.build_map(repo)
    fid = next(iter(before["nodes"]))

    preview = verbs.plan_split(repo, fid)
    assert preview.ok
    projected = _project_feature_preview(repo, "split", preview)

    assert projected["verb"] == "split"
    joined = "\n".join(projected["summary"])
    assert "splits in two" in joined
    assert preview.new_id[:8] in joined  # the fresh id the split would mint is surfaced
    assert tree.load(repo) == before  # pure


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


def test_rename_resolves_the_short_id_prefix_the_ui_prints(tmp_path):
    """The tree/map/save-hint print an abbreviated feature id (e.g. `1a2131ff`), and `sgt revert`
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
    """R3 authority inversion: where a user has authored a feature over a leaf's symbols, `sgt map`
    (map_view) shows that authored feature's label/id, not the clustered one; un-authored leaves
    keep their cluster labels untouched."""
    from sgt.lens import authored

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


def test_split_mints_an_identical_content_id_on_two_replicas_of_one_store(tmp_path):
    """KTD4 regression: `apply_split` must mint a content-addressed `f-<founding-op>` id, not a
    replica-local `F<n>`. Two independent clones splitting the identical members over a byte-
    identical op store must converge to the same id (a replica-local sequential mint did not)."""
    repo_a = corpus.CORPUS["linear_history"].build(tmp_path / "a")  # has a natural 2-way split
    repo_b = corpus.CORPUS["linear_history"].build(tmp_path / "b")
    get(repo_a)
    get(repo_b)
    res_a = lensmap.build_map(repo_a)
    res_b = lensmap.build_map(repo_b)
    fid_a = next(iter(res_a["nodes"]))
    fid_b = next(iter(res_b["nodes"]))
    assert fid_a == fid_b  # identical store -> identical content-addressed clustering ids

    applied_a = verbs.apply_split(repo_a, verbs.plan_split(repo_a, fid_a), confirm=True)
    applied_b = verbs.apply_split(repo_b, verbs.plan_split(repo_b, fid_b), confirm=True)
    new_a = next(nid for nid in applied_a["nodes"] if nid not in res_a["nodes"])
    new_b = next(nid for nid in applied_b["nodes"] if nid not in res_b["nodes"])
    assert new_a.startswith("f-")  # content-addressed, not a replica-local F<n>
    assert new_a == new_b


def test_split_preview_new_id_matches_what_apply_mints(tmp_path):
    # linear_history has a feature with a natural 2-way cut; mixed_coverage's 8 tightly-coupled
    # symbols cohere into one unsplittable leaf under the co-commit/path signals (correct, but no
    # split to exercise). These verbs test split/merge/move *mechanics*, not clustering shape.
    repo = corpus.CORPUS["linear_history"].build(tmp_path / "repo")
    get(repo)
    before = lensmap.build_map(repo)
    fid = next(iter(before["nodes"]))

    preview = verbs.plan_split(repo, fid)
    assert preview.ok
    applied = verbs.apply_split(repo, preview, confirm=True)
    new_id = next(nid for nid in applied["nodes"] if nid not in before["nodes"])
    assert preview.new_id == new_id


def test_rename_also_writes_an_authored_feature(tmp_path):
    """A reorg verb writes an authored-feature op *in addition to* its pin (R3): renaming a cluster
    feature is an authoring act, recorded as first-class merged state, while the labels pin that
    keeps the rename stable across a recluster is still written."""
    from sgt.lens import authored

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


def test_split_also_writes_an_authored_feature_for_the_new_group(tmp_path):
    from sgt.lens import authored

    repo = corpus.CORPUS["linear_history"].build(tmp_path / "repo")  # a splittable feature (see above)
    get(repo)
    result = lensmap.build_map(repo)
    fid = next(iter(result["nodes"]))

    applied = verbs.apply_split(repo, verbs.plan_split(repo, fid), confirm=True)
    new_id = next(nid for nid in applied["nodes"] if nid not in result["nodes"])

    af = authored.load_authored(repo)
    feat = af[f"af-{new_id}"]
    assert feat.live_members() == frozenset(applied["nodes"][new_id]["members"])
    # the pin writes are preserved too: the new group's members are assign-pinned to the new id
    pins = load_pins(repo)
    for m in applied["nodes"][new_id]["members"]:
        assert pins.assign[m] == new_id


def test_merge_absorbs_the_authored_feature_and_tombstones_the_absorbed(tmp_path):
    """R3 merge op: the survivor's authored feature gains the absorbed's members (OR-Set add), and a
    previously-authored absorbed feature is tombstoned (OR-Set delete) to zero live members."""
    from sgt.lens import authored

    repo = corpus.CORPUS["linear_history"].build(tmp_path / "repo")  # a splittable feature (see above)
    get(repo)
    split_result = _split_into_two(repo)
    survivor, absorbed = sorted(nid for nid, nd in split_result["nodes"].items() if not nd["children"])
    absorbed_members = split_result["nodes"][absorbed]["members"]
    assert absorbed_members  # the absorbed leaf carries members to absorb

    # Author the absorbed first, so the merge must tombstone its authored record (not just its pin).
    verbs.apply_rename(repo, verbs.plan_rename(repo, absorbed, "Doomed"))
    assert authored.load_authored(repo)[f"af-{absorbed}"].live_members()  # live members before merge

    merged = verbs.apply_merge(repo, verbs.plan_merge(repo, survivor, absorbed))

    af = authored.load_authored(repo)
    assert af[f"af-{survivor}"].live_members() == frozenset(merged["nodes"][survivor]["members"])
    for m in absorbed_members:
        assert m in af[f"af-{survivor}"].live_members()  # the absorbed's members came across
    assert af[f"af-{absorbed}"].live_members() == frozenset()  # ...and its record is tombstoned


def test_move_adds_the_moved_member_to_the_targets_authored_feature(tmp_path):
    """R3 move op: moving an op re-homes its member symbols; the target's authored feature gains them
    (OR-Set add) and a previously-authored source feature drops them (OR-Set remove)."""
    from sgt.lens import authored

    repo = corpus.CORPUS["linear_history"].build(tmp_path / "repo")  # a splittable feature (see above)
    get(repo)
    split_result = _split_into_two(repo)
    source, target = sorted(nid for nid, nd in split_result["nodes"].items() if not nd["children"])
    op_refs = [op for op, leaf in split_result["op_leaf"].items() if leaf == source][:1]
    assert op_refs

    ops_by_id = {op.id: op for op in Store(repo).all_ops()}
    src_members = set(split_result["nodes"][source]["members"])
    moved = {sym for sym in ops_by_id[op_refs[0]].footprint if sym in src_members}
    assert moved  # the moved op carries at least one tracked source member

    # Author the source first, so the move must drop the moved members from its authored record.
    verbs.apply_rename(repo, verbs.plan_rename(repo, source, "Source"))

    verbs.apply_move(repo, verbs.plan_move(repo, op_refs, target))

    af = authored.load_authored(repo)
    target_live = af[f"af-{target}"].live_members()
    source_live = af[f"af-{source}"].live_members()
    for sym in moved:
        assert sym in target_live  # the target's authored feature gained the moved member
        assert sym not in source_live  # ...and the source's authored feature dropped it
