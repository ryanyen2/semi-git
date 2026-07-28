"""Tests for sgt.intent.segment -- the deterministic feature-scoped segmentation (rungs 0/1).
No LLM/network anywhere; every assertion is pure, rebuildable derivation from the mined store +
git history + a hand-authored feature tree, same idiom as tests/intent/test_group.py."""

from __future__ import annotations

from sgt.core.lens import get
from sgt.core.store import Store
from sgt.intent import segment
from sgt.lens import tree
from sgt.store.gitbind import init_store


def _leaf(members: list[str], label: str) -> dict:
    return {"parent": None, "children": [], "members": sorted(members), "size": len(members),
            "dir": "", "label": label}


def _mine_fully(repo) -> None:
    """`get()` mines at most one deadline-bounded backfill chunk per call (KTD-3): on a fresh repo
    with enough history that walking backward to genesis doesn't finish inside that wall-clock
    budget, a single call can silently leave the earliest commits (and their ops) unmined --
    entirely a test-machine-speed artifact, not anything about the fixture's content. Loop until
    the backward walk actually reaches genesis so a fixture's commit count is never gated by how
    fast this happens to run."""
    from sgt.core.lens import _load_backfill_state, _ref_key
    from sgt.store.gitbind import GitBinding

    gb = GitBinding(repo)
    key = _ref_key(gb) or gb.head()
    for _ in range(10):
        get(repo)
        if _load_backfill_state(repo).get(key, {}).get("reached_genesis"):
            return
    raise AssertionError(f"{repo}: backfill did not reach genesis after 10 get() chunks")


def _save_tree(repo, leaves: dict[str, list[str]]) -> dict[str, str]:
    nodes = {fid: _leaf(members, fid) for fid, members in leaves.items()}
    ops = Store(repo).all_ops()
    result = {"nodes": nodes, "roots": sorted(nodes),
              "op_leaf": tree.assign_ops_to_leaves(nodes, ops),
              "max_depth": 0, "cannot_link_moves": [], "identity_events": []}
    tree.save(repo, result)
    return result["op_leaf"]


# -- rung 0: feature_runs -------------------------------------------------------------------------


def test_ops_of_a_feature_in_one_commit_form_one_run(tmp_path):
    gb, _ = init_store(tmp_path)
    (tmp_path / "a.py").write_text("def foo():\n    return 1\n\n\ndef bar():\n    return 2\n")
    gb.commit_all("feat(x): add foo and bar")
    _mine_fully(tmp_path)

    op_leaf = _save_tree(tmp_path, {"F-A": ["a.py::foo", "a.py::bar"]})
    runs = segment.feature_runs(tmp_path, op_leaf)
    assert list(runs) == ["F-A"]
    assert len(runs["F-A"]) == 1
    assert runs["F-A"][0].scope == "x"


def test_runs_are_ordered_by_commit_index(tmp_path):
    gb, _ = init_store(tmp_path)
    (tmp_path / "a.py").write_text("def foo():\n    return 1\n")
    gb.commit_all("feat(x): add foo")
    (tmp_path / "a.py").write_text("def foo():\n    return 1\n\n\ndef baz():\n    return 3\n")
    gb.commit_all("feat(x): add baz")
    _mine_fully(tmp_path)

    op_leaf = _save_tree(tmp_path, {"F-A": ["a.py::foo", "a.py::baz"]})
    runs = segment.feature_runs(tmp_path, op_leaf)["F-A"]
    assert [r.commit_index for r in runs] == sorted(r.commit_index for r in runs)
    assert len(runs) == 2


def test_op_with_unknown_feature_is_skipped_from_runs(tmp_path):
    gb, _ = init_store(tmp_path)
    (tmp_path / "a.py").write_text("def foo():\n    return 1\n")
    gb.commit_all("add foo")
    _mine_fully(tmp_path)
    # empty op_leaf -> no feature known -> no runs, never an error
    assert segment.feature_runs(tmp_path, {}) == {}


