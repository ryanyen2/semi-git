"""Tests for sgt.core.store -- the content-addressed op store (plan U3, R1/R11/R12)."""

from __future__ import annotations

import json
import os
import threading

import pytest

from sgt.core.op import Attribution, make_op
from sgt.core.store import FsckReport, Store, StoreError, _serialize, fsck


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


def _same_op_provenance_worker(repo: str, tag: str, n: int) -> None:
    """Add the same content-addressed op `n` times, each add carrying a distinct provenance sha --
    the read-modify-write union `add()` runs under `.sgt/lock`. Module-level so `multiprocessing`
    (spawn on macOS) can pickle it."""
    store = Store(repo)
    for i in range(n):
        op = make_op(
            {"shared.py::sym": (None, "v0")}, {"shared.py::sym": b"same"},
            provenance=(f"{tag}{i:04d}",),
        )
        store.add(op)


def test_concurrent_same_op_adds_across_processes_lose_no_provenance(tmp_path):
    """U23 store-lock audit, encoded as a regression guard: two *separate OS processes* each add the
    same op id many times, each add with a distinct witness sha. The per-`add()` exclusive flock
    protects the provenance-union read-modify-write, so every witness survives -- no lost update.
    This is the finding that says `land` needs a branch-record CAS (not a wider store lock): the
    single-writer lock is already correct for op appends."""
    import multiprocessing as mp

    store = Store(tmp_path)
    store.init()
    n = 150
    procs = [
        mp.Process(target=_same_op_provenance_worker, args=(str(tmp_path), tag, n))
        for tag in ("P1", "P2")
    ]
    for p in procs:
        p.start()
    for p in procs:
        p.join()
        assert p.exitcode == 0

    ops = store.all_ops()
    assert len(ops) == 1  # one content-addressed file, not many
    got = set(ops[0].provenance)
    expected = {f"{tag}{i:04d}" for tag in ("P1", "P2") for i in range(n)}
    assert got == expected  # all 2n witnesses present -- none dropped by a lost update
    assert fsck(tmp_path).ok


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
    # op_index_stale is True here: this test writes via `Store.add` directly, never through
    # `sgt.core.lens`'s `_sync` (the only writer that keeps the opindex sidecar current) -- an
    # advisory finding, not corruption, so `ok` stays True.
    assert report == FsckReport(ok=True, checked=1, bad_hash=(), corrupt=(), op_index_stale=True)


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


# -- structured provenance codec (plan U22, D7) ------------------------------------------------

def _v0_bytes(op, provenance: list[str]) -> bytes:
    """A pre-U22 op file: `provenance` is a flat list of SHA strings (the shape every committed
    repo carries today). Built from `op`'s real content so it still hashes to `op.id`."""
    payload = {
        "id": op.id,
        "footprint": {k: list(v) for k, v in sorted(op.footprint.items())},
        "images": {k: (v.hex() if v is not None else None) for k, v in sorted(op.images.items())},
        "requires": [list(r) for r in sorted(op.requires)],
        "kind": op.kind,
        "provenance": provenance,
        "intent": op.intent,
        "miner_version": op.miner_version,
        "off_chain": op.off_chain,
    }
    return json.dumps(payload, indent=2, sort_keys=True).encode("utf-8") + b"\n"


def test_v1_attribution_roundtrips_through_the_store(tmp_path):
    """An op with structured attribution serializes to the v1 shape and deserializes back equal."""
    store = Store(tmp_path)
    store.init()
    op = make_op(
        {"a.py::foo": (None, "v0")}, {"a.py::foo": b"body"},
        provenance=("shaA", "shaB"),
        attribution=(Attribution(sha="shaA", session="s1", agent="claude"),),
    )
    store.add(op)

    fetched = store.get(op.id)
    assert fetched == op
    assert fetched.provenance == ("shaA", "shaB")
    assert fetched.attribution == (Attribution(sha="shaA", session="s1", agent="claude"),)


def test_v0_file_deserializes_with_empty_attribution(tmp_path):
    """A committed v0 op file (list-of-strings provenance) reads back with `attribution == ()` and
    its provenance intact -- old shapes live in history forever (D3)."""
    store = Store(tmp_path)
    store.init()
    op = _op()
    (store.ops_dir / op.id).write_bytes(_v0_bytes(op, ["sha0", "sha1"]))

    fetched = store.get(op.id)
    assert fetched.provenance == ("sha0", "sha1")
    assert fetched.attribution == ()


