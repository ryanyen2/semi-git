"""Stage-level tests for the `sgt.core.sync` package (plan U19, D4).

The 6 integration tests in `test_sync.py` are the behavior contract; these cover the decomposed
stages in isolation -- the pieces that reuse into `land`/`propose` later (U23/U24) -- plus the
`-X ours` litmus the plan calls for: after a clean sync the merged tree must be *exactly*
`code(merged_ideal, all_ops)`, proving no textual git merge contributed to it.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from sgt.core import lens, sync
from sgt.core.fold import code
from sgt.core.ideal import Ideal
from sgt.core.op import make_op
from sgt.core.store import Store
from sgt.core.sync.fetch import fetch
from sgt.core.sync.ingest import Ingested, ingest
from sgt.core.sync.resolve import resolve
from sgt.lens.pins import Pins
from sgt.store.gitbind import GitBinding

from tests.core.test_sync import _BASE, _edit_and_commit, _push, _two_clones


def test_resolve_surfaces_a_fork_before_building_any_merged_state():
    """A same-symbol fork short-circuits `resolve` -- it returns the triples with no merged ideal,
    pins, or tree, so nothing downstream (the disk-writing `materialize`) is ever reached. This is
    the isolation that makes the fork path need no rollback: `resolve` is pure."""
    ours = make_op({"a.py::foo": (None, "v1")}, {"a.py::foo": b"1"})
    theirs = make_op({"a.py::foo": (None, "v2")}, {"a.py::foo": b"2"})  # same (sym, before) -> fork
    all_ops = [ours, theirs]
    ing = Ingested(
        ours_pins=Pins(), theirs_pins=Pins(),
        ours_declared=frozenset(), theirs_declared=frozenset(),
        ours_tree=None,
        ours_ideal=Ideal.from_ops({ours.id}, all_ops),
        theirs_ideal_ids=frozenset({theirs.id}),
        all_ops=all_ops, theirs_ops=[theirs], ops_added=1,
    )

    res = resolve(Path("/nonexistent"), ing)  # repo unused: fork returns before any repo read

    assert len(res.forks) == 1
    sym, _a, _b = res.forks[0]
    assert sym == "a.py::foo"
    assert res.merged_ideal is None
    assert res.unioned_pins is None
    assert res.tree_result is None


def test_ingest_unions_the_op_store_in_memory_without_touching_disk(tmp_path):
    """The rollback trap defused at the stage level: `ingest` reads theirs' op blobs and unions
    them in memory only -- the on-disk store is untouched -- so a fork discovered by `resolve`
    right after leaves nothing to undo. Persisting for real is `materialize`'s job alone."""
    a, b = _two_clones(tmp_path, _BASE)
    baz = _BASE + "\n\ndef baz():\n    return 42\n"
    _edit_and_commit(a, "main.py", baz, "A: add baz")
    _push(a)

    gb = GitBinding(b)
    fetched = fetch(Path(b), gb, "origin", "main")
    assert not fetched.up_to_date

    before_ids = {op.id for op in Store(b).all_ops()}
    ing = ingest(Path(b), gb, fetched.theirs_sha)
    after_ids = {op.id for op in Store(b).all_ops()}

    assert after_ids == before_ids  # ingest wrote no op file to disk
    union_ids = {op.id for op in ing.all_ops}
    assert union_ids > before_ids  # theirs' baz op(s) are present in the in-memory union
    assert ing.ops_added == len(union_ids - before_ids) > 0


def test_resolve_reports_a_declared_cycle_but_still_produces_an_ideal(tmp_path):
    """`resolve` drops the cyclic declared edges from the ideal it folds, reports them, and keeps
    the full union (cycles included) in `declared` so the retraction target still travels."""
    a, b = _two_clones(tmp_path, _BASE)
    _edit_and_commit(a, "main.py", "def foo():\n    return 1\n\n\ndef bar():\n    return 200\n", "A: bump bar")
    _push(a)

    gb = GitBinding(b)
    fetched = fetch(Path(b), gb, "origin", "main")
    ing = ingest(Path(b), gb, fetched.theirs_sha)
    foo_id = next(op.id for op in ing.all_ops if "main.py::foo" in op.footprint)
    bar_id = next(op.id for op in ing.all_ops if "main.py::bar" in op.footprint)
    ing = replace(
        ing,
        ours_declared=frozenset({(foo_id, bar_id)}),
        theirs_declared=frozenset({(bar_id, foo_id)}),  # unioned -> a cycle
    )

    res = resolve(Path(b), ing)

    assert not res.forks
    assert res.merged_ideal is not None
    assert len(res.declared_cycles) > 0
    assert res.declared == frozenset({(foo_id, bar_id), (bar_id, foo_id)})


def test_sync_materializes_exactly_the_ideal_algebra(tmp_path):
    """The `-X ours` litmus (plan U19): the merged working tree is independently reproducible as
    `code(merged_ideal, all_ops)` -- byte-for-byte, with zero contribution from any git textual
    merge, because none runs -- and the merge commit is a real 2-parent commit."""
    a, b = _two_clones(tmp_path, _BASE)
    _edit_and_commit(a, "main.py", "def foo():\n    return 100\n\n\ndef bar():\n    return 2\n", "A: bump foo")
    _push(a)
    _edit_and_commit(b, "main.py", "def foo():\n    return 1\n\n\ndef bar():\n    return 200\n", "B: bump bar")

    report = sync.sync(b, remote="origin", branch="main")
    assert report.merged

    merged_ideal = lens.current_ideal(b)
    all_ops = Store(b).all_ops()
    expected = code(merged_ideal, all_ops)
    on_disk = (b / "main.py").read_bytes()
    assert expected["main.py"] == on_disk
    assert b"return 100" in on_disk and b"return 200" in on_disk

    gb = GitBinding(b)
    parents = gb._git("rev-list", "--parents", "-n", "1", report.merge_sha).stdout.split()
    assert len(parents) == 3  # the merge commit itself + its two parents (ours, theirs)
