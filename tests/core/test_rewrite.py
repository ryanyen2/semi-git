"""Tests for ugh.core.rewrite -- `resolve_fork`, the kernel behind `ugh resolve`: the working tree's
version of every forked symbol is committed as one op that chain-extends `tip_a` and names both tips
in `resolves`, gated on the oracle for that exact candidate."""

from __future__ import annotations

import pytest

from ugh import state
from ugh.core import lens, rewrite, sync
from ugh.core.fold import code
from ugh.core.lens import get
from ugh.core.op import compute_id
from ugh.core.order import is_valid_ideal
from ugh.core.store import Store
from ugh.store.gitbind import GitBinding
from tests.core.test_sync import _BASE, _edit_and_commit, _push, _two_clones
from tests.laws import corpus

_MERGED = "def slugify(s):\n    return s.lower().strip().replace(' ', '-')\n"


def _diverged(tmp_path):
    repo = corpus.CORPUS["diverged_chain"].build(tmp_path / "repo")
    corpus.checkout(repo, "release")
    release_ideal = get(repo)
    corpus.checkout(repo, "main")
    main_ideal = get(repo)
    ops = Store(repo).all_ops()
    main_tip = main_ideal.frontier(ops)["slugify.py::slugify"]
    release_tip = release_ideal.frontier(ops)["slugify.py::slugify"]
    return repo, main_tip, release_tip


def test_resolve_fork_is_gated_on_the_oracle_then_lands_the_edited_version(tmp_path):
    repo, main_tip, release_tip = _diverged(tmp_path)
    (repo / "slugify.py").write_text(_MERGED, encoding="utf-8")

    with pytest.raises(rewrite.ResolveError, match="pending"):
        rewrite.resolve_fork(repo, main_tip, release_tip, message="resolve")
    with pytest.raises(rewrite.ResolveError, match="fail"):
        rewrite.resolve_fork(repo, main_tip, release_tip, message="resolve",
                             override=("fail", "still broken", "reviewer"))

    sha = rewrite.resolve_fork(repo, main_tip, release_tip, message="resolve",
                               override=("pass", "both diffs reconciled by hand", "reviewer"))
    assert sha

    ops = Store(repo).all_ops()
    after = get(repo)
    assert is_valid_ideal(ops, after.op_ids)
    assert code(after, ops)["slugify.py"] == _MERGED.encode()
    merged = next(o for o in ops if o.id in after.op_ids and o.resolves)
    assert merged.resolves == frozenset({main_tip, release_tip})
    assert merged.id == compute_id(merged.footprint, merged.images, merged.requires, merged.kind,
                                   merged.miner_version)


def test_resolve_fork_refuses_when_nothing_is_forked(tmp_path):
    repo, main_tip, _release_tip = _diverged(tmp_path)
    with pytest.raises(rewrite.ResolveError, match="no forked symbol"):
        rewrite.resolve_fork(repo, main_tip, main_tip, message="resolve")


def test_a_sync_fork_resolves_end_to_end_and_its_record_closes(tmp_path):
    a, b = _two_clones(tmp_path, _BASE)
    _edit_and_commit(a, "main.py", "def foo():\n    return 999\n\n\ndef bar():\n    return 2\n", "A")
    _push(a)
    _edit_and_commit(b, "main.py", "def foo():\n    return 42\n\n\ndef bar():\n    return 2\n", "B")

    report = sync.sync(b, remote="origin", branch="main")
    assert len(report.forks) == 1
    (record,) = state.load_json(b, "forks", default=[])
    assert record["symbol"] == "main.py::foo" and record["remedy"] == "ugh resolve main.py::foo"

    merged = "def foo():\n    return 1041\n\n\ndef bar():\n    return 2\n"
    (b / "main.py").write_text(merged, encoding="utf-8")
    assert rewrite.resolve_fork(b, *record["tips"], message="resolve fork: main.py::foo",
                                override=("pass", "reconciled by hand", "rev"))

    assert state.load_json(b, "forks", default=[]) == []
    assert GitBinding(b).is_clean()
    assert code(lens.get(b), Store(b).all_ops())["main.py"] == merged.encode()
