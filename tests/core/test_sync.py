"""Tests for sgt.core.sync -- the U15 `sgt sync` pipeline (plan R19/AE4).

Op-store union is nearly free (git's own file merge dedups by content-addressed path); the real
surface under test is what isn't free: same-symbol chain forks must be *surfaced* with a concrete
remedy rather than silently resolved, a same-id op independently mined on both clones must end up
with both sides' provenance (git's `-X ours` alone would drop one), pins/declared-edges/the
feature tree must reconcile across the union, and a second `sync` must be a no-op.

Two-clone fixtures are built directly with `GitBinding`/`lens` (no `tests/laws/corpus.py` case
fits a bare-remote-plus-two-working-clones shape) but follow the same hermetic discipline: real
`git` subprocess calls, no network, no wall-clock/LLM dependency.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from sgt.core import lens, sync, verbs
from sgt.core.store import Store
from sgt.lens.pins import Pins, load_pins, save_pins
from sgt.store.gitbind import GitBinding


def _init_bare(root: Path) -> Path:
    remote = root / "remote.git"
    remote.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True, capture_output=True
    )
    return remote


def _clone(remote: Path, dest: Path) -> Path:
    subprocess.run(["git", "clone", "-q", str(remote), str(dest)], check=True, capture_output=True)
    GitBinding(dest).init()  # repo-scope identity, matches every other fixture in this suite
    return dest


def _push(repo: Path, branch: str = "main") -> None:
    subprocess.run(
        ["git", "-C", str(repo), "push", "-q", "origin", branch], check=True, capture_output=True
    )


def _edit_and_commit(repo: Path, path: str, content: str, message: str) -> str:
    """Write content, commit it for real (works whether or not HEAD exists yet -- dirty-tree
    mining can't see anything on an unborn branch, so a real commit comes first), mine it, then
    re-commit with `Sgt-Op:` trailers via `lens.put`. `sync.py` reads a remote ref's ideal purely
    from its tip commit's trailers, without ever checking that ref out -- so any commit that can
    become a ref's tip in these tests must carry them, exactly like `lens.put` does on every real
    commit outside tests."""
    (repo / path).write_text(content, encoding="utf-8")
    content_sha = GitBinding(repo).commit_all(message)
    ideal = lens.get(repo)
    put_sha = lens.put(repo, ideal, message=f"sgt: mine {message}")
    lens.record_ideal(repo, ideal, put_sha)
    return content_sha  # the commit that actually witnesses the diff -- ops' provenance points here


def _two_clones(tmp_path: Path, main_py: str) -> tuple[Path, Path]:
    """A bare remote plus two clones, both past one shared init commit that writes `main_py`."""
    remote = _init_bare(tmp_path)
    a = _clone(remote, tmp_path / "a")
    lens.init(a)
    _edit_and_commit(a, "main.py", main_py, "init")
    _push(a)
    b = _clone(remote, tmp_path / "b")
    lens.get(b)  # baseline mine, mirrors a fresh teammate clone
    return a, b


_BASE = "def foo():\n    return 1\n\n\ndef bar():\n    return 2\n"


def test_sync_merges_disjoint_edits_with_zero_interaction(tmp_path):
    """AE4: two clones edit disjoint symbols; sync merges with zero interaction and no fork."""
    a, b = _two_clones(tmp_path, _BASE)

    _edit_and_commit(a, "main.py", "def foo():\n    return 100\n\n\ndef bar():\n    return 2\n", "bump foo")
    _push(a)
    _edit_and_commit(b, "main.py", "def foo():\n    return 1\n\n\ndef bar():\n    return 200\n", "bump bar")

    report = sync.sync(b, remote="origin", branch="main")

    assert report.merged
    assert not report.forks
    text = (b / "main.py").read_text(encoding="utf-8")
    assert "return 100" in text  # A's edit
    assert "return 200" in text  # B's edit, folded together with zero interaction


def test_sync_is_idempotent_and_double_mine_is_deterministic(tmp_path):
    a, b = _two_clones(tmp_path, _BASE)
    _edit_and_commit(a, "main.py", "def foo():\n    return 100\n\n\ndef bar():\n    return 2\n", "bump foo")
    _push(a)

    first = sync.sync(b, remote="origin", branch="main")
    assert first.merged

    ideal_after_sync = lens.get(b)  # re-mining B's own just-written merge commit is a no-op
    assert ideal_after_sync.op_ids == lens.current_ideal(b).op_ids

    second = sync.sync(b, remote="origin", branch="main")
    assert not second.merged
    assert second.message == "already up to date"


def test_sync_records_a_fork_and_lands_the_forked_symbol_at_the_common_ancestor(tmp_path):
    """Divergence-as-state (U20/C4, updated from the pre-U20 abort-on-fork behavior): a same-symbol
    fork no longer aborts the sync. Here the *only* divergence is the forked symbol, so there is no
    fork-free advance -- but the fork is still recorded as durable, committed `.sgt/forks.json`
    state, and the forked symbol materializes at the pre-fork common ancestor (never either tip).
    `merged` is False (an open fork needs attention) even though the reconciling merge commit
    lands."""
    a, b = _two_clones(tmp_path, _BASE)

    _edit_and_commit(a, "main.py", "def foo():\n    return 999\n\n\ndef bar():\n    return 2\n", "A: rework foo")
    _push(a)
    _edit_and_commit(b, "main.py", "def foo():\n    return 42\n\n\ndef bar():\n    return 2\n", "B: rework foo")

    gb = GitBinding(b)
    before_head = gb.head()

    report = sync.sync(b, remote="origin", branch="main")

    assert not report.merged  # an open fork -- attention needed
    assert "merge-op" in report.message
    assert len(report.forks) == 1
    symbol, _tip_a, _tip_b = report.forks[0]
    assert symbol == "main.py::foo"

    assert (b / ".sgt" / "forks.json").is_file()  # the fork is durable, shared state (LAW-R)
    text = (b / "main.py").read_text(encoding="utf-8")
    assert "return 1" in text  # the forked symbol sits at the common ancestor...
    assert "return 42" not in text and "return 999" not in text  # ...never either tip
    assert gb.is_clean()  # the reconciling merge landed cleanly, not left half-applied
    assert gb.head() == report.merge_sha and gb.head() != before_head  # branch advanced past the fork


def test_sync_dedups_an_op_independently_mined_on_both_clones(tmp_path):
    """The identification law at sync time: the same symbol added identically on both clones
    mines to one op id on each side; the union must not double it, and must keep both sides'
    provenance (git's own `-X ours` merge would otherwise drop one side's witness commit)."""
    a, b = _two_clones(tmp_path, _BASE)
    baz = _BASE + "\n\ndef baz():\n    return 42\n"

    a_sha = _edit_and_commit(a, "main.py", baz, "A: add baz")
    _push(a)
    b_sha = _edit_and_commit(b, "main.py", baz, "B: add baz (same content, independently)")

    before_ids = {op.id for op in Store(b).all_ops()}
    report = sync.sync(b, remote="origin", branch="main")

    assert report.merged
    assert not report.forks
    after_ids = {op.id for op in Store(b).all_ops()}
    assert after_ids == before_ids  # zero new op ids -- both sides had already identified it
    assert report.ops_added == 0

    baz_op = next(op for op in Store(b).all_ops() if "main.py::baz" in op.footprint)
    assert {a_sha, b_sha} <= set(baz_op.provenance)


def test_sync_reports_a_pin_contradiction_and_still_merges(tmp_path):
    a, b = _two_clones(tmp_path, _BASE)

    save_pins(a, Pins(assign={"m1": "featureA", "m2": "featureB"}))
    GitBinding(a).commit_all("A: pin m1/m2 to separate features")
    _push(a)

    save_pins(b, Pins(must_link=frozenset({("m1", "m2")})))
    GitBinding(b).commit_all("B: must-link m1 and m2")

    report = sync.sync(b, remote="origin", branch="main")

    assert report.merged
    assert len(report.pin_contradictions) == 1
    contradiction = report.pin_contradictions[0]
    assert contradiction.kind == "assign_conflict_in_must_link_group"

    unioned = load_pins(b)
    assert unioned.must_link == frozenset({("m1", "m2")})
    assert unioned.assign == {"m1": "featureA", "m2": "featureB"}


def test_sync_reports_a_declared_edge_cycle_and_declared_edges_travel(tmp_path):
    a, b = _two_clones(tmp_path, _BASE)

    verbs.after(a, "main.py::foo", "main.py::bar")  # A declares foo <= bar
    GitBinding(a).commit_all("A: declare foo <= bar")
    _push(a)

    verbs.after(b, "main.py::bar", "main.py::foo")  # B declares bar <= foo -- a cycle once unioned
    GitBinding(b).commit_all("B: declare bar <= foo")

    report = sync.sync(b, remote="origin", branch="main")

    assert report.merged
    assert len(report.declared_cycles) > 0

    ops = Store(b).all_ops()
    foo_id = next(op.id for op in ops if "main.py::foo" in op.footprint)
    bar_id = next(op.id for op in ops if "main.py::bar" in op.footprint)
    declared = lens._load_declared(b)
    assert (foo_id, bar_id) in declared  # A's declared edge travelled to B post-sync
    assert (bar_id, foo_id) in declared


# -- U8: three-way resolve -- reverts travel, sync stops resurrecting removed work ---------------

_WITH_BAZ = _BASE + "\n\ndef baz():\n    return 42\n"


def test_sync_revert_travels_and_removes_the_bytes(tmp_path):
    """The review's resurrection reproduction, inverted (U8/R10-R11): A adds baz, B syncs it, then A
    *reverts* baz and pushes. On B's next sync the revert travels -- baz leaves B's ideal *and* its
    bytes leave the working tree -- instead of the blind union resurrecting it from B's own side."""
    a, b = _two_clones(tmp_path, _BASE)
    _edit_and_commit(a, "main.py", _WITH_BAZ, "A: add baz")
    _push(a)
    sync.sync(b, remote="origin", branch="main")
    assert "def baz" in (b / "main.py").read_text(encoding="utf-8")  # B has it after the first sync

    baz_op = next(o for o in Store(a).all_ops() if "main.py::baz" in o.footprint)
    verbs.revert(a, baz_op.id)  # A reverts baz on its own clone
    _push(a)

    sync.sync(b, remote="origin", branch="main")
    assert "def baz" not in (b / "main.py").read_text(encoding="utf-8")  # the revert traveled
    assert baz_op.id not in lens.current_ideal(b).op_ids  # ...in the ideal, not just the bytes


def test_sync_revert_of_a_base_op_removes_the_dependents_that_rode_its_upset(tmp_path):
    """Scenario 3: A reverts a base op while B extended that op's symbol. The extension rides the
    reverted op's up-set and is removed with it (it stops being grounded once its base is gone) --
    B's work is not silently duplicated onto a resurrected base."""
    a, b = _two_clones(tmp_path, _BASE)
    _edit_and_commit(a, "main.py", _WITH_BAZ, "A: add baz")  # base op for baz
    _push(a)
    sync.sync(b, remote="origin", branch="main")

    # B extends baz (a new op chaining onto A's baz), A reverts baz's original add.
    _edit_and_commit(b, "main.py", _BASE + "\n\ndef baz():\n    return 43\n", "B: bump baz")
    baz_add = next(o for o in Store(a).all_ops() if "main.py::baz" in o.footprint)
    verbs.revert(a, baz_add.id)
    _push(a)

    sync.sync(b, remote="origin", branch="main")
    # baz's whole chain (add + B's extension) leaves the ideal: reverting the base removes its up-set.
    live = lens.current_ideal(b)
    assert not any("main.py::baz" in Store(b).get(oid).footprint for oid in live.op_ids)
    assert "def baz" not in (b / "main.py").read_text(encoding="utf-8")
