"""Pins for the 2026-08-08 performance caches: the racy-clean ops-dir stat, the persisted
derivation stamps, the hash-based drift manifest, the persisted tree-sitter cache, and the
gitbind head-state memo. Each test targets the failure mode that would make the cache WRONG
rather than merely cold -- a later write served stale, a stamp masking a missing op, a manifest
hiding real drift, a corrupt backing file erroring instead of re-parsing."""

from __future__ import annotations

import hashlib
import os
import time

import pytest

from sgt import state
from sgt.core import lens, opindex
from sgt.core.op import make_op
from sgt.core.store import Store
from sgt.store.gitbind import GitBinding


def _op(sym: str = "a.py::foo", n: int = 0):
    return make_op({sym: (None, f"v{n}")}, {sym: f"body{n}".encode()}, provenance=(f"sha{n}",))


def _age_dir(path, seconds: int = 10) -> None:
    old = time.time_ns() - seconds * 1_000_000_000
    os.utime(path, ns=(old, old))


def test_ops_dir_stat_detects_write_after_trusted_scan(tmp_path):
    store = Store(tmp_path)
    store.init()
    store.add(_op(n=0))
    # Age the dir so the first scan lands outside the racy-clean slack and earns trust.
    _age_dir(store.ops_dir)
    assert opindex._ops_dir_stat(tmp_path)[0] == 1
    assert opindex._ops_dir_stat(tmp_path)[0] == 1  # fast path
    store.add(_op(n=1))  # bumps the dir mtime -- the fast path must see it
    assert opindex._ops_dir_stat(tmp_path)[0] == 2


def test_ops_dir_stat_persisted_record_survives_a_fresh_process(tmp_path):
    store = Store(tmp_path)
    store.init()
    store.add(_op(n=0))
    _age_dir(store.ops_dir)
    opindex._ops_dir_stat(tmp_path)  # trusted scan -> persisted record
    assert state.load_json(tmp_path, "ops_dirstat", default=None) is not None
    opindex._DIR_STAT_MEMO.clear()  # simulate a fresh process
    assert opindex._ops_dir_stat(tmp_path)[0] == 1
    store.add(_op(n=1))
    opindex._DIR_STAT_MEMO.clear()
    assert opindex._ops_dir_stat(tmp_path)[0] == 2  # stale record must not be served


def test_validity_stamp_never_masks_missing_ops(tmp_path):
    store = Store(tmp_path)
    store.init()
    op = store.add(_op(n=0))
    opindex.rebuild(tmp_path)
    index = opindex.index_ops(tmp_path)

    ideal = lens._validated_from_ops(tmp_path, {op.id}, index)  # full check, earns the stamp
    assert op.id in ideal.op_ids
    # Same stamped digest, but the op universe no longer carries the op (the git-switch
    # scenario): the live presence check must fail exactly like Ideal.from_ops.
    with pytest.raises(ValueError):
        lens._validated_from_ops(tmp_path, {op.id}, [])


def test_reduced_ideal_ids_stamp_reproduces_the_reduction(tmp_path):
    store = Store(tmp_path)
    store.init()
    a = store.add(_op(sym="a.py::f", n=0))
    b = store.add(_op(sym="b.py::g", n=1))
    opindex.rebuild(tmp_path)
    index = opindex.index_ops(tmp_path)

    first = lens._reduced_ideal_ids(tmp_path, {a.id, b.id}, index)
    # A fresh in-memory state (new process) must reproduce the same result from the stamp.
    lens._DERIVE_STATE.clear()
    assert lens._reduced_ideal_ids(tmp_path, {a.id, b.id}, index) == first


def test_drift_by_hash_matches_byte_drift(tmp_path):
    from sgt import api

    (tmp_path / "a.txt").write_bytes(b"hello")
    materialized = {"a.txt": b"hello", "b.txt": b"gone"}
    hashes = {p: hashlib.sha256(c).hexdigest() for p, c in materialized.items()}
    assert api._drift_paths_by_hash(tmp_path, hashes) == api._drift_paths(tmp_path, materialized)
    (tmp_path / "a.txt").write_bytes(b"edited")
    assert api._drift_paths_by_hash(tmp_path, hashes) == ["a.txt", "b.txt"]
    assert api._drift_paths(tmp_path, materialized) == ["a.txt", "b.txt"]


def test_persistent_extract_cache_roundtrips_and_degrades_on_corruption(tmp_path):
    from sgt.entities import extract

    Store(tmp_path).init()
    src = "def f():\n    return 1\n"
    extract._PERSIST_REPO = None  # detach whatever repo an earlier test attached
    extract.attach_persistent_cache(tmp_path)
    ents = extract.extract_file("m.py", src)
    assert ents
    extract.flush_persistent_cache()
    assert state.load_json(tmp_path, "extract_cache")["entries"]

    # A fresh "process": the parse must come back from disk, hashes intact.
    extract._EXTRACT_CACHE.clear()
    extract._PERSIST_REPO = None
    extract.attach_persistent_cache(tmp_path)
    ents2 = extract.extract_file("m.py", src)
    assert [e.id for e in ents2] == [e.id for e in ents]
    assert ents2[0].structural_hash == ents[0].structural_hash
    assert ents2[0].content_hash == ents[0].content_hash

    # A torn/corrupt backing file degrades to a silent re-parse, never an error.
    state.path(tmp_path, "extract_cache").write_text("{not json")
    extract._EXTRACT_CACHE.clear()
    extract._PERSIST_REPO = None
    extract.attach_persistent_cache(tmp_path)
    assert [e.id for e in extract.extract_file("m.py", src)] == [e.id for e in ents]


def test_head_memo_tracks_mutations(tmp_path):
    gb = GitBinding(tmp_path)
    gb.init()
    (tmp_path / "f.txt").write_text("x")
    gb._git("add", "-A")
    gb._git("commit", "-m", "one")
    h1 = gb.head()
    assert gb.head() == h1  # memo hit
    (tmp_path / "f.txt").write_text("y")
    gb._git("add", "-A")
    gb._git("commit", "-m", "two")  # moves HEAD -- the signature must catch it unprompted
    h2 = gb.head()
    assert h2 is not None and h2 != h1
    assert gb.commit_shas()[0] == h2
    assert len(gb.history_meta()) == 2


def test_sync_no_op_gate_misses_after_miner_upgrade(tmp_path, monkeypatch):
    """A sync memo written by an older miner must not gate away the re-mine after an upgrade:
    the fingerprint folds MINER_VERSION in, so the gate misses and the new miner's ops (e.g.
    anchor revisions absent under the old rules) actually land."""
    from sgt.core import lens
    from sgt.store.gitbind import init_store

    gb, _ = init_store(tmp_path)
    (tmp_path / "a.py").write_text("def alpha():\n    return 1\n", encoding="utf-8")
    gb.commit_all("seed")
    lens.get(tmp_path)  # warm the sync memo under the current miner version
    assert lens.cached_map_is_current(tmp_path)

    monkeypatch.setattr(lens, "MINER_VERSION", lens.MINER_VERSION + "-next")
    assert not lens.cached_map_is_current(tmp_path)
