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
    assert names == {"sgt_init", "sgt_log", "sgt_grid", "sgt_status", "sgt_diff", "sgt_advanced_fsck",
                      "sgt_revert", "sgt_restore", "sgt_advanced_oracle_run",
                      "sgt_plan_intake", "sgt_checkpoint", "sgt_drift", "sgt_plan_done",
                      "sgt_recall"}


def test_unknown_method_is_method_not_found(tmp_path):
    repo = _seed(tmp_path, 1)
    resp = handle_request(repo, {"jsonrpc": "2.0", "id": 9, "method": "nonsense"})
    assert resp["error"]["code"] == -32601


def test_unknown_tool_is_reported_as_tool_error(tmp_path):
    repo = _seed(tmp_path, 1)
    resp, payload = _call(repo, "sgt_nope")
    assert resp["result"]["isError"] is True and "unknown tool" in payload["error"]


def test_tool_call_response_text_has_no_indent_whitespace(tmp_path):
    """Part D: the JSON-RPC tool-call response body is `json.dumps(..., separators=(",", ":"))`,
    not `indent=2` -- a cold MCP process's response payload should carry no pretty-printing
    whitespace, whatever the tool's own shape."""
    repo = _seed(tmp_path, 1)
    msg = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
           "params": {"name": "sgt_log", "arguments": {}}}
    resp = handle_request(repo, msg)
    text = resp["result"]["content"][0]["text"]
    assert "\n" not in text and ": " not in text


# -- read tools -------------------------------------------------------------
def test_log_lists_mined_ops(tmp_path):
    repo = _seed(tmp_path, 1)
    _, payload = _call(repo, "sgt_log")
    assert payload["count"] >= 1
    assert any("a.py::foo" in op["symbols"] for op in payload["ops"])  # compact by default


def test_log_full_carries_footprint(tmp_path):
    repo = _seed(tmp_path, 1)
    _, payload = _call(repo, "sgt_log", {"full": True})
    assert any("a.py::foo" in [f["symbol"] for f in op["footprint"]] for op in payload["ops"])


def test_grid_returns_the_canonical_lane_commit_join(tmp_path):
    """U1: `sgt_grid` mines-on-contact then returns `grid_view` -- the commit axis, cells, ghosts,
    and fidelity marks. `sgt_log`'s op-DAG shape is untouched (KTD9), so the two are distinct."""
    from sgt.lens.map import build_map

    repo = _seed(tmp_path, 2)
    build_map(repo)  # populate op_leaf so ops land in cells
    _, grid = _call(repo, "sgt_grid")
    assert set(grid) == {"commits", "cells", "features", "ghosts", "partial_commits",
                         "commit_count", "op_count", "feature_count"}
    assert grid["commit_count"] == 2

    # tool_log still returns the op DAG, not the grid — the schema-stable contract (KTD9).
    _, log = _call(repo, "sgt_log")
    assert set(log) == {"count", "kinds", "truncated", "ops"}


def test_state_shows_frontier(tmp_path):
    repo = _seed(tmp_path, 1)
    _, payload = _call(repo, "sgt_status")
    assert payload["oracle_configured"] is False
    assert "frontier" not in payload  # compact by default


def test_state_full_carries_frontier(tmp_path):
    repo = _seed(tmp_path, 1)
    _, payload = _call(repo, "sgt_status", {"full": True})
    assert "a.py::foo" in payload["frontier"]


def test_diff_requires_both_refs(tmp_path):
    repo = _seed(tmp_path, 1)
    _, payload = _call(repo, "sgt_diff", {"ref_a": "HEAD"})
    assert "error" in payload


def test_fsck_reports_clean_store(tmp_path):
    repo = _seed(tmp_path, 1)
    _call(repo, "sgt_log")  # mine, so the store isn't empty
    _, payload = _call(repo, "sgt_advanced_fsck")
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
    _, payload = _call(repo, "sgt_advanced_oracle_run")
    assert payload["configured"] is False


# -- agentic loop tools (plan U14) -------------------------------------------
def _no_client(*args, **kwargs):
    raise RuntimeError("no client")


def test_plan_intake_tool_mints_steps_from_a_numbered_list(tmp_path, monkeypatch):
    from sgt.loop import plan as plan_mod

    monkeypatch.setattr(plan_mod, "get_client", _no_client)
    repo = _seed(tmp_path, 1)
    _, payload = _call(
        repo, "sgt_plan_intake", {"plan_text": "1. step one\n2. step two\n", "session_id": "s1"}
    )
    assert payload["session_id"] == "s1"
    assert payload["step_count"] == 2
    assert [s["title"] for s in payload["steps"]] == ["step one", "step two"]


def test_plan_intake_tool_requires_plan_text(tmp_path):
    repo = _seed(tmp_path, 1)
    _, payload = _call(repo, "sgt_plan_intake", {})
    assert "error" in payload


