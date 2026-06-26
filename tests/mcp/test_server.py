"""MCP server — JSON-RPC dispatch and the agent loop, driven through ``handle_request``.

No subprocess: we call the pure dispatcher directly. The headline is the *checkpoint loop* — an
agent edits a file on disk, then checkpoints it into the semantic tree with a declared intent,
and the drift is gone (the tree now reproduces the edit).
"""

from __future__ import annotations

import json

from sgt.effects.model import Effect
from sgt.mcp import handle_request
from sgt.project import Project
from sgt.store.graph import Node, NodeKind


def _seed(tmp_path) -> str:
    p = Project.init(tmp_path, replica_id="R1")
    p.add_feature(Node("shorten", NodeKind.CAPABILITY, "url shortener"),
                  [Effect.add_def("app.py", "shorten", "def shorten(u):\n    return u[:6]")])
    p.commit("feat: shorten", node_id="shorten")
    return str(tmp_path)


def _call(repo, name, arguments=None, mid=1):
    msg = {"jsonrpc": "2.0", "id": mid, "method": "tools/call",
           "params": {"name": name, "arguments": arguments or {}}}
    resp = handle_request(repo, msg)
    payload = json.loads(resp["result"]["content"][0]["text"])
    return resp, payload


# -- protocol handshake -----------------------------------------------------
def test_initialize_advertises_tools_capability(tmp_path):
    repo = _seed(tmp_path)
    resp = handle_request(repo, {"jsonrpc": "2.0", "id": 0, "method": "initialize",
                                 "params": {"protocolVersion": "2024-11-05"}})
    assert resp["result"]["capabilities"]["tools"] == {}
    assert resp["result"]["serverInfo"]["name"] == "semi-git"


def test_initialized_notification_has_no_response(tmp_path):
    repo = _seed(tmp_path)
    assert handle_request(repo, {"jsonrpc": "2.0", "method": "notifications/initialized"}) is None


def test_tools_list_advertises_full_agent_surface(tmp_path):
    repo = _seed(tmp_path)
    resp = handle_request(repo, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    names = {t["name"] for t in resp["result"]["tools"]}
    # full parity with the CLI's mutating verbs — a regression dropping any is caught here
    assert {"sgt_graph", "sgt_show", "sgt_status", "sgt_conflicts", "sgt_plan", "sgt_checkpoint",
            "sgt_revert", "sgt_restore", "sgt_reconcile", "sgt_init"} <= names


def test_unknown_method_is_method_not_found(tmp_path):
    repo = _seed(tmp_path)
    resp = handle_request(repo, {"jsonrpc": "2.0", "id": 9, "method": "nonsense"})
    assert resp["error"]["code"] == -32601


def test_unknown_tool_is_reported_as_tool_error(tmp_path):
    repo = _seed(tmp_path)
    resp, payload = _call(repo, "sgt_nope")
    assert resp["result"]["isError"] is True and "unknown tool" in payload["error"]


# -- read tools -------------------------------------------------------------
def test_graph_lists_nodes(tmp_path):
    repo = _seed(tmp_path)
    _, payload = _call(repo, "sgt_graph")
    assert payload["count"] == 1 and payload["nodes"][0]["id"] == "shorten"


def test_show_resolves_fuzzy_ref(tmp_path):
    repo = _seed(tmp_path)
    _, payload = _call(repo, "sgt_show", {"ref": "shorten"})
    assert payload["id"] == "shorten" and any(e["target"] == "shorten" for e in payload["effects"])


def test_conflicts_empty_on_clean_tree(tmp_path):
    repo = _seed(tmp_path)
    _, payload = _call(repo, "sgt_conflicts")
    assert payload == {"conflicts": [], "count": 0}


# -- the checkpoint loop (the agent-facing reconcile path) ------------------
def test_checkpoint_requires_intent(tmp_path):
    repo = _seed(tmp_path)
    resp, payload = _call(repo, "sgt_checkpoint", {})
    assert resp["result"]["isError"] and "intent" in payload["error"]


def test_checkpoint_distills_disk_edit_under_declared_intent(tmp_path):
    repo = _seed(tmp_path)
    # an external agent edits the file on disk ...
    (tmp_path / "app.py").write_text("def shorten(u):\n    return u[:8]\n")
    _, before = _call(repo, "sgt_status")
    assert before["drift"]["any"]                           # drift present before checkpoint
    # ... then checkpoints its work with a declared intent
    _, rep = _call(repo, "sgt_checkpoint", {"intent": "shorten to 8 chars"})
    assert rep["ok"] and len(rep["landed"]) == 1            # a fix node landed
    # the tree now reproduces the edit and the drift is gone
    _, after = _call(repo, "sgt_status")
    assert after["drift"]["any"] is False
    _, node = _call(repo, "sgt_show", {"ref": rep["landed"][0]})
    assert node["intent"] == "shorten to 8 chars"           # intent captured live, not guessed
    assert "shorten" in node["depends_on"]                  # anchored to the function it edits
    assert "u[:8]" in Project.open(tmp_path).materialize()["app.py"]


# -- graph verbs + the full agent loop through MCP --------------------------
def test_revert_tool_plugs_a_feature_out(tmp_path):
    repo = _seed(tmp_path)
    _, payload = _call(repo, "sgt_revert", {"ref": "shorten"})
    assert payload["ok"] and "shorten" in payload["landed"]
    assert Project.open(tmp_path).materialize() == {}


def test_restore_tool_plugs_a_feature_back_in(tmp_path):
    repo = _seed(tmp_path)
    _call(repo, "sgt_revert", {"ref": "shorten"})
    assert Project.open(tmp_path).materialize() == {}  # out of force
    _, payload = _call(repo, "sgt_restore", {"ref": "shorten"})
    assert payload["ok"] and "shorten" in payload["landed"]
    assert "def shorten" in Project.open(tmp_path).materialize().get("app.py", "")


def test_reconcile_tool_no_pending(tmp_path):
    repo = _seed(tmp_path)
    _, payload = _call(repo, "sgt_reconcile")
    assert payload["ok"] and "no pending" in payload["message"]


def test_init_tool_bootstraps_workspace(tmp_path):
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    _, payload = _call(str(fresh), "sgt_init")
    assert payload["ok"] and (fresh / ".sgt").exists()


def test_checkpoint_fulfills_flips_planned_via_mcp(tmp_path):
    repo = _seed(tmp_path)
    proj = Project.open(tmp_path)
    proj.add_plan([Node("greet", NodeKind.CAPABILITY, "add a greeter")], edges=[])
    proj.save()
    (tmp_path / "greet.py").write_text("def greet():\n    return 'hi'\n")
    _, payload = _call(repo, "sgt_checkpoint", {"intent": "greet returns hi", "fulfills": "greet"})
    assert payload["ok"] and "greet" in payload["fulfilled"]
    from sgt.store.graph import NodeStatus
    assert Project.open(tmp_path).graph.get("greet").status is NodeStatus.ACTIVE


def test_held_checkpoint_returns_witness_for_agent(tmp_path):
    # When a fulfill is held, the agent must get the witness back (why + against what) so it can act.
    repo = _seed(tmp_path)
    proj = Project.open(tmp_path)
    proj.add_plan([Node("reg", NodeKind.CAPABILITY, "register", needs=["missing"])], edges=[])
    proj.save()
    (tmp_path / "reg.py").write_text("def register():\n    return missing()\n")
    _, payload = _call(repo, "sgt_checkpoint", {"intent": "register", "fulfills": "reg"})
    assert "reg" in payload["quarantined"]
    assert payload["witnesses"]["reg"].get("reason")        # actionable detail, not just for humans
