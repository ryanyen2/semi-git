"""The canonical JSON projection (sgt.api) consumed by the CLI --json mode and MCP.

The operation-ideal kernel's read surface: the op DAG, the current ideal, and ideal-vs-ideal
semantic diffs. Fixtures are deterministic git repos (tests/laws/corpus.py, pinned SHAs) mined by
`sgt.core.lens.get`.
"""

import json

from sgt.api import drift_view, history_view, ideal_diff_view, oplog_view, plan_view, state_view
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
    provenance, and intent -- no set-iteration leakage."""
    v = oplog_view(_mined(tmp_path, "mixed_coverage"))
    assert v["count"] == len(v["ops"]) > 0
    assert [op["id"] for op in v["ops"]] == sorted(op["id"] for op in v["ops"])
    op = v["ops"][0]
    assert set(op) == {"id", "kind", "footprint", "provenance", "intent"}
    assert op["footprint"] and all({"symbol", "before", "after"} == set(f) for f in op["footprint"])
    assert op["provenance"]  # every mined op carries at least its witnessing commit


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
    assert bar_entries[0]["files"] == [
        {"path": "a.py", "spans": [{"symbol": "a.py::bar", "start_line": 5, "end_line": 6}]}
    ]
