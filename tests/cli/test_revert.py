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


# ── feedforward confirm gate (plain-text revert) ─────────────────────────────────────────────────
# A bare `sgt revert <feature>` (no --json) draws the feedforward graph, then gates on [y/N].
# --yes skips the prompt; a non-tty stdin refuses to apply (exit 2). --json/--emit are covered above.


def _revertable_feature(repo):
    """A leaf feature id `sgt.lens.verbs.resolve_feature` will match -- reuse the spanning lane."""
    lane, _, _ = _spanning_lane(repo)
    return lane


def _make_tty(monkeypatch, reply):
    """Present stdin as a tty and feed `input()` a fixed reply."""
    import sys

    monkeypatch.setattr(sys.stdin, "isatty", lambda: True, raising=False)
    monkeypatch.setattr("builtins.input", lambda prompt="": reply)


def test_revert_confirm_applies_on_yes(tmp_path, capsys, monkeypatch):
    repo = corpus.CORPUS["linear_history"].build(tmp_path / "repo")
    get(repo)
    feat = _revertable_feature(repo)
    assert feat is not None
    _make_tty(monkeypatch, "y")

    before = current_ideal(repo).op_ids
    rc = _in(repo, ["revert", feat])
    assert rc == 0
    out = capsys.readouterr().out
    assert "rewind" in out and "applied" in out  # feedforward graph, then the apply confirmation
    assert current_ideal(repo).op_ids < before  # y applied the edit


def test_revert_confirm_skips_on_no(tmp_path, capsys, monkeypatch):
    repo = corpus.CORPUS["linear_history"].build(tmp_path / "repo")
    get(repo)
    feat = _revertable_feature(repo)
    _make_tty(monkeypatch, "n")

    before = current_ideal(repo).op_ids
    rc = _in(repo, ["revert", feat])
    assert rc == 1
    assert "skipped" in capsys.readouterr().out
    assert current_ideal(repo).op_ids == before  # n applied nothing


def test_revert_yes_applies_without_prompting(tmp_path, capsys, monkeypatch):
    repo = corpus.CORPUS["linear_history"].build(tmp_path / "repo")
    get(repo)
    feat = _revertable_feature(repo)

    def _boom(prompt=""):
        raise AssertionError("--yes must not prompt")

    monkeypatch.setattr("builtins.input", _boom)

    before = current_ideal(repo).op_ids
    rc = _in(repo, ["revert", feat, "--yes"])
    assert rc == 0
    assert "applied" in capsys.readouterr().out
    assert current_ideal(repo).op_ids < before


def test_revert_non_tty_without_yes_exits_2_and_applies_nothing(tmp_path, capsys, monkeypatch):
    repo = corpus.CORPUS["linear_history"].build(tmp_path / "repo")
    get(repo)
    feat = _revertable_feature(repo)

    import sys

    monkeypatch.setattr(sys.stdin, "isatty", lambda: False, raising=False)

    before = current_ideal(repo).op_ids
    rc = _in(repo, ["revert", feat])
    assert rc == 2
    out = capsys.readouterr().out
    assert "rewind" in out and "not applied" in out
    assert current_ideal(repo).op_ids == before  # nothing applied without a confirmation


# ── bare-hex feature handles (the copy token the graph prints) ─────────────────────────────────
# The overview advertises handles as bare hex (no `f-`), which collides with the founding op's id
# (feature id = `f-<founding op id>`). Reverting by that handle must resolve the FEATURE
# deterministically -- the full op-set, not the single founding op -- and never fall to the
# 2-minute LLM rung (the hang the user reported: `sgt revert f-00aa` waited ~2 minutes then errored).


def _no_llm(monkeypatch):
    """Guard: fail loudly if the LLM NL rung is ever reached for a handle-shaped target."""
    from sgt.cli import ideal_edit

    def _boom(*a, **k):
        raise AssertionError("a handle-shaped target must resolve deterministically, never via the LLM")

    monkeypatch.setattr(ideal_edit, "_resolve_via_intent", _boom)


def test_resolve_feature_accepts_f_prefix_and_bare_hex_prefix(tmp_path):
    repo = corpus.CORPUS["linear_history"].build(tmp_path / "repo")
    get(repo)
    from sgt.lens import verbs

    fid = _revertable_feature(repo)
    assert fid and fid.startswith("f-")
    body = fid[2:]
    for ref in (fid, fid[:6], body, body[:4]):  # full id, `f-`-prefix, bare hex, short bare-hex prefix
        resolved = verbs.resolve_feature(repo, ref)
        assert resolved is not None and resolved[1] == fid, ref


def test_resolve_feature_ambiguous_prefix_returns_none(tmp_path, monkeypatch):
    """Two leaf features sharing a hex prefix -> the bare prefix is ambiguous -> None (falls through,
    never guesses). A synthetic tree stands in because every corpus mints exactly one feature."""
    repo = corpus.CORPUS["linear_history"].build(tmp_path / "repo")
    get(repo)
    from sgt.lens import verbs

    fake = {"nodes": {"f-abcd0001": {"children": [], "label": "One"},
                      "f-abcd0002": {"children": [], "label": "Two"}},
            "op_leaf": {}}
    monkeypatch.setattr(verbs.tree, "load", lambda r: fake)
    assert verbs.resolve_feature(repo, "abcd") is None                  # matches both -> ambiguous
    assert verbs.resolve_feature(repo, "abcd0001")[1] == "f-abcd0001"    # unique bare-hex prefix resolves


def test_revert_bare_hex_handle_reverts_the_feature_not_the_founding_op(tmp_path, capsys, monkeypatch):
    repo = corpus.CORPUS["linear_history"].build(tmp_path / "repo")
    get(repo)
    _no_llm(monkeypatch)
    fid = _revertable_feature(repo)
    handle = fid[2:10]  # the bare-hex copy token the overview prints

    before = current_ideal(repo).op_ids
    rc = _in(repo, ["revert", handle, "--json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] and out["verb"] == "revert"
    assert len(out["removed"]) > 1                       # the whole feature op-set, not one founding op
    assert current_ideal(repo).op_ids == before - frozenset(out["removed"])


def test_revert_handle_shaped_miss_exits_2_without_the_llm(tmp_path, capsys, monkeypatch):
    repo = corpus.CORPUS["linear_history"].build(tmp_path / "repo")
    get(repo)
    _no_llm(monkeypatch)

    before = current_ideal(repo).op_ids
    rc = _in(repo, ["revert", "deadbeef", "--json"])     # hex-shaped, matches no feature
    assert rc == 2
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False and out["candidates"] == []
    assert current_ideal(repo).op_ids == before          # nothing applied
