"""The canonical JSON projection (sgt.api) consumed by the CLI --json mode and MCP.

The operation-ideal kernel's read surface: the op DAG, the current ideal, and ideal-vs-ideal
semantic diffs. Fixtures are deterministic git repos (tests/laws/corpus.py, pinned SHAs) mined by
`sgt.core.lens.get`.
"""

import json

from sgt.api import (
    compose_view, drift_view, fold_view, history_view, ideal_diff_view, map_view, oplog_view,
    plan_view, state_view, trust_view,
)
from sgt.core.lens import get
from sgt.core.op import make_op
from sgt.core.store import Store
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
    v = state_view(_mined(tmp_path, "mixed_coverage"))
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


def test_oplog_view_is_sorted_and_carries_op_fields(tmp_path):
    """The op DAG is emitted in a deterministic (id-sorted) order with each op's kind, footprint,
    provenance, structured attribution (U22/D7), and intent -- no set-iteration leakage."""
    v = oplog_view(_mined(tmp_path, "mixed_coverage"))
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
    for verb in ("log", "state"):
        assert main([verb, "--json"]) == 0
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
    v = history_view(repo)

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

    v = history_view(repo)
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
    hist = history_view(repo)
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
    hist = history_view(repo)
    non_root = next(o["id"] for o in hist["ops"] if o["commit_index"] > 0)

    v = fold_view(repo, op_ids=[non_root])
    assert v.get("forked") is True
    assert "message" in v


def test_plan_view_and_drift_view_are_empty_with_no_active_sessions(tmp_path):
    """A repo that's never seen `sgt plan intake` reports no sessions, no checkpoint groups, and
    no drift -- drift is only meaningful relative to a plan session's own predictions."""
    repo = _mined(tmp_path, "mixed_coverage")
    assert plan_view(repo) == {"sessions": [], "checkpoint": {"matches": [], "drift_op_ids": []}}
    assert drift_view(repo) == {"entries": []}


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

    view = plan_view(repo)
    step = view["sessions"][0]["steps"][0]
    assert step["status"] == "matched"
    assert step["files"] == [{"path": "a.py", "spans": [{"symbol": "a.py::foo", "start_line": 1, "end_line": 2}]}]

    # mining also mints residue/anchor pseudo-symbol ops for the same two commits (their own,
    # unpredicted drift) -- assert on `bar`'s own entry specifically, not the total count.
    drift = drift_view(repo)
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

    v = trust_view(tmp_path)
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

    op_ids = trust_view(tmp_path)["groups"][0]["op_ids"]
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

    v = trust_view(repo)
    assert [g["provenance"] for g in v["groups"]] == ["drift"]
    drift_ops = v["groups"][0]["ops"]
    assert drift_ops and all(op["drift"] for op in drift_ops)
    assert any(op["footprint"] == ["a.py::bar"] for op in drift_ops)
