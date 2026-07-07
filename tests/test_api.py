"""The canonical JSON projection (sgt.api) consumed by the CLI --json mode, MCP, and the UIs."""

import json

from sgt.api import (
    blame_view,
    export_view,
    graph_view,
    ideal_diff_view,
    oplog_view,
    show_view,
    state_view,
    status_view,
)
from sgt.core.lens import get
from sgt.effects.model import Effect
from sgt.project import Project
from sgt.store.graph import Node, NodeKind, NodeStatus
from tests.laws import corpus


def _proj(tmp_path):
    proj = Project.init(tmp_path)
    proj.add_feature(
        Node(id="base", kind=NodeKind.CAPABILITY, intent="base capability"),
        [Effect.add_def("m.py", "base", "def base():\n    return 1")],
    )
    proj.add_feature(
        Node(id="user", kind=NodeKind.CAPABILITY, intent="uses base"),
        [Effect.add_def("m.py", "user", "def user():\n    return base()")],
    )
    proj.save()
    return proj


def test_graph_view_has_nodes_and_typed_edges(tmp_path):
    g = graph_view(_proj(tmp_path))
    assert g["count"] == 2
    ids = {n["id"] for n in g["nodes"]}
    assert ids == {"base", "user"}
    # user depends on base (inferred), surfaced as a typed edge
    assert {"src": "user", "dst": "base", "type": "depends_on"} in g["edges"]
    user = next(n for n in g["nodes"] if n["id"] == "user")
    assert user["depends_on"] == ["base"]


def test_show_view_resolves_fuzzy_and_lists_effects(tmp_path):
    v = show_view(_proj(tmp_path), "uses base")
    assert v["id"] == "user"
    assert any(e["op"] == "add_def" and e["target"] == "user" for e in v["effects"])
    assert v["dependents"] == []


def test_show_view_missing_ref_is_error(tmp_path):
    v = show_view(_proj(tmp_path), "nonexistent-thing")
    assert "error" in v


def test_status_view_shape(tmp_path):
    s = status_view(_proj(tmp_path))
    assert s["nodes"] == 2
    assert {f["path"] for f in s["files"]} == {"m.py"}
    assert s["drift"]["any"] in (True, False)


def test_status_view_surfaces_planned_work_without_drift(tmp_path):
    """A reopened session with planned-but-unimplemented decisions must not read as 'nothing to do'.

    PLANNED nodes carry no effects, so a clean working tree has no drift — the agent's only
    status probe must still surface them as outstanding work, or 'continue prior session' breaks.
    """
    proj = _proj(tmp_path)
    proj.graph.add_node(
        Node(id="enhance", kind=NodeKind.CAPABILITY, intent="enhance preprocess",
             status=NodeStatus.PLANNED, provides=["preprocess"], needs=["retrieve"])
    )
    proj.write_working_tree()  # the reopened-session state: tree in sync, plan pending
    proj.save()

    s = status_view(proj)
    assert s["drift"]["any"] is False  # nothing on disk changed
    assert s["pending"]["count"] == 1
    planned = s["pending"]["planned"]
    assert [n["id"] for n in planned] == ["enhance"]
    assert planned[0]["intent"] == "enhance preprocess"
    assert planned[0]["provides"] == ["preprocess"]
    assert planned[0]["needs"] == ["retrieve"]


def test_blame_view_carries_node_metadata(tmp_path):
    v = blame_view(_proj(tmp_path), "m.py")
    assert v["file"] == "m.py"
    owners = {s["node_id"] for s in v["spans"] if s["node_id"]}
    assert owners == {"base", "user"}
    assert v["nodes"]["base"]["intent"] == "base capability"


def test_blame_view_unmanaged_file(tmp_path):
    v = blame_view(_proj(tmp_path), "not-a-file.py")
    assert "error" in v and v["spans"] == []


def test_export_view_includes_effects_per_node(tmp_path):
    e = export_view(_proj(tmp_path))
    base = next(n for n in e["nodes"] if n["id"] == "base")
    assert any(eff["op"] == "add_def" for eff in base["effects"])


# -- kernel views (plan U7): oplog_view / state_view / ideal_diff_view over the op-ideal store.
# These read git-repo fixtures (tests/laws/corpus.py, deterministic pinned SHAs) mined by
# `sgt.core.lens.get`, not the legacy in-memory Project above.


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
    assert v["oracle_verdict"] is None  # the oracle (U9) is not built yet
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
