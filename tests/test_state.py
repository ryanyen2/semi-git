"""Tests for `sgt.state` -- the `.sgt/` layout registry and schema envelope (plan U17, D3/C2).

Covers: the registry/codec round-trips both the pre-U17 (v0, no envelope) and the post-U17 (v1,
`{"schema": 1, "data": ...}`) shape; the historical-blob dispatch (`load_blob_json`) that `sync`
relies on to read a teammate's arbitrary-vintage committed metadata; and that a v0-shaped `.sgt/`
tree (built by hand-writing the exact pre-U17 byte format, since no real checkout of this repo's
own history has ever committed `.sgt/` -- it's gitignored here) round-trips through the kernel
verbs that read it.
"""

from __future__ import annotations

import json
import subprocess

from sgt import state
from sgt.core.lens import _load_declared, _save_declared
from sgt.lens.pins import Pins, load_pins, save_pins
from sgt.lens import tree
from sgt.store.gitbind import GitBinding


def _init_repo(root):
    subprocess.run(["git", "init", "-q", "-b", "main", str(root)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "sgt-test"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "test@semi-git.local"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(root), "config", "commit.gpgsign", "false"], check=True, capture_output=True)


def _commit_all(root, message):
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(root), "commit", "-q", "-m", message], check=True, capture_output=True)
    return subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()


# -- registry / path plumbing --------------------------------------------------------------------


def test_path_and_rel_agree_on_layout(tmp_path):
    assert state.path(tmp_path, "pins") == tmp_path / ".sgt" / "pins" / "pins.json"
    assert state.rel("pins") == ".sgt/pins/pins.json"
    assert state.path(tmp_path, "declared") == tmp_path / ".sgt" / "declared.json"
    # two distinct "oracle.json"s at different layers -- committed config vs. local verdict cache.
    assert state.path(tmp_path, "oracle_config") == tmp_path / ".sgt" / "oracle.json"
    assert state.path(tmp_path, "verdicts") == tmp_path / ".sgt" / "local" / "oracle.json"


def test_missing_artifact_returns_default(tmp_path):
    assert state.load_json(tmp_path, "pins") is None
    assert state.load_json(tmp_path, "pins", default={}) == {}


# -- schema envelope: v0 (pre-U17) and v1 (enveloped) round-trip --------------------------------


def test_writer_emits_versioned_envelope(tmp_path):
    state.save_json(tmp_path, "declared", [["op_a", "op_b"]])
    raw = json.loads(state.path(tmp_path, "declared").read_text(encoding="utf-8"))
    assert raw == {"schema": 1, "data": [["op_a", "op_b"]]}


def test_reader_accepts_v0_shape_with_no_envelope(tmp_path):
    """The exact pre-U17 byte format: the parsed JSON *is* the body, no `schema` key at all. Every
    real repo's committed history predates this unit, so this is the shape `sync` must keep reading
    forever (D3)."""
    p = state.path(tmp_path, "pins")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"assign": {"m1": "featureA"}, "must_link": [], "cannot_link": [], "labels": {}}) + "\n", encoding="utf-8")

    body = state.load_json(tmp_path, "pins")
    assert body == {"assign": {"m1": "featureA"}, "must_link": [], "cannot_link": [], "labels": {}}


def test_reader_accepts_v1_enveloped_shape(tmp_path):
    p = state.path(tmp_path, "pins")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"schema": 1, "data": {"assign": {"m1": "featureB"}}}) + "\n", encoding="utf-8")

    assert state.load_json(tmp_path, "pins") == {"assign": {"m1": "featureB"}}


def test_v0_and_v1_payloads_round_trip_through_the_same_caller(tmp_path):
    """A caller (`load_pins`/`save_pins`) shouldn't be able to tell whether the file on disk predates
    this unit or not."""
    save_pins(tmp_path, Pins(assign={"m1": "featureA"}))
    assert load_pins(tmp_path).assign == {"m1": "featureA"}

    # Overwrite with a hand-written v0 (no-envelope) payload -- the shape every real clone has today.
    p = state.path(tmp_path, "pins")
    p.write_text(
        json.dumps({"assign": {"m2": "featureB"}, "must_link": [], "cannot_link": [], "labels": {}}) + "\n",
        encoding="utf-8",
    )
    assert load_pins(tmp_path).assign == {"m2": "featureB"}


