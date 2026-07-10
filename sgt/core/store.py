"""Append-only, content-addressed operation store (ADR S3.2; plan R1, R11, R12).

One committed file per op under ``.sgt/ops/<id>`` -- append-only makes push conflicts
structurally impossible for ops: two developers adding different ops never touch the same
file, and the same op mined twice (by different developers, or by a squash/rebase re-mine,
R8) is byte-identical and lands on the same path, so a second write is a no-op beyond
appending a witness. Local, uncommitted state (ref->ideal table, caches, oracle verdicts, and
hollow off-chain ops, R18) lives under ``.sgt/local/``, gitignored -- this module only
guarantees that directory exists with a ``.gitignore`` inside; U6/U9/U14 populate it further.

Concurrency: a single-writer ``flock`` on ``.sgt/lock`` serializes mutating store operations
across processes; every mutable-file write is write-temp-then-rename (``os.replace``, atomic
on POSIX), so a crash mid-write can never leave a torn file for a reader to trip over.

The ADR describes an op's id as a hash over "(payload, parents, miner-version)". This store
deliberately does *not* hash an explicit parents list (see ``sgt.core.op.compute_id``): a
symbol's lineage is already carried by its footprint's ``before_version`` (matched against
another op's ``after_version`` for the same symbol -- U4's chain edges), and hashing parent
*op ids* directly would make the identification law (R8) impossible -- two mining runs that
reach the same content via different provenance paths must still collapse to one id.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path

from sgt.core.op import Images, Op, compute_id

_OPS_DIR = "ops"
_LOCAL_DIR = "local"
_HOLLOW_DIR = "local/hollow"
_LOCK_FILE = "lock"


class StoreError(Exception):
    """The store detected corruption or an attempt to write something that isn't a valid Op."""


def _serialize(op: Op) -> bytes:
    payload = {
        "id": op.id,
        "footprint": {k: list(v) for k, v in sorted(op.footprint.items())},
        "images": {
            k: (v.hex() if v is not None else None) for k, v in sorted(op.images.items())
        },
        "requires": [list(r) for r in sorted(op.requires)],
        "kind": op.kind,
        "provenance": list(op.provenance),
        "intent": op.intent,
        "miner_version": op.miner_version,
        "off_chain": op.off_chain,
    }
    return json.dumps(payload, indent=2, sort_keys=True).encode("utf-8") + b"\n"


def _deserialize(data: bytes) -> Op:
    payload = json.loads(data)
    footprint = {k: tuple(v) for k, v in payload["footprint"].items()}
    images: Images = {
        k: (bytes.fromhex(v) if v is not None else None) for k, v in payload["images"].items()
    }
    return Op(
        id=payload["id"],
        footprint=footprint,
        images=images,
        requires=frozenset(tuple(r) for r in payload["requires"]),
        kind=payload["kind"],
        provenance=tuple(payload["provenance"]),
        intent=payload.get("intent"),
        miner_version=payload["miner_version"],
        off_chain=payload.get("off_chain", False),
    )


def _write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, path)  # atomic rename on POSIX -- readers see old or new, never torn
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
        self.hollow_dir = self.sgt_dir / _HOLLOW_DIR

    def init(self) -> None:
        """Create the store's directories. Idempotent."""
        self.ops_dir.mkdir(parents=True, exist_ok=True)
        self.hollow_dir.mkdir(parents=True, exist_ok=True)
        gitignore = self.local_dir / ".gitignore"
        if not gitignore.exists():
            _write_atomic(gitignore, b"*\n")

    @contextlib.contextmanager
    def _locked(self):
        self.sgt_dir.mkdir(parents=True, exist_ok=True)
        lock_path = self.sgt_dir / _LOCK_FILE
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
        """Append ``op``. Rejects anything whose id doesn't match its own content. If an op
        with the same id already exists, its provenance is unioned (an appendable witness set,
        R8) rather than the file being duplicated or overwritten -- every other field is
        identical by construction (same content address)."""
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

    def add_bytes(self, data: bytes) -> Op:
        """Deserialize a stored op's raw file bytes -- as read from another commit or clone via
        git, not this store's own filesystem -- and add it. `sgt sync` (U15) uses this to union a
        teammate's provenance into an op this store already has: `git merge -X ours` picks our
        bytes on any same-path conflict (the two sides' provenance lists differ though the op's
        content is identical), silently dropping theirs' witness commits unless this re-applies
        `add`'s provenance union afterward."""
        return self.add(_deserialize(data))

    def get(self, op_id: str) -> Op | None:
        path = self._path(op_id)
        if not path.is_file():
            return None
        return _deserialize(path.read_bytes())

    def all_ops(self) -> list[Op]:
        """Every stored op, in a deterministic (sorted-by-id) order."""
        if not self.ops_dir.is_dir():
            return []
        return [
            _deserialize((self.ops_dir / name).read_bytes())
            for name in sorted(p.name for p in self.ops_dir.iterdir() if p.is_file())
        ]

    def __contains__(self, op_id: str) -> bool:
        return self._path(op_id).is_file()

    # -- hollow ops (R18 substrate; workflow lands in U14) -----------------------------------
    def add_hollow(self, op: Op) -> Op:
        """Store a hollow (``off_chain=True``) op under ``.sgt/local/hollow/`` -- never
        committed, never in the main chain, so a human editing the same symbol mid-plan can't
        collide with it."""
        if not op.off_chain:
            raise StoreError(f"add_hollow requires an off_chain op, got {op.id}")
        with self._locked():
            _write_atomic(self.hollow_dir / op.id, _serialize(op))
            return op

    def get_hollow(self, op_id: str) -> Op | None:
        path = self.hollow_dir / op_id
        if not path.is_file():
            return None
        return _deserialize(path.read_bytes())

    def all_hollow_ops(self) -> list[Op]:
        if not self.hollow_dir.is_dir():
            return []
        return [
            _deserialize((self.hollow_dir / name).read_bytes())
            for name in sorted(p.name for p in self.hollow_dir.iterdir() if p.is_file())
        ]


@dataclass(frozen=True)
class FsckReport:
    ok: bool
    checked: int
    bad_hash: tuple[str, ...]  # file name (== claimed id) whose content hashes to something else
    corrupt: tuple[str, ...]  # file name that isn't valid JSON, or is missing a required field


def fsck(repo: str | Path) -> FsckReport:
    """Verify every stored op's content address matches its own content and its filename.
    Repair (re-mining) is a caller concern -- this only reports."""
    store = Store(repo)
    bad_hash: list[str] = []
    corrupt: list[str] = []
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
    return FsckReport(
        ok=not bad_hash and not corrupt,
        checked=len(names),
        bad_hash=tuple(bad_hash),
        corrupt=tuple(corrupt),
    )
