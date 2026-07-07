"""Round-trip law harness for the operation-ideal kernel (plan R20, R22, docs/plans/
2026-07-06-001-feat-operation-ideal-kernel-plan.md). These are the executable definition of
kernel correctness, written first and red per the plan's P0 Execution note: every law below is
real test logic, not a stub. Each is ``skipif``-guarded on the kernel module(s) it needs and
un-skips itself the moment that module lands (U2 through U6). Do not soften a law to make it
pass -- land the kernel code that makes it true.

Minimal kernel contract these laws assume. Individual units may expose a richer API; these
entry points must exist with this behavior (owning unit in parens):

    sgt.core.mine.mine(repo_path, since=None) -> list[Op]          (U2) newly mined ops since
        witness commit `since` (or full history if None); Op.id content-addressed, deterministic.
    sgt.core.op.Op                                                  (U3) frozen; .id, .footprint
        (dict[str, tuple[str, str]], symbol -> (before_version, after_version)).
    sgt.core.order.is_valid_ideal(ops, op_ids) -> bool              (U4) downward-closure +
        unique-maximal-per-chain over `ops` for the subset `op_ids`.
    sgt.core.ideal.Ideal.from_ops(op_ids, ops) -> Ideal             (U4) raises ValueError if
        `op_ids` is not a valid ideal; exposes `.op_ids` and `.covered_paths()`.
    sgt.core.fold.code(ideal) -> dict[str, bytes]                   (U5) total, deterministic;
        path -> materialized bytes.
    sgt.core.lens.get(repo_path) -> Ideal                           (U6) mine + advance the ideal
        tracked for `repo_path`'s current ref.
    sgt.core.lens.put(repo_path, ideal) -> str                      (U6) materialize + witness
        commit; returns the new commit sha.
"""

from __future__ import annotations

import importlib.util

import pytest
from hypothesis import HealthCheck, given, settings, strategies as st

from tests.laws import corpus


def _has(*names: str) -> bool:
    def _found(name: str) -> bool:
        try:
            return importlib.util.find_spec(name) is not None
        except ModuleNotFoundError:
            return False  # a missing parent package (e.g. `sgt.core`) also means "not found"

    return all(_found(name) for name in names)


_HAS_LENS = _has("sgt.core.lens", "sgt.core.fold", "sgt.core.mine")
_LENS_SKIP = "sgt.core.{lens,fold,mine} not implemented yet (U2, U5, U6)"

_HAS_ORDER = _has("sgt.core.mine", "sgt.core.order", "sgt.core.ideal")
_ORDER_SKIP = "sgt.core.{mine,order,ideal} not implemented yet (U2, U4)"


@pytest.mark.skipif(not _HAS_LENS, reason=_LENS_SKIP)
def test_put_get_fixed_point(tmp_path):
    """put-get: materializing an ideal and re-mining the witness commit it just wrote yields
    zero new ops -- the commit is fully explained by ops already in the ideal."""
    from sgt.core.lens import get, put

    repo = corpus.CORPUS["linear_history"].build(tmp_path / "repo")
    ideal = get(repo)
    put(repo, ideal)
    reidealed = get(repo)
    assert reidealed.op_ids == ideal.op_ids


@pytest.mark.skipif(not _HAS_LENS, reason=_LENS_SKIP)
def test_get_put_byte_fidelity(tmp_path):
    """get-put: code(get(edits)) reproduces the committed bytes at entity granularity -- an
    untouched entity's exact comments/formatting survive the fold (the `ast.unparse` formatting-
    loss regression class the plan's byte-splicing KTD exists to kill)."""
    from sgt.core.fold import code
    from sgt.core.lens import get
    from sgt.core.store import Store

    repo = corpus.CORPUS["linear_history"].build(tmp_path / "repo")
    before = {p: (repo / p).read_bytes() for p in corpus.tracked_paths(repo)}

    ideal = get(repo)
    materialized = code(ideal, Store(repo).all_ops())

    for path, original_bytes in before.items():
        assert materialized.get(path) == original_bytes, f"{path} lost byte fidelity through the fold"


@pytest.mark.skipif(not _HAS_LENS, reason=_LENS_SKIP)
def test_coverage_every_path_has_an_image(tmp_path):
    """Coverage (R7): every tracked path -- parseable or not -- is in exactly one symbol's image
    set. Whole-file pseudo-symbols make config/binary paths first-class, never silently dropped."""
    from sgt.core.lens import get
    from sgt.core.store import Store

    repo = corpus.CORPUS["linear_history"].build(tmp_path / "repo")
    tracked = set(corpus.tracked_paths(repo))
    ideal = get(repo)
    covered = ideal.covered_paths(Store(repo).all_ops())
    assert tracked <= covered, f"uncovered paths: {tracked - covered}"


