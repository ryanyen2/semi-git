"""Append-only, content-addressed operation store (ADR S3.2; plan R1, R11, R12).

One committed file per op under ``.sgt/ops/<id>`` -- append-only makes push conflicts
structurally impossible for ops: two developers adding different ops never touch the same
file, and the same op mined twice (by different developers, or by a squash/rebase re-mine,
R8) is byte-identical and lands on the same path, so a second write is a no-op beyond
appending a witness. Local, uncommitted state (ref->ideal table, caches, oracle verdicts, and
hollow off-chain ops, R18) lives under ``.sgt/local/``, gitignored -- this module only
guarantees that directory exists with a ``.gitignore`` inside; U6/U9/U14 populate it further.

Concurrency: a single-writer ``flock`` on ``.sgt/local/lock`` serializes mutating store operations
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

from sgt.core.op import Attribution, Images, Op, compute_id, merge_attribution

_OPS_DIR = "ops"
_LOCAL_DIR = "local"
_HOLLOW_DIR = "local/hollow"
_LOCK_FILE = "lock"


class StoreError(Exception):
    """The store detected corruption or an attempt to write something that isn't a valid Op."""


_ATTR_FIELDS = ("session", "agent", "plan")


def _payload(op: Op) -> dict:
    """`op`'s full on-disk payload (including `images`), shared by `_serialize` and
    `sgt.core.opindex` (which strips `images` back off for its footprint-only sidecar). v1
    provenance shape (D7): a list of `{sha, session?, agent?, plan?}` dicts, one per witnessing
    SHA, folding in any structured attribution for that SHA. Provenance is still excluded from the
    id (`compute_id` untouched), so this is pure on-disk enrichment -- old repos' v0 tuple-of-shas
    files keep round-tripping via `_deserialize`, and no committed op is bulk-rewritten."""
    attr_by_sha = {a.sha: a for a in op.attribution}
    provenance = []
    for sha in sorted(op.provenance):
        entry = {"sha": sha}
        a = attr_by_sha.get(sha)
        if a is not None:
            entry.update({f: getattr(a, f) for f in _ATTR_FIELDS if getattr(a, f) is not None})
        provenance.append(entry)
    return {
        "id": op.id,
        "footprint": {k: list(v) for k, v in sorted(op.footprint.items())},
        "images": {
            k: (v.hex() if v is not None else None) for k, v in sorted(op.images.items())
        },
        "requires": [list(r) for r in sorted(op.requires)],
        "kind": op.kind,
        "provenance": provenance,
        "intent": op.intent,
        "miner_version": op.miner_version,
        "off_chain": op.off_chain,
        "derived": op.derived,
        "resolves": sorted(op.resolves),
    }


def _serialize(op: Op) -> bytes:
    return json.dumps(_payload(op), indent=2, sort_keys=True).encode("utf-8") + b"\n"


def _op_from_payload(payload: dict, images: Images) -> Op:
    """Reconstruct an `Op` from a decoded payload dict plus its already-resolved `images` --
    shared by `_deserialize` (hex-decodes `payload["images"]`) and `sgt.core.opindex` (passes
    `{}`, since the sidecar never stores images at all)."""
    footprint = {k: tuple(v) for k, v in payload["footprint"].items()}
    prov = payload["provenance"]
    if prov and isinstance(prov[0], dict):  # v1: a list of `{sha, session?, ...}` dicts
        provenance = tuple(sorted(e["sha"] for e in prov))
        attribution = tuple(sorted(
            (
                Attribution(sha=e["sha"], **{f: e.get(f) for f in _ATTR_FIELDS})
                for e in prov
                if any(e.get(f) is not None for f in _ATTR_FIELDS)
            ),
            key=lambda a: a.sha,
        ))
    else:  # v0 (every committed repo today): a list of bare SHA strings, no attribution
        provenance = tuple(prov)
        attribution = ()
    return Op(
        id=payload["id"],
        footprint=footprint,
        images=images,
        requires=frozenset(tuple(r) for r in payload["requires"]),
        kind=payload["kind"],
        provenance=provenance,
        attribution=attribution,
        intent=payload.get("intent"),
        miner_version=payload["miner_version"],
        off_chain=payload.get("off_chain", False),
        derived=payload.get("derived", False),
        resolves=frozenset(payload.get("resolves", [])),
    )


