"""A footprint-only sidecar index over the op store (plan: optimize the sgt agent surface for
context + retrieval speed, Part A). `Store.all_ops()` hex-decodes every op's `images` blob --
85%+ of the store's on-disk bytes -- yet no read-only projection view (`sgt.api`, `sgt.core.lens`'s
pure ideal-derivations) ever looks at `op.images`; only `fold.py`/`rewrite.py`/`migrate.py`/
`repair/context.py` and `compute_id` do. This module maintains `.sgt/local/op_index.json`, a
snapshot of every stored op's payload minus `images`, so those callers can read footprint/
requires/provenance/intent/kind without paying the decode.

Never pass an `index_ops` result to `fold.code` or anything that materializes bytes -- its
`images` are `{}` (empty), not absent, so a fold would silently produce zero-length content for
every symbol instead of raising on a missing dict entry.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from sgt import state
from sgt.core.op import MINER_VERSION, Op
from sgt.core.store import Store, _op_from_payload, _payload


def _to_op(entry: dict) -> Op:
    """One sidecar entry (a stored op's payload minus `images`) back into an `Op`, with
    `images={}`."""
    return _op_from_payload(entry, images={})


def _from_op(op: Op) -> dict:
    """`op`'s sidecar entry: `Store`'s on-disk payload with `images` stripped back off."""
    entry = _payload(op)
    del entry["images"]
    return entry


def _snapshot_body(ops: list[Op]) -> dict:
    return {
        "miner_version": MINER_VERSION,
        "op_count": len(ops),
        "built_mtime_ns": time.time_ns(),
        "ops": [_from_op(op) for op in sorted(ops, key=lambda o: o.id)],
    }


def rebuild(repo: str | Path, store: Store | None = None) -> None:
    """Full backfill: iterate `Store.all_ops()` once (paying the images decode exactly once) and
    write the snapshot."""
    repo = Path(repo)
    store = store or Store(repo)
    state.save_json(repo, "op_index", _snapshot_body(store.all_ops()))


def apply_delta(repo: str | Path, stored_ops: list[Op]) -> None:
    """Incremental upsert of `stored_ops` (the in-memory ops `_sync` just mined/merged -- no
    re-read of the store) into the existing snapshot. Rebuilds from scratch if the snapshot is
    absent."""
    repo = Path(repo)
    if not stored_ops:
        return
    body = state.load_json(repo, "op_index", default=None)
    if body is None:
        rebuild(repo)
        return
    entries = {e["id"]: e for e in body["ops"]}
    for op in stored_ops:
        entries[op.id] = _from_op(op)
    ops_sorted = [entries[k] for k in sorted(entries)]
    state.save_json(repo, "op_index", {
        "miner_version": MINER_VERSION,
        "op_count": len(ops_sorted),
        "built_mtime_ns": time.time_ns(),
        "ops": ops_sorted,
    })


def _ops_dir_stat(repo: Path) -> tuple[int, int]:
    """`(dirent_count, max_mtime_ns)` over `.sgt/ops/`, stat-only -- no file content reads."""
    ops_dir = Store(repo).ops_dir
    if not ops_dir.is_dir():
        return 0, -1
    count = 0
    max_mtime = -1
    for entry in os.scandir(ops_dir):
        if entry.is_file():
            count += 1
            mtime = entry.stat().st_mtime_ns
            if mtime > max_mtime:
                max_mtime = mtime
    return count, max_mtime


def is_stale(repo: str | Path) -> bool:
    """True if the snapshot is missing or out of date with `.sgt/ops/`, checked stat-only (no op
    file reads): absent, a miner-version mismatch (catches `migrate ops-v3`), a dirent-count
    mismatch (catches sync ingest / prune), or a build timestamp at or before the newest op file's
    mtime (catches `Store.add`'s provenance-merge and `Store.attribute` rewrites, which bump mtime
    without changing count)."""
    repo = Path(repo)
    body = state.load_json(repo, "op_index", default=None)
    if body is None:
        return True
    if body.get("miner_version") != MINER_VERSION:
        return True
    count, max_mtime = _ops_dir_stat(repo)
    if body.get("op_count") != count:
        return True
    return body.get("built_mtime_ns", -1) <= max_mtime


def index_ops(repo: str | Path) -> list[Op]:
    """Every stored op, `Op`s reconstructed with `images={}` -- the fast accessor for read-only
    projection views. Self-heals: rebuilds first if the snapshot is stale. Never pass the result
    to `fold.code`."""
    repo = Path(repo)
    if is_stale(repo):
        rebuild(repo)
    body = state.load_json(repo, "op_index", default=None)
    if body is None:
        return []
    return [_to_op(entry) for entry in body["ops"]]
