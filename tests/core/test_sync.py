"""A save is a git commit, ops do not travel, and sync merges unit by unit
(docs/design/2026-11-18-b4-save-sync-brief.md).

Two clones of one bare remote in `tmp_path`, real `git`, no network. Each test drives the CLI the
way a person would (`main` inside the clone) and reads git directly to check what it did.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from ugh import names
from ugh.address import Record
from ugh.cli import main
from ugh.cli.porcelain import save
from ugh.core import lens
from ugh.store.gitbind import GitBinding, PushRejected
from tests.conftest import _clone, _init_bare, _push
from tests.laws import corpus

_BASE = "def foo():\n    return 1\n\n\ndef bar():\n    return 2\n"


def _in(repo, argv) -> int:
    cwd = os.getcwd()
    os.chdir(repo)
    try:
        return main(argv)
    finally:
        os.chdir(cwd)


def _json(repo, capsys, *argv):
    capsys.readouterr()
    rc = _in(repo, [*argv, "--json"])
    return rc, json.loads(capsys.readouterr().out)


def _save(repo: Path, text: str, message: str, as_label: str | None = None) -> str:
    (repo / "main.py").write_text(text, encoding="utf-8")
    out = save(str(repo), message=message, as_label=as_label)
    assert out["ok"] and out["saved"], out
    return out["commit"]


def _two_clones(tmp_path: Path, main_py: str) -> tuple[Path, Path]:
    """A bare remote and two clones of it, both at one shared commit that writes `main.py`."""
    remote = _init_bare(tmp_path)
    a = _clone(remote, tmp_path / "a")
    lens.init(a)
    (a / "main.py").write_text(main_py, encoding="utf-8")
    GitBinding(a).commit_all("init")
    lens.get(a)
    _push(a)
    b = _clone(remote, tmp_path / "b")
    lens.get(b)
    return a, b


def _git(repo, *args) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)


def _choices(repo, capsys) -> list[str]:
    _rc, view = _json(repo, capsys, "status")
    return [c["address"] for c in view["choices"]]


# -- a save is a git commit ---------------------------------------------------------------------

def test_save_commits_exactly_the_working_tree_with_no_op_trailers(tmp_path, capsys):
    a, _b = _two_clones(tmp_path, _BASE)
    (a / "main.py").write_text(_BASE.replace("return 1", "return 100"), encoding="utf-8")
    (a / "notes.txt").write_text("hello\n", encoding="utf-8")

    rc, out = _json(a, capsys, "save", "-m", "bump foo")

    gb = GitBinding(a)
    assert rc == 0 and out["saved"]
    assert gb.blob_bytes("HEAD", "main.py") == (a / "main.py").read_bytes()
    assert gb.blob_bytes("HEAD", "notes.txt") == b"hello\n"
    assert gb.is_clean()
    assert "Sgt-Op" not in gb.commit_message("HEAD")


def test_a_clone_made_after_a_save_has_the_same_steps_and_log_and_no_ops_travel(tmp_path, capsys):
    a, _b = _two_clones(tmp_path, _BASE)
    _save(a, _BASE.replace("return 1", "return 100"), "bump foo", as_label="speed")
    assert _in(a, ["push"]) == 0

    c = _clone(tmp_path / "remote.git", tmp_path / "c")
    assert _in(c, ["init", "--json"]) == 0

    assert _json(c, capsys, "log")[1] == _json(a, capsys, "log")[1]
    state = _git(tmp_path / "remote.git", "ls-tree", "-r", "--name-only", "refs/ugh/state").stdout
    assert state.strip() and ".sgt/ops/" not in state


# -- sync merges unit by unit -------------------------------------------------------------------

def test_sync_merges_different_definitions_changed_on_each_side(tmp_path, capsys):
    a, b = _two_clones(tmp_path, _BASE)
    _save(a, _BASE.replace("return 1", "return 100"), "bump foo")
    _push(a)
    _save(b, _BASE.replace("return 2", "return 200"), "bump bar")

    rc, _out = _json(b, capsys, "sync")

    assert rc == 0
    assert (b / "main.py").read_text() == _BASE.replace("return 1", "return 100").replace("return 2", "return 200")
    assert len(GitBinding(b).parents("HEAD")) == 2
    assert _choices(b, capsys) == []


_LONG = "def foo():\n    a = 1\n    b = 2\n    c = 3\n    return a + b + c\n\n\ndef bar():\n    return 2\n"


def test_sync_merges_one_function_changed_on_different_lines(tmp_path, capsys):
    a, b = _two_clones(tmp_path, _LONG)
    _save(a, _LONG.replace("a = 1", "a = 10"), "a is ten")
    _push(a)
    _save(b, _LONG.replace("c = 3", "c = 30"), "c is thirty")

    rc, _out = _json(b, capsys, "sync")

    assert rc == 0
    assert (b / "main.py").read_text() == _LONG.replace("a = 1", "a = 10").replace("c = 3", "c = 30")
    assert _choices(b, capsys) == []


_THEIRS = _BASE.replace("return 1", "return 999")
_OURS = _BASE.replace("return 1", "return 42")


@pytest.fixture
def choice(tmp_path, capsys):
    """Clone a changed foo's one line to 999 and pushed; clone b changed it to 42 and synced."""
    a, b = _two_clones(tmp_path, _BASE)
    _save(a, _THEIRS, "foo is 999")
    _push(a)
    _save(b, _OURS, "foo is 42")
    rc, out = _json(b, capsys, "sync")
    assert rc == 0, out
    return a, b


