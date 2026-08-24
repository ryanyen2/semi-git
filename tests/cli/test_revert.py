"""CLI dispatch tests for `sgt revert <lane> --to <commit>` (plan U11): the timeline-scrub
truncation edit. The plan-level algebra -- which ops the up-set removes, the `--keep` strand-guard,
the `no change` no-op -- is pinned in tests/lens/test_feature_verbs.py; this file is the thin CLI
layer: `--to` routes to `plan_revert_lane_to_commit`, `--emit` projects the preview (carrying U4's
coupling rows), and a bare apply lands the smaller ideal through the shared `verbs.apply` spine.
"""

from __future__ import annotations

import json
import os
import re

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


def test_a_reverted_checkpoint_can_be_restored_by_the_same_handle(tmp_path, capsys, monkeypatch):
    """`sgt revert <feature>@<n>` is the rewind the map and the checkpoint detail both tell users to
    type, so `sgt restore <feature>@<n>` has to be the way back. It was not: `restore` never entered
    the checkpoint branch, so the handle fell through every deterministic rung to the NL rung and
    exited with `could not resolve ... set OPENAI_API_KEY` -- a one-way door out of the only rewind
    unit the UI advertises, and the checkpoint detail's own remedy line pointed straight at it. The
    round trip has to land the same op-set it removed."""
    _no_llm(monkeypatch)
    repo = corpus.CORPUS["linear_history"].build(tmp_path / "repo")
    get(repo)
    from sgt.api import segments_view

    feat = _revertable_feature(repo)
    assert feat is not None
    segs = [s for s in segments_view(repo) if s["feature_id"] == feat]
    assert segs, "the spanning lane should cut at least one chapter"
    ckpt = segs[-1]["checkpoint"]

    before = current_ideal(repo).op_ids
    assert _in(repo, ["revert", ckpt, "--yes"]) == 0
    rewound = current_ideal(repo).op_ids
    assert rewound < before

    capsys.readouterr()
    assert _in(repo, ["restore", ckpt, "--yes"]) == 0
    assert "applied" in capsys.readouterr().out
    assert current_ideal(repo).op_ids == before  # exactly what the revert took, back


def test_the_restore_gap_warning_is_printed_once_under_yes_and_twice_around_a_confirm(
        tmp_path, monkeypatch, capsys):
    """`_restore_gap_report` is printed before the confirm and again after the apply, so a warning
    about work that stays gone survives a long preview scroll. `--yes` removes the confirm between
    them, and the two identical lines then landed two lines apart, reading as two separate problems.
    Faked here rather than provoked: no corpus fixture has the cross-op dependency a real gap needs,
    and the defect is in this function's control flow, not in the gap walk."""
    _no_llm(monkeypatch)
    repo = corpus.CORPUS["linear_history"].build(tmp_path / "repo")
    get(repo)
    fid = _revertable_feature(repo)
    monkeypatch.setattr("sgt.cli.ideal_edit._restore_gap_report", lambda repo, preview: ["  ⚠ GAP"])

    capsys.readouterr()
    _in(repo, ["restore", fid, "--yes"])
    assert capsys.readouterr().out.count("⚠ GAP") == 1

    _in(repo, ["revert", fid, "--yes"])
    capsys.readouterr()
    _make_tty(monkeypatch, "y")  # a confirm scrolls the preview away, so the repeat earns its place
    _in(repo, ["restore", fid])
    assert capsys.readouterr().out.count("⚠ GAP") == 2


def test_restoring_a_checkpoint_that_was_never_reverted_changes_nothing(tmp_path, capsys, monkeypatch):
    """The no-op has to be honest rather than silent-successful: a live checkpoint has nothing to
    bring back, and saying so is the whole answer. It must not report an apply it did not make."""
    _no_llm(monkeypatch)
    repo = corpus.CORPUS["linear_history"].build(tmp_path / "repo")
    get(repo)
    from sgt.api import segments_view

    feat = _revertable_feature(repo)
    ckpt = [s for s in segments_view(repo) if s["feature_id"] == feat][-1]["checkpoint"]

    before = current_ideal(repo).op_ids
    assert _in(repo, ["restore", ckpt, "--yes"]) == 0
    assert current_ideal(repo).op_ids == before
    assert "changed nothing" in capsys.readouterr().out


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
    # The reverted op is gone. Not a strict subset, though: a revert also emits the layout repairs
    # that keep surviving entities' separators alive (here, `b.py::__residue__::baz` back to
    # `b'\n'`), so the after-set can carry ops the before-set did not. That was already true of the
    # spliced path; the upward-closed path used to skip the repair pass and silently drop the
    # trailing newline instead.
    after = current_ideal(repo).op_ids
    assert op_id not in after
    assert after != before


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


