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
from collections import OrderedDict
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


def _snapshot_body(ops: list[Op], built_mtime_ns: int) -> dict:
    return {
        "miner_version": MINER_VERSION,
        "op_count": len(ops),
        "built_mtime_ns": built_mtime_ns,
        "ops": [_from_op(op) for op in sorted(ops, key=lambda o: o.id)],
    }


def rebuild(repo: str | Path, store: Store | None = None) -> None:
    """Full backfill: iterate `Store.all_ops()` once (paying the images decode exactly once) and
    write the snapshot. `built_mtime_ns` is captured *before* that read starts, not after --
    `Store.all_ops()` takes real wall-clock time on a large store (seconds, not instant) and holds
    no lock, so a concurrent `Store.add`/`attribute` landing mid-read would otherwise timestamp
    *before* a post-read capture and never be flagged stale. Stamping first guarantees any write
    happening during or after the read has `mtime >= built_mtime_ns`, so it's correctly seen as
    newer."""
    repo = Path(repo)
    begin_ts = time.time_ns()
    store = store or Store(repo)
    state.save_json(repo, "op_index", _snapshot_body(store.all_ops(), begin_ts))


def apply_delta(repo: str | Path, stored_ops: list[Op]) -> None:
    """Incremental upsert of `stored_ops` (the in-memory ops `_sync` just mined/merged -- no
    re-read of the store) into the existing snapshot. Rebuilds from scratch if the snapshot is
    absent. `built_mtime_ns` is captured at entry, before touching the existing snapshot, for the
    same reason `rebuild` captures it before its read (see there)."""
    repo = Path(repo)
    if not stored_ops:
        return
    begin_ts = time.time_ns()
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
        "built_mtime_ns": begin_ts,
        "ops": ops_sorted,
    })


# `_ops_dir_stat` memo keyed by the ops directory's mtime *and* its dirent count: every store
# write lands via rename into the directory (`_write_atomic`'s `os.replace`, git's object/checkout
# writes), and any rename/add/delete bumps the dir mtime -- so an unchanged dir mtime usually
# proves the per-entry scan would return what it returned last time. The count is the same-tick
# backstop: on a coarse-granularity filesystem a write can land in the same mtime tick as the
# memoized read, leaving the dir mtime unchanged, and recounting dirents (cheap, one readdir)
# catches any add/remove that slipped through that tick. (A count-neutral in-place rewrite within
# the same tick still isn't caught -- but every writer renames a fresh temp file into place, so a
# rewrite replaces a dirent and normally bumps the dir mtime; the residual needs a same-tick
# collision, deliberately left rather than paying git-style racy-clean logic on this hot path.)
# LRU: hits re-order, overflow evicts the oldest.
_DIR_STAT_MEMO: "OrderedDict[str, tuple[tuple[int, int], tuple[int, int]]]" = OrderedDict()


def _ops_dir_stat(repo: Path) -> tuple[int, int]:
    """`(dirent_count, max_mtime_ns)` over `.sgt/ops/`, stat-only -- no file content reads."""
    ops_dir = Store(repo).ops_dir
    try:
        dir_mtime = ops_dir.stat().st_mtime_ns
    except OSError:
        return 0, -1
    key = os.path.realpath(ops_dir)
    # One scan feeds both the cheap count (the same-tick guard) and the per-entry stat (the
    # expensive part the memo actually skips on a hit).
    entries = [e for e in os.scandir(ops_dir) if e.is_file()]
    count = len(entries)
    memo = _DIR_STAT_MEMO.get(key)
    if memo is not None and memo[0] == (dir_mtime, count):
        _DIR_STAT_MEMO.move_to_end(key)
        return memo[1]
    max_mtime = -1
    for entry in entries:
        mtime = entry.stat().st_mtime_ns
        if mtime > max_mtime:
            max_mtime = mtime
    _DIR_STAT_MEMO[key] = ((dir_mtime, count), (count, max_mtime))
    if len(_DIR_STAT_MEMO) > 8:
        _DIR_STAT_MEMO.popitem(last=False)
    return count, max_mtime


def _stale_against_dir(repo: Path, miner_version, op_count, built_mtime_ns: int) -> bool:
    """Whether a snapshot with this `(miner_version, op_count, built_mtime_ns)` is stale against
    `.sgt/ops/`, checked stat-only (no op file reads). The single staleness rule, shared by
    `_is_stale_body` (which reads the fields off a loaded snapshot body) and `index_ops`' memo
    fast path (which reads them off the memoized parse), so the two cannot silently diverge if the
    rule ever gains a signal: a miner-version mismatch (catches `migrate ops-v3`), a dirent-count
    mismatch (catches sync ingest / prune), or a build timestamp at or before the newest op file's
    mtime (catches `Store.add`'s provenance-merge and `Store.attribute` rewrites, which bump mtime
    without changing count)."""
    if miner_version != MINER_VERSION:
        return True
    count, max_mtime = _ops_dir_stat(repo)
    if op_count != count:
        return True
    return built_mtime_ns <= max_mtime


def _is_stale_body(repo: Path, body: dict | None) -> bool:
    """`is_stale`'s check against an already-loaded (or absent) snapshot body -- factored out so
    `index_ops` can check staleness against the body it already read instead of reloading the
    file a second time."""
    if body is None:
        return True
    return _stale_against_dir(
        repo, body.get("miner_version"), body.get("op_count"), body.get("built_mtime_ns", -1)
    )


