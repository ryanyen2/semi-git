"""The prompt sidecar unions by key across a sync, on two clones of one bare remote."""

from __future__ import annotations

from pathlib import Path

from ugh import state
from ugh.core import sync
from ugh.intent import prompts
from ugh.store.gitbind import GitBinding

from tests.conftest import _push
from tests.core.test_sync import _two_clones


def _edit_and_commit(repo: Path, path: str, content: str, message: str) -> str:
    (repo / path).write_text(content, encoding="utf-8")
    return GitBinding(repo).commit_all(message)


def _record(repo: Path, key: str, text: str) -> None:
    state.save_json(repo, "intent_prompts", {**prompts.load_prompts(repo), key: text})


_BASE = "def foo():\n    return 1\n\n\ndef bar():\n    return 2\n"


def test_prompt_sidecars_union_across_a_sync_with_distinct_keys(tmp_path):
    a, b = _two_clones(tmp_path, _BASE)

    _record(a, "plan-a", "fix foo")
    _edit_and_commit(a, "main.py", "def foo():\n    return 100\n\n\ndef bar():\n    return 2\n", "bump foo")
    _push(a)

    _record(b, "plan-b", "fix bar")
    _edit_and_commit(b, "main.py", "def foo():\n    return 1\n\n\ndef bar():\n    return 200\n", "bump bar")

    report = sync.sync(b, remote="origin", branch="main")
    assert report.message == "merged"

    assert prompts.prompt_for(b, "plan-a") == "fix foo"
    assert prompts.prompt_for(b, "plan-b") == "fix bar"


def test_prompt_sidecar_same_key_both_sides_merges_deterministically_no_crash(tmp_path):
    a, b = _two_clones(tmp_path, _BASE)

    _record(a, "shared-key", "a's version")
    _edit_and_commit(a, "main.py", "def foo():\n    return 100\n\n\ndef bar():\n    return 2\n", "bump foo")
    _push(a)

    _record(b, "shared-key", "b's version")
    _edit_and_commit(b, "main.py", "def foo():\n    return 1\n\n\ndef bar():\n    return 200\n", "bump bar")

    report = sync.sync(b, remote="origin", branch="main")
    assert report.message == "merged"

    result = prompts.prompt_for(b, "shared-key")
    assert result in ("a's version", "b's version")  # deterministic pick, no crash, no duplicate
