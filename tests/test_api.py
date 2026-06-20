"""The canonical JSON projection (sgt.api) consumed by the CLI --json mode, MCP, and the UIs."""

from sgt.api import blame_view, export_view, graph_view, show_view, status_view
from sgt.effects.model import Effect
from sgt.project import Project
from sgt.store.graph import Node, NodeKind


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
