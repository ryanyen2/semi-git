"""The take-out / bring-back planner (`ugh.core.planner`), one behaviour per test."""

from __future__ import annotations

import json
import subprocess

import pytest

from ugh.core import lens, oplog, order, planner
from ugh.core.fold import code
from ugh.core.lens import current_ideal, get
from ugh.core.opindex import index_ops
from ugh.store.gitbind import GitBinding, init_store


def _repo(root, *saves):
    """A repo with one commit per save; a save maps path -> text (None deletes). Returns
    (repo, [sha per save])."""
    gb, _ = init_store(root)
    shas = []
    for files in saves:
        shas.append(_commit(root, gb, files))
    get(root)
    return root, shas


def _commit(root, gb, files):
    for path, text in files.items():
        full = root / path
        if text is None:
            full.unlink()
        else:
            full.parent.mkdir(parents=True, exist_ok=True)
            full.write_text(text, encoding="utf-8")
    return gb.commit_all("save")


def _edit(root, files):
    from ugh.store.gitbind import GitBinding

    sha = _commit(root, GitBinding(root), files)
    get(root)
    return sha


def _ops(repo, sha):
    return {o.id for o in index_ops(repo) if sha in o.provenance}


def _steps(repo, sha):
    return set(planner._Ctx(repo).of_commit.get(sha, ()))


def _tree(repo):
    return {p.relative_to(repo).as_posix(): p.read_bytes()
            for p in sorted(repo.rglob("*.py")) if ".sgt" not in p.parts}


def _out(repo, sha, **kw):
    return planner.apply(repo, planner.plan(repo, _steps(repo, sha), target=sha[:7], **kw))


def _back(repo, sha):
    return planner.apply(repo, planner.plan(repo, _steps(repo, sha), verb="restore", target=sha[:7]))


def _fold(repo):
    ideal = current_ideal(repo)
    return {p: t for p, t in code(ideal, lens.ops_with_frontier_images(repo, ideal)).items()
            if p.endswith(".py")}


def _held(p):
    return {(h["step"].unit, h["reason"]) for h in p.held}


F5 = "def f():\n    a = 1\n    b = 2\n    c = 3\n    return a + b + c\n"


def test_newest_save_out_and_back_is_byte_exact(tmp_path):
    repo, (_, x) = _repo(
        tmp_path / "r",
        {"a.py": "def f():\n    return 1\n"},
        {"a.py": "def f():\n    return 2\n\n\ndef g():\n    return f()\n", "b.py": "X = 1\n"},
    )
    after = _tree(repo)
    _out(repo, x)
    assert _tree(repo) == {"a.py": b"def f():\n    return 1\n"}
    _back(repo, x)
    assert _tree(repo) == after


def test_out_then_back_leaves_no_step_out(tmp_path):
    repo, (_, x) = _repo(
        tmp_path / "r",
        {"a.py": "def a():\n    return 1\n"},
        {"a.py": "def a():\n    return 1\n\n\ndef b():\n    return 2\n"},
    )
    _out(repo, x)
    _back(repo, x)
    assert planner.out_table(repo) == {}


def test_a_middle_save_comes_out_by_its_own_lines_and_back(tmp_path):
    repo, (_, x, _y) = _repo(
        tmp_path / "r",
        {"a.py": F5},
        {"a.py": F5.replace("a = 1", "a = 10")},
        {"a.py": F5.replace("a = 1", "a = 10").replace("c = 3", "c = 30")},
    )
    original = _tree(repo)
    p = _out(repo, x)
    assert [s.unit for s in p.undone] == ["a.py::f"]
    assert _tree(repo)["a.py"] == F5.replace("c = 3", "c = 30").encode()
    _back(repo, x)
    assert _tree(repo) == original


def test_lines_a_later_save_rewrote_hold_the_step(tmp_path):
    repo, (_, x, y) = _repo(
        tmp_path / "r",
        {"a.py": F5},
        {"a.py": F5.replace("a = 1", "a = 10")},
        {"a.py": F5.replace("a = 1", "a = 11")},
    )
    p = planner.plan(repo, _steps(repo, x), target=x)
    assert _held(p) == {("a.py::f", "same_lines")}
    assert p.held[0]["holders"] == sorted(_steps(repo, y))
    assert p.files == {}


