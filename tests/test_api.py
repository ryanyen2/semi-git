"""The canonical JSON projection (sgt.api) consumed by the CLI --json mode and MCP.

The operation-ideal kernel's read surface: the op DAG, the current ideal, and ideal-vs-ideal
semantic diffs. Fixtures are deterministic git repos (tests/laws/corpus.py, pinned SHAs) mined by
`sgt.core.lens.get`.
"""

import json

from sgt.api import (
    compose_view, drift_view, fold_view, history_view, ideal_diff_view, map_view, oplog_view,
    plan_view, resolve_selection, state_view, status_view, trust_view,
)
from sgt.core.lens import get
from sgt.core.op import make_op
from sgt.core.store import Store
from sgt.lens import authored
from sgt.loop import match as match_mod
from sgt.loop import plan as plan_mod
from sgt.store.gitbind import init_store
from tests.laws import corpus


def _mined(tmp_path, name):
    """Build a deterministic kernel git fixture and mine it (get) so the store is populated."""
    repo = corpus.CORPUS[name].build(tmp_path / "repo")
    get(repo)
    return repo


def test_state_view_coverage_fraction_on_mixed_fixture(tmp_path):
    """R7: on a tree of two Python files + a YAML + a Markdown file, exactly the two code paths
    are entity-granular and the two non-parseable paths get whole-file coverage -- so the honest
    entity-granularity coverage fraction is 2/4 = 0.5."""
    v = state_view(_mined(tmp_path, "mixed_coverage"), full=True)
    assert v["covered_paths"] == ["config.yaml", "notes.md", "pkg.py", "util.py"]
    assert v["entity_paths"] == ["pkg.py", "util.py"]
    assert v["coverage_fraction"] == 0.5
    assert v["oracle_configured"] is False  # no `.sgt/oracle.json` in this fixture
    assert v["oracle_verdict"] is None
    # the frontier is the per-chain vector: it names entity, residue, anchor, and whole-file syms
    assert "pkg.py::compute" in v["frontier"]
    assert "config.yaml" in v["frontier"]


def test_ideal_diff_view_lists_symmetric_difference_grouped_by_symbol(tmp_path):
    """`ideal_diff_view` between two diverged branch ideals lists exactly the symmetric-difference
    ops, grouped by the symbol whose chain forked, labeled by side."""
    repo = corpus.CORPUS["diverged_chain"].build(tmp_path / "repo")
    corpus.checkout(repo, "release")
    get(repo)
    corpus.checkout(repo, "main")
    get(repo)

    v = ideal_diff_view(repo, "main", "release")
    assert v["ref_a"] == "main" and v["ref_b"] == "release"
    assert v["count"] == 2  # each branch's own tweak, and nothing else
    assert list(v["by_symbol"]) == ["slugify.py::slugify"]  # only the forked chain differs
    sides = v["by_symbol"]["slugify.py::slugify"]
    assert len(sides["only_in_a"]) == 1 and len(sides["only_in_b"]) == 1
    assert sides["only_in_a"] != sides["only_in_b"]  # a genuine fork, two distinct op ids


def test_resolve_selection_view_projects_op_set_label_and_counts(tmp_path):
    """`resolve_selection` is the thin projection over `select.resolve`: it exposes the resolved
    direct/closure op sets, the closure counts, and the display label for any spec form."""
    from sgt.store.gitbind import init_store

    gb, _ = init_store(tmp_path)
    (tmp_path / "a.py").write_text("def base():\n    return 1\n", encoding="utf-8")
    gb.commit_all("add a.py")
    get(tmp_path)
    base_op = next(op for op in Store(tmp_path).all_ops() if "a.py::base" in op.footprint)

    v = resolve_selection(tmp_path, "a.py::base")
    assert v["ok"] is True
    assert v["label"] == "a.py::base"
    assert v["direct_ops"] == [base_op.id]
    assert v["closure"] == [base_op.id]
    assert v["direct_op_count"] == 1
    assert v["closure_op_count"] == 1
    assert v["candidates"] == []


def test_resolve_selection_view_reports_ambiguous_candidates(tmp_path):
    """An ambiguous NL phrase projects `ok=False` with the ranked candidates, never raising."""
    from sgt.store.gitbind import init_store

    gb, _ = init_store(tmp_path)
    (tmp_path / "a.py").write_text("def base():\n    return 1\n", encoding="utf-8")
    gb.commit_all("add a.py")
    get(tmp_path)
    fa = authored.create(["a.py::base"], "payment alpha")
    fb = authored.create(["a.py::base"], "payment gamma")
    authored.save_authored(tmp_path, {fa.id: fa, fb.id: fb})

    v = resolve_selection(tmp_path, "payment")
    assert v["ok"] is False
    assert v["message"]
    assert {c["label"] for c in v["candidates"]} >= {"payment alpha", "payment gamma"}


