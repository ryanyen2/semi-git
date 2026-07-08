"""Tests for sgt.core.verbs -- ideal-edit verbs (plan U8, R5/R14-surfacing/R20).

Each verb is an exact set edit of the current ref's ideal, previewable with no I/O and validated
through `Ideal.from_ops` so an invalid (forked) result is refused, never committed. The property
test at the bottom is U8's verification surface: every verb output that succeeds is a valid ideal,
including over the `revert_to_original` after-value collision that the collision-safe `upset_in`/
`downset_in` exist to handle.
"""

from __future__ import annotations

from hypothesis import HealthCheck, given, settings, strategies as st

from sgt.core import verbs
from sgt.core.fold import code
from sgt.core.lens import get
from sgt.core.order import is_valid_ideal
from sgt.core.store import Store
from sgt.store.gitbind import init_store
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


def test_revert_removes_exactly_the_upset_and_preview_lists_it(tmp_path):
    """revert of a mid-chain op removes that op and every op that builds on it (`↑X`), and the
    preview names exactly what was removed; the fold reverts to the pre-op bytes."""
    repo = _foo_chain(tmp_path / "repo", 3)
    ideal = get(repo)
    ops = Store(repo).all_ops()
    mid = _op_with(ops, "a.py::foo", b"return 2")  # add(1) -> mid(2) -> tip(3)
    tip = _op_with(ops, "a.py::foo", b"return 3")

    preview = verbs.revert(repo, mid.id, emit=True)
    assert preview.ok
    assert preview.removed == {mid.id, tip.id}  # exactly ↑mid
    assert "a.py::foo" in preview.affected_symbols

    verbs.revert(repo, mid.id)  # apply
    materialized = code(get(repo), Store(repo).all_ops())
    assert materialized["a.py"] == b"def foo():\n    return 1\n"


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
        assert is_valid_ideal(ops, preview.after_ids)
    else:
        assert preview.after_ids == preview.before_ids  # a refusal leaves the ideal untouched
