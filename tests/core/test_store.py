"""Tests for sgt.core.store -- the content-addressed op store (plan U3, R1/R11/R12)."""

from __future__ import annotations

import os
import threading

import pytest

from sgt.core.op import make_op
from sgt.core.store import FsckReport, Store, StoreError, fsck


def _op(sym: str = "a.py::foo", n: int = 0):
    return make_op({sym: (None, f"v{n}")}, {sym: f"body{n}".encode()}, provenance=(f"sha{n}",))


def test_same_payload_same_id_across_separate_construction(tmp_path):
    """R12: two independently-constructed ops with identical content get the identical id."""
    a = make_op({"a.py::foo": (None, "v0")}, {"a.py::foo": b"body0"}, provenance=("sha1",))
    b = make_op({"a.py::foo": (None, "v0")}, {"a.py::foo": b"body0"}, provenance=("sha2",))
    assert a.id == b.id  # provenance differs; content-address doesn't care


def test_add_then_get_roundtrips(tmp_path):
    store = Store(tmp_path)
    store.init()
    op = _op()
    store.add(op)
    fetched = store.get(op.id)
    assert fetched == op


def test_get_missing_returns_none(tmp_path):
    store = Store(tmp_path)
    store.init()
    assert store.get("does-not-exist") is None


def test_add_rejects_op_whose_id_does_not_match_its_content(tmp_path):
    store = Store(tmp_path)
    store.init()
    op = _op()
    tampered = _op()
    object.__setattr__(tampered, "id", "0" * 64)  # forge an id that doesn't hash to its content
    with pytest.raises(StoreError):
        store.add(tampered)
    assert store.get(op.id) is None


def test_add_is_idempotent_and_unions_provenance(tmp_path):
    """Re-mining the same content (e.g. a squash/rebase re-mine, R8) appends a witness rather
    than duplicating or overwriting the stored op."""
    store = Store(tmp_path)
    store.init()
    first = make_op({"a.py::foo": (None, "v0")}, {"a.py::foo": b"body0"}, provenance=("sha1",))
    second = make_op({"a.py::foo": (None, "v0")}, {"a.py::foo": b"body0"}, provenance=("sha2",))
    assert first.id == second.id

    store.add(first)
    merged = store.add(second)
    assert merged.provenance == ("sha1", "sha2")
    assert store.get(first.id).provenance == ("sha1", "sha2")
    assert len(store.all_ops()) == 1  # one file, not two


def test_all_ops_deterministic_order(tmp_path):
    store = Store(tmp_path)
    store.init()
    ops = [_op(sym=f"a.py::f{i}", n=i) for i in range(5)]
    for op in ops:
        store.add(op)
    ids = [op.id for op in store.all_ops()]
    assert ids == sorted(ids)


def test_write_atomic_never_leaves_a_torn_file_on_crash(tmp_path, monkeypatch):
    """Simulates a crash between write and rename: os.replace failing must leave neither a
    torn destination file nor a leftover temp file."""
    import sgt.core.store as store_mod

    store = Store(tmp_path)
    store.init()
    op = _op()

    real_replace = os.replace

    def _boom(src, dst):
        raise OSError("simulated crash mid-write")

    monkeypatch.setattr(store_mod.os, "replace", _boom)
    with pytest.raises(OSError):
        store.add(op)
    monkeypatch.setattr(store_mod.os, "replace", real_replace)

    assert store.get(op.id) is None
    leftover_tmp = [p for p in store.ops_dir.iterdir() if p.name.startswith(".tmp-")]
    assert leftover_tmp == []


def test_concurrent_adds_all_land_without_corruption(tmp_path):
    store = Store(tmp_path)
    store.init()
    ops = [_op(sym=f"a.py::f{i}", n=i) for i in range(20)]

    threads = [threading.Thread(target=store.add, args=(op,)) for op in ops]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    stored = {op.id: op for op in store.all_ops()}
    assert len(stored) == len(ops)
    for op in ops:
        assert stored[op.id] == op


def test_hollow_op_roundtrips_with_empty_images(tmp_path):
    store = Store(tmp_path)
    store.init()
    hollow = make_op({"a.py::planned": (None, "predicted")}, {}, off_chain=True, intent="plan: add caching")
    store.add_hollow(hollow)

    fetched = store.get_hollow(hollow.id)
    assert fetched == hollow
    assert fetched.off_chain is True
    assert fetched.images == {}
    assert hollow.id not in store  # never lands in the main chain
    assert store.all_hollow_ops() == [hollow]


def test_add_hollow_rejects_a_normal_op(tmp_path):
    store = Store(tmp_path)
    store.init()
    with pytest.raises(StoreError):
        store.add_hollow(_op())


def test_fsck_clean_store(tmp_path):
    store = Store(tmp_path)
    store.init()
    store.add(_op())
    report = fsck(tmp_path)
    assert report == FsckReport(ok=True, checked=1, bad_hash=(), corrupt=())


def test_fsck_detects_bit_flipped_op_file(tmp_path):
    store = Store(tmp_path)
    store.init()
    op = _op()  # images = {"a.py::foo": b"body0"}, stored hex-encoded
    store.add(op)

    path = store.ops_dir / op.id
    text = path.read_text(encoding="utf-8")
    image_hex = b"body0".hex()
    assert image_hex in text
    # Flip one hex digit of the stored image -- still valid JSON, but the content no longer
    # hashes to the filename.
    flipped_digit = "1" if image_hex[0] != "1" else "2"
    text = text.replace(image_hex, flipped_digit + image_hex[1:], 1)
    path.write_text(text, encoding="utf-8")

    report = fsck(tmp_path)
    assert not report.ok
    assert report.checked == 1
    assert op.id in report.bad_hash


def test_fsck_reports_corrupt_json(tmp_path):
    store = Store(tmp_path)
    store.init()
    (store.ops_dir / "not-valid-json").write_bytes(b"{ this is not json")

    report = fsck(tmp_path)
    assert not report.ok
    assert "not-valid-json" in report.corrupt
