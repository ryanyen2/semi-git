"""Tests for ugh.cli.porcelain -- the D3 daily-loop verbs `save`/`undo` (plan U26).

The D2 refusal table itself (`ugh git <tree-mutating-sub>`) is covered end-to-end in
tests/test_cli_git_passthrough.py. This file covers the other half: `undo` over the operation log
(`oplog.undo` takes out the ops of the last commit ugh made), and `save`/`undo`
exercised through `cli.main` on real repos -- ending with the plan's named scenario, the full
daily loop running git-free.
"""

from __future__ import annotations

import contextlib
import json
import os

import ugh.cli as cli
from ugh.core import oplog
from ugh.core.lens import current_ideal, get
from ugh.core.store import Store
from ugh.store.gitbind import init_store
from tests.laws import corpus


def _revert(repo, op_id):
    from ugh.core import planner

    steps = [st for st, ops in planner._Ctx(repo).steps.items() if op_id in ops]
    return planner.apply(repo, planner.plan(repo, steps, target=op_id))


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
# undo takes out the ops of the last commit ugh made
# ---------------------------------------------------------------------------


def _tree(repo):
    return {p: (repo / p).read_bytes() for p in ("a.py", "b.py", "c.py") if (repo / p).exists()}


def test_undo_reports_empty_when_nothing_has_been_recorded(tmp_path):
    repo = corpus.CORPUS["linear_history"].build(tmp_path / "repo")
    get(repo)
    assert oplog.undo(repo).status == "empty"


def test_undo_brings_back_what_the_last_take_out_took(tmp_path):
    repo = corpus.CORPUS["linear_history"].build(tmp_path / "repo")
    original_ids = get(repo).op_ids
    original = _tree(repo)
    baz = next(o for o in Store(repo).all_ops() if "b.py::baz" in o.footprint)

    _revert(repo, baz.id)
    assert get(repo).op_ids > original_ids  # a take-out only adds ops
    outcome = oplog.undo(repo)
    assert outcome.status == "commit" and outcome.plan.undone
    assert _tree(repo) == original


def test_repeated_undo_walks_back_through_two_independent_edits(tmp_path):
    """Two take-outs log two commits; undo takes them out one at a time, newest first."""
    repo = corpus.CORPUS["linear_history"].build(tmp_path / "repo")
    get(repo)
    original = _tree(repo)
    ops = Store(repo).all_ops()
    baz = next(o for o in ops if "b.py::baz" in o.footprint)
    qux = [o for o in ops if "c.py::qux" in o.footprint][-1]

    _revert(repo, baz.id)
    after_first = _tree(repo)
    _revert(repo, qux.id)
    assert _tree(repo) != after_first

    assert oplog.undo(repo).status == "commit"
    assert _tree(repo) == after_first
    assert oplog.undo(repo).status == "commit"
    assert _tree(repo) == original
    assert oplog.undo(repo).status == "empty"


# ---------------------------------------------------------------------------
# `ugh save` / `ugh undo` through cli.main
# ---------------------------------------------------------------------------


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


def _seed_resolvable_fork(repo, symbol="a.py::foo"):
    """Add two genuinely-divergent tips to the store and record them as an open fork -- a fork the
    read side surfaces (real symbol, differing after versions), so a banner must render."""
    from ugh import state
    from ugh.core.op import make_op
    from ugh.core.store import Store

    store = Store(repo)
    a = store.add(make_op({symbol: ("v0", "v1")}, {symbol: b"a"}, kind="touched"))
    b = store.add(make_op({symbol: ("v0", "v2")}, {symbol: b"b"}, kind="rebirth"))
    state.save_json(repo, "forks", [
        {"symbol": symbol, "tips": [a.id, b.id], "remedy": f"ugh merge-op {a.id} {b.id}"},
    ])