def _deserialize(data: bytes) -> Op:
    payload = json.loads(data)
    images: Images = {
        k: (bytes.fromhex(v) if v is not None else None) for k, v in payload["images"].items()
    }
    return _op_from_payload(payload, images)


@contextlib.contextmanager
def locked_section(repo: str | Path):
    """Hold the store's per-mutation flock across a multi-artifact read-modify-write, so a pair
    that must stay mutually consistent (ideal table + witness; journal push + table overwrite;
    forks + the ops they name) is computed and written under one lock, each file landing via
    atomic rename (R5/R6). Same lock and per-mutation granularity as `Store.add()` -- deliberately
    NOT verb-wide (U23). MUST NOT nest inside another `locked_section` or a `Store.add()` call:
    `flock` on a second fd of the same lock file blocks this very process (a self-deadlock), so a
    caller adds its ops *before* entering the section and writes only metadata inside it."""
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
        self._ensure_local()

    def _ensure_local(self) -> None:
        """`.sgt/local/` exists and ignores itself (never committed)."""
        gitignore = self.local_dir / ".gitignore"
        if not gitignore.exists():
            _write_atomic(gitignore, b"*\n")

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
        """Append ``op``. Rejects anything whose id doesn't match its own content. If an op
        with the same id already exists, its provenance *and* structured attribution are unioned
        (both appendable, R8/D7) rather than the file being duplicated or overwritten -- every
        other field is identical by construction (same content address). The file is rewritten
        only when either side actually changed."""
        self._validate(op)
        with self._locked():
            path = self._path(op.id)
            if path.exists():
                existing = _deserialize(path.read_bytes())
                merged_provenance = tuple(sorted(set(existing.provenance) | set(op.provenance)))
                merged_attribution = merge_attribution(existing.attribution, op.attribution)
                if (merged_provenance == existing.provenance
                        and merged_attribution == existing.attribution):
                    return existing
                merged = replace(
                    existing, provenance=merged_provenance, attribution=merged_attribution
                )
                _write_atomic(path, _serialize(merged))
                return merged
            _write_atomic(path, _serialize(op))
            return op

    def attribute(self, op_id: str, entries: tuple[Attribution, ...]) -> Op | None:
        """Merge structured attribution `entries` into a committed op's provenance shape (D7),
        rewriting its file only when the merge actually changes something. Returns the updated op,
        or ``None`` if no committed op with that id exists -- hollow ops (never committed) are not
        attributed."""
        with self._locked():
            path = self._path(op_id)
            if not path.is_file():
                return None
            existing = _deserialize(path.read_bytes())
            merged = merge_attribution(existing.attribution, entries)
            if merged == existing.attribution:
                return existing
            updated = replace(existing, attribution=merged)
            _write_atomic(path, _serialize(updated))
            return updated

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
        """Every stored op, in a deterministic (sorted-by-id) order. A corrupt file degrades to a
        read-side skip (R1) rather than raising, so every verb still runs on a store with one
        truncated op file; `fsck` is the single place that reports the corruption."""
        if not self.ops_dir.is_dir():
            return []
        ops: list[Op] = []
        for name in sorted(p.name for p in self.ops_dir.iterdir() if p.is_file()):
            try:
                ops.append(_deserialize((self.ops_dir / name).read_bytes()))
            except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                continue  # corrupt: skipped here, surfaced by fsck
        return ops

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
    # R11 completion (U2). All default () so a healthy store reports exactly the two fields above.
    chain_gaps: tuple[str, ...] = ()          # `sym@version` steps produced by no op (advisory:
    # real single-clone histories carry benign off-ref-predecessor gaps, so this never flips `ok`)
    invalid_ideals: tuple[str, ...] = ()      # ref keys whose stored ideal isn't a valid ideal
    unreachable_witnesses: tuple[str, ...] = ()  # ref keys whose witness SHA no longer resolves
    mixed_versions: tuple[str, ...] = ()      # distinct miner_versions present, only when >1 (U10)
    pending_land: tuple[str, ...] = ()        # a `land` crashed mid-flight; the ref it was advancing
    # (U5/R7). Advisory: the next `sgt land` auto-recovers by rolling back to the journaled snapshot,
    # so this names an interrupted-but-recoverable state rather than corruption -- never flips `ok`.
    op_index_stale: bool = False  # the `sgt.core.opindex` sidecar is out of date. Advisory, like
    # `mixed_versions` -- the next read self-heals via a rebuild, so this only surfaces that the
    # *next* read view pays that rebuild cost rather than the cheap incremental path.
    pending_chain_gaps: tuple[str, ...] = ()  # `chain_gaps` entries that sit exactly at a ref's
    # in-progress genesis-backfill frontier (U5) -- expected, self-healing once the backfill
    # finishes, so these are split out of `chain_gaps` and never flip `ok`.


