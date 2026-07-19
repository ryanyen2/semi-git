"""Tests for `sgt.core.sync.log` -- the D1 append-only shared-branch land log.

Unit-level coverage of the module's own contract (append/read/lookup, ref-scoping by branch,
best-effort failure tolerance). Cross-module integration -- `land` writing an entry, `ingest`
reading one back as a recovery rung -- lives in `test_land.py`/`test_sync_stages.py`.
"""

from __future__ import annotations

from sgt.core.sync import log
from sgt.store.gitbind import GitBinding


def _sha(tag: str) -> str:
    """A well-formed-looking 40-hex sha, distinct per tag (these are just trailer payloads --
    `log` never dereferences them as real git objects)."""
    return (tag * 40)[:40]


def test_append_then_read_roundtrips_landed_sha_and_ideal(tmp_path):
    gb = GitBinding(tmp_path)
    gb.init()

    ok = log.append(gb, "main", _sha("a"), frozenset({"op1", "op2"}))
    assert ok

    entries = log.read(gb, "main")
    assert len(entries) == 1
    assert entries[0].landed_sha == _sha("a")
    assert entries[0].ideal_ids == frozenset({"op1", "op2"})


def test_read_on_a_branch_with_no_entries_is_empty(tmp_path):
    gb = GitBinding(tmp_path)
    gb.init()
    assert log.read(gb, "main") == []


def test_entries_are_scoped_per_branch(tmp_path):
    gb = GitBinding(tmp_path)
    gb.init()
    log.append(gb, "main", _sha("a"), frozenset({"op1"}))
    log.append(gb, "release", _sha("b"), frozenset({"op2"}))

    assert [e.landed_sha for e in log.read(gb, "main")] == [_sha("a")]
    assert [e.landed_sha for e in log.read(gb, "release")] == [_sha("b")]


def test_multiple_appends_chain_and_read_returns_newest_first(tmp_path):
    gb = GitBinding(tmp_path)
    gb.init()
    log.append(gb, "main", _sha("a"), frozenset({"op1"}))
    log.append(gb, "main", _sha("b"), frozenset({"op1", "op2"}))
    log.append(gb, "main", _sha("c"), frozenset({"op1", "op2", "op3"}))

    entries = log.read(gb, "main")
    assert [e.landed_sha for e in entries] == [_sha("c"), _sha("b"), _sha("a")]
    # a real parent chain, not three disconnected roots
    tip = gb.rev_parse(log.log_ref("main"))
    shas = gb.commit_shas(log.log_ref("main"))
    assert shas[0] == tip and len(shas) == 3


def test_ideal_for_sha_finds_and_misses(tmp_path):
    gb = GitBinding(tmp_path)
    gb.init()
    log.append(gb, "main", _sha("a"), frozenset({"op1"}))

    assert log.ideal_for_sha(gb, "main", _sha("a")) == frozenset({"op1"})
    assert log.ideal_for_sha(gb, "main", _sha("z")) is None


def test_append_survives_persistent_cas_contention_on_the_log_ref(tmp_path, monkeypatch):
    """Best-effort contract: if `update_ref_cas` never succeeds for the log ref (raced out by
    something else touching it, or any other transient failure), `append` gives up cleanly and
    returns False rather than raising -- `land` already succeeded by the time it calls this."""
    gb = GitBinding(tmp_path)
    gb.init()
    monkeypatch.setattr(GitBinding, "update_ref_cas", lambda self, ref, new, old: False)

    ok = log.append(gb, "main", _sha("a"), frozenset({"op1"}))

    assert ok is False
    assert log.read(gb, "main") == []
