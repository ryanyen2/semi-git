"""Tests for `ugh.state` -- the `.sgt/` layout registry and schema envelope (plan U17, D3/C2).

Covers: the registry/codec round-trips both the pre-U17 (v0, no envelope) and the post-U17 (v1,
`{"schema": 1, "data": ...}`) shape; the historical-blob dispatch (`load_blob_json`) that `sync`
relies on to read a teammate's arbitrary-vintage committed metadata; and that a v0-shaped `.sgt/`
tree (built by hand-writing the exact pre-U17 byte format, since no real checkout of this repo's
own history has ever committed `.sgt/` -- it's gitignored here) round-trips.
"""

from __future__ import annotations

import json
import subprocess

from ugh import state
from ugh.store.gitbind import GitBinding


def _init_repo(root):
    subprocess.run(["git", "init", "-q", "-b", "main", str(root)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "ugh-test"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "test@ugh.local"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(root), "config", "commit.gpgsign", "false"], check=True, capture_output=True)


def _commit_all(root, message):
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(root), "commit", "-q", "-m", message], check=True, capture_output=True)
    return subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()


# -- registry / path plumbing --------------------------------------------------------------------


def test_path_and_rel_agree_on_layout(tmp_path):
    assert state.path(tmp_path, "intent_prompts") == tmp_path / ".sgt" / "intent" / "prompts.json"
    assert state.rel("intent_prompts") == ".sgt/intent/prompts.json"
    assert state.path(tmp_path, "oracle_config") == tmp_path / ".sgt" / "oracle.json"


def test_missing_artifact_returns_default(tmp_path):
    assert state.load_json(tmp_path, "names") is None
    assert state.load_json(tmp_path, "names", default={}) == {}


# -- schema envelope: v0 (pre-U17) and v1 (enveloped) round-trip --------------------------------


def test_writer_emits_versioned_envelope(tmp_path):
    state.save_json(tmp_path, "names", [["op_a", "op_b"]])
    raw = json.loads(state.path(tmp_path, "names").read_text(encoding="utf-8"))
    assert raw == {"schema": 1, "data": [["op_a", "op_b"]]}


def test_reader_accepts_v0_shape_with_no_envelope(tmp_path):
    """The exact pre-U17 byte format: the parsed JSON *is* the body, no `schema` key at all. Every
    real repo's committed history predates this unit, so this is the shape `sync` must keep reading
    forever (D3)."""
    p = state.path(tmp_path, "names")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"op-1": "checkout"}) + "\n", encoding="utf-8")

    body = state.load_json(tmp_path, "names")
    assert body == {"op-1": "checkout"}


def test_reader_accepts_v1_enveloped_shape(tmp_path):
    p = state.path(tmp_path, "names")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"schema": 1, "data": {"op-1": "billing"}}) + "\n", encoding="utf-8")

    assert state.load_json(tmp_path, "names") == {"op-1": "billing"}


def test_bare_list_body_is_not_mistaken_for_an_envelope(tmp_path):
    """A committed artifact whose body is a bare list (any table), not a dict -- `_unwrap`
    must not choke trying to call `.get` on it."""
    state.save_json(tmp_path, "names", [["op_a", "op_b"], ["op_c", "op_d"]])
    assert state.load_json(tmp_path, "names") == [["op_a", "op_b"], ["op_c", "op_d"]]

    p = state.path(tmp_path, "names")
    p.write_text(json.dumps([["op_x", "op_y"]]) + "\n", encoding="utf-8")  # hand-written v0
    assert state.load_json(tmp_path, "names") == [["op_x", "op_y"]]


# -- historical-blob dispatch: the `sync` read path ----------------------------------------------


def test_load_blob_json_reads_v0_and_v1_from_different_historical_commits(tmp_path):
    """The scenario `sync` actually hits: a teammate's ref tip can be any vintage. One commit
    carries the pre-U17 (v0) shape, a later commit carries the v1 envelope -- `load_blob_json` must
    parse both correctly, from their own historical SHA, without touching the working tree."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    gb = GitBinding(repo)

    names_file = repo / ".sgt" / "names.json"
    names_file.parent.mkdir(parents=True)
    names_file.write_text(
        json.dumps({"op-1": "checkout"}) + "\n",
        encoding="utf-8",
    )
    v0_sha = _commit_all(repo, "v0 names")

    names_file.write_text(
        json.dumps({"schema": 1, "data": {"op-1": "checkout", "op-2": "billing"}}) + "\n",
        encoding="utf-8",
    )
    v1_sha = _commit_all(repo, "v1 names")

    body_v0 = state.load_blob_json(gb, v0_sha, "names")
    body_v1 = state.load_blob_json(gb, v1_sha, "names")
    assert body_v0 == {"op-1": "checkout"}
    assert body_v1 == {"op-1": "checkout", "op-2": "billing"}


def test_load_blob_json_missing_artifact_returns_default(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "main.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    sha = _commit_all(repo, "init")

    gb = GitBinding(repo)
    assert state.load_blob_json(gb, sha, "names") is None
    assert state.load_blob_json(gb, sha, "names", default={}) == {}


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
    state.path(tmp_path, "names").parent.mkdir(parents=True, exist_ok=True)
    state.path(tmp_path, "names").write_text("{ truncated", encoding="utf-8")
    with pytest.raises(_json.JSONDecodeError):
        state.load_json(tmp_path, "names", default=[])


def test_concurrent_locked_section_writers_lose_no_update(tmp_path):
    """R5 (mirrors U23's concurrent-add methodology): two threads each do a locked read-modify-
    write appending to a shared local table. Under the store flock both appends survive; without
    it the last writer would clobber the other's."""
    import threading
    from ugh.core.store import Store, locked_section
    Store(tmp_path).init()
    state.save_json(tmp_path, "plan", {"items": []})
    barrier = threading.Barrier(2)

    def _append(tag):
        barrier.wait()
        for _ in range(50):
            with locked_section(tmp_path):
                body = state.load_json(tmp_path, "plan", default={"items": []})
                body["items"].append(tag)
                state.save_json(tmp_path, "plan", body)

    a = threading.Thread(target=_append, args=("a",))
    b = threading.Thread(target=_append, args=("b",))
    a.start(); b.start(); a.join(); b.join()

    items = state.load_json(tmp_path, "plan")["items"]
    assert items.count("a") == 50 and items.count("b") == 50  # no lost update
