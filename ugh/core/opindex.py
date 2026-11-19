"""The op index: `.sgt/local/op_index.json`, every stored op's payload without its images, so
reads that need footprints, requires and provenance skip decoding the images that make up most of
the store. Never fold an `index_ops` result: its images are `{}` and fold to empty files.
"""

from __future__ import annotations

import os
import time
from collections import OrderedDict
from pathlib import Path

from ugh import state
from ugh.core.op import MINER_VERSION, Op
from ugh.core.store import Store, _op_from_payload, _payload


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
    """Write the index from `Store.all_ops()`. The build time is taken before the read, which holds
    no lock, so a write landing during it is newer than the build and marks the index stale."""
    repo = Path(repo)
    begin_ts = time.time_ns()
    store = store or Store(repo)
    state.save_json(repo, "op_index", _snapshot_body(store.all_ops(), begin_ts))


def apply_delta(repo: str | Path, stored_ops: list[Op]) -> None:
    """Upsert `stored_ops` into the index without reading the store, or rebuild it when absent. The
    build time is taken first, as in `rebuild`."""
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


# `_ops_dir_stat` memo, trusted by git's racy-clean rule: every write renames into the ops
# directory and stamps its mtime, so a scan that started `_RACY_SLACK_NS` after that mtime stays
# current while the mtime is unchanged. A trusted scan also persists to `ops_dirstat` for the next
# process.
_DIR_STAT_MEMO: "OrderedDict[str, tuple[int, int, tuple[int, int]]]" = OrderedDict()
_RACY_SLACK_NS = 2_000_000_000  # > any real filesystem timestamp granularity (HFS+: 1s)


def _ops_dir_stat(repo: Path) -> tuple[int, int]:
    """`(dirent_count, max_mtime_ns)` over `.sgt/ops/`, stat-only -- no file content reads."""
    ops_dir = Store(repo).ops_dir
    scan_start = time.time_ns()
    try:
        dir_mtime = ops_dir.stat().st_mtime_ns
    except OSError:
        return 0, -1
    key = os.path.realpath(ops_dir)
    memo = _DIR_STAT_MEMO.get(key)
    if memo is not None and memo[0] == dir_mtime and memo[1] - dir_mtime > _RACY_SLACK_NS:
        _DIR_STAT_MEMO.move_to_end(key)
        return memo[2]
    if memo is None:
        rec = state.load_json(repo, "ops_dirstat", default=None)
        if (
            isinstance(rec, dict) and rec.get("dir_mtime_ns") == dir_mtime
            and rec.get("scan_start_ns", 0) - dir_mtime > _RACY_SLACK_NS
            and isinstance(rec.get("count"), int) and isinstance(rec.get("max_mtime_ns"), int)
        ):
            result = (rec["count"], rec["max_mtime_ns"])
            _DIR_STAT_MEMO[key] = (dir_mtime, rec["scan_start_ns"], result)
            return result
    # within the slack window an unchanged count reuses the memoized max mtime
    entries = [e for e in os.scandir(ops_dir) if e.is_file()]
    count = len(entries)
    if memo is not None and memo[0] == dir_mtime and memo[2][0] == count:
        max_mtime = memo[2][1]
    else:
        max_mtime = -1
        for entry in entries:
            mtime = entry.stat().st_mtime_ns
            if mtime > max_mtime:
                max_mtime = mtime
    _DIR_STAT_MEMO[key] = (dir_mtime, scan_start, (count, max_mtime))
    if len(_DIR_STAT_MEMO) > 8:
        _DIR_STAT_MEMO.popitem(last=False)
    if scan_start - dir_mtime > _RACY_SLACK_NS:
        state.save_json(repo, "ops_dirstat", {
            "dir_mtime_ns": dir_mtime, "count": count,
            "max_mtime_ns": max_mtime, "scan_start_ns": scan_start,
        })
    return count, max_mtime


def _stale_against_dir(repo: Path, miner_version, op_count, built_mtime_ns: int) -> bool:
    """The one staleness rule, stat-only: another miner version, another op count, or a build time
    at or before the newest op file's mtime (a provenance merge rewrites a file in place)."""
    if miner_version != MINER_VERSION:
        return True
    count, max_mtime = _ops_dir_stat(repo)
    if op_count != count:
        return True
    return built_mtime_ns <= max_mtime


def _is_stale_body(repo: Path, body: dict | None) -> bool:
    """`is_stale` for a snapshot body already loaded (or None)."""
    if body is None:
        return True
    return _stale_against_dir(
        repo, body.get("miner_version"), body.get("op_count"), body.get("built_mtime_ns", -1)
    )


def is_stale(repo: str | Path) -> bool:
    """True if the index is missing or out of date with `.sgt/ops/` (`_stale_against_dir`)."""
    repo = Path(repo)
    return _is_stale_body(repo, state.load_json(repo, "op_index", default=None))


# The parsed index per repo, keyed by the file's (mtime_ns, size). A hit skips the parse, never
# the staleness check.
_PARSED_MEMO: "OrderedDict[str, tuple[tuple[int, int], object, int, object, list[Op]]]" = OrderedDict()


def _snapshot_stat(repo: Path) -> tuple[int, int] | None:
    try:
        st = state.path(repo, "op_index").stat()
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size)


def index_ops(repo: str | Path) -> list[Op]:
    """Every stored op with `images={}`, rebuilding the index first when it is stale. Never pass
    the result to `fold.code`."""
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
        snap_stat = _snapshot_stat(repo)  # stat before the reload, so a stale key only misses
        body = state.load_json(repo, "op_index", default=None)
    if body is None:
        return []
    ops = [_to_op(entry) for entry in body["ops"]]
    if snap_stat is not None:
        _PARSED_MEMO[repo_key] = (
            snap_stat, body.get("miner_version"), body.get("built_mtime_ns", -1),
            body.get("op_count", -1), ops,
        )
        if len(_PARSED_MEMO) > 8:
            _PARSED_MEMO.popitem(last=False)
    return list(ops)