# ── truthful magnitude when the revert is a forward subtraction ──────────────────────────────────
# A symbol whose edit is *shared* with later work cannot come out as a whole op: `plan_subtraction`
# splices the removal into the live code instead, so `preview.removed` is empty while files change.
# Every surface used to report that op count as the headline, printing "removes 0 edits" for a
# revert that rewrote the file -- the silent-success shape (a command that reads as a no-op and
# isn't). Pilot 1, coursecraft/slots.py::overlaps.


def _shared_edit_repo(root):
    """`overlaps` and its caller modified in ONE op (def-use connected, neither born there), then
    later work on the caller alone -- so the caller's op sits above the shared op and nothing is
    upward-closed enough to exclude."""
    repo = root / "shared"
    corpus._init(repo)
    corpus._write(repo, "slots.py", "def overlaps(a, b):\n    return a < b\n")
    corpus._write(repo, "test_slots.py",
                  "from slots import overlaps\n\n\ndef test_overlaps():\n    assert overlaps(1, 2)\n")
    corpus._commit(repo, "overlaps and its test", 1)
    corpus._write(repo, "slots.py", "def overlaps(a, b):\n    return a <= b\n")
    corpus._write(repo, "test_slots.py", "from slots import overlaps\n\n\ndef test_overlaps():\n"
                  "    assert overlaps(1, 2)\n    assert overlaps(2, 2)\n")
    corpus._commit(repo, "touching ends count as overlap", 2)
    corpus._write(repo, "test_slots.py", "from slots import overlaps\n\n\ndef test_overlaps():\n"
                  "    assert overlaps(1, 2)\n    assert overlaps(2, 2)\n    assert not overlaps(3, 1)\n")
    corpus._commit(repo, "one more case", 3)
    return repo


def test_show_does_not_report_a_subtraction_as_zero_edits(tmp_path, capsys):
    repo = _shared_edit_repo(tmp_path)
    get(repo)
    from sgt.core import verbs as core_verbs

    preview = core_verbs.plan_revert(repo, "slots.py::overlaps")
    assert preview.ok and not preview.removed and preview.subtracted_symbols  # the shape under test

    assert _in(repo, ["show", "slots.py::overlaps"]) == 0
    out = capsys.readouterr().out
    assert "0 edit" not in out          # the consequence line, and the `sgt revert` cost beside it
    assert "1 symbol" in out            # what the revert would actually change


def test_revert_apply_reports_what_a_subtraction_changed(tmp_path, capsys):
    repo = _shared_edit_repo(tmp_path)
    get(repo)
    src = (repo / "slots.py").read_text()

    assert _in(repo, ["revert", "slots.py::overlaps", "--yes"]) == 0
    out = capsys.readouterr().out
    assert "0 edit" not in out
    assert "symbol" in out
    assert (repo / "slots.py").read_text() != src  # it really did rewrite the file


# F33. The other end of the same report: a revert whose removal is *entirely* overlapped by later
# work changes nothing -- no op leaves the ideal, no file moves, no journal event is appended -- yet
# the apply line still promised "`sgt undo` reverses this". Taking the tool at its word pops the
# *previous* save instead and silently drops that edit. Found by the WP-V4 random-op harness.


def _twice_reworked_repo(root):
    """`qux` reworked twice in a row. Reverting the first rework is fully superseded by the second,
    so the subtraction has nothing left to splice out and the op is kept unchanged."""
    repo = root / "twice"
    corpus._init(repo)
    corpus._write(repo, "c.py", "def qux():\n    return 0\n")
    corpus._commit(repo, "qux", 1)
    corpus._write(repo, "c.py", "def qux():\n    return 1\n")
    corpus._commit(repo, "qux returns one", 2)
    corpus._write(repo, "c.py", "def qux():\n    return 2\n")
    corpus._commit(repo, "qux returns two", 3)
    return repo