def test_oplog_view_is_sorted_and_carries_op_fields(tmp_path):
    """The op DAG is emitted in a deterministic (id-sorted) order with each op's kind, footprint,
    provenance, structured attribution (U22/D7), and intent -- no set-iteration leakage."""
    v = oplog_view(_mined(tmp_path, "mixed_coverage"), full=True)
    assert v["count"] == len(v["ops"]) > 0
    assert [op["id"] for op in v["ops"]] == sorted(op["id"] for op in v["ops"])
    op = v["ops"][0]
    assert set(op) == {"id", "kind", "footprint", "provenance", "attribution", "intent"}
    assert op["footprint"] and all({"symbol", "before", "after"} == set(f) for f in op["footprint"])
    assert op["provenance"]  # every mined op carries at least its witnessing commit
    assert op["attribution"] == []  # no session/agent/plan stamped by plain mining


def test_kernel_views_are_pure(tmp_path):
    """Views are side-effect-free reads: called twice over a freshly-mined store they produce
    byte-identical output and mint no new ops (no network/timestamp leakage)."""
    repo = _mined(tmp_path, "mixed_coverage")
    first = json.dumps(state_view(repo), sort_keys=True)
    op_count = oplog_view(repo)["count"]
    second = json.dumps(state_view(repo), sort_keys=True)
    assert first == second
    assert oplog_view(repo)["count"] == op_count


def test_log_and_state_cli_json_match_views_byte_for_byte(tmp_path, capsys, monkeypatch):
    """R21: `sgt log/state --json` output is byte-identical to the api views -- the single
    projection, no drift between the CLI surface and the api."""
    from sgt.cli import main

    repo = _mined(tmp_path, "mixed_coverage")
    expected = {"log": json.dumps(oplog_view(repo), indent=2),
                "state": json.dumps(state_view(repo), indent=2)}

    monkeypatch.chdir(repo)
    # `log` stays a top-level spine verb; `state` is re-homed under the `advanced` grouping (KTD2).
    for verb, argv in (("log", ["log"]), ("state", ["advanced", "state"])):
        assert main([*argv, "--json"]) == 0
        assert capsys.readouterr().out.rstrip("\n") == expected[verb]


def test_diff_cli_json_matches_view_byte_for_byte(tmp_path, capsys, monkeypatch):
    """R21: `sgt diff --json <a> <b>` output is byte-identical to `sgt.api.ideal_diff_view`."""
    from sgt.cli import main

    repo = corpus.CORPUS["diverged_chain"].build(tmp_path / "repo")
    corpus.checkout(repo, "release")
    get(repo)
    corpus.checkout(repo, "main")
    get(repo)
    expected = json.dumps(ideal_diff_view(repo, "main", "release"), indent=2)

    monkeypatch.chdir(repo)
    assert main(["diff", "--json", "main", "release"]) == 0
    assert capsys.readouterr().out.rstrip("\n") == expected


def test_history_view_orders_commits_chronologically_and_places_every_op_on_the_axis(tmp_path):
    """`commits` is oldest-first (matching `GitBinding.history`); every op's `commit_index` is a
    valid position in that list, and the whole `ops` list is sorted by (commit_index, id)."""
    repo = _mined(tmp_path, "linear_history")
    v = history_view(repo, full=True)

    assert [c["index"] for c in v["commits"]] == list(range(len(v["commits"])))
    subjects = [c["subject"] for c in v["commits"]]
    assert subjects.index("add foo, qux, config, binary") < subjects.index("modify foo")
    assert subjects.index("modify foo") < subjects.index("rename foo -> bar within a.py")

    assert v["ops"]  # linear_history mints several ops
    valid_indices = {c["index"] for c in v["commits"]}
    for op in v["ops"]:
        assert set(op) == {"id", "kind", "feature_id", "commit_index"}
        assert op["commit_index"] in valid_indices
        assert op["feature_id"] is None  # no `sgt map` has run in this fixture
    assert [(o["commit_index"], o["id"]) for o in v["ops"]] == sorted(
        (o["commit_index"], o["id"]) for o in v["ops"]
    )


