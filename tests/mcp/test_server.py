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
    # kernel parity with the CLI's registered verbs — a regression dropping any is caught here.
    #
    # The surface is deliberately NOT every CLI verb. Three groups are held back on purpose, and the
    # reasoning belongs next to the assertion so "parity" is never restored by reflex:
    #   * `land`/`sync`/`propose land`/`resolve` advance *shared* state and are gated behind the
    #     CLI's interactive confirm; exposing them here would drop that gate for the least
    #     supervised caller.
    #   * the feature verbs (merge/split/move/rename) set authored labels and re-cut groupings,
    #     which permanently override generated ones -- a human call made while looking at the graph.
    #   * `sgt_grid` returns a *complete* projection (right for a TUI/webview that draws it, wrong
    #     for a model that never does): ~129,000 tokens on a 290-commit repo, growing with history.
    assert names == {"sgt_init", "sgt_log", "sgt_status", "sgt_diff", "sgt_advanced_fsck",
                      "sgt_revert", "sgt_restore", "sgt_advanced_oracle_run",
                      "sgt_plan_intake", "sgt_checkpoint", "sgt_drift", "sgt_plan_done",
                      "sgt_plan_adopt", "sgt_recall", "sgt_now", "sgt_show",
                      # An agent could read the graph and edit code but record neither, so a human
                      # relayed every save by hand -- the back-and-forth between editor, terminal
                      # and agent that the graph exists to remove.
                      "sgt_save",
                      # Every other tool here needs an id the caller already holds. Without a way
                      # in from a description, an agent asked to remove "the waitlist" had to
                      # guess at symbols or read the whole graph to find one.
                      "sgt_find"}


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


def test_the_grid_is_not_an_mcp_tool_and_log_stays_bounded(tmp_path):
    """The grid is deliberately absent from MCP, and the shape that replaces it stays capped.

    `grid_view` is a *complete* projection on purpose -- a TUI or webview needs every cell to draw
    one -- but a language model never draws it, so over MCP that completeness is pure cost. Measured
    on a 290-commit repo it was ~515 KB, about 129,000 tokens in one tool result, and it grows with
    history (1.5k tokens at 10 commits, 4.6k at 30, 6.5k at 60). `sgt_log` answers the same "what
    happened" question at a flat ~1.1k tokens because it is windowed.

    The projection itself is untouched and still tested in `tests/test_api.py`; only the MCP exposure
    is gone. This test exists so re-adding it requires deleting an explanation rather than just
    appending a registry entry."""
    from sgt.lens.map import build_map

    repo = _seed(tmp_path, 2)
    build_map(repo)
    # `grid_view`'s own shape (including `save_count`/`bookkeeping_count`) is asserted in
    # `tests/test_api.py`, which is where it belongs now that the projection has no MCP surface.

    resp, payload = _call(repo, "sgt_grid")
    assert resp["result"]["isError"] is True
    assert "unknown tool" in payload["error"]

    # `sgt_log` still returns the op DAG (the KTD9 schema-stable contract) and carries the
    # `truncated` flag that tells a caller a window was applied rather than hiding it.
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


def test_plan_done_refuses_to_close_another_agents_plan(tmp_path):
    """Ownership was stated in the skill ("only confirm your own") and enforced by nothing: any
    agent holding another's session id could close its plan out from under it, mid-build."""
    from sgt.loop import plan as plan_mod

    repo = _seed(tmp_path, 1)
    plan_mod.intake(repo, "1. do the thing\n", session_id="theirs", claude_session_id="chat-A")

    _, refused = _call(repo, "sgt_plan_done", {"session_id": "theirs", "claude_session_id": "chat-B"})
    # Assert what makes a refusal actionable rather than one phrasing of it: who owns the plan, and
    # what to do about it. A substring match on a sentence goes red on any rewording of the same
    # message, which teaches the next person to weaken the message rather than keep it useful.
    error = refused.get("error", "")
    assert "chat-A" in error, f"the owner must be named: {error!r}"
    assert "adopt" in error, f"the way forward must be named: {error!r}"
    assert refused.get("owner") == "chat-A"
    assert plan_mod.active_sessions(repo).get("theirs") is not None  # still open

    _, ok = _call(repo, "sgt_plan_done", {"session_id": "theirs", "claude_session_id": "chat-A"})
    assert ok.get("ok") is True


def test_plan_done_without_a_caller_id_still_closes(tmp_path):
    """Identifying yourself is what buys the check; a caller that cannot (a human on the CLI, an
    older agent) keeps the previous behavior rather than being locked out."""
    from sgt.loop import plan as plan_mod

    repo = _seed(tmp_path, 1)
    plan_mod.intake(repo, "1. do the thing\n", session_id="theirs", claude_session_id="chat-A")

    _, ok = _call(repo, "sgt_plan_done", {"session_id": "theirs"})
    assert ok.get("ok") is True


# -- the agent can record its own work -------------------------------------------------------