def _superseded_op(repo):
    """The middle rework of `c.py::qux` -- the one the last commit fully overwrote."""
    from sgt.api import history_view
    from sgt.core import verbs as core_verbs
    from sgt.core.store import Store

    ci = {o["id"]: o["commit_index"] for o in history_view(repo, full=True)["ops"]}
    ops = [o for o in Store(repo).all_ops() if any("c.py::qux" in f for f in o.footprint)]
    for op in sorted(ops, key=lambda o: ci.get(o.id, -1)):
        preview = core_verbs.plan_revert(repo, op.id)
        changed = [s for s in preview.affected_symbols if "::__" not in s]
        if preview.ok and not preview.removed and not changed:
            return op.id, preview
    return None, None


def test_a_revert_that_changes_nothing_does_not_claim_undo_reverses_it(tmp_path, capsys):
    repo = _twice_reworked_repo(tmp_path)
    get(repo)
    op_id, preview = _superseded_op(repo)
    assert op_id is not None, "fixture no longer produces a fully-superseded revert"
    assert preview.kept_conflicts  # the shape under test: kept, not removed

    before = current_ideal(repo).op_ids
    assert _in(repo, ["revert", op_id, "--yes"]) == 0
    out = capsys.readouterr().out
    assert current_ideal(repo).op_ids == before  # it really did change nothing

    assert "sgt undo" not in out, "promised an undo for an edit that never happened"
    assert "changed nothing" in out


# F35. Reverting the only entity in a file leaves the file's *layout* artifacts behind -- the
# `__residue__`/`__anchor__` ops are siblings of the entity, not dependents, so its up-set never
# reaches them. The fold appends an orphaned residue at the end of the file, so `code(ideal)` is one
# gap longer than whatever the developer types there next, forever: `put()` refuses every save in
# that path, `sgt undo` included, and the file becomes unwritable. Found by the WP-V4 random-op
# harness (seed 11, op 66), minimized here.


def _one_symbol_module_repo(root):
    """Two modules, each holding exactly one function. `mod.py::only` is the whole file."""
    repo = root / "solo"
    corpus._init(repo)
    corpus._write(repo, "keep.py", "def keep():\n    return 1\n")
    corpus._write(repo, "mod.py", "def only():\n    return 2\n")
    corpus._commit(repo, "two modules", 1)
    return repo


def test_a_file_emptied_by_revert_can_still_be_written_to(tmp_path, capsys):
    from sgt.core.store import Store

    repo = _one_symbol_module_repo(tmp_path)
    ideal = get(repo)
    ops = {o.id: o for o in Store(repo).all_ops()}
    op_id = next(i for i in ideal.op_ids if "mod.py::only" in " ".join(ops[i].footprint))

    assert _in(repo, ["revert", op_id, "--yes"]) == 0
    capsys.readouterr()

    (repo / "mod.py").write_text("def revived():\n    return 3\n")
    rc = _in(repo, ["save", "-m", "revive mod"])
    out = capsys.readouterr().out
    assert rc == 0, f"save refused after the revert emptied the file:\n{out}"

    # The point of the fix: the composed image agrees with the bytes on disk, so the path is
    # writable again rather than permanently drifted.
    from sgt.core.lens import code
    composed = code(get(repo), Store(repo).all_ops())
    assert composed["mod.py"] == (repo / "mod.py").read_bytes()

    # And the layout facts must come back *with* the entity, not stay behind. The first attempt at
    # this fix pruned the orphaned gap forward, which left `restore` composing
    # `    return 2def revived():` -- a SyntaxError -- because the gap was gone for good.
    assert _in(repo, ["restore", op_id, "--yes"]) == 0
    capsys.readouterr()
    revived = (repo / "mod.py").read_text()
    assert "return 2\n" in revived and "def revived():" in revived, revived
    compile(revived, "mod.py", "exec")


