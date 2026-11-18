"""MCP server — JSON-RPC dispatch over the kernel-backed tools. No subprocess: the pure dispatcher
is called directly."""

from __future__ import annotations

import json

from ugh.mcp import handle_request
from ugh.store.gitbind import init_store


def _seed(tmp_path, n: int = 2) -> str:
    """A repo whose a.py::foo is a linear chain of `n` versions, one per commit."""
    gb, _ = init_store(tmp_path)
    for i in range(1, n + 1):
        (tmp_path / "a.py").write_text(f"def foo():\n    return {i}\n", encoding="utf-8")
        gb.commit_all(f"foo v{i}")
    return str(tmp_path)


def _head(repo) -> str:
    from ugh.store.gitbind import GitBinding

    return GitBinding(repo).head()[:7]


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
    assert resp["result"]["serverInfo"]["name"] == "ugh"


def test_initialized_notification_has_no_response(tmp_path):
    repo = _seed(tmp_path, 1)
    assert handle_request(repo, {"jsonrpc": "2.0", "method": "notifications/initialized"}) is None


def test_tools_list_advertises_kernel_surface(tmp_path):
    repo = _seed(tmp_path, 1)
    resp = handle_request(repo, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    names = {t["name"] for t in resp["result"]["tools"]}
    # `sync`/`resolve`/`rename` advance shared state or relabel a whole name, and stay behind the
    # CLI, where a person runs them.
    assert names == {"ugh_init", "ugh_log", "ugh_check", "ugh_save", "ugh_name", "ugh_revert",
                     "ugh_restore", "ugh_show", "ugh_find", "ugh_plan"}


def test_unknown_method_is_method_not_found(tmp_path):
    repo = _seed(tmp_path, 1)
    resp = handle_request(repo, {"jsonrpc": "2.0", "id": 9, "method": "nonsense"})
    assert resp["error"]["code"] == -32601


def test_unknown_tool_is_reported_as_tool_error(tmp_path):
    repo = _seed(tmp_path, 1)
    resp, payload = _call(repo, "ugh_nope")
    assert resp["result"]["isError"] is True and "unknown tool" in payload["error"]


def test_tool_call_response_text_has_no_indent_whitespace(tmp_path):
    """Part D: the JSON-RPC tool-call response body is `json.dumps(..., separators=(",", ":"))`,
    not `indent=2` -- a cold MCP process's response payload should carry no pretty-printing
    whitespace, whatever the tool's own shape."""
    repo = _seed(tmp_path, 1)
    msg = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
           "params": {"name": "ugh_log", "arguments": {}}}
    resp = handle_request(repo, msg)
    text = resp["result"]["content"][0]["text"]
    assert "\n" not in text and ": " not in text


# -- read tools -------------------------------------------------------------
def test_log_lists_the_saves_with_their_steps(tmp_path):
    repo = _seed(tmp_path, 2)
    _, payload = _call(repo, "ugh_log")
    assert [s["subject"] for s in payload["saves"]] == ["foo v2", "foo v1"]
    step = payload["saves"][0]["steps"][0]
    assert step == {"address": f"a.py::foo@{_head(repo)}", "kind": "change", "name": None, "out": False}


def test_check_reports_a_clean_store(tmp_path):
    repo = _seed(tmp_path, 1)
    _call(repo, "ugh_log")  # mine, so the store isn't empty
    _, payload = _call(repo, "ugh_check")
    assert payload["ok"] is True and payload["checked"] >= 1


def test_show_gives_the_steps_and_the_planners_answer(tmp_path):
    repo = _seed(tmp_path, 2)
    _, payload = _call(repo, "ugh_show", {"address": "a.py::foo"})
    assert payload["ok"] and len(payload["steps"]) == 2
    assert payload["plan"]["verb"] == "revert" and payload["plan"]["removal_set"] is not None


def test_find_returns_addresses(tmp_path):
    repo = _seed(tmp_path, 1)
    _, payload = _call(repo, "ugh_find", {"query": "foo"})
    assert {"address": "a.py::foo", "matched": "definition", "text": "a.py::foo"} in payload["hits"]


