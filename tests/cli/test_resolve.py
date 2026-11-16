"""`ugh resolve <symbol>` (plan U13/R11): the guided one-verb fork resolution over the fork a sync
recorded in `.sgt/forks.json`, driven end to end on a real two-clone fork.
"""

from __future__ import annotations

import json

from ugh.api import forks_view
from ugh.cli.resolve import _resolve
from ugh.core import sync
from tests.core.test_sync import _BASE, _edit_and_commit, _push, _two_clones


def _forked_clone(tmp_path):
    """A clone `b` carrying one open `main.py::foo` fork after syncing a divergent teammate edit."""
    a, b = _two_clones(tmp_path, _BASE)
    _edit_and_commit(a, "main.py", "def foo():\n    return 999\n\n\ndef bar():\n    return 2\n", "A: rework foo")
    _push(a)
    _edit_and_commit(b, "main.py", "def foo():\n    return 42\n\n\ndef bar():\n    return 2\n", "B: rework foo")
    report = sync.sync(b, remote="origin", branch="main")
    assert report.forks and forks_view(b)["open"] == 1
    return b


def test_resolve_applies_the_edited_file_and_closes_the_fork(tmp_path):
    """After the user edits the file, `--apply` takes it from the tree, passes the oracle, lands,
    and closes the fork."""
    b = _forked_clone(tmp_path)
    # a trivially-passing oracle so the guided --apply can land (R14's gate is real)
    (b / ".sgt" / "oracle.json").write_text(
        json.dumps({"tiers": [{"name": "t", "command": "true"}]}), encoding="utf-8"
    )

    assert _resolve(str(b), "main.py::foo", apply=False, as_json=False) == 0  # says what to do
    assert forks_view(b)["open"] == 1

    # the user reconciles both versions by hand, then applies.
    (b / "main.py").write_text("def foo():\n    return 4200\n\n\ndef bar():\n    return 2\n", encoding="utf-8")
    assert _resolve(str(b), "main.py::foo", apply=True, as_json=False) == 0

    assert forks_view(b)["open"] == 0  # the fork is closed


def test_resolve_reports_no_open_fork(tmp_path):
    """A symbol with no recorded fork errors clearly."""
    a, b = _two_clones(tmp_path, _BASE)
    assert _resolve(str(b), "main.py::nothing", apply=False, as_json=False) == 1
    assert _resolve(str(b), "main.py::nothing", apply=True, as_json=False) == 1


def test_resolve_apply_refuses_on_a_pending_oracle_and_leaves_the_fork_open(tmp_path):
    """No oracle configured and no `--override`: the verdict is pending, so nothing lands."""
    b = _forked_clone(tmp_path)
    assert _resolve(str(b), "main.py::foo", apply=True, as_json=False) == 1
    assert forks_view(b)["open"] == 1


def _configure_true_oracle(repo) -> None:
    (repo / ".sgt" / "oracle.json").write_text(
        json.dumps({"tiers": [{"name": "t", "command": "true"}]}), encoding="utf-8"
    )


def test_resolve_apply_preview_view_states(tmp_path):
    """The `resolve --apply` feedforward projection: refuses (clean=False) with a reason when there's
    no open fork, and otherwise reports a clean three-step plan carrying the fork's two tips."""
    from ugh.api import resolve_apply_preview_view

    b = _forked_clone(tmp_path)

    no_fork = resolve_apply_preview_view(str(b), "main.py::nothing")
    assert no_fork["clean"] is False and "no open fork" in no_fork["error"]

    clean = resolve_apply_preview_view(str(b), "main.py::foo")
    assert clean["clean"] is True and clean["error"] is None
    assert clean["target"] == "main.py::foo" and len(clean["tips"]) == 2


def test_resolve_cli_tty_abort_resolves_nothing(tmp_path, monkeypatch, capsys):
    """On a tty the pane is the confirm: aborting a `resolve --apply` leaves the fork open (rc 1)."""
    import sys

    from ugh.cli import _common
    from ugh.tui.consequence import Decision

    b = _forked_clone(tmp_path)
    _configure_true_oracle(b)
    (b / "main.py").write_text("def foo():\n    return 4200\n\n\ndef bar():\n    return 2\n", encoding="utf-8")

    monkeypatch.setattr(sys.stdin, "isatty", lambda: True, raising=False)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True, raising=False)
    monkeypatch.setattr(_common, "maybe_confirm", lambda *a, **k: Decision(False))

    assert _resolve(str(b), "main.py::foo", apply=True, as_json=False) == 1
    assert "aborted" in capsys.readouterr().out
    assert forks_view(b)["open"] == 1  # still open


def test_resolve_cli_interactive_pick_left_closes_the_fork(tmp_path, monkeypatch):
    """On a tty, a bare `ugh resolve <symbol>` shows a side-by-side diff and lets the user pick a side
    in one step: `l` writes tip A's content, then the flow lands and closes the fork -- no second
    `--apply` command."""
    import builtins
    import sys

    b = _forked_clone(tmp_path)
    _configure_true_oracle(b)

    monkeypatch.setattr(sys.stdin, "isatty", lambda: True, raising=False)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True, raising=False)
    monkeypatch.setattr(builtins, "input", lambda *a, **k: "l")

    # apply=False on a tty is the whole flow: diff -> pick left -> land.
    assert _resolve(str(b), "main.py::foo", apply=False, as_json=False) == 0
    assert forks_view(b)["open"] == 0  # closed by the single interactive pick


def test_resolve_cli_interactive_quit_keeps_the_fork(tmp_path, monkeypatch):
    """Quitting the interactive pick (`q`) leaves the fork in place; `--apply` is still available."""
    import builtins
    import sys

    b = _forked_clone(tmp_path)
    _configure_true_oracle(b)

    monkeypatch.setattr(sys.stdin, "isatty", lambda: True, raising=False)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True, raising=False)
    monkeypatch.setattr(builtins, "input", lambda *a, **k: "q")

    assert _resolve(str(b), "main.py::foo", apply=False, as_json=False) == 0
    assert forks_view(b)["open"] == 1  # still open -- nothing resolved


def test_resolve_cli_tty_confirm_closes_the_fork(tmp_path, monkeypatch):
    """A confirm on a tty runs the real guided apply and closes the fork."""
    import sys

    from ugh.cli import _common
    from ugh.tui.consequence import Decision

    b = _forked_clone(tmp_path)
    _configure_true_oracle(b)
    (b / "main.py").write_text("def foo():\n    return 4200\n\n\ndef bar():\n    return 2\n", encoding="utf-8")

    monkeypatch.setattr(sys.stdin, "isatty", lambda: True, raising=False)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True, raising=False)
    monkeypatch.setattr(_common, "maybe_confirm", lambda *a, **k: Decision(True))

    assert _resolve(str(b), "main.py::foo", apply=True, as_json=False) == 0
    assert forks_view(b)["open"] == 0  # closed