# F42. Reverting a file's last entity left the path behind as a *blank tracked file* -- 38 of the 69
# non-fatal WP-V4 failures, through both `revert` and `undo`. The cause is one symbol: a file's leading
# gap is `path::__residue__::\x00HEAD\x00`, which is nobody's entity name, so `layout_ops_of` (which
# mints layout symbols for born *entity* names) never reaches it. It stays live, `residue` is
# content-bearing, so `code(I)` keeps covering the path and folds it to b"". Three shapes have the same
# lone-sentinel ideal and must not be touched, and each one killed an earlier version of the fix.


def _phantom_shapes_repo(root):
    """One module per shape the fix has to tell apart: a lone entity, an entity under a header comment,
    a comment-only file, and a legitimately empty file."""
    repo = root / "shapes"
    corpus._init(repo)
    corpus._write(repo, "keep.py", "def keep():\n    return 1\n")
    corpus._write(repo, "solo.py", "def only():\n    return 2\n")
    corpus._write(repo, "headed.py", "# what this module is for\n\n\ndef only():\n    return 3\n")
    corpus._write(repo, "comments.py", "# just a comment file\n# no entities at all\n")
    corpus._write(repo, "empty.py", "")
    corpus._commit(repo, "four shapes", 1)
    return repo


def _op_writing(repo, sym: str) -> str:
    from sgt.core.store import Store

    ops = {o.id: o for o in Store(repo).all_ops()}
    return next(i for i in get(repo).op_ids if sym in ops[i].footprint)


def test_reverting_a_files_last_entity_removes_the_file(tmp_path, capsys):
    repo = _phantom_shapes_repo(tmp_path)
    get(repo)
    before = (repo / "solo.py").read_bytes()
    others = {p: (repo / p).read_bytes() for p in ("comments.py", "empty.py", "keep.py")}

    assert _in(repo, ["revert", _op_writing(repo, "solo.py::only"), "--yes"]) == 0
    capsys.readouterr()

    assert not (repo / "solo.py").exists(), "left behind as a phantom: " + repr(
        (repo / "solo.py").read_bytes())
    # An entity-free file has the *same* ideal shape as the phantom -- one `__residue__::\x00HEAD\x00`
    # op, blank for `empty.py` -- so a predicate on the ideal alone would delete a user's `__init__.py`.
    for path, was in others.items():
        assert (repo / path).read_bytes() == was, f"{path} was not the removal's business"

    assert _in(repo, ["undo"]) == 0
    capsys.readouterr()
    assert (repo / "solo.py").read_bytes() == before


def test_reverting_the_last_entity_keeps_the_files_header_comment(tmp_path, capsys):
    """The counterexample that killed the second attempt: bottoming the leading-gap sentinel
    unconditionally threw away the header comment's bytes, *and* left the ideal covering no symbol for
    the path -- so `_write_working_tree` routed it to `to_delete`, R4's backstop kept the un-reverted
    file, and `sgt revert` printed a `✓` over a file it had not changed. A silent success."""
    repo = _phantom_shapes_repo(tmp_path)
    get(repo)
    op_id = _op_writing(repo, "headed.py::only")

    assert _in(repo, ["revert", op_id, "--yes"]) == 0
    capsys.readouterr()

    left = (repo / "headed.py").read_text()
    assert "what this module is for" in left, f"discarded the header comment: {left!r}"
    assert "def only" not in left, f"revert reported success without removing the entity: {left!r}"

    assert _in(repo, ["restore", op_id, "--yes"]) == 0
    capsys.readouterr()
    back = (repo / "headed.py").read_text()
    assert "what this module is for" in back and "return 3" in back, back
    compile(back, "headed.py", "exec")


def _reborn_symbol_repo(root):
    """`mod.py::only` edited, deleted, and re-added twice. F39: each removal makes
    `subtract._repair_layout` mint a `before=None` repair for the layout symbols, so the store
    legitimately accumulates several chain heads per symbol -- and a restore that would draw two of
    them is refused by `Ideal.from_ops`."""
    repo = root / "reborn"
    corpus._init(repo)
    corpus._write(repo, "keep.py", "def keep():\n    return 1\n")
    corpus._write(repo, "mod.py", "def only():\n    return 2\n")
    corpus._commit(repo, "two modules", 1)
    n = 2
    for _cycle in range(2):
        for _edit in range(2):
            n += 1
            body = "".join(f"    x{i} = {i}\n" for i in range(n))
            corpus._write(repo, "mod.py", f"def only():\n{body}    return {n}\n")
            corpus._commit(repo, f"edit {n}", n)
        n += 1
        corpus._write(repo, "mod.py", "def other():\n    return 0\n")
        corpus._commit(repo, f"drop only {n}", n)
        n += 1
        body = "".join(f"    y{i} = {i}\n" for i in range(n))
        corpus._write(repo, "mod.py", f"def other():\n    return 0\n\ndef only():\n{body}    return {n}\n")
        corpus._commit(repo, f"re-add only {n}", n)
    return repo