def _chain_gaps(ops: list[Op]) -> dict[str, set[str]]:
    """Every `symbol@before_version` step whose predecessor version is produced by no op in the
    store -- the R11 linearity check -- mapped to the provenance shas of the op(s) that reference
    it. Advisory: a squashed/rebased-away branch legitimately leaves an off-ref predecessor gap
    (FINDINGS U22.5), so a gap is reported, never treated as corruption. The provenance is returned
    so callers (U5) can tell a genuine gap apart from one that sits at an in-progress backfill's
    frontier -- the oldest op mined by a chunked backward walk carries the frontier sha as its own
    provenance."""
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
    """The R11 contract (U2): content addresses, chain linearity, ideal validity of every stored
    ideal-table entry, and witness reachability, plus a mixed-miner-version backstop (U10). A
    corrupt op file degrades to a reported skip -- no verb crashes on it. Repair (re-mining) is a
    caller concern; this only reports."""
    from sgt import state
    from sgt.core import opindex, order
    from sgt.store.gitbind import GitBinding

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
        # `rev_parse(sha)` only checks syntax; peel to `^{commit}` so a well-formed but absent
        # object (a deleted branch's tip) is reported as unreachable rather than silently accepted.
        if not sha or gb.rev_parse(f"{sha}^{{commit}}") is None:
            unreachable.append(key)

    mixed = tuple(sorted(versions)) if len(versions) > 1 else ()

    # A `land` crashed mid-flight leaves its journal behind (U5/R7); name the interrupted ref.
    pending = state.load_json(repo, "land_pending", default=None)
    pending_land = (pending["ref"],) if pending and pending.get("ref") else ()

    # Split chain gaps that sit exactly at an in-progress genesis-backfill's frontier (U5) out of
    # the confirmed set: a ref whose chunked backward walk (U3/U4) hasn't reached genesis yet
    # leaves its oldest-mined op referencing a predecessor version one chunk further back, which is
    # indistinguishable from a genuine gap by footprint alone -- provenance is what tells them apart.
    open_frontiers = {
        entry["genesis_frontier"]
        for entry in state.load_json(repo, "backfill", default={}).values()
        if entry.get("genesis_frontier") and not entry.get("reached_genesis", False)
    }
    gap_provenance = _chain_gaps(ops)
    pending_chain_gaps = sorted(g for g, prov in gap_provenance.items() if prov & open_frontiers)
    confirmed_chain_gaps = sorted(g for g, prov in gap_provenance.items() if not (prov & open_frontiers))

    return FsckReport(
        ok=not (bad_hash or corrupt or invalid_ideals or unreachable or mixed),
        checked=len(names),
        bad_hash=tuple(bad_hash),
        corrupt=tuple(corrupt),
        chain_gaps=tuple(confirmed_chain_gaps),
        invalid_ideals=tuple(sorted(invalid_ideals)),
        unreachable_witnesses=tuple(sorted(unreachable)),
        mixed_versions=mixed,
        pending_land=pending_land,
        op_index_stale=opindex.is_stale(repo),
        pending_chain_gaps=tuple(pending_chain_gaps),
    )
