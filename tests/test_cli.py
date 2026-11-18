"""CLI surface tests (the operation-ideal kernel, plan U7/U8/U9 flipped onto the CLI in U10):
argument parsing, human-readable rendering, and `--json` output for the kernel-backed verbs.
Verb *behavior* (the algebra) is tested in `tests/core/`; this file is the thin CLI layer only.
"""

import json
import os
import sys
from types import SimpleNamespace

import pytest

from ugh.cli import main
from ugh.store.gitbind import GitBinding, init_store


def _in(tmp_path, argv):
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        return main(argv)
    finally:
        os.chdir(cwd)


def _seed(tmp_path, n: int = 2):
    """A repo whose a.py::foo is a linear chain of `n` versions, one per commit."""
    gb, _ = init_store(tmp_path)
    for i in range(1, n + 1):
        (tmp_path / "a.py").write_text(f"def foo():\n    return {i}\n", encoding="utf-8")
        gb.commit_all(f"foo v{i}")
    return gb


def test_init_with_horizon_mines_only_from_that_commit_forward(tmp_path, capsys):
    """`ugh init --horizon <ref>` must reach `lens.init`'s `horizon` param, not get swallowed
    as a positional path -- the escape hatch for a repo whose pre-horizon history is unminable
    (e.g. self-referential rename/delete cycles the miner can't represent, R10)."""
    gb, _ = init_store(tmp_path)
    (tmp_path / "a.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    gb.commit_all("foo v1")
    (tmp_path / "a.py").write_text("def foo():\n    return 2\n", encoding="utf-8")
    horizon_sha = gb.commit_all("foo v2")
    (tmp_path / "a.py").write_text("def foo():\n    return 3\n", encoding="utf-8")
    gb.commit_all("foo v3")

    capsys.readouterr()
    assert _in(tmp_path, ["init", "--horizon", horizon_sha]) == 0
    assert not (tmp_path / "--horizon").exists()  # not misparsed as the repo path

    capsys.readouterr()
    assert _in(tmp_path, ["log", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    foo = [st["kind"] for s in payload["saves"] for st in s["steps"] if st["address"].startswith("a.py::foo@")]
    # v1 (pre-horizon) is never mined at all; v2 becomes one creation, v3 one change on top --
    # not the 3-step chain a horizon-less init would produce.
    assert foo == ["change", "create"]


def _seed_resolvable_fork(tmp_path, symbol="a.py::foo"):
    """Record a genuinely-divergent open fork (real symbol, differing after versions) so the state
    banner must render on any fork-surfacing verb."""
    from ugh import state
    from ugh.core.op import make_op
    from ugh.core.store import Store

    store = Store(tmp_path)
    a = store.add(make_op({symbol: ("v0", "v1")}, {symbol: b"a"}, kind="touched"))
    b = store.add(make_op({symbol: ("v0", "v2")}, {symbol: b"b"}, kind="rebirth"))
    state.save_json(tmp_path, "forks", [
        {"symbol": symbol, "tips": [a.id, b.id], "remedy": f"ugh merge-op {a.id} {b.id}"},
    ])


def test_status_names_an_open_fork_and_its_remedy(tmp_path, capsys):
    """A genuine open fork is listed by `ugh status` with its `ugh resolve` remedy -- and the verb
    does not change its exit code (non-blocking)."""
    _seed(tmp_path, 2)
    _seed_resolvable_fork(tmp_path)
    capsys.readouterr()

    assert _in(tmp_path, ["status"]) == 0
    assert "ugh resolve a.py::foo" in capsys.readouterr().out


def test_revert_emit_previews_without_writing(tmp_path, capsys):
    _seed(tmp_path, 2)
    assert _in(tmp_path, ["revert", "--emit", _head(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert f"revert {_head(tmp_path)}" in out and "check: not run yet" in out
    assert (tmp_path / "a.py").read_text() == "def foo():\n    return 2\n"  # untouched


def test_revert_then_restore_roundtrip(tmp_path, capsys):
    _seed(tmp_path, 2)
    step = _head(tmp_path)
    assert _in(tmp_path, ["revert", step, "--yes"]) == 0
    capsys.readouterr()
    assert (tmp_path / "a.py").read_text() == "def foo():\n    return 1\n"

    assert _in(tmp_path, ["restore", "--json", step]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True and payload["applied"] and payload["undone"]
    assert (tmp_path / "a.py").read_text() == "def foo():\n    return 2\n"


# -- NL intent-resolution rung (fallback ladder's last step: op-id -> prefix -> file::symbol ->
# feature label -> NL intent) -------------------------------------------------------------------

class _FakeResponses:
    def __init__(self, output_parsed):
        self._output_parsed = output_parsed
        self.calls = 0

    def parse(self, **kwargs):
        self.calls += 1
        return SimpleNamespace(output_parsed=self._output_parsed)


class FakeClient:
    """Stands in for `get_client(repo).responses.parse(...)` -- the `tests/loop/test_plan.py`
    idiom, applied to `ugh.intent.resolve`."""

    def __init__(self, output_parsed):
        self.responses = _FakeResponses(output_parsed)


def test_check_reports_clean_store(tmp_path, capsys):
    _seed(tmp_path, 1)
    _in(tmp_path, ["log"])  # mine, so the store isn't empty
    capsys.readouterr()  # drain the priming `log` output
    assert _in(tmp_path, ["check", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True and payload["checked"] >= 1


def test_help_mentions_kernel_verbs(capsys):
    main(["help"])
    out = capsys.readouterr().out
    assert "revert" in out and "restore" in out and "check" in out
    assert "--json" in out


def test_unknown_verb_is_an_error_not_a_help_dump(capsys):
    """An unrecognized verb used to print the full help and return 0, which reads as success to any
    caller -- the same silent-no-op class the workbench fixed. It is now an error naming the token.
    The detailed contract (did-you-mean) lives in `test_cli_unknown_verb.py`."""
    assert main(["nonsense"]) == 2
    captured = capsys.readouterr()
    assert "unknown verb" in captured.err
    assert "ugh —" not in captured.out


def test_verbs_is_exactly_the_surviving_surface():
    """The daily spine, `name`/`rename`, `log`/`status`/`show`/`find` to read, `plan`, `check`, the
    collaboration and setup verbs, and the hidden `intent` hook entry point."""
    from ugh.cli import _VERBS

    assert _VERBS == {
        "save", "name", "rename", "log", "status", "show", "find", "undo", "revert", "restore",
        "resolve", "plan", "check", "sync", "push", "init", "mcp", "intent",
    }


# -- inline revert confirm gate (feedforward graph + `[y/N]` in the normal log flow) -------------
#
# A bare `ugh revert <ref>` on an interactive tty draws the feedforward inline (no modal pane) and
# gates on `[y/N]`. Both isattys are forced so the gate is entered deterministically regardless of
# how pytest was invoked, and `input` is monkeypatched so no real prompt blocks the suite.

def _head(tmp_path) -> str:
    """The newest save's foo step: one address, one op."""
    return f"a.py::foo@{GitBinding(tmp_path).head()[:7]}"


def _force_tty(monkeypatch, stdin=True, stdout=True):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: stdin)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: stdout)


def test_revert_on_a_tty_applies_when_the_user_confirms(tmp_path, monkeypatch, capsys):
    _seed(tmp_path, 2)
    _force_tty(monkeypatch)
    monkeypatch.setattr("builtins.input", lambda *a: "y")

    assert _in(tmp_path, ["revert", _head(tmp_path)]) == 0
    assert "applied" in capsys.readouterr().out
    assert (tmp_path / "a.py").read_text() == "def foo():\n    return 1\n"  # reverted


def test_revert_on_a_tty_changes_nothing_when_the_user_declines(tmp_path, monkeypatch, capsys):
    _seed(tmp_path, 2)
    _force_tty(monkeypatch)
    monkeypatch.setattr("builtins.input", lambda *a: "n")

    assert _in(tmp_path, ["revert", _head(tmp_path)]) == 1
    assert "skipped" in capsys.readouterr().out
    assert (tmp_path / "a.py").read_text() == "def foo():\n    return 2\n"  # untouched


def test_revert_without_tty_or_yes_still_refuses_with_exit_2(tmp_path, monkeypatch, capsys):
    """The machine/degrade contract is byte-unchanged: no tty and no `--yes` prints the feedforward
    and refuses (exit 2), never launching the pane."""
    _seed(tmp_path, 2)
    _force_tty(monkeypatch, stdin=False)

    assert _in(tmp_path, ["revert", _head(tmp_path)]) == 2
    assert "not applied — this was the preview" in capsys.readouterr().out
    assert (tmp_path / "a.py").read_text() == "def foo():\n    return 2\n"  # untouched


def test_revert_json_still_applies_immediately_without_the_pane(tmp_path, capsys):
    """`--json` is the immediate-apply machine contract VS Code/tests depend on -- unchanged."""
    _seed(tmp_path, 2)
    assert _in(tmp_path, ["revert", _head(tmp_path), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] and payload["applied"] and payload["undone"]
    assert (tmp_path / "a.py").read_text() == "def foo():\n    return 1\n"  # reverted



# -- P0 repair: verb spellings a developer actually types -----------------------------------------