def test_a_refused_restore_reports_its_reason_not_that_the_op_is_unknown(tmp_path, capsys):
    """F39's first collateral defect, and the reason a WP-V4 sweep recorded a hard stop with no
    working move. `_explain_restore_block` returns None both when the hex names no stored op and
    when it names one the ideal cannot re-admit for any reason *other* than a competing live
    sibling -- and the ladder answered both with `no feature matches handle '<id>' -- run `sgt log
    --map``. That denies an op the store is holding and sends the reader to look for a feature,
    while `plan_restore`'s own truthful refusal is discarded."""
    from sgt.core import verbs
    from sgt.core.store import Store

    repo = _reborn_symbol_repo(tmp_path)
    ideal = get(repo)
    ops = {o.id: o for o in Store(repo).all_ops()}
    live = sorted(i for i in ideal.op_ids if "mod.py::only" in ops[i].footprint)
    assert _in(repo, ["revert", live[0], "--take-dependents", "--yes"]) == 0
    capsys.readouterr()

    after = current_ideal(repo)
    refused = sorted(o.id for o in Store(repo).all_ops()
                     if o.id not in after.op_ids and not verbs.plan_restore(repo, o.id).ok)
    assert refused, "fixture no longer produces a restore the validator refuses"

    for op_id in refused:
        rc = _in(repo, ["restore", op_id[:12], "--yes"])
        out = capsys.readouterr().out
        assert rc != 0, f"{op_id[:12]} was refused by the planner but the CLI returned 0:\n{out}"
        assert "no feature matches" not in out, (
            f"{op_id[:12]} is in the store, and the CLI says it is not:\n{out}")
        assert "refused" in out or "another version" in out, (
            f"{op_id[:12]}'s refusal names no reason:\n{out}")


def test_a_refused_restore_names_the_symbol_rather_than_dumping_the_proposed_set(tmp_path, capsys):
    """F39's second collateral defect. `Ideal.from_ops` raises with `sorted(ids)` -- the *whole*
    proposed ideal, thousands of 64-hex ids on a real repository and never the offending one -- and
    `_validated` passed it straight through. The reader needs the symbol whose chain forked."""
    from sgt.core import verbs
    from sgt.core.store import Store

    repo = _reborn_symbol_repo(tmp_path)
    ideal = get(repo)
    ops = {o.id: o for o in Store(repo).all_ops()}
    live = sorted(i for i in ideal.op_ids if "mod.py::only" in ops[i].footprint)
    assert _in(repo, ["revert", live[0], "--take-dependents", "--yes"]) == 0
    capsys.readouterr()

    after = current_ideal(repo)
    refused = sorted(o.id for o in Store(repo).all_ops()
                     if o.id not in after.op_ids and not verbs.plan_restore(repo, o.id).ok)
    assert refused, "fixture no longer produces a restore the validator refuses"

    _in(repo, ["restore", refused[0][:12], "--yes"])
    out = capsys.readouterr().out
    assert "mod.py::" in out, f"the refusal names no symbol:\n{out}"
    assert not re.search(r"[0-9a-f]{40}", out), f"the refusal dumps whole op ids:\n{out}"
    assert len(out) < 300, f"the refusal is {len(out)} characters long:\n{out}"