# -- novelty --------------------------------------------------------------------------------------


def test_creating_a_symbol_is_high_novelty(tmp_path):
    gb, _ = init_store(tmp_path)
    (tmp_path / "a.py").write_text("def foo():\n    return 1\n")
    gb.commit_all("feat(x): add foo")
    _mine_fully(tmp_path)

    op_leaf = _save_tree(tmp_path, {"F-A": ["a.py::foo"]})
    run = segment.feature_runs(tmp_path, op_leaf)["F-A"][0]
    assert run.novelty == 1.0  # every content touch is a create


def test_modifying_in_place_is_low_novelty(tmp_path):
    gb, _ = init_store(tmp_path)
    (tmp_path / "a.py").write_text("def foo(x):\n    return 1\n")
    gb.commit_all("feat(x): add foo")
    (tmp_path / "a.py").write_text("def foo(x, y):\n    return 1\n")  # rename/add-param: a tweak
    gb.commit_all("feat(x): add a param to foo")
    _mine_fully(tmp_path)

    op_leaf = _save_tree(tmp_path, {"F-A": ["a.py::foo"]})
    runs = segment.feature_runs(tmp_path, op_leaf)["F-A"]
    modify_run = runs[-1]
    assert modify_run.novelty == 0.0  # no create/remove, only in-place modification


# -- rung 1: segment_runs -------------------------------------------------------------------------


def test_low_novelty_tweak_merges_into_previous_segment(tmp_path):
    gb, _ = init_store(tmp_path)
    (tmp_path / "a.py").write_text("def foo(x):\n    return 1\n")
    gb.commit_all("feat(x): add foo")
    (tmp_path / "a.py").write_text("def foo(x, y):\n    return 1\n")
    gb.commit_all("feat(x): tweak foo signature")
    _mine_fully(tmp_path)

    op_leaf = _save_tree(tmp_path, {"F-A": ["a.py::foo"]})
    runs = segment.feature_runs(tmp_path, op_leaf)["F-A"]
    segs = segment.segment_runs(runs)
    assert len(segs) == 1  # the tweak did not open a new chapter
    # the whole feature's ops are in the one segment (total partition)
    assert segs[0].op_ids == frozenset().union(*(r.op_ids for r in runs))


def test_scope_change_forces_a_boundary(tmp_path):
    gb, _ = init_store(tmp_path)
    (tmp_path / "a.py").write_text("def foo(x):\n    return 1\n")
    gb.commit_all("feat(x): add foo")
    (tmp_path / "a.py").write_text("def foo(x, y):\n    return 1\n")
    gb.commit_all("fix(y): tweak foo under a different scope")
    _mine_fully(tmp_path)

    op_leaf = _save_tree(tmp_path, {"F-A": ["a.py::foo"]})
    runs = segment.feature_runs(tmp_path, op_leaf)["F-A"]
    segs = segment.segment_runs(runs)
    assert len(segs) == 2  # scope shift cuts even though the second run is a low-novelty tweak


def test_every_run_lands_in_exactly_one_segment(tmp_path):
    gb, _ = init_store(tmp_path)
    for i in range(4):
        (tmp_path / "a.py").write_text("".join(f"def f{j}():\n    return {j}\n\n\n" for j in range(i + 1)))
        gb.commit_all(f"feat(x): step {i}")
    _mine_fully(tmp_path)

    op_leaf = _save_tree(tmp_path, {"F-A": [f"a.py::f{j}" for j in range(4)]})
    runs = segment.feature_runs(tmp_path, op_leaf)["F-A"]
    segs = segment.segment_runs(runs)
    covered = frozenset().union(*(s.op_ids for s in segs))
    assert covered == frozenset().union(*(r.op_ids for r in runs))
    # no op counted twice
    assert sum(s.op_count for s in segs) == len(covered)


