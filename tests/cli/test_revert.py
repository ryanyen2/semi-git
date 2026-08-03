"""CLI dispatch tests for `sgt revert <lane> --to <commit>` (plan U11): the timeline-scrub
truncation edit. The plan-level algebra -- which ops the up-set removes, the `--keep` strand-guard,
the `no change` no-op -- is pinned in tests/lens/test_feature_verbs.py; this file is the thin CLI
layer: `--to` routes to `plan_revert_lane_to_commit`, `--emit` projects the preview (carrying U4's
coupling rows), and a bare apply lands the smaller ideal through the shared `verbs.apply` spine.
"""

from __future__ import annotations

import json
import os

import pytest

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


def test_revert_on_a_dirty_unrelated_file_refuses_cleanly_not_a_traceback(tmp_path, capsys):
    """F4/F5 (Phase 0.3): a materializing verb blocked by an unrelated dirty tracked file must
    refuse cleanly at the CLI boundary -- an exit code and the file list + a truthful, executable
    remedy -- never a raw `DirtyWorkingTreeError` traceback with a half-written `.sgt`."""
    import json as _json

    from sgt.core import order, verbs
    from sgt.core.lens import current_ideal, get
    from sgt.core.store import Store
    from sgt.store.gitbind import init_store

    repo = tmp_path / "repo"
    gb, _ = init_store(repo)
    (repo / "a.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    (repo / "b.py").write_text("def bar():\n    return 1\n", encoding="utf-8")
    gb.commit_all("init")
    (repo / "a.py").write_text("def foo():\n    return 2\n", encoding="utf-8")
    gb.commit_all("foo v2")
    get(repo)
    tip = order.frontier(current_ideal(repo).op_ids, Store(repo).all_ops())["a.py::foo"]

    # Dirty an *unrelated* tracked file with bytes the revert's fold would overwrite.
    (repo / "b.py").write_text("def bar():\n    return 999  # local WIP\n", encoding="utf-8")

    before = current_ideal(repo).op_ids
    rc = _in(repo, ["revert", tip, "--json"])  # no traceback escapes
    assert rc == 1
    out = _json.loads(capsys.readouterr().out)
    assert out["ok"] is False
    assert "b.py" in out["error"]          # names the offending file
    assert "sgt put" not in out["error"]   # no nonexistent verb
    assert "sgt save" in out["error"]      # the actual, executable remedy
    assert current_ideal(repo).op_ids == before  # refused -- nothing applied


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


# -- revert by NL resolves against the intent ledger's reasons *before* the LLM (M3, plan U8) -------
# A prose target with no OPENAI_API_KEY used to error ("could not resolve ... set OPENAI_API_KEY").
# Now it first matches the phrase against the ledger's captured reasons (M1's topic tokenizer) and
# reverts that record's subject op-set deterministically -- the LLM rung is only a last resort.


def test_revert_by_nl_resolves_via_the_intent_ledger_without_the_llm(tmp_path, capsys, monkeypatch):
    from sgt.intent import rationale

    repo = corpus.CORPUS["linear_history"].build(tmp_path / "repo")
    get(repo)
    _no_llm(monkeypatch)  # if the ledger rung works, the LLM rung is never reached

    op_id = sorted(current_ideal(repo).op_ids)[-1]  # a real, revertible op in the ideal
    rationale.record_rationale(
        repo, subject=rationale._subject_for(repo, [op_id]),
        reason="added the retry backoff loop", actor="human", evidence=[])

    before = current_ideal(repo).op_ids
    rc = _in(repo, ["revert", "drop the retry backoff", "--json"])  # prose, no numbered/hex/symbol match
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] and op_id in out["removed"]           # resolved to the ledgered op and reverted it
    assert current_ideal(repo).op_ids < before


def test_revert_by_nl_with_no_ledger_match_still_reaches_the_llm_rung(tmp_path, capsys, monkeypatch):
    repo = corpus.CORPUS["linear_history"].build(tmp_path / "repo")
    get(repo)
    from sgt.cli import ideal_edit

    def _sentinel(*a, **k):
        raise AssertionError("REACHED_LLM_RUNG")

    monkeypatch.setattr(ideal_edit, "_resolve_via_intent", _sentinel)
    # No rationale recorded -> nothing for the ledger rung to match -> it must fall through to the LLM.
    with pytest.raises(AssertionError, match="REACHED_LLM_RUNG"):
        _in(repo, ["revert", "some unrelated phrase nobody captured", "--json"])
