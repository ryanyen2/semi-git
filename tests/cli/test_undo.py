"""`ugh undo` over the unified operation log (plan U8, R7).

`tests/test_porcelain.py` covers `undo` over the ideal-edit journal alone; this file proves the
*unified* walk-back: a mixed sequence of mutating verbs (save, revert, feature rename) each
appends one event, and repeated `undo` pops them one at a time, reverse-chronologically, restoring
the prior state at every step -- the "arbitrarily far back" (= sequential-undo depth) promise.
"""

from __future__ import annotations

import json
import os

import ugh.cli as cli
from ugh.core import lens
from ugh.core.store import Store
from ugh.lens import map as lensmap
from ugh.lens import tree
from ugh.lens import verbs as fverbs
from ugh.store.gitbind import init_store



def _revert(repo, op_id):
    from ugh.core import planner

    return planner.apply(repo, planner.plan(repo, {op_id}, target=op_id))

def _in(repo, argv):
    cwd = os.getcwd()
    os.chdir(repo)
    try:
        return cli.main(argv)
    finally:
        os.chdir(cwd)


def _seed(repo):
    """`a.py` (foo, bar -- independent) + `m.py` (helper, then user -- user calls helper, each its
    own commit), an oracle that trivially compiles both files, and a seeded ideal table so later
    edits journal."""
    gb, _ = init_store(repo)
    (repo / "a.py").write_text("def foo():\n    return 1\n\n\ndef bar():\n    return 2\n", encoding="utf-8")
    (repo / "m.py").write_text("def helper():\n    return 1\n", encoding="utf-8")
    gb.commit_all("seed")
    (repo / "m.py").write_text(
        "def helper():\n    return 1\n\n\ndef user():\n    return helper() + 1\n", encoding="utf-8"
    )
    gb.commit_all("add user")
    (repo / ".sgt").mkdir(exist_ok=True)
    (repo / ".sgt" / "oracle.json").write_text(
        json.dumps({"tiers": [{"name": "c", "command": "python -m py_compile a.py m.py"}]}),
        encoding="utf-8",
    )
    lens.get(repo)
    return gb


def _files(repo):
    return {p: (repo / p).read_bytes() for p in ("a.py", "m.py")}


def test_three_undos_walk_back_through_save_revert_rename(tmp_path, capsys):
    repo = tmp_path / "repo"
    _seed(repo)
    after_seed = _files(repo)

    (repo / "a.py").write_text(
        (repo / "a.py").read_text(encoding="utf-8") + "\n\ndef baz():\n    return 3\n", encoding="utf-8"
    )
    assert _in(repo, ["save"]) == 0
    after_save = _files(repo)

    bar = next(o for o in Store(repo).all_ops() if "a.py::bar" in o.footprint)
    _revert(repo, bar.id)
    after_revert = _files(repo)
    assert after_revert != after_save

    result = lensmap.build_map(repo)
    fid = next(iter(result["nodes"]))
    original_label = result["nodes"][fid].get("label", fid)
    fverbs.apply_rename(repo, fverbs.plan_rename(repo, fid, "renamed-feature"))
    assert tree.load(repo)["nodes"][fid]["label"] == "renamed-feature"
    capsys.readouterr()

    assert _in(repo, ["undo"]) == 0                             # undo the rename (feature reorg)
    assert tree.load(repo)["nodes"][fid]["label"] == original_label
    assert _files(repo) == after_revert

    assert _in(repo, ["undo"]) == 0                             # undo the revert
    assert _files(repo) == after_save

    assert _in(repo, ["undo"]) == 0                             # undo the save
    assert _files(repo) == after_seed

    capsys.readouterr()
    assert _in(repo, ["undo"]) == 0
    assert "nothing to undo" in capsys.readouterr().out


def test_undo_of_a_take_out_keeps_a_raw_commit_made_since(tmp_path, capsys):
    """Undo un-applies only the take-out, so work committed after it survives."""
    repo = tmp_path / "repo"
    gb = _seed(repo)
    bar = next(o for o in Store(repo).all_ops() if "a.py::bar" in o.footprint)
    _revert(repo, bar.id)
    (repo / "a.py").write_text(
        (repo / "a.py").read_text(encoding="utf-8") + "\n\ndef baz():\n    return 3\n", encoding="utf-8"
    )
    gb.commit_all("RAW: add baz (no ugh)")
    lens.get(repo)

    assert _in(repo, ["undo"]) == 0
    assert b"def bar" in (repo / "a.py").read_bytes()
    assert b"def baz" in (repo / "a.py").read_bytes()


def test_emit_says_what_undo_would_reverse_without_reversing_it(tmp_path, capsys):
    """`ugh undo --emit`: the dry run, for a caller that has to draw a confirm dialog."""
    repo = tmp_path / "repo"
    _seed(repo)
    bar = next(o for o in Store(repo).all_ops() if "a.py::bar" in o.footprint)
    _revert(repo, bar.id)
    assert b"def bar" not in (repo / "a.py").read_bytes()

    capsys.readouterr()
    assert _in(repo, ["undo", "--emit", "--json"]) == 0
    view = json.loads(capsys.readouterr().out)
    assert view["ok"] is True and view["applied"] is False
    assert view["kind"] == "commit" and view["undone"]
    assert any("bar" in s for s in view["symbols"])
    assert b"def bar" not in (repo / "a.py").read_bytes()