def test_segmentation_is_deterministic(tmp_path):
    gb, _ = init_store(tmp_path)
    (tmp_path / "a.py").write_text("def foo():\n    return 1\n")
    gb.commit_all("feat(x): add foo")
    (tmp_path / "b.py").write_text("def bar():\n    return 2\n")
    gb.commit_all("fix(y): add bar")
    _mine_fully(tmp_path)

    op_leaf = _save_tree(tmp_path, {"F-A": ["a.py::foo", "b.py::bar"]})
    first = segment.deterministic_segments(tmp_path, op_leaf)
    second = segment.deterministic_segments(tmp_path, op_leaf)
    assert first == second


def test_empty_feature_yields_no_segments(tmp_path):
    assert segment.segment_runs([]) == []


# -- resolve_checkpoint (the <feature>@<n> rewind handle) -----------------------------------------


def _feature_with_two_segments(tmp_path):
    gb, _ = init_store(tmp_path)
    (tmp_path / "a.py").write_text("def foo():\n    return 1\n")
    gb.commit_all("feat(x): add foo")
    (tmp_path / "b.py").write_text("def bar():\n    return 2\n")
    gb.commit_all("fix(y): add bar")  # scope change -> two segments
    _mine_fully(tmp_path)
    op_leaf = _save_tree(tmp_path, {"F-A": ["a.py::foo", "b.py::bar"]})
    # write a label so label-matching can be exercised
    nodes = {"F-A": _leaf(["a.py::foo", "b.py::bar"], "My Feature")}
    ops = Store(tmp_path).all_ops()
    tree.save(tmp_path, {"nodes": nodes, "roots": ["F-A"],
                         "op_leaf": tree.assign_ops_to_leaves(nodes, ops),
                         "max_depth": 0, "cannot_link_moves": [], "identity_events": []})
    return segment.deterministic_segments(tmp_path, op_leaf)["F-A"]


def test_resolve_checkpoint_by_feature_id_and_index(tmp_path):
    segs = _feature_with_two_segments(tmp_path)
    resolved = segment.resolve_checkpoint(tmp_path, "F-A@1")
    assert resolved is not None
    op_ids, label = resolved
    assert op_ids == segs[1].op_ids
    assert "My Feature@1" in label


def test_resolve_checkpoint_by_label(tmp_path):
    _feature_with_two_segments(tmp_path)
    resolved = segment.resolve_checkpoint(tmp_path, "my feature@0")
    assert resolved is not None


def test_resolve_checkpoint_bad_specs_return_none(tmp_path):
    _feature_with_two_segments(tmp_path)
    assert segment.resolve_checkpoint(tmp_path, "F-A") is None          # no @n
    assert segment.resolve_checkpoint(tmp_path, "F-A@9") is None        # index out of range
    assert segment.resolve_checkpoint(tmp_path, "nope@0") is None       # unknown feature
    assert segment.resolve_checkpoint(tmp_path, "F-A@x") is None        # non-numeric index


def test_checkpoint_slug_shapes():
    assert segment.checkpoint_slug("validate email") == "validate-email"
    assert segment.checkpoint_slug("fix(effects): resolve the leak") == "fix-effects-resolve"
    assert segment.checkpoint_slug("  Trim  Me  ") == "trim-me"
    assert segment.checkpoint_slug("UPPER_snake.Case") == "upper-snake-case"
    assert segment.checkpoint_slug("!!!") == ""  # no alphanumerics -> empty (a "no match" slug)
    assert len(segment.checkpoint_slug("a" * 40)) <= 24


def test_resolve_checkpoint_by_slug(tmp_path):
    segs = _feature_with_two_segments(tmp_path)
    slug = segment.checkpoint_slug(segs[1].label)
    resolved = segment.resolve_checkpoint(tmp_path, f"F-A:{slug}")
    assert resolved is not None
    op_ids, label = resolved
    assert op_ids == segs[1].op_ids          # same deterministic op-set as `@1`
    assert "My Feature@1" in label           # display still carries the positional index


def test_resolve_checkpoint_slug_unknown_returns_none(tmp_path):
    _feature_with_two_segments(tmp_path)
    assert segment.resolve_checkpoint(tmp_path, "F-A:no-such-checkpoint") is None


