"""`refs/ugh/state`: it carries the names table and the captured prompts, never ops."""

import pytest

from ugh import names, state
from ugh.core.sync import state_ref
from ugh.store.gitbind import GitBinding, init_store
from tests.conftest import _clone, _init_bare


def _seed_local(repo):
    """Traveling tables on disk, and an op file that must not travel."""
    ops = repo / ".sgt" / "ops"
    ops.mkdir(parents=True, exist_ok=True)
    (ops / "op-aaa").write_bytes(b'{"id": "op-aaa"}\n')
    state.save_json(repo, "names", {"op-aaa": "auth"})
    state.save_json(repo, "intent_prompts", {"sha1": "add auth"})


def test_publish_carries_names_and_prompts_and_no_ops(tmp_path):
    gb, _ = init_store(tmp_path)
    _seed_local(tmp_path)

    sha = state_ref.publish_from_local(gb, tmp_path)

    assert gb.rev_parse(state_ref.STATE_REF) == sha
    assert set(state_ref.read_tree(gb)) == {".sgt/names.json", ".sgt/intent/prompts.json"}


def test_config_and_local_sidecars_do_not_travel(tmp_path):
    gb, _ = init_store(tmp_path)
    _seed_local(tmp_path)
    state.save_json(tmp_path, "oracle_config", {"model": "x"})
    state.save_json(tmp_path, "tiers", {"entity": []})
    state.save_json(tmp_path, "witness", {"main": "abc"})

    tree = state_ref.read_tree(gb, state_ref.publish_from_local(gb, tmp_path))
    assert ".sgt/oracle.json" not in tree and ".sgt/tiers.json" not in tree
    assert not any(p.startswith(".sgt/local/") for p in tree)


def test_read_sha_and_read_tree_are_empty_before_any_publish(tmp_path):
    gb, _ = init_store(tmp_path)
    assert state_ref.read_sha(gb) is None
    assert state_ref.read_tree(gb) == {}


def test_publish_is_a_noop_when_nothing_changed(tmp_path):
    gb, _ = init_store(tmp_path)
    _seed_local(tmp_path)
    first = state_ref.publish_from_local(gb, tmp_path)
    assert state_ref.publish_from_local(gb, tmp_path) == first


def test_publish_advances_the_ref_when_a_table_changes(tmp_path):
    gb, _ = init_store(tmp_path)
    _seed_local(tmp_path)
    first = state_ref.publish_from_local(gb, tmp_path)

    state.save_json(tmp_path, "names", {"op-aaa": "auth", "op-bbb": "sync"})
    second = state_ref.publish_from_local(gb, tmp_path)

    assert second != first and gb.parent_of(second) == first
    assert state_ref.read_tree(gb) == state_ref._local_blobs(tmp_path)


def test_publish_raises_when_the_ref_cannot_advance(tmp_path, monkeypatch):
    gb, _ = init_store(tmp_path)
    _seed_local(tmp_path)
    monkeypatch.setattr(gb, "update_ref_cas", lambda *a, **k: False)
    with pytest.raises(state_ref.StateRefError):
        state_ref.publish_from_local(gb, tmp_path)


def test_publish_and_push_merges_a_remote_that_moved(tmp_path):
    """b's push is rejected because a published since b cloned; b merges a's names and prompts and
    pushes again, so the remote holds both sides'."""
    remote = _init_bare(tmp_path)
    a = _clone(remote, tmp_path / "a")
    names.save(a, {"op-a1": "auth"})
    state_ref.publish_and_push(GitBinding(a), a, "origin")

    b = _clone(remote, tmp_path / "b")
    names.save(a, {"op-a1": "auth", "op-a2": "login"})
    state.save_json(a, "intent_prompts", {"sha-a": "from a"})
    state_ref.publish_and_push(GitBinding(a), a, "origin")

    names.save(b, {"op-a1": "auth", "op-b1": "billing"})
    state.save_json(b, "intent_prompts", {"sha-b": "from b"})
    state_ref.publish_and_push(GitBinding(b), b, "origin")

    c = _clone(remote, tmp_path / "c")
    assert names.load(c) == {"op-a1": "auth", "op-a2": "login", "op-b1": "billing"}
    assert state.load_json(c, "intent_prompts", default={}) == {"sha-a": "from a", "sha-b": "from b"}


def test_publish_and_push_raises_when_the_push_can_never_succeed(tmp_path, monkeypatch):
    remote = _init_bare(tmp_path)
    a = _clone(remote, tmp_path / "a")
    names.save(a, {"op-a1": "auth"})
    gb = GitBinding(a)
    monkeypatch.setattr(gb, "push_ref", lambda *args, **kwargs: False)

    with pytest.raises(state_ref.StateRefError):
        state_ref.publish_and_push(gb, a, "origin", retries=2)