def test_add_bytes_unions_a_v0_and_a_v1_file_for_the_same_id(tmp_path):
    """The D7 pitfall: two clones hold a v0 and a v1 file for the *same* op id. `add`/`add_bytes`
    unions on collision, so the union must merge shapes (not compare bytes) -- provenance grows and
    the v1 file's attribution survives."""
    store = Store(tmp_path)
    store.init()
    op = make_op({"a.py::foo": (None, "v0")}, {"a.py::foo": b"body"})

    # a v0 file already on disk: provenance ["shaA"], no attribution
    (store.ops_dir / op.id).write_bytes(_v0_bytes(op, ["shaA"]))

    # a v1 file for the same id arrives (as raw bytes, e.g. from a teammate's clone via sync)
    v1 = make_op(
        {"a.py::foo": (None, "v0")}, {"a.py::foo": b"body"},
        provenance=("shaB",), attribution=(Attribution(sha="shaB", session="s1"),),
    )
    merged = store.add_bytes(_serialize(v1))

    assert merged.provenance == ("shaA", "shaB")
    assert merged.attribution == (Attribution(sha="shaB", session="s1"),)
    # ...and persisted, not just returned
    persisted = store.get(op.id)
    assert persisted.provenance == ("shaA", "shaB")
    assert persisted.attribution == (Attribution(sha="shaB", session="s1"),)


def test_attribute_stamps_a_committed_op_without_moving_its_id(tmp_path):
    store = Store(tmp_path)
    store.init()
    op = store.add(_op())  # provenance ("sha0",), no attribution

    updated = store.attribute(op.id, (Attribution(sha="sha0", session="s1"),))

    assert updated.id == op.id  # id is content-addressed; attribution is not part of it
    assert updated.attribution == (Attribution(sha="sha0", session="s1"),)
    assert store.get(op.id).attribution == (Attribution(sha="sha0", session="s1"),)
    assert fsck(tmp_path).ok  # the rewritten file still hashes to its filename


def test_attribute_returns_none_for_an_unknown_op(tmp_path):
    store = Store(tmp_path)
    store.init()
    assert store.attribute("does-not-exist", (Attribution(sha="x", session="s1"),)) is None


def test_provenance_roundtrip_preserves_op_ids_across_a_corpus(tmp_path):
    """The U22 verification (D7 proven end to end): mine a real corpus repo, then enrich every op's
    structured provenance. No op id moves -- provenance/attribution are outside the content address
    -- and the store stays fsck-clean."""
    from sgt.core.lens import get
    from tests.laws import corpus

    repo = corpus.CORPUS["mixed_coverage"].build(tmp_path / "repo")
    get(repo)  # mine-on-contact populates .sgt/ops/
    store = Store(repo)
    before = sorted(op.id for op in store.all_ops())
    assert before  # the fixture actually produced ops

    for op in store.all_ops():
        for sha in op.provenance:
            store.attribute(op.id, (Attribution(sha=sha, session="s1"),))

    after = sorted(op.id for op in store.all_ops())
    assert after == before
    assert fsck(repo).ok


# -- U2: fsck completion (R11 contract) + read-side corruption tolerance ----------------------


def test_all_ops_skips_corrupt_file_instead_of_raising(tmp_path):
    """R1: a corrupt op file degrades to a read-side skip so every verb still runs; fsck is the
    one place that reports it. Before U2 `all_ops` raised, crashing `sgt status` on any repo with
    a single truncated op file."""
    store = Store(tmp_path)
    store.init()
    good = _op()
    store.add(good)
    (store.ops_dir / "truncated").write_bytes(b"{ not json")

    ops = store.all_ops()
    assert [o.id for o in ops] == [good.id]  # the good op survives, the corrupt one is skipped


