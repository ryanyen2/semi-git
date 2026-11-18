"""Materializing a fold onto disk: the `revert/restore --emit --out <dir>` counterfactual. These
pin the two
properties that make the difference: the bytes are the fold's own bytes, and the write is a
*sync* against a long-lived overlay directory (the render panel's target, which also holds
`node_modules` and a dev-server cache) rather than a dump into a directory anyone may wipe.
"""

import json
import subprocess

from ugh.api import FOLD_MANIFEST
from ugh.core.fold import code
from ugh.core.store import Store
from ugh.core.lens import current_ideal, get, ideal_for_ref
from tests.laws import corpus


def _mined(tmp_path, name):
    repo = corpus.CORPUS[name].build(tmp_path / "repo")
    get(repo)
    return repo


def _forward_subtracting_target(repo):
    """A step address whose revert changes files. Found rather than hardcoded so a re-mine that
    reshuffles op-ids fails loudly here instead of silently weakening every assertion downstream."""
    from ugh.address import Record, resolve
    from ugh.core import planner

    rec = Record(repo)
    for st in sorted(rec.ctx.steps):
        address = rec.step(st)["address"]
        p = planner.plan(repo, resolve(repo, address, rec), target=address)
        if p.ok and p.files:
            return address
    raise AssertionError("fixture has no revert that changes a file")


def _in(repo, argv):
    import os

    from ugh.cli import main

    cwd = os.getcwd()
    os.chdir(repo)
    try:
        return main(argv)
    finally:
        os.chdir(cwd)


def _emit(repo, argv):
    """Run a CLI verb with `--json` and return its parsed view."""
    import contextlib
    import io

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        _in(repo, argv)
    return json.loads(buf.getvalue())


def _tree(out):
    """Every file under `out` except the manifest, as `{relative path: bytes}`."""
    return {
        str(p.relative_to(out)): p.read_bytes()
        for p in sorted(out.rglob("*")) if p.is_file() and p.name != FOLD_MANIFEST
    }


def test_ideal_for_ref_agrees_with_the_current_ideal_after_a_take_out(tmp_path):
    """Every op comes from mining a commit, the take-out's included, so the ideal a ref's history
    implies is the current ideal."""
    repo = _mined(tmp_path, "linear_history")
    target = _forward_subtracting_target(repo)
    assert _in(repo, ["revert", target, "--json"]) == 0

    from ugh.core import opindex

    live = current_ideal(repo).op_ids
    assert all(o.provenance for o in opindex.index_ops(repo) if o.id in live)
    assert set(ideal_for_ref(repo, "HEAD").op_ids) == set(live)


def test_emit_out_materializes_the_tree_the_apply_leaves(tmp_path):
    """`revert --emit --out <dir>` writes the tree the plan describes; applying the same revert for
    real on a second copy produces a byte-identical tree."""
    repo = _mined(tmp_path, "linear_history")
    applied = corpus.CORPUS["linear_history"].build(tmp_path / "applied")
    get(applied)

    target = _forward_subtracting_target(repo)
    out = tmp_path / "counterfactual"

    view = _emit(repo, ["revert", target, "--emit", "--out", str(out), "--json"])
    assert view["ok"] and view["out"]["ok"]

    assert _in(applied, ["revert", target, "--json"]) == 0
    assert _tree(out) == code(current_ideal(applied), Store(applied).all_ops())


def test_restore_emit_out_round_trips_the_same_way(tmp_path):
    """The inverse verb gets the same treatment: `restore --emit --out` writes the tree restoring
    would leave, and the applied restore on a second copy matches it byte for byte."""
    repo = _mined(tmp_path, "linear_history")
    applied = corpus.CORPUS["linear_history"].build(tmp_path / "applied")
    get(applied)

    target = _forward_subtracting_target(repo)
    assert _in(repo, ["revert", target, "--json"]) == 0
    assert _in(applied, ["revert", target, "--json"]) == 0

    out = tmp_path / "counterfactual"
    view = _emit(repo, ["restore", target, "--emit", "--out", str(out), "--json"])
    assert view["ok"] and view["out"]["ok"] and view["files"]

    assert _in(applied, ["restore", target, "--json"]) == 0
    assert _tree(out) == code(current_ideal(applied), Store(applied).all_ops())


def _worktree_managed(repo):
    """Every git-tracked path in `repo` that ugh has an opinion about, as `{path: bytes}` --
    tracked, not `ignored` tier, not ugh's own state. The set a materialized preview is supposed
    to be able to reproduce."""
    import subprocess

    from ugh.core import tiers

    cfg = tiers.load_tiers(repo)
    listed = subprocess.run(["git", "-C", str(repo), "ls-files"], check=True,
                            capture_output=True, text=True).stdout.split()
    return {
        p: (repo / p).read_bytes() for p in listed
        if not p.startswith(".sgt/") and tiers.resolve_tier(p, cfg) != "ignored" and (repo / p).is_file()
    }


def test_emit_out_writes_the_tree_the_apply_leaves_not_the_strict_fold(tmp_path):
    """The preview agrees with the apply about which files exist and their bytes."""
    repo = _mined(tmp_path, "linear_history")
    applied = corpus.CORPUS["linear_history"].build(tmp_path / "applied")
    get(applied)

    target = _forward_subtracting_target(repo)
    out = tmp_path / "counterfactual"
    assert _emit(repo, ["revert", target, "--emit", "--out", str(out), "--json"])["ok"]
    assert _in(applied, ["revert", target, "--json"]) == 0

    assert _tree(out) == _worktree_managed(applied)


def test_emit_out_writes_nothing_for_a_refused_plan(tmp_path):
    """A refused plan would write today's tree and read as a counterfactual: nothing is written
    and the view carries no `out` key to mistake for one."""
    from ugh.cli.ideal_edit import _answer
    from ugh.core import planner

    repo = _mined(tmp_path, "linear_history")
    out = tmp_path / "counterfactual"
    refused = planner.Plan("revert", "x", ok=False, message="refused")

    import contextlib
    import io
    from unittest import mock

    buf = io.StringIO()
    with mock.patch.object(planner, "plan", lambda *a, **k: refused), contextlib.redirect_stdout(buf):
        _answer(str(repo), "revert", frozenset(), "x", True, True, False, out=str(out))

    assert "out" not in json.loads(buf.getvalue())
    assert not out.exists()
