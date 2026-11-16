"""Snapshot and restore of the gitignored `.sgt/local/` caches, so a `plan_sync` dry run leaves no
trace (R7)."""

from __future__ import annotations

from pathlib import Path

from ugh import state


# `.sgt/local/` files the rollback below must NOT rewind: the oracle verdict is real work worth
# caching, and `lock` is the store mutex. Every other local
# artifact `lens.get`'s mine-on-contact touches (fidelity/sync_cache marks, the derived
# ideal/witness/backfill caches, the op-index sidecar) is derived and self-healing, so it must roll
# back to leave no trace (R7).
_KEEPS_LOCAL = frozenset({"oracle.json", "lock"})


def _iter_local_caches(repo: Path):
    """The top-level `.sgt/local/` files a `plan_sync` dry run rolls back. Top-level only,
    so the `.sgt/local/hollow/` op-store subtree (monotone, like `.sgt/ops/`) is left untouched; the
    exempt caches and in-flight temp files are skipped."""
    local = repo / state.SGT_DIR / "local"
    if not local.is_dir():
        return
    for p in local.iterdir():
        if p.is_file() and p.name not in _KEEPS_LOCAL and not p.name.startswith(".tmp-"):
            yield p


def _snapshot_local_caches(repo: Path) -> dict[str, bytes]:
    """Byte snapshot of the local caches, captured before mine-on-contact touches them."""
    return {p.name: p.read_bytes() for p in _iter_local_caches(repo)}


def _restore_local_caches(repo: Path, before: dict[str, bytes]) -> None:
    """Roll the local caches back to `before` (R7): rewrite any the dry run changed, delete any it
    newly created, and re-create any it removed."""
    local = repo / state.SGT_DIR / "local"
    now = {p.name: p for p in _iter_local_caches(repo)}
    for name, p in now.items():
        if name not in before:
            p.unlink()
        elif p.read_bytes() != before[name]:
            state._atomic_write_text(p, before[name].decode("utf-8"))
    for name, data in before.items():
        if name not in now:
            state._atomic_write_text(local / name, data.decode("utf-8"))