def test_history_view_reports_feature_id_once_a_tree_is_built(tmp_path):
    """After `sgt map`, `op_leaf` is populated -- every op with a feature assignment reports it."""
    from sgt.lens.map import build_map

    repo = _mined(tmp_path, "mixed_coverage")
    build_map(repo)

    v = history_view(repo, full=True)
    assert v["ops"]
    assert any(op["feature_id"] is not None for op in v["ops"])


def test_compose_view_bundles_every_sub_view_with_no_reshaping(tmp_path):
    """`compose_view` is purely additive glue: each key is exactly what calling the underlying
    view function directly would return, plus the current ideal's oracle verdict and an
    open-proposal list -- a workbench refresh in one call instead of ~9 shell-outs."""
    from sgt.api import drift_view as _drift, forks_view, intent_view, sessions_view, status_view
    from sgt.core.lens import current_ideal
    from sgt.core.oracle import verdict_for

    repo = _mined(tmp_path, "mixed_coverage")
    v = compose_view(repo)

    assert set(v) == {
        "map", "history", "status", "forks", "plan", "drift", "sessions", "trust", "intent",
        "oracle_verdict", "proposals",
    }
    assert v["map"] == map_view(repo)
    assert v["history"] == history_view(repo)
    assert v["status"] == status_view(repo)
    assert v["forks"] == forks_view(repo)
    assert v["plan"] == plan_view(repo)
    assert v["drift"] == _drift(repo)
    assert v["sessions"] == sessions_view(repo)
    assert v["trust"] == trust_view(repo)
    assert v["intent"] == intent_view(repo)
    assert v["oracle_verdict"] == verdict_for(repo, current_ideal(repo))
    assert v["proposals"] == []  # nothing proposed in this fixture


def test_fold_view_at_commit_index_matches_that_frontiers_code(tmp_path):
    """`--at <commit-index>` folds every op at or before that position on `history_view`'s axis --
    an earlier index yields fewer files/ops than a later one, and the returned bytes match a
    direct `code()` fold of the same op-set."""
    from sgt.core.fold import code
    from sgt.core.ideal import Ideal
    from sgt.core.store import Store

    repo = _mined(tmp_path, "linear_history")
    hist = history_view(repo, full=True)
    last_index = hist["commits"][-1]["index"]

    v = fold_view(repo, at_commit_index=last_index)
    assert "error" not in v and not v.get("forked")

    ops = Store(repo).all_ops()
    frontier_ids = frozenset(o["id"] for o in hist["ops"] if o["commit_index"] <= last_index)
    ideal = Ideal.from_ops(frontier_ids, ops)
    expected = {p: b.decode("utf-8", "replace") for p, b in code(ideal, ops).items()}
    assert v["files"] == expected
    assert v["op_count"] == len(frontier_ids)

    earlier = fold_view(repo, at_commit_index=0)
    assert earlier["op_count"] < v["op_count"]


def test_fold_view_ref_matches_ideal_for_ref(tmp_path):
    """`--at <ref>` folds a ref's own committed ideal (`lens.ideal_for_ref`), matching `state`'s
    own notion of a ref -- HEAD here, since the fixture never branches."""
    from sgt.core.fold import code
    from sgt.core.lens import ideal_for_ref
    from sgt.core.store import Store

    repo = _mined(tmp_path, "mixed_coverage")
    v = fold_view(repo, ref="HEAD")
    ops = Store(repo).all_ops()
    expected = {p: b.decode("utf-8", "replace") for p, b in code(ideal_for_ref(repo, "HEAD"), ops).items()}
    assert v["files"] == expected


def test_fold_view_requires_exactly_one_frontier_kwarg(tmp_path):
    repo = _mined(tmp_path, "mixed_coverage")
    assert "error" in fold_view(repo)
    assert "error" in fold_view(repo, ref="HEAD", at_commit_index=0)


def test_fold_view_rejects_an_ungrounded_op_id_set_without_raising(tmp_path):
    """An explicit `op_ids` set that isn't downward-closed (here: a single non-root op missing
    its own chain prerequisites) is refused as `{"forked": True, "message": ...}` -- the same
    `Ideal.from_ops` refusal `sgt.core.verbs._validated` turns into a preview outcome elsewhere,
    never a raised `ValueError` through the API."""
    repo = _mined(tmp_path, "linear_history")
    hist = history_view(repo, full=True)
    non_root = next(o["id"] for o in hist["ops"] if o["commit_index"] > 0)

    v = fold_view(repo, op_ids=[non_root])
    assert v.get("forked") is True
    assert "message" in v


