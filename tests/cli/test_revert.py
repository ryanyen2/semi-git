"""CLI dispatch tests for `sgt revert <lane> --to <commit>` (plan U11): the timeline-scrub
truncation edit. The plan-level algebra -- which ops the up-set removes, the `--keep` strand-guard,
the `no change` no-op -- is pinned in tests/lens/test_feature_verbs.py; this file is the thin CLI
layer: `--to` routes to `plan_revert_lane_to_commit`, `--emit` projects the preview (carrying U4's
coupling rows), and a bare apply lands the smaller ideal through the shared `verbs.apply` spine.
"""

from __future__ import annotations

import json
import os

from sgt.cli import main
from sgt.core.lens import current_ideal, get
from sgt.lens import map as lensmap
from tests.laws import corpus


def _in(repo, argv):
    cwd = os.getcwd()
    os.chdir(repo)
    try:
        return main(argv)
    finally:
        os.chdir(cwd)


def _spanning_lane(repo):
    """A leaf lane spanning >=2 commit indices, with its earliest and latest cut points."""
    from sgt.api import history_view

    result = lensmap.build_map(repo)
    ci = {o["id"]: o["commit_index"] for o in history_view(repo, full=True)["ops"]}
    spans: dict[str, list[int]] = {}
    for op_id, leaf in result["op_leaf"].items():
        if op_id in ci:
            spans.setdefault(leaf, []).append(ci[op_id])
    for leaf, idxs in spans.items():
        distinct = sorted(set(idxs))
        if len(distinct) >= 2:
            return leaf, distinct[0], distinct[-1]
    return None, None, None


def test_revert_to_commit_applies_a_truncation(tmp_path, capsys):
    repo = corpus.CORPUS["linear_history"].build(tmp_path / "repo")
    get(repo)
    lane, cut, _ = _spanning_lane(repo)
    assert lane is not None

    before = current_ideal(repo).op_ids
    rc = _in(repo, ["revert", lane, "--to", str(cut), "--json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] and out["verb"] == "revert"
    assert out["removed"]  # the truncation actually removed post-cut ops

    after = current_ideal(repo).op_ids
    assert after < before  # the ideal shrank to the truncated shape
    assert set(out["removed"]) == before - after

    # idempotent: re-running the same truncation on the now-truncated ideal is a no-op
    assert _in(repo, ["revert", lane, "--to", str(cut), "--json"]) == 0
    again = json.loads(capsys.readouterr().out)
    assert again["ok"] and not again["removed"]


def test_revert_to_commit_emit_projects_the_preview_with_coupling(tmp_path, capsys):
    repo = corpus.CORPUS["linear_history"].build(tmp_path / "repo")
    get(repo)
    lane, cut, _ = _spanning_lane(repo)
    assert lane is not None

    before = current_ideal(repo).op_ids
    rc = _in(repo, ["revert", lane, "--to", str(cut), "--emit", "--json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"]
    assert "coupling" in out  # U4's coupling rows flow through the truncation preview unchanged
    assert current_ideal(repo).op_ids == before  # --emit is pure: nothing applied


def test_revert_to_commit_is_a_no_op_past_the_last_commit(tmp_path, capsys):
    repo = corpus.CORPUS["linear_history"].build(tmp_path / "repo")
    get(repo)
    lane, _, last = _spanning_lane(repo)
    assert lane is not None

    before = current_ideal(repo).op_ids
    rc = _in(repo, ["revert", lane, "--to", str(last), "--json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] and not out["removed"]
    assert current_ideal(repo).op_ids == before