# -- write tools --------------------------------------------------------------
def test_revert_tool_takes_one_step_out(tmp_path):
    repo = _seed(tmp_path, 2)
    _, payload = _call(repo, "ugh_revert", {"ref": f"a.py::foo@{_head(repo)}"})
    assert payload["ok"] and payload["applied"] and payload["undone"]
    assert payload["check"]["passed"]
    assert (tmp_path / "a.py").read_text() == "def foo():\n    return 1\n"


def test_revert_emit_previews_without_writing(tmp_path):
    repo = _seed(tmp_path, 2)
    _, payload = _call(repo, "ugh_revert", {"ref": _head(repo), "emit": True})
    assert payload["ok"]
    assert (tmp_path / "a.py").read_text() == "def foo():\n    return 2\n"  # untouched


def test_restore_tool_is_reverts_inverse(tmp_path):
    repo = _seed(tmp_path, 2)
    sha = _head(repo)
    _call(repo, "ugh_revert", {"ref": sha})
    assert (tmp_path / "a.py").read_text() == "def foo():\n    return 1\n"
    _, payload = _call(repo, "ugh_restore", {"ref": sha})
    assert payload["ok"] and payload["applied"] and payload["undone"]
    assert (tmp_path / "a.py").read_text() == "def foo():\n    return 2\n"


def test_revert_missing_ref_is_an_error(tmp_path):
    repo = _seed(tmp_path, 1)
    _, payload = _call(repo, "ugh_revert", {})
    assert "error" in payload


def test_revert_refuses_free_text_with_find_hits(tmp_path):
    repo = _seed(tmp_path, 1)
    resp, payload = _call(repo, "ugh_revert", {"ref": "the foo thing"})
    assert payload["ok"] is False and "no such address" in payload["message"]
    assert "a.py::foo" in payload["hits"] and resp["result"]["isError"] is True


def test_name_tool_labels_the_steps_at_an_address(tmp_path):
    repo = _seed(tmp_path, 2)
    _, payload = _call(repo, "ugh_name", {"address": "a.py::foo", "label": "the foo"})
    assert payload["ok"] and {s["name"] for s in payload["steps"]} == {"the foo"}
    _, log = _call(repo, "ugh_log")
    assert all(st["name"] == "the foo" for s in log["saves"] for st in s["steps"]
               if st["address"].startswith("a.py::foo@"))


def test_plan_tool_declares_reports_and_clears(tmp_path):
    repo = _seed(tmp_path, 1)
    _, declared = _call(repo, "ugh_plan", {"declare": ["bar"]})
    assert declared["plan"]["names"] == [{"name": "bar", "saves": []}]
    (tmp_path / "a.py").write_text("def foo():\n    return 1\n\n\ndef bar():\n    return 2\n")
    _call(repo, "ugh_save", {"message": "bar", "as": "bar"})
    _, status = _call(repo, "ugh_plan")
    assert [s["subject"] for s in status["plan"]["names"][0]["saves"]] == ["bar"]
    _, done = _call(repo, "ugh_plan", {"done": True})
    assert done["plan"] is None


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

    _, payload = _call(str(fresh), "ugh_init")
    assert payload["ok"] and (fresh / ".sgt").exists()


# -- the agent can record its own work -------------------------------------------------------

def test_save_records_the_agents_edits(tmp_path):
    """An agent could read the graph and edit code but record neither, so a human had to relay
    every save by hand -- the exact back-and-forth between editor, terminal and agent that the
    graph exists to remove."""
    repo = _seed(tmp_path, 1)
    (tmp_path / "a.py").write_text("def foo():\n    return 99\n", encoding="utf-8")

    _, payload = _call(repo, "ugh_save", {"message": "bump foo"})

    assert payload["ok"] is True and payload["saved"] is True
    assert payload["steps"] == [{"address": f"a.py::foo@{payload['commit'][:7]}", "kind": "change",
                                 "name": None, "out": False, "ran": None}]
    assert payload["observed"] is None  # no check configured


