"""The op store: one file per op under ``.sgt/ops/<id>``, append-only and content-addressed. Ops
are untracked and every clone mines its own; the same op mined twice lands on the same path and
only its provenance grows. A ``flock`` on ``.sgt/local/lock`` serializes writers, and every write
is temp-then-rename, so a reader never sees a torn file.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import tempfile
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from pathlib import Path

from ugh.core.op import Images, Op, compute_id

# `Store.all_ops` memo: ops-dir realpath -> ((dir mtime_ns, dirent count), ops, scan-start time).
# A scan that started well after the dir's mtime stays current while that mtime is unchanged.
_ALL_OPS_MEMO: "OrderedDict[str, tuple[tuple[int, int], list[Op], int]]" = OrderedDict()
_RACY_SLACK_NS = 2_000_000_000  # > any real filesystem timestamp granularity (HFS+: 1s)

_OPS_DIR = "ops"
_LOCAL_DIR = "local"
_LOCK_FILE = "lock"

# `.sgt/.gitignore`, relative to `.sgt/`; `.sgt/local/` ignores itself through its own.
_SGT_GITIGNORE = b"""\
# Managed by ugh: the op store is mined from history on every clone, and names and prompts
# travel on refs/ugh/state, off the branch tree. Do not add tiers.json / oracle.json /
# .sgtignore here -- those stay tracked in the branch tree.
.gitignore
ops/
intent/
names.json
"""


class StoreError(Exception):
    """The store detected corruption or an attempt to write something that isn't a valid Op."""


def _payload(op: Op) -> dict:
    """`op`'s on-disk payload, images included. Provenance is a list of `{sha}`, one per witnessing
    SHA; `_deserialize` also reads the older list of bare SHAs."""
    return {
        "id": op.id,
        "footprint": {k: list(v) for k, v in sorted(op.footprint.items())},
        "images": {
            k: (v.hex() if v is not None else None) for k, v in sorted(op.images.items())
        },
        "requires": [list(r) for r in sorted(op.requires)],
        "kind": op.kind,
        "provenance": [{"sha": sha} for sha in sorted(op.provenance)],
        "miner_version": op.miner_version,
        "derived": op.derived,
    }


def _serialize(op: Op) -> bytes:
    return json.dumps(_payload(op), indent=2, sort_keys=True).encode("utf-8") + b"\n"


def _op_from_payload(payload: dict, images: Images) -> Op:
    """An `Op` from a decoded payload and its already-decoded `images` (`{}` from the op index)."""
    footprint = {k: tuple(v) for k, v in payload["footprint"].items()}
    prov = payload["provenance"]
    if prov and isinstance(prov[0], dict):  # v1: a list of `{sha}` dicts
        provenance = tuple(sorted(e["sha"] for e in prov))
    else:  # v0: a list of bare SHA strings
        provenance = tuple(prov)
    # Filling `__dict__` skips the frozen dataclass's per-field `object.__setattr__`, which
    # dominates loading the index; it raises if `Op` ever gains `__slots__`.
    reqs = payload["requires"]
    op = object.__new__(Op)
    op.__dict__.update(
        id=payload["id"],
        footprint=footprint,
        images=images,
        requires=frozenset(map(tuple, reqs)) if reqs else frozenset(),
        kind=payload["kind"],
        provenance=provenance,
        miner_version=payload["miner_version"],
        derived=payload.get("derived", False),
    )
    return op


def _deserialize(data: bytes) -> Op:
    payload = json.loads(data)
    images: Images = {
        k: (bytes.fromhex(v) if v is not None else None) for k, v in payload["images"].items()
    }
    return _op_from_payload(payload, images)


@contextlib.contextmanager
def locked_section(repo: str | Path):
    """Hold the store's flock across a read-modify-write of tables that must stay consistent
    (the ideal table and the witness). It must not nest inside another `locked_section` or a
    `Store.add()`: flock on a second fd of the same file deadlocks this process, so add ops first."""
    with Store(repo)._locked():
        yield


def _write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, path)  # atomic on POSIX: a reader sees old or new, never torn
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise


