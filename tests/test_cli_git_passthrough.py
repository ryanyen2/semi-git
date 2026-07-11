"""`sgt git <args...>` passthrough (plan U18, C8): argv[1:] is forwarded to real git verbatim --
no `--json` stripping, no flag rewriting -- with a stderr advisory (never a refusal) for
tree-mutating subcommands. Refusal is deferred to the porcelain plan; this is advisory-only.
"""

import os

import sgt.cli as cli
from sgt.store.gitbind import init_store


class _FakeResult:
    def __init__(self, returncode):
        self.returncode = returncode


def _record_run(monkeypatch, returncode=0):
    """Patch the subprocess.run the passthrough calls; record each forwarded command."""
    calls = []

    def fake_run(cmd, *args, **kwargs):
        calls.append(cmd)
        return _FakeResult(returncode)

    monkeypatch.setattr(cli.subprocess, "run", fake_run)
    return calls


def test_forwards_args_verbatim_and_propagates_exit_code(monkeypatch):
    """Short flags, `=`-valued flags, and positional args pass through unchanged; git's exit
    code is what `main` returns."""
    calls = _record_run(monkeypatch, returncode=7)
    rc = cli.main(["git", "-c", "core.pager=cat", "log", "--format=%H", "-n1"])
    assert calls == [["git", "-c", "core.pager=cat", "log", "--format=%H", "-n1"]]
    assert rc == 7


def test_advisory_fires_for_tree_mutating_subcommand(monkeypatch, capsys):
    _record_run(monkeypatch)
    cli.main(["git", "checkout", "-b", "feature"])
    err = capsys.readouterr().err
    assert "bypasses sgt's own tracking" in err
    assert "sgt sync" in err and "sgt log" in err


def test_no_advisory_for_read_only_subcommands(monkeypatch, capsys):
    _record_run(monkeypatch)
    cli.main(["git", "status"])
    cli.main(["git", "log"])
    assert capsys.readouterr().err == ""


def test_leading_git_global_flag_is_not_misparsed(monkeypatch, capsys):
    """`sgt git --no-pager log`: the leading git global is forwarded untouched (not read as sgt's
    own `--json`-stripping logic), and it is not mistaken for a tree-mutating subcommand."""
    calls = _record_run(monkeypatch)
    cli.main(["git", "--no-pager", "log", "--json"])
    # `--json` is NOT stripped; the whole arg vector is forwarded byte-for-byte.
    assert calls == [["git", "--no-pager", "log", "--json"]]
    assert capsys.readouterr().err == ""


def test_real_git_exit_code_propagates(tmp_path):
    """End-to-end against real git: a passthrough command's real exit code reaches the caller."""
    init_store(tmp_path)
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        assert cli.main(["git", "rev-parse", "--is-inside-work-tree"]) == 0
        assert cli.main(["git", "cat-file", "-e", "does-not-exist"]) != 0
    finally:
        os.chdir(cwd)
