"""CLI tests for `ugh revert` / `ugh restore`: the confirm gate, how a reference resolves, the
machine-readable answer, and the refusals a reader sees."""

from __future__ import annotations

import json
import os

import pytest

from ugh.cli import main
from ugh.core.lens import get
from ugh.core.planner import out_table as load_out
from ugh.store.gitbind import GitBinding
from tests.laws import corpus


def _in(repo, argv):
    cwd = os.getcwd()
    os.chdir(repo)
    try:
        return main(argv)
    finally:
        os.chdir(cwd)


def _json(repo, argv, capsys):
    capsys.readouterr()
    rc = _in(repo, argv)
    return rc, json.loads(capsys.readouterr().out)


def _newest(repo):
    """The newest save's address: nothing stands on it."""
    return GitBinding(repo).head()[:7]


def _make_tty(monkeypatch, reply):
    import sys

    monkeypatch.setattr(sys.stdin, "isatty", lambda: True, raising=False)
    monkeypatch.setattr("builtins.input", lambda prompt="": reply)


def _two_saves(root):
    repo = root / "two"
    corpus._init(repo)
    corpus._write(repo, "cart.py", "def total(items):\n    return sum(items)\n")
    corpus._commit(repo, "the cart", 1)
    corpus._write(repo, "cart.py", "def total(items):\n    return sum(items)\n\n\n"
                                   "def tax(t):\n    return t * 0.2\n")
    sha = corpus._commit(repo, "add the cart tax", 2)
    get(repo)
    return repo, sha


@pytest.fixture
def linear(tmp_path):
    repo = corpus.CORPUS["linear_history"].build(tmp_path / "repo")
    get(repo)
    return repo


def test_revert_confirm_applies_on_yes(linear, capsys, monkeypatch):
    feat = _newest(linear)
    _make_tty(monkeypatch, "y")
    assert _in(linear, ["revert", feat]) == 0
    out = capsys.readouterr().out
    assert out.startswith("  revert ") and "applied" in out
    assert load_out(linear)


def test_revert_confirm_skips_on_no(linear, capsys, monkeypatch):
    feat = _newest(linear)
    _make_tty(monkeypatch, "n")
    assert _in(linear, ["revert", feat]) == 1
    assert "skipped" in capsys.readouterr().out
    assert not load_out(linear)


def test_revert_non_tty_without_yes_exits_2_and_applies_nothing(linear, capsys, monkeypatch):
    import sys

    monkeypatch.setattr(sys.stdin, "isatty", lambda: False, raising=False)
    feat = _newest(linear)
    assert _in(linear, ["revert", feat]) == 2
    assert "not applied" in capsys.readouterr().out
    assert not load_out(linear)


def test_a_save_sha_reverts_that_save_and_restores_it(tmp_path, capsys, monkeypatch):
    repo, sha = _two_saves(tmp_path)
    before = (repo / "cart.py").read_bytes()
    assert _in(repo, ["revert", sha[:7], "--yes"]) == 0
    assert b"def tax" not in (repo / "cart.py").read_bytes()
    assert _in(repo, ["restore", sha[:7], "--yes"]) == 0
    assert (repo / "cart.py").read_bytes() == before


def test_the_dry_run_answers_in_three_parts_and_writes_nothing(tmp_path, capsys):
    repo, sha = _two_saves(tmp_path)
    rc, view = _json(repo, ["revert", sha[:7], "--emit", "--json"], capsys)
    assert rc == 0 and view["ok"] and view["applied"] is False
    assert {"undone", "held", "removal_set", "files", "check"} <= set(view)
    assert view["check"] == "not_run"
    assert "cart.py::tax" in {u["symbol"] for u in view["undone"]}
    assert "def tax" in view["files"]["cart.py"]["before"]
    assert "def tax" not in view["files"]["cart.py"]["after"]
    assert view["removal_set"]["saves"][0]["sha"] == sha[:7]
    assert not load_out(repo)