@pytest.mark.parametrize("verb", ["restore", "revert"])
@pytest.mark.parametrize("target", ["a.py::nosuch", "b.py::whatever"])
def test_a_symbol_target_that_cannot_be_planned_reports_why_not_an_api_key(
        tmp_path, capsys, monkeypatch, verb, target):
    """F94, the same defect as F91 one rung further down. `file::Symbol` is a deterministic
    reference, but when the planner refuses one the ladder discarded its reason and answered with
    the LLM rung's `could not resolve ... set OPENAI_API_KEY to enable natural-language targets`.
    The remedy named is not the remedy: no key resolves a symbol that is not in the history.

    This is the rung the WP-V4 recoverability ladder uses, so every refusal it recorded said
    `set OPENAI_API_KEY` where the planner had computed a true reason -- which is why both hard
    stops were unexplainable from their own artifacts."""
    # Offline means every credential `config.resolve_api_key` consults, not just this one. Any test
    # whose code path calls `load_env(".")` imports this repo's real `.env` -- a Claude `SGT_MODEL`
    # and an `ANTHROPIC_AUTH_TOKEN` -- into `os.environ` for the rest of the process, so deleting
    # `OPENAI_API_KEY` alone left these assertions passing or failing by collection order.
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    repo = tmp_path / "one"
    corpus._init(repo)
    corpus._write(repo, "a.py", "def keep():\n    return 1\n")
    corpus._commit(repo, "one", 1)
    get(repo)

    rc = _in(repo, [verb, target, "--yes"])
    out = capsys.readouterr().out
    assert rc != 0, f"{verb} {target} returned 0:\n{out}"
    assert "OPENAI_API_KEY" not in out, (
        f"{verb} {target} blames a missing API key for a deterministic reference:\n{out}")
    assert target in out, f"{verb} {target}'s refusal does not name the target:\n{out}"
    if verb == "restore":
        # `not live in the ideal` is `revert`'s reason read out to a `restore` caller, for whom it
        # is the premise of the request rather than an objection to it.
        assert "not live in the ideal" not in out, (
            f"restore {target} gives revert's reason:\n{out}")


@pytest.mark.parametrize("verb", ["restore", "revert"])
def test_a_refused_verb_does_not_exit_0_under_json(tmp_path, capsys, verb):
    """`_emit_json` keys its exit status off an `error` field the verb view does not carry, so a
    refusal rendered through that view exited 0 while saying `"ok": false`. A machine caller reading
    the exit code — the contract VS Code and the TUI use — saw a refusal as a success: the
    silent-success shape, on the two verbs whose job is to refuse safely."""
    repo = tmp_path / "one"
    corpus._init(repo)
    corpus._write(repo, "a.py", "def keep():\n    return 1\n")
    corpus._commit(repo, "one", 1)
    get(repo)

    rc = _in(repo, [verb, "a.py::nosuch", "--json"])
    out = capsys.readouterr().out
    assert json.loads(out)["ok"] is False, f"{verb} claims ok on a target that does not exist:\n{out}"
    assert rc != 0, f"{verb} refused and exited 0:\n{out}"


# F123/F124. Two defects found chasing why the `still references removed code` warning never fired
# once in the 10,237-operation WP-V4 sweep. Both are the silent-success shape.


def _string_reference_repo(root):
    """`user` names `helper` only inside a string literal, so no extractor edge exists and only the
    byte-level half of `subtract.broken_references` can see it. `shared` is reworked twice so a
    revert can be aimed at its middle version, which is the only way to make the removal need a
    forward splice while also un-creating an entity."""
    repo = root / "strref"
    corpus._init(repo)
    corpus._write(repo, "m.py", "def helper():\n    return 1\n\n\ndef shared():\n    a = 1\n"
                  "    return a\n\n\ndef user():\n    return \"helper\"\n")
    corpus._commit(repo, "helper, shared and user", 1)
    corpus._write(repo, "m.py", "def helper():\n    return 1\n\n\ndef shared():\n    a = 1\n"
                  "    b = 2\n    return a + b\n\n\ndef user():\n    return \"helper\"\n")
    corpus._commit(repo, "shared adds b", 2)
    corpus._write(repo, "m.py", "def helper():\n    return 1\n\n\ndef shared():\n    a = 1\n"
                  "    b = 2\n    c = 3\n    return a + b + c\n\n\ndef user():\n    return \"helper\"\n")
    corpus._commit(repo, "shared adds c", 3)
    return repo


