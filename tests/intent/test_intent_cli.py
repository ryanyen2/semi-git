"""Tests for `sgt intent` (plan U7): the thin CLI layer over `sgt.api.intent_view` and
`sgt.intent.theme.build_themes`. Verb behavior is tested in tests/intent/; this is argument
parsing, dispatch, and --json rendering only."""

from __future__ import annotations

import json
import os

from sgt.cli import main
from sgt.intent import theme
from sgt.store.gitbind import init_store


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


def test_intent_list_json_matches_intent_view(tmp_path, capsys, monkeypatch):
    def _no_client(*args, **kwargs):
        raise RuntimeError("OPENAI_API_KEY not found in environment or .env")

    monkeypatch.setattr(theme, "get_client", _no_client)
    _seed(tmp_path)
    assert _in(tmp_path, ["intent", "build"]) == 0
    capsys.readouterr()

    assert _in(tmp_path, ["intent", "list", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)

    from sgt.api import intent_view

    assert payload == intent_view(tmp_path)


def test_intent_show_commit_resolves_atom_and_lists_ops(tmp_path, capsys):
    gb = _seed(tmp_path)
    sha = gb.rev_parse("HEAD")

    assert _in(tmp_path, ["intent", "show", sha, "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["kind"] == "atom"
    assert payload["commit_sha"] == sha
    assert len(payload["op_ids"]) >= 1


def test_intent_show_unknown_target_fails(tmp_path, capsys):
    _seed(tmp_path)

    assert _in(tmp_path, ["intent", "show", "no-such-target", "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False


def test_intent_build_writes_themes_json_second_build_is_a_no_op_cache_hit(tmp_path, monkeypatch):
    def _no_client(*args, **kwargs):
        raise RuntimeError("OPENAI_API_KEY not found in environment or .env")

    monkeypatch.setattr(theme, "get_client", _no_client)
    _seed(tmp_path)

    assert _in(tmp_path, ["intent", "build"]) == 0
    themes_path = tmp_path / ".sgt" / "intent" / "themes.json"
    assert themes_path.is_file()
    before_mtime = themes_path.stat().st_mtime_ns

    assert _in(tmp_path, ["intent", "build"]) == 0
    after_mtime = themes_path.stat().st_mtime_ns
    assert before_mtime == after_mtime  # no-op cache hit -- save_json_if_changed skips the write


def test_intent_usage_on_missing_or_unknown_sub(tmp_path, capsys):
    _seed(tmp_path)
    assert _in(tmp_path, ["intent"]) == 2
    assert "usage: sgt intent" in capsys.readouterr().out
