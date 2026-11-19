"""Tests for ugh.core.oracle: a revert with no `.sgt/oracle.json` writes nothing to the verdict
table."""

from __future__ import annotations

from ugh.core.lens import get
from ugh.core.store import Store
from ugh.store.gitbind import init_store



def _revert(repo, op_id):
    from ugh.core import planner

    steps = [st for st, ops in planner._Ctx(repo).steps.items() if op_id in ops]
    return planner.apply(repo, planner.plan(repo, steps, target=op_id))

def _foo_chain(repo_path, n: int):
    """A repo whose a.py::foo is a linear chain of `n` versions, one per commit."""
    gb, _ = init_store(repo_path)
    for i in range(1, n + 1):
        (repo_path / "a.py").write_text(f"def foo():\n    return {i}\n", encoding="utf-8")
        gb.commit_all(f"foo v{i}")
    return repo_path


def test_apply_with_no_oracle_configured_never_touches_the_verdict_table(tmp_path):
    repo = _foo_chain(tmp_path / "repo", 2)
    ideal = get(repo)
    tip = ideal.frontier(Store(repo).all_ops())["a.py::foo"]

    _revert(repo, tip)  # a materializing verb -- must never invoke the oracle

    assert not (repo / ".sgt" / "local" / "oracle.json").is_file()