def test_apply_label_pins_overrides_and_marks_user_source(tmp_path):
    segs = _feature_with_two_segments(tmp_path)
    key = segment.pin_key(segs[0])
    pinned = segment.apply_label_pins(segs, {key: "My Custom Intent"})
    assert pinned[0].label == "My Custom Intent"
    assert pinned[0].source == "user"
    # op membership is untouched by a relabel -- only the label/source change
    assert pinned[0].op_ids == segs[0].op_ids
    # the unpinned segment is unchanged
    assert pinned[1].label == segs[1].label


def test_apply_label_pins_ignores_unmatched_key(tmp_path):
    segs = _feature_with_two_segments(tmp_path)
    pinned = segment.apply_label_pins(segs, {"deadbeef" * 5: "nope"})
    assert [s.label for s in pinned] == [s.label for s in segs]
    assert all(s.source != "user" for s in pinned)


def test_verb_preview_view_resolves_a_checkpoint(tmp_path):
    """The VS Code hover-preview path: `verb_preview_view('revert', '<feature>@<n>')` must resolve
    to the segment's deterministic op-set (not silently fall through to an unresolvable plan)."""
    from sgt.api import verb_preview_view

    segs = _feature_with_two_segments(tmp_path)
    v = verb_preview_view(tmp_path, "revert", "F-A@1")
    assert v["ok"] is True
    # the removed set is the segment's upset closure -> at least the segment's own ops
    assert segs[1].op_ids <= frozenset(v["removed"])


def test_soft_cap_bounds_segment_count(tmp_path):
    """A feature with many distinct-scope commits would cut into many segments; the MAX_SEGMENTS
    soft cap keeps the offline view readable."""
    gb, _ = init_store(tmp_path)
    members = []
    for i in range(segment.MAX_SEGMENTS + 4):
        sym = f"f{i}"
        members.append(f"a.py::{sym}")
        body = "".join(f"def f{j}():\n    return {j}\n\n\n" for j in range(i + 1))
        (tmp_path / "a.py").write_text(body)
        gb.commit_all(f"feat(s{i}): add {sym}")  # every commit a fresh scope -> a seam each
    _mine_fully(tmp_path)

    op_leaf = _save_tree(tmp_path, {"F-A": members})
    runs = segment.feature_runs(tmp_path, op_leaf)["F-A"]
    segs = segment.segment_runs(runs)
    assert len(segs) <= segment.MAX_SEGMENTS
    # still a total partition after capping
    assert frozenset().union(*(s.op_ids for s in segs)) == frozenset().union(*(r.op_ids for r in runs))


def test_stale_record_leftover_is_capped_not_appended_one_by_one(tmp_path):
    """A partial/stale persisted record must not let a feature's segment count grow unbounded as
    commits land after the last `sgt intent build` -- the runs it doesn't name are cut with the
    same MAX_SEGMENTS-capped partition a from-scratch feature gets (`_partition_runs`), not one
    raw trailing segment per unclaimed commit."""
    gb, _ = init_store(tmp_path)
    members = []
    for i in range(segment.MAX_SEGMENTS + 4):
        sym = f"f{i}"
        members.append(f"a.py::{sym}")
        body = "".join(f"def f{j}():\n    return {j}\n\n\n" for j in range(i + 1))
        (tmp_path / "a.py").write_text(body)
        gb.commit_all(f"feat(s{i}): add {sym}")  # every commit a fresh scope -> a seam each
    _mine_fully(tmp_path)

    op_leaf = _save_tree(tmp_path, {"F-A": members})
    runs = segment.feature_runs(tmp_path, op_leaf)["F-A"]
    # a record that names none of these commits (as if built long ago, then the feature moved on)
    stale_record = [{"commit_shas": ["deadbeef" * 5], "label": "old chapter",
                     "rationale": "", "source": "llm"}]
    segs = segment.overlay_persisted(runs, stale_record)
    assert len(segs) <= segment.MAX_SEGMENTS
    assert frozenset().union(*(s.op_ids for s in segs)) == frozenset().union(*(r.op_ids for r in runs))


