"""Snapshot and restore of the gitignored `.sgt/local/` caches, so a `plan_sync` dry run leaves no
trace (R7)."""

from __future__ import annotations

from pathlib import Path

from ugh import state


# `.sgt/local/` files the rollback below must NOT rewind: the oracle verdict is real work worth
# caching, `land_pending` is a crash journal, and `lock` is the store mutex. Every other local
# artifact `lens.get`'s mine-on-contact touches (fidelity/sync_cache marks, the derived
# ideal/witness/backfill caches, the op-index sidecar) is derived and self-healing, so it must roll
# back to leave no trace (R7).
_LAND_KEEPS_LOCAL = frozenset({"oracle.json", "land_pending.json", "lock"})


def _iter_local_caches(repo: Path):
    """The top-level `.sgt/local/` files that participate in a land's transaction. Top-level only,
    so the `.sgt/local/hollow/` op-store subtree (monotone, like `.sgt/ops/`) is left untouched; the
    exempt caches and in-flight temp files are skipped."""
    local = repo / state.SGT_DIR / "local"
    if not local.is_dir():
        return
    for p in local.iterdir():
        if p.is_file() and p.name not in _LAND_KEEPS_LOCAL and not p.name.startswith(".tmp-"):
            yield p


def _snapshot_local_caches(repo: Path) -> dict[str, bytes]:
    """Byte snapshot of the transactional local caches, captured before mine-on-contact touches
    them so every non-landing exit can restore the true pre-land baseline."""
    return {p.name: p.read_bytes() for p in _iter_local_caches(repo)}


def _restore_local_caches(repo: Path, before: dict[str, bytes]) -> None:
    """Roll the transactional local caches back to `before` (R7): rewrite any the land changed,
    delete any it newly created, and re-create any it removed. `restore_worktree_to` only rewinds
    git-*tracked* state, so these gitignored caches -- which a `restore_worktree_to` never sees --
    need their own restore for a land that does not land to leave the tree byte-identical."""
    local = repo / state.SGT_DIR / "local"
    now = {p.name: p for p in _iter_local_caches(repo)}
    for name, p in now.items():
        if name not in before:
            p.unlink()  # appeared during the land -> remove it
        elif p.read_bytes() != before[name]:
            state._atomic_write_text(p, before[name].decode("utf-8"))  # churned -> rewind
    for name, data in before.items():
        if name not in now:
            state._atomic_write_text(local / name, data.decode("utf-8"))  # land removed it -> restore
