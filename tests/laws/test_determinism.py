"""Determinism laws (R12, R20, R22): mining is bit-deterministic given a fixed (parser version,
matcher thresholds), and the corpus fixtures themselves reproduce identically. That last one is
this file's only unconditional test -- it is the harness's own foundation, and must hold before
any kernel-dependent law built on top of ``tests/laws/corpus.py`` can be trusted.

Kernel-dependent laws below are real test logic, ``skipif``-guarded on the modules they need
(see ``tests/laws/test_roundtrip.py`` for the full minimal-contract docstring). They un-skip the
moment U2 (``sgt.core.mine``) lands -- do not soften a law to make it pass; land the kernel code.
"""

from __future__ import annotations

import importlib.util

import pytest

from tests.laws import corpus


def _has(*names: str) -> bool:
    def _found(name: str) -> bool:
        try:
            return importlib.util.find_spec(name) is not None
        except ModuleNotFoundError:
            return False  # a missing parent package (e.g. `sgt.core`) also means "not found"

    return all(_found(name) for name in names)


def test_corpus_builder_reproduces_identical_fixtures(tmp_path):
    """Meta-coverage (U1 Test scenarios): the corpus builder itself must be deterministic --
    same commit SHAs, same order -- across two independent builds, with a fixed author identity
    and fixed commit timestamps ruling out wall-clock leakage."""
    repo_a = corpus.CORPUS["linear_history"].build(tmp_path / "a")
    repo_b = corpus.CORPUS["linear_history"].build(tmp_path / "b")
    shas_a = corpus.commit_shas(repo_a)
    shas_b = corpus.commit_shas(repo_b)
    assert shas_a == shas_b
    assert len(shas_a) == 7  # the 7 commits _case_linear_history makes


def test_large_corpus_budgets_are_encoded_numerically():
    """R22/BET-E: adoption-scale budgets are pass/fail numbers, not prose."""
    assert corpus.MAX_INIT_SECONDS_PER_1K_COMMITS > 0
    assert corpus.MAX_STORE_BYTES_PER_COMMIT > 0


def test_large_corpus_repo_is_opt_in_never_fetched(monkeypatch):
    """The suite must never clone or fetch a large repo on its own -- only read an
    already-local path from an explicit env var, and skip cleanly when unset."""
    monkeypatch.delenv("SGT_LARGE_CORPUS_REPO", raising=False)
    assert corpus.large_corpus_repo() is None


_HAS_MINE = _has("sgt.core.mine")
_MINE_SKIP = "sgt.core.mine not implemented yet (U2)"


@pytest.mark.skipif(not _HAS_MINE, reason=_MINE_SKIP)
def test_mining_idempotence(tmp_path):
    """R20 'idempotence': mining a fixed, unchanged history twice in the same process yields
    byte-identical op ids in the same order -- no op minted twice, no nondeterministic id."""
    from sgt.core.mine import mine

    repo = corpus.CORPUS["linear_history"].build(tmp_path / "repo")
    first, _last_sha = mine(repo)
    second, _last_sha = mine(repo)
    assert [op.id for op in first] == [op.id for op in second]


@pytest.mark.skipif(not _HAS_MINE, reason=_MINE_SKIP)
def test_double_machine_mining_determinism(tmp_path):
    """R12/R20: mining two independently-built copies of the same history (simulating two
    machines / two clones) yields byte-identical op ids given a fixed (parser version, matcher
    thresholds) -- the honest form of the determinism claim."""
    from sgt.core.mine import mine

    repo_a = corpus.CORPUS["linear_history"].build(tmp_path / "machine_a")
    repo_b = corpus.CORPUS["linear_history"].build(tmp_path / "machine_b")
    ops_a, _last_sha = mine(repo_a)
    ops_b, _last_sha = mine(repo_b)
    assert [op.id for op in ops_a] == [op.id for op in ops_b]


@pytest.mark.skipif(not _HAS_MINE, reason=_MINE_SKIP)
def test_rebirth_cycle_op_ids_deterministic_across_clones(tmp_path):
    """R13 / LAW-0: the rebirth salt is derived from the *deleting commit's sha*, a pure function
    of git history. An add->del->A->del->A cycle mined on two independent clones of the same
    history yields byte-identical op ids -- the salt never leaks the local `.sgt` store or a
    wall-clock, so the distinct-bottom-per-deletion chaining reproduces exactly."""
    import subprocess

    from sgt.store.gitbind import init_store
    from sgt.core.mine import mine

    origin = tmp_path / "origin"
    gb, _ = init_store(origin)
    (origin / "n.txt").write_text("A\n", encoding="utf-8")
    gb.commit_all("add A")
    (origin / "n.txt").unlink()
    gb.commit_all("del 1")
    (origin / "n.txt").write_text("A\n", encoding="utf-8")
    gb.commit_all("re-add A")
    (origin / "n.txt").unlink()
    gb.commit_all("del 2")
    (origin / "n.txt").write_text("A\n", encoding="utf-8")
    gb.commit_all("re-add A again")

    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "--quiet", "--local", str(origin), str(clone)], check=True)

    origin_ops, _last_sha = mine(origin)
    clone_ops, _last_sha = mine(clone)
    assert [op.id for op in origin_ops] == [op.id for op in clone_ops]