def test_fsck_reports_chain_gap_naming_the_symbol(tmp_path):
    """R11 linearity: an op whose non-None before_version is produced by no op in the store is a
    chain gap. fsck names the symbol@version. Advisory only -- real histories carry benign
    off-ref-predecessor gaps, so a gap does not by itself flip `ok`."""
    store = Store(tmp_path)
    store.init()
    store.add(make_op({"a.py::foo": (None, "v0")}, {"a.py::foo": b"b0"}, provenance=("s0",)))
    # v1 was produced by nobody -- a gap in a.py::foo's chain
    store.add(make_op({"a.py::foo": ("v1", "v2")}, {"a.py::foo": b"b2"}, provenance=("s1",)))

    report = fsck(tmp_path)
    assert any("a.py::foo" in g for g in report.chain_gaps)


def test_fsck_reports_invalid_ideal_table_entry_with_ref_key(tmp_path):
    """R11 ideal validity: an ideal-table entry naming an op id no op in the store produces is not
    a valid ideal. fsck reports the ref key and flips `ok`."""
    from sgt import state
    store = Store(tmp_path)
    store.init()
    store.add(_op())
    state.save_json(tmp_path, "ideal_table", {"refs/heads/main": ["deadbeef-no-such-op"]})

    report = fsck(tmp_path)
    assert not report.ok
    assert "refs/heads/main" in report.invalid_ideals


def test_fsck_reports_unreachable_witness_with_ref_key(tmp_path):
    """R11 witness reachability: a witness SHA that no longer resolves (deleted branch, foreign
    clone) is reported against its ref key and flips `ok`; the remedy is prune-or-reseed."""
    from sgt import state
    from sgt.store.gitbind import init_store
    gb, _ = init_store(tmp_path)
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    gb.commit_all("seed")
    Store(tmp_path).add(_op())
    # a well-formed sha that is not an object in this repo (a deleted branch's tip)
    state.save_json(tmp_path, "witness", {"refs/heads/main": "1234567890abcdef1234567890abcdef12345678"})

    report = fsck(tmp_path)
    assert not report.ok
    assert "refs/heads/main" in report.unreachable_witnesses


# -- D5: structured resolution provenance -------------------------------------------------------

def test_resolves_roundtrips_through_the_store(tmp_path):
    store = Store(tmp_path)
    store.init()
    op = make_op(
        {"a.py::foo": (None, "v0")}, {"a.py::foo": b"body"},
        resolves=frozenset({"tip-a", "tip-b"}),
    )
    store.add(op)

    fetched = store.get(op.id)
    assert fetched == op
    assert fetched.resolves == frozenset({"tip-a", "tip-b"})


def test_payload_missing_resolves_key_deserializes_to_empty_frozenset(tmp_path):
    """A pre-D5 op file has no `resolves` key at all -- old shapes read back with `resolves ==
    frozenset()` rather than raising (the same `.get(..., default)` discipline as `off_chain`)."""
    store = Store(tmp_path)
    store.init()
    op = _op()
    payload = json.loads(_serialize(op))
    del payload["resolves"]
    (store.ops_dir / op.id).write_bytes(json.dumps(payload, indent=2, sort_keys=True).encode() + b"\n")

    fetched = store.get(op.id)
    assert fetched.resolves == frozenset()


def test_fsck_flags_mixed_miner_versions(tmp_path):
    """U10 backstop: a store containing ops from two miner versions is a mid-migration hazard.
    fsck lists the versions and flips `ok`. A single-version store reports nothing (mixed=())."""
    store = Store(tmp_path)
    store.init()
    store.add(make_op({"a.py::foo": (None, "v0")}, {"a.py::foo": b"b0"}, provenance=("s0",)))  # current (v3)
    from dataclasses import replace as _replace
    from sgt.core.store import _serialize
    # A leftover op minted by a *prior* miner version (v2), the mid-migration hazard fsck must flag.
    prior = make_op({"a.py::bar": (None, "v0")}, {"a.py::bar": b"b1"}, provenance=("s1",))
    prior = _replace(prior, miner_version="2")
    # re-mint id under the v2 version so the file still hashes to its own name
    from sgt.core.op import compute_id
    prior = _replace(prior, id=compute_id(prior.footprint, prior.images, prior.requires, prior.kind, "2"))
    (store.ops_dir / prior.id).write_bytes(_serialize(prior))

    report = fsck(tmp_path)
    assert not report.ok
    assert set(report.mixed_versions) == {"2", "3"}