@pytest.mark.skipif(not _HAS_LENS, reason=_LENS_SKIP)
def test_fully_removed_file_leaves_no_phantom(tmp_path):
    """get-put fidelity (R7/R20): a file added with an entity and then fully ``git rm``'d
    resurrects nowhere. Its anchor pseudo-symbol is ordering metadata mining never revises to
    BOTTOM, so it lingers at the frontier after the entity and residue are pruned -- but an
    anchor produces no bytes, so it must not keep the path alive as an empty ``b''``. A sibling
    that keeps >=1 entity after losing another (positive control) stays covered, materializing
    only the survivor's exact bytes. `code(I)` and `covered_paths` must agree exactly."""
    from sgt.core.fold import code
    from sgt.core.lens import get
    from sgt.core.store import Store

    repo = corpus.CORPUS["removed_paths"].build(tmp_path / "repo")
    ideal = get(repo)
    ops = Store(repo).all_ops()
    materialized = code(ideal, ops)
    covered = ideal.covered_paths(ops)

    # Negative: the fully-removed file is gone from both the fold and coverage -- no b'' phantom.
    assert "gone.py" not in materialized, f"removed file resurrected: {materialized.get('gone.py')!r}"
    assert "gone.py" not in covered, "removed file still reported covered by a lingering anchor"

    # Positive control: the sibling stays covered, materializing only the surviving entity.
    assert "survivor.py" in covered
    assert materialized.get("survivor.py") == (repo / "survivor.py").read_bytes()

    # The two views agree exactly on which paths materialize (R7 coverage law).
    assert set(materialized) == set(covered)


@pytest.mark.skipif(not _HAS_LENS, reason=_LENS_SKIP)
def test_locality(tmp_path):
    """Locality (07-02 S6.3): mining one commit only mints ops whose footprint touches paths
    that commit -- or an earlier one in that same symbol's own history -- actually changed; an
    unrelated part of the tree never gets a new op. This is exactly what the linear_history
    case's tangled commit (baz added to b.py, qux edited in c.py in one commit) is built to
    stress: two def-use-disjoint symbols, still both local to the commit's own changed paths.

    Checked cumulatively (paths changed up to and including `cur`), not against `cur`'s own
    diff alone: a cross-file move canonicalizes to its *original* surface path (plan U2's
    `_UnionFind` anchors on the earlier side), so a later op on that same symbol -- e.g. its
    eventual removal -- legitimately carries a footprint key naming the path it was born at,
    not the path the removing commit's diff actually touched. That's a stale canonical name,
    not an unrelated part of the tree being touched.
    """
    from sgt.core.mine import mine

    repo = corpus.CORPUS["linear_history"].build(tmp_path / "repo")
    shas = corpus.commit_shas(repo)
    # One full mine(), then group by witnessing commit -- since=None means "from genesis", not
    # "just this one commit", so per-commit locality has to be checked by provenance, not by
    # re-mining with `since` set to each commit's own predecessor in turn.
    ops = mine(repo)
    ops_by_sha: dict[str, list] = {}
    for op in ops:
        for sha in op.provenance:
            ops_by_sha.setdefault(sha, []).append(op)

    changed_so_far: set[str] = set()
    for prev, cur in zip([None, *shas], shas):
        changed_so_far |= set(corpus.changed_paths(repo, prev, cur))
        for op in ops_by_sha.get(cur, []):
            touched = {sym.split("::", 1)[0] for sym in op.footprint}
            assert touched <= changed_so_far, (
                f"op {op.id} touched {touched - changed_so_far} never changed by any commit up to {cur[:8]}"
            )


@pytest.mark.skipif(not _HAS_LENS, reason=_LENS_SKIP)
def test_squash_remine_identification(tmp_path):
    """AE1 / R8, the identification law: a squash-merged copy of already-mined work mints zero
    new ops -- the existing ops gain the squash commit as an additional witness instead of
    forking. This is also what makes rebase and re-mining converge."""
    from sgt.core.lens import get

    repo = corpus.CORPUS["squash_merge"].build(tmp_path / "repo")

    corpus.checkout(repo, "feature")
    feature_ideal = get(repo)
    op_count_before = len(feature_ideal.op_ids)
    assert op_count_before > 0

    corpus.checkout(repo, "main")
    squashed_ideal = get(repo)
    assert len(squashed_ideal.op_ids) == op_count_before, (
        "squash merge minted new ops instead of identifying with the already-mined feature ops"
    )


@pytest.mark.skipif(not _HAS_ORDER, reason=_ORDER_SKIP)
@settings(max_examples=10, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(st.data())
def test_verb_output_is_valid_ideal(tmp_path_factory, data):
    """R3/R20: downward-closure + unique-maximal-per-chain is unconstructible through the
    public API. For all subsets of a real mined op set, `Ideal.from_ops` either refuses (raises
    ValueError) or produces something `order.is_valid_ideal` agrees is valid -- never a silent
    ill-formed ideal. (U4's own test_order.py/test_ideal.py additionally check the frontier
    representation against a naive-set reference on synthetic DAGs; this law is the black-box
    acceptance version, run against real mined ops.)"""
    from sgt.core.ideal import Ideal
    from sgt.core.mine import mine
    from sgt.core.order import is_valid_ideal

    repo = corpus.CORPUS["linear_history"].build(tmp_path_factory.mktemp("repo"))
    ops = mine(repo)
    op_ids = [op.id for op in ops]
    subset = frozenset(data.draw(st.sets(st.sampled_from(op_ids)))) if op_ids else frozenset()

    if is_valid_ideal(ops, subset):
        ideal = Ideal.from_ops(subset, ops)
        assert is_valid_ideal(ops, ideal.op_ids)
    else:
        with pytest.raises(ValueError):
            Ideal.from_ops(subset, ops)
