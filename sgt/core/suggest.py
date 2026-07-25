"""The clustering / merge suggestion queue (plan U7).

Batch clustering is demoted from an authority to an *advisor* (R4): where it disagrees with what a
user (or the save-time ledger) authored, it never re-partitions -- it drops a suggestion here for
the user to accept or ignore. `sgt.lens.ledger` (U6) writes a `conflict` suggestion when a sync
merge leaves one symbol a live member of two lanes.

Mirrors `sgt.core.review`'s content-addressed record shape, with two differences owed to a
suggestion being a *proposal*, not a decision: it is stored in a single local (never-committed)
table rather than a committed G-Set, and it is *dismissable* -- accepting one (via the existing
`sgt feature merge`/`split`/`move` verbs) or dismissing it removes it, whereas a review ack is
permanent. Accepting is the existing verb surface; this module only records, lists, and drops --
it never mutates the feature tree itself.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from sgt import state

__all__ = ["SuggestionRecord", "add", "load", "all_records", "dismiss"]

_KINDS = ("merge", "split", "conflict")


@dataclass(frozen=True)
class SuggestionRecord:
    """One suggestion. `kind` is `merge` (two lanes couple enough to be one), `split` (a lane holds
    two weakly-coupled groups), or `conflict` (two lanes each claim one symbol -- a cross-clone
    dual-membership, U6). `features` are the lane ids involved; `op_ids` the representative op-set
    the id is content-addressed by; `rationale` a one-line human reason."""

    id: str
    kind: str
    features: tuple[str, ...]
    op_ids: tuple[str, ...]
    rationale: str
    created_ts: float


def _mint_id(kind: str, op_ids) -> str:
    """Content-addressed by `(kind, sorted op-id set)` -- re-emitting the same suggestion overwrites
    the identical key (a no-op), while a `merge` and a `conflict` over the same ops stay distinct."""
    blob = kind + ":" + ",".join(sorted(op_ids))
    return sha256(blob.encode("utf-8")).hexdigest()[:12]


def _load_table(repo: Path) -> dict:
    return state.load_json(repo, "suggestions", default={})


def add(repo: str | Path, kind: str, features, op_ids, rationale: str = "") -> SuggestionRecord:
    """Record a suggestion. Content-addressed, so re-adding the same `(kind, op-set)` is a no-op.
    Raises `ValueError` on an unknown kind or an empty op-set (nothing to key on)."""
    repo = Path(repo)
    if kind not in _KINDS:
        raise ValueError(f"unknown suggestion kind {kind!r} (expected one of {_KINDS})")
    ids = tuple(sorted(set(op_ids)))
    if not ids:
        raise ValueError("cannot record a suggestion over an empty op-set")
    rec = SuggestionRecord(
        id=_mint_id(kind, ids), kind=kind, features=tuple(features), op_ids=ids,
        rationale=rationale, created_ts=time.time(),
    )
    table = _load_table(repo)
    table[rec.id] = {
        "id": rec.id, "kind": rec.kind, "features": list(rec.features),
        "op_ids": list(rec.op_ids), "rationale": rec.rationale, "created_ts": rec.created_ts,
    }
    state.save_json(repo, "suggestions", table)
    return rec


def _from_body(body: dict) -> SuggestionRecord:
    return SuggestionRecord(
        id=body["id"], kind=body["kind"], features=tuple(body.get("features", ())),
        op_ids=tuple(body.get("op_ids", ())), rationale=body.get("rationale", ""),
        created_ts=body.get("created_ts", 0.0),
    )


def load(repo: str | Path, sid: str) -> SuggestionRecord | None:
    """The suggestion with this id, or `None` if absent (a pure read)."""
    body = _load_table(Path(repo)).get(sid)
    return _from_body(body) if body is not None else None


def all_records(repo: str | Path) -> list[SuggestionRecord]:
    """Every open suggestion, sorted by id (a pure read)."""
    return sorted((_from_body(b) for b in _load_table(Path(repo)).values()), key=lambda r: r.id)


def dismiss(repo: str | Path, sid: str) -> bool:
    """Drop a suggestion (the user accepted it via a feature verb, or is ignoring it). Returns
    True if it was present. Accepting a suggestion is the existing `sgt feature merge`/`split`/
    `move` -- this only removes the queue entry."""
    repo = Path(repo)
    table = _load_table(repo)
    if sid not in table:
        return False
    del table[sid]
    state.save_json(repo, "suggestions", table)
    return True