def test_a_definition_changed_later_is_held_and_whole_takes_the_change(tmp_path):
    repo, (_, x, y) = _repo(
        tmp_path / "r",
        {"a.py": "def f():\n    return 1\n"},
        {"a.py": "def f():\n    return 1\n\n\ndef g():\n    return 2\n"},
        {"a.py": "def f():\n    return 1\n\n\ndef g():\n    return 3\n"},
    )
    p = planner.plan(repo, _steps(repo, x), target=x, removal_set=True)
    assert _held(p) == {("a.py::g", "changed_later")}
    assert {s["sha"]: s["steps"] for s in p.removal_set["saves"]}[y[:7]] == 1
    whole = planner.apply(repo, planner.plan(repo, _steps(repo, x), target=x, whole=True))
    assert set(whole.undone) >= _steps(repo, y)
    assert _tree(repo)["a.py"] == b"def f():\n    return 1\n"


def test_what_other_code_uses_is_held(tmp_path):
    repo, (_, x, _y) = _repo(
        tmp_path / "r",
        {"a.py": "def f():\n    return 1\n", "consts.py": "A = 1\n"},
        {"a.py": "def f():\n    return 1\n\n\ndef helper():\n    return 2\n",
         "consts.py": "A = 1\nLIMIT = 3\n"},
        {"a.py": "def f():\n    return 1\n\n\ndef helper():\n    return 2\n\n\n"
                 "def run():\n    return helper()\n",
         "b.py": "from consts import LIMIT\n"},
    )
    p = planner.plan(repo, _steps(repo, x), target=x)
    assert _held(p) == {("a.py::helper", "uses"), ("consts.py::LIMIT", "uses")}
    assert p.files == {}


def test_an_import_goes_only_when_nothing_needs_it(tmp_path):
    repo, (_, x, _y) = _repo(
        tmp_path / "r",
        {"a.py": "def f():\n    return 1\n"},
        {"a.py": "import os\nimport sys\n\n\ndef f():\n    return 1\n\n\n"
                 "def where():\n    return os.getcwd()\n"},
        {"a.py": "import os\nimport sys\n\n\ndef f():\n    return 1\n\n\n"
                 "def where():\n    return os.getcwd()\n\n\ndef argv():\n    return sys.argv\n"},
    )
    p = _out(repo, x)
    assert _held(p) == {("a.py::__import__::sys", "uses")}
    text = _tree(repo)["a.py"]
    assert b"import os" not in text and b"def where" not in text
    assert b"import sys" in text and b"def argv" in text


def test_steps_that_are_out_do_not_hold(tmp_path):
    repo, (_, a, b) = _repo(
        tmp_path / "r",
        {"a.py": "def f():\n    return 1\n"},
        {"a.py": "def f():\n    return 1\n\n\ndef g():\n    return 2\n"},
        {"a.py": "def f():\n    return 1\n\n\ndef g():\n    return 3\n"},
    )
    _out(repo, b)
    p = _out(repo, a)
    assert p.held == []
    assert _tree(repo)["a.py"] == b"def f():\n    return 1\n"


def test_edits_made_while_out_combine_on_restore(tmp_path):
    repo, (_, x) = _repo(tmp_path / "r", {"a.py": F5}, {"a.py": F5.replace("a = 1", "a = 10")})
    _out(repo, x)
    _edit(repo, {"a.py": F5.replace("c = 3", "c = 30")})
    _back(repo, x)
    assert _tree(repo)["a.py"] == F5.replace("a = 1", "a = 10").replace("c = 3", "c = 30").encode()


def test_edits_on_the_same_lines_while_out_hold_with_both_versions(tmp_path):
    repo, (_, x) = _repo(tmp_path / "r", {"a.py": F5}, {"a.py": F5.replace("a = 1", "a = 10")})
    _out(repo, x)
    edit = _edit(repo, {"a.py": F5.replace("a = 1", "a = 7")})
    p = planner.plan(repo, _steps(repo, x), verb="restore", target=x)
    assert _held(p) == {("a.py::f", "same_lines")}
    assert p.held[0]["holders"] == sorted(_steps(repo, edit))
    assert b"a = 7" in p.held[0]["now"] and b"a = 10" in p.held[0]["would"]
    assert p.files == {}


def _check(repo, command="python3 check.py"):
    (repo / ".sgt" / "oracle.json").write_text(json.dumps(
        {"tiers": [{"name": "run", "command": command}]}))