def test_save_as_names_the_saves_steps(tmp_path):
    repo = _seed(tmp_path, 1)
    (tmp_path / "a.py").write_text("def foo():\n    return 99\n", encoding="utf-8")
    _, payload = _call(repo, "ugh_save", {"message": "bump foo", "as": "bumps"})
    assert [s["name"] for s in payload["steps"]] == ["bumps"]


def test_save_carries_the_driving_prompt_into_the_closing_manifest(tmp_path):
    """Capture weave P1 (§4a): an MCP client has no `UserPromptSubmit` hook, so the save call is
    the door the user's ask arrives through. With a chat key the turn is recorded *before* the
    save, so the capture window this save closes carries it -- channel `agent`, the trust tier for
    a relayed claim of the user's verbatim words."""
    from ugh.intent.manifest import load_manifests
    from ugh.intent.turns import turns_for

    repo = _seed(tmp_path, 1)
    (tmp_path / "a.py").write_text("def foo():\n    return 99\n", encoding="utf-8")

    _, payload = _call(repo, "ugh_save", {"message": "bump foo", "prompt": "make foo return 99",
                                          "claude_session_id": "cs-42"})

    assert payload["saved"] is True
    hits = turns_for(repo, "cs-42", key_kind="chat")
    assert len(hits) == 1 and hits[0]["text"] == "make foo return 99"
    assert hits[0]["channel"] == "agent" and hits[0]["actor"] == "human"
    rec = load_manifests(repo)[payload["commit"]]
    assert "make foo return 99" in [t["text"] for t in rec["turns"]]
    # The carry is a whole-save claim, so it is one of the asks `show` picks the save's line from.
    from ugh.intent.stint import asks_for_ops

    from ugh.core.opindex import index_ops

    ops = [o.id for o in index_ops(repo) if payload["commit"] in o.provenance]
    assert "make foo return 99" in [a["text"] for a in asks_for_ops(repo, ops, [payload["commit"]])]


def test_save_without_a_chat_key_keys_the_prompt_by_the_new_commit(tmp_path):
    """No session id, no pre-save key -- so the prompt is recorded after the save, keyed by the
    commit sha it produced. The words still reach every
    read surface; they just don't ride the manifest."""
    from ugh.intent.turns import turns_for

    repo = _seed(tmp_path, 1)
    (tmp_path / "a.py").write_text("def foo():\n    return 99\n", encoding="utf-8")

    _, payload = _call(repo, "ugh_save", {"prompt": "make foo return 99"})

    assert payload["saved"] is True
    hits = turns_for(repo, payload["commit"], key_kind="sha")
    assert [t["text"] for t in hits] == ["make foo return 99"]
    assert hits[0]["channel"] == "agent"


def test_save_on_a_clean_tree_says_so_rather_than_committing_nothing(tmp_path):
    repo = _seed(tmp_path, 1)
    _call(repo, "ugh_log")  # mine, so the ideal is current

    _, payload = _call(repo, "ugh_save", {})

    assert payload["ok"] is True and payload["saved"] is False


def test_a_failing_verb_is_a_tool_error_not_a_dead_server(tmp_path):
    repo = _seed(tmp_path, 1)
    resp, payload = _call(repo, "ugh_show", {"address": "nowhere"})
    assert payload["ok"] is False and resp["result"]["isError"] is True


def test_revert_whole_takes_what_stands_on_the_target(tmp_path):
    repo = _seed(tmp_path, 1)
    (tmp_path / "a.py").write_text("def foo():\n    return 1\n\n\ndef bar():\n    return foo()\n")
    from ugh.store.gitbind import GitBinding

    sha = GitBinding(tmp_path).commit_all("bar")
    (tmp_path / "a.py").write_text("def foo():\n    return 1\n\n\ndef bar():\n    return foo() + 1\n")
    GitBinding(tmp_path).commit_all("bar plus one")
    _, held = _call(repo, "ugh_revert", {"ref": sha[:7], "emit": True})
    assert [h["reason"] for h in held["held"]] == ["changed_later"]
    _, payload = _call(repo, "ugh_revert", {"ref": sha[:7], "whole": True})
    assert payload["applied"] and not payload["held"]
    assert (tmp_path / "a.py").read_text() == "def foo():\n    return 1\n"