def test_a_whole_entity_revert_still_warns_about_surviving_references(tmp_path):
    """F123. `plan_subtraction` returns early when nothing needs a forward splice, and both
    consequence sweeps sit after that return -- so the ordinary shape (remove an entity outright) is
    the one shape whose `still references removed code` warning can never fire, even though it is
    the only shape whose `born` set is reliably non-empty. Same repository, same removed entity, same
    surviving reference: the warning appeared only when an unrelated symbol needed a splice."""
    from sgt.core import order, verbs as core_verbs
    from sgt.core.subtract import plan_subtraction

    repo = _string_reference_repo(tmp_path)
    get(repo)
    ops, ideal, declared = core_verbs._load(repo)
    helper_add = next(o.id for o in ops if o.footprint.get("m.py::helper", (0,))[0] is None)
    mid = order._ordered_chains(ideal.op_ids, ops)["m.py::shared"][1]

    # The control: with a splice to perform, the byte sweep sees it.
    both = plan_subtraction(repo, {helper_add, mid}, ops, ideal.op_ids, declared, tag="t")
    assert both.broken_references == ("m.py::user",), both.broken_references

    alone = plan_subtraction(repo, {helper_add}, ops, ideal.op_ids, declared, tag="t")
    assert alone.broken_references == ("m.py::user",), (
        "removing helper outright leaves `user` naming it and says nothing: "
        f"{alone.broken_references}")


def test_json_revert_says_whether_it_applied(tmp_path, capsys, monkeypatch):
    """F124. The plain path prints its preview and declines with no terminal attached; `--json` skips
    the confirm block and applies. That asymmetry is the machine contract (`--emit --json` is the dry
    run, and the extension and four tests here depend on plain `--json` applying), so what was wrong
    was not the behaviour but that the view carried no field distinguishing the two. A machine caller
    saw the same keys whether it had previewed or mutated."""
    import json as _json
    import sys

    repo = _string_reference_repo(tmp_path)
    get(repo)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False, raising=False)
    src = (repo / "m.py").read_text()
    before = current_ideal(repo).op_ids

    _in(repo, ["revert", "m.py::helper", "--emit", "--json"])
    dry = _json.loads(capsys.readouterr().out)
    assert dry["applied"] is False, dry
    assert current_ideal(repo).op_ids == before, "--emit --json mutated the ideal"
    assert (repo / "m.py").read_text() == src, "--emit --json rewrote the file"

    _in(repo, ["revert", "m.py::helper", "--json"])
    wet = _json.loads(capsys.readouterr().out)
    assert wet["applied"] is True, wet
    assert current_ideal(repo).op_ids != before, "plain --json did not apply"


def test_the_dry_run_carries_the_two_consequence_reports(tmp_path, capsys, monkeypatch):
    """F129. `--emit` renders through `_project_verb_preview`, which carries `so_what`, `carry_count`
    and `fallout` but neither `kept_conflicts` nor `broken_references`; the apply path hand-builds a
    different view that carries both. So the two reports reached the developer only in the result of
    the mutation that had already happened, and a dry run -- in either format -- named neither.

    That is the surface §4's claim rests on: "\\sgt{} names every function it changes that way and
    every function it decided to leave alone", closing "Naming the function before the revert runs is
    as far as we are willing to go on the developer's behalf". True for a human at a tty, where the
    plain-text apply prints the report before the `[y/N]`. False for the preview and false for an
    agent, where "before" was "after".

    Same repository and same removed entity as the F123 test above, which fixed the sweep that
    computes this; nothing made the answer observable to a machine, so a harness counting the class
    through `--emit --json` would have read zero forever whatever sgt did.
    """
    import json as _json
    import sys

    repo = _string_reference_repo(tmp_path)
    get(repo)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False, raising=False)

    _in(repo, ["revert", "m.py::helper", "--emit", "--json"])
    dry = _json.loads(capsys.readouterr().out)
    assert dry["broken_references"] == ["m.py::user"], (
        f"the dry run does not name the surviving reference: {dry.get('broken_references')!r}")
    assert "kept_conflicts" in dry, sorted(dry)

    _in(repo, ["revert", "m.py::helper", "--emit"])
    assert "still references removed code" in capsys.readouterr().out


