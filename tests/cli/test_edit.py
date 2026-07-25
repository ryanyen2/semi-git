"""CLI dispatch tests for `sgt edit <selection>` (plan U4, R5/KTD5).

Verb *behavior* -- the chain-extension hollow, the mechanical repoint, the red-oracle repair path
-- is tested in `tests/core/test_rewrite.py`; this file is the thin CLI layer: selection resolves
(U1), the edit hollow drafts, and the `fulfill --from-tree` -> `commit` spine flow round-trips.
"""

from __future__ import annotations

import json
import os

from sgt.core.lens import get
from sgt.core.store import Store
from sgt.cli import main
from sgt.store.gitbind import init_store


def _in(tmp_path, argv):
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        return main(argv)
    finally:
        os.chdir(cwd)


def _seed(tmp_path):
    """`helper` + one direct caller, each its own commit (a real reference edge to repoint)."""
    gb, _ = init_store(tmp_path)
    (tmp_path / "m.py").write_text("def helper():\n    return 1\n", encoding="utf-8")
    gb.commit_all("add helper")
    (tmp_path / "m.py").write_text(
        "def helper():\n    return 1\n\n\ndef user():\n    return helper() + 1\n", encoding="utf-8"
    )
    gb.commit_all("add user")
    (tmp_path / ".sgt").mkdir(exist_ok=True)
    (tmp_path / ".sgt" / "oracle.json").write_text(
        json.dumps({"tiers": [{"name": "py_compile", "command": "python -m py_compile m.py"}]}),
        encoding="utf-8",
    )
    return gb


def test_edit_verb_is_registered_and_dispatches(tmp_path):
    # U14: `edit` demoted under `advanced` (opt-in oracle-gated ceremony; ordinary edits go through
    # plain `save`). No longer a top-level verb; re-homed via `_ROUTING`.
    from sgt.cli import _ROUTING, _VERBS

    assert "edit" not in _VERBS
    assert _ROUTING["edit"] == "advanced"


def test_edit_resolves_a_symbol_selection_and_drafts_a_hollow(tmp_path, capsys):
    _seed(tmp_path)
    rc = _in(tmp_path, ["advanced", "edit", "m.py::helper", "--json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] and out["verb"] == "edit" and out["draft_id"]
    assert len(out["hollow_ids"]) == 1


def test_edit_refuses_an_unresolvable_selection(tmp_path, capsys):
    _seed(tmp_path)
    rc = _in(tmp_path, ["advanced", "edit", "m.py::nope"])
    assert rc == 1


def test_edit_fulfill_from_tree_then_commit_lands_a_behavior_preserving_edit(tmp_path, capsys):
    _seed(tmp_path)
    rc = _in(tmp_path, ["advanced", "edit", "m.py::helper", "--json"])
    assert rc == 0
    draft_id = json.loads(capsys.readouterr().out)["draft_id"]

    # The user edits the symbol in place (behavior-preserving), then fulfills from the tree.
    (tmp_path / "m.py").write_text(
        "def helper():\n    return 1  # tidy\n\n\ndef user():\n    return helper() + 1\n",
        encoding="utf-8",
    )
    assert _in(tmp_path, ["advanced", "fulfill", draft_id, "--from-tree"]) == 0
    capsys.readouterr()
    # Land via the same commit spine the other rewrite verbs use (genuine oracle-green landing is
    # proven at the core level in tests/core/test_rewrite.py; the CLI `oracle run`->staged-candidate
    # keying is a pre-existing gap shared by all rewrite verbs, out of scope here).
    assert _in(tmp_path, ["advanced", "commit", "--override", "pass", "--reason", "behavior-preserving edit"]) == 0

    ops = Store(tmp_path).all_ops()
    live = get(tmp_path).frontier(ops)
    by_id = {o.id: o for o in ops}
    # helper carries the edited bytes; user is mechanically repointed (kind "repoint"), unchanged.
    assert b"# tidy" in by_id[live["m.py::helper"]].images["m.py::helper"]
    assert by_id[live["m.py::user"]].kind == "repoint"
