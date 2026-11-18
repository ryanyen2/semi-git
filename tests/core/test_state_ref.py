"""Phase 1.2 Step 2: the `refs/ugh/state` transport layer, tested in isolation. Covers the
local-mirror -> ref publish and the no-op/advance behavior of the CAS boundary."""

import pytest

from ugh import state
from ugh.core.sync import state_ref
from ugh.store.gitbind import GitBinding, init_store
from tests.conftest import _clone, _init_bare


def _seed_local(repo):
    """A minimal traveling state on disk: two op files and a JSON table."""
    ops = repo / ".sgt" / "ops"
    ops.mkdir(parents=True, exist_ok=True)
    (ops / "op-aaa").write_bytes(b'{"id": "op-aaa"}\n')
    (ops / "op-bbb").write_bytes(b'{"id": "op-bbb"}\n')
    state.save_json(repo, "names", {"op-aaa": "auth"})


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
        ".sgt/names.json",
    }
    assert tree[".sgt/ops/op-aaa"] == b'{"id": "op-aaa"}\n'


def test_config_and_local_sidecars_do_not_travel(tmp_path):
    """`oracle.json`/`tiers.json` stay in the branch tree, and anything
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

    state.save_json(tmp_path, "names", {"op-aaa": "auth", "op-bbb": "sync"})
    second = state_ref.publish_from_local(gb, tmp_path)

    assert second != first
    assert gb.parent_of(second) == first  # a new tip chained off the old one
    assert state_ref.read_tree(gb)[".sgt/names.json"] != b""


def test_publish_round_trips_the_mirror_byte_for_byte(tmp_path):
    """Every traveling blob on disk comes back from the ref byte-for-byte."""
    gb, _ = init_store(tmp_path)
    _seed_local(tmp_path)
    before = state_ref._local_blobs(tmp_path)
    state_ref.publish_from_local(gb, tmp_path)
    assert state_ref.read_tree(gb) == before


def test_publish_raises_when_the_ref_cannot_advance(tmp_path, monkeypatch):
    """The ref is correctness-bearing: a final CAS failure raises rather than passing silently."""
    gb, _ = init_store(tmp_path)
    _seed_local(tmp_path)
    monkeypatch.setattr(gb, "update_ref_cas", lambda *a, **k: False)
    with pytest.raises(state_ref.StateRefError):
        state_ref.publish_from_local(gb, tmp_path)


# -- publish_and_push: transport with the §D push-ordering / CRDT-reconcile invariant --------------

def _seed_ops(repo, ops: dict[str, bytes]) -> None:
    d = repo / ".sgt" / "ops"
    d.mkdir(parents=True, exist_ok=True)
    for name, content in ops.items():
        (d / name).write_bytes(content)


def test_publish_and_push_fast_forwards_a_fresh_remote(tmp_path):
    """The clean case: no contention, so the local mirror publishes and pushes fast-forward, and a
    fresh clone reads the ops back off the shared ref."""
    remote = _init_bare(tmp_path)
    a = _clone(remote, tmp_path / "a")
    _seed_ops(a, {"op-a1": b'{"id":"op-a1"}\n'})

    tip = state_ref.publish_and_push(GitBinding(a), a, "origin")
    assert tip == GitBinding(a).rev_parse(state_ref.STATE_REF)

    c = _clone(remote, tmp_path / "c")  # `_clone` fetches + materializes the state ref
    assert ".sgt/ops/op-a1" in state_ref.read_tree(GitBinding(c))


def test_publish_and_push_reconciles_a_non_ff_remote(tmp_path):
    """Contention: `b`'s local ref is behind the remote (a teammate advanced it), so the push is
    rejected non-ff. `publish_and_push` must fetch the remote tip, CRDT-merge it, and re-push a
    fast-forward -- so every side's ops survive the union on the remote."""
    remote = _init_bare(tmp_path)
    a = _clone(remote, tmp_path / "a")
    _seed_ops(a, {"op-a1": b"a1"})
    state_ref.publish_and_push(GitBinding(a), a, "origin")

    b = _clone(remote, tmp_path / "b")  # b's local state ref pins to a's tip
    _seed_ops(a, {"op-a2": b"a2"})
    state_ref.publish_and_push(GitBinding(a), a, "origin")  # remote moves behind b's back

    _seed_ops(b, {"op-b1": b"b1"})
    state_ref.publish_and_push(GitBinding(b), b, "origin")  # must reconcile, not clobber

    c = _clone(remote, tmp_path / "c")
    tree = state_ref.read_tree(GitBinding(c))
    assert {".sgt/ops/op-a1", ".sgt/ops/op-a2", ".sgt/ops/op-b1"} <= set(tree)


def test_publish_and_push_raises_when_the_push_can_never_succeed(tmp_path, monkeypatch):
    """An unrecoverable push (the network is down, say -- the ref push keeps failing and there is
    nothing to reconcile against) exhausts the retries and raises, so the caller aborts the branch
    push rather than leaving its trailers dangling."""
    remote = _init_bare(tmp_path)
    a = _clone(remote, tmp_path / "a")
    _seed_ops(a, {"op-a1": b"a1"})
    gb = GitBinding(a)
    monkeypatch.setattr(gb, "push_ref", lambda *args, **kwargs: False)

    with pytest.raises(state_ref.StateRefError):
        state_ref.publish_and_push(gb, a, "origin", retries=2)
