"""The U10 op-store migration: `sgt migrate ops-v3` crosses an existing v2 store to v3 (U9's
rebirth/flip identity), re-keying every op and every op-id-bearing artifact under one resumable
manifest and recovering the ~20% closure the v2 rebirth pseudo-fork dropped.

A real v2 store can't be produced by this tree's mine() (it always mines v3 now), so a v2 store is
*synthesized* from a real v3 rebirth history and downgraded to the pre-U9 on-disk shape: bare `⊥`
deletion sentinels (not salted), naive `(sym, None)` re-adds (the pseudo-fork), v2 ids, and the
lossy v2 ideal (`reduce_to_ideal` drops the forked birth, so the re-added file vanishes from
`code(I)`). Migrating that store must re-birth the file. All fixtures are hermetic: real `git`, no
network, no wall-clock/LLM dependency -- the same discipline as `tests/core/test_store.py`.

This file covers the *op-store* crossing (`migrate ops-v3`) -- the only remaining `migrate`
subcommand now that the legacy feature-id re-mint has been removed.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from sgt import state
from sgt.core import lens, migrate, order, sync
from sgt.core.fold import code
from sgt.core.op import BOTTOM, compute_id, is_bottom, make_op
from sgt.core.store import Store, _serialize, fsck
from sgt.store.gitbind import GitBinding, init_store


def _downgrade_to_v2(repo: Path) -> None:
    """Rewrite the store into the pre-U9 v2 on-disk shape: every salted bottom `⊥@sha` collapses to
    the bare `⊥` sentinel, every re-add's `⊥@sha` before-version collapses to `None` (v2's naive
    re-claim of `(sym, None)` -- the pseudo-fork), ids re-hash under `miner_version="2"`, and the
    ideal table becomes the lossy v2 reduction (the fork drops the reborn file). This is exactly the
    store a pre-U9 clone committed for a rebirth history."""
    store = Store(repo)
    ops = store.all_ops()
    for op in ops:
        os.remove(store.ops_dir / op.id)
    for op in ops:
        fp = {
            sym: (None if (b is not None and is_bottom(b)) else b, BOTTOM if is_bottom(a) else a)
            for sym, (b, a) in op.footprint.items()
        }
        v2 = replace(op, footprint=fp, miner_version="2")
        v2 = replace(v2, id=compute_id(fp, op.images, op.requires, op.kind, "2"))
        (store.ops_dir / v2.id).write_bytes(_serialize(v2))
    v2_ops = Store(repo).all_ops()
    key = lens._ref_key(GitBinding(repo))
    committed = {o.id for o in v2_ops if o.provenance}
    lossy = sorted(order.reduce_to_ideal(committed, v2_ops))
    state.save_json(repo, "ideal_table", {key: lossy})


def _build_rebirth_v2(repo: Path) -> GitBinding:
    """A single-clone add->delete->re-add history mined under v3, then downgraded to a v2 store.
    Under v2 the re-added `notes.txt` is lost to the pseudo-fork; the migration must re-birth it."""
    gb, _ = init_store(repo)
    (repo / "keep.py").write_text("def keep():\n    return 1\n", encoding="utf-8")
    (repo / "notes.txt").write_text("alpha\n", encoding="utf-8")
    gb.commit_all("add keep and notes")
    (repo / "notes.txt").unlink()
    gb.commit_all("delete notes")
    (repo / "notes.txt").write_text("beta\n", encoding="utf-8")
    gb.commit_all("re-add notes")
    lens.get(repo)  # mine under v3
    _downgrade_to_v2(repo)
    return gb


def _ops_snapshot(repo: Path) -> dict[str, bytes]:
    d = Store(repo).ops_dir
    return {p.name: p.read_bytes() for p in d.iterdir() if p.is_file()}


# -- scenario 1: dry run touches nothing --------------------------------------------------------


def test_dry_run_reports_and_writes_nothing(tmp_path):
    repo = tmp_path / "repo"
    _build_rebirth_v2(repo)
    before_ops = _ops_snapshot(repo)
    before_table = state.load_json(repo, "ideal_table", default={})

    report = migrate.migrate_ops_v3(repo, dry_run=True)

    assert report.dry_run and not report.changed
    assert report.total_ops > 0
    assert report.rekey_clean + report.rebirth_remapped + len(report.orphaned) == report.total_ops
    assert report.rebirth_remapped >= 1  # the prune + re-add chain is footprint-changed under v3
    assert "ideal_table" in report.artifacts  # closure recovery would rewrite the current ideal
    # not one byte written: op files, the ideal table, and no manifest.
    assert _ops_snapshot(repo) == before_ops
    assert state.load_json(repo, "ideal_table", default={}) == before_table
    assert state.load_json(repo, "migration_manifest", default=None) is None


# -- scenario 2: apply recovers closure and leaves a clean v3 store ------------------------------


def test_apply_recovers_closure_and_leaves_a_clean_v3_store(tmp_path):
    repo = tmp_path / "repo"
    _build_rebirth_v2(repo)

    # Precondition: the v2 store lost the reborn file to the pseudo-fork.
    v2_ops = Store(repo).all_ops()
    v2_ideal = lens.current_ideal(repo)
    assert "notes.txt" not in code(v2_ideal, v2_ops)
    assert all(o.miner_version == "2" for o in v2_ops)

    report = migrate.migrate_ops_v3(repo, dry_run=False)
    assert report.changed and not report.orphaned

    all_ops = Store(repo).all_ops()
    assert all_ops and all(o.miner_version == "3" for o in all_ops)  # store is pure v3

    f = fsck(repo)
    assert f.ok and not f.mixed_versions and not f.invalid_ideals  # clean, no mixed-version backstop

    ideal = lens.current_ideal(repo)
    assert order.is_valid_ideal(all_ops, ideal.op_ids)  # zero pseudo-forks: grounded + fork-free
    materialized = code(ideal, all_ops)
    assert materialized["notes.txt"] == b"beta\n"  # closure recovered -- the reborn file materializes
    assert "keep.py" in materialized

    # every ideal-table reference resolves to a present op.
    present = {o.id for o in all_ops}
    for ids in state.load_json(repo, "ideal_table", default={}).values():
        assert set(ids) <= present

    # idempotent: a second apply on the now-v3 store is a no-op.
    again = migrate.migrate_ops_v3(repo, dry_run=False)
    assert not again.changed and again.total_ops == 0
    assert state.load_json(repo, "migration_manifest", default=None) is None


# -- scenario 3: crash mid-apply, then resume ---------------------------------------------------


def test_crash_mid_apply_resumes_to_the_same_result(tmp_path, monkeypatch):
    base = tmp_path / "base"
    _build_rebirth_v2(base)
    clean = tmp_path / "clean"
    crash = tmp_path / "crash"
    shutil.copytree(base, clean)
    shutil.copytree(base, crash)

    # Uninterrupted apply -- the reference result.
    migrate.migrate_ops_v3(clean, dry_run=False)
    clean_ids = sorted(o.id for o in Store(clean).all_ops())
    clean_ideal = set(lens.current_ideal(clean).op_ids)

    # Crash after the ops + artifacts are written but before the pre-v3 files are pruned.
    def _boom(store, ids):
        raise RuntimeError("crash mid-apply")

    monkeypatch.setattr(migrate, "_prune_pre_v3", _boom)
    with pytest.raises(RuntimeError):
        migrate.migrate_ops_v3(crash, dry_run=False)

    # The manifest survives and the store is transiently mixed -- fsck's backstop names it.
    assert state.load_json(crash, "migration_manifest", default=None) is not None
    assert set(fsck(crash).mixed_versions) == {"2", "3"} and not fsck(crash).ok

    monkeypatch.undo()  # restore _prune_pre_v3
    resumed = migrate.migrate_ops_v3(crash, dry_run=False)  # resume from the manifest
    assert resumed.changed
    assert state.load_json(crash, "migration_manifest", default=None) is None

    # Converges to the uninterrupted result: same op ids, same ideal, clean fsck.
    assert sorted(o.id for o in Store(crash).all_ops()) == clean_ids
    assert set(lens.current_ideal(crash).op_ids) == clean_ideal
    assert fsck(crash).ok


# -- scenario 4: refused across miner versions, both directions ---------------------------------


def _init_bare(root: Path) -> Path:
    remote = root / "remote.git"
    remote.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True, capture_output=True)
    return remote


def _clone(remote: Path, dest: Path) -> Path:
    subprocess.run(["git", "clone", "-q", str(remote), str(dest)], check=True, capture_output=True)
    GitBinding(dest).init()
    return dest


def _push(repo: Path) -> None:
    subprocess.run(["git", "-C", str(repo), "push", "-q", "origin", "main"], check=True, capture_output=True)


def _edit_and_commit(repo: Path, path: str, content: str, message: str) -> None:
    (repo / path).write_text(content, encoding="utf-8")
    GitBinding(repo).commit_all(message)
    ideal = lens.get(repo)
    put_sha = lens.put(repo, ideal, message=f"sgt: {message}")
    lens.record_ideal(repo, ideal, put_sha)


def _mint_v2_op_file(repo: Path, sym: str) -> str:
    """Write a hand-minted v2 op blob into `repo`'s `.sgt/ops/` (mirrors test_store.py's mixed-version
    fixture): its id hashes under `miner_version="2"`, so it is a genuine foreign-version op."""
    op = make_op({sym: (None, "vz")}, {sym: b"zzz"}, provenance=("deadbeefdeadbeefdeadbeefdeadbeefdeadbeef",))
    v2 = replace(op, miner_version="2")
    v2 = replace(v2, id=compute_id(v2.footprint, v2.images, v2.requires, v2.kind, "2"))
    (repo / ".sgt" / "ops" / v2.id).write_bytes(_serialize(v2))
    return v2.id


def _two_clones(tmp_path: Path) -> tuple[Path, Path, Path]:
    remote = _init_bare(tmp_path)
    a = _clone(remote, tmp_path / "a")
    lens.init(a)
    _edit_and_commit(a, "main.py", "def foo():\n    return 1\n", "init")
    _push(a)
    b = _clone(remote, tmp_path / "b")
    lens.get(b)
    return remote, a, b


def test_sync_refused_when_theirs_carries_a_v2_op(tmp_path):
    """v3-local + v2-remote: B (v3) syncs a remote tip carrying a v2 op -> refused, remedy names
    `sgt migrate ops-v3`."""
    remote, a, b = _two_clones(tmp_path)
    _mint_v2_op_file(a, "legacy.py::old")
    subprocess.run(["git", "-C", str(a), "add", ".sgt"], check=True, capture_output=True)
    GitBinding(a).commit_all("a: commit a v2 op")
    _push(a)

    with pytest.raises(sync.MinerVersionMismatch) as ei:
        sync.sync(b, remote="origin", branch="main")
    assert "sgt migrate ops-v3" in str(ei.value)
    assert "landing clone first" in str(ei.value)  # the team ordering rule rides the same message


def test_sync_refused_when_ours_carries_a_v2_op(tmp_path):
    """v2-local + v3-remote: B carries a v2 op locally and syncs a pure-v3 remote that is ahead ->
    refused, remedy names `sgt migrate ops-v3` and points at our (behind) side."""
    remote, a, b = _two_clones(tmp_path)
    _edit_and_commit(a, "main.py", "def foo():\n    return 2\n", "a: advance")  # remote moves ahead
    _push(a)
    _mint_v2_op_file(b, "legacy.py::old")  # B's own store is now mixed
    subprocess.run(["git", "-C", str(b), "add", ".sgt"], check=True, capture_output=True)
    GitBinding(b).commit_all("b: commit a v2 op")  # commit it so the tree is clean for sync

    with pytest.raises(sync.MinerVersionMismatch) as ei:
        sync.sync(b, remote="origin", branch="main")
    assert "sgt migrate ops-v3" in str(ei.value)
    assert "ours" in str(ei.value)  # our side is the one behind


# -- scenario 5: an old proposal referencing pre-migration ids survives -------------------------


def test_old_proposal_referencing_pre_migration_ids_survives(tmp_path):
    """A proposal naming v2 op ids is re-keyed by the migration (its ids remapped through the map)
    and stays readable -- `api.proposal_view`/`state.load_proposal` never crash on a pre-migration
    id."""
    from sgt import api

    repo = tmp_path / "repo"
    _build_rebirth_v2(repo)
    v2_ids = [o.id for o in Store(repo).all_ops() if o.provenance]
    pid = "deadbeef0001"
    state.save_proposal(repo, f"{pid}.json", {
        "id": pid, "base_ref": "HEAD", "base_ideal_ids": [], "delta_ids": sorted(v2_ids),
        "feature_delta": [], "claim_key": None, "title": "legacy", "description": None,
        "created_ts": 0.0, "approvals": [],
    })

    migrate.migrate_ops_v3(repo, dry_run=False)

    body = state.load_proposal(repo, f"{pid}.json")
    assert body is not None
    present = {o.id for o in Store(repo).all_ops()}
    assert body["delta_ids"] and set(body["delta_ids"]) <= present  # re-keyed to live v3 ids
    view = api.proposal_view(repo, pid)  # must return, not raise
    assert view["id"] == pid and "error" not in view