def test_the_check_holds_the_step_a_failure_sits_in(tmp_path):
    a0 = "def f():\n    return old()\n\n\ndef old():\n    return 1\n"
    repo, (_, x, _y) = _repo(
        tmp_path / "r",
        {"a.py": a0, "check.py": "import a\n\nassert a.f() == 1\n"},
        {"a.py": a0.replace("return old()", "return 1") + "\n\ndef extra():\n    return 2\n"},
        {"a.py": "def f():\n    return 1\n\n\ndef extra():\n    return 2\n"},
    )
    _check(repo)
    p = planner.plan(repo, _steps(repo, x), target=x)
    assert p.held == [] and {s.unit for s in p.undone} == {"a.py::f", "a.py::extra"}
    done = planner.apply(repo, p)
    assert done.check["passed"] and done.check["judge"] == "check" and done.check["rounds"] == 2
    assert _held(done) == {("a.py::f", "check")}
    assert _tree(repo)["a.py"] == b"def f():\n    return 1\n"


def test_a_failure_outside_the_undone_steps_applies_nothing(tmp_path):
    lib = "def compute():\n    return [1, 2]\n"
    app = "from lib import compute\n\n\ndef run():\n    return sum(compute())\n"
    repo, (_, x, _y) = _repo(
        tmp_path / "r",
        {"lib.py": lib, "app.py": app, "check.py": "import app\n\nassert app.run() == 3\n"},
        {"lib.py": "def compute():\n    return {'total': 3}\n"},
        {"app.py": app.replace("sum(compute())", "compute()['total']")},
    )
    _check(repo)
    before = _tree(repo)
    done = planner.apply(repo, planner.plan(repo, _steps(repo, x), target=x))
    assert done.check["passed"] is False and "app.py" in done.check["output"]
    assert _tree(repo) == before and planner.out_table(repo) == {}


def test_a_check_that_already_fails_refuses_before_writing(tmp_path):
    repo, (_, x) = _repo(
        tmp_path / "r",
        {"a.py": "def f():\n    return 1\n", "check.py": "import sys\nsys.exit(1)\n"},
        {"a.py": "def f():\n    return 1\n\n\ndef g():\n    return 2\n"},
    )
    _check(repo)
    before = _tree(repo)
    done = planner.apply(repo, planner.plan(repo, _steps(repo, x), target=x))
    assert not done.ok and done.check["passed"] is False and done.check["rounds"] == 0
    assert _tree(repo) == before


def test_without_a_check_an_unparseable_result_is_held(tmp_path):
    body = "def f(a):\n    y = (\n    )\n    return y\n"
    other = "\n\ndef other():\n    pass\n"
    later = body.replace("(\n    )", "foo(\n    a=1)") + other
    repo, (_, x, _y) = _repo(
        tmp_path / "r",
        {"a.py": body},
        {"a.py": body.replace("(\n", "foo(\n") + other},
        {"a.py": later},
    )
    p = planner.plan(repo, _steps(repo, x), target=x)
    assert p.held == [] and len(p.undone) == 2
    done = planner.apply(repo, p)
    assert _held(done) == {("a.py::f", "check")}
    assert done.check["judge"] == "parser"
    assert _tree(repo)["a.py"] == later.replace(other, "").encode()


def test_the_removal_set_never_cuts_another_save_in_two(tmp_path):
    h = "def h():\n    return 1\n"
    k = "def k():\n    return 1\n"
    g = "def g():\n    return 1\n"
    repo, (_, x, y, z) = _repo(
        tmp_path / "r",
        {"a.py": h + "\n\n" + k + "\n\n" + g},
        {"a.py": h.replace("1", "2") + "\n\n" + k + "\n\n" + g + "\n\ndef f():\n    return 1\n"},
        {"a.py": h.replace("1", "2") + "\n\n" + k + "\n\n" + g.replace("1", "2")
                 + "\n\ndef f():\n    return 2\n"},
        {"a.py": h.replace("1", "3") + "\n\n" + k.replace("1", "3") + "\n\n" + g.replace("1", "2")
                 + "\n\ndef f():\n    return 2\n"},
    )
    p = planner.plan(repo, _steps(repo, x), target=x, removal_set=True)
    counts = {s["sha"]: s["steps"] for s in p.removal_set["saves"]}
    assert counts == {x[:7]: 2, y[:7]: 1, z[:7]: 2}


def test_undo_of_a_take_out_and_of_a_bring_back(tmp_path):
    repo, (_, x) = _repo(tmp_path / "r", {"a.py": F5}, {"a.py": F5.replace("a = 1", "a = 10")})
    before = _tree(repo)
    _out(repo, x)
    out_tree = _tree(repo)
    assert set(planner.out_table(repo)) == _steps(repo, x)
    oplog.undo(repo)
    assert _tree(repo) == before and not set(planner.out_table(repo)) & _steps(repo, x)
    _out(repo, x)
    _back(repo, x)
    oplog.undo(repo)
    assert _tree(repo) == out_tree and set(planner.out_table(repo)) >= _steps(repo, x)


