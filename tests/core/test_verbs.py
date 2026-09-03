"""Tests for sgt.core.verbs -- ideal-edit verbs (plan U8, R5/R14-surfacing/R20).

Each verb is an exact set edit of the current ref's ideal, previewable with no I/O and validated
through `Ideal.from_ops` so an invalid (forked) result is refused, never committed. The property
test at the bottom is U8's verification surface: every verb output that succeeds is a valid ideal,
including over the `revert_to_original` after-value collision that the collision-safe `upset_in`/
`downset_in` exist to handle.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings, strategies as st

from sgt.core import order
from sgt.core import session as session_mod
from sgt.core import verbs
from sgt.core.fold import code
from sgt.core.lens import get
from sgt.core.op import make_op
from sgt.core.order import is_valid_ideal
from sgt.core.store import Store
from sgt.store.gitbind import init_store
from tests.core.test_session import _seed_repo, _write_and_commit
from tests.laws import corpus


def _foo_chain(repo_path, n: int):
    """A repo whose a.py::foo is a linear chain of `n` versions (return 1..n), one per commit."""
    gb, _ = init_store(repo_path)
    for i in range(1, n + 1):
        (repo_path / "a.py").write_text(f"def foo():\n    return {i}\n", encoding="utf-8")
        gb.commit_all(f"foo v{i}")
    return repo_path


def _op_with(ops, sym: str, needle: bytes):
    """The op whose after-image for `sym` contains `needle` (a stable way to name a chain step)."""
    return next(
        o for o in ops
        if sym in o.footprint and o.images.get(sym) is not None and needle in o.images[sym]
    )


def test_revert_take_dependents_removes_exactly_the_upset(tmp_path):
    """With `take_dependents=True` (explicit, never the default), revert of a mid-chain op
    removes that op and every op that builds on it (`↑X`), the preview names exactly what was
    removed, and the fold reverts to the pre-op bytes -- the pre-2026-08-09 behavior."""
    repo = _foo_chain(tmp_path / "repo", 3)
    get(repo)
    ops = Store(repo).all_ops()
    mid = _op_with(ops, "a.py::foo", b"return 2")  # add(1) -> mid(2) -> tip(3)
    tip = _op_with(ops, "a.py::foo", b"return 3")

    preview = verbs.revert(repo, mid.id, emit=True, take_dependents=True)
    assert preview.ok
    assert preview.removed == {mid.id, tip.id}  # exactly ↑mid
    assert "a.py::foo" in preview.affected_symbols

    verbs.revert(repo, mid.id, take_dependents=True)  # apply
    materialized = code(get(repo), Store(repo).all_ops())
    assert materialized["a.py"] == b"def foo():\n    return 1\n"


def test_default_revert_of_overlapping_midchain_keeps_later_work_and_reports(tmp_path):
    """The safe default never demolishes: subtracting a mid-chain op whose lines the tip also
    rewrote is a conflict, so the symbol is KEPT byte-identical and reported for a manual edit --
    later work survives, nothing is silently dropped."""
    repo = _foo_chain(tmp_path / "repo", 3)
    get(repo)
    ops = Store(repo).all_ops()
    mid = _op_with(ops, "a.py::foo", b"return 2")

    preview = verbs.revert(repo, mid.id, emit=True)
    assert preview.ok
    assert "a.py::foo" in preview.kept_conflicts
    assert not preview.removed  # the mid op stays in history; nothing is excluded

    verbs.revert(repo, mid.id)  # apply -- a no-op edit plus the report
    materialized = code(get(repo), Store(repo).all_ops())
    assert materialized["a.py"] == b"def foo():\n    return 3\n"  # later work intact


def test_restore_is_reverts_inverse_on_a_tip(tmp_path):
    """restore(X) undoes revert(X) for a tip op: revert then restore returns the original ideal."""
    repo = _foo_chain(tmp_path / "repo", 3)
    original = get(repo).op_ids
    ops = Store(repo).all_ops()
    tip = _op_with(ops, "a.py::foo", b"return 3")

    verbs.revert(repo, tip.id)
    assert tip.id not in get(repo).op_ids

    verbs.restore(repo, tip.id)
    assert get(repo).op_ids == original


def test_restore_resolves_a_superseded_ghost_and_validation_gates_reentry(tmp_path):
    """The revert -> save -> restore triangle: after reverting v2 and committing a sibling v3,
    restoring v2 by the id the revert printed is *refused as a fork* while v3 is live (one live
    version per symbol), and *succeeds* once v3 is reverted -- the swap. Pins that plan_restore
    resolves a ghost against the whole store (the fork-reduced HEAD ideal parks both siblings,
    so the old source-only resolution could never see v2 again), while `Ideal.from_ops`
    validation still decides what may re-enter."""
    from sgt.store.gitbind import GitBinding

    repo = _foo_chain(tmp_path / "repo", 2)
    get(repo)
    ops = Store(repo).all_ops()
    v2 = _op_with(ops, "a.py::foo", b"return 2")

    verbs.revert(repo, v2.id)  # foo back to v1
    (repo / "a.py").write_text("def foo():\n    return 33\n", encoding="utf-8")
    GitBinding(repo).commit_all("foo v3, a sibling of the reverted v2")
    get(repo)  # mine v3
    v3 = _op_with(Store(repo).all_ops(), "a.py::foo", b"return 33")

    blocked = verbs.plan_restore(repo, v2.id)
    assert not blocked.ok and blocked.forked  # sibling live -> refusal, not a silent no-op

    verbs.revert(repo, v3.id)  # the swap's first half
    swapped = verbs.restore(repo, v2.id)
    assert swapped.ok and v2.id in swapped.added
    materialized = code(get(repo), Store(repo).all_ops())
    assert materialized["a.py"] == b"def foo():\n    return 2\n"


def test_restore_by_symbol_resolves_a_ghost_with_no_live_frontier_tip(tmp_path):
    """F2: `restore <file::symbol>` when the symbol has no live frontier tip in the reduced source
    ideal (here a genuine birth-fork from a merge -- `reduce_to_ideal` drops both births, so
    `resolve_target`'s live-frontier path can't see it) must still bring the symbol back over the
    whole store, not fall through to the NL rung's "set OPENAI_API_KEY". `_validated` keeps the
    result legal. README flagship inverse."""
    from sgt.store.gitbind import init_store

    repo = tmp_path / "repo"
    gb, _ = init_store(repo)
    (repo / "base.py").write_text("x = 1\n", encoding="utf-8")
    gb.commit_all("base, no foo yet")
    main = gb.symbolic_ref().rsplit("/", 1)[-1]
    gb._git("checkout", "-q", "-b", "feature")
    (repo / "a.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    gb.commit_all("feature adds foo")
    gb._git("checkout", "-q", main)
    (repo / "a.py").write_text("def foo():\n    return 2\n", encoding="utf-8")
    gb.commit_all("main adds foo")
    gb._git("merge", "-q", "--no-edit", "-X", "ours", "feature")
    get(repo)

    ops = Store(repo).all_ops()
    # Precondition (the F2 root cause): the birth-fork leaves a.py::foo with no live frontier tip,
    # so plain symbol resolution (verbs.py:72) errs and the pre-fix `::` guard refused to widen.
    assert "a.py::foo" not in order.frontier(get(repo).op_ids, ops)

    preview = verbs.plan_restore(repo, "a.py::foo")
    assert preview.ok and preview.added  # widened to the whole store; a ghost tip resolved

    verbs.restore(repo, "a.py::foo")  # apply
    materialized = code(get(repo), Store(repo).all_ops())
    # Byte-correct: the symbol is back, as one of its two committed versions (never a fork).
    assert materialized["a.py"] in (b"def foo():\n    return 1\n", b"def foo():\n    return 2\n")


def test_cherry_pick_of_an_independent_op_splices_cleanly(tmp_path):
    """cherry-pick `↓X` from a branch whose X shares the base but adds an independent symbol
    splices into the other branch's ideal without forking."""
    repo = tmp_path / "repo"
    gb, _ = init_store(repo)
    (repo / "a.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    gb.commit_all("base foo")
    base_branch = gb.symbolic_ref().rsplit("/", 1)[-1]

    gb._git("checkout", "-q", "-b", "feature")
    (repo / "a.py").write_text("def foo():\n    return 1\n\n\ndef bar():\n    return 2\n", encoding="utf-8")
    gb.commit_all("feature: add independent bar")
    get(repo)  # mine feature
    bar_op = _op_with(Store(repo).all_ops(), "a.py::bar", b"return 2")

    gb._git("checkout", "-q", base_branch)
    main_ideal = get(repo)
    assert bar_op.id not in main_ideal.op_ids

    preview = verbs.cherry_pick(repo, bar_op.id, "feature", emit=True)
    assert preview.ok and not preview.forked
    assert bar_op.id in preview.added

    verbs.cherry_pick(repo, bar_op.id, "feature")  # apply
    materialized = code(get(repo), Store(repo).all_ops())
    assert b"def foo" in materialized["a.py"] and b"def bar" in materialized["a.py"]


def test_cherry_pick_into_a_diverged_chain_surfaces_the_fork_and_refuses(tmp_path):
    """AE2: cherry-picking one branch's diverged edit of a symbol into the other branch's ideal
    would fork that symbol's chain -- the verb surfaces it (`forked`) and refuses to commit."""
    repo = corpus.CORPUS["diverged_chain"].build(tmp_path / "repo")
    corpus.checkout(repo, "release")
    release_ideal = get(repo)
    corpus.checkout(repo, "main")
    get(repo)
    ops = Store(repo).all_ops()
    release_tip = release_ideal.frontier(ops)["slugify.py::slugify"]

    head_before = corpus.commit_shas(repo)[-1]
    preview = verbs.cherry_pick(repo, release_tip, "release", emit=True)
    assert not preview.ok
    assert preview.forked
    assert preview.after_ids == preview.before_ids  # nothing changed

    # A wrapper call (non-emit) on a refused plan also commits nothing; apply() raises.
    verbs.cherry_pick(repo, release_tip, "release")
    assert corpus.commit_shas(repo)[-1] == head_before
    import pytest
    with pytest.raises(verbs.VerbError):
        verbs.apply(repo, preview)


def test_pin_to_an_older_version_removes_the_induced_upset(tmp_path):
    """pin a symbol to an older version: the preview shows the induced up-set removal (the ops
    after that version), and the fold reverts to the pinned bytes."""
    repo = _foo_chain(tmp_path / "repo", 3)
    get(repo)
    ops = Store(repo).all_ops()
    add = _op_with(ops, "a.py::foo", b"return 1")
    mid = _op_with(ops, "a.py::foo", b"return 2")
    tip = _op_with(ops, "a.py::foo", b"return 3")
    v1 = add.footprint["a.py::foo"][1]  # the "return 1" version

    preview = verbs.pin(repo, "a.py::foo", v1, emit=True)
    assert preview.ok
    assert preview.removed == {mid.id, tip.id}  # everything after v1

    verbs.pin(repo, "a.py::foo", v1)  # apply
    assert code(get(repo), Store(repo).all_ops())["a.py"] == b"def foo():\n    return 1\n"


def test_after_edge_changes_a_subsequent_reverts_closure(tmp_path):
    """`after(a, b)` records a declared edge a ≤ b; a later revert(a) then also removes b, which
    it would not without the edge (the two symbols are otherwise independent)."""
    repo = tmp_path / "repo"
    gb, _ = init_store(repo)
    (repo / "a.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    gb.commit_all("add foo")
    (repo / "a.py").write_text("def foo():\n    return 1\n\n\ndef bar():\n    return 2\n", encoding="utf-8")
    gb.commit_all("add independent bar")
    get(repo)
    ops = Store(repo).all_ops()
    foo_op = _op_with(ops, "a.py::foo", b"return 1")
    bar_op = _op_with(ops, "a.py::bar", b"return 2")

    # Control: without a declared edge, reverting foo leaves bar untouched.
    assert bar_op.id not in verbs.plan_revert(repo, foo_op.id).removed

    verbs.after(repo, foo_op.id, bar_op.id)  # declare foo ≤ bar
    assert bar_op.id in verbs.plan_revert(repo, foo_op.id).removed  # closure now pulls bar out


def test_emit_and_plan_are_side_effect_free(tmp_path):
    """`--emit`/`plan_*` write nothing: the store, the persisted ideal, and the working tree are
    byte-identical before and after previewing an edit."""
    repo = corpus.CORPUS["linear_history"].build(tmp_path / "repo")
    get(repo)
    ops_before = len(Store(repo).all_ops())
    ideal_json = (repo / ".sgt" / "local" / "ideal.json").read_text()
    tree = {p: (repo / p).read_bytes() for p in corpus.tracked_paths(repo)}
    tip = get(repo).frontier(Store(repo).all_ops())["c.py::qux"]

    verbs.plan_revert(repo, tip)
    verbs.revert(repo, tip, emit=True)

    assert len(Store(repo).all_ops()) == ops_before
    assert (repo / ".sgt" / "local" / "ideal.json").read_text() == ideal_json
    assert {p: (repo / p).read_bytes() for p in corpus.tracked_paths(repo)} == tree


def test_apply_then_get_persists_the_edit_without_a_phantom_inverse(tmp_path):
    """After apply, a re-get returns exactly the edited ideal -- the reverted op stays gone and
    no fresh inverse op is mined from the materializing commit (the U7.5 record_ideal path)."""
    repo = corpus.CORPUS["linear_history"].build(tmp_path / "repo")
    before = get(repo).op_ids
    tip = get(repo).frontier(Store(repo).all_ops())["c.py::qux"]

    preview = verbs.revert(repo, tip)  # apply
    after = get(repo).op_ids
    assert after == before - preview.removed  # exactly the edit, nothing resurrected or inverted
    assert is_valid_ideal(Store(repo).all_ops(), after)


def test_plan_revert_session_removes_exactly_the_landed_session_ops(tmp_path):
    """Addressing by provenance (plan U31, S7): `revert --session <name>` resolves a session name
    to the op-set it landed via structured attribution (`ops_by_session`), not the session record
    (which `land` already dropped), then removes exactly that op-set's up-set closure."""
    _seed_repo(tmp_path)
    session = session_mod.start(tmp_path, "s1")
    _write_and_commit(Path(session.scratch), "b.py", "def bar():\n    return 5\n")
    session_mod.land(tmp_path, "s1")
    get(tmp_path)  # absorb the landing commit into the main repo's store

    session_ops = session_mod.ops_by_session(tmp_path, "s1")
    assert session_ops  # the landed op still carries the attribution after the record is gone
    assert session_mod.list_sessions(tmp_path) == ()  # the session record itself is gone

    preview = verbs.plan_revert_session(tmp_path, "s1")
    assert preview.ok
    assert preview.removed == session_ops

    verbs.apply(tmp_path, preview)
    materialized = code(get(tmp_path), Store(tmp_path).all_ops())
    assert "b.py" not in materialized


def test_plan_revert_session_refuses_an_unknown_session_name(tmp_path):
    _seed_repo(tmp_path)
    preview = verbs.plan_revert_session(tmp_path, "nope")
    assert not preview.ok
    assert "no op carries session" in preview.message
    assert preview.after_ids == preview.before_ids


def test_plan_revert_session_reports_no_change_once_already_reverted(tmp_path):
    _seed_repo(tmp_path)
    session = session_mod.start(tmp_path, "s1")
    _write_and_commit(Path(session.scratch), "b.py", "def bar():\n    return 5\n")
    session_mod.land(tmp_path, "s1")
    get(tmp_path)

    verbs.plan_revert_session(tmp_path, "s1")
    verbs.apply(tmp_path, verbs.plan_revert_session(tmp_path, "s1"))  # reverted once

    preview = verbs.plan_revert_session(tmp_path, "s1")
    assert preview.ok
    assert preview.after_ids == preview.before_ids
    assert "no change" in preview.message


@settings(max_examples=20, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(st.data())
def test_every_verb_output_is_a_valid_ideal(tmp_path_factory, data):
    """R20 verb-validity law: for random targets across `linear_history` and the
    `revert_to_original` after-value collision, every verb that succeeds yields a valid ideal --
    never a silent downward-closure or fork-freedom violation. A refusal (`ok=False`) is fine."""
    case = data.draw(st.sampled_from(["linear_history", "revert_to_original"]))
    repo = corpus.CORPUS[case].build(tmp_path_factory.mktemp("repo"))
    ideal = get(repo)
    ops = Store(repo).all_ops()
    if not ideal.op_ids:
        return
    target = data.draw(st.sampled_from(sorted(ideal.op_ids)))
    verb = data.draw(st.sampled_from(["revert", "restore"]))

    preview = (verbs.plan_revert if verb == "revert" else verbs.plan_restore)(repo, target)
    if preview.ok:
        # `after_ids` may name ops the preview *mints* (splices, prunes) that `apply` will store,
        # so the law is over the composed set -- the same one `plan_revert` validates against.
        assert is_valid_ideal(ops + list(preview.new_ops), preview.after_ids)
    else:
        assert preview.after_ids == preview.before_ids  # a refusal leaves the ideal untouched


def test_redo_after_undo_saves_again(tmp_path):
    """`sgt undo` of a save excludes that save's ops; re-authoring the same content by hand is a
    new statement of intent, so the next save must lift the exclusion (tombstone its tags) instead
    of wedging every future save on put()'s byte-drift refusal (found 2026-08-09 building the
    study testbed: save -> undo -> redo -> save failed forever)."""
    from sgt.cli.porcelain import _undo, save
    from sgt.store.gitbind import init_store

    gb, _ = init_store(tmp_path)
    (tmp_path / "a.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    gb.commit_all("seed")

    body = "def foo():\n    return 1\n\ndef bar():\n    return 2\n"
    (tmp_path / "a.py").write_text(body, encoding="utf-8")
    first = save(str(tmp_path), message="add bar")
    assert first.get("saved")

    assert _undo(str(tmp_path), True) == 0
    assert b"bar" not in gb.blob_bytes("HEAD", "a.py")

    (tmp_path / "a.py").write_text(body, encoding="utf-8")  # the redo, byte-identical
    second = save(str(tmp_path), message="add bar again")
    assert second.get("saved"), f"redo save wedged: {second}"
    assert b"bar" in gb.blob_bytes("HEAD", "a.py")


def test_default_revert_subtracts_cleanly_and_keeps_interleaved_later_work(tmp_path):
    """The 2026-08-09 demolition scenario in miniature: feature F adds a symbol AND wires it
    into a shared symbol; a later feature reworks the same shared symbol. Default revert of F
    prunes F's own symbol, splices F's wiring out of the shared symbol's tip, and leaves the
    later feature byte-identical -- nothing beyond F is removed."""
    from sgt.store.gitbind import init_store

    repo = tmp_path / "repo"
    gb, _ = init_store(repo)
    (repo / "util.py").write_text("def build():\n    a()\n    b()\n", encoding="utf-8")
    gb.commit_all("base")
    (repo / "util.py").write_text(
        "def build():\n    a()\n    wl()\n    b()\n\ndef wl():\n    return 1\n",
        encoding="utf-8")
    gb.commit_all("feature F: wl plus wiring")
    (repo / "util.py").write_text(
        "def build():\n    a()\n    wl()\n    b()\n    c()\n\ndef wl():\n    return 1\n"
        "\ndef c_helper():\n    return 2\n",
        encoding="utf-8")
    gb.commit_all("later feature: c")

    get(repo)
    ops = Store(repo).all_ops()
    target = next(o for o in ops
                  if "util.py::wl" in o.footprint and o.footprint["util.py::wl"][0] is None)

    preview = verbs.revert(repo, target.id, emit=True)
    assert preview.ok, preview.message
    assert "util.py::wl" in preview.pruned_symbols
    assert "util.py::build" in preview.subtracted_symbols
    assert not preview.kept_conflicts

    verbs.revert(repo, target.id)  # apply
    text = code(get(repo), Store(repo).all_ops())["util.py"].decode()
    assert "def wl" not in text
    assert "wl()" not in text
    assert "c()" in text and "def c_helper" in text and "b()" in text


def test_revert_regrounds_the_layout_facts_of_symbols_it_keeps():
    """The fold splices a file as `entity + its residue gap` in anchor order and synthesizes no
    separators of its own, so a kept entity whose layout facts died with the removal renders
    glued to its neighbour (`    passdef find_section(...)`, a SyntaxError) and, with no anchor,
    drops into the sorted end-of-file fallback. A removal that owns the save which first recorded
    a file's partition strips exactly those facts off symbols it never meant to touch, so the
    subtraction has to re-ground them.

    Drives `_repair_layout` directly: the frontiers below are the shape the tracer found on the
    study repo -- `a` and `c` still live, their residue and anchor chains gone with the removal,
    `b` removed from between them.
    """
    from sgt.core.subtract import _ANCHOR_FIRST, _repair_layout

    path = "m.py"

    def mk(sym: str, image: bytes):
        return make_op({sym: (None, f"v-{sym}")}, {sym: image})

    entities = {name: mk(f"{path}::{name}", f"def {name}():\n    return 0".encode())
                for name in ("a", "b", "c")}
    residues = {name: mk(f"{path}::__residue__::{name}", b"\n\n\n") for name in ("a", "b", "c")}
    anchors = {
        "a": mk(f"{path}::__anchor__::a", _ANCHOR_FIRST.encode()),
        "b": mk(f"{path}::__anchor__::b", b"a"),
        "c": mk(f"{path}::__anchor__::c", b"b"),
    }
    every = [*entities.values(), *residues.values(), *anchors.values()]
    by_id = {op.id: op for op in every}
    pre = {next(iter(op.footprint)): op.id for op in every}

    # After the removal: `b` is gone, `a` and `c` survive -- but the removal owned their layout
    # chains, so only the two entity symbols are left live.
    live_after = {f"{path}::a": entities["a"].id, f"{path}::c": entities["c"].id}

    repairs = _repair_layout(path, pre, live_after, lambda oid: by_id[oid].images, by_id, "F")
    emitted = {next(iter(op.footprint)): op.images[next(iter(op.footprint))] for op in repairs}

    # Both survivors get their recorded gap back -- verbatim, never invented.
    assert emitted[f"{path}::__residue__::a"] == b"\n\n\n"
    assert emitted[f"{path}::__residue__::c"] == b"\n\n\n"
    # `a` is still first; `c`'s anchor named the removed `b`, so it re-points to the nearest
    # surviving predecessor rather than falling into the fold's sorted fallback.
    assert emitted[f"{path}::__anchor__::a"] == _ANCHOR_FIRST.encode()
    assert emitted[f"{path}::__anchor__::c"] == b"a"
    # Nothing is re-grounded for the entity the removal actually took.
    assert not any(sym.endswith("::b") for sym in emitted)


def test_repair_layout_is_a_no_op_when_the_removal_left_the_partition_intact():
    """The repair only fires where facts are missing or point at removed code -- a removal that
    leaves a file's layout alone must mint no ops, or every revert would churn the residue chain
    and ungroundthe next save's mined rework."""
    from sgt.core.subtract import _ANCHOR_FIRST, _repair_layout

    path = "m.py"

    def mk(sym: str, image: bytes):
        return make_op({sym: (None, f"v-{sym}")}, {sym: image})

    every = {
        f"{path}::a": mk(f"{path}::a", b"def a():\n    return 0"),
        f"{path}::b": mk(f"{path}::b", b"def b():\n    return 1"),
        f"{path}::__residue__::a": mk(f"{path}::__residue__::a", b"\n\n\n"),
        f"{path}::__residue__::b": mk(f"{path}::__residue__::b", b"\n"),
        f"{path}::__anchor__::a": mk(f"{path}::__anchor__::a", _ANCHOR_FIRST.encode()),
        f"{path}::__anchor__::b": mk(f"{path}::__anchor__::b", b"a"),
    }
    by_id = {op.id: op for op in every.values()}
    frontier = {sym: op.id for sym, op in every.items()}

    assert _repair_layout(path, frontier, dict(frontier),
                          lambda oid: by_id[oid].images, by_id, "F") == []


def test_restore_picks_one_layout_head_when_repairs_forked_the_anchor_chain(tmp_path):
    """F39: `_repair_layout` mints an anchor repair with `before=None` (`subtract.py`'s `_emit`,
    kind `touched`) whenever a removal leaves that layout symbol no live tip, so an entity removed
    and reborn a few times owns *several* heads of one anchor chain in the store. That is legal
    there -- the store is a forest of versions, fork-freedom binds the ideal -- but `plan_restore`'s
    whole-store fallback rung (the one the test above exercises, for a symbol the reduced ideal
    parks) pulled *every* sibling layout op, so the union it handed to `_validated` forked the
    anchor chain and the restore was refused. The entity's bytes were in the store the whole time
    and compose identically under either head, so the refusal manufactured data loss: found by the
    WP-V4 sweep, where it left a file at 1 byte with no documented command able to bring it back.

    The birth-fork below is what puts resolution on the whole-store rung; the second anchor head is
    added by hand in the shape `_repair_layout` emits it (`intent="revert <tag>: keep <name>'s place
    in <path>"`), rather than driven through the multi-cycle revert sequence the sweep took to it.
    """
    from sgt.core.mine import _content_version, _positional_version
    from sgt.core.subtract import _ANCHOR_FIRST

    repo = tmp_path / "repo"
    gb, _ = init_store(repo)
    (repo / "base.py").write_text("x = 1\n", encoding="utf-8")
    gb.commit_all("base, no foo yet")
    main = gb.symbolic_ref().rsplit("/", 1)[-1]
    gb._git("checkout", "-q", "-b", "feature")
    (repo / "a.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    gb.commit_all("feature adds foo")
    gb._git("checkout", "-q", main)
    (repo / "a.py").write_text("def foo():\n    return 2\n", encoding="utf-8")
    gb.commit_all("main adds foo")
    gb._git("merge", "-q", "--no-edit", "-X", "ours", "feature")
    get(repo)

    ops = Store(repo).all_ops()
    assert "a.py::foo" not in order.frontier(get(repo).op_ids, ops)  # on the whole-store rung

    anchor = "a.py::__anchor__::foo"
    image = _ANCHOR_FIRST.encode()
    Store(repo).add(make_op(
        {anchor: (None, _positional_version(anchor, _content_version(image + b"repair")))},
        {anchor: image}, kind="touched", intent="revert F: keep foo's place in a.py",
    ))
    heads = [o for o in Store(repo).all_ops()
             if anchor in o.footprint and o.footprint[anchor][0] is None]
    assert len(heads) > 1  # precondition: the anchor chain is forked in the store

    preview = verbs.plan_restore(repo, "a.py::foo")
    assert preview.ok, preview.message
    verbs.restore(repo, "a.py::foo")
    assert code(get(repo), Store(repo).all_ops())["a.py"] in (
        b"def foo():\n    return 1\n", b"def foo():\n    return 2\n")


# -- the event inverse: restore as the exact reversal of a recorded revert (fix B) ----------------
#
# `I ∪ ↓X` is not `I \ ↑X`'s inverse, and no amount of care inside the downset closes the gap: a
# revert removes an *up*-set (which reaches other features' work) and mints subtraction ops, while
# a restore unions a *down*-set and can only add. The information about what a particular revert
# did lives in the journal event that revert wrote, so these tests pin the behaviour of resolving a
# restore against that event instead of re-deriving it from the order structure.


def _journal_events(repo):
    from sgt.core import oplog

    return oplog.load(repo).get(oplog._ref_key(Path(repo)), [])


def test_apply_records_the_verb_and_its_target_ops_on_the_journal_event(tmp_path):
    """The event inverse needs to know which revert removed what. Before this, an `ideal_edit`
    entry carried only the before/after op-sets, so nothing durable said *which* revert wrote it
    and `restore` had to guess at scaffolding from an advisory `intent` string."""
    repo = _foo_chain(tmp_path / "repo", 3)
    get(repo)
    tip = _op_with(Store(repo).all_ops(), "a.py::foo", b"return 3")

    verbs.revert(repo, tip.id)

    event = _journal_events(repo)[-1]
    assert event["verb"] == "revert"
    assert event["target_ops"] == [tip.id]
    assert event["applied"] is True


def test_the_revert_a_restore_reverses_is_found_by_the_name_the_person_typed(tmp_path):
    """Op ids do not survive a re-derivation. A name does.

    `sgt log --refresh` and `advanced resync` re-mine and re-cluster, and afterwards the same
    handle resolves to a different op-set -- 39 ops where the revert had recorded 20, measured on
    the study's footfall bundle. Both comparisons this resolver made were over op ids, so neither
    matched, and `restore` fell back to the algebraic union: `would leave two live versions of
    footfall/metrics.py::_exclude_events` on one path, and on the other a ✓ over a commit that
    changed no files -- which is what a participant hit in stage 4, twice, on two different days.

    `sgt undo` reversed the very same edit without trouble the whole time, because it replays the
    journal instead of re-resolving a name. This is the rule that lets restore do the same.
    """
    repo = _foo_chain(tmp_path / "repo", 3)
    get(repo)
    tip = _op_with(Store(repo).all_ops(), "a.py::foo", b"return 3")
    verbs.revert(repo, tip.id)

    ops = Store(repo).all_ops()
    # What a re-derivation leaves behind: the handle still resolves, to ops the event never named.
    rederived = frozenset({"0" * 64})

    assert verbs._matching_revert_event(Path(repo), rederived, ops) is None, (
        "ids that share nothing with the event must not match on ids alone")

    found = verbs._matching_revert_event(Path(repo), rederived, ops, tip.id)
    assert found is not None, "the revert recorded under this handle was not found by it"
    event, _later = found
    assert event["verb"] == "revert"
    assert event["target"] == tip.id
    assert event["target_ops"] == [tip.id], "matched an event, but not the removal that happened"

    # ...but only while that removal is still standing. A handle matches every revert ever
    # recorded under it, and a study bundle ships a journal in which its own build reverted the
    # theme -- an event `./stage 3` undoes by moving git rather than by restoring, so it stays in
    # the journal with nothing left to reverse. Matching it made `restore` answer "that revert has
    # already been reversed" on an untouched project, about a revert the person never ran, where
    # F135 asks for "there is nothing to restore". Caught by verify-bundles on the packed bundle,
    # which is the only place the shipped journal exists.
    everything_back = frozenset(o.id for o in ops)
    assert verbs._matching_revert_event(
        Path(repo), rederived, ops, tip.id, everything_back) is None, (
        "a revert whose removal no longer stands must not be found by its name")


def test_a_name_that_stopped_resolving_is_still_answered_by_the_journal(tmp_path):
    """F141. The rung above `_matching_revert_event`: resolving the name at all.

    The earlier fix made restore find the recorded revert once the target had resolved to an
    op-set. A re-mine re-labels the ◆ cross-feature work from commit subjects, so on the study
    bundle the handle `./stage 3` prints -- "Event Day Handling" -- stops naming anything in the
    map, and restore never reached that fix: it fell past every deterministic rung to the
    natural-language one and exited `could not resolve ... set OPENAI_API_KEY`. The journal wrote
    the handle down when the revert applied, and a handle is the one part a re-derivation cannot
    rename.
    """
    repo = _foo_chain(tmp_path / "repo", 3)
    get(repo)
    ops = Store(repo).all_ops()
    tip = _op_with(ops, "a.py::foo", b"return 3")

    assert verbs.ops_removed_by_named_revert(repo, tip.id) is None, (
        "nothing has been reverted under this name yet")

    before = get(repo).op_ids
    verbs.revert(repo, tip.id)
    removed = verbs.ops_removed_by_named_revert(repo, tip.id)
    assert removed, "the revert just recorded under this name was not found by it"
    assert removed == before - get(repo).op_ids, "found an event, but not the removal that happened"

    # Restoring through that set is the edit the person meant, and it is exact.
    preview = verbs.plan_restore_op_set(repo, tip.id, removed)
    assert preview.ok, preview.message
    assert preview.after_ids == before

    # And once it is back, the name answers nothing again -- the guard that keeps a bundle's own
    # shipped build-time revert from being reversed on an untouched project (F135).
    verbs.apply(repo, preview)
    assert get(repo).op_ids == before
    assert verbs.ops_removed_by_named_revert(repo, tip.id) is None, (
        "a revert whose removal no longer stands must not answer to its name")


def test_restore_brings_back_a_dependent_the_revert_swept(tmp_path):
    """The direction gap, in miniature. `revert --keep-dependents=False`... i.e. the explicit
    `take_dependents` demolition removes `↑mid` = {mid, tip}; `↓mid` = {add, mid} can never
    return `tip`, because tip is a *dependent* of the target, not a prerequisite of it. The
    algebraic restore therefore left the chain's head permanently gone while printing a bare ✓."""
    repo = _foo_chain(tmp_path / "repo", 3)
    original = get(repo).op_ids
    ops = Store(repo).all_ops()
    mid = _op_with(ops, "a.py::foo", b"return 2")

    verbs.revert(repo, mid.id, take_dependents=True)
    assert get(repo).op_ids != original

    verbs.restore(repo, mid.id)
    assert get(repo).op_ids == original
    assert code(get(repo), Store(repo).all_ops())["a.py"] == b"def foo():\n    return 3\n"


def test_restore_peels_the_splice_a_subtraction_revert_minted(tmp_path):
    """The default (subtraction) revert removes no op at all for a shared symbol: it appends an
    inverse-patch splice at the tip. A union can never take that back off, so `restore` used to
    either refuse the exact rewind as a fork or report ✓ with the call site still spliced out --
    the case `cli.ideal_edit._restore_gap` exists to warn about."""
    from sgt.store.gitbind import init_store

    repo = tmp_path / "repo"
    gb, _ = init_store(repo)
    (repo / "util.py").write_text("def build():\n    a()\n    b()\n", encoding="utf-8")
    gb.commit_all("base")
    (repo / "util.py").write_text(
        "def build():\n    a()\n    wl()\n    b()\n\ndef wl():\n    return 1\n",
        encoding="utf-8")
    gb.commit_all("feature F: wl plus wiring")
    (repo / "util.py").write_text(
        "def build():\n    a()\n    wl()\n    b()\n    c()\n\ndef wl():\n    return 1\n"
        "\ndef c_helper():\n    return 2\n",
        encoding="utf-8")
    gb.commit_all("later feature: c")

    get(repo)
    target = next(o for o in Store(repo).all_ops()
                  if "util.py::wl" in o.footprint and o.footprint["util.py::wl"][0] is None)

    verbs.revert(repo, target.id)
    text = code(get(repo), Store(repo).all_ops())["util.py"].decode()
    assert "def wl" not in text and "wl()" not in text

    preview = verbs.restore(repo, target.id)
    assert preview.ok, preview.message
    text = code(get(repo), Store(repo).all_ops())["util.py"].decode()
    assert "def wl" in text
    assert "wl()" in text          # the splice was peeled, not merely out-voted
    assert "c()" in text and "def c_helper" in text  # the later feature is untouched


def test_restore_keeps_work_committed_after_the_revert(tmp_path):
    """What makes the event inverse worth having over `sgt undo`: undo re-materializes the prior
    ideal as an absolute snapshot and so *refuses* once anything landed on top (the F3 guard).
    The event inverse is a delta applied to the current ideal, so it is random-access -- the
    reverted work comes back and the intervening commit stays."""
    from sgt.store.gitbind import init_store

    repo = tmp_path / "repo"
    gb, _ = init_store(repo)
    (repo / "a.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    gb.commit_all("foo")
    (repo / "a.py").write_text("def foo():\n    return 1\n\ndef bar():\n    return 2\n",
                               encoding="utf-8")
    gb.commit_all("bar")
    get(repo)
    bar = next(o for o in Store(repo).all_ops()
               if "a.py::bar" in o.footprint and o.footprint["a.py::bar"][0] is None)

    verbs.revert(repo, bar.id)

    (repo / "b.py").write_text("def later():\n    return 3\n", encoding="utf-8")
    gb.commit_all("unrelated later work")
    get(repo)

    preview = verbs.restore(repo, bar.id)
    assert preview.ok, preview.message
    files = code(get(repo), Store(repo).all_ops())
    assert b"def bar" in files["a.py"]        # the revert was reversed
    assert b"def later" in files["b.py"]      # and the later commit survived it


def test_restore_declines_the_event_inverse_when_later_work_built_on_the_splice(tmp_path):
    """The boundary the event inverse must respect. Once later work has built on a splice, that
    splice is no longer its symbol's tip and peeling it would orphan the later op -- so the event
    inverse declines and the restore degrades to the algebraic union, which refuses this as a
    fork exactly as it did before. Reversing a revert *through* work layered on top of it needs
    re-addition as a forward merge at the tip, which is a different change than this one.

    What is pinned here is that the decline is non-destructive: the ideal does not move, it stays
    valid, and the later work is still there to be built on."""
    from sgt.store.gitbind import init_store

    repo = tmp_path / "repo"
    gb, _ = init_store(repo)
    (repo / "util.py").write_text("def build():\n    a()\n    b()\n", encoding="utf-8")
    gb.commit_all("base")
    (repo / "util.py").write_text(
        "def build():\n    a()\n    wl()\n    b()\n\ndef wl():\n    return 1\n",
        encoding="utf-8")
    gb.commit_all("feature F")
    get(repo)
    target = next(o for o in Store(repo).all_ops()
                  if "util.py::wl" in o.footprint and o.footprint["util.py::wl"][0] is None)

    verbs.revert(repo, target.id)

    text = code(get(repo), Store(repo).all_ops())["util.py"].decode()
    (repo / "util.py").write_text(text.replace("    b()\n", "    b()\n    d()\n"),
                                  encoding="utf-8")
    gb.commit_all("later work on the spliced symbol")
    get(repo)
    before = get(repo).op_ids

    preview = verbs.plan_restore(repo, target.id)
    assert not preview.ok and preview.forked
    assert "util.py::build" in preview.message  # names the symbol, not a wall of op ids

    assert get(repo).op_ids == before  # nothing moved
    assert is_valid_ideal(Store(repo).all_ops(), get(repo).op_ids)
    assert "d()" in code(get(repo), Store(repo).all_ops())["util.py"].decode()


@pytest.mark.parametrize("mangle, why", [
    (lambda evs: evs[-1].__setitem__("ideal", 7), "a field that is a number, not a list"),
    (lambda evs: evs[-1].__setitem__("ideal", "abc"), "a field that is a bare string"),
    (lambda evs: evs.append(None), "a stray null where an entry should be"),
    (lambda evs: evs.__setitem__(slice(None), "not a list at all"), "a whole log of the wrong type"),
])
def test_a_malformed_journal_falls_back_instead_of_crashing(tmp_path, mangle, why):
    """The journal is a plain JSON file on disk, so it can be hand-edited or half-written.

    The exact path is an optimization over the downset union and nothing more, so a journal it
    cannot read has exactly one correct behaviour: take the answer restore gave before it existed.
    `frozenset("abc")` is the trap -- three one-character "op-ids" rather than an error, so a
    malformed field reaches a planner and raises from inside it unless it is rejected by shape.
    """
    repo = _foo_chain(tmp_path / "repo", 3)
    get(repo)
    tip = _op_with(Store(repo).all_ops(), "a.py::foo", b"return 3")
    verbs.revert(repo, tip.id)

    journal = Path(repo) / ".sgt" / "local" / "ideal_journal.json"
    doc = json.loads(journal.read_text(encoding="utf-8"))
    mangle(doc["data"]["refs/heads/main"])
    journal.write_text(json.dumps(doc), encoding="utf-8")

    preview = verbs.plan_restore(repo, tip.id)  # must not raise, whatever `why` says

    assert preview.ok
    assert tip.id in set(preview.after_ids)  # the downset union still answers


def test_meta_cannot_overwrite_the_fields_undo_relies_on(tmp_path):
    """`record_ideal` merges caller metadata into the entry, and undo reads that same entry to
    re-materialize a prior ideal. A caller that could set `ideal` or `result` through `meta`
    could therefore rewrite what undo restores, so the reserved keys are not overridable."""
    from sgt.core import lens

    repo = _foo_chain(tmp_path / "repo", 2)
    ideal = get(repo)

    reserved = ("kind", "ideal", "witness", "result", "applied")
    lens.record_ideal(repo, ideal, "deadbeef", meta={
        "verb": "revert", "target": "x", "target_ops": [],
        **{k: "hijacked" for k in reserved},
    })

    event = _journal_events(repo)[-1]
    assert not [k for k in reserved if event[k] == "hijacked"]
    assert event["kind"] == "ideal_edit" and event["applied"] is True
    assert event["result"] == sorted(ideal.op_ids)
    assert event["verb"] == "revert"  # non-reserved metadata still lands


def test_restore_does_not_resurrect_what_a_later_revert_removed(tmp_path):
    """The event inverse reverses one edit; it must not reach through a newer one.

    A revert's recorded `removed` set describes the ideal as it stood then. Re-admitting it
    wholesale means a later, deliberate revert of one of those ops is silently undone -- the user
    asked to reverse the first revert and got the second one reversed too, with no refusal and no
    report. What a newer edit took out belongs to that edit."""
    repo = _foo_chain(tmp_path / "repo", 3)
    get(repo)
    ops = Store(repo).all_ops()
    v2 = _op_with(ops, "a.py::foo", b"return 2")
    v3 = _op_with(ops, "a.py::foo", b"return 3")

    verbs.revert(repo, v2.id, take_dependents=True)  # sweeps v2 and v3 together
    verbs.restore(repo, v2.id)
    assert v3.id in get(repo).op_ids  # the exact inverse does bring the swept dependent back

    verbs.revert(repo, v3.id)  # ... and now a later edit takes v3 out on purpose
    verbs.restore(repo, v2.id)  # reversing the *first* revert again

    assert v3.id not in get(repo).op_ids
    assert code(get(repo), Store(repo).all_ops())["a.py"] == b"def foo():\n    return 2\n"
    assert is_valid_ideal(Store(repo).all_ops(), get(repo).op_ids)


def test_restore_never_peels_only_some_of_the_stand_ins_a_revert_minted(tmp_path):
    """All of them or none. A revert can mint several stand-ins for one target -- here a prune for
    `def wl` and a splice for its call site. If later work lands on the splice, only the prune is
    still at a tip, and peeling just that one puts the definition back while its call site stays
    spliced out. `_validated` accepts that: groundedness and fork-freedom say nothing about
    whether a reversal is *complete*. So the result is a half-edit reported as a success, which is
    the failure class this whole path exists to remove."""
    from sgt.store.gitbind import init_store

    repo = tmp_path / "repo"
    gb, _ = init_store(repo)
    (repo / "util.py").write_text("def build():\n    a()\n    b()\n", encoding="utf-8")
    gb.commit_all("base")
    (repo / "util.py").write_text(
        "def build():\n    a()\n    wl()\n    b()\n\ndef wl():\n    return 1\n", encoding="utf-8")
    gb.commit_all("feature F")
    get(repo)
    (repo / "util.py").write_text(
        "def build():\n    a()\n    wl()\n    b()\n    c()\n\ndef wl():\n    return 1\n"
        "\ndef c():\n    return 2\n", encoding="utf-8")
    gb.commit_all("an interleaved later feature")
    get(repo)
    target = next(o for o in Store(repo).all_ops()
                  if "util.py::wl" in o.footprint and o.footprint["util.py::wl"][0] is None)

    verbs.revert(repo, target.id)
    text = code(get(repo), Store(repo).all_ops())["util.py"].decode()
    (repo / "util.py").write_text(text.replace("    b()\n", "    b()\n    d()\n"),
                                  encoding="utf-8")
    gb.commit_all("later work on the spliced symbol")
    get(repo)

    preview = verbs.plan_restore(repo, target.id)
    if preview.ok:
        verbs.apply(repo, preview)

    out = code(get(repo), Store(repo).all_ops())["util.py"].decode()
    assert not ("def wl" in out and "    wl()" not in out), "half a reversal was applied"
    assert "d()" in out  # whatever it decided, the later work is untouched
    assert is_valid_ideal(Store(repo).all_ops(), get(repo).op_ids)


def test_restore_matches_the_revert_event_across_shifted_layout_attribution(tmp_path):
    """A theme resolves to the ops its member atoms carry, and re-deriving that record across the
    revert's own land commit moves the entities' anchor and residue chains into the theme's atoms.
    The same name then resolves to more ops than the revert recorded (24 -> 31 on the study's
    stage 4, 2026-08-30, every extra one layout-only), the exact-equality journal lookup missed,
    and the algebraic fallback refused the rewind as a fork. Removals that agree on every entity
    op are the same removal, so the lookup compares entity cores."""
    from sgt.core.op import _symbol_kind

    repo = tmp_path / "repo"
    gb, _ = init_store(repo)
    (repo / "util.py").write_text("def build():\n    a()\n    b()\n", encoding="utf-8")
    gb.commit_all("base")
    (repo / "util.py").write_text(
        "def build():\n    a()\n    wl()\n    b()\n\ndef wl():\n    return 1\n",
        encoding="utf-8")
    gb.commit_all("feature F: wl plus wiring")

    get(repo)
    target = next(o for o in Store(repo).all_ops()
                  if "util.py::wl" in o.footprint and o.footprint["util.py::wl"][0] is None)
    preview = verbs.plan_revert_op_set(repo, "the wl work", frozenset({target.id}))
    assert preview.ok, preview.message
    verbs.apply(repo, preview)
    assert b"def wl" not in code(get(repo), Store(repo).all_ops())["util.py"]

    layout = {o.id for o in Store(repo).all_ops()
              if o.footprint
              and all(_symbol_kind(s) in ("anchor", "residue") for s in o.footprint)}
    inflated = frozenset(preview.target_ops) | layout
    assert inflated - frozenset(preview.target_ops), \
        "the inflation needs a layout op the event never named"

    ops_now = verbs._load(repo)[0]
    assert verbs._matching_revert_event(Path(repo), inflated) is None       # exact match misses
    assert verbs._matching_revert_event(Path(repo), inflated, ops_now) is not None

    again = verbs.plan_restore_op_set(repo, "the wl work", inflated)
    assert again.ok, again.message
    verbs.apply(repo, again)
    text = code(get(repo), Store(repo).all_ops())["util.py"].decode()
    assert "def wl" in text and "wl()" in text


def test_a_pre_upgrade_journal_entry_is_never_mistaken_for_a_revert(tmp_path):
    """Real repositories hold entries written before `verb`/`target_ops` existed. They carry a
    revert-shaped before/after delta and nothing that says which verb wrote it, so matching one
    would reverse an edit on a guess. They must simply not match, leaving the union to answer."""
    repo = _foo_chain(tmp_path / "repo", 3)
    get(repo)
    tip = _op_with(Store(repo).all_ops(), "a.py::foo", b"return 3")
    verbs.revert(repo, tip.id)

    journal = Path(repo) / ".sgt" / "local" / "ideal_journal.json"
    doc = json.loads(journal.read_text(encoding="utf-8"))
    for event in doc["data"]["refs/heads/main"]:
        event.pop("verb", None)
        event.pop("target_ops", None)
    journal.write_text(json.dumps(doc), encoding="utf-8")

    preview = verbs.plan_restore(repo, tip.id)

    assert preview.ok and tip.id in set(preview.after_ids)  # the algebraic union still answers


def test_the_inverse_is_the_whole_peel_not_only_the_stand_ins_still_at_a_tip(tmp_path):
    """F145. The journal path used to decline whenever a minted stand-in was no longer at a
    frontier tip, on the grounds that the caller's union fallback would refuse. It does not
    refuse: it re-admits the closure of the removed set while the mints keep holding the current
    bytes, so both sides fold to identical text. On the study's footfall bundle that answered
    `restores 39 edits` and changed no file, under a ✓ -- while `sgt undo` reversed the same edit
    without trouble, because it replays the recorded snapshot instead of asking where tips are.

    So the plan is the complete reversal and `_validated` is the judge. What this pins is that a
    reversal of a recorded revert restores the exact prior op-set, mints and all.
    """
    repo = _foo_chain(tmp_path / "repo", 3)
    before = get(repo).op_ids
    ops = Store(repo).all_ops()
    tip = _op_with(ops, "a.py::foo", b"return 3")

    verbs.revert(repo, tip.id)
    reverted = get(repo).op_ids
    assert reverted != before

    # Whatever the revert minted to hold the layout is peeled whole, so the ideal comes back
    # exactly -- not "the tips of it".
    removed = verbs.ops_removed_by_named_revert(repo, tip.id)
    plan = verbs.plan_restore_op_set(repo, tip.id, removed)
    assert plan.ok, plan.message
    assert plan.after_ids == before, "a reversal that lands anywhere else is not the inverse"
    assert not (plan.after_ids - before), "no stand-in may survive the reversal"


def test_apply_journals_the_planned_edit_even_when_a_reader_syncs_mid_apply(tmp_path, monkeypatch):
    """F146. The editor polls sgt every few seconds. A poll that lands between `put()`'s commit and
    `record_ideal` sees HEAD moved past the witness and mines the revert's own commit as ordinary
    edits. `record_ideal` then read the journal's "before" set off disk, so the entry claimed the
    revert removed those mined ops too -- and `restore`, reversing the entry exactly, re-admitted
    ops that hold the removed bytes and changed no file. Measured live on footfall: 39 re-admitted,
    0 files, `./check 4` still red. The journal must describe the edit that was planned."""
    from sgt.core import lens

    repo = _foo_chain(tmp_path / "repo", 3)
    original = get(repo).op_ids
    tip = _op_with(Store(repo).all_ops(), "a.py::foo", b"return 3")
    plan = verbs.plan_revert(repo, tip.id)

    real_put = lens.put

    def put_then_someone_reads(*a, **kw):
        sha = real_put(*a, **kw)
        get(repo)  # the poller: mines the commit `put` just made
        return sha

    monkeypatch.setattr(lens, "put", put_then_someone_reads)
    verbs.apply(repo, plan)

    entry = lens._load_ideal_journal(repo)["refs/heads/main"][-1]
    assert frozenset(entry["ideal"]) == plan.before_ids
    assert frozenset(entry["result"]) == plan.after_ids

    monkeypatch.setattr(lens, "put", real_put)
    verbs.restore(repo, tip.id)
    assert get(repo).op_ids == original