def test_the_same_lines_changed_differently_keep_ours_as_a_two_version_choice(choice, capsys):
    _a, b = choice
    assert (b / "main.py").read_text() == _OURS
    assert _choices(b, capsys) == ["main.py::foo"]
    assert "Ugh-Choice: main.py::foo" in GitBinding(b).commit_message("HEAD")


def test_save_refuses_while_a_choice_is_pending_and_names_it(choice, capsys):
    _a, b = choice
    (b / "main.py").write_text(_OURS.replace("return 2", "return 7"), encoding="utf-8")
    head = GitBinding(b).head()

    rc, out = _json(b, capsys, "save", "-m", "more")

    assert rc != 0 and not out["ok"]
    assert "main.py::foo" in out["error"] and "ugh resolve main.py::foo" in out["error"]
    assert GitBinding(b).head() == head


def test_resolve_prints_both_versions_and_changes_nothing(choice, capsys):
    _a, b = choice
    head = GitBinding(b).head()
    capsys.readouterr()

    assert _in(b, ["resolve", "main.py::foo"]) == 0

    printed = capsys.readouterr().out
    assert "return 42" in printed and "return 999" in printed
    assert GitBinding(b).head() == head and GitBinding(b).is_clean()


def test_resolve_theirs_writes_theirs_and_commits_the_resolution(choice, capsys):
    _a, b = choice

    rc, _out = _json(b, capsys, "resolve", "main.py::foo", "--theirs")

    assert rc == 0
    assert (b / "main.py").read_text() == _THEIRS
    assert "Ugh-Resolves: main.py::foo" in GitBinding(b).commit_message("HEAD")
    assert _choices(b, capsys) == []
    (b / "main.py").write_text(_THEIRS.replace("return 2", "return 7"), encoding="utf-8")
    assert _json(b, capsys, "save", "-m", "more")[1]["saved"]


@pytest.mark.parametrize("pick", ["--theirs", "--ours"])
def test_the_record_reproduces_the_file_after_a_resolution(choice, capsys, pick):
    from ugh.core import lens
    from ugh.core.fold import code

    _a, b = choice
    assert _json(b, capsys, "resolve", "main.py::foo", pick)[0] == 0
    ideal = lens.current_ideal(b)
    assert code(ideal, lens.ops_with_frontier_images(b, ideal))["main.py"] == (b / "main.py").read_bytes()


def test_resolve_apply_keeps_what_the_file_now_holds(choice, capsys):
    _a, b = choice
    (b / "main.py").write_text(_BASE.replace("return 1", "return 42 + 999"), encoding="utf-8")

    rc, _out = _json(b, capsys, "resolve", "main.py::foo", "--apply")

    assert rc == 0
    assert GitBinding(b).blob_bytes("HEAD", "main.py") == _BASE.replace("return 1", "return 42 + 999").encode()
    assert _choices(b, capsys) == []


def test_the_other_clone_sees_the_choice_and_its_resolution_after_its_own_sync(choice, capsys):
    a, b = choice
    _push(b)
    assert _json(a, capsys, "sync")[0] == 0
    assert GitBinding(a).head() == GitBinding(b).head()  # a fast-forward
    assert _choices(a, capsys) == ["main.py::foo"]

    assert _json(b, capsys, "resolve", "main.py::foo", "--theirs")[0] == 0
    _push(b)
    assert _json(a, capsys, "sync")[0] == 0
    assert _choices(a, capsys) == []
    assert (a / "main.py").read_text() == _THEIRS


