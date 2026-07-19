"""Stage-level tests for the `sgt.core.sync` package (plan U19, D4).

The 6 integration tests in `test_sync.py` are the behavior contract; these cover the decomposed
stages in isolation -- the pieces that reuse into `land`/`propose` later (U23/U24) -- plus the
`-X ours` litmus the plan calls for: after a clean sync the merged tree must be *exactly*
`code(merged_ideal, all_ops)`, proving no textual git merge contributed to it.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from sgt.core import lens, order, sync
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


def test_resolve_advances_the_fork_free_part_and_surfaces_the_fork(tmp_path):
    """Under divergence-as-state (U20/C4) a fork no longer short-circuits `resolve`: it returns the
    fork triples *and* a fork-free merged ideal with both tips excluded (here the whole union, so
    the ideal is empty). Still pure -- no disk write -- so the fork path needs no rollback, and the
    excluded tips never enter a verb-visible ideal (`order.is_valid_ideal` holds on the remainder,
    proven directly in `test_sync_hardening.py`)."""
    ours = make_op({"a.py::foo": (None, "v1")}, {"a.py::foo": b"1"})
    theirs = make_op({"a.py::foo": (None, "v2")}, {"a.py::foo": b"2"})  # same (sym, before) -> fork
    all_ops = [ours, theirs]
    ing = Ingested(
        ours_pins=Pins(), theirs_pins=Pins(),
        ours_declared_orset=lens.DeclaredORSet(), theirs_declared_orset=lens.DeclaredORSet(),
        ours_aliases=frozenset(), theirs_aliases=frozenset(),
        ours_tree=None,
        ours_ideal=Ideal.from_ops({ours.id}, all_ops),
        theirs_ideal_ids=frozenset({theirs.id}),
        all_ops=all_ops, theirs_ops=[theirs], mined_ops=[], ops_added=1,
    )

    res = resolve(tmp_path, ing)

    assert len(res.forks) == 1
    sym, _a, _b = res.forks[0]
    assert sym == "a.py::foo"
    assert res.merged_ideal is not None
    assert ours.id not in res.merged_ideal.op_ids  # both forked tips excluded from the ideal...
    assert theirs.id not in res.merged_ideal.op_ids
    assert res.merged_ideal.op_ids == frozenset()  # ...leaving nothing else in this fixture


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
    ing = ingest(Path(b), gb, fetched.theirs_sha, fetched.ours_sha)
    after_ids = {op.id for op in Store(b).all_ops()}

    assert after_ids == before_ids  # ingest wrote no op file to disk
    union_ids = {op.id for op in ing.all_ops}
    assert union_ids > before_ids  # theirs' baz op(s) are present in the in-memory union
    assert ing.ops_added == len(union_ids - before_ids) > 0


def test_resolve_reports_a_declared_cycle_but_still_produces_an_ideal(tmp_path):
    """`resolve` drops the cyclic declared edges from the ideal it folds, reports them, and keeps
    the full union (cycles included) in the declared OR-Set so the retraction target still travels."""
    a, b = _two_clones(tmp_path, _BASE)
    _edit_and_commit(a, "main.py", "def foo():\n    return 1\n\n\ndef bar():\n    return 200\n", "A: bump bar")
    _push(a)

    gb = GitBinding(b)
    fetched = fetch(Path(b), gb, "origin", "main")
    ing = ingest(Path(b), gb, fetched.theirs_sha, fetched.ours_sha)
    foo_id = next(op.id for op in ing.all_ops if "main.py::foo" in op.footprint)
    bar_id = next(op.id for op in ing.all_ops if "main.py::bar" in op.footprint)
    ing = replace(
        ing,
        ours_declared_orset=lens.DeclaredORSet(adds=frozenset({(foo_id, bar_id, "t1")})),
        theirs_declared_orset=lens.DeclaredORSet(adds=frozenset({(bar_id, foo_id, "t2")})),  # -> cycle
    )

    res = resolve(Path(b), ing)

    assert not res.forks
    assert res.merged_ideal is not None
    assert len(res.declared_cycles) > 0
    assert res.declared_orset.live() == frozenset({(foo_id, bar_id), (bar_id, foo_id)})


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


# -- U7: base recovery + trailer/record witness-containment (R12) --------------------------------

import subprocess as _subprocess

from sgt.core.store import _serialize
from sgt.core.sync.ingest import recover_base
from tests.core.test_sync import _clone, _init_bare


def _squash(repo: Path, tip_sha: str, parent_sha: str, message: str) -> str:
    """A single commit with `tip_sha`'s tree, a plain (trailer-less) message, and `parent_sha` as
    parent -- exactly what GitHub's squash-merge produces (mirrors test_sync_hardening)."""
    tree = _subprocess.run(
        ["git", "-C", str(repo), "rev-parse", f"{tip_sha}^{{tree}}"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    return _subprocess.run(
        ["git", "-C", str(repo), "commit-tree", tree, "-p", parent_sha, "-m", message],
        check=True, capture_output=True, text=True,
    ).stdout.strip()


def test_u7_base_recovery_from_witnessed_trailers(tmp_path):
    """Scenario 1: the merge-base (an sgt-native `put` commit) carries witnessed `Sgt-Op:` trailers,
    so its *full* ideal is recovered -- both foo and bar, not just a divergent contribution."""
    a, b = _two_clones(tmp_path, _BASE)
    _edit_and_commit(a, "main.py", _BASE + "\n\ndef baz():\n    return 42\n", "A: add baz")
    _push(a)

    gb = GitBinding(b)
    fetched = fetch(Path(b), gb, "origin", "main")
    ing = ingest(Path(b), gb, fetched.theirs_sha, fetched.ours_sha)

    assert ing.base_recovery == "trailers"
    by_id = {op.id: op for op in ing.all_ops}
    base_syms = {sym for oid in ing.base_ideal_ids for sym in by_id[oid].footprint}
    assert {"main.py::foo", "main.py::bar"} <= base_syms  # the base's full ideal (both symbols)


def test_u7_base_recovery_rejects_a_stale_inherited_record_and_mines(tmp_path):
    """Scenario 2: a plain-git commit that inherited (never wrote) `.sgt/ideal.json` is not a
    witness of it, so the stale record is rejected and recovery falls through to a full-range mine."""
    remote = _init_bare(tmp_path)
    a = _clone(remote, tmp_path / "a")
    lens.init(a)
    _edit_and_commit(a, "main.py", _BASE, "init")  # witnessed ideal.json + trailers

    (a / "README.md").write_text("docs\n", encoding="utf-8")  # touches no .sgt path
    plain = GitBinding(a).commit_all("plain: docs only, no trailers")  # inherits stale ideal.json

    ids, method = recover_base(Path(a), GitBinding(a), plain)
    assert method == "mined"  # the inherited record was not trusted; full-range mine instead
    syms = {sym for op in Store(a).all_ops() if op.id in ids for sym in op.footprint}
    assert {"main.py::foo", "main.py::bar"} <= syms  # still a full ideal, recovered by mining


def test_u7_squash_tip_recovers_via_witnessed_record(tmp_path):
    """Scenario 3: a squash-merge destroys theirs' tip trailers but the committed `.sgt/ideal.json`
    survives and the tip witnesses it -- recovered via the record (existing C5 behavior preserved)."""
    remote = _init_bare(tmp_path)
    a = _clone(remote, tmp_path / "a")
    lens.init(a)
    _edit_and_commit(a, "main.py", _BASE, "init")
    _push(a)
    base_sha = GitBinding(a).head()
    b = _clone(remote, tmp_path / "b")
    lens.get(b)

    _edit_and_commit(a, "main.py", _BASE + "\n\ndef baz():\n    return 42\n", "A: add baz")
    squash = _squash(a, GitBinding(a).head(), base_sha, "Squash merge (no trailers)")
    _subprocess.run(["git", "-C", str(a), "push", "-q", "-f", "origin", f"{squash}:main"],
                    check=True, capture_output=True)

    report = sync.sync(b, remote="origin", branch="main")
    assert report.theirs_recovery == "ideal-record"
    assert "def baz" in (b / "main.py").read_text(encoding="utf-8")  # fine ideal, not a coarse re-mine


def test_u7_disjoint_base_recovers_none_and_warns(tmp_path):
    """Scenario 4: no witnessed merge-base (disjoint histories) -> `base_recovery: none`, a loud
    warning, and union semantics. `recover_base(None)` is the ∅ case directly."""
    assert recover_base(tmp_path, GitBinding(tmp_path), None) == (frozenset(), "none")

    remote = _init_bare(tmp_path)
    a = _clone(remote, tmp_path / "a")
    lens.init(a)
    _edit_and_commit(a, "main.py", _BASE, "init")
    _push(a)
    b = _clone(remote, tmp_path / "b")
    lens.get(b)

    # A publishes an unrelated orphan branch -- no common ancestor with b's main.
    _subprocess.run(["git", "-C", str(a), "checkout", "-q", "--orphan", "feature"],
                    check=True, capture_output=True)
    _subprocess.run(["git", "-C", str(a), "rm", "-rfq", "."], check=True, capture_output=True)
    (a / "other.py").write_text("def unrelated():\n    return 0\n", encoding="utf-8")
    GitBinding(a).commit_all("feature: orphan root")
    _subprocess.run(["git", "-C", str(a), "push", "-q", "origin", "feature"],
                    check=True, capture_output=True)

    report = sync.sync(b, remote="origin", branch="feature")
    assert report.base_recovery == "none"
    assert "base recovery: none" in report.message  # loud, not silent


def test_u7_tip_with_new_ops_but_no_trailers_is_a_detected_footgun(tmp_path):
    """Scenario 5: theirs' tip carries a *new* `.sgt/ops` blob (it ran sgt) but no trailers and no
    witnessed record -- a detected footgun. Recovery degrades to ∅ with a named remedy rather than
    mis-mining a coarse squash."""
    a, b = _two_clones(tmp_path, _BASE)
    new = make_op({"main.py::foo": ("v1", "v2")}, {"main.py::foo": b"x"}, kind="rework")
    (a / ".sgt" / "ops" / new.id).write_bytes(_serialize(new))  # a raw op blob, no trailers/record
    GitBinding(a).commit_all("A: raw op blob, no trailers")
    _push(a)

    report = sync.sync(b, remote="origin", branch="main")
    assert report.theirs_recovery == "none"
    assert "no witnessed trailers/record" in report.message  # named remedy


def test_u7_forged_trailers_are_rejected(tmp_path):
    """Scenario 6: a `Sgt-Op:` trailer naming an id no `.sgt/ops` blob backs is forged -- not
    witnessed by the tip's tree -- so it is not trusted and recovery falls through to mining."""
    a, b = _two_clones(tmp_path, _BASE)
    forged = "f" * 64
    (a / "main.py").write_text(_BASE.replace("return 1", "return 9"), encoding="utf-8")
    GitBinding(a).commit_all(f"A: edit\n\nSgt-Op: {forged}")  # trailer with no backing blob
    _push(a)

    gb = GitBinding(b)
    fetched = fetch(Path(b), gb, "origin", "main")
    ing = ingest(Path(b), gb, fetched.theirs_sha, fetched.ours_sha)

    assert ing.theirs_recovery == "mined"  # forged trailer rejected -> fell through to mine
    assert forged not in ing.theirs_ideal_ids  # the forged id never enters theirs' ideal


# -- D1: the land log as a rung-0 base/tip recovery method ---------------------------------------

from sgt.core.sync import log as _log


def test_base_recovery_via_the_land_log(tmp_path):
    """A base sha that a prior `sgt land` recorded in the D1 log recovers as `"log"` -- checked
    before trailers, since it survives a squash the same way a witnessed record does but without
    depending on `.sgt/ideal.json` staying untouched by later commits."""
    remote = _init_bare(tmp_path)
    a = _clone(remote, tmp_path / "a")
    lens.init(a)
    from sgt import state
    state.save_json(a, "oracle_config", {"tiers": [{"name": "gate", "command": "exit 0"}]})
    _edit_and_commit(a, "main.py", _BASE, "init")
    _push(a)

    report = sync.land(a, branch="main")  # self-union land: gates green, logs an entry
    assert report.landed
    base_sha = report.land_sha

    ids, method = recover_base(Path(a), GitBinding(a), base_sha, branch="main")
    assert method == "log"
    assert ids == _log.ideal_for_sha(GitBinding(a), "main", base_sha)


def test_land_log_recovery_is_rejected_when_unwitnessed_and_falls_through(tmp_path):
    """A forged log entry -- naming op ids the sha's tree never produced -- is not trusted, exactly
    like a forged trailer (R12): recovery falls through to the next rung rather than trusting it."""
    a, b = _two_clones(tmp_path, _BASE)
    _edit_and_commit(a, "main.py", _BASE + "\n\ndef baz():\n    return 42\n", "A: add baz")
    _push(a)

    gb = GitBinding(b)
    fetched = fetch(Path(b), gb, "origin", "main")
    base_sha = gb.merge_base(fetched.ours_sha, fetched.theirs_sha)
    _log.append(gb, "main", base_sha, frozenset({"f" * 64}))  # forged: no backing .sgt/ops blob

    ids, method = recover_base(Path(b), gb, base_sha, branch="main")
    assert method == "trailers"  # the forged log entry was rejected, fell through to trailers


def test_land_log_ref_transports_across_clones_and_recovers_the_base(tmp_path):
    """The log ref is best-effort transport, not just a local artifact: pushed alongside the
    branch (`sgt push`), it's there for a *different* clone's base recovery after a plain fetch."""
    from sgt import state
    from sgt.cli.sync import _push as cli_push

    remote = _init_bare(tmp_path)
    a = _clone(remote, tmp_path / "a")
    lens.init(a)
    state.save_json(a, "oracle_config", {"tiers": [{"name": "gate", "command": "exit 0"}]})
    _edit_and_commit(a, "main.py", _BASE, "init")
    _push(a)

    report = sync.land(a, branch="main")
    assert report.landed
    base_sha = report.land_sha
    assert cli_push(str(a), "origin", "main", False) == 0  # pushes refs/heads/main + the log ref

    b = _clone(remote, tmp_path / "b")
    lens.get(b)
    gb_b = GitBinding(b)
    fetch(Path(b), gb_b, "origin", "main")
    assert gb_b.rev_parse(_log.log_ref("main")) is not None  # the log ref transported

    ids, method = recover_base(Path(b), gb_b, base_sha, branch="main")
    assert method == "log"


# -- U8: three-way resolve -- fork protection and base=∅ equivalence -----------------------------

def _resolve_ingested(tmp_path, all_ops, ours_ids, theirs_ids, base_ids, theirs_recovery="trailers"):
    """A minimal `Ingested` for driving `resolve` directly over hand-built op sets (U8). Defaults
    `theirs_recovery` to "trailers" so `theirs_ids` is treated as theirs' *full* ideal and a
    revert there (an op absent from it) drives the three-way subtraction."""
    return Ingested(
        ours_pins=Pins(), theirs_pins=Pins(),
        ours_declared_orset=lens.DeclaredORSet(), theirs_declared_orset=lens.DeclaredORSet(),
        ours_aliases=frozenset(), theirs_aliases=frozenset(),
        ours_tree=None,
        ours_ideal=Ideal.from_ops(ours_ids, all_ops),
        theirs_ideal_ids=frozenset(theirs_ids),
        all_ops=all_ops, theirs_ops=[], mined_ops=[], ops_added=0,
        base_ideal_ids=frozenset(base_ids), theirs_recovery=theirs_recovery,
    )


def test_u8_fork_tips_survive_base_subtraction(tmp_path):
    """Scenario 4: a fork whose one tip the base witnessed. Three-way subtraction would sweep that
    tip into a removal (theirs lacks it), silently resolving the fork by deletion -- fork protection
    keeps both tips, so the fork is *surfaced* and both tips survive; the fold parks them at the
    common ancestor exactly as divergence-as-state requires."""
    a0 = make_op({"foo": (None, "v1")}, {"foo": b"1"})
    ta = make_op({"foo": ("v1", "va")}, {"foo": b"a"})
    tb = make_op({"foo": ("v1", "vb")}, {"foo": b"b"})  # forks ta at (foo, v1)
    all_ops = [a0, ta, tb]

    ing = _resolve_ingested(
        tmp_path, all_ops,
        ours_ids={a0.id, ta.id}, theirs_ids={a0.id, tb.id}, base_ids={a0.id, ta.id},
    )
    res = resolve(tmp_path, ing)

    assert len(res.forks) == 1  # the fork survives subtraction and is surfaced, not deleted
    assert res.merged_ideal.op_ids == frozenset({a0.id})  # both tips parked at the ancestor


def test_u8_empty_base_reproduces_the_plain_union(tmp_path):
    """Scenario 5: `base == ∅` (base_recovery "none") makes `removed_seed` empty, so no removals --
    the merged ideal is exactly today's grounded, fork-free union over the same ops."""
    a0 = make_op({"foo": (None, "v1")}, {"foo": b"1"})
    m1 = make_op({"foo": ("v1", "v2")}, {"foo": b"2"})   # ours extends foo
    n1 = make_op({"bar": (None, "w1")}, {"bar": b"9"})   # theirs adds bar (disjoint)
    all_ops = [a0, m1, n1]

    ing = _resolve_ingested(
        tmp_path, all_ops,
        ours_ids={a0.id, m1.id}, theirs_ids={a0.id, n1.id}, base_ids=set(),
    )
    res = resolve(tmp_path, ing)

    union = {a0.id, m1.id, n1.id}
    expected = order.reduce_to_ideal(union, all_ops)  # today's semantics on a grounded union
    assert res.merged_ideal.op_ids == expected == frozenset(union)  # full disjoint union, no loss
