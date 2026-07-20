"""Tests for sgt.core.oplog -- the unified operation-event log + universal undo (plan U8, R7/KTD6).

The log **subsumes** the old per-ref `ideal_journal` (single store, one pop per undo): every
mutating verb appends a typed event, and `undo` pops the tail and applies its inverse uniformly.
The ideal-edit undo behavior itself (save/revert/restore/rewrite) is proven byte-for-byte in
`tests/test_porcelain.py`; this file covers the *unified* mechanism -- the append/tail/pop store,
the per-kind inverse dispatch, feature-reorg undo (the previously-un-journaled gap), and the
log-but-refuse contract for an already-shared `land`.
"""

from __future__ import annotations

from pathlib import Path

from sgt.core import oplog
from sgt.core.lens import current_ideal, get
from sgt.core.store import Store
from sgt.lens import authored
from sgt.lens import map as lensmap
from sgt.lens import tree
from sgt.lens import verbs as fverbs
from sgt.store.gitbind import init_store
from tests.laws import corpus


# ---------------------------------------------------------------------------
# store: append / tail / pop
# ---------------------------------------------------------------------------


def test_append_tail_pop_roundtrip_is_per_ref_and_reverse_chronological(tmp_path):
    repo = corpus.CORPUS["linear_history"].build(tmp_path / "repo")
    get(repo)
    oplog.append(repo, {"kind": "after", "snapshot": {}, "edge": ["a", "b"]})
    oplog.append(repo, {"kind": "after", "snapshot": {}, "edge": ["c", "d"]})

    tail = oplog.tail(repo)
    assert tail is not None and tail["edge"] == ["c", "d"]  # LIFO: newest is the tail
    popped = oplog.pop(repo)
    assert popped["edge"] == ["c", "d"]
    assert oplog.tail(repo)["edge"] == ["a", "b"]  # the older event is now the tail


def test_undo_on_an_empty_log_reports_nothing_to_undo(tmp_path):
    repo = corpus.CORPUS["linear_history"].build(tmp_path / "repo")
    get(repo)
    outcome = oplog.undo(repo)
    assert outcome.status == "empty"
    assert "nothing to undo" in outcome.message


# ---------------------------------------------------------------------------
# ideal-edit kind (subsumes the old ideal_journal)
# ---------------------------------------------------------------------------


def test_record_ideal_appends_an_ideal_edit_event_that_undo_inverts(tmp_path):
    """A revert flows through `record_ideal`, which now appends an `ideal_edit` event to the unified
    log; `oplog.undo` restores the prior ideal exactly -- the same restore `undo_ideal` did."""
    from sgt.core import verbs

    repo = corpus.CORPUS["linear_history"].build(tmp_path / "repo")
    original = get(repo).op_ids
    baz = next(o for o in Store(repo).all_ops() if "b.py::baz" in o.footprint)

    verbs.revert(repo, baz.id)
    assert baz.id not in get(repo).op_ids

    tail = oplog.tail(repo)
    assert tail.get("kind", "ideal_edit") == "ideal_edit"  # a revert is one ideal_edit event

    outcome = oplog.undo(repo)
    assert outcome.status == "ideal_edit"
    assert get(repo).op_ids == original  # the reverted op is back


# ---------------------------------------------------------------------------
# feature-reorg kind (the previously-un-journaled gap)
# ---------------------------------------------------------------------------


def test_feature_reorg_undo_restores_prior_label_and_membership(tmp_path):
    """A feature rename mutates `tree`/`pins`/`authored_features` but no ideal -- previously
    un-journaled, so `undo` could not reverse it. The unified log snapshots those artifacts at
    append time; `undo` restores them, so the prior label/membership comes back."""
    repo = corpus.CORPUS["mixed_coverage"].build(tmp_path / "repo")
    get(repo)
    result = lensmap.build_map(repo)
    fid = next(iter(result["nodes"]))
    original_label = result["nodes"][fid].get("label", fid)

    fverbs.apply_rename(repo, fverbs.plan_rename(repo, fid, "renamed-xyz"))
    assert tree.load(repo)["nodes"][fid]["label"] == "renamed-xyz"
    assert any(f.label == "renamed-xyz" for f in authored.load_authored(repo).values())

    outcome = oplog.undo(repo)
    assert outcome.status == "reorg"
    assert tree.load(repo)["nodes"][fid]["label"] == original_label  # label restored
    assert not any(f.label == "renamed-xyz" for f in authored.load_authored(repo).values())


# ---------------------------------------------------------------------------
# land: logged for provenance, inverse refused (KTD6 / journal=checked_out guard)
# ---------------------------------------------------------------------------


_BASE = "def foo():\n    return 1\n"


def _seed_checked_out(root: Path):
    from sgt import state
    from sgt.core import lens

    gb, _ = init_store(root)
    (root / "main.py").write_text(_BASE, encoding="utf-8")
    state.save_json(root, "oracle_config", {"tiers": [{"name": "gate", "command": "exit 0"}]})
    gb.commit_all("init")
    ideal = lens.get(root)
    put = lens.put(root, ideal, message="sgt: init")
    lens.record_ideal(root, ideal, put)
    return gb, gb.symbolic_ref()


def _commit_op(gb, root: Path, symbol: str) -> str:
    from sgt.core import lens

    (root / "main.py").write_text(
        (root / "main.py").read_text(encoding="utf-8") + f"\n\ndef {symbol}():\n    return 0\n",
        encoding="utf-8",
    )
    gb.commit_all(f"add {symbol}")
    ideal = lens.get(root)
    put = lens.put(root, ideal, message=f"sgt: add {symbol}")
    lens.record_ideal(root, ideal, put)
    return put


def test_shared_land_is_logged_but_its_undo_is_refused(tmp_path):
    """Landing a *non-checked-out* branch is a shared-out mutation (mirrors the `journal=checked_out`
    guard, land.py:207): it is logged for provenance in the unified log, but `undo` refuses to apply
    its inverse -- undo only reverses local, not-yet-shared operations."""
    from sgt.core import sync

    repo = tmp_path / "repo"
    gb, _cur = _seed_checked_out(repo)
    gb._git("branch", "release")            # target branch, not checked out
    _commit_op(gb, repo, "baz")             # advance the checked-out branch past release

    report = sync.land(repo, branch="release")
    assert report.landed

    table = oplog.load(repo)
    assert any(e.get("kind") == "land" for entries in table.values() for e in entries), \
        "the shared land must be logged for provenance"

    outcome = oplog.undo(repo)
    assert outcome.status == "refused"
    assert "shared" in outcome.message.lower() and "land" in outcome.message.lower()
    # refused, not popped: the provenance record survives (no rebase story past a shared op).
    table_after = oplog.load(repo)
    assert any(e.get("kind") == "land" for entries in table_after.values() for e in entries)
