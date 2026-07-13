"""Tests for sgt.cli.porcelain -- the D3 daily-loop verbs `switch`/`save`/`undo` (plan U26).

The D2 refusal table itself (`sgt git <tree-mutating-sub>`) is covered end-to-end in
tests/test_cli_git_passthrough.py. This file covers the other half: the ideal-edit journal that
makes `undo` possible (`lens.record_ideal`/`lens.undo_ideal`), and `switch`/`save`/`undo`
exercised through `cli.main` on real repos -- ending with the plan's named scenario, the full
daily loop running git-free.
"""

from __future__ import annotations

import contextlib
import json
import os

import sgt.cli as cli
from sgt.core import verbs
from sgt.core.lens import current_ideal, get, undo_ideal
from sgt.core.store import Store
from sgt.store.gitbind import init_store
from tests.laws import corpus


@contextlib.contextmanager
def _in(repo):
    cwd = os.getcwd()
    os.chdir(repo)
    try:
        yield
    finally:
        os.chdir(cwd)


def _two_branches(repo_path):
    """`main` with just `a.py::foo`; `feature` (checked out) adds an independent `a.py::bar`."""
    gb, _ = init_store(repo_path)
    (repo_path / "a.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    gb.commit_all("base foo")
    base = gb.symbolic_ref().rsplit("/", 1)[-1]
    gb._git("checkout", "-q", "-b", "feature")
    (repo_path / "a.py").write_text(
        "def foo():\n    return 1\n\n\ndef bar():\n    return 2\n", encoding="utf-8"
    )
    gb.commit_all("feature: add independent bar")
    return gb, base


# ---------------------------------------------------------------------------
# The ideal-edit journal (lens.record_ideal / lens.undo_ideal)
# ---------------------------------------------------------------------------


def test_undo_ideal_is_none_when_nothing_has_been_recorded(tmp_path):
    repo = corpus.CORPUS["linear_history"].build(tmp_path / "repo")
    get(repo)
    assert undo_ideal(repo) is None


def test_undo_ideal_restores_the_ideal_from_before_the_last_apply(tmp_path):
    """`verbs.revert`'s apply path calls `lens.put` + `lens.record_ideal`, which journals the
    outgoing ideal; `undo_ideal` pops that entry and restores it exactly (set arithmetic)."""
    repo = corpus.CORPUS["linear_history"].build(tmp_path / "repo")
    original_ids = get(repo).op_ids
    ops = Store(repo).all_ops()
    baz = next(o for o in ops if "b.py::baz" in o.footprint)

    verbs.revert(repo, baz.id)
    reverted_ids = get(repo).op_ids
    assert baz.id not in reverted_ids

    result = undo_ideal(repo)
    assert result is not None
    assert result.ideal.op_ids == original_ids
    assert get(repo).op_ids == original_ids
    assert result.removed == set()  # nothing left over from the revert
    assert result.added == original_ids - reverted_ids  # baz.id came back


def test_repeated_undo_walks_back_through_two_independent_edits(tmp_path):
    """Two applied edits push two journal entries; undo pops them one at a time, oldest last."""
    repo = corpus.CORPUS["linear_history"].build(tmp_path / "repo")
    original_ids = get(repo).op_ids
    ops = Store(repo).all_ops()
    baz = next(o for o in ops if "b.py::baz" in o.footprint)
    qux_ops = [o for o in ops if "c.py::qux" in o.footprint]
    qux_tip = max(qux_ops, key=lambda o: len(o.footprint["c.py::qux"]))

    verbs.revert(repo, baz.id)
    after_first = get(repo).op_ids
    verbs.revert(repo, qux_tip.id)
    after_second = get(repo).op_ids
    assert after_second != after_first

    first_undo = undo_ideal(repo)
    assert get(repo).op_ids == after_first  # back to just the baz revert

    second_undo = undo_ideal(repo)
    assert get(repo).op_ids == original_ids  # both edits inverted

    assert undo_ideal(repo) is None  # journal exhausted
    assert first_undo.ideal.op_ids == after_first
    assert second_undo.ideal.op_ids == original_ids


# ---------------------------------------------------------------------------
# `sgt switch` / `sgt save` / `sgt undo` through cli.main
# ---------------------------------------------------------------------------


def test_switch_materializes_the_target_branchs_ideal(tmp_path):
    repo = tmp_path / "repo"
    gb, base = _two_branches(repo)
    with _in(repo):
        rc = cli.main(["switch", base])
    assert rc == 0
    assert current_ideal(repo).op_ids == get(repo).op_ids
    assert b"def bar" not in (repo / "a.py").read_bytes()  # base never had bar
    assert gb.symbolic_ref().rsplit("/", 1)[-1] == base


def test_switch_reports_a_git_error_for_an_unknown_branch(tmp_path, capsys):
    repo = tmp_path / "repo"
    _two_branches(repo)
    with _in(repo):
        rc = cli.main(["switch", "does-not-exist"])
    assert rc == 1
    assert "✗" in capsys.readouterr().out  # _fail's "✗" marker


def test_switch_json_reports_branch_and_op_count(tmp_path, capsys):
    repo = tmp_path / "repo"
    gb, base = _two_branches(repo)
    with _in(repo):
        rc = cli.main(["switch", base, "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload == {"ok": True, "branch": base, "ops": len(get(repo).op_ids)}


def test_save_reports_nothing_to_save_on_a_clean_tree(tmp_path, capsys):
    repo = corpus.CORPUS["linear_history"].build(tmp_path / "repo")
    get(repo)
    with _in(repo):
        rc = cli.main(["save"])
    assert rc == 0
    assert "nothing to save" in capsys.readouterr().out


def test_save_json_reports_nothing_to_save_on_a_clean_tree(tmp_path, capsys):
    repo = corpus.CORPUS["linear_history"].build(tmp_path / "repo")
    get(repo)
    with _in(repo):
        rc = cli.main(["save", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert payload == {"ok": True, "saved": False, "message": "nothing to save -- no uncommitted ops"}


def test_save_commits_a_witness_for_a_dirty_tree(tmp_path, capsys):
    repo = corpus.CORPUS["linear_history"].build(tmp_path / "repo")
    before_ids = get(repo).op_ids
    (repo / "d.py").write_text("def quux():\n    return 42\n", encoding="utf-8")
    with _in(repo):
        rc = cli.main(["save", "-m", "add quux"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "save" in out
    after_ids = get(repo).op_ids
    assert after_ids != before_ids
    assert current_ideal(repo).op_ids == after_ids


def test_undo_inverts_a_save_and_then_reports_nothing_to_undo(tmp_path, capsys):
    repo = corpus.CORPUS["linear_history"].build(tmp_path / "repo")
    before_ids = get(repo).op_ids
    (repo / "d.py").write_text("def quux():\n    return 42\n", encoding="utf-8")
    with _in(repo):
        cli.main(["save"])
        assert get(repo).op_ids != before_ids

        rc = cli.main(["undo"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "undo" in out
        assert get(repo).op_ids == before_ids

        rc = cli.main(["undo"])
        assert rc == 0
        assert "nothing to undo" in capsys.readouterr().out


def test_full_daily_loop_runs_git_free(tmp_path, capsys):
    """switch -> save -> undo -> a raw `git checkout` still refuses: the whole loop is sgt verbs,
    never a direct git tree mutation."""
    repo = tmp_path / "repo"
    gb, base = _two_branches(repo)
    with _in(repo):
        assert cli.main(["switch", base]) == 0
        assert gb.symbolic_ref().rsplit("/", 1)[-1] == base

        before_ids = get(repo).op_ids
        (repo / "e.py").write_text("def new_thing():\n    return 1\n", encoding="utf-8")
        assert cli.main(["save"]) == 0
        assert get(repo).op_ids != before_ids

        assert cli.main(["undo"]) == 0
        assert get(repo).op_ids == before_ids
        capsys.readouterr()

        rc = cli.main(["git", "checkout", "feature"])
        assert rc == 1
        err = capsys.readouterr().err
        assert "sgt switch" in err and "--force" in err
        assert gb.symbolic_ref().rsplit("/", 1)[-1] == base  # refused: HEAD never moved
