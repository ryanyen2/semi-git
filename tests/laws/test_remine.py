"""The store is a memo of one pure function of history: mining everything again from the horizon
gives exactly the ops the incremental mines stored, ugh's own commits included."""

from __future__ import annotations

import pytest

from tests.laws import corpus


def _stored(repo):
    from ugh.core.store import Store

    return {op.id for op in Store(repo).all_ops()}


def _full(repo):
    from ugh.core.mine import mine

    return {op.id for op in mine(repo)[0]}


@pytest.mark.parametrize("name", sorted(corpus.CORPUS))
def test_a_full_re_mine_gives_the_ops_the_incremental_mines_stored(tmp_path, monkeypatch, name):
    from ugh.core import lens

    repo = corpus.CORPUS[name].build(tmp_path / name)
    monkeypatch.setattr(lens, "_CHUNK_BUDGET_SECONDS", 0.0)  # one commit per contact
    while not lens.sync_status(repo)["complete"]:
        lens.get(repo)
    assert _stored(repo) == _full(repo)


def test_take_outs_and_bring_backs_re_mine_to_the_same_ops(tmp_path):
    from ugh.core import planner
    from ugh.core.lens import get

    repo = corpus.CORPUS["linear_history"].build(tmp_path / "repo")
    get(repo, unbounded=True)
    sha = corpus.commit_shas(repo)[4]
    steps = planner._Ctx(repo).of_commit[sha]
    planner.apply(repo, planner.plan(repo, steps, target="tangled"))
    planner.apply(repo, planner.plan(repo, steps, verb="restore", target="tangled"))
    assert _stored(repo) == _full(repo)