def test_json_apply_says_it_applied_and_how_it_was_judged(tmp_path, capsys):
    repo, sha = _two_saves(tmp_path)
    rc, view = _json(repo, ["revert", sha[:7], "--json"], capsys)
    assert rc == 0 and view["applied"] is True
    assert view["check"]["passed"] and view["check"]["judge"] == "parser"


def test_a_held_step_is_named_with_its_reason_and_holders(tmp_path, capsys):
    repo = tmp_path / "held"
    corpus._init(repo)
    corpus._write(repo, "m.py", "def keep():\n    return 1\n")
    corpus._commit(repo, "base", 1)
    corpus._write(repo, "m.py", "def keep():\n    return 1\n\n\ndef helper():\n    return 2\n")
    sha = corpus._commit(repo, "add a helper", 2)
    corpus._write(repo, "m.py", "def keep():\n    return helper()\n\n\ndef helper():\n    return 2\n")
    later = corpus._commit(repo, "use the helper", 3)
    get(repo)
    rc, view = _json(repo, ["revert", sha[:7], "--emit", "--json"], capsys)
    held = {h["symbol"]: h for h in view["held"]}
    assert held["m.py::helper"]["reason"] == "uses"
    assert held["m.py::helper"]["holders"][0]["save"] == later[:7]
    capsys.readouterr()
    _in(repo, ["revert", sha[:7], "--emit"])
    out = capsys.readouterr().out
    assert "m.py::helper" in out and "uses" in out and later[:7] in out


def test_whole_takes_what_stands_on_the_target(tmp_path, capsys):
    repo = tmp_path / "whole"
    corpus._init(repo)
    corpus._write(repo, "m.py", "def keep():\n    return 1\n")
    corpus._commit(repo, "base", 1)
    corpus._write(repo, "m.py", "def keep():\n    return 1\n\n\ndef helper():\n    return 2\n")
    sha = corpus._commit(repo, "add a helper", 2)
    corpus._write(repo, "m.py", "def keep():\n    return 1\n\n\ndef helper():\n    return 3\n")
    corpus._commit(repo, "change the helper", 3)
    get(repo)
    assert _in(repo, ["revert", sha[:7], "--whole", "--yes"]) == 0
    assert (repo / "m.py").read_bytes() == b"def keep():\n    return 1\n"


def test_restoring_what_was_never_reverted_says_so(linear, capsys, monkeypatch):
    feat = _newest(linear)
    assert _in(linear, ["restore", feat, "--yes"]) == 0
    assert "nothing to restore" in capsys.readouterr().out


def test_a_dirty_tracked_file_refuses_cleanly(tmp_path, capsys):
    repo, sha = _two_saves(tmp_path)
    (repo / "cart.py").write_text("def total(items):\n    return 0  # local WIP\n")
    rc, view = _json(repo, ["revert", sha[:7], "--json"], capsys)
    assert rc == 1 and view["ok"] is False
    assert "cart.py" in view["error"] and "ugh save" in view["error"]
    assert not load_out(repo)


def test_an_unknown_address_exits_2_with_the_nearest_find_hits(linear, capsys):
    rc, view = _json(linear, ["revert", "deadbeef", "--json"], capsys)
    assert rc == 2 and view["ok"] is False and "no such address" in view["message"]
    assert view["hits"] == []


def test_reverting_a_files_only_definition_removes_the_file_and_undo_brings_it_back(tmp_path, capsys):
    repo = tmp_path / "solo"
    corpus._init(repo)
    corpus._write(repo, "keep.py", "def keep():\n    return 1\n")
    corpus._commit(repo, "keep", 1)
    corpus._write(repo, "solo.py", "def only():\n    return 2\n")
    sha = corpus._commit(repo, "solo", 2)
    get(repo)
    assert _in(repo, ["revert", sha[:7], "--yes"]) == 0
    assert not (repo / "solo.py").exists()
    assert _in(repo, ["undo"]) == 0
    assert (repo / "solo.py").read_bytes() == b"def only():\n    return 2\n"
    from ugh.core.opindex import index_ops

    assert not set(load_out(repo)) & {o.id for o in index_ops(repo) if sha in o.provenance}


