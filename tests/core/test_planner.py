"""The take-out / bring-back planner (`ugh.core.planner`), one behaviour per test."""

from __future__ import annotations

import json

from ugh.core import oplog, planner
from ugh.core.lens import get
from ugh.core.opindex import index_ops
from ugh.store.gitbind import init_store


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


def _tree(repo):
    return {p.relative_to(repo).as_posix(): p.read_bytes()
            for p in sorted(repo.rglob("*.py")) if ".sgt" not in p.parts}


def _out(repo, sha, **kw):
    return planner.apply(repo, planner.plan(repo, _ops(repo, sha), target=sha, **kw))


def _back(repo, sha):
    return planner.apply(repo, planner.plan(repo, _ops(repo, sha), verb="restore", target=sha))


def _held(p):
    return {(h["step"].symbol, h["reason"]) for h in p.held}


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


def test_a_middle_save_comes_out_by_its_own_lines_and_back(tmp_path):
    repo, (_, x, _y) = _repo(
        tmp_path / "r",
        {"a.py": F5},
        {"a.py": F5.replace("a = 1", "a = 10")},
        {"a.py": F5.replace("a = 1", "a = 10").replace("c = 3", "c = 30")},
    )
    original = _tree(repo)
    p = _out(repo, x)
    assert [s.symbol for s in p.undone] == ["a.py::f"]
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
    p = planner.plan(repo, _ops(repo, x), target=x)
    assert _held(p) == {("a.py::f", "same_lines")}
    assert p.held[0]["holders"] == sorted(_ops(repo, y))
    assert p.files == {}


def test_a_definition_changed_later_is_held_and_whole_takes_the_change(tmp_path):
    repo, (_, x, y) = _repo(
        tmp_path / "r",
        {"a.py": "def f():\n    return 1\n"},
        {"a.py": "def f():\n    return 1\n\n\ndef g():\n    return 2\n"},
        {"a.py": "def f():\n    return 1\n\n\ndef g():\n    return 3\n"},
    )
    p = planner.plan(repo, _ops(repo, x), target=x)
    assert _held(p) == {("a.py::g", "changed_later")}
    assert {s["sha"]: s["steps"] for s in p.removal_set["saves"]}[y[:7]] == 1
    whole = planner.apply(repo, planner.plan(repo, _ops(repo, x), target=x, whole=True))
    assert {s.op for s in whole.undone} >= _ops(repo, y)
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
    p = planner.plan(repo, _ops(repo, x), target=x)
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


def test_edits_on_the_same_lines_while_out_are_two_versions(tmp_path):
    repo, (_, x) = _repo(tmp_path / "r", {"a.py": F5}, {"a.py": F5.replace("a = 1", "a = 10")})
    _out(repo, x)
    _edit(repo, {"a.py": F5.replace("a = 1", "a = 7")})
    p = planner.plan(repo, _ops(repo, x), verb="restore", target=x)
    assert _held(p) == {("a.py::f", "two_versions")}
    assert b"a = 7" in p.held[0]["now"] and b"a = 10" in p.held[0]["would"]
    assert p.files == {}


def test_the_check_holds_what_it_points_at_and_gives_back_the_rest(tmp_path):
    lib = "def compute():\n    return [1, 2]\n"
    app = "from lib import compute\n\n\ndef run():\n    return sum(compute())\n"
    check = "import app\n\nassert app.run() == 3\n"
    repo, (_, x, _y) = _repo(
        tmp_path / "r",
        {"lib.py": lib, "app.py": app, "check.py": check},
        {"lib.py": "def compute():\n    return {'total': 3}\n\n\ndef extra():\n    return 1\n"},
        {"app.py": app.replace("sum(compute())", "compute()['total']")},
    )
    (repo / ".sgt" / "oracle.json").write_text(json.dumps(
        {"tiers": [{"name": "run", "command": "python3 check.py"}]}))
    p = planner.plan(repo, _ops(repo, x), target=x)
    assert p.held == [] and len(p.undone) == 2
    done = planner.apply(repo, p)
    assert done.check["passed"] and done.check["judge"] == "check"
    assert _held(done) == {("lib.py::compute", "check")}
    assert [s.symbol for s in done.undone] == ["lib.py::extra"]
    assert b"extra" not in _tree(repo)["lib.py"]


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
    p = planner.plan(repo, _ops(repo, x), target=x)
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
    p = planner.plan(repo, _ops(repo, x), target=x)
    counts = {s["sha"]: s["steps"] for s in p.removal_set["saves"]}
    assert counts == {x[:7]: 2, y[:7]: 1, z[:7]: 2}


def test_undo_puts_back_the_table_and_the_tree(tmp_path):
    repo, (_, x) = _repo(tmp_path / "r", {"a.py": F5}, {"a.py": F5.replace("a = 1", "a = 10")})
    before = _tree(repo)
    _out(repo, x)
    out_tree, out_table = _tree(repo), planner.load_out(repo)
    assert out_table
    oplog.undo(repo)
    assert _tree(repo) == before and planner.load_out(repo) == {}
    _out(repo, x)
    _back(repo, x)
    oplog.undo(repo)
    assert _tree(repo) == out_tree and planner.load_out(repo) == out_table