class Store:
    """The op store for one repo's ``.sgt/`` directory."""

    def __init__(self, repo: str | Path) -> None:
        self.repo = Path(repo)
        self.sgt_dir = self.repo / ".sgt"
        self.ops_dir = self.sgt_dir / _OPS_DIR
        self.local_dir = self.sgt_dir / _LOCAL_DIR

    def init(self) -> None:
        """Create the store's directories. Idempotent."""
        self.ops_dir.mkdir(parents=True, exist_ok=True)
        self._ensure_local()
        self._ensure_gitignore()

    def _ensure_local(self) -> None:
        """`.sgt/local/` exists and ignores itself (never committed)."""
        gitignore = self.local_dir / ".gitignore"
        if not gitignore.exists():
            _write_atomic(gitignore, b"*\n")

    def _ensure_gitignore(self) -> None:
        """Write `.sgt/.gitignore` when it differs, so `git add -A` never sees the op store or the
        tables on the state ref. It ignores itself too: committed on one branch only, it would make
        `git checkout` abort over an untracked file. `oracle.json` and `tiers.json` stay tracked."""
        gitignore = self.sgt_dir / ".gitignore"
        if gitignore.is_file() and gitignore.read_bytes() == _SGT_GITIGNORE:
            return
        _write_atomic(gitignore, _SGT_GITIGNORE)

    @contextlib.contextmanager
    def _locked(self):
        self._ensure_local()
        lock_path = self.local_dir / _LOCK_FILE
        with open(lock_path, "w") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)

    def _validate(self, op: Op) -> None:
        expected = compute_id(op.footprint, op.images, op.requires, op.kind, op.miner_version)
        if expected != op.id:
            raise StoreError(f"op {op.id} does not hash to its own content (expected {expected})")

    def _path(self, op_id: str) -> Path:
        return self.ops_dir / op_id

    def add(self, op: Op) -> Op:
        """Append ``op`` and return the stored op. Raises `StoreError` when the id does not match
        the content. An op already stored gets the union of both provenances, rewritten only when
        that changes it."""
        self._validate(op)
        with self._locked():
            path = self._path(op.id)
            if path.exists():
                existing = _deserialize(path.read_bytes())
                merged_provenance = tuple(sorted(set(existing.provenance) | set(op.provenance)))
                if merged_provenance == existing.provenance:
                    return existing
                merged = replace(existing, provenance=merged_provenance)
                _write_atomic(path, _serialize(merged))
                return merged
            _write_atomic(path, _serialize(op))
            return op

    def get(self, op_id: str) -> Op | None:
        path = self._path(op_id)
        if not path.is_file():
            return None
        return _deserialize(path.read_bytes())

    def all_ops(self) -> list[Op]:
        """Every stored op, sorted by id. A corrupt file is skipped here and reported by `fsck`.
        Memoized per process on the ops directory's mtime and entry count; the list is copied per
        call, the frozen ops are shared."""
        if not self.ops_dir.is_dir():
            return []
        # Every write renames into this directory, which bumps its mtime; the entry count catches
        # a write landing in the same mtime tick as the memoized read.
        scan_start = time.time_ns()
        sig = self.ops_dir.stat().st_mtime_ns
        memo_key = os.path.realpath(self.ops_dir)
        memo = _ALL_OPS_MEMO.get(memo_key)
        if memo is not None and memo[0][0] == sig and memo[2] - sig > _RACY_SLACK_NS:
            _ALL_OPS_MEMO.move_to_end(memo_key)
            return list(memo[1])
        names = sorted(e.name for e in os.scandir(self.ops_dir) if e.is_file())
        if memo is not None and memo[0] == (sig, len(names)):
            _ALL_OPS_MEMO[memo_key] = (memo[0], memo[1], scan_start)
            _ALL_OPS_MEMO.move_to_end(memo_key)
            return list(memo[1])
        with ThreadPoolExecutor(max_workers=8) as pool:
            raws = list(pool.map(lambda n: (self.ops_dir / n).read_bytes(), names))
        ops: list[Op] = []
        for raw in raws:
            try:
                ops.append(_deserialize(raw))
            except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                continue  # corrupt: skipped here, surfaced by fsck
        _ALL_OPS_MEMO[memo_key] = ((sig, len(names)), ops, scan_start)
        if len(_ALL_OPS_MEMO) > 4:
            _ALL_OPS_MEMO.popitem(last=False)
        return list(ops)

    def __contains__(self, op_id: str) -> bool:
        return self._path(op_id).is_file()


