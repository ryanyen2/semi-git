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


def test_sync_surfaces_a_same_symbol_fork_without_committing(tmp_path):
    a, b = _two_clones(tmp_path, _BASE)

    _edit_and_commit(a, "main.py", "def foo():\n    return 999\n\n\ndef bar():\n    return 2\n", "A: rework foo")
    _push(a)
    _edit_and_commit(b, "main.py", "def foo():\n    return 42\n\n\ndef bar():\n    return 2\n", "B: rework foo")

    gb = GitBinding(b)
    before_head = gb.head()

    report = sync.sync(b, remote="origin", branch="main")

    assert not report.merged
    assert "fork" in report.message
    assert "merge-op" in report.message
    assert len(report.forks) == 1
    symbol, _tip_a, _tip_b = report.forks[0]
    assert symbol == "main.py::foo"

    assert gb.is_clean()  # merge was aborted, not left half-applied
    assert gb.head() == before_head
    assert (b / "main.py").read_text(encoding="utf-8") == "def foo():\n    return 42\n\n\ndef bar():\n    return 2\n"


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
