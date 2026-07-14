"""`sgt git <args...>` passthrough (plan U18, C8; refusal added in U26/D2): argv[1:] is forwarded
to real git verbatim -- no `--json` stripping, no flag rewriting -- EXCEPT a tree-mutating
subcommand (`porcelain.REFUSALS`), which is refused (never run) with the native sgt verb named,
unless `--force` overrides (the token is consumed, the plain git command runs, and the out-of-band
detector re-mines on next contact). U18 shipped this as an advisory; U26's porcelain plan turned it
into the refusal the design doc §1 routing table calls for.
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


def test_refuses_tree_mutating_subcommand_naming_the_sgt_verb(monkeypatch, capsys):
    """A tree-mutating subcommand is refused (git never runs), exits non-zero, and names the sgt
    remedy on stderr (D2). `checkout` routes to `sgt switch`; `pull` to `sgt sync`."""
    calls = _record_run(monkeypatch)
    rc = cli.main(["git", "checkout", "main"])
    assert rc == 1
    assert calls == []  # git was NOT run
    err = capsys.readouterr().err
    assert "sgt switch" in err and "--force" in err

    rc = cli.main(["git", "pull", "origin", "main"])
    assert rc == 1
    assert calls == []
    assert "sgt sync" in capsys.readouterr().err


def test_force_overrides_refusal_and_consumes_the_token(monkeypatch, capsys):
    """`--force` overrides the refusal: git runs, but the override token is stripped so the plain
    git command reaches git (uniform across subcommands that don't accept `--force`)."""
    calls = _record_run(monkeypatch)
    rc = cli.main(["git", "checkout", "--force", "main"])
    assert rc == 0
    assert calls == [["git", "checkout", "main"]]  # --force consumed, not forwarded
    assert capsys.readouterr().err == ""


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