def test_save_names_an_open_fork_but_does_not_block(tmp_path, capsys):
    """An open resolvable fork is impossible to ignore -- `ugh save` names the symbol and the
    `ugh resolve` remedy -- yet never blocks: the save still succeeds (rc 0)."""
    repo = corpus.CORPUS["linear_history"].build(tmp_path / "repo")
    _seed_resolvable_fork(repo)
    (repo / "d.py").write_text("def quux():\n    return 42\n", encoding="utf-8")
    with _in(repo):
        rc = cli.main(["save", "-m", "add quux"])
    out = capsys.readouterr().out
    assert rc == 0  # non-blocking: divergence-as-state never refuses a save
    assert "1 open fork" in out  # inflected, not the "fork(s)" placeholder
    assert "ugh resolve a.py::foo" in out


def test_save_json_reports_open_fork_count(tmp_path, capsys):
    """The `--json` save contract carries `open_fork_count` for parity with `sync_view`, so a machine
    consumer (VSCode toast) can key its warning on the same filtered count the terminal banner uses."""
    repo = corpus.CORPUS["linear_history"].build(tmp_path / "repo")
    _seed_resolvable_fork(repo)
    (repo / "d.py").write_text("def quux():\n    return 42\n", encoding="utf-8")
    with _in(repo):
        rc = cli.main(["save", "--json", "-m", "add quux"])
    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload["ok"] is True and payload["open_fork_count"] == 1


