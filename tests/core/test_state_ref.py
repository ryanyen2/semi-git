"""Phase 1.2 Step 2: the `refs/sgt/state` transport layer, tested in isolation (nothing wires it in
yet). Covers the local-mirror <-> ref round trip, the no-op/advance behavior of the CAS boundary,
and the conflict-free content-addressed union the Step-6 push merge builds on."""

import pytest

from sgt import state
from sgt.core.sync import state_ref
from sgt.store.gitbind import init_store


def _seed_local(repo):
    """A minimal traveling state on disk: two op files, a couple of JSON tables, and one immutable
    file-set member under `.sgt/claims/`."""
    ops = repo / ".sgt" / "ops"
    ops.mkdir(parents=True, exist_ok=True)
    (ops / "op-aaa").write_bytes(b'{"id": "op-aaa"}\n')
    (ops / "op-bbb").write_bytes(b'{"id": "op-bbb"}\n')
    state.save_json(repo, "pins", {"f-0001": "auth"})
    state.save_json(repo, "declared_orset", {"adds": [], "tombstones": []})
    state.save_claim(repo, "key.runner.json", {"verdict": "green"})


def test_publish_then_read_tree_carries_the_traveling_state(tmp_path):
    gb, _ = init_store(tmp_path)
    _seed_local(tmp_path)

    sha = state_ref.publish_from_local(gb, tmp_path)
    assert sha is not None
    assert gb.rev_parse(state_ref.STATE_REF) == sha

    tree = state_ref.read_tree(gb)
    assert set(tree) == {
        ".sgt/ops/op-aaa",
        ".sgt/ops/op-bbb",
        ".sgt/pins/pins.json",
        ".sgt/declared_edges.json",
        ".sgt/claims/key.runner.json",
    }
    assert tree[".sgt/ops/op-aaa"] == b'{"id": "op-aaa"}\n'


def test_config_and_local_sidecars_do_not_travel(tmp_path):
    """`oracle.json`/`identity_constraints.json`/`tiers.json` stay in the branch tree, and anything
    under `.sgt/local/` stays local -- none of them appears on the ref."""
    gb, _ = init_store(tmp_path)
    _seed_local(tmp_path)
    state.save_json(tmp_path, "oracle_config", {"model": "x"})
    state.save_json(tmp_path, "tiers", {"entity": []})
    state.save_json(tmp_path, "witness", {"main": "abc"})  # local sidecar

    tree = state_ref.read_tree(gb, state_ref.publish_from_local(gb, tmp_path))
    assert ".sgt/oracle.json" not in tree
    assert ".sgt/tiers.json" not in tree
    assert not any(p.startswith(".sgt/local/") for p in tree)


def test_read_sha_and_read_tree_are_empty_before_any_publish(tmp_path):
    gb, _ = init_store(tmp_path)
    assert state_ref.read_sha(gb) is None
    assert state_ref.read_tree(gb) == {}


def test_publish_is_a_noop_when_nothing_changed(tmp_path):
    """A second publish over an unchanged mirror returns the same tip and does not advance the ref
    -- these are `.sgt/**` paths a client's file watcher invalidates on, so ref churn matters."""
    gb, _ = init_store(tmp_path)
    _seed_local(tmp_path)

    first = state_ref.publish_from_local(gb, tmp_path)
    second = state_ref.publish_from_local(gb, tmp_path)
    assert second == first
    assert gb.rev_parse(state_ref.STATE_REF) == first


def test_publish_advances_the_ref_when_a_table_changes(tmp_path):
    gb, _ = init_store(tmp_path)
    _seed_local(tmp_path)
    first = state_ref.publish_from_local(gb, tmp_path)

    state.save_json(tmp_path, "pins", {"f-0001": "auth", "f-0002": "sync"})
    second = state_ref.publish_from_local(gb, tmp_path)

    assert second != first
    assert gb.parent_of(second) == first  # a new tip chained off the old one
    assert state_ref.read_tree(gb)[".sgt/pins/pins.json"] != b""


def test_materialize_round_trips_the_mirror(tmp_path):
    """publish then wipe the local tables and op files then materialize -> the mirror is restored
    byte-for-byte from the ref."""
    gb, _ = init_store(tmp_path)
    _seed_local(tmp_path)
    before = state_ref._local_blobs(tmp_path)
    state_ref.publish_from_local(gb, tmp_path)

    # wipe the local mirror's traveling files
    for rel_path in before:
        (tmp_path / rel_path).unlink()

    state_ref.materialize_into_local(gb, tmp_path)
    assert state_ref._local_blobs(tmp_path) == before


def test_union_content_addressed_is_a_conflict_free_idempotent_union(tmp_path):
    ours = {
        ".sgt/ops/op-aaa": b"a",
        ".sgt/claims/k1.json": b"c1",
        ".sgt/pins/pins.json": b"OURS",  # a mutable table -- must be left to the field-level merge
    }
    theirs = {
        ".sgt/ops/op-aaa": b"a",  # shared content-addressed path: identical bytes by construction
        ".sgt/ops/op-ccc": b"c",  # only theirs has it
        ".sgt/proposals/p1.json": b"prop",
        ".sgt/pins/pins.json": b"THEIRS",
    }
    merged = state_ref._union_content_addressed(ours, theirs)

    assert merged[".sgt/ops/op-ccc"] == b"c"  # theirs' content-addressed path is folded in
    assert merged[".sgt/proposals/p1.json"] == b"prop"
    assert merged[".sgt/pins/pins.json"] == b"OURS"  # mutable table left untouched by this half
    # idempotent: merging with a subset (or itself) changes nothing.
    assert state_ref._union_content_addressed(merged, theirs) == merged
    assert state_ref._union_content_addressed(merged, merged) == merged


def test_publish_raises_when_the_ref_cannot_advance(tmp_path, monkeypatch):
    """The ref is correctness-bearing: a final CAS failure raises rather than passing silently."""
    gb, _ = init_store(tmp_path)
    _seed_local(tmp_path)
    monkeypatch.setattr(gb, "update_ref_cas", lambda *a, **k: False)
    with pytest.raises(state_ref.StateRefError):
        state_ref.publish_from_local(gb, tmp_path)
