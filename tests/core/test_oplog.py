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


def test_drop_event_removes_the_target_and_never_clobbers_a_concurrent_append(tmp_path):
    """`undo` cannot hold the lock across `apply_inverse` (it re-enters the non-reentrant flock),
    so it drops the inverted event in a separate lock-held read-modify-write via `_drop_event`.
    This proves the guarantee that closes the lost-append race: an event appended *after* `undo`
    read its tail (modelled here by `B` following `A` on the stack) survives the drop of `A`, and a
    plain tail drop removes only the tail."""
    repo = corpus.CORPUS["linear_history"].build(tmp_path / "repo")
    get(repo)
    key = oplog._ref_key(Path(repo))
    a = {"kind": "after", "snapshot": {}, "edge": ["a", "b"]}
    b = {"kind": "after", "snapshot": {}, "edge": ["c", "d"]}
    oplog.append(repo, a, ref_key=key)
    oplog.append(repo, b, ref_key=key)  # a concurrent append landing during undo's apply window

    # undo read `a` as the tail, applied its inverse, then goes to drop it: `b` (appended
    # concurrently) must be preserved, and `a` removed from below the tail.
    assert oplog._drop_event(repo, key, a) is True
    assert oplog.load(repo)[key] == [b], "the concurrently-appended event must survive"

    # plain tail case: dropping `b` leaves an empty stack, no clobber.
    assert oplog._drop_event(repo, key, b) is True
    assert oplog.load(repo).get(key, []) == []
    # dropping an event already gone is a no-op, not an error.
    assert oplog._drop_event(repo, key, a) is False


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
# undo hardening (Phase 0, 0.2): applied-flag (F6) + F3 snapshot-drop guard
# ---------------------------------------------------------------------------


def gb_head(repo) -> str:
    from sgt.store.gitbind import GitBinding

    return GitBinding(Path(repo)).head()


def _seed_two_symbols(repo):
    """A repo with foo+bar committed and mined -- the baseline for the undo-hardening cases. `get`
    mines the commit and persists the ref's ideal table; no explicit `record_ideal` (that would add
    a spurious no-op journal entry over the ideal `get` just recorded)."""
    from sgt.core import lens

    gb, _ = init_store(repo)
    (repo / "a.py").write_text("def foo():\n    return 1\n\n\ndef bar():\n    return 2\n", encoding="utf-8")
    gb.commit_all("add foo and bar")
    lens.get(repo)
    return gb


def test_undo_refuses_to_drop_work_committed_after_the_journal_entry(tmp_path):
    """F3 (0.2c): `revert` -> raw `git commit` -> `undo`. The undo's absolute-snapshot restore of the
    pre-revert ideal would silently clobber the intervening manual commit (an op mined *after* the
    journal entry). Undo must detect that casualty and refuse rather than destroy it -- the manual
    commit's content stays on disk, the reverted op is NOT re-added, and the entry is left in place
    for a later `undo(force=True)`."""
    from sgt.core import lens, verbs

    repo = tmp_path / "repo"
    gb = _seed_two_symbols(repo)
    bar = next(o for o in Store(repo).all_ops() if "a.py::bar" in o.footprint)
    verbs.revert(repo, bar.id)  # journals the pre-revert ideal
    assert b"def bar" not in (repo / "a.py").read_bytes()

    # a raw manual commit between the revert and the undo -- work sgt only mines on next contact.
    (repo / "a.py").write_text(
        (repo / "a.py").read_text(encoding="utf-8") + "\n\ndef baz():\n    return 3\n", encoding="utf-8"
    )
    gb.commit_all("RAW: add baz (no sgt)")
    lens.get(repo)  # absorb the manual commit into the current ideal

    outcome = oplog.undo(repo)
    assert outcome.status == "refused"
    assert "baz" in outcome.message  # names the work it would have destroyed
    assert b"def baz" in (repo / "a.py").read_bytes()   # the intervening work was NOT clobbered
    assert b"def bar" not in (repo / "a.py").read_bytes()  # and the revert was NOT undone
    assert oplog.tail(repo) is not None  # refused, not popped -- survives for `undo(force=True)`


def test_undo_force_drops_intervening_work_deliberately(tmp_path):
    """The escape hatch for 0.2(c): `undo(force=True)` proceeds despite the F3 casualty -- the user
    has been told and chose to drop the intervening commit. This is what `sgt undo --force` wires
    to."""
    from sgt.core import lens, verbs

    repo = tmp_path / "repo"
    gb = _seed_two_symbols(repo)
    original = lens.get(repo).op_ids
    bar = next(o for o in Store(repo).all_ops() if "a.py::bar" in o.footprint)
    verbs.revert(repo, bar.id)
    (repo / "a.py").write_text(
        (repo / "a.py").read_text(encoding="utf-8") + "\n\ndef baz():\n    return 3\n", encoding="utf-8"
    )
    gb.commit_all("RAW: add baz")
    lens.get(repo)

    outcome = oplog.undo(repo, force=True)
    assert outcome.status == "ideal_edit"
    assert b"def bar" in (repo / "a.py").read_bytes()  # the revert was undone (bar restored)
    assert b"def baz" not in (repo / "a.py").read_bytes()  # baz dropped as opted in
    assert original <= get(repo).op_ids  # the pre-revert ops are all back


