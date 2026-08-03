"""The canonical JSON projection (sgt.api) consumed by the CLI --json mode and MCP.

The operation-ideal kernel's read surface: the op DAG, the current ideal, and ideal-vs-ideal
semantic diffs. Fixtures are deterministic git repos (tests/laws/corpus.py, pinned SHAs) mined by
`sgt.core.lens.get`.
"""

import json

from sgt.api import (
    compose_view, drift_view, fold_view, history_view, ideal_diff_view, map_view, now_view,
    oplog_view, plan_view, resolve_selection, save_preview_view, state_view, status_view,
    trust_view,
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
    """R21: the CLI `--json` surface is byte-identical to the api views -- one projection, no drift.
    `sgt log` defaults to the grid (`grid_view`, KTD9); the raw op DAG moved to `sgt log --ops`
    (`oplog_view`); `state` is re-homed under the `advanced` grouping (KTD2)."""
    from sgt.api import grid_view
    from sgt.cli import main
    from sgt.lens.map import build_map

    repo = _mined(tmp_path, "mixed_coverage")
    build_map(repo)  # a stable built map so `sgt log`'s grid doesn't auto-build mid-test
    expected = {
        ("log",): json.dumps(grid_view(repo), indent=2),
        ("log", "--ops"): json.dumps(oplog_view(repo), indent=2),
        ("advanced", "state"): json.dumps(state_view(repo), indent=2),
    }

    monkeypatch.chdir(repo)
    for argv, want in expected.items():
        assert main([*argv, "--json"]) == 0
        assert capsys.readouterr().out.rstrip("\n") == want


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


def _shared_feature_dag(tmp_path):
    """Build a minimal DAG for the shared-feature projection tests. Corpus fixtures are all
    single-node, so we reshape a freshly built `tree.json` by hand: a LEAF listed under *both* N1
    and N2 (a DAG) but canonically parented to N1 -- N2 only borrows it. Returns
    (repo, leaf_id, real_op_id); the caller reads `map_view(repo)`."""
    from sgt.lens import tree
    from sgt.lens.map import build_map

    repo = _mined(tmp_path, "mixed_coverage")
    build_map(repo)
    res = tree.load(repo)
    real_op = next(iter(res["op_leaf"]))  # a real op so leaf_op_count / attribution pick it up

    def node(nid, parent, children, label, members=()):
        return {"id": nid, "parent": parent, "children": list(children), "depth": 0,
                "members": list(members), "size": max(1, len(members)), "dir": "",
                "label": label, "why": "", "split_reason": None}

    leaf = "f-shared"
    res["nodes"] = {
        "N0": node("N0", None, ["N1", "N2"], "root"),
        "N1": node("N1", "N0", [leaf], "SubA"),
        "N2": node("N2", "N0", [leaf], "SubB"),  # DAG: also lists the leaf, but is not its parent
        leaf: node(leaf, "N1", [], "Shared", members=["x"]),
    }
    res["roots"] = ["N0"]
    res["op_leaf"] = {real_op: leaf}
    tree.save(repo, res)
    return repo, leaf, real_op


def test_map_view_never_emits_a_node_id_as_its_label(tmp_path):
    """A node id is a content hash (`f-`/`af-`), not a name. A tree persisted without a label pass
    (or a node minted after it) can arrive label-less or with the id copied into `label`; `map_view`
    must derive a readable name from the node's members instead of leaking the raw hash to the graph
    / grid, which is what made the workbench show `f-<hash>` rows ("unreadable")."""
    from sgt.lens import tree
    from sgt.lens.map import build_map

    repo = _mined(tmp_path, "mixed_coverage")
    build_map(repo)
    res = tree.load(repo)
    real_op = next(iter(res["op_leaf"]))

    def node(nid, parent, children, label, members=()):
        return {"id": nid, "parent": parent, "children": list(children), "depth": 0,
                "members": list(members), "size": max(1, len(members)), "dir": "",
                "label": label, "why": "", "split_reason": None}

    blank, idlab = "f-blanklabel", "f-idislabel"
    res["nodes"] = {
        "R": node("R", None, [blank, idlab], "root"),
        blank: node(blank, "R", [], "", members=["svc/auth.py::login", "svc/auth.py::logout"]),
        idlab: node(idlab, "R", [], idlab, members=["net/wire.py::encode"]),  # label copied from id
    }
    res["roots"] = ["R"]
    res["op_leaf"] = {real_op: blank}
    tree.save(repo, res)

    by_id = {n["id"]: n for n in map_view(repo)["nodes"]}
    assert by_id[blank]["label"] not in ("", blank)  # empty label -> derived name, not blank/hash
    assert "login" in by_id[blank]["label"]          # derived from the leading member symbols
    assert by_id[idlab]["label"] != idlab            # id-copied-into-label -> derived name instead


def test_map_view_renders_a_shared_feature_under_its_one_canonical_parent(tmp_path):
    """The tree is a DAG: an authored feature can be spliced under more than one subsystem, so its
    id is listed in several parents' `children` while carrying a single canonical `parent`. `map_view`
    must render it once -- under that one parent -- so the feature tree, the workbench timeline, and
    the TUI agree, and it must not double-count the shared ops in every ancestor's `op_count` rollup."""
    repo, leaf, _ = _shared_feature_dag(tmp_path)

    v = map_view(repo)
    by_id = {n["id"]: n for n in v["nodes"]}
    # the shared leaf is rendered under its canonical parent only, never the borrowing subsystem
    assert by_id["N1"]["children"] == [leaf]
    assert by_id["N2"]["children"] == []
    assert [n["id"] for n in v["nodes"] if leaf in n["children"]] == ["N1"]
    # and its single op is counted once at the root, not once per listing parent
    assert by_id[leaf]["op_count"] == 1
    assert by_id["N2"]["op_count"] == 0
    assert by_id["N0"]["op_count"] == 1
    # `kind` must agree with the now-canonical `children`: a borrower-only node (all its listed
    # children are borrowed, none canonically its own) is a leaf, not a subsystem. Consumers gate on
    # this pair -- VS Code tree actions/collapsibility, TUI expand, timeline recursion -- so a
    # `kind:"subsystem"` row emitting `children:[]` is mishandled (drops actions / vanishes).
    assert by_id["N2"]["kind"] == "feature"
    assert by_id["N1"]["kind"] == "subsystem"
    # `feature_count` counts feature nodes, so it must agree with the canonical `kind`: the borrower-
    # only N2 and the shared leaf are the two features here (raw children would miscount N2 as a
    # subsystem and report 1).
    assert v["feature_count"] == 2
    # Canonical de-duplication must yield a proper forest, which is what makes each node's canonical
    # parent well-defined (the residual-risk the whole scheme rests on): every non-root node appears
    # in exactly one node's emitted `children`, and that node is its `parent`. Reverting `children`
    # to raw would list the shared leaf under both N1 and N2 (listers == ["N1", "N2"]) -- so this
    # bites the double-listing directly, and also flags an orphan (a parent that fails to list its
    # child would give listers == []).
    for nid, nd in by_id.items():
        if nd["parent"] is None:
            continue
        listers = [p for p in by_id if nid in by_id[p]["children"]]
        assert listers == [nd["parent"]], (nid, listers)


def test_map_view_rolls_sessions_up_through_the_canonical_parent_only(tmp_path, monkeypatch):
    """`node_sessions` must roll provenance up through *canonical* children, exactly like `op_count`:
    a session attributed to the shared leaf reaches its one canonical ancestor chain (N1 -> N0), not
    the borrowing subsystem N2. Sessions come from `opindex` attribution keyed by op-id and can't be
    injected via `tree.save`, so we attach one to the shared leaf's op. Reverting `node_sessions` to
    raw children would leak the session onto N2 -- this asserts it does not."""
    import dataclasses

    from sgt.core import opindex
    from sgt.core.op import Attribution

    repo, leaf, real_op = _shared_feature_dag(tmp_path)

    real_ops = opindex.index_ops(repo)
    assert any(op.id == real_op for op in real_ops)  # the leaf's op is attributable

    def with_session(_repo):
        return [
            dataclasses.replace(op, attribution=op.attribution + (Attribution(sha=op.id, session="sess-1"),))
            if op.id == real_op else op
            for op in real_ops
        ]

    monkeypatch.setattr(opindex, "index_ops", with_session)

    v = map_view(repo)
    by_id = {n["id"]: n for n in v["nodes"]}
    assert by_id[leaf]["sessions"] == ["sess-1"]  # attributed directly to the shared leaf
    assert by_id["N1"]["sessions"] == ["sess-1"]  # its one canonical parent rolls it up
    assert by_id["N0"]["sessions"] == ["sess-1"]  # ...to the root
    assert by_id["N2"]["sessions"] == []  # the borrower does NOT (bites a raw-children revert)


def test_grid_view_joins_ops_into_feature_commit_cells(tmp_path):
    """U1/R5: `grid_view` is the canonical (op -> cell) join. Every cell holds exactly the ops that
    share one (feature_id, commit_index), and that join is faithful to `history_view` -- each
    cell's ops carry that cell's feature and commit-index, and every attributed op lands in exactly
    one cell."""
    from sgt.api import grid_view
    from sgt.lens.map import build_map

    repo = _mined(tmp_path, "mixed_coverage")
    build_map(repo)

    hv = history_view(repo, full=True)
    v = grid_view(repo)

    # the join reproduces history_view's attributed ops exactly, partitioned by cell.
    attributed = {op["id"] for op in hv["ops"] if op["feature_id"] is not None}
    from_cells = [oid for cell in v["cells"] for oid in cell["op_ids"]]
    assert sorted(from_cells) == sorted(attributed)          # every attributed op, once
    assert len(from_cells) == len(set(from_cells))           # no op in two cells

    op_by_id = {op["id"]: op for op in hv["ops"]}
    for cell in v["cells"]:
        assert cell["op_count"] == len(cell["op_ids"])
        assert cell["fidelity"] == "full"                    # nothing dropped in this fixture
        for oid in cell["op_ids"]:
            assert op_by_id[oid]["feature_id"] == cell["feature_id"]
            assert op_by_id[oid]["commit_index"] == cell["commit_index"]

    # cells sorted by (feature_id, commit_index); the feature roster covers every celled feature.
    assert v["cells"] == sorted(v["cells"], key=lambda c: (c["feature_id"], c["commit_index"]))
    assert set(v["features"]) == {c["feature_id"] for c in v["cells"]}
    assert v["commits"] == hv["commits"]


def test_grid_view_is_deterministic_across_calls(tmp_path):
    """A grid surface polls; the join must be byte-stable so a re-render never reshuffles."""
    from sgt.api import grid_view
    from sgt.lens.map import build_map

    repo = _mined(tmp_path, "mixed_coverage")
    build_map(repo)
    assert json.dumps(grid_view(repo), sort_keys=True) == json.dumps(grid_view(repo), sort_keys=True)


def test_grid_view_omits_unattributed_ops_before_a_tree_is_built(tmp_path):
    """An op with no feature (no `sgt map` yet) has no lane, so it produces no cell -- the same
    drop `graph_layout`/`episodes` already apply. The commit axis is still present."""
    from sgt.api import grid_view

    repo = _mined(tmp_path, "mixed_coverage")  # mined, but no build_map
    v = grid_view(repo)
    assert v["cells"] == []
    assert v["feature_count"] == 0
    assert v["op_count"] == 0
    assert v["commits"]  # the time axis exists regardless of feature assignment
    assert v["partial_commits"] == []


def test_grid_view_marks_no_partial_commits_until_a_reduction_is_recorded(tmp_path):
    """U1's fidelity field reads "full" for every cell until U2's producer records a real drop --
    forward-compatible, not a stub: `partial_commits` is empty and no cell is "partial"."""
    from sgt.api import grid_view
    from sgt.lens.map import build_map

    repo = _mined(tmp_path, "mixed_coverage")
    build_map(repo)
    v = grid_view(repo)
    assert v["partial_commits"] == []
    assert all(c["fidelity"] == "full" for c in v["cells"])


def test_coupling_flags_a_shared_residue_removal():
    """U4/R3: `_coupling_rows` names when a removal drops a residue op in a file a *different*
    surviving feature still occupies -- the shared whitespace the U32 corruption cuts through --
    so a preview shows which feature it reaches into. No flag when the shared file has no surviving
    other-feature entity, or when no residue is dropped."""
    from sgt.api import _coupling_rows

    foo = make_op({"a.py::foo": (None, "v1")}, {"a.py::foo": b"1"})
    bar = make_op({"a.py::bar": (None, "v1")}, {"a.py::bar": b"1"})
    residue = make_op({"a.py::__residue__::foo": (None, "v1")}, {"a.py::__residue__::foo": b" "})
    ops = [foo, bar, residue]
    op_leaf = {foo.id: "A", residue.id: "A", bar.id: "B"}  # residue follows foo's lane (U4)

    # revert A drops foo + its residue; B's bar survives in a.py -> coupling flagged.
    coupling = _coupling_rows(ops, op_leaf, {foo.id, residue.id}, {bar.id})
    assert coupling == [{"file": "a.py", "removed_feature": "A", "coupled_feature": "B"}]

    # no surviving other-feature entity in the file -> no coupling.
    assert _coupling_rows(ops, op_leaf, {foo.id, residue.id}, set()) == []
    # a removal that drops no residue -> no coupling (nothing shared was cut).
    assert _coupling_rows(ops, op_leaf, {foo.id}, {bar.id}) == []


def test_grid_view_marks_partial_commits_over_a_fork(tmp_path):
    """U2/R6 end to end: once `_record_fidelity` records a fork's dropped commits, `grid_view`
    reports them in `partial_commits` and marks every cell at those commit-indices "partial" --
    the commit couldn't be fully reconstructed, so its whole column is flagged."""
    from sgt.api import grid_view
    from sgt.core import sync
    from sgt.lens.map import build_map
    from tests.core.test_sync import _BASE, _edit_and_commit, _push, _two_clones

    a, b = _two_clones(tmp_path, _BASE)
    _edit_and_commit(a, "main.py", "def foo():\n    return 999\n\n\ndef bar():\n    return 2\n", "A: rework foo")
    _push(a)
    _edit_and_commit(b, "main.py", "def foo():\n    return 42\n\n\ndef bar():\n    return 2\n", "B: rework foo")
    sync.sync(b, remote="origin", branch="main")
    get(b)          # record fidelity for the post-fork ideal
    build_map(b)    # so surviving ops land in cells

    v = grid_view(b)
    assert v["partial_commits"]  # the fork's commits are flagged
    partial = set(v["partial_commits"])
    for cell in v["cells"]:
        assert cell["fidelity"] == ("partial" if cell["commit_index"] in partial else "full")


def test_grid_view_surfaces_a_pending_plan_prediction_as_a_ghost(tmp_path):
    """A pending plan step predicting a feature is a ghost cell -- the only place a prediction
    reaches the grid (off-chain hollows never enter the ideal). `known_feature` flags whether the
    predicted lane still exists."""
    from sgt.api import grid_view
    from sgt.lens.map import build_map

    repo = _mined(tmp_path, "mixed_coverage")
    build_map(repo)
    real_feature = next(iter(grid_view(repo)["features"]))

    table = plan_mod._load_sessions(repo)
    table["s1"] = {
        "plan_text": "1. extend it\n2. ghost step\n", "created_ts": 0.0, "last_activity_ts": 0.0,
        "status": "active", "baseline_op_ids": [],
        "steps": [
            {"hollow_id": "h0", "title": "extend it", "predicted_footprint": [],
             "predicted_feature": real_feature, "rationale": "", "status": "pending", "matched_op_ids": []},
            {"hollow_id": "h1", "title": "unknown lane", "predicted_footprint": [],
             "predicted_feature": "f-doesnotexist", "rationale": "", "status": "matched", "matched_op_ids": []},
        ],
    }
    plan_mod._save_sessions(repo, table)

    ghosts = grid_view(repo)["ghosts"]
    assert len(ghosts) == 1  # only the pending step; the matched one is not a ghost
    g = ghosts[0]
    assert g["feature_id"] == real_feature
    assert g["title"] == "extend it"
    assert g["known_feature"] is True


def test_grid_view_feature_roster_labels_match_the_map(tmp_path):
    """The label a lane shows on the grid is the same one `sgt map` shows -- `grid_view` resolves
    labels the same way `map_view` does (tree labels + authored overrides), just without the
    expensive `fused_graph` recompute."""
    from sgt.api import grid_view
    from sgt.lens.map import build_map

    repo = _mined(tmp_path, "mixed_coverage")
    build_map(repo)
    map_labels = {n["id"]: n["label"] for n in map_view(repo)["nodes"]}
    for fid, roster in grid_view(repo)["features"].items():
        assert roster["label"] == map_labels[fid]


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
        "save_preview", "oracle_verdict", "proposals",
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
    assert v["save_preview"] == save_preview_view(repo)
    assert v["oracle_verdict"] == verdict_for(repo, current_ideal(repo))
    assert v["proposals"] == []  # nothing proposed in this fixture


def test_save_preview_view_splits_affected_features_from_new_work(tmp_path):
    """The in-situ save preview answers "which features gain ops if I save now". A clean tree has
    nothing pending; uncommitted work that reworks an existing feature's symbol lands in `affected`
    (attributed to that feature's leaf), while a brand-new symbol -- a member of no built leaf --
    falls into the `new_work` bucket. `total_op_count` accounts for both."""
    from sgt.lens.map import build_map
    from sgt.lens.tree import load as load_tree

    repo = tmp_path / "repo"
    gb, _ = init_store(repo)
    (repo / "a.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    gb.commit_all("add foo")
    get(repo)
    build_map(repo)

    # the leaf that owns the committed symbol -- affected work should attribute here
    nodes = load_tree(repo)["nodes"]
    foo_leaf = next(nid for nid, nd in nodes.items()
                    if not nd["children"] and "a.py::foo" in nd["members"])

    # clean tree: nothing would land on save
    assert save_preview_view(repo) == {"affected": [], "new_work_count": 0, "total_op_count": 0}

    # uncommitted: rework the existing symbol (-> its feature) + add a brand-new one (-> new work)
    (repo / "a.py").write_text(
        "def foo():\n    return 2\n\n\ndef bar():\n    return 3\n", encoding="utf-8")

    v = save_preview_view(repo)
    assert v["total_op_count"] > 0
    affected_ids = {row["feature_id"] for row in v["affected"]}
    assert foo_leaf in affected_ids  # the reworked symbol's feature is flagged
    assert v["new_work_count"] >= 1  # `bar` belongs to no built leaf
    # the split is exhaustive: every pending op is either attributed or new work
    attributed = sum(row["op_count"] for row in v["affected"])
    assert attributed + v["new_work_count"] == v["total_op_count"]


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
    line span, decoupled from the session. The session carries a second, never-built step so it
    stays active after the first match confirms -- that partial-progress state is exactly when
    `plan_view` surfaces a matched step's spans (a fully-matched session completes and drops off)."""
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
    # A second step predicting an entity that never gets built: it stays pending, keeping the
    # session active so its already-matched sibling step still surfaces through `plan_view`.
    fp2 = {"a.py::qux": (None, plan_mod._PENDING), "__plan__::s1::step1": (None, plan_mod._PENDING)}
    hollow2 = make_op(fp2, {}, kind="planned", off_chain=True, intent="build qux")
    store.add_hollow(hollow2)
    table = plan_mod._load_sessions(repo)
    table["s1"] = {
        "plan_text": "1. touch foo\n2. build qux\n", "created_ts": 0.0, "last_activity_ts": 0.0,
        "status": "active", "baseline_op_ids": baseline,
        "steps": [{
            "hollow_id": hollow.id, "title": "touch foo", "predicted_footprint": ["a.py::foo"],
            "predicted_feature": None, "rationale": "", "status": "pending", "matched_op_ids": [],
        }, {
            "hollow_id": hollow2.id, "title": "build qux", "predicted_footprint": ["a.py::qux"],
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


def _seed_plan_session(repo, gb, *, session_id, last_activity_ts, claude_session_id=None):
    """Seed one active, single-pending-step plan session directly into `plan_sessions.json` (the
    same shape `intake` writes) so a test can control `last_activity_ts` deterministically.
    `baseline_op_ids` is the store's current op set, so no already-mined op is ever a candidate."""
    store = Store(repo)
    baseline = sorted(op.id for op in store.all_ops())
    footprint = {"a.py::foo": (None, plan_mod._PENDING),
                 f"__plan__::{session_id}::step0": (None, plan_mod._PENDING)}
    hollow = make_op(footprint, {}, kind="planned", off_chain=True, intent="touch foo")
    store.add_hollow(hollow)
    table = plan_mod._load_sessions(repo)
    table[session_id] = {
        "plan_text": "1. touch foo\n", "created_ts": 0.0, "last_activity_ts": last_activity_ts,
        "status": "active", "claude_session_id": claude_session_id, "baseline_op_ids": baseline,
        "steps": [{
            "hollow_id": hollow.id, "title": "touch foo", "predicted_footprint": ["a.py::foo"],
            "predicted_feature": None, "rationale": "", "status": "pending", "matched_op_ids": [],
        }],
    }
    plan_mod._save_sessions(repo, table)


def test_plan_view_derives_building_and_stalled_from_activity_and_candidates(tmp_path):
    """A pending plan is `building` while recently active, `stalled` once it goes quiet past
    STALLED_SECONDS with no work flowing toward it, and `building` again -- age notwithstanding --
    the moment a checkpoint candidate names it. `claude_session_id` is surfaced for the Resume
    hand-off, and `pending_count`/`remaining_titles` come along without a `full` fetch."""
    import time

    from sgt.loop.plan import STALLED_SECONDS

    repo = tmp_path / "repo"
    gb, _ = init_store(repo)
    (repo / "a.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    gb.commit_all("add foo")
    get(repo)

    # Recent activity, pending step, no candidate -> building.
    _seed_plan_session(repo, gb, session_id="fresh", last_activity_ts=time.time(),
                       claude_session_id="sess-abc")
    session = plan_view(repo)["sessions"][0]
    assert session["derived_status"] == "building"
    assert session["pending_count"] == 1
    assert session["remaining_titles"] == ["touch foo"]
    assert session["claude_session_id"] == "sess-abc"

    # Same session backdated well past STALLED_SECONDS with still no candidate -> stalled.
    table = plan_mod._load_sessions(repo)
    table["fresh"]["last_activity_ts"] = time.time() - STALLED_SECONDS - 60
    plan_mod._save_sessions(repo, table)
    assert plan_view(repo)["sessions"][0]["derived_status"] == "stalled"

    # Commit work matching the predicted footprint -> a live candidate names the session, so it
    # reads `building` again despite the stale activity timestamp (candidate overrides age).
    (repo / "a.py").write_text("def foo():\n    return 2\n", encoding="utf-8")
    gb.commit_all("touch foo")
    get(repo)
    view = plan_view(repo)
    assert view["checkpoint"]["matches"], "expected a checkpoint candidate for the matching work"
    assert view["sessions"][0]["derived_status"] == "building"


def test_now_view_clean_repo_reports_clean_next_action(tmp_path):
    """A mined, clean repo has nothing in flight, nothing needing the user, and a `clean`
    next-action. The four sections are always present so the surfaces can render unconditionally."""
    repo = _mined(tmp_path, "mixed_coverage")
    v = now_view(repo)
    assert set(v) == {"in_flight", "needs_you", "recently_done", "context", "next_action"}
    assert v["in_flight"] == {"affected": [], "new_work_count": 0, "total_op_count": 0}
    assert v["needs_you"] == {"forks": [], "reviews": [], "stalled_plans": []}
    assert v["next_action"]["kind"] == "clean"
    assert v["next_action"]["command"] is None
    assert v["recently_done"]  # the fixture's commits show up as recently done


def test_now_view_dirty_tree_recommends_save(tmp_path):
    """Uncommitted work puts ops in flight and makes `sgt save` the next action."""
    repo = tmp_path / "repo"
    gb, _ = init_store(repo)
    (repo / "a.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    gb.commit_all("add foo")
    get(repo)
    (repo / "a.py").write_text("def foo():\n    return 2\n", encoding="utf-8")

    v = now_view(repo)
    assert v["in_flight"]["total_op_count"] > 0
    assert v["next_action"] == {"kind": "save", "command": "sgt save", "target": None,
                                "label": f"save {v['in_flight']['total_op_count']} pending op(s)"}


def test_now_view_include_preview_false_skips_the_mine(tmp_path):
    """`include_preview=False` returns an empty in-flight block without touching the working tree,
    so a dirty tree is NOT reported -- the seam that lets a cheap feed-tick skip the save preview."""
    repo = tmp_path / "repo"
    gb, _ = init_store(repo)
    (repo / "a.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    gb.commit_all("add foo")
    get(repo)
    (repo / "a.py").write_text("def foo():\n    return 2\n", encoding="utf-8")

    v = now_view(repo, include_preview=False)
    assert v["in_flight"] == {"affected": [], "new_work_count": 0, "total_op_count": 0}
    assert v["next_action"]["kind"] == "clean"  # save rung never fires without the preview


def test_now_view_stalled_plan_recommends_claude_resume(tmp_path):
    """A stalled plan session outranks a clean tree: the next action is Claude's own `--resume`
    (there is no `sgt plan resume` verb), targeting the stalled session."""
    import time

    from sgt.loop.plan import STALLED_SECONDS

    repo = tmp_path / "repo"
    gb, _ = init_store(repo)
    (repo / "a.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    gb.commit_all("add foo")
    get(repo)
    _seed_plan_session(repo, gb, session_id="s1",
                       last_activity_ts=time.time() - STALLED_SECONDS - 60,
                       claude_session_id="sess-xyz")

    v = now_view(repo)
    assert len(v["needs_you"]["stalled_plans"]) == 1
    assert v["needs_you"]["stalled_plans"][0]["session_id"] == "s1"
    assert v["next_action"]["kind"] == "resume_plan"
    assert v["next_action"]["command"] == "claude --resume sess-xyz"
    assert v["next_action"]["target"] == "s1"


def test_now_view_open_fork_outranks_everything_and_reuses_its_remedy(tmp_path):
    """An open fork is the top rung: it blocks even a dirty tree, and the recommended command is
    the fork record's OWN remedy (not a hardcoded verb)."""
    from sgt import state

    repo = tmp_path / "repo"
    gb, _ = init_store(repo)
    (repo / "a.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    gb.commit_all("add foo")
    get(repo)
    (repo / "a.py").write_text("def foo():\n    return 2\n", encoding="utf-8")  # dirty too
    state.save_json(repo, "forks", [
        {"symbol": "a.py::foo", "tips": ["x", "y"], "remedy": "sgt merge-op a.py::foo"},
    ])

    v = now_view(repo)
    assert len(v["needs_you"]["forks"]) == 1
    assert v["next_action"] == {"kind": "resolve_fork", "command": "sgt merge-op a.py::foo",
                                "target": "a.py::foo", "label": "resolve fork on a.py::foo"}


def test_now_view_context_carries_recent_activity_newest_first(tmp_path):
    """The context block surfaces the live agent-action feed, newest first -- the "what just
    happened" signal the `PostToolUse` hook writes."""
    from sgt.intent import activity

    repo = _mined(tmp_path, "mixed_coverage")
    activity.record_activity(repo, tool="Edit", file="a.py", ts=1.0)
    activity.record_activity(repo, tool="Write", file="b.py", ts=2.0)

    v = now_view(repo)
    feed = v["context"]["activity"]
    assert [e["file"] for e in feed] == ["b.py", "a.py"]


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


def test_blame_view_on_an_uncovered_working_file_is_not_an_error(tmp_path):
    """A working-tree file sgt has no op for (a doc like JOURNAL.md, an untracked config) has no
    semantic blame -- a valid empty answer, not a failure. `blame_view` must not stamp an `error`
    key for it: that key flips the CLI `--json` exit code to 1, which the editor's per-file blame
    surfaces as a repeated "Command failed" for every non-code tab the user focuses. It reports
    `covered: False` with empty spans instead. A genuinely absent path still gets an `error`."""
    from sgt.api import blame_view

    repo = _mined(tmp_path, "mixed_coverage")
    (repo / "JOURNAL.md").write_text("# notes\n")  # exists on disk, but no op produces it

    v = blame_view(repo, "JOURNAL.md")
    assert v["covered"] is False
    assert v["spans"] == [] and v["features"] == {}
    assert "error" not in v  # not a failure -> `_emit_json` exits 0

    missing = blame_view(repo, "does-not-exist.py")
    assert missing.get("error")  # genuinely absent path -> a real error


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


def test_so_what_layer_fallout_is_act_required_only_and_carry_is_a_hidden_count(tmp_path):
    """The consequence-pane contract: `fallout` carries exactly the toggleable blast dependents
    (never carry, never foundation), `carry_count` counts the hidden mechanical repoints, and
    `reversible` is True for an ideal edit -- so the pane can lead with "so what" and footnote the
    carries without ever listing them."""
    from sgt.api import verb_preview_view

    repo = _chain_repo(tmp_path)
    ops = Store(repo).all_ops()
    user_op = next(o for o in ops if "b.py::user" in o.footprint)

    view = verb_preview_view(repo, "revert", user_op.id)
    frontier = view["frontier"]
    blast = {r["op_id"] for r in frontier if r["bucket"] == "blast"}
    carry = {r["op_id"] for r in frontier if r["bucket"] == "carry"}

    fallout_ids = {r["op_id"] for r in view["fallout"] if r["kind"] == "blast"}
    assert fallout_ids == blast  # every act-required blast op is in the fallout
    assert fallout_ids.isdisjoint(carry)  # carry is never in the fallout
    assert all(r["kind"] == "blast" for r in view["fallout"])  # no fork row on a clean revert
    assert view["carry_count"] == len(carry) > 0  # the chain has a transitive dependent
    assert view["reversible"] is True
    assert view["so_what"].startswith("b.py::user will break —")


# -- focus_subgraph: the "Focus & Morph" preview contract ---------------------------------------

def _per_file_leaf_tree(tmp_path):
    """The small-repo clusterer fuses the whole `_chain_repo` into one Leiden community, so to test a
    *multi*-feature subgraph we hand-reshape the built tree to one leaf per file (mirroring
    `_shared_feature_dag`): a.py→f-a, b.py→f-b, ... . Reverting `user` (b.py) then removes ops from
    the user/caller/deep leaves (blast/target) and leaves the helper leaf they build on (foundation).
    Returns (repo, ops)."""
    from sgt.lens import tree as tree_mod
    from sgt.lens.map import build_map

    repo = _chain_repo(tmp_path)
    build_map(repo)
    ops = Store(repo).all_ops()

    def leaf_of(op) -> str:
        path = next((s.partition("::")[0] for s in op.footprint), "?")
        return "f-" + path.removesuffix(".py")

    op_leaf = {op.id: leaf_of(op) for op in ops}
    leaves = sorted(set(op_leaf.values()))

    def node(nid, parent, children, label):
        return {"id": nid, "parent": parent, "children": list(children), "depth": 0,
                "members": [], "size": 1, "dir": "", "label": label, "why": "", "split_reason": None}

    res = tree_mod.load(repo)
    res["nodes"] = {"N0": node("N0", None, leaves, "root"),
                    **{lf: node(lf, "N0", [], lf) for lf in leaves}}
    res["roots"] = ["N0"]
    res["op_leaf"] = op_leaf
    tree_mod.save(repo, res)
    return repo, ops


def test_focus_subgraph_revert_splits_target_blast_and_foundation_with_before_after_counts(tmp_path):
    """The keystone morph contract: a revert's focus subgraph names exactly the affected features,
    one spotlight `target`, the rest of the shrinking leaves as `blast`, and the live prerequisite the
    revert is built on as an unchanged `foundation` -- each carrying its op-count before and after, so
    a renderer can dim the field and morph just these nodes instead of listing 64 op-ids."""
    from sgt.api import map_view, verb_preview_view

    repo, ops = _per_file_leaf_tree(tmp_path)
    user_op = next(o for o in ops if "b.py::user" in o.footprint)

    view = verb_preview_view(repo, "revert", user_op.id)
    focus = view["focus"]
    assert focus["so_what"] == view["so_what"]  # the headline is the same line the pane leads with
    by_fid = {n["feature_id"]: n for n in focus["nodes"]}

    # helper's leaf is the prerequisite the revert stands on: lit as foundation, its count untouched.
    assert by_fid["f-a"]["role"] == "foundation"
    assert by_fid["f-a"]["ops_before"] == by_fid["f-a"]["ops_after"] > 0

    roles = [n["role"] for n in focus["nodes"]]
    assert roles.count("target") == 1  # exactly one spotlight node
    target = next(n for n in focus["nodes"] if n["role"] == "target")
    assert target["ops_after"] < target["ops_before"]  # the acted-on leaf shrinks
    assert all(n["ops_after"] < n["ops_before"] for n in focus["nodes"] if n["role"] == "blast")
    assert set(roles) <= {"target", "blast", "foundation"}

    # the per-node deltas account for exactly the reverted ops (nothing added on a revert).
    total_dropped = sum(n["ops_before"] - n["ops_after"] for n in focus["nodes"])
    assert total_dropped == len(view["removed"])

    # edges are the map's cross-feature edges restricted to focus members; context is the rest.
    fids = set(by_fid)
    assert all(e["a"] in fids and e["b"] in fids for e in focus["edges"])
    assert focus["context_count"] == map_view(repo)["feature_count"] - len(focus["nodes"])


def test_focus_subgraph_restore_grows_the_restored_features_lane(tmp_path):
    """The reverse morph: a `restore` adds ops back, so the touched leaf's `ops_after` exceeds its
    `ops_before` and it grows against the dim field -- nothing shrinks. Built from a preview whose
    `after_ids` re-adds a removed op, so the direction flips without a full apply round-trip."""
    from sgt.api import focus_subgraph
    from sgt.core import verbs

    repo, ops = _per_file_leaf_tree(tmp_path)
    deep_op = next(o for o in ops if "d.py::deep" in o.footprint)
    all_ids = frozenset(o.id for o in ops)

    preview = verbs._preview("restore", deep_op.id, all_ids - {deep_op.id}, all_ids, ops)
    focus = focus_subgraph(preview, repo)
    by_fid = {n["feature_id"]: n for n in focus["nodes"]}

    # the restored op's leaf is the one acted-on lane, and it grows.
    assert by_fid["f-d"]["role"] == "target"
    assert by_fid["f-d"]["ops_after"] > by_fid["f-d"]["ops_before"]
    assert all(n["role"] != "blast" for n in focus["nodes"])  # nothing shrinks on a pure restore
    assert sum(n["ops_after"] - n["ops_before"] for n in focus["nodes"]) == 1


def test_focus_subgraph_is_empty_when_no_feature_tree_is_built(tmp_path):
    """Degrade cleanly: before `sgt map` builds the tree there is no `op_leaf` to roll ops up
    through, so the subgraph is empty (`nodes: []`) and the renderer falls back to the `so_what`
    headline alone -- never an error."""
    from sgt.api import focus_subgraph
    from sgt.core import verbs

    repo = _chain_repo(tmp_path)  # mined, but no `build_map` -> no op_leaf
    ops = Store(repo).all_ops()
    user_op = next(o for o in ops if "b.py::user" in o.footprint)
    all_ids = frozenset(o.id for o in ops)

    preview = verbs._preview("revert", user_op.id, all_ids, all_ids - {user_op.id}, ops)
    focus = focus_subgraph(preview, repo, so_what="X")
    assert focus == {"so_what": "X", "nodes": [], "edges": [], "context_count": 0}
