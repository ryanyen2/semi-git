"""Tests for sgt.cli.porcelain -- the D3 daily-loop verbs `switch`/`save`/`undo` (plan U26).

The D2 refusal table itself (`sgt git <tree-mutating-sub>`) is covered end-to-end in
tests/test_cli_git_passthrough.py. This file covers the other half: the ideal-edit journal that
makes `undo` possible (`lens.record_ideal` -> `oplog.undo`), and `switch`/`save`/`undo`
exercised through `cli.main` on real repos -- ending with the plan's named scenario, the full
daily loop running git-free.
"""

from __future__ import annotations

import contextlib
import json
import os

import sgt.cli as cli
from sgt.core import verbs
from sgt.core import oplog
from sgt.core.lens import current_ideal, get
from sgt.core.store import Store
from sgt.store.gitbind import init_store
from tests.laws import corpus


@contextlib.contextmanager
def _in(repo):
    cwd = os.getcwd()
    os.chdir(repo)
    try:
        yield
    finally:
        os.chdir(cwd)


def _two_branches(repo_path):
    """`main` with just `a.py::foo`; `feature` (checked out) adds an independent `a.py::bar`."""
    gb, _ = init_store(repo_path)
    (repo_path / "a.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    gb.commit_all("base foo")
    base = gb.symbolic_ref().rsplit("/", 1)[-1]
    gb._git("checkout", "-q", "-b", "feature")
    (repo_path / "a.py").write_text(
        "def foo():\n    return 1\n\n\ndef bar():\n    return 2\n", encoding="utf-8"
    )
    gb.commit_all("feature: add independent bar")
    return gb, base


# ---------------------------------------------------------------------------
# The ideal-edit undo (lens.record_ideal -> unified oplog.undo)
# ---------------------------------------------------------------------------


def test_undo_reports_empty_when_nothing_has_been_recorded(tmp_path):
    repo = corpus.CORPUS["linear_history"].build(tmp_path / "repo")
    get(repo)
    assert oplog.undo(repo).status == "empty"


def test_undo_restores_the_ideal_from_before_the_last_apply(tmp_path):
    """`verbs.revert`'s apply path calls `lens.put` + `lens.record_ideal`, which appends the
    outgoing ideal as an `ideal_edit` event; `oplog.undo` pops that event and restores it exactly
    (set arithmetic), reporting the delta via its `UndoResult`."""
    repo = corpus.CORPUS["linear_history"].build(tmp_path / "repo")
    original_ids = get(repo).op_ids
    ops = Store(repo).all_ops()
    baz = next(o for o in ops if "b.py::baz" in o.footprint)

    verbs.revert(repo, baz.id)
    reverted_ids = get(repo).op_ids
    assert baz.id not in reverted_ids

    outcome = oplog.undo(repo)
    assert outcome.status == "ideal_edit"
    result = outcome.ideal
    assert result.ideal.op_ids == original_ids
    assert get(repo).op_ids == original_ids
    assert result.removed == set()  # nothing left over from the revert
    assert result.added == original_ids - reverted_ids  # baz.id came back


def test_repeated_undo_walks_back_through_two_independent_edits(tmp_path):
    """Two applied edits push two events; undo pops them one at a time, oldest last."""
    repo = corpus.CORPUS["linear_history"].build(tmp_path / "repo")
    original_ids = get(repo).op_ids
    ops = Store(repo).all_ops()
    baz = next(o for o in ops if "b.py::baz" in o.footprint)
    qux_ops = [o for o in ops if "c.py::qux" in o.footprint]
    qux_tip = max(qux_ops, key=lambda o: len(o.footprint["c.py::qux"]))

    verbs.revert(repo, baz.id)
    after_first = get(repo).op_ids
    verbs.revert(repo, qux_tip.id)
    after_second = get(repo).op_ids
    assert after_second != after_first

    first_undo = oplog.undo(repo)
    assert get(repo).op_ids == after_first  # back to just the baz revert

    second_undo = oplog.undo(repo)
    assert get(repo).op_ids == original_ids  # both edits inverted

    assert oplog.undo(repo).status == "empty"  # log exhausted
    assert first_undo.ideal.ideal.op_ids == after_first
    assert second_undo.ideal.ideal.op_ids == original_ids


# ---------------------------------------------------------------------------
# `sgt switch` / `sgt save` / `sgt undo` through cli.main
# ---------------------------------------------------------------------------


def test_switch_materializes_the_target_branchs_ideal(tmp_path):
    repo = tmp_path / "repo"
    gb, base = _two_branches(repo)
    with _in(repo):
        rc = cli.main(["switch", base])
    assert rc == 0
    assert current_ideal(repo).op_ids == get(repo).op_ids
    assert b"def bar" not in (repo / "a.py").read_bytes()  # base never had bar
    assert gb.symbolic_ref().rsplit("/", 1)[-1] == base


def test_switch_reports_a_git_error_for_an_unknown_branch(tmp_path, capsys):
    repo = tmp_path / "repo"
    _two_branches(repo)
    with _in(repo):
        rc = cli.main(["switch", "does-not-exist"])
    assert rc == 1
    assert "✗" in capsys.readouterr().out  # _fail's "✗" marker


def test_switch_json_reports_branch_and_op_count(tmp_path, capsys):
    repo = tmp_path / "repo"
    gb, base = _two_branches(repo)
    with _in(repo):
        rc = cli.main(["switch", base, "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload == {"ok": True, "branch": base, "ops": len(get(repo).op_ids)}


def test_save_reports_nothing_to_save_on_a_clean_tree(tmp_path, capsys):
    repo = corpus.CORPUS["linear_history"].build(tmp_path / "repo")
    get(repo)
    with _in(repo):
        rc = cli.main(["save"])
    assert rc == 0
    assert "nothing to save" in capsys.readouterr().out


def test_save_json_reports_nothing_to_save_on_a_clean_tree(tmp_path, capsys):
    repo = corpus.CORPUS["linear_history"].build(tmp_path / "repo")
    get(repo)
    with _in(repo):
        rc = cli.main(["save", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert payload == {"ok": True, "saved": False, "message": "nothing to save -- no uncommitted ops"}


def test_save_commits_a_witness_for_a_dirty_tree(tmp_path, capsys):
    repo = corpus.CORPUS["linear_history"].build(tmp_path / "repo")
    before_ids = get(repo).op_ids
    (repo / "d.py").write_text("def quux():\n    return 42\n", encoding="utf-8")
    with _in(repo):
        rc = cli.main(["save", "-m", "add quux"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "save" in out
    after_ids = get(repo).op_ids
    assert after_ids != before_ids
    assert current_ideal(repo).op_ids == after_ids


def test_save_leaves_the_tree_clean_for_the_next_verb(tmp_path):
    """F1 (workflow-hardening 2026-07-31-001): the save-time ownership cascade (`assign_at_save`)
    must be folded *into* the witness commit, not written after it. When a save introduces a brand-
    new symbol the cascade writes the committed `.sgt` tables (pins/authored/tree); running it after
    `put` already committed leaves them modified/untracked, so the very next `switch`/`sync`/`land`
    aborts on a dirty tree. Reproduce by saving a new symbol (forcing a lane mint) and asserting the
    tree is clean afterward -- exactly the precondition those verbs require."""
    from sgt.lens import tree

    gb, _ = init_store(tmp_path)
    (tmp_path / "a.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    gb.commit_all("base foo")
    ideal = get(tmp_path)
    # The cascade only fires once a feature tree exists (the first build owns the initial
    # clustering); build it so this save actually exercises the lane-mint write path.
    tree.save(tmp_path, tree.build(tmp_path, Store(tmp_path).all_ops(), ideal))

    (tmp_path / "b.py").write_text("def bar():\n    return 2\n", encoding="utf-8")
    with _in(tmp_path):
        assert cli.main(["save", "-m", "add bar"]) == 0

    status = gb._git("status", "--porcelain").stdout
    dirty = [ln for ln in status.splitlines() if ".sgt/" in ln and "/local/" not in ln]
    assert dirty == [], f"save left committed .sgt metadata dirty: {dirty!r}"
    assert gb.is_clean(), f"save left the tree dirty; next verb would refuse:\n{status}"


def test_save_with_a_message_harvests_it_as_a_turn(tmp_path):
    """Zero-burden intent capture (intent-ledger M1): a user-supplied `-m` message is recorded as a
    turn keyed by the witness commit sha -- the user's own words about the work, taken from their
    existing workflow, never a new prompt we asked them to type."""
    from sgt.intent import turns

    repo = corpus.CORPUS["linear_history"].build(tmp_path / "repo")
    get(repo)
    (repo / "d.py").write_text("def quux():\n    return 42\n", encoding="utf-8")
    with _in(repo):
        assert cli.main(["save", "-m", "add quux helper"]) == 0

    captured = [t for t in turns.load_turns(repo).values() if t["text"] == "add quux helper"]
    assert len(captured) == 1
    assert captured[0]["key_kind"] == "sha"
    assert captured[0]["actor"] == "human"


def test_save_without_a_message_harvests_no_turn(tmp_path):
    """The `sgt save` default placeholder is not intent, so a save with no `-m` records no turn --
    capture is faithful to what the user actually wrote, never a synthesized string."""
    from sgt.intent import turns

    repo = corpus.CORPUS["linear_history"].build(tmp_path / "repo")
    get(repo)
    (repo / "d.py").write_text("def quux():\n    return 42\n", encoding="utf-8")
    with _in(repo):
        assert cli.main(["save"]) == 0

    assert turns.load_turns(repo) == {}


def test_save_echo_reports_no_words_captured_without_a_message(tmp_path, capsys):
    """Save-echo legibility (intent-ledger P1): a bare save (no `-m`, no plan step) says so
    explicitly rather than staying silent or -- the failure the design forbids -- printing the
    temporally-nearest unrelated turn as if it were this save's words. The trust loop depends on the
    echo never bluffing."""
    repo = corpus.CORPUS["linear_history"].build(tmp_path / "repo")
    get(repo)
    (repo / "d.py").write_text("def quux():\n    return 42\n", encoding="utf-8")
    with _in(repo):
        assert cli.main(["save"]) == 0
    out = capsys.readouterr().out
    assert "no words captured" in out


def test_save_echo_does_not_duplicate_the_message_line(tmp_path, capsys):
    """When `-m` is given the header already echoes it in quotes; the dedicated words line is only
    for the no-`-m` cases, so a `-m` save must NOT also print a `· ...` line or the 'no words
    captured' empty state."""
    repo = corpus.CORPUS["linear_history"].build(tmp_path / "repo")
    get(repo)
    (repo / "d.py").write_text("def quux():\n    return 42\n", encoding="utf-8")
    with _in(repo):
        assert cli.main(["save", "-m", "add quux helper"]) == 0
    out = capsys.readouterr().out
    assert '"add quux helper"' in out          # header echoes the message
    assert "no words captured" not in out       # ...and doesn't also claim none
    assert "· add quux helper" not in out       # ...nor duplicate it on a second line


def test_save_json_carries_the_captured_words(tmp_path, capsys):
    """The captured words are structured in `--json` so the editor / VSCode surface reads them from
    the same save output the terminal renders -- the integrate-don't-annex contract."""
    repo = corpus.CORPUS["linear_history"].build(tmp_path / "repo")
    get(repo)
    (repo / "d.py").write_text("def quux():\n    return 42\n", encoding="utf-8")
    with _in(repo):
        assert cli.main(["save", "-m", "add quux helper", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["words"] == "add quux helper"


def test_echo_words_prefers_message_then_plan_then_none():
    """`_echo_words` priority, in isolation: the `-m` message wins; with no message it falls to the
    stated intent of an auto-confirmed plan step; with neither it returns None (the caller renders
    the explicit empty state). It never reaches for chat turns -- those are the P2 rung."""
    from sgt.cli.porcelain import _echo_words

    assert _echo_words("unused", "typed words", None) == "typed words"
    assert _echo_words("unused", None, None) is None
    assert _echo_words("unused", None, {"auto_confirmed": []}) is None


def test_save_message_feeds_the_segment_labeler_via_the_local_turn(tmp_path):
    """Goal-1 label feed (M1), kept local: the `-m` message is harvested as a turn keyed by the
    witness sha, and the segment labeler's `label_prompt_for` resolves it from there -- so the
    feature is named in the user's own words with no committed-sidecar write (a save must not dirty
    `.sgt/`) and no new labeling code."""
    from sgt.intent import turns
    from sgt.intent.theme_segment import label_prompt_for

    repo = corpus.CORPUS["linear_history"].build(tmp_path / "repo")
    get(repo)
    (repo / "d.py").write_text("def quux():\n    return 42\n", encoding="utf-8")
    with _in(repo):
        assert cli.main(["save", "-m", "extract the quux helper"]) == 0

    turn = next(t for t in turns.load_turns(repo).values() if t["text"] == "extract the quux helper")
    assert label_prompt_for(repo, turn["key"]) == "extract the quux helper"


def test_save_refuses_during_an_in_progress_merge(tmp_path, capsys):
    """F26 safety (0.9): `sgt save` with MERGE_HEAD present must refuse rather than commit the
    conflict-marker bytes and finalize the merge blind."""
    gb, _ = init_store(tmp_path)
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    gb.commit_all("base")
    base = gb.symbolic_ref().rsplit("/", 1)[-1]
    gb._git("checkout", "-q", "-b", "other")
    (tmp_path / "a.py").write_text("x = 2\n", encoding="utf-8")
    gb.commit_all("other: x=2")
    gb._git("checkout", "-q", base)
    (tmp_path / "a.py").write_text("x = 3\n", encoding="utf-8")
    gb.commit_all("main: x=3")
    gb._git("merge", "other", check=False)  # conflicts -> leaves MERGE_HEAD, uncommitted
    assert gb.rev_parse("MERGE_HEAD") is not None
    head_before = gb.head()

    with _in(tmp_path):
        rc = cli.main(["save"])

    out = capsys.readouterr().out
    assert rc != 0
    assert "merge" in out.lower()
    assert gb.head() == head_before  # nothing committed
    assert gb.rev_parse("MERGE_HEAD") is not None  # merge still in progress


def test_save_with_no_active_plan_omits_the_plan_key(tmp_path, capsys):
    """The plan-matching fold (U12) is invisible when no plan session is active: a plain dirty save
    carries no `plan` key, so `save`'s common-case JSON shape is byte-unchanged."""
    repo = corpus.CORPUS["linear_history"].build(tmp_path / "repo")
    get(repo)
    (repo / "d.py").write_text("def quux():\n    return 42\n", encoding="utf-8")
    with _in(repo):
        rc = cli.main(["save", "-m", "add quux", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True and payload["saved"] is True
    assert "plan" not in payload  # no active plan -> no fold reporting


def test_save_assigns_a_new_symbol_a_durable_lane(tmp_path):
    """U6 integration: `sgt save` runs the ownership cascade -- a new function added after a `map`
    lands a durable assign pin (so a later recluster keeps it in place), and the save still
    succeeds. The lane algebra + visibility are pinned in tests/lens/test_ledger.py; this is the
    thin `cli.main` wiring."""
    from sgt.lens import map as lensmap
    from sgt.lens.pins import load_pins

    gb, _ = init_store(tmp_path)
    (tmp_path / "core.py").write_text(
        "def alpha():\n    return 1\n\n\ndef beta():\n    return alpha()\n", encoding="utf-8")
    gb.commit_all("core")
    get(tmp_path)
    lensmap.build_map(tmp_path)  # a persisted tree the cascade can build on

    (tmp_path / "core.py").write_text(
        "def alpha():\n    return 1\n\n\ndef beta():\n    return alpha()\n\n\n"
        "def delta():\n    return alpha() + beta()\n", encoding="utf-8")
    with _in(tmp_path):
        rc = cli.main(["save", "-m", "add delta"])
    assert rc == 0
    assert "core.py::delta" in load_pins(tmp_path).assign  # the cascade pinned it


def test_undo_inverts_a_save_and_then_reports_nothing_to_undo(tmp_path, capsys):
    repo = corpus.CORPUS["linear_history"].build(tmp_path / "repo")
    before_ids = get(repo).op_ids
    (repo / "d.py").write_text("def quux():\n    return 42\n", encoding="utf-8")
    with _in(repo):
        cli.main(["save"])
        assert get(repo).op_ids != before_ids

        rc = cli.main(["undo"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "undo" in out
        assert get(repo).op_ids == before_ids

        rc = cli.main(["undo"])
        assert rc == 0
        assert "nothing to undo" in capsys.readouterr().out


def test_full_daily_loop_runs_git_free(tmp_path, capsys):
    """switch -> save -> undo -> a raw `git checkout` still refuses: the whole loop is sgt verbs,
    never a direct git tree mutation."""
    repo = tmp_path / "repo"
    gb, base = _two_branches(repo)
    with _in(repo):
        assert cli.main(["switch", base]) == 0
        assert gb.symbolic_ref().rsplit("/", 1)[-1] == base

        before_ids = get(repo).op_ids
        (repo / "e.py").write_text("def new_thing():\n    return 1\n", encoding="utf-8")
        assert cli.main(["save"]) == 0
        assert get(repo).op_ids != before_ids

        assert cli.main(["undo"]) == 0
        assert get(repo).op_ids == before_ids
        capsys.readouterr()

        rc = cli.main(["git", "checkout", "feature"])
        assert rc == 1
        err = capsys.readouterr().err
        assert "sgt switch" in err and "--force" in err
        assert gb.symbolic_ref().rsplit("/", 1)[-1] == base  # refused: HEAD never moved


def test_switch_preserves_a_symlink_in_both_directions(tmp_path):
    """U1/R3: a symlink is unmanaged, so switching branches (each of which materializes an ideal
    that never covers the link) must leave the link and its external target intact both ways."""
    repo = tmp_path / "repo"
    gb, base = _two_branches(repo)
    outside = tmp_path / "outside.txt"
    outside.write_text("SECRET\n", encoding="utf-8")
    with _in(repo):
        cli.main(["switch", base])
        (repo / "link.txt").symlink_to(outside)
        gb.commit_all("add link on base")
        get(repo)  # mine the link commit (skipped as unmanaged)

        assert cli.main(["switch", "feature"]) == 0
        assert cli.main(["switch", base]) == 0

    assert outside.read_text() == "SECRET\n"      # target never clobbered
    assert (repo / "link.txt").is_symlink()        # link survives both switches
