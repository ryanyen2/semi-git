"""`sgt undo` over the unified operation log (plan U8, R7).

`tests/test_porcelain.py` covers `undo` over the ideal-edit journal alone; this file proves the
*unified* walk-back: a mixed sequence of mutating verbs (save, revert, feature rename, edit) each
appends one event, and repeated `undo` pops them one at a time, reverse-chronologically, restoring
the prior state at every step -- the "arbitrarily far back" (= sequential-undo depth) promise.
"""

from __future__ import annotations

import json
import os

import sgt.cli as cli
from sgt.core import lens, verbs
from sgt.core.store import Store
from sgt.lens import map as lensmap
from sgt.lens import tree
from sgt.lens import verbs as fverbs
from sgt.store.gitbind import init_store


def _in(repo, argv):
    cwd = os.getcwd()
    os.chdir(repo)
    try:
        return cli.main(argv)
    finally:
        os.chdir(cwd)


def _seed(repo):
    """`a.py` (foo, bar -- independent) + `m.py` (helper, then user -- user calls helper, each its
    own commit so `helper` is a single-symbol op `edit` can target), an oracle that trivially
    compiles both files, and a seeded ideal table so later edits journal."""
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
    ideal = lens.get(repo)
    # `journal=False`: `get` already seeded the ideal table, so a plain `record_ideal` here would
    # journal the seed as an undoable event -- we want the log to start empty so the four verbs
    # below are exactly the four events undo walks back through.
    lens.record_ideal(repo, ideal, lens.put(repo, ideal, message="seed"), journal=False)
    return gb


def test_four_undos_walk_back_through_save_revert_rename_edit(tmp_path, capsys):
    repo = tmp_path / "repo"
    _seed(repo)
    ideal_after_seed = lens.get(repo).op_ids

    # 1) save: add an independent symbol, commit it as ops (one ideal_edit event).
    (repo / "a.py").write_text(
        (repo / "a.py").read_text(encoding="utf-8") + "\n\ndef baz():\n    return 3\n", encoding="utf-8"
    )
    assert _in(repo, ["save"]) == 0
    ideal_after_save = lens.get(repo).op_ids
    assert ideal_after_save != ideal_after_seed

    # 2) revert: drop an independent seed symbol (one ideal_edit event).
    bar = next(o for o in Store(repo).all_ops() if "a.py::bar" in o.footprint)
    verbs.revert(repo, bar.id)
    ideal_after_revert = lens.get(repo).op_ids
    assert ideal_after_revert != ideal_after_save

    # 3) feature rename: authored-feature reorg -- byte-neutral for the ideal (one reorg event).
    result = lensmap.build_map(repo)
    fid = next(iter(result["nodes"]))
    original_label = result["nodes"][fid].get("label", fid)
    fverbs.apply_rename(repo, fverbs.plan_rename(repo, fid, "renamed-feature"))
    assert tree.load(repo)["nodes"][fid]["label"] == "renamed-feature"

    # 4) edit: in-place change of a symbol, landed (one ideal_edit event).
    capsys.readouterr()  # drop prior verb output so the next capture is just the edit's JSON
    assert _in(repo, ["advanced", "edit", "m.py::helper", "--json"]) == 0
    draft_id = json.loads(capsys.readouterr().out)["draft_id"]
    (repo / "m.py").write_text(
        "def helper():\n    return 1  # tidy\n\n\ndef user():\n    return helper() + 1\n", encoding="utf-8"
    )
    assert _in(repo, ["advanced", "fulfill", draft_id, "--from-tree"]) == 0
    assert _in(repo, ["advanced", "commit", "--override", "pass", "--reason", "behavior-preserving edit"]) == 0
    capsys.readouterr()

    # Four undos, reverse-chronological, each restoring the prior state.
    assert _in(repo, ["undo"]) == 0                             # undo the edit
    assert lens.get(repo).op_ids == ideal_after_revert

    assert _in(repo, ["undo"]) == 0                             # undo the rename (feature reorg)
    assert tree.load(repo)["nodes"][fid]["label"] == original_label
    assert lens.get(repo).op_ids == ideal_after_revert          # reorg is byte-neutral for the ideal

    assert _in(repo, ["undo"]) == 0                             # undo the revert
    assert lens.get(repo).op_ids == ideal_after_save

    assert _in(repo, ["undo"]) == 0                             # undo the save
    assert lens.get(repo).op_ids == ideal_after_seed

    # Log exhausted.
    capsys.readouterr()
    assert _in(repo, ["undo"]) == 0
    assert "nothing to undo" in capsys.readouterr().out


def test_undo_refuses_when_it_would_clobber_a_raw_commit_and_force_overrides(tmp_path, capsys):
    """0.2c/F3 through the CLI: after a revert, a raw `git commit` lands work sgt mines on next
    contact. Plain `sgt undo` would restore the pre-revert snapshot and silently drop that work, so
    it refuses (nonzero, naming the casualty); `sgt undo --force` is the opt-in that proceeds."""
    repo = tmp_path / "repo"
    gb = _seed(repo)
    bar = next(o for o in Store(repo).all_ops() if "a.py::bar" in o.footprint)
    verbs.revert(repo, bar.id)  # one applied ideal_edit event to undo

    # a raw commit between the revert and the undo -- intervening work absent from the edit's result
    (repo / "a.py").write_text(
        (repo / "a.py").read_text(encoding="utf-8") + "\n\ndef baz():\n    return 3\n", encoding="utf-8"
    )
    gb.commit_all("RAW: add baz (no sgt)")
    lens.get(repo)  # absorb it into the current ideal

    capsys.readouterr()
    assert _in(repo, ["undo"]) != 0  # refused
    assert "baz" in capsys.readouterr().out  # names the work it would have destroyed
    assert b"def baz" in (repo / "a.py").read_bytes()  # not clobbered
    assert b"def bar" not in (repo / "a.py").read_bytes()  # revert not undone

    assert _in(repo, ["undo", "--force"]) == 0  # opt in to dropping baz
    assert b"def bar" in (repo / "a.py").read_bytes()  # revert undone
    assert b"def baz" not in (repo / "a.py").read_bytes()  # baz dropped as forced
