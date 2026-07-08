"""CLI surface tests (the operation-ideal kernel, plan U7/U8/U9 flipped onto the CLI in U10):
argument parsing, human-readable rendering, and `--json` output for the kernel-backed verbs.
Verb *behavior* (the algebra) is tested in `tests/core/`; this file is the thin CLI layer only.
"""

import json
import os

from sgt.cli import main
from sgt.store.gitbind import init_store


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


def test_init_and_log_roundtrip(tmp_path, capsys):
    _seed(tmp_path, 1)
    capsys.readouterr()  # drain seed commit output (none, but keep symmetry with other tests)
    assert _in(tmp_path, ["log"]) == 0
    out = capsys.readouterr().out
    assert "op(s)" in out and "a.py::foo" in out


def test_log_json_is_machine_readable(tmp_path, capsys):
    _seed(tmp_path, 1)
    assert _in(tmp_path, ["log", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["count"] >= 1
    assert any("a.py::foo" in [f["symbol"] for f in op["footprint"]] for op in payload["ops"])


def test_state_shows_frontier_and_coverage(tmp_path, capsys):
    _seed(tmp_path, 1)
    assert _in(tmp_path, ["state", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert "a.py::foo" in payload["frontier"]
    assert payload["covered_paths"] == ["a.py"]
    assert payload["oracle_configured"] is False
    assert payload["oracle_verdict"] is None


def test_revert_emit_previews_without_writing(tmp_path, capsys):
    _seed(tmp_path, 2)
    assert _in(tmp_path, ["revert", "--emit", "a.py::foo"]) == 0
    out = capsys.readouterr().out
    assert "[revert] a.py::foo" in out
    assert (tmp_path / "a.py").read_text() == "def foo():\n    return 2\n"  # untouched


def test_revert_then_restore_roundtrip(tmp_path, capsys):
    _seed(tmp_path, 2)
    assert _in(tmp_path, ["revert", "a.py::foo"]) == 0
    capsys.readouterr()
    assert (tmp_path / "a.py").read_text() == "def foo():\n    return 1\n"

    assert _in(tmp_path, ["restore", "--json", "a.py::foo"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True and payload["added"]
    assert (tmp_path / "a.py").read_text() == "def foo():\n    return 2\n"


def test_revert_unknown_ref_fails_with_message(tmp_path, capsys):
    _seed(tmp_path, 1)
    assert _in(tmp_path, ["revert", "nope::nothing"]) == 1
    assert "✗" in capsys.readouterr().out


def test_diff_between_refs(tmp_path, capsys):
    gb = _seed(tmp_path, 1)
    base = gb.symbolic_ref().rsplit("/", 1)[-1]
    gb._git("checkout", "-q", "-b", "feature")
    (tmp_path / "b.py").write_text("def bar():\n    return 1\n", encoding="utf-8")
    gb.commit_all("feature: add bar")
    _in(tmp_path, ["log"])  # mine feature
    gb._git("checkout", "-q", base)
    capsys.readouterr()  # drain the priming `log` output

    assert _in(tmp_path, ["diff", "--json", base, "feature"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert "b.py::bar" in payload["by_symbol"]


def test_fsck_reports_clean_store(tmp_path, capsys):
    _seed(tmp_path, 1)
    _in(tmp_path, ["log"])  # mine, so the store isn't empty
    capsys.readouterr()  # drain the priming `log` output
    assert _in(tmp_path, ["fsck", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True and payload["checked"] >= 1


def test_oracle_run_with_no_config_warns(tmp_path, capsys):
    _seed(tmp_path, 1)
    assert _in(tmp_path, ["oracle", "run"]) == 0
    assert "no oracle configured" in capsys.readouterr().out


def test_oracle_override_then_state_shows_verdict(tmp_path, capsys):
    _seed(tmp_path, 1)
    assert _in(tmp_path, ["oracle", "override", "--status", "pass", "--reason", "manual check"]) == 0
    capsys.readouterr()

    assert _in(tmp_path, ["state", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    # No `.sgt/oracle.json` -> not "configured", so state doesn't surface the override either
    # (a repo can still call `oracle override` without any tier config; `state` only surfaces
    # the verdict once an oracle is declared, per R13's "no config" degrade).
    assert payload["oracle_configured"] is False


def test_help_mentions_kernel_verbs(capsys):
    main(["help"])
    out = capsys.readouterr().out
    assert "revert" in out and "restore" in out and "oracle" in out and "fsck" in out
    assert "--json" in out


def test_unknown_verb_falls_back_to_help(capsys):
    assert main(["nonsense"]) == 0
    assert "sgt —" in capsys.readouterr().out