def test_reverting_a_save_sha_says_it_is_a_save_and_names_the_features_it_touched(tmp_path, capsys):
    """The pilot's dead end. `sgt save` prints the commit it made, so the obvious next move after a
    save you regret is `sgt revert <that sha>` -- and a bare hex is handle-shaped, so it fell to
    `_no_feature_match`, which denied the id exists and pointed at `sgt log --map`. The map never
    shows save shas, so the one pointer given cannot resolve the one id typed: a name the tool
    itself printed reads as unknown. The sha names a save, and the answer says so and names the
    moves that do work."""
    repo = tmp_path / "saved"
    corpus._init(repo)
    corpus._write(repo, "cart.py", "def total(items):\n    return sum(items)\n")
    corpus._commit(repo, "the cart", 1)
    corpus._write(repo, "cart.py", "def total(items):\n    return sum(items)\n\ndef tax(t):\n    return t * 0.2\n")
    sha = corpus._commit(repo, "add the cart tax", 2)
    get(repo)  # mine the history, as any read of the repo would
    lensmap.build_map(repo)

    rc = _in(repo, ["revert", sha[:7]])
    out = capsys.readouterr().out

    assert rc == 2
    assert "no feature matches" not in out, f"the sha names a save, and the CLI denies it:\n{out}"
    assert "save" in out and sha[:7] in out, out
    assert "add the cart tax" in out, f"the refusal doesn't say which save:\n{out}"
    assert re.search(r"sgt revert [0-9a-f]{4,}\s{2,}\S", out), (
        f"the refusal names no feature to revert instead:\n{out}")
    assert "sgt undo" in out and f"sgt why {sha[:7]}" in out, out


def test_reverting_a_save_sha_answers_json_consumers_with_the_same_features(tmp_path, capsys):
    """`--json` is the extension's only channel: a refusal whose `candidates` are empty renders as a
    bare "Cannot revert X." there, which is the same dead end one surface further from a terminal."""
    repo = tmp_path / "saved-json"
    corpus._init(repo)
    corpus._write(repo, "cart.py", "def total(items):\n    return sum(items)\n")
    corpus._commit(repo, "the cart", 1)
    corpus._write(repo, "cart.py", "def total(items):\n    return sum(items)\n\ndef tax(t):\n    return t * 0.2\n")
    sha = corpus._commit(repo, "add the cart tax", 2)
    get(repo)  # mine the history, as any read of the repo would
    lensmap.build_map(repo)

    rc = _in(repo, ["revert", sha[:7], "--json"])
    view = json.loads(capsys.readouterr().out)

    assert rc == 2 and view["ok"] is False
    assert "save" in view["message"]
    assert view["candidates"], f"no feature offered to a JSON consumer: {view}"


@pytest.mark.parametrize("verb", ["revert", "restore"])
def test_an_out_of_range_checkpoint_index_reports_the_range_not_an_api_key(
        tmp_path, capsys, monkeypatch, verb):
    """The same defect family as F94/F91, on the one handle the practice sheet types verbatim.
    `<feature>@<n>` is a deterministic reference, but `resolve_checkpoint` returns a bare `None`
    for an index past the feature's last chapter, so the ladder fell through to the NL rung and
    answered `could not resolve ... set OPENAI_API_KEY to enable natural-language targets`. No key
    conjures a chapter that does not exist, and the feature the user named resolved perfectly well:
    the answer they can act on is how many chapters it has."""
    _no_llm(monkeypatch)
    repo = corpus.CORPUS["linear_history"].build(tmp_path / "repo")
    get(repo)
    from sgt.api import segments_view

    feat = _revertable_feature(repo)
    assert feat is not None
    segs = [s for s in segments_view(repo) if s["feature_id"] == feat]
    assert segs, "the spanning lane should cut at least one chapter"
    past_the_end = len(segs) + 3

    rc = _in(repo, [verb, f"{feat}@{past_the_end}", "--yes"])
    out = capsys.readouterr().out
    assert rc != 0, f"{verb} of a nonexistent chapter returned 0:\n{out}"
    assert "OPENAI_API_KEY" not in out, (
        f"{verb} blames a missing API key for an out-of-range chapter index:\n{out}")
    assert str(len(segs)) in out, f"the refusal does not say how many chapters exist:\n{out}"
    assert f"{feat}@0" in out, f"the refusal offers no chapter that does exist:\n{out}"
