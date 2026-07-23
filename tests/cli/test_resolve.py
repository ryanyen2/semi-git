"""`sgt resolve <symbol>` (plan U13/R11): the guided one-verb fork resolution.

`resolve` only sequences existing `sgt.core.rewrite` verbs (`merge_op` -> `fulfill` -> oracle ->
`land`) over the fork the sync recorded in `.sgt/forks.json`, so these tests drive it end to end on
a real two-clone fork and check the error surfaces (no fork, no drafted resolution yet).
"""

from __future__ import annotations

import json

from sgt.api import forks_view
from sgt.cli.resolve import _resolve
from sgt.core import sync
from tests.core.test_sync import _BASE, _edit_and_commit, _push, _two_clones


def _forked_clone(tmp_path):
    """A clone `b` carrying one open `main.py::foo` fork after syncing a divergent teammate edit."""
    a, b = _two_clones(tmp_path, _BASE)
    _edit_and_commit(a, "main.py", "def foo():\n    return 999\n\n\ndef bar():\n    return 2\n", "A: rework foo")
    _push(a)
    _edit_and_commit(b, "main.py", "def foo():\n    return 42\n\n\ndef bar():\n    return 2\n", "B: rework foo")
    report = sync.sync(b, remote="origin", branch="main")
    assert report.forks and forks_view(b)["open"] == 1
    return b


def test_resolve_drafts_then_applies_and_closes_the_fork(tmp_path):
    """`sgt resolve <symbol>` drafts the reconciliation; after the user edits the file,
    `--apply` fulfills from the tree, passes the oracle, lands, and closes the fork."""
    b = _forked_clone(tmp_path)
    # a trivially-passing oracle so the guided --apply can land (R14's gate is real)
    (b / ".sgt" / "oracle.json").write_text(
        json.dumps({"tiers": [{"name": "t", "command": "true"}]}), encoding="utf-8"
    )

    assert _resolve(str(b), "main.py::foo", apply=False, as_json=False) == 0  # draft

    # the user reconciles both versions by hand, then applies.
    (b / "main.py").write_text("def foo():\n    return 4200\n\n\ndef bar():\n    return 2\n", encoding="utf-8")
    assert _resolve(str(b), "main.py::foo", apply=True, as_json=False) == 0

    assert forks_view(b)["open"] == 0  # the fork is closed


def test_resolve_reports_no_open_fork(tmp_path):
    """A symbol with no recorded fork errors clearly rather than drafting a spurious hollow."""
    a, b = _two_clones(tmp_path, _BASE)
    assert _resolve(str(b), "main.py::nothing", apply=False, as_json=False) == 1
    assert _resolve(str(b), "main.py::nothing", apply=True, as_json=False) == 1


def test_resolve_apply_without_a_drafted_resolution_errors(tmp_path):
    """`--apply` before any `sgt resolve <symbol>` drafted a reconciliation reports the missing
    draft, rather than silently doing nothing."""
    b = _forked_clone(tmp_path)
    assert _resolve(str(b), "main.py::foo", apply=True, as_json=False) == 1  # nothing drafted yet