def test_undo_of_a_save_takes_it_out_whole(tmp_path):
    from ugh.cli.porcelain import save

    repo, _ = _repo(tmp_path / "r", {"a.py": "def f():\n    return 1\n"})
    before = _tree(repo)
    (repo / "a.py").write_text("def f():\n    return 2\n\n\ndef g():\n    return f()\n")
    assert save(str(repo))["saved"]
    outcome = oplog.undo(repo)
    assert outcome.status == "commit" and not outcome.plan.held
    assert _tree(repo) == before


# -- regressions from the review of the B2 planner ------------------------------------------


def test_re_adding_a_definition_after_taking_it_out_continues_its_chain(tmp_path):  # D1
    repo, (_, x) = _repo(tmp_path / "r", {"a.py": "def f():\n    return 1\n"},
                         {"a.py": "def f():\n    return 1\n\n\ndef g():\n    return 2\n"})
    _out(repo, x)
    s = _edit(repo, {"a.py": "def f():\n    return 1\n\n\ndef g():\n    return 5\n"})
    assert _ops(repo, s) <= current_ideal(repo).op_ids
    assert order.forks(index_ops(repo), current_ideal(repo).op_ids) == []
    assert _fold(repo) == _tree(repo)


def test_retyping_a_change_that_is_out_is_a_new_edit(tmp_path):  # D2
    repo, (_, x) = _repo(tmp_path / "r", {"a.py": F5}, {"a.py": F5.replace("a = 1", "a = 10")})
    _out(repo, x)
    s = _edit(repo, {"a.py": F5.replace("a = 1", "a = 10")})
    assert _ops(repo, s) and not _ops(repo, s) & _ops(repo, x)
    assert _fold(repo) == _tree(repo)


def test_a_whole_file_deletion_comes_out_and_back(tmp_path):  # D3
    repo, (_, x) = _repo(tmp_path / "r", {"a.py": "def f():\n    return 1\n", "notes.txt": "hello\n"},
                         {"notes.txt": None})
    assert [s.unit for s in _out(repo, x).undone] == ["notes.txt"]
    assert (repo / "notes.txt").read_text() == "hello\n"
    assert planner.out_table(repo)
    _back(repo, x)
    assert not (repo / "notes.txt").exists()


def test_a_take_out_is_never_a_holder(tmp_path):  # D4
    imp = "from os import getcwd\n\n\n"
    body = "def f():\n    a = 1\n    b = 2\n    c = 3\n    return 1\n"
    repo, (_, x, y, w) = _repo(
        tmp_path / "r", {"a.py": body}, {"a.py": imp + body},
        {"a.py": imp + body.replace("a = 1", "a = getcwd()")},
        {"a.py": imp + body.replace("a = 1", "a = getcwd()").replace("return 1", "return 2")})
    _out(repo, w)
    p = planner.plan(repo, _steps(repo, x), target=x, removal_set=True)
    assert _held(p) == {("a.py::__import__::os", "uses")}
    assert p.held[0]["holders"] == sorted(_steps(repo, y))
    assert sorted(s["sha"] for s in p.removal_set["saves"]) == sorted([x[:7], y[:7]])


def test_same_lines_names_the_later_save_that_rewrote_them(tmp_path):  # D5
    f = "def f():\n    a = 1\n    b = 2\n    c = 3\n    d = 4\n    return a + b + c\n"
    repo, (_, x, y, z) = _repo(
        tmp_path / "r", {"a.py": f, "b.py": "def k():\n    return 1\n"},
        {"a.py": f.replace("a = 1", "a = 10")}, {"a.py": f.replace("a = 1", "a = 11")},
        {"a.py": f.replace("a = 1", "a = 11").replace("d = 4", "d = 40"), "b.py": "def k():\n    return 2\n"})
    p = planner.plan(repo, _steps(repo, x), target=x, removal_set=True)
    assert [h["holders"] for h in p.held] == [sorted(_steps(repo, y))]
    assert sorted(s["sha"] for s in p.removal_set["saves"]) == sorted([x[:7], y[:7]])


def test_a_clone_knows_what_is_out(tmp_path):  # D6
    repo, (_, x) = _repo(tmp_path / "r", {"a.py": "def f():\n    return 1\n"},
                         {"a.py": "def f():\n    return 1\n\n\ndef g():\n    return 2\n"})
    _out(repo, x)
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", str(repo), str(clone)], check=True)
    lens.init(clone)
    assert set(planner.out_table(clone)) == set(planner.out_table(repo)) == _steps(clone, x)
    _back(clone, x)
    assert b"def g" in _tree(clone)["a.py"]