def test_save_as_leaves_the_tree_clean_for_the_next_verb(tmp_path):
    """F1: `save --as` writes `.sgt/names.json`, which travels on `refs/ugh/state` and must stay
    untracked -- also in a `.sgt` whose ignore file predates the names table -- or the very next
    `sync` aborts on a dirty tree."""
    gb, _ = init_store(tmp_path)
    (tmp_path / ".sgt" / ".gitignore").write_text(".gitignore\nops/\n", encoding="utf-8")
    (tmp_path / "a.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    gb.commit_all("base foo")
    get(tmp_path)

    (tmp_path / "b.py").write_text("def bar():\n    return 2\n", encoding="utf-8")
    with _in(tmp_path):
        assert cli.main(["save", "-m", "add bar", "--as", "bar"]) == 0

    assert (tmp_path / ".sgt" / "names.json").is_file()
    status = gb._git("status", "--porcelain").stdout
    assert gb.is_clean(), f"save left the tree dirty; next verb would refuse:\n{status}"


def test_save_with_a_message_harvests_it_as_a_turn(tmp_path):
    """Zero-burden intent capture (intent-ledger M1): a user-supplied `-m` message is recorded as a
    turn keyed by the witness commit sha -- the user's own words about the work, taken from their
    existing workflow, never a new prompt we asked them to type."""
    from ugh.intent import turns

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
    """The `ugh save` default placeholder is not intent, so a save with no `-m` records no turn --
    capture is faithful to what the user actually wrote, never a synthesized string."""
    from ugh.intent import turns

    repo = corpus.CORPUS["linear_history"].build(tmp_path / "repo")
    get(repo)
    (repo / "d.py").write_text("def quux():\n    return 42\n", encoding="utf-8")
    with _in(repo):
        assert cli.main(["save"]) == 0

    assert turns.load_turns(repo) == {}


def test_save_closes_a_capture_manifest(tmp_path):
    """The save beat closes the capture window (weave P1): everything captured since the previous
    save -- here a hook turn and the `-m` turn the save itself harvests -- plus the new ops'
    footprint anchors land in one durable manifest keyed by the witness commit. A second save
    chains its window from the first's end, so nothing is harvested twice."""
    from ugh.intent import manifest, turns
    from ugh.store.gitbind import GitBinding

    repo = corpus.CORPUS["linear_history"].build(tmp_path / "repo")
    get(repo)
    turns.record_turn(repo, key="cs-9", key_kind="chat", actor="human", channel="hook",
                      text="add a quux helper")
    (repo / "d.py").write_text("def quux():\n    return 42\n", encoding="utf-8")
    with _in(repo):
        assert cli.main(["save", "-m", "add quux helper"]) == 0

    sha = GitBinding(repo).head()
    rec = manifest.load_manifests(repo).get(sha)
    assert rec is not None
    assert sorted(t["channel"] for t in rec["turns"]) == ["cli", "hook"]
    assert any("d.py::quux" in o["symbols"] for o in rec["ops"])

    # the second save's window starts where the first ended: the first save's turns do not reappear
    (repo / "d.py").write_text("def quux():\n    return 43\n", encoding="utf-8")
    with _in(repo):
        assert cli.main(["save", "-m", "bump quux"]) == 0
    sha2 = GitBinding(repo).head()
    rec2 = manifest.load_manifests(repo)[sha2]
    assert rec2["start"] == rec["end"]
    assert [t["text"] for t in rec2["turns"]] == ["bump quux"]


def test_save_refuses_during_an_in_progress_merge(tmp_path, capsys):
    """F26 safety (0.9): `ugh save` with MERGE_HEAD present must refuse rather than commit the
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


def test_undo_inverts_a_save_and_then_reports_nothing_to_undo(tmp_path, capsys):
    repo = corpus.CORPUS["linear_history"].build(tmp_path / "repo")
    get(repo)
    (repo / "d.py").write_text("def quux():\n    return 42\n", encoding="utf-8")
    with _in(repo):
        cli.main(["save"])
        assert (repo / "d.py").exists()

        rc = cli.main(["undo"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "undo" in out
        assert not (repo / "d.py").exists()

        rc = cli.main(["undo"])
        assert rc == 0
        assert "nothing to undo" in capsys.readouterr().out


def test_full_daily_loop_runs_git_free(tmp_path, capsys):
    """save -> undo -> a raw `git reset` still refuses: the whole loop is ugh verbs, never a direct
    git tree mutation."""
    repo = tmp_path / "repo"
    gb, _base = _two_branches(repo)
    with _in(repo):
        get(repo)
        (repo / "e.py").write_text("def new_thing():\n    return 1\n", encoding="utf-8")
        assert cli.main(["save"]) == 0
        assert (repo / "e.py").exists()

        assert cli.main(["undo"]) == 0
        assert not (repo / "e.py").exists()
        capsys.readouterr()

        head = gb.head()
        rc = cli.main(["git", "reset", "--hard", "HEAD~1"])
        assert rc == 1
        err = capsys.readouterr().err
        assert "ugh undo" in err and "--force" in err
        assert gb.head() == head  # refused: HEAD never moved


def test_save_does_not_claim_nothing_to_save_while_the_mine_is_incomplete(tmp_path, capsys, monkeypatch):
    """F76/F78: while a backfill is still walking, `mine()` skips its dirty pass on every chunk that
    spends the whole budget on history (`mine.py:791`), so a real working-tree edit is never looked
    at. `save` must not report that as success -- "nothing to save" is a claim about the user's tree
    that ugh has not actually checked. Observed on ugh's own repo, where 267 of 351 commits were
    still unmined and `save` printed `✓ nothing to save` over a freshly added function."""
    from ugh.core import lens

    repo = corpus.CORPUS["linear_history"].build(tmp_path / "repo")
    monkeypatch.setattr(lens, "_CHUNK_BUDGET_SECONDS", 0.0)  # every chunk dies before the dirty pass
    get(repo)
    assert not lens.sync_status(repo)["complete"]  # the precondition this test is about

    (repo / "c.py").write_text(
        "def qux():\n    return 'unrelated'\n\n\ndef added_by_user():\n    return 42\n", encoding="utf-8"
    )
    with _in(repo):
        rc = cli.main(["save", "-m", "add added_by_user"])
    out = capsys.readouterr().out
    assert "nothing to save" not in out, f"save claimed nothing to save mid-backfill: {out!r}"
    assert rc != 0 or "added_by_user" in out