def test_a_definition_deleted_on_one_side_and_changed_on_the_other_is_a_choice(tmp_path, capsys):
    a, b = _two_clones(tmp_path, _BASE)
    _save(a, "def foo():\n    return 1\n", "drop bar")
    _push(a)
    _save(b, _BASE.replace("return 2", "return 200"), "bump bar")

    assert _json(b, capsys, "sync")[0] == 0

    assert (b / "main.py").read_text() == _BASE.replace("return 2", "return 200")
    assert _choices(b, capsys) == ["main.py::bar"]


def test_sync_is_a_no_op_when_theirs_is_already_in_ours(tmp_path, capsys):
    a, b = _two_clones(tmp_path, _BASE)
    _save(b, _OURS, "foo is 42")
    head = GitBinding(b).head()

    rc, out = _json(b, capsys, "sync")

    assert rc == 0 and out["message"] == "already up to date"
    assert GitBinding(b).head() == head


@pytest.mark.parametrize("taken", ["ours", "theirs", "both"])
def test_a_plain_git_merge_is_settled(tmp_path, capsys, taken):
    a, b = _two_clones(tmp_path, _BASE)
    theirs_sha = _save(a, _THEIRS, "foo is 999")
    _push(a)
    ours_sha = _save(b, _OURS, "foo is 42")
    _git(b, "fetch", "-q", "origin")
    assert _git(b, "merge", "origin/main").returncode != 0  # git conflicts on foo
    text = {"ours": _OURS, "theirs": _THEIRS, "both": _BASE.replace("return 1", "return 42 + 999")}[taken]
    (b / "main.py").write_text(text, encoding="utf-8")
    _git(b, "add", "main.py")
    assert _git(b, "commit", "-q", "--no-edit").returncode == 0

    assert _choices(b, capsys) == []
    assert _json(b, capsys, "check", "--tree")[1]["drift"] == []
    rec = Record(b)
    assert {st.sha for st in rec.ctx.unit_steps["main.py::foo"]} >= {ours_sha, theirs_sha}


# -- names and the state ref --------------------------------------------------------------------

def test_names_reach_the_other_clone_through_push_and_sync(tmp_path, capsys):
    a, b = _two_clones(tmp_path, _BASE)
    _save(a, _THEIRS, "foo is 999", as_label="speed")
    assert _in(a, ["push"]) == 0
    assert _json(b, capsys, "sync")[0] == 0
    assert set(names.load(b).values()) == {"speed"}

    assert _json(b, capsys, "name", "main.py::bar", "billing")[0] == 0
    assert _in(b, ["push"]) == 0
    assert _json(a, capsys, "sync")[0] == 0
    assert names.load(a) == names.load(b) and set(names.load(a).values()) == {"speed", "billing"}


def test_ideal_for_ref_is_the_ideal_mined_from_that_refs_history(tmp_path):
    repo = corpus.CORPUS["diverged_chain"].build(tmp_path / "repo")
    corpus.checkout(repo, "release")
    release = lens.get(repo).op_ids
    corpus.checkout(repo, "main")
    lens.get(repo)
    # an old `Sgt-Op:` trailer naming the other branch's op is ignored
    GitBinding(repo).commit_all("note", trailers="".join(f"Sgt-Op: {o}\n" for o in sorted(release)))
    main = lens.get(repo).op_ids

    assert lens.ideal_for_ref(repo, "release").op_ids == release
    assert lens.ideal_for_ref(repo, "main").op_ids == main


# -- push ---------------------------------------------------------------------------------------

def test_push_never_forces_and_routes_a_rejection_to_sync(tmp_path, capsys):
    a, b = _two_clones(tmp_path, _BASE)
    _save(a, _THEIRS, "foo is 999")
    _push(a)
    _save(b, _BASE.replace("return 2", "return 8"), "bar is 8")
    gb = GitBinding(b)

    with pytest.raises(PushRejected):
        gb.push("origin", "main")
    rc, out = _json(b, capsys, "push")
    assert rc == 1 and out["rejected"] and out["remedy"] == "ugh sync origin main"
    assert gb.head() != gb.rev_parse("origin/main")


def test_push_leaves_the_branch_alone_when_the_state_ref_cannot_publish(tmp_path, monkeypatch):
    from ugh.core.sync import state_ref

    a, _b = _two_clones(tmp_path, _BASE)
    _save(a, _THEIRS, "foo is 999")
    before = _git(tmp_path / "remote.git", "rev-parse", "main").stdout

    def boom(*_args, **_kwargs):
        raise state_ref.StateRefError("simulated")

    monkeypatch.setattr(state_ref, "publish_and_push", boom)
    assert _in(a, ["push"]) != 0
    assert _git(tmp_path / "remote.git", "rev-parse", "main").stdout == before
