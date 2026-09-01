"""Tests for `sgt.intent.manifest` (capture weave P1, design doc 2026-09-01 §4b): the per-save
capture manifest -- window harvesting, non-overlapping chaining, write-once per sha, and the
footprint anchor. The save-beat integration (porcelain writing one) lives in tests/test_porcelain.py;
the MCP carry that feeds it lives in tests/mcp/test_server.py."""

from __future__ import annotations

from dataclasses import dataclass

from sgt.intent.activity import record_activity
from sgt.intent.manifest import load_manifests, record_manifest
from sgt.intent.turns import record_turn
from sgt.store.gitbind import init_store


@dataclass(frozen=True)
class _FakeOp:
    id: str
    footprint: dict


def _seed(tmp_path):
    gb, _ = init_store(tmp_path)
    (tmp_path / "a.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    gb.commit_all("base")
    return gb


def test_record_manifest_harvests_only_the_window(tmp_path):
    """Turns and events at or before the window's start belong to an earlier save (or pre-weave
    history) and must not be re-harvested; everything in (start, end] is copied in."""
    _seed(tmp_path)
    record_turn(tmp_path, key="cs-1", key_kind="chat", actor="human", channel="hook",
                text="ancient history", ts=5.0)
    record_turn(tmp_path, key="cs-1", key_kind="chat", actor="human", channel="hook",
                text="make foo faster", ts=20.0)
    record_activity(tmp_path, tool="Edit", file="a.py", session_id="cs-1", ts=6.0)
    record_activity(tmp_path, tool="Edit", file="a.py", session_id="cs-1", ts=21.0)

    op = _FakeOp(id="op-1", footprint={"a.py::foo": ("v0", "v1")})
    rec = record_manifest(tmp_path, sha="c1" * 20, ops=[op], end=30.0, prev_save_ts=10.0)

    assert rec["start"] == 10.0 and rec["end"] == 30.0
    assert [t["text"] for t in rec["turns"]] == ["make foo faster"]
    assert [e["ts"] for e in rec["events"]] == [21.0]
    assert rec["ops"] == [{"id": "op-1", "symbols": ["a.py::foo"]}]


def test_windows_chain_without_overlap(tmp_path):
    """The second manifest starts where the first ended -- the watermark is the store itself, so a
    turn is harvested by exactly one save and `prev_save_ts` only matters for the first window."""
    _seed(tmp_path)
    record_turn(tmp_path, key="cs-1", key_kind="chat", actor="human", channel="hook",
                text="first ask", ts=20.0)
    record_turn(tmp_path, key="cs-1", key_kind="chat", actor="human", channel="hook",
                text="second ask", ts=40.0)

    first = record_manifest(tmp_path, sha="c1" * 20, ops=[], end=30.0, prev_save_ts=10.0)
    second = record_manifest(tmp_path, sha="c2" * 20, ops=[], end=50.0)

    assert [t["text"] for t in first["turns"]] == ["first ask"]
    assert second["start"] == 30.0
    assert [t["text"] for t in second["turns"]] == ["second ask"]


def test_record_manifest_is_write_once_per_sha(tmp_path):
    """A retried save must not harvest a second, different window under the same key: the second
    call returns the existing record untouched, mirroring `intent_prompts`' write-once rule."""
    _seed(tmp_path)
    first = record_manifest(tmp_path, sha="c1" * 20, ops=[], end=30.0, prev_save_ts=10.0)
    record_turn(tmp_path, key="cs-1", key_kind="chat", actor="human", channel="hook",
                text="arrived between the calls", ts=25.0)
    again = record_manifest(tmp_path, sha="c1" * 20, ops=[], end=99.0)

    assert again == first and again["turns"] == []
    assert len(load_manifests(tmp_path)) == 1


def test_record_manifest_with_an_empty_sha_is_a_no_op(tmp_path):
    _seed(tmp_path)
    assert record_manifest(tmp_path, sha="", ops=[], end=30.0) is None
    assert load_manifests(tmp_path) == {}
