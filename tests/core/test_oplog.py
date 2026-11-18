"""Tests for ugh.core.oplog -- the operation log and `ugh undo`: a commit ugh made is undone by
taking out the ops mined from it; a `name` or `rename` by restoring its names snapshot."""

from __future__ import annotations

from pathlib import Path

from ugh import names
from ugh.core import oplog
from ugh.core.lens import current_ideal, get
from ugh.core.store import Store
from ugh.store.gitbind import init_store
from tests.laws import corpus


# ---------------------------------------------------------------------------
# store: append / tail
# ---------------------------------------------------------------------------



def _revert(repo, op_id):
    from ugh.core import planner

    steps = [st for st, ops in planner._Ctx(repo).steps.items() if op_id in ops]
    return planner.apply(repo, planner.plan(repo, steps, target=op_id))

def test_append_tail_is_reverse_chronological(tmp_path):
    repo = corpus.CORPUS["linear_history"].build(tmp_path / "repo")
    get(repo)
    oplog.append(repo, {"kind": "names", "snapshot": {}, "verb": "a"})
    oplog.append(repo, {"kind": "names", "snapshot": {}, "verb": "b"})

    tail = oplog.tail(repo)
    assert tail is not None and tail["verb"] == "b"  # LIFO: newest is the tail


def test_undo_on_an_empty_log_reports_nothing_to_undo(tmp_path):
    repo = corpus.CORPUS["linear_history"].build(tmp_path / "repo")
    get(repo)
    outcome = oplog.undo(repo)
    assert outcome.status == "empty"
    assert "nothing to undo" in outcome.message


def test_drop_event_removes_the_target_and_never_clobbers_a_concurrent_append(tmp_path):
    """`undo` cannot hold the lock across `apply_inverse` (it re-enters the non-reentrant flock),
    so it drops the inverted event in a separate lock-held read-modify-write via `_drop_event`.
    This proves the guarantee that closes the lost-append race: an event appended *after* `undo`
    read its tail (modelled here by `B` following `A` on the stack) survives the drop of `A`, and a
    plain tail drop removes only the tail."""
    repo = corpus.CORPUS["linear_history"].build(tmp_path / "repo")
    get(repo)
    key = oplog._ref_key(Path(repo))
    a = {"kind": "names", "snapshot": {}, "verb": "a"}
    b = {"kind": "names", "snapshot": {}, "verb": "b"}
    oplog.append(repo, a, ref_key=key)
    oplog.append(repo, b, ref_key=key)  # a concurrent append landing during undo's apply window

    # undo read `a` as the tail, applied its inverse, then goes to drop it: `b` (appended
    # concurrently) must be preserved, and `a` removed from below the tail.
    assert oplog._drop_event(repo, key, a) is True
    assert oplog.load(repo)[key] == [b], "the concurrently-appended event must survive"

    # plain tail case: dropping `b` leaves an empty stack, no clobber.
    assert oplog._drop_event(repo, key, b) is True
    assert oplog.load(repo).get(key, []) == []
    # dropping an event already gone is a no-op, not an error.
    assert oplog._drop_event(repo, key, a) is False


def _seed_two_symbols(repo):
    """A repo with foo+bar committed and mined."""
    from ugh.core import lens

    gb, _ = init_store(repo)
    (repo / "a.py").write_text("def foo():\n    return 1\n\n\ndef bar():\n    return 2\n", encoding="utf-8")
    gb.commit_all("add foo and bar")
    lens.get(repo)
    return gb


def _bar(repo):
    return next(o.id for o in Store(repo).all_ops() if "a.py::bar" in o.footprint)


# ---------------------------------------------------------------------------
# commit kind: undo takes out the ops mined from the commit
# ---------------------------------------------------------------------------


def test_undo_of_a_take_out_brings_it_back_and_walks_back(tmp_path):
    repo = tmp_path / "repo"
    _seed_two_symbols(repo)
    before = (repo / "a.py").read_bytes()
    _revert(repo, _bar(repo))
    assert b"def bar" not in (repo / "a.py").read_bytes()
    assert oplog.tail(repo)["kind"] == "commit"

    outcome = oplog.undo(repo)
    assert outcome.status == "commit"
    assert (repo / "a.py").read_bytes() == before
    assert oplog.undo(repo).status == "empty"  # the undo itself is not logged


def test_undo_keeps_work_committed_after_the_take_out(tmp_path):
    """Undoing a take-out un-applies only that take-out, so a raw commit made since stays."""
    repo = tmp_path / "repo"
    gb = _seed_two_symbols(repo)
    _revert(repo, _bar(repo))
    (repo / "b.py").write_text("def baz():\n    return 3\n", encoding="utf-8")
    gb.commit_all("RAW: add baz")
    get(repo)

    assert oplog.undo(repo).status == "commit"
    assert b"def bar" in (repo / "a.py").read_bytes()
    assert (repo / "b.py").read_text() == "def baz():\n    return 3\n"


# ---------------------------------------------------------------------------
# names kind
# ---------------------------------------------------------------------------


def test_undo_of_a_rename_restores_the_names_before_it(tmp_path):
    repo = corpus.CORPUS["linear_history"].build(tmp_path / "repo")
    get(repo)
    op = next(iter(current_ideal(repo).op_ids))
    names.name(repo, [op], "checkout")
    names.rename(repo, "checkout", "billing")
    assert names.load(repo) == {op: "billing"}

    outcome = oplog.undo(repo)
    assert outcome.status == "names"
    assert names.load(repo) == {op: "checkout"}
    assert oplog.undo(repo).status == "names"
    assert names.load(repo) == {}


# ---------------------------------------------------------------------------
# preview: what undo will do, before it does it
# ---------------------------------------------------------------------------

def test_preview_names_what_undo_would_take_out(tmp_path):
    repo = tmp_path / "repo"
    _seed_two_symbols(repo)
    _revert(repo, _bar(repo))
    pv = oplog.preview(repo)
    assert pv["kind"] == "commit" and pv["ok"] is True
    assert pv["undone"] and any(st.startswith("a.py::bar@") for st in pv["steps"])


def test_preview_of_an_empty_log_says_so_without_raising(tmp_path):
    repo = tmp_path / "repo"
    _seed_two_symbols(repo)
    pv = oplog.preview(repo)
    assert pv["kind"] is None and pv["ok"] is True and pv["undone"] == 0


def test_preview_writes_nothing(tmp_path):
    """A preview that mutates is not a preview."""
    repo = tmp_path / "repo"
    _seed_two_symbols(repo)
    _revert(repo, _bar(repo))
    before_log = oplog.load(repo)
    before_ideal = current_ideal(repo).op_ids
    before_tree = (repo / "a.py").read_bytes()

    oplog.preview(repo)

    assert oplog.load(repo) == before_log
    assert current_ideal(repo).op_ids == before_ideal
    assert (repo / "a.py").read_bytes() == before_tree