def test_persisted_entries_are_never_recut_only_the_unclaimed_tail_is(tmp_path):
    """The cap applies to runs the record doesn't cover -- a real persisted (LLM-authored) chapter
    is never merged away by MAX_SEGMENTS, even if the feature as a whole has more chapters than
    the cap; only the leftover partition is bounded."""
    segs = _feature_with_two_segments(tmp_path)
    record = [{"commit_shas": [segs[0].commit_shas[0]], "label": "kept as-is",
              "rationale": "", "source": "llm"}]
    op_leaf = tree.load(tmp_path)["op_leaf"]
    runs = segment.feature_runs(tmp_path, op_leaf)["F-A"]
    out = segment.overlay_persisted(runs, record)
    llm_segs = [s for s in out if s.source == "llm"]
    assert len(llm_segs) == 1 and llm_segs[0].label == "kept as-is"


# -- rung-1 seam hysteresis (§3.4) ----------------------------------------------------------------


def _mk_run(i, sha, novelty=0.0, scope="x"):
    return segment.Run(feature_id="F-A", commit_index=i, commit_sha=sha, subject=f"s{i}",
                       scope=scope, op_ids=frozenset({f"op{i}"}), novelty=novelty)


def test_seam_bonus_preserves_a_near_threshold_persisted_boundary():
    """A seam scoring just below `CUT_THRESHOLD` (novelty 0.6, same scope, no gap) is normally
    merged; if it already started a chapter in the persisted record, `SEAM_BONUS` pushes it over
    and the boundary is kept -- pure anti-flicker hysteresis."""
    runs = [_mk_run(0, "aaaaaaaa"), _mk_run(1, "bbbbbbbb", novelty=0.6)]
    assert segment._cut_points(runs) == []                                # 0.6 < 1.0 -> merged
    assert segment._cut_points(runs, frozenset({"bbbbbbbb"})) == [1]       # 0.6 + 0.5 >= 1.0 -> kept


def test_seam_bonus_never_invents_a_boundary_from_nothing():
    """`SEAM_BONUS` is strictly below `CUT_THRESHOLD`, so a seam with no real signal (score 0)
    stays merged even when named as a prior boundary -- the bonus can only *preserve*, never invent."""
    runs = [_mk_run(0, "aaaaaaaa"), _mk_run(1, "bbbbbbbb", novelty=0.0)]
    assert segment._cut_points(runs, frozenset({"bbbbbbbb"})) == []        # 0.0 + 0.5 < 1.0


def test_seam_bonus_survives_cap_reranking():
    """A persisted boundary must survive `_cap_cuts`: when every seam ties (each commit a fresh
    scope) the cap drops some to stay within `MAX_SEGMENTS`, but a bonused seam ranks strictly
    highest and is always kept."""
    runs = [_mk_run(i, f"c{i:07d}", scope=f"s{i}") for i in range(segment.MAX_SEGMENTS + 3)]
    base = segment._cut_points(runs)
    assert len(base) == segment.MAX_SEGMENTS - 1  # capped, all seams tied at W_SCOPE
    pinned = runs[3].commit_sha
    assert 3 in segment._cut_points(runs, frozenset({pinned}))


def test_segment_runs_prior_boundaries_default_is_byte_identical():
    """The `prior_boundaries` default (empty) leaves `segment_runs` byte-identical to a prior-free
    cut -- the migration-safe guarantee the hysteresis is layered on top of."""
    runs = [_mk_run(0, "aaaaaaaa"), _mk_run(1, "bbbbbbbb", novelty=0.6)]
    assert segment.segment_runs(runs) == segment.segment_runs(runs, frozenset())


