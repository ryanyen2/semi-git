"""Tests for `sgt.core.review` -- the trust queue's dequeue mechanism (plan U31, S7).

A review record marks an op-set reviewed, content-addressed by the sorted op-id set exactly like a
claim (D8) or a proposal (C10): re-acking the same set is a no-op on content, and the file *set*
is the artifact, so it travels to a syncing clone byte-for-byte (`materialize._union_reviews`).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sgt import state
from sgt.core import review
from sgt.core import sync
from sgt.store.gitbind import GitBinding
from tests.core.test_sync import _BASE, _edit_and_commit, _push, _two_clones


def test_ack_load_roundtrip_and_reack_is_idempotent(tmp_path):
    r1 = review.ack(tmp_path, ["op-b", "op-a"], scope="op-set:2 ops", note="looks fine")
    assert r1.op_ids == ("op-a", "op-b")  # sorted, deduped
    assert review.load(tmp_path, r1.id) == r1
    assert review.all_records(tmp_path) == [r1]

    r2 = review.ack(tmp_path, ["op-a", "op-b", "op-a"], scope="a different scope label", note=None)
    assert r2.id == r1.id  # same op-set -> same content-addressed id, regardless of scope/note
    assert review.all_records(tmp_path) == [r2]  # overwrites the one file, not a second record


def test_ack_rejects_an_empty_op_set(tmp_path):
    with pytest.raises(ValueError, match="empty"):
        review.ack(tmp_path, [], scope="op-set:0 ops")


def test_reviewed_op_ids_unions_across_records(tmp_path):
    review.ack(tmp_path, ["op-a", "op-b"], scope="session:s1")
    review.ack(tmp_path, ["op-c"], scope="session:s2")

    assert review.reviewed_op_ids(tmp_path) == frozenset({"op-a", "op-b", "op-c"})


def test_a_review_record_travels_to_a_syncing_clone(tmp_path):
    """G-Set travel: a committed review file unions to a syncing clone byte-for-byte, exactly like
    a claim/proposal."""
    a, b = _two_clones(tmp_path, _BASE)
    _edit_and_commit(a, "main.py", _BASE.replace("return 1", "return 42"), "rework foo")
    r = review.ack(a, ["op-a"], scope="op-set:1 ops", note="reviewed on A")
    GitBinding(a).commit_all("A: ack review")
    _push(a)

    assert state.list_review_files(b) == []  # nothing before syncing
    report = sync.sync(b, remote="origin", branch="main")
    assert report.merge_sha is not None

    assert state.list_review_files(b) == [f"{r.id}.json"]
    assert review.load(b, r.id) == r  # round-trips as an identical ReviewRecord on B
