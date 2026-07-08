"""MCP server — JSON-RPC dispatch over the kernel-backed tools (plan U7/U8/U9, flipped onto MCP
in U10). No subprocess: we call the pure dispatcher directly.
"""

from __future__ import annotations

import json

from sgt.mcp import handle_request
from sgt.store.gitbind import init_store


def _seed(tmp_path, n: int = 2) -> str:
    """A repo whose a.py::foo is a linear chain of `n` versions, one per commit."""
    gb, _ = init_store(tmp_path)
    for i in range(1, n + 1):
        (tmp_path / "a.py").write_text(f"def foo():\n    return {i}\n", encoding="utf-8")
        gb.commit_all(f"foo v{i}")
    return str(tmp_path)


def _call(repo, name, arguments=None, mid=1):
    msg = {"jsonrpc": "2.0", "id": mid, "method": "tools/call",
           "params": {"name": name, "arguments": arguments or {}}}
    resp = handle_request(repo, msg)
    payload = json.loads(resp["result"]["content"][0]["text"])
    return resp, payload


# -- protocol handshake -----------------------------------------------------
def test_initialize_advertises_tools_capability(tmp_path):
    repo = _seed(tmp_path, 1)
    resp = handle_request(repo, {"jsonrpc": "2.0", "id": 0, "method": "initialize",
                                 "params": {"protocolVersion": "2024-11-05"}})
    assert resp["result"]["capabilities"]["tools"] == {}
    assert resp["result"]["serverInfo"]["name"] == "semi-git"


def test_initialized_notification_has_no_response(tmp_path):
    repo = _seed(tmp_path, 1)
    assert handle_request(repo, {"jsonrpc": "2.0", "method": "notifications/initialized"}) is None


def test_tools_list_advertises_kernel_surface(tmp_path):
    repo = _seed(tmp_path, 1)
    resp = handle_request(repo, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    names = {t["name"] for t in resp["result"]["tools"]}
    # kernel parity with the CLI's registered verbs — a regression dropping any is caught here
    assert names == {"sgt_init", "sgt_log", "sgt_state", "sgt_diff", "sgt_fsck",
                      "sgt_revert", "sgt_restore", "sgt_oracle_run"}


def test_unknown_method_is_method_not_found(tmp_path):
    repo = _seed(tmp_path, 1)
    resp = handle_request(repo, {"jsonrpc": "2.0", "id": 9, "method": "nonsense"})
    assert resp["error"]["code"] == -32601


def test_unknown_tool_is_reported_as_tool_error(tmp_path):
    repo = _seed(tmp_path, 1)
    resp, payload = _call(repo, "sgt_nope")
    assert resp["result"]["isError"] is True and "unknown tool" in payload["error"]


# -- read tools -------------------------------------------------------------
def test_log_lists_mined_ops(tmp_path):
    repo = _seed(tmp_path, 1)
    _, payload = _call(repo, "sgt_log")
    assert payload["count"] >= 1
    assert any("a.py::foo" in [f["symbol"] for f in op["footprint"]] for op in payload["ops"])


def test_state_shows_frontier(tmp_path):
    repo = _seed(tmp_path, 1)
    _, payload = _call(repo, "sgt_state")
    assert "a.py::foo" in payload["frontier"]
    assert payload["oracle_configured"] is False


def test_diff_requires_both_refs(tmp_path):
    repo = _seed(tmp_path, 1)
    _, payload = _call(repo, "sgt_diff", {"ref_a": "HEAD"})
    assert "error" in payload


def test_fsck_reports_clean_store(tmp_path):
    repo = _seed(tmp_path, 1)
    _call(repo, "sgt_log")  # mine, so the store isn't empty
    _, payload = _call(repo, "sgt_fsck")
    assert payload["ok"] is True and payload["checked"] >= 1


# -- write tools --------------------------------------------------------------
def test_revert_tool_removes_the_upset(tmp_path):
    repo = _seed(tmp_path, 2)
    _, payload = _call(repo, "sgt_revert", {"ref": "a.py::foo"})
    assert payload["ok"] and payload["removed"]
    assert (tmp_path / "a.py").read_text() == "def foo():\n    return 1\n"


def test_revert_emit_previews_without_writing(tmp_path):
    repo = _seed(tmp_path, 2)
    _, payload = _call(repo, "sgt_revert", {"ref": "a.py::foo", "emit": True})
    assert payload["ok"]
    assert (tmp_path / "a.py").read_text() == "def foo():\n    return 2\n"  # untouched


def test_restore_tool_is_reverts_inverse(tmp_path):
    repo = _seed(tmp_path, 2)
    _call(repo, "sgt_revert", {"ref": "a.py::foo"})
    assert (tmp_path / "a.py").read_text() == "def foo():\n    return 1\n"
    _, payload = _call(repo, "sgt_restore", {"ref": "a.py::foo"})
    assert payload["ok"] and payload["added"]
    assert (tmp_path / "a.py").read_text() == "def foo():\n    return 2\n"


def test_revert_missing_ref_is_an_error(tmp_path):
    repo = _seed(tmp_path, 1)
    _, payload = _call(repo, "sgt_revert", {})
    assert "error" in payload


def test_init_tool_bootstraps_workspace(tmp_path):
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    import subprocess
    subprocess.run(["git", "init", "-q"], cwd=fresh, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=fresh, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=fresh, check=True)
    (fresh / "a.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "a.py"], cwd=fresh, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=fresh, check=True)

    _, payload = _call(str(fresh), "sgt_init")
    assert payload["ok"] and (fresh / ".sgt").exists()


def test_oracle_run_tool_with_no_config(tmp_path):
    repo = _seed(tmp_path, 1)
    _, payload = _call(repo, "sgt_oracle_run")
    assert payload["configured"] is False