# -- granularity gate ------------------------------------------------------------------------------
# The redesign plan (2026-07-21, "segment-chunks as the atom") makes the segment the visual atom of
# `sgt graph` -- if the cut weights ever drift towards over-fragmenting, every lane in that view
# degrades back into the "confetti" the redesign exists to fix. This harness measures the two
# numbers that redesign is gated on (median cars/feature, single-op-car fraction) against a fixed
# mix of realistic feature shapes -- a one-shot feature, a tweaked-in-place feature, a genuinely
# multi-chapter feature, and a pathologically long-lived one -- so a future weight change (Phase 4
# of that plan, or any later retune) has a concrete regression signal instead of "feels chunkier".


def _granularity_fixture(tmp_path):
    gb, _ = init_store(tmp_path)
    leaves: dict[str, list[str]] = {}

    (tmp_path / "simple.py").write_text("def simple():\n    return 1\n")
    gb.commit_all("feat(simple): add simple")
    leaves["F-SIMPLE"] = ["simple.py::simple"]

    (tmp_path / "tweak.py").write_text("def tweak(x):\n    return 1\n")
    gb.commit_all("feat(tweak): add tweak")
    (tmp_path / "tweak.py").write_text("def tweak(x, y):\n    return 1\n")
    gb.commit_all("feat(tweak): widen tweak's signature")
    (tmp_path / "tweak.py").write_text("def tweak(x, y, z):\n    return 1\n")
    gb.commit_all("feat(tweak): widen tweak's signature again")
    leaves["F-TWEAKS"] = ["tweak.py::tweak"]

    chapter_members = []
    for i, scope in enumerate(["intake", "process", "export"]):
        sym = f"chapter{i}"
        chapter_members.append(f"chapters.py::{sym}")
        body = "".join(f"def chapter{j}():\n    return {j}\n\n\n" for j in range(i + 1))
        (tmp_path / "chapters.py").write_text(body)
        gb.commit_all(f"feat({scope}): add {sym}")  # a fresh scope each commit -> a boundary each
    leaves["F-CHAPTERS"] = chapter_members

    big_members = []
    for i in range(segment.MAX_SEGMENTS + 4):
        sym = f"big{i}"
        big_members.append(f"big.py::{sym}")
        body = "".join(f"def big{j}():\n    return {j}\n\n\n" for j in range(i + 1))
        (tmp_path / "big.py").write_text(body)
        gb.commit_all(f"feat(s{i}): add {sym}")
    leaves["F-BIG"] = big_members

    _mine_fully(tmp_path)
    op_leaf = _save_tree(tmp_path, leaves)
    return segment.deterministic_segments(tmp_path, op_leaf)


def test_granularity_metric_median_cars_per_feature(tmp_path):
    by_feature = _granularity_fixture(tmp_path)
    counts = sorted(len(segs) for segs in by_feature.values())
    median = counts[len(counts) // 2] if len(counts) % 2 else \
        (counts[len(counts) // 2 - 1] + counts[len(counts) // 2]) / 2
    # F-SIMPLE and F-TWEAKS each merge into one car; only F-CHAPTERS/F-BIG genuinely fragment --
    # the redesign's whole premise is that most lanes read as one or a very few cars.
    assert median <= 2, f"granularity regressed: median cars/feature = {median} ({counts})"
    assert by_feature["F-SIMPLE"] == [] or len(by_feature["F-SIMPLE"]) == 1
    assert len(by_feature["F-TWEAKS"]) == 1  # in-place tweaks, same scope -> no new chapter
    assert len(by_feature["F-BIG"]) <= segment.MAX_SEGMENTS


def test_granularity_metric_single_op_car_fraction(tmp_path):
    by_feature = _granularity_fixture(tmp_path)
    all_segs = [s for segs in by_feature.values() for s in segs]
    single_op = sum(1 for s in all_segs if s.op_count == 1)
    fraction = single_op / len(all_segs)
    # a car with exactly one op is the least informative shape a chunk can take; if a weight
    # change ever pushes most cars down to single-op slivers, this is the number that catches it.
    assert fraction <= 0.6, f"granularity regressed: single-op-car fraction = {fraction} ({len(all_segs)} cars)"