def test_an_interrupt_after_the_commit_leaves_a_consistent_record(tmp_path, monkeypatch):  # D8
    repo, (_, x) = _repo(tmp_path / "r", {"a.py": "def f():\n    return 1\n"},
                         {"a.py": "def f():\n    return 1\n\n\ndef g():\n    return 2\n"})
    p = planner.plan(repo, _steps(repo, x), target=x)

    def interrupt(*_a, **_k):
        raise KeyboardInterrupt

    with monkeypatch.context() as m:
        m.setattr(lens, "get", interrupt)
        with pytest.raises(KeyboardInterrupt):
            planner.apply(repo, p)
    assert GitBinding(repo).is_clean()
    get(repo)
    assert set(planner.out_table(repo)) == _steps(repo, x)
    assert _tree(repo)["a.py"] == b"def f():\n    return 1\n" == _fold(repo)["a.py"]


def test_restoring_a_deletion_of_a_re_created_file_removes_the_file(tmp_path):  # D9
    repo, shas = _repo(tmp_path / "r", {"a.py": "def f():\n    return 1\n", "b.py": "B = 1\n"},
                       {"a.py": None}, {"a.py": "def g():\n    return 4\n"}, {"a.py": None})
    _out(repo, shas[-1])
    assert _tree(repo)["a.py"] == b"def g():\n    return 4\n"
    _back(repo, shas[-1])
    assert not (repo / "a.py").exists()


def test_restoring_a_change_whose_definition_is_out_names_what_is_out(tmp_path):  # D10
    repo, (_, x1, x2) = _repo(tmp_path / "r", {"a.py": "def f():\n    return 1\n"},
                              {"a.py": "def f():\n    return 1\n\n\ndef g():\n    return 2\n"},
                              {"a.py": "def f():\n    return 1\n\n\ndef g():\n    return 3\n"})
    _out(repo, x2)
    _out(repo, x1)
    p = planner.plan(repo, _steps(repo, x2), verb="restore", target=x2)
    assert _held(p) == {("a.py::g", "changed_later")}
    assert p.held[0]["holders"] == sorted(_steps(repo, x1))


def test_a_definition_keeps_its_birth_on_its_chain_after_a_take_out(tmp_path):  # e12
    f = "def f():\n    a = 1\n    return a\n"
    repo, (base, x, _y) = _repo(tmp_path / "r", {"a.py": f}, {"a.py": f.replace("a = 1", "a = 10")},
                                {"a.py": f.replace("a = 1", "a = 10") + "\n\ndef g():\n    return 2\n"})
    _out(repo, x)
    s = _edit(repo, {"a.py": f.replace("return a", "return a + 1") + "\n\ndef g():\n    return 2\n"})
    ops = index_ops(repo)
    chain = order.ordered_chains(current_ideal(repo).op_ids, ops)["a.py::f"]
    (birth,) = [o for o in chain if o in _ops(repo, base)]
    (last,) = [o for o in chain if o in _ops(repo, s)]
    assert chain[0] == birth and chain[-1] == last and len(chain) == 4
    assert birth in order.downset(last, ops)


def test_a_comment_that_cycles_folds_back(tmp_path):  # e13
    g = lambda c: "def k():\n    return 4\n\n\n" + c + "def m():\n    return 0\n"  # noqa: E731
    repo, shas = _repo(tmp_path / "r", {"a.py": g("")}, {"a.py": g("# x\n")}, {"a.py": g("# y\n")},
                       {"a.py": g("# x\n")}, {"a.py": g("# y\n")})
    assert _fold(repo) == _tree(repo)
    assert not _ops(repo, shas[-1]) & _ops(repo, shas[2])


def test_a_take_out_survives_a_rebase(tmp_path):
    repo, (_, x) = _repo(tmp_path / "r", {"a.py": "def f():\n    return 1\n"},
                         {"a.py": "def f():\n    return 1\n\n\ndef g():\n    return 2\n"})
    _out(repo, x)
    ideal = current_ideal(repo).op_ids
    git = GitBinding(repo)._git
    branch = git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    root = git("rev-list", "--max-parents=0", "HEAD").stdout.strip()
    git("checkout", "-q", "--detach", root)
    git("commit", "-q", "--amend", "-m", "reworded")
    git("rebase", "-q", "--onto", git("rev-parse", "HEAD").stdout.strip(), root, branch)
    get(repo)
    assert current_ideal(repo).op_ids == ideal
    assert [st.unit for st in planner.out_table(repo)] == ["a.py::g"]
    assert b"def g" not in _tree(repo)["a.py"]
