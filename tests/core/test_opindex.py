"""Tests for sgt.core.opindex -- the footprint-only sidecar over the op store (plan: optimize the
sgt agent surface for context + retrieval speed, Part A)."""

from __future__ import annotations

import time

from sgt import state
from sgt.core import opindex
from sgt.core.op import Attribution, make_op
from sgt.core.store import Store


def _op(sym: str = "a.py::foo", n: int = 0):
    return make_op({sym: (None, f"v{n}")}, {sym: f"body{n}".encode()}, provenance=(f"sha{n}",))


def test_index_ops_matches_all_ops_modulo_images(tmp_path):
    store = Store(tmp_path)
    store.init()
    for i in range(5):
        store.add(_op(sym=f"a.py::f{i}", n=i))

    opindex.rebuild(tmp_path)
    indexed = {op.id: op for op in opindex.index_ops(tmp_path)}
    real = {op.id: op for op in store.all_ops()}

    assert set(indexed) == set(real)
    for op_id, op in real.items():
        idx_op = indexed[op_id]
        assert idx_op.footprint == op.footprint
        assert idx_op.requires == op.requires
        assert idx_op.provenance == op.provenance
        assert idx_op.attribution == op.attribution
        assert idx_op.kind == op.kind
        assert idx_op.intent == op.intent
        assert idx_op.miner_version == op.miner_version
        assert idx_op.off_chain == op.off_chain
        assert idx_op.derived == op.derived
        assert idx_op.images == {}  # never carried by the sidecar


def test_index_ops_self_heals_when_snapshot_absent(tmp_path):
    store = Store(tmp_path)
    store.init()
    store.add(_op())
    assert state.load_json(tmp_path, "op_index", default=None) is None

    ops = opindex.index_ops(tmp_path)
    assert len(ops) == 1
    assert state.load_json(tmp_path, "op_index", default=None) is not None


def test_is_stale_true_when_snapshot_absent(tmp_path):
    Store(tmp_path).init()
    assert opindex.is_stale(tmp_path) is True


def test_is_stale_false_immediately_after_rebuild(tmp_path):
    store = Store(tmp_path)
    store.init()
    store.add(_op())
    opindex.rebuild(tmp_path, store)
    assert opindex.is_stale(tmp_path) is False


def test_is_stale_true_on_op_count_change(tmp_path):
    store = Store(tmp_path)
    store.init()
    store.add(_op(sym="a.py::f0", n=0))
    opindex.rebuild(tmp_path, store)
    assert opindex.is_stale(tmp_path) is False

    store.add(_op(sym="a.py::f1", n=1))  # a fresh op file lands -- dirent count moves
    assert opindex.is_stale(tmp_path) is True


def test_is_stale_true_on_miner_version_mismatch(tmp_path):
    store = Store(tmp_path)
    store.init()
    store.add(_op())
    opindex.rebuild(tmp_path, store)
    body = state.load_json(tmp_path, "op_index")
    state.save_json(tmp_path, "op_index", {**body, "miner_version": "0"})
    assert opindex.is_stale(tmp_path) is True


def test_is_stale_true_when_op_file_rewritten_without_count_change(tmp_path):
    """`Store.attribute`'s rewrite bumps an op file's mtime without changing the dirent count --
    the `built_mtime_ns <= max(op-file mtime)` staleness trigger must still catch it."""
    store = Store(tmp_path)
    store.init()
    op = store.add(_op())
    opindex.rebuild(tmp_path, store)
    assert opindex.is_stale(tmp_path) is False

    time.sleep(0.01)  # ensure a distinguishable mtime tick on filesystems with coarse resolution
    store.attribute(op.id, (Attribution(sha=f"sha0", session="s1"),))
    assert opindex.is_stale(tmp_path) is True


def test_ops_dir_stat_recounts_dirents_within_the_same_mtime_tick(tmp_path):
    """The `_ops_dir_stat` memo keys on (dir_mtime, dirent_count): on a coarse-granularity
    filesystem a store write can land in the same mtime tick as a memoized read, leaving the dir
    mtime unchanged. Recounting dirents (one readdir, cheap) catches the add that slipped through
    that tick, so a stale op count is never served on the memo fast path. The unchanged dir mtime
    is simulated by resetting it after the add."""
    import os

    store = Store(tmp_path)
    store.init()
    ops_dir = store.ops_dir
    (ops_dir / "aaaaaaaa").write_bytes(b"{}")
    count1, _ = opindex._ops_dir_stat(tmp_path)  # memoizes (dir_mtime, count1)
    frozen_mtime = ops_dir.stat().st_mtime_ns

    (ops_dir / "bbbbbbbb").write_bytes(b"{}")  # a second file lands...
    os.utime(ops_dir, ns=(frozen_mtime, frozen_mtime))  # ...within the same (simulated) tick

    count2, _ = opindex._ops_dir_stat(tmp_path)
    assert count2 == count1 + 1  # recount caught the add despite the unchanged dir mtime


def test_apply_delta_upserts_without_full_rebuild(tmp_path):
    store = Store(tmp_path)
    store.init()
    first = store.add(_op(sym="a.py::f0", n=0))
    opindex.rebuild(tmp_path, store)

    second = make_op({"a.py::f0": (f"v0", "v1")}, {"a.py::f0": b"body1"}, provenance=("sha1",))
    store.add(second)
    opindex.apply_delta(tmp_path, [second])

    body = state.load_json(tmp_path, "op_index")
    assert body["op_count"] == 2
    ids = {e["id"] for e in body["ops"]}
    assert ids == {first.id, second.id}


def test_apply_delta_rebuilds_when_snapshot_absent(tmp_path):
    store = Store(tmp_path)
    store.init()
    op = store.add(_op())
    assert state.load_json(tmp_path, "op_index", default=None) is None

    opindex.apply_delta(tmp_path, [op])

    body = state.load_json(tmp_path, "op_index")
    assert body["op_count"] == 1


def test_rebuild_and_index_ops_over_a_real_corpus(tmp_path):
    """End-to-end (plan Verification 1): `opindex.index_ops` matches `Store.all_ops()` on every
    identity-bearing field over a mined real corpus repo."""
    from sgt.core.lens import get
    from tests.laws import corpus

    repo = corpus.CORPUS["mixed_coverage"].build(tmp_path / "repo")
    get(repo)  # mine-on-contact, wiring _sync's opindex bookkeeping

    store = Store(repo)
    real = {op.id: op for op in store.all_ops()}
    assert real  # the fixture actually produced ops
    assert opindex.is_stale(repo) is False  # _sync should have left the sidecar fresh

    indexed = {op.id: op for op in opindex.index_ops(repo)}
    assert set(indexed) == set(real)
    for op_id, op in real.items():
        assert indexed[op_id].footprint == op.footprint
        assert indexed[op_id].requires == op.requires
        assert indexed[op_id].provenance == op.provenance
        assert indexed[op_id].intent == op.intent
