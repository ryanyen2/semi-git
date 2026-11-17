"""Laws of take-out and bring-back (`ugh.core.planner`) over the generated corpus.

- For every save, taking it out and bringing it back gives the original bytes.
- Taking out two saves that do not stand on each other gives the same tree in either order.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

from tests.laws import corpus


def _tree(repo):
    paths = subprocess.run(["git", "-C", str(repo), "ls-files", "-z"], capture_output=True,
                           text=True, check=True).stdout.split("\0")
    return {p: (repo / p).read_bytes() for p in paths
            if p and not p.startswith(".sgt/") and (repo / p).is_file()}


def _saves(repo):
    from ugh.core.lens import current_ideal
    from ugh.core.opindex import index_ops

    ideal = current_ideal(repo).op_ids
    by_sha: dict[str, set[str]] = {}
    for op in index_ops(repo):
        if op.id in ideal and op.provenance:
            by_sha.setdefault(min(op.provenance), set()).add(op.id)
    return {sha: frozenset(ops) for sha, ops in sorted(by_sha.items())}


def _apply(repo, ops, verb):
    from ugh.core import planner
    from ugh.core.lens import get

    get(repo)
    return planner.apply(repo, planner.plan(repo, ops, verb=verb, target=verb))


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    from ugh.core.lens import get

    root = tmp_path_factory.mktemp("takeout")
    repos = {}
    for name, case in corpus.CORPUS.items():
        repo = case.build(root / name)
        get(repo, unbounded=True)
        repos[name] = repo
    return root, repos


@pytest.mark.parametrize("name", sorted(corpus.CORPUS))
def test_every_save_out_then_back_gives_the_original_bytes(built, name):
    root, repos = built
    for i, (sha, ops) in enumerate(_saves(repos[name]).items()):
        repo = root / f"{name}-{i}"
        shutil.copytree(repos[name], repo, symlinks=True)
        original = _tree(repo)
        out = _apply(repo, ops, "revert")
        if out.files:
            _apply(repo, ops, "restore")
        assert _tree(repo) == original, f"{name}: save {sha[:7]}"


@pytest.mark.parametrize("name", sorted(corpus.CORPUS))
def test_two_independent_saves_come_out_the_same_in_either_order(built, name):
    from ugh.core import planner

    root, repos = built
    saves = _saves(repos[name])
    plans = {sha: planner.plan(repos[name], ops) for sha, ops in saves.items()}
    stands = {sha: {h for hd in p.held for h in hd["holders"]} for sha, p in plans.items()}
    pairs = [(a, b) for a in saves for b in saves if a < b
             and plans[a].files and plans[b].files
             and not (stands[a] & saves[b]) and not (stands[b] & saves[a])]
    for a, b in pairs[:4]:
        trees = []
        for first, second in ((a, b), (b, a)):
            repo = root / f"{name}-{first[:7]}-{second[:7]}"
            shutil.copytree(repos[name], repo, symlinks=True)
            _apply(repo, saves[first], "revert")
            _apply(repo, saves[second], "revert")
            trees.append(_tree(repo))
        assert trees[0] == trees[1], f"{name}: {a[:7]} and {b[:7]}"