def test_save_records_the_agents_edits(tmp_path):
    """An agent could read the graph and edit code but record neither, so a human had to relay
    every save by hand -- the exact back-and-forth between editor, terminal and agent that the
    graph exists to remove."""
    repo = _seed(tmp_path, 1)
    (tmp_path / "a.py").write_text("def foo():\n    return 99\n", encoding="utf-8")

    _, payload = _call(repo, "sgt_save", {"message": "bump foo"})

    assert payload["ok"] is True and payload["saved"] is True
    assert payload["commit"]
    # The agent's own words are what got recorded, not a generated paraphrase of them.
    assert payload.get("words") == "bump foo"


def test_save_carries_the_driving_prompt_into_the_closing_manifest(tmp_path):
    """Capture weave P1 (§4a): an MCP client has no `UserPromptSubmit` hook, so the save call is
    the door the user's ask arrives through. With a chat key the turn is recorded *before* the
    save, so the capture window this save closes carries it -- channel `agent`, the trust tier for
    a relayed claim of the user's verbatim words."""
    from sgt.intent.manifest import load_manifests
    from sgt.intent.turns import turns_for

    repo = _seed(tmp_path, 1)
    (tmp_path / "a.py").write_text("def foo():\n    return 99\n", encoding="utf-8")

    _, payload = _call(repo, "sgt_save", {"message": "bump foo", "prompt": "make foo return 99",
                                          "claude_session_id": "cs-42"})

    assert payload["saved"] is True
    hits = turns_for(repo, "cs-42", key_kind="chat")
    assert len(hits) == 1 and hits[0]["text"] == "make foo return 99"
    assert hits[0]["channel"] == "agent" and hits[0]["actor"] == "human"
    rec = load_manifests(repo)[payload["commit"]]
    assert "make foo return 99" in [t["text"] for t in rec["turns"]]
    # An MCP client without hooks produces no activity events, so nothing grounds file-by-file --
    # but the deliberate carry is a whole-save claim, and the save's reflection records it (P2):
    # `sgt why <sha>` answers with the user's words from the very next read.
    from sgt.api import why_view
    reasons = [r["reason"] for r in why_view(repo, payload["commit"])["rationale"]]
    assert "make foo return 99" in reasons


def test_save_without_a_chat_key_keys_the_prompt_by_the_new_commit(tmp_path):
    """No session id, no pre-save key -- so the prompt is recorded after the save, keyed by the
    commit sha it produced (the direct key `_atom_prompt` joins first). The words still reach
    every read surface; they just don't ride the manifest."""
    from sgt.intent.turns import turns_for

    repo = _seed(tmp_path, 1)
    (tmp_path / "a.py").write_text("def foo():\n    return 99\n", encoding="utf-8")

    _, payload = _call(repo, "sgt_save", {"prompt": "make foo return 99"})

    assert payload["saved"] is True
    hits = turns_for(repo, payload["commit"], key_kind="sha")
    assert [t["text"] for t in hits] == ["make foo return 99"]
    assert hits[0]["channel"] == "agent"
    # The carry landed after the save's own reflection, so tool_save re-reflects: the sha-keyed
    # prompt still becomes a save-wide recorded why.
    from sgt.api import why_view
    reasons = [r["reason"] for r in why_view(repo, payload["commit"])["rationale"]]
    assert "make foo return 99" in reasons


def test_save_on_a_clean_tree_says_so_rather_than_committing_nothing(tmp_path):
    repo = _seed(tmp_path, 1)
    _call(repo, "sgt_log")  # mine, so the ideal is current

    _, payload = _call(repo, "sgt_save", {})

    assert payload["ok"] is True and payload["saved"] is False


def test_now_gives_an_agent_the_users_own_ask(tmp_path):
    """`working_on` is as useful to the agent as to the human: picking work back up, it says what
    was actually asked instead of leaving the agent to infer it from a diff."""
    from sgt.intent.turns import record_turn

    repo = _seed(tmp_path, 1)
    record_turn(repo, key="chat-1", key_kind="chat", actor="human", channel="hook",
                text="Add rate limiting to the API")
    (tmp_path / "a.py").write_text("def foo():\n    return 99\n", encoding="utf-8")

    _, payload = _call(repo, "sgt_now")

    assert payload["working_on"]["title"] == "Add rate limiting to the API"
    assert payload["working_on"]["source"] == "prompt"


def test_show_reads_a_past_file_without_touching_the_tree(tmp_path):
    repo = _seed(tmp_path, 3)
    before = (tmp_path / "a.py").read_text(encoding="utf-8")

    _, listing = _call(repo, "sgt_show", {"at": "0"})
    _, content = _call(repo, "sgt_show", {"at": "0", "path": "a.py"})

    assert listing["files"] == ["a.py"]
    assert "return 1" in content["content"]
    assert (tmp_path / "a.py").read_text(encoding="utf-8") == before


def test_a_failing_verb_is_a_tool_error_not_a_dead_server(tmp_path):
    """The adapter captures stdout because this process speaks JSON-RPC on it; a verb that fails
    must come back as an error payload, never as stray output mid-transport."""
    repo = _seed(tmp_path, 1)

    resp, payload = _call(repo, "sgt_show", {"at": "op:not-a-real-op-id"})

    assert "error" in payload
    assert resp["result"]["isError"] is True