def test_declared_edges_v0_list_shape_is_not_mistaken_for_an_envelope(tmp_path):
    """`declared.json`'s body is a bare list, not a dict -- `_unwrap` must not choke trying to call
    `.get` on it."""
    edges = frozenset({("op_a", "op_b"), ("op_c", "op_d")})
    _save_declared(tmp_path, edges)
    assert _load_declared(tmp_path) == edges

    p = state.path(tmp_path, "declared")
    p.write_text(json.dumps([["op_x", "op_y"]]) + "\n", encoding="utf-8")  # hand-written v0
    assert _load_declared(tmp_path) == frozenset({("op_x", "op_y")})


# -- historical-blob dispatch: the `sync` read path ----------------------------------------------


def test_load_blob_json_reads_v0_and_v1_from_different_historical_commits(tmp_path):
    """The scenario `sync` actually hits: a teammate's ref tip can be any vintage. One commit
    carries the pre-U17 (v0) shape, a later commit carries the v1 envelope -- `load_blob_json` must
    parse both correctly, from their own historical SHA, without touching the working tree."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    gb = GitBinding(repo)

    pins_dir = repo / ".sgt" / "pins"
    pins_dir.mkdir(parents=True)
    (pins_dir / "pins.json").write_text(
        json.dumps({"assign": {"m1": "featureA"}, "must_link": [], "cannot_link": [], "labels": {}}) + "\n",
        encoding="utf-8",
    )
    v0_sha = _commit_all(repo, "v0 pins")

    (pins_dir / "pins.json").write_text(
        json.dumps({"schema": 1, "data": {"assign": {"m1": "featureA", "m2": "featureB"}}}) + "\n",
        encoding="utf-8",
    )
    v1_sha = _commit_all(repo, "v1 pins")

    body_v0 = state.load_blob_json(gb, v0_sha, "pins")
    body_v1 = state.load_blob_json(gb, v1_sha, "pins")
    assert body_v0 == {"assign": {"m1": "featureA"}, "must_link": [], "cannot_link": [], "labels": {}}
    assert body_v1 == {"assign": {"m1": "featureA", "m2": "featureB"}}


def test_load_blob_json_missing_artifact_returns_default(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "main.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    sha = _commit_all(repo, "init")

    gb = GitBinding(repo)
    assert state.load_blob_json(gb, sha, "pins") is None
    assert state.load_blob_json(gb, sha, "pins", default={}) == {}


# -- a v0-shaped `.sgt/` tree round-trips through the kernel verbs that read it -------------------


def test_a_v0_shaped_repo_round_trips_through_load_and_save(tmp_path):
    """Simulates a clone whose `.sgt/` predates this unit: every committed artifact hand-written in
    the exact pre-U17 shape (no envelope). The verbs that read them (`load_pins`, `_load_declared`,
    `tree.load`) must parse them exactly as before, and a subsequent save must not corrupt anything
    -- the round-trip this unit's whole `read-side-first` approach is built to guarantee (D3)."""
    repo = tmp_path / "repo"
    repo.mkdir()

    pins_dir = repo / ".sgt" / "pins"
    pins_dir.mkdir(parents=True)
    (pins_dir / "pins.json").write_text(
        json.dumps({"assign": {"m1": "featureA"}, "must_link": [["m2", "m3"]], "cannot_link": [], "labels": {}}) + "\n",
        encoding="utf-8",
    )
    (repo / ".sgt" / "declared.json").write_text(json.dumps([["op_a", "op_b"]]) + "\n", encoding="utf-8")
    tree_dir = repo / ".sgt" / "tree"
    tree_dir.mkdir(parents=True)
    tree_body = {"nodes": {"F1": {"members": ["main.py::foo"], "children": [], "parent": None, "depth": 0}}, "roots": ["F1"]}
    (tree_dir / "tree.json").write_text(json.dumps(tree_body, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    pins = load_pins(repo)
    assert pins.assign == {"m1": "featureA"}
    assert pins.must_link == frozenset({("m2", "m3")})

    assert _load_declared(repo) == frozenset({("op_a", "op_b")})
    assert tree.load(repo) == tree_body

    # A subsequent save (post-U17) upgrades the file to the versioned envelope, and a re-read
    # still recovers the identical logical body -- no silent corruption across the version bump.
    save_pins(repo, pins)
    assert load_pins(repo).assign == {"m1": "featureA"}
    raw = json.loads((pins_dir / "pins.json").read_text(encoding="utf-8"))
    assert raw["schema"] == 1


# -- U3: atomic writes + torn-file tolerance (R5/R6) ------------------------------------------


def test_save_json_is_atomic_leaving_no_torn_file_on_crash(tmp_path, monkeypatch):
    """R5: a crash mid-write leaves the *prior* file intact -- writes go to a temp file that is
    fsync'd and atomically renamed, never a partial overwrite of the live file."""
    import os
    state.save_json(tmp_path, "ideal_table", {"refs/heads/main": ["op-a"]})

    real_replace = os.replace

    def _boom(src, dst):
        raise RuntimeError("crash before rename")

    monkeypatch.setattr(os, "replace", _boom)
    try:
        state.save_json(tmp_path, "ideal_table", {"refs/heads/main": ["op-b"]})
    except RuntimeError:
        pass
    monkeypatch.setattr(os, "replace", real_replace)

    # the original bytes survived; no torn/partial file
    assert state.load_json(tmp_path, "ideal_table") == {"refs/heads/main": ["op-a"]}
    # and no leftover temp files in .sgt/local/
    leftovers = [p.name for p in (tmp_path / ".sgt" / "local").iterdir() if p.name.startswith(".tmp-")]
    assert leftovers == []


def test_torn_local_artifact_reseeds_instead_of_crashing(tmp_path):
    """R6: a torn *local* reseedable artifact (a crash before atomic writes shipped) degrades to
    the default so the next verb re-mines it, rather than crashing every read."""
    state.path(tmp_path, "ideal_table").parent.mkdir(parents=True, exist_ok=True)
    state.path(tmp_path, "ideal_table").write_text("{ truncated", encoding="utf-8")
    assert state.load_json(tmp_path, "ideal_table", default={}) == {}


def test_torn_committed_artifact_fails_loudly(tmp_path):
    """R6: a torn *committed* artifact is real shared-state corruption -- it re-raises to reach
    `fsck` loudly, never silently reseeds into a wrong team-visible state."""
    import json as _json
    import pytest
    state.path(tmp_path, "forks").parent.mkdir(parents=True, exist_ok=True)
    state.path(tmp_path, "forks").write_text("{ truncated", encoding="utf-8")
    with pytest.raises(_json.JSONDecodeError):
        state.load_json(tmp_path, "forks", default=[])


def test_concurrent_locked_section_writers_lose_no_update(tmp_path):
    """R5 (mirrors U23's concurrent-add methodology): two threads each do a locked read-modify-
    write appending to a shared local table. Under the store flock both appends survive; without
    it the last writer would clobber the other's."""
    import threading
    from sgt.core.store import Store, locked_section
    Store(tmp_path).init()
    state.save_json(tmp_path, "drafts", {"items": []})
    barrier = threading.Barrier(2)

    def _append(tag):
        barrier.wait()
        for _ in range(50):
            with locked_section(tmp_path):
                body = state.load_json(tmp_path, "drafts", default={"items": []})
                body["items"].append(tag)
                state.save_json(tmp_path, "drafts", body)

    a = threading.Thread(target=_append, args=("a",))
    b = threading.Thread(target=_append, args=("b",))
    a.start(); b.start(); a.join(); b.join()

    items = state.load_json(tmp_path, "drafts")["items"]
    assert items.count("a") == 50 and items.count("b") == 50  # no lost update