def test_plan_view_and_drift_view_are_empty_with_no_active_sessions(tmp_path):
    """A repo that's never seen `sgt plan intake` reports no sessions, no checkpoint groups, and
    no drift -- drift is only meaningful relative to a plan session's own predictions."""
    repo = _mined(tmp_path, "mixed_coverage")
    assert plan_view(repo) == {"sessions": [], "checkpoint": {"matches": [], "drift_op_ids": []}}
    assert drift_view(repo) == {"count": 0, "op_ids": [], "kinds": {}}


def test_plan_view_reports_matched_step_spans_and_drift_view_reports_the_unpredicted_op(tmp_path):
    """A hand-seeded session predicting `a.py::foo` matches the real op that edits it; a second,
    unrelated real op (`a.py::bar`) is unpredicted -- it shows up as drift, with its own current
    line span, decoupled from the session."""
    repo = tmp_path / "repo"
    gb, _ = init_store(repo)
    (repo / "a.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    gb.commit_all("add foo")
    get(repo)
    store = Store(repo)
    baseline = sorted(op.id for op in store.all_ops())

    footprint = {"a.py::foo": (None, plan_mod._PENDING), "__plan__::s1::step0": (None, plan_mod._PENDING)}
    hollow = make_op(footprint, {}, kind="planned", off_chain=True, intent="touch foo")
    store.add_hollow(hollow)
    table = plan_mod._load_sessions(repo)
    table["s1"] = {
        "plan_text": "1. touch foo\n", "created_ts": 0.0, "last_activity_ts": 0.0, "status": "active",
        "baseline_op_ids": baseline,
        "steps": [{
            "hollow_id": hollow.id, "title": "touch foo", "predicted_footprint": ["a.py::foo"],
            "predicted_feature": None, "rationale": "", "status": "pending", "matched_op_ids": [],
        }],
    }
    plan_mod._save_sessions(repo, table)

    (repo / "a.py").write_text("def foo():\n    return 2\n", encoding="utf-8")
    gb.commit_all("touch foo")
    (repo / "a.py").write_text("def foo():\n    return 2\n\n\ndef bar():\n    return 3\n", encoding="utf-8")
    gb.commit_all("add bar")
    get(repo)

    checkpoint = plan_view(repo)["checkpoint"]
    assert len(checkpoint["matches"]) == 1
    group = checkpoint["matches"][0]
    assert group["session_id"] == "s1"
    assert checkpoint["drift_op_ids"]  # bar's own op, unpredicted

    match_mod.confirm_match(repo, "s1", group["hollow_ids"], group["op_ids"])

    view = plan_view(repo, full=True)
    step = view["sessions"][0]["steps"][0]
    assert step["status"] == "matched"
    assert step["files"] == [{"path": "a.py", "spans": [{"symbol": "a.py::foo", "start_line": 1, "end_line": 2}]}]

    # mining also mints residue/anchor pseudo-symbol ops for the same two commits (their own,
    # unpredicted drift) -- assert on `bar`'s own entry specifically, not the total count.
    drift = drift_view(repo, full=True)
    bar_entries = [e for e in drift["entries"] if e["footprint"] == ["a.py::bar"]]
    assert len(bar_entries) == 1


def test_map_view_reports_no_sessions_for_a_plain_mined_tree(tmp_path):
    """No `sgt session` has ever landed against this repo, so every node's rollup is empty --
    additive to `map_view` (plan U31, S7), never a required field elsewhere."""
    from sgt.lens.map import build_map

    repo = _mined(tmp_path, "mixed_coverage")
    build_map(repo)
    v = map_view(repo)
    assert v["nodes"]
    assert all(n["sessions"] == [] for n in v["nodes"])


def test_map_view_rolls_up_a_landed_session_onto_its_feature_node(tmp_path):
    """A session's landed op's `Attribution(session=...)` rolls up through `map_view`'s node tree
    (plan U31, S7) exactly like `op_count` does -- the leaf feature node it lands under names the
    session."""
    from pathlib import Path

    from sgt.core import session as session_mod
    from sgt.lens.map import build_map
    from tests.core.test_session import _seed_repo, _write_and_commit

    _seed_repo(tmp_path)
    session = session_mod.start(tmp_path, "s1")
    _write_and_commit(Path(session.scratch), "b.py", "def bar():\n    return 5\n")
    session_mod.land(tmp_path, "s1")
    get(tmp_path)
    build_map(tmp_path)

    v = map_view(tmp_path)
    named = [n for n in v["nodes"] if n["sessions"]]
    assert named, "at least one node should roll up the landed session"
    assert all(n["sessions"] == ["s1"] for n in named)


def test_sync_status_reports_complete_from_both_views_on_a_fresh_fully_synced_fixture(tmp_path):
    """U6: a freshly-mined, fully-synced fixture reports `sync_status.complete == True` from both
    `map_view` and `status_view`, per the plan's own scenario (1)."""
    repo = _mined(tmp_path, "mixed_coverage")

    assert map_view(repo)["sync_status"] == {"complete": True, "reached_genesis": True}
    assert status_view(repo)["sync_status"] == {"complete": True, "reached_genesis": True}


def test_sync_status_reports_incomplete_from_both_views_and_neither_view_mines(tmp_path, monkeypatch):
    """U6 scenario (2): a ref whose first-contact `_sync()` chunk is cut short by a deadline (same
    fixture technique as U4's tests) reports `complete == False` from both views, and reading
    either view alone triggers no additional mining."""
    import sgt.core.lens as lens_mod

    repo = corpus.CORPUS["mixed_coverage"].build(tmp_path / "repo")
    monkeypatch.setattr(lens_mod, "_CHUNK_BUDGET_SECONDS", -1.0)
    get(repo)

    op_count_before = len(Store(repo).all_ops())
    assert map_view(repo)["sync_status"] == {"complete": False, "reached_genesis": False}
    assert status_view(repo)["sync_status"] == {"complete": False, "reached_genesis": False}
    assert len(Store(repo).all_ops()) == op_count_before


def test_trust_view_is_empty_with_nothing_attributed_or_drifting(tmp_path):
    repo = _mined(tmp_path, "mixed_coverage")
    assert trust_view(repo) == {"groups": [], "total_ops": 0}


def test_trust_view_groups_a_landed_sessions_ops_under_its_session_name(tmp_path):
    from pathlib import Path

    from sgt.core import session as session_mod
    from tests.core.test_session import _seed_repo, _write_and_commit

    _seed_repo(tmp_path)
    session = session_mod.start(tmp_path, "s1")
    _write_and_commit(Path(session.scratch), "b.py", "def bar():\n    return 5\n")
    session_mod.land(tmp_path, "s1")
    get(tmp_path)

    v = trust_view(tmp_path, full=True)
    assert [g["provenance"] for g in v["groups"]] == ["s1"]
    group = v["groups"][0]
    assert group["op_ids"]
    assert all(not op["drift"] for op in group["ops"])
    assert v["total_ops"] == len(group["op_ids"])


def test_trust_view_dequeues_ops_covered_by_a_review_record(tmp_path):
    from pathlib import Path

    from sgt.core import review, session as session_mod
    from tests.core.test_session import _seed_repo, _write_and_commit

    _seed_repo(tmp_path)
    session = session_mod.start(tmp_path, "s1")
    _write_and_commit(Path(session.scratch), "b.py", "def bar():\n    return 5\n")
    session_mod.land(tmp_path, "s1")
    get(tmp_path)

    op_ids = trust_view(tmp_path, full=True)["groups"][0]["op_ids"]
    review.ack(tmp_path, op_ids, scope="session:s1")

    assert trust_view(tmp_path) == {"groups": [], "total_ops": 0}


def test_trust_view_includes_an_unattributed_drift_op_under_the_drift_key(tmp_path):
    """A mined op with no session/agent attribution but flagged drift by an active plan session
    still shows up in the trust queue -- grouped under the `"drift"` key -- until reviewed or
    retagged (the plan's second test scenario)."""
    repo = tmp_path / "repo"
    gb, _ = init_store(repo)
    (repo / "a.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    gb.commit_all("add foo")
    get(repo)
    store = Store(repo)
    baseline = sorted(op.id for op in store.all_ops())

    footprint = {"a.py::foo": (None, plan_mod._PENDING), "__plan__::s1::step0": (None, plan_mod._PENDING)}
    hollow = make_op(footprint, {}, kind="planned", off_chain=True, intent="touch foo")
    store.add_hollow(hollow)
    table = plan_mod._load_sessions(repo)
    table["s1"] = {
        "plan_text": "1. touch foo\n", "created_ts": 0.0, "last_activity_ts": 0.0, "status": "active",
        "baseline_op_ids": baseline,
        "steps": [{
            "hollow_id": hollow.id, "title": "touch foo", "predicted_footprint": ["a.py::foo"],
            "predicted_feature": None, "rationale": "", "status": "pending", "matched_op_ids": [],
        }],
    }
    plan_mod._save_sessions(repo, table)

    (repo / "a.py").write_text("def foo():\n    return 2\n\n\ndef bar():\n    return 3\n", encoding="utf-8")
    gb.commit_all("add bar")
    get(repo)

    v = trust_view(repo, full=True)
    assert [g["provenance"] for g in v["groups"]] == ["drift"]
    drift_ops = v["groups"][0]["ops"]
    assert drift_ops and all(op["drift"] for op in drift_ops)
    assert any(op["footprint"] == ["a.py::bar"] for op in drift_ops)


# -- compact-by-default view contracts (plan: optimize the sgt agent surface for context +
# retrieval speed, Part B) -----------------------------------------------------------------------


def test_oplog_view_default_is_compact_and_full_restores_today_shape(tmp_path):
    repo = _mined(tmp_path, "mixed_coverage")
    compact = oplog_view(repo)
    full = oplog_view(repo, full=True)

    assert set(compact) == {"count", "kinds", "truncated", "ops"}
    assert compact["count"] == full["count"]
    assert sum(compact["kinds"].values()) == compact["count"]
    op = compact["ops"][0]
    assert set(op) == {"id", "kind", "symbols", "intent"}
    assert op["symbols"] == sorted(op["symbols"])
    assert set(full) == {"ops", "count"}
    assert set(full["ops"][0]) == {"id", "kind", "footprint", "provenance", "attribution", "intent"}


def test_oplog_view_paginates_and_reports_truncated(tmp_path):
    repo = _mined(tmp_path, "mixed_coverage")
    total = oplog_view(repo)["count"]
    assert total > 1  # the fixture mints more than one op

    page = oplog_view(repo, limit=1, offset=0)
    assert len(page["ops"]) == 1
    assert page["truncated"] is True

    rest = oplog_view(repo, limit=total, offset=0)
    assert len(rest["ops"]) == total
    assert rest["truncated"] is False


def test_oplog_view_last_page_past_offset_is_not_truncated(tmp_path):
    """A regression guard: `truncated` must mean "more ops remain beyond this window", not just
    "this window is smaller than the total" -- the latter is trivially true for any page after
    the first, which would make an agent paginating forward (offset += limit while truncated)
    issue one guaranteed-empty extra request on every walk."""
    repo = _mined(tmp_path, "mixed_coverage")
    total = oplog_view(repo)["count"]
    assert total > 2  # the fixture mints more than two ops

    last_page = oplog_view(repo, limit=total, offset=total - 1)
    assert len(last_page["ops"]) == 1
    assert last_page["truncated"] is False  # nothing left after this partial-but-final page

    past_the_end = oplog_view(repo, limit=total, offset=total)
    assert past_the_end["ops"] == []
    assert past_the_end["truncated"] is False


def test_state_view_default_is_compact_and_full_restores_frontier(tmp_path):
    repo = _mined(tmp_path, "mixed_coverage")
    compact = state_view(repo)
    full = state_view(repo, full=True)

    assert "frontier" not in compact and "entity_paths" not in compact
    assert compact["frontier_count"] == len(full["frontier"])
    assert compact["entity_path_count"] == len(full["entity_paths"])
    # fields status_view depends on stay present at the default
    for key in ("covered_paths", "coverage_fraction", "derived_paths", "oracle_configured", "oracle_verdict"):
        assert compact[key] == full[key]


def test_history_view_default_is_compact_with_latest_commits_most_recent_first(tmp_path):
    repo = _mined(tmp_path, "linear_history")
    compact = history_view(repo)
    full = history_view(repo, full=True)

    assert set(compact) == {"commit_count", "op_count", "kinds", "features", "latest_commits"}
    assert compact["commit_count"] == len(full["commits"])
    assert compact["op_count"] == len(full["ops"])
    assert [c["index"] for c in compact["latest_commits"]] == sorted(
        (c["index"] for c in full["commits"]), reverse=True
    )


def test_history_view_latest_commits_respects_limit_and_offset(tmp_path):
    repo = _mined(tmp_path, "linear_history")
    full = history_view(repo, full=True)
    n = len(full["commits"])
    assert n > 1

    first = history_view(repo, limit=1)["latest_commits"]
    assert len(first) == 1
    assert first[0]["index"] == n - 1  # most recent

    second = history_view(repo, limit=1, offset=1)["latest_commits"]
    assert len(second) == 1
    assert second[0]["index"] == n - 2


def test_drift_view_default_is_compact(tmp_path):
    repo = _mined(tmp_path, "mixed_coverage")
    compact = drift_view(repo)
    assert set(compact) == {"count", "op_ids", "kinds"}
    assert compact["op_ids"] == sorted(compact["op_ids"])


def test_plan_view_default_reports_step_counts_not_steps(tmp_path):
    """Compact `plan_view` drops the per-step list (and its spans) in favor of counts, but keeps
    the checkpoint's small op-id lists intact -- `sgt checkpoint --confirm-...` needs them."""
    repo = tmp_path / "repo"
    gb, _ = init_store(repo)
    (repo / "a.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    gb.commit_all("add foo")
    get(repo)
    store = Store(repo)
    baseline = sorted(op.id for op in store.all_ops())

    footprint = {"a.py::foo": (None, plan_mod._PENDING), "__plan__::s1::step0": (None, plan_mod._PENDING)}
    hollow = make_op(footprint, {}, kind="planned", off_chain=True, intent="touch foo")
    store.add_hollow(hollow)
    table = plan_mod._load_sessions(repo)
    table["s1"] = {
        "plan_text": "1. touch foo\n", "created_ts": 0.0, "last_activity_ts": 0.0, "status": "active",
        "baseline_op_ids": baseline,
        "steps": [{
            "hollow_id": hollow.id, "title": "touch foo", "predicted_footprint": ["a.py::foo"],
            "predicted_feature": None, "rationale": "", "status": "pending", "matched_op_ids": [],
        }],
    }
    plan_mod._save_sessions(repo, table)

    (repo / "a.py").write_text("def foo():\n    return 2\n", encoding="utf-8")
    gb.commit_all("touch foo")
    get(repo)

    compact = plan_view(repo)
    session = compact["sessions"][0]
    assert "steps" not in session
    assert session["step_count"] == 1
    assert session["matched_count"] == 0

    checkpoint = compact["checkpoint"]
    assert checkpoint["matches"]  # the real op matches the predicted footprint
    match = checkpoint["matches"][0]
    assert "files" not in match
    assert match["hollow_ids"] and match["op_ids"]  # kept -- `--confirm-...` needs them

    full = plan_view(repo, full=True)["checkpoint"]["matches"][0]
    assert "files" in full


def test_trust_view_default_reports_op_count_not_op_detail(tmp_path):
    from pathlib import Path

    from sgt.core import session as session_mod
    from tests.core.test_session import _seed_repo, _write_and_commit

    _seed_repo(tmp_path)
    session = session_mod.start(tmp_path, "s1")
    _write_and_commit(Path(session.scratch), "b.py", "def bar():\n    return 5\n")
    session_mod.land(tmp_path, "s1")
    get(tmp_path)

    compact = trust_view(tmp_path)
    full = trust_view(tmp_path, full=True)
    group = compact["groups"][0]
    assert "op_ids" not in group and "ops" not in group
    assert group["op_count"] == len(full["groups"][0]["op_ids"]) > 0
    assert compact["total_ops"] == full["total_ops"]


def test_compose_view_full_threads_into_children(tmp_path):
    from sgt.api import status_view

    repo = _mined(tmp_path, "mixed_coverage")
    v = compose_view(repo, full=True)

    assert v["history"] == history_view(repo, full=True)
    assert v["plan"] == plan_view(repo, full=True)
    assert v["drift"] == drift_view(repo, full=True)
    assert v["trust"] == trust_view(repo, full=True)
    # unaffected children are unchanged regardless of `full`
    assert v["map"] == map_view(repo)
    assert v["status"] == status_view(repo)


# -- U3: per-dependent revert frontier (R4) -----------------------------------------------------

def _chain_repo(tmp_path):
    """helper <- user <- caller <- deep, each in its own commit. Reverting `user` exercises all
    three frontier buckets: `helper` is a foundation (user builds on it), `caller` is a blast
    (direct reference-edge dependent of user), `deep` is a carry (transitive, via caller)."""
    repo = tmp_path / "repo"
    gb, _ = init_store(repo)
    (repo / "a.py").write_text("def helper():\n    return 1\n", encoding="utf-8")
    gb.commit_all("helper")
    (repo / "b.py").write_text("from a import helper\n\ndef user():\n    return helper() + 1\n", encoding="utf-8")
    gb.commit_all("user")
    (repo / "c.py").write_text("from b import user\n\ndef caller():\n    return user() + 1\n", encoding="utf-8")
    gb.commit_all("caller")
    (repo / "d.py").write_text("from c import caller\n\ndef deep():\n    return caller() + 1\n", encoding="utf-8")
    gb.commit_all("deep")
    get(repo)
    return repo


def test_verb_preview_frontier_classifies_blast_carry_and_foundation(tmp_path):
    from sgt.api import verb_preview_view

    repo = _chain_repo(tmp_path)
    ops = Store(repo).all_ops()
    by_sym = lambda s: next(o for o in ops if s in o.footprint)
    helper_op, user_op = by_sym("a.py::helper"), by_sym("b.py::user")
    caller_op, deep_op = by_sym("c.py::caller"), by_sym("d.py::deep")

    v = verb_preview_view(repo, "revert", user_op.id)
    rows = {r["op_id"]: r for r in v["frontier"]}
    assert rows[caller_op.id] == {"op_id": caller_op.id, "bucket": "blast", "toggleable": True}
    assert rows[deep_op.id] == {"op_id": deep_op.id, "bucket": "carry", "toggleable": True}
    assert rows[helper_op.id] == {"op_id": helper_op.id, "bucket": "foundation", "toggleable": False}
    assert user_op.id not in rows  # the revert target itself is not a frontier row


def test_verb_preview_frontier_populates_for_a_symbol_ref_not_only_an_op_id(tmp_path):
    """The frontier must resolve a `file::symbol` (or op-id prefix) ref to its op the same way the
    plan does -- otherwise a symbol-targeted revert (the editor/blame entry point) gets an empty
    frontier and silently degrades to a plain revert. Regression: `_frontier_rows` used the raw
    unresolved `preview.target`."""
    from sgt.api import verb_preview_view

    repo = _chain_repo(tmp_path)
    ops = Store(repo).all_ops()
    by_sym = lambda s: next(o for o in ops if s in o.footprint)
    caller_op, deep_op = by_sym("c.py::caller"), by_sym("d.py::deep")

    v = verb_preview_view(repo, "revert", "b.py::user")  # symbol ref, not an op-id
    rows = {r["op_id"]: r for r in v["frontier"]}
    assert rows[caller_op.id]["bucket"] == "blast"
    assert rows[deep_op.id]["bucket"] == "carry"
    # identical to the op-id-targeted frontier for the same symbol
    assert rows == {r["op_id"]: r for r in verb_preview_view(repo, "revert", by_sym("b.py::user").id)["frontier"]}


def test_verb_preview_frontier_matches_what_revert_keep_dependents_applies(tmp_path):
    """The contract the TUI checklist (U9) and CLI `--keep` rely on: the projection's blast/carry
    buckets and toggleability line up with the hollows drafted and symbols carried by apply."""
    from sgt.api import verb_preview_view
    from sgt.core import rewrite

    repo = _chain_repo(tmp_path)
    ops = Store(repo).all_ops()
    by_id = {o.id: o for o in ops}
    user_op = next(o for o in ops if "b.py::user" in o.footprint)

    rows = verb_preview_view(repo, "revert", user_op.id)["frontier"]
    blast = {r["op_id"] for r in rows if r["bucket"] == "blast"}
    carry = {r["op_id"] for r in rows if r["bucket"] == "carry"}
    foundation = {r["op_id"] for r in rows if r["bucket"] == "foundation"}

    draft = rewrite.revert_keep_dependents(repo, user_op.id)  # keep all
    hollow_syms = {next(iter(Store(repo).get_hollow(h).footprint)) for h in draft.hollow_ids}
    blast_syms = {sym for oid in blast for sym in by_id[oid].footprint}
    carry_syms = {sym for oid in carry for sym in by_id[oid].footprint}

    assert blast_syms == hollow_syms  # every blast op's symbol got a continuation hollow
    assert carry_syms == set(draft.meta["carry_forward"])  # every carry op's symbol is carried
    assert foundation.isdisjoint(draft.meta["removed_ids"])  # foundation is never removed
    assert all(not r["toggleable"] for r in rows if r["bucket"] == "foundation")
    assert all(r["toggleable"] for r in rows if r["bucket"] in ("blast", "carry"))


def test_verb_preview_frontier_is_empty_for_non_revert_verbs(tmp_path):
    from sgt.api import verb_preview_view

    repo = _chain_repo(tmp_path)
    ops = Store(repo).all_ops()
    helper_op = next(o for o in ops if "a.py::helper" in o.footprint)
    # `restore` of an already-live op is a no-op preview; its frontier block is empty.
    assert verb_preview_view(repo, "restore", helper_op.id)["frontier"] == []
