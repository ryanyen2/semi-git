"""Replica identity: stable id, crash-safe monotonic counter, eid parse."""

from __future__ import annotations

from sgt.store.replica import ReplicaIdentity


def test_fresh_identity_is_stable_across_reload(tmp_path):
    a = ReplicaIdentity.load_or_create(tmp_path)
    rid = a.replica_id
    b = ReplicaIdentity.load_or_create(tmp_path)
    assert b.replica_id == rid  # persisted, not re-minted


def test_mint_is_monotonic_and_survives_reload(tmp_path):
    a = ReplicaIdentity.load_or_create(tmp_path)
    e0, e1 = a.mint(), a.mint()
    assert e0 != e1
    # a fresh load must not reuse counters already minted
    b = ReplicaIdentity.load_or_create(tmp_path)
    e2 = b.mint()
    assert len({e0, e1, e2}) == 3
    _, n0 = ReplicaIdentity.parse(e0)
    _, n2 = ReplicaIdentity.parse(e2)
    assert n2 > n0


def test_distinct_stores_never_share_replica_id(tmp_path):
    a = ReplicaIdentity.load_or_create(tmp_path / "a")
    b = ReplicaIdentity.load_or_create(tmp_path / "b")
    assert a.replica_id != b.replica_id


def test_injected_replica_id_for_determinism(tmp_path):
    a = ReplicaIdentity.load_or_create(tmp_path, replica_id="R1")
    assert a.mint() == "R1:0"
    assert a.mint() == "R1:1"


def test_parse_round_trips_and_tolerates_legacy(tmp_path):
    a = ReplicaIdentity.load_or_create(tmp_path, replica_id="abc")
    eid = a.mint()
    assert ReplicaIdentity.parse(eid) == ("abc", 0)
    # legacy / empty ids sort before authored ones
    assert ReplicaIdentity.parse("") == ("", -1)
    assert ReplicaIdentity.parse("nocolon") == ("nocolon", -1)