def test_undo_skips_an_unapplied_journal_entry(tmp_path):
    """F6/0.2a: a journal entry from an edit that never landed (`applied=False`) is bogus -- undo
    must discard it and reverse the real edit beneath it, not execute the crashed one."""
    from sgt.core import verbs

    repo = tmp_path / "repo"
    _seed_two_symbols(repo)
    original = get(repo).op_ids
    bar = next(o for o in Store(repo).all_ops() if "a.py::bar" in o.footprint)
    verbs.revert(repo, bar.id)  # a genuine, applied ideal_edit entry

    # a bogus unapplied entry pushed on top, as a crashed verb would leave behind.
    key = oplog._ref_key(Path(repo))
    table = oplog.load(repo)
    table[key].append({"kind": "ideal_edit", "ideal": [], "witness": None, "applied": False})
    oplog.save(repo, table)

    outcome = oplog.undo(repo)
    assert outcome.status == "ideal_edit"  # the bogus entry was skipped, the revert undone
    assert get(repo).op_ids == original    # bar is back -- the real edit was reversed
    assert not oplog.load(repo).get(key)   # both the bogus and the consumed real entry are gone


def test_record_ideal_marks_its_journal_entry_applied_only_after_the_edit_lands(tmp_path):
    """0.2a/F6: `record_ideal` writes its `ideal_edit` entry `applied=False`, then flips it to True
    only after the ideal-table + witness advance succeed. A clean revert leaves an `applied=True`
    tail; a crash between the journal push and the table write leaves `applied=False`, which
    `oplog.undo` discards."""
    from sgt.core import lens, verbs

    repo = tmp_path / "repo"
    _seed_two_symbols(repo)
    bar = next(o for o in Store(repo).all_ops() if "a.py::bar" in o.footprint)
    verbs.revert(repo, bar.id)
    assert oplog.tail(repo).get("applied") is True  # a completed edit is marked applied

    # simulate a crash during the next record_ideal, after the journal push but before the table
    # write: the entry must be left unapplied.
    ideal = lens.current_ideal(repo)
    real_save = lens._save_ideal_table

    def explode(r, t):
        raise RuntimeError("simulated crash mid record_ideal")

    lens._save_ideal_table = explode
    try:
        lens.record_ideal(repo, ideal, gb_head(repo))
    except RuntimeError:
        pass
    finally:
        lens._save_ideal_table = real_save

    assert oplog.tail(repo).get("applied") is False  # the crashed entry is not trusted


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


# ---------------------------------------------------------------------------
# preview: what undo will do, before it does it
# ---------------------------------------------------------------------------

def test_preview_names_the_edits_undo_would_bring_back(tmp_path):
    """`undo` is what a developer reaches for when something has gone wrong, which is the worst
    moment to make them run it blind and find out afterward. Everything shown is already known
    before the fact."""
    from sgt.core import verbs

    from sgt.core import opindex
    from sgt.core.op import is_behavioral

    repo = tmp_path / "repo"
    _seed_two_symbols(repo)
    # Pick an op that carries a symbol a developer would recognize, rather than whichever op sorts
    # first: most of a file's ops are positional residue, and `_symbols_for` filters those out by
    # design, so a blind `sorted(...)[0]` asserted about a line that is *correctly* empty. It went red
    # when a miner-version bump reshuffled the id order, which is a false alarm about hashing.
    live = current_ideal(repo).op_ids
    op_id = next(o.id for o in sorted(opindex.index_ops(repo), key=lambda o: o.id)
                 if o.id in live and any(is_behavioral(s) for s in o.footprint))
    verbs.revert(repo, op_id)

    pv = oplog.preview(repo)

    assert pv["kind"] == "ideal_edit"
    assert pv["ok"] is True
    assert op_id in pv["restored"]
    assert any("a.py::" in s for s in pv["symbols"])  # named in terms the developer recognizes


def test_preview_reports_a_refusal_before_the_user_commits_to_it(tmp_path):
    """The F3 guard already refuses an undo that would drop work committed after the edit. Learning
    that *after* asking for the undo is the same information arriving too late to be useful."""
    from sgt.core import lens, verbs

    repo = tmp_path / "repo"
    gb = _seed_two_symbols(repo)
    op_id = sorted(current_ideal(repo).op_ids)[0]
    verbs.revert(repo, op_id)
    # Work lands after the edit was recorded -- the casualty the guard exists to protect.
    (repo / "b.py").write_text("def baz():\n    return 3\n", encoding="utf-8")
    gb.commit_all("add baz")
    lens.get(repo)

    pv = oplog.preview(repo)

    assert pv["ok"] is False
    assert "drop work committed after" in pv["message"]
    assert oplog.preview(repo, force=True)["ok"] is True  # --force is the documented override


def test_preview_of_an_empty_log_says_so_without_raising(tmp_path):
    repo = tmp_path / "repo"
    _seed_two_symbols(repo)

    pv = oplog.preview(repo)

    assert pv["kind"] is None and pv["ok"] is True and pv["restored"] == []


def test_preview_of_a_shared_land_reports_the_refusal(tmp_path):
    repo = tmp_path / "repo"
    _seed_two_symbols(repo)
    oplog.append(repo, {"kind": "land", "branch": "main", "ops": []})

    pv = oplog.preview(repo)

    assert pv["ok"] is False and pv["kind"] == "land"
    assert "shared branch" in pv["message"]


def test_preview_writes_nothing(tmp_path):
    """A preview that mutates is not a preview."""
    from sgt.core import verbs

    repo = tmp_path / "repo"
    _seed_two_symbols(repo)
    verbs.revert(repo, sorted(current_ideal(repo).op_ids)[0])
    before_log = oplog.load(repo)
    before_ideal = current_ideal(repo).op_ids

    oplog.preview(repo)

    assert oplog.load(repo) == before_log
    assert current_ideal(repo).op_ids == before_ideal