def is_stale(repo: str | Path) -> bool:
    """True if the snapshot is missing or out of date with `.sgt/ops/`, checked stat-only (no op
    file reads): absent, a miner-version mismatch (catches `migrate ops-v3`), a dirent-count
    mismatch (catches sync ingest / prune), or a build timestamp at or before the newest op file's
    mtime (catches `Store.add`'s provenance-merge and `Store.attribute` rewrites, which bump mtime
    without changing count)."""
    repo = Path(repo)
    return _is_stale_body(repo, state.load_json(repo, "op_index", default=None))


# Process-level memo of the parsed snapshot, keyed by the snapshot file's identity
# (mtime_ns, size) plus the metadata staleness gates on (miner_version, built_mtime_ns,
# op_count). One CLI command reads the index through several projection views (map/grid/segments/
# history all call `index_ops`), and each call re-parsed the same multi-megabyte JSON into the
# same Op list. Staleness against `.sgt/ops/` is still re-verified stat-only on every call
# through the *shared* `_stale_against_dir` predicate `_is_stale_body` uses, so a concurrent
# writer is caught exactly as before -- the memo skips the parse, never the check. LRU: hits
# re-order, overflow evicts the oldest.
_PARSED_MEMO: "OrderedDict[str, tuple[tuple[int, int], object, int, object, list[Op]]]" = OrderedDict()


def _snapshot_stat(repo: Path) -> tuple[int, int] | None:
    try:
        st = state.path(repo, "op_index").stat()
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size)


def index_ops(repo: str | Path) -> list[Op]:
    """Every stored op, `Op`s reconstructed with `images={}` -- the fast accessor for read-only
    projection views. Self-heals: rebuilds first if the snapshot is stale. Never pass the result
    to `fold.code`. Loads the snapshot at most twice (once to check staleness, once more only if
    a rebuild just ran) rather than `is_stale` + a separate reload on every call; an unchanged
    snapshot file (same mtime/size) skips even that load and returns the memoized parse."""
    repo = Path(repo)
    repo_key = os.path.realpath(repo)  # "." renames per chdir; the memo must not follow it
    memo = _PARSED_MEMO.get(repo_key)
    snap_stat = _snapshot_stat(repo)
    if memo is not None and snap_stat is not None and memo[0] == snap_stat:
        _stat_key, miner_v, built_mtime_ns, op_count, memo_ops = memo
        if not _stale_against_dir(repo, miner_v, op_count, built_mtime_ns):
            _PARSED_MEMO.move_to_end(repo_key)
            return list(memo_ops)
    body = state.load_json(repo, "op_index", default=None)
    if _is_stale_body(repo, body):
        rebuild(repo)
        snap_stat = _snapshot_stat(repo)  # stat before the reload (mirrors the top-of-function
        body = state.load_json(repo, "op_index", default=None)  # order): a stale key just misses
    if body is None:
        return []
    ops = [_to_op(entry) for entry in body["ops"]]
    if snap_stat is not None:
        _PARSED_MEMO[repo_key] = (
            snap_stat, body.get("miner_version"), body.get("built_mtime_ns", -1),
            body.get("op_count", -1), ops,
        )
        if len(_PARSED_MEMO) > 8:  # a CLI process touches one repo; long-lived hosts a few
            _PARSED_MEMO.popitem(last=False)
    return list(ops)


def earliest_commit_sha(gb, rows, ops) -> dict[str, str]:
    """``op_id -> sha of the earliest commit that embodies it`` -- the one time-axis rule
    `history_view`, `intent.segment.feature_runs`, and `intent.group.atoms` all read, so the three
    projections agree on *when* an op happened.

    An op's own in-history provenance wins (the earliest of its witnessing commits present in
    `rows`). A *pending* op -- one materialized by a `put`/`commit_materialized` witness commit that
    a later `record_ideal` advanced the ref's witness past, so `_sync` never re-mined its diff to
    stamp provenance -- carries an empty `provenance` and would otherwise be dropped, hiding
    just-saved work from every time-aware view. It falls back to the earliest commit whose committed
    ``Sgt-Op:`` trailers name it (the tree-witnessed record of which ops that commit embodies).

    Read-time only: no store write, so a save leaves the working tree clean. (Stamping the witness
    sha into the store instead would rewrite tracked ``.sgt/ops`` files *after* the commit, leaving
    the tree dirty and making the next `sgt sync`/`land` refuse -- and provenance can never live in
    its own witnessing commit anyway, since writing it changes that commit's tree and thus its sha.)

    `gb` is a `GitBinding`, `rows` its `history()` (oldest-first), `ops` the store's ops -- passed in
    so a caller that already computed them pays no extra git call. An op embodied by no commit in
    `rows` is absent from the result (dropped, exactly as before)."""
    commit_index = {sha: i for i, (sha, _parent, _subject) in enumerate(rows)}
    out: dict[str, str] = {}
    pending: set[str] = set()
    for op in ops:
        witnessed = [sha for sha in op.provenance if sha in commit_index]
        if witnessed:
            out[op.id] = min(witnessed, key=lambda s: commit_index[s])
        else:
            pending.add(op.id)
    if pending:
        trailers_by_sha = gb.op_ids_by_commit()
        for sha, _parent, _subject in rows:  # oldest-first, so the first hit is the earliest
            for oid in pending & trailers_by_sha.get(sha, set()):
                out.setdefault(oid, sha)
    return out