def test_checkpoint_tool_previews_then_confirms(tmp_path, monkeypatch):
    from pathlib import Path

    from sgt.core.op import make_op
    from sgt.core.store import Store
    from sgt.loop import plan as plan_mod
    from sgt.store.gitbind import GitBinding

    monkeypatch.setattr(plan_mod, "get_client", _no_client)
    repo = _seed(tmp_path, 1)  # a.py::foo == "return 1"
    repo_path = Path(repo)
    store = Store(repo)
    baseline = sorted(op.id for op in store.all_ops())

    footprint = {"a.py::foo": (None, plan_mod._PENDING), "__plan__::s1::step0": (None, plan_mod._PENDING)}
    hollow = make_op(footprint, {}, kind="planned", off_chain=True, intent="touch foo")
    store.add_hollow(hollow)
    table = plan_mod._load_sessions(repo_path)
    table["s1"] = {
        "plan_text": "1. touch foo\n", "created_ts": 0.0, "last_activity_ts": 0.0, "status": "active",
        "baseline_op_ids": baseline,
        "steps": [{
            "hollow_id": hollow.id, "title": "touch foo", "predicted_footprint": ["a.py::foo"],
            "predicted_feature": None, "rationale": "", "status": "pending", "matched_op_ids": [],
        }],
    }
    plan_mod._save_sessions(repo_path, table)

    (repo_path / "a.py").write_text("def foo():\n    return 99\n", encoding="utf-8")
    GitBinding(repo).commit_all("touch foo")

    _, preview = _call(repo, "sgt_checkpoint")
    assert len(preview["matches"]) == 1
    group = preview["matches"][0]
    assert group["session_id"] == "s1"

    _, confirmed = _call(
        repo, "sgt_checkpoint",
        {"confirm": [{"hollow_ids": group["hollow_ids"], "op_ids": group["op_ids"]}]},
    )
    assert confirmed["matches"] == []  # the step is no longer pending

    from sgt.api import plan_view

    # Confirming the only step completes the session, so it leaves the active review surface
    # (`plan_view`); its matched step is recorded in the full table for provenance.
    assert plan_view(repo, full=True)["sessions"] == []
    assert plan_mod._load_sessions(repo_path)["s1"]["status"] == "completed"
    assert plan_mod._load_sessions(repo_path)["s1"]["steps"][0]["status"] == "matched"


def test_drift_tool_reports_nothing_with_no_active_session(tmp_path):
    repo = _seed(tmp_path, 2)  # two commits, but no plan session at all
    _, payload = _call(repo, "sgt_drift")
    assert payload["op_ids"] == []  # compact by default


def test_drift_full_carries_entries(tmp_path):
    repo = _seed(tmp_path, 2)
    _, payload = _call(repo, "sgt_drift", {"full": True})
    assert payload["entries"] == []


# -- agent-facing docs must agree with the tool contract ------------------------------------------

def _skill_text() -> str | None:
    from pathlib import Path
    p = Path(__file__).resolve().parents[2] / ".claude" / "skills" / "sgt-plan" / "SKILL.md"
    return p.read_text(encoding="utf-8") if p.is_file() else None


def test_plan_skill_and_mcp_tool_name_the_same_session_id_variable():
    """The `sgt-plan` skill told agents to store `$CLAUDE_CODE_BRIDGE_SESSION_ID` while the
    `UserPromptSubmit` hook keys captured prompts by `$CLAUDE_CODE_SESSION_ID`. The two never
    matched, so an agent that followed the skill silently lost both the prompt-to-commit join and
    the `claude --resume` handle -- with nothing failing loudly enough to notice.

    Both surfaces are prose read by an agent, so nothing but a test keeps them honest."""
    skill = _skill_text()
    if skill is None:
        import pytest
        pytest.skip("skill file not present in this checkout")

    from sgt.mcp.server import TOOLS

    intake_schema = TOOLS["sgt_plan_intake"][1]
    tool_desc = intake_schema["properties"]["claude_session_id"]["description"]

    assert "CLAUDE_CODE_SESSION_ID" in tool_desc
    assert "CLAUDE_CODE_SESSION_ID" in skill, "the skill must name the id the hook actually keys by"
    # The bridge id may only appear as an explicit warning, never as the thing to pass.
    for surface, text in (("skill", skill), ("tool description", tool_desc)):
        if "CLAUDE_CODE_BRIDGE_SESSION_ID" in text:
            idx = text.index("CLAUDE_CODE_BRIDGE_SESSION_ID")
            # Strip markdown emphasis so the check reads meaning, not formatting: the warning is
            # equally a warning whether it is written "do not" or "do **not**".
            window = text[max(0, idx - 200):idx].lower().replace("*", "").replace("_", "")
            assert any(w in window for w in ("do not", "don't", "never")), (
                f"the {surface} mentions the bridge id without warning against it"
            )
