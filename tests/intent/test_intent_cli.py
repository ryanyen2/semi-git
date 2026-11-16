"""Tests for `ugh intent record` / `ugh intent activity`, the hook entry points `ugh init` installs,
and for the hook installation itself."""

from __future__ import annotations

import json
import os

from ugh.cli import main
from ugh.store.gitbind import init_store


def _in(tmp_path, argv):
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        return main(argv)
    finally:
        os.chdir(cwd)


def _seed(tmp_path, subject: str = "fix(auth): add foo"):
    gb, _ = init_store(tmp_path)
    (tmp_path / "a.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    gb.commit_all(subject)
    return gb


def test_intent_record_captures_a_chat_turn_from_hook_stdin(tmp_path, monkeypatch):
    """`ugh intent record` is the `UserPromptSubmit` hook sink: the payload's prompt lands
    verbatim as a chat-keyed turn, and the command stays silent (exit 0)."""
    import io

    from ugh.intent.turns import turns_for

    _seed(tmp_path)
    monkeypatch.setattr("sys.stdin", io.StringIO('{"session_id": "cs-9", "prompt": "make auth stateless"}'))
    assert _in(tmp_path, ["intent", "record"]) == 0
    hits = turns_for(tmp_path, "cs-9", key_kind="chat")
    assert len(hits) == 1 and hits[0]["text"] == "make auth stateless"


def test_intent_record_is_a_no_op_outside_an_ugh_repo(tmp_path, monkeypatch):
    """The hook fires wherever Claude Code runs; without a prior `ugh init` (no `.sgt/`) the sink
    must exit 0 AND write nothing -- materializing `.sgt/` into an arbitrary cwd is pollution
    (testbed 2026-07-31: a stray fire minted `/tmp/.sgt`)."""
    import io
    import subprocess

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    monkeypatch.setattr("sys.stdin", io.StringIO('{"session_id": "cs-9", "prompt": "hello"}'))
    assert _in(tmp_path, ["intent", "record"]) == 0
    assert not (tmp_path / ".sgt").exists()


def test_intent_activity_appends_a_tool_event_from_hook_stdin(tmp_path, monkeypatch):
    """`ugh intent activity` is the `PostToolUse` hook sink: the payload's tool + edited file land
    as one row in the local activity feed, and the command stays silent (exit 0)."""
    import io

    from ugh.intent.activity import load_activity

    _seed(tmp_path)
    monkeypatch.setattr("sys.stdin", io.StringIO(
        '{"session_id": "cs-9", "tool_name": "Edit", "tool_input": {"file_path": "a.py"}}'))
    assert _in(tmp_path, ["intent", "activity"]) == 0
    feed = load_activity(tmp_path)
    assert len(feed) == 1
    assert feed[0]["tool"] == "Edit" and feed[0]["file"] == "a.py" and feed[0]["session_id"] == "cs-9"


def test_intent_activity_is_a_no_op_outside_an_ugh_repo(tmp_path, monkeypatch):
    """Like `record`, the PostToolUse hook fires wherever Claude Code runs; without a prior
    `ugh init` (no `.sgt/`) the sink exits 0 AND writes nothing."""
    import io
    import subprocess

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    monkeypatch.setattr("sys.stdin", io.StringIO(
        '{"tool_name": "Write", "tool_input": {"file_path": "a.py"}}'))
    assert _in(tmp_path, ["intent", "activity"]) == 0
    assert not (tmp_path / ".sgt").exists()


def test_prompt_hook_installs_with_an_absolute_ugh_path(tmp_path, monkeypatch):
    """The hook command must carry the running `ugh`'s absolute path: hooks execute in whatever
    shell Claude Code has, and a bare `ugh` silently no-ops when the venv is not on that PATH
    (testbed 2026-07-31). Under pytest argv[0] is not `ugh`, so the resolver falls back to
    `shutil.which` -- pinned here to a fake binary for hermeticity."""
    import shutil

    from ugh.cli.init import _install_prompt_hook

    fake = tmp_path / "bin" / "ugh"
    fake.parent.mkdir()
    fake.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setattr(shutil, "which", lambda name: str(fake))

    assert _install_prompt_hook(str(tmp_path)) is True
    settings = json.loads((tmp_path / ".claude" / "settings.local.json").read_text(encoding="utf-8"))
    (entry,) = settings["hooks"]["UserPromptSubmit"]
    (hook,) = entry["hooks"]
    assert hook["command"] == f'"{fake.resolve()}" intent record'

    assert _install_prompt_hook(str(tmp_path)) is True  # idempotent: no duplicate entry
    settings = json.loads((tmp_path / ".claude" / "settings.local.json").read_text(encoding="utf-8"))
    assert len(settings["hooks"]["UserPromptSubmit"]) == 1


def test_activity_hook_installs_a_posttooluse_matcher_and_preserves_the_prompt_hook(tmp_path, monkeypatch):
    """The PostToolUse edit hook installs alongside the prompt hook (both live in the same
    settings): it carries the Edit|Write|MultiEdit matcher and the absolute-path `intent activity`
    command, is idempotent, and never clobbers an existing UserPromptSubmit entry."""
    import shutil

    from ugh.cli.init import _install_activity_hook, _install_prompt_hook

    fake = tmp_path / "bin" / "ugh"
    fake.parent.mkdir()
    fake.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setattr(shutil, "which", lambda name: str(fake))

    assert _install_prompt_hook(str(tmp_path)) is True
    assert _install_activity_hook(str(tmp_path)) is True
    settings = json.loads((tmp_path / ".claude" / "settings.local.json").read_text(encoding="utf-8"))
    (entry,) = settings["hooks"]["PostToolUse"]
    assert entry["matcher"] == "Edit|Write|MultiEdit"
    (hook,) = entry["hooks"]
    assert hook["command"] == f'"{fake.resolve()}" intent activity'
    assert len(settings["hooks"]["UserPromptSubmit"]) == 1  # prompt hook preserved

    assert _install_activity_hook(str(tmp_path)) is True  # idempotent: no duplicate entry
    settings = json.loads((tmp_path / ".claude" / "settings.local.json").read_text(encoding="utf-8"))
    assert len(settings["hooks"]["PostToolUse"]) == 1


def test_intent_record_rejects_harness_injected_wrappers(tmp_path, monkeypatch):
    """`UserPromptSubmit` fires for more than typed prompts: the harness routes task
    notifications, system reminders, and slash-command markup through the same hook, wrapped in a
    leading tag. Recording those as `actor="human"` poisons every surface that trusts the turn
    store (dogfood 2026-09-01: 137 of 294 captured "prompts" were `<task-notification>` blobs). A
    leading known
    wrapper tag means "not the user's voice" -- skip it, silently, exit 0."""
    import io

    from ugh.intent.turns import turns_for

    _seed(tmp_path)
    for tag in ("task-notification", "system-reminder", "command-name", "local-command-stdout"):
        payload = json.dumps({"session_id": "cs-9", "prompt": f"<{tag}>whatever</{tag}>"})
        monkeypatch.setattr("sys.stdin", io.StringIO(payload))
        assert _in(tmp_path, ["intent", "record"]) == 0
    assert turns_for(tmp_path, "cs-9", key_kind="chat") == []


def test_intent_record_keeps_a_real_prompt_that_merely_mentions_a_tag(tmp_path, monkeypatch):
    """Only a LEADING wrapper tag marks an injection. A user asking about the machinery --
    pasting a `<task-notification>` mid-sentence, or starting with unrelated markup -- is still
    a human utterance and must be kept verbatim."""
    import io

    from ugh.intent.turns import turns_for

    _seed(tmp_path)
    text = "why does <task-notification> show up in my turn store?"
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"session_id": "cs-9", "prompt": text})))
    assert _in(tmp_path, ["intent", "record"]) == 0
    hits = turns_for(tmp_path, "cs-9", key_kind="chat")
    assert len(hits) == 1 and hits[0]["text"] == text


def test_intent_activity_skips_an_edit_outside_this_repo(tmp_path, monkeypatch):
    """The PostToolUse hook fires with cwd = the session's repo, but the edited file can live
    anywhere (dogfood 2026-09-01: a sibling checkout's edits landed in this repo's feed). An
    event whose file is outside the repo root is another repo's motion, not this one's."""
    import io

    from ugh.intent.activity import load_activity

    _seed(tmp_path)
    payload = json.dumps({"session_id": "cs-9", "tool_name": "Edit",
                          "tool_input": {"file_path": "/somewhere/else/b.py"}})
    monkeypatch.setattr("sys.stdin", io.StringIO(payload))
    assert _in(tmp_path, ["intent", "activity"]) == 0
    assert load_activity(tmp_path) == []