@dataclass(frozen=True)
class FsckReport:
    """What `fsck` found. Chain gaps and a stale op index are advisory and leave `ok` true."""

    ok: bool
    checked: int
    bad_hash: tuple[str, ...]  # file name (== claimed id) whose content hashes to something else
    corrupt: tuple[str, ...]  # file name that isn't valid JSON, or is missing a required field
    chain_gaps: tuple[str, ...] = ()          # `sym@version` produced by no op; advisory
    invalid_ideals: tuple[str, ...] = ()      # ref keys whose stored ideal isn't a valid ideal
    unreachable_witnesses: tuple[str, ...] = ()  # ref keys whose witness SHA no longer resolves
    mixed_versions: tuple[str, ...] = ()      # distinct miner_versions present, only when >1
    op_index_stale: bool = False  # advisory: the next read rebuilds the op index


def _chain_gaps(ops: list[Op]) -> dict[str, set[str]]:
    """Every `symbol@before_version` that no op in the store produces, mapped to the provenance of
    the ops starting from it. Advisory: a branch rebased away leaves such gaps legitimately."""
    produced: set[tuple[str, str]] = set()
    for op in ops:
        for sym, (_before, after) in op.footprint.items():
            produced.add((sym, after))
    gaps: dict[str, set[str]] = {}
    for op in ops:
        for sym, (before, _after) in op.footprint.items():
            if before is not None and (sym, before) not in produced:
                gaps.setdefault(f"{sym}@{before}", set()).update(op.provenance)
    return gaps


def fsck(repo: str | Path) -> FsckReport:
    """Report on the store: content addresses, chain gaps, the validity of every cached ideal, the
    reachability of every witness, and mixed miner versions. It reports and repairs nothing."""
    from ugh import state
    from ugh.core import opindex, order
    from ugh.store.gitbind import GitBinding

    store = Store(repo)
    bad_hash: list[str] = []
    corrupt: list[str] = []
    ops: list[Op] = []
    versions: set[str] = set()
    names = sorted(p.name for p in store.ops_dir.iterdir() if p.is_file()) if store.ops_dir.is_dir() else []
    for name in names:
        try:
            op = _deserialize((store.ops_dir / name).read_bytes())
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            corrupt.append(name)
            continue
        if op.id != name:
            corrupt.append(name)
            continue
        expected = compute_id(op.footprint, op.images, op.requires, op.kind, op.miner_version)
        if expected != op.id:
            bad_hash.append(op.id)
            continue
        ops.append(op)
        versions.add(op.miner_version)

    # Ideal validity: every persisted per-ref ideal must be a valid ideal over the readable ops.
    invalid_ideals: list[str] = []
    for key, ids in state.load_json(repo, "ideal_table", default={}).items():
        if not order.is_valid_ideal(ops, set(ids)):
            invalid_ideals.append(key)

    # Witness reachability: every recorded witness SHA must still resolve in git.
    unreachable: list[str] = []
    gb = GitBinding(repo)
    for key, sha in state.load_json(repo, "witness", default={}).items():
        # peel to `^{commit}`: a bare `rev_parse(sha)` accepts a well-formed sha of a missing object
        if not sha or gb.rev_parse(f"{sha}^{{commit}}") is None:
            unreachable.append(key)

    mixed = tuple(sorted(versions)) if len(versions) > 1 else ()

    return FsckReport(
        ok=not (bad_hash or corrupt or invalid_ideals or unreachable or mixed),
        checked=len(names),
        bad_hash=tuple(bad_hash),
        corrupt=tuple(corrupt),
        chain_gaps=tuple(sorted(_chain_gaps(ops))),
        invalid_ideals=tuple(sorted(invalid_ideals)),
        unreachable_witnesses=tuple(sorted(unreachable)),
        mixed_versions=mixed,
        op_index_stale=opindex.is_stale(repo),
    )
