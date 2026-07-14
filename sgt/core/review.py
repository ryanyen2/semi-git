"""Review records: the trust queue's dequeue mechanism (plan U31, S7).

A review record marks an explicit op-set reviewed -- content-addressed by the sorted op-id set,
exactly like a claim (D8) or a proposal (C10), so acking the same op-set twice is a no-op on
content and a teammate's ack arrives verbatim on `sgt sync` (a committed, immutable G-Set, like
claims/proposals -- `materialize._union_reviews`). `trust_view` dequeues any op covered by *any*
review record. This module never retags or reverts an op: organization and rejection are the
existing verbs (`feature move`, `revert --session`), not this one -- the plan's explicit "no other
new mutation semantics" boundary for U31.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from sgt import state

__all__ = ["ReviewRecord", "ack", "load", "all_records", "reviewed_op_ids"]


@dataclass(frozen=True)
class ReviewRecord:
    """One ack of an op-set. `scope` is an advisory human-readable descriptor of what was acked
    (e.g. ``"session:s1"`` or ``"op-set:<n> ops"``) -- not part of identity, since two acks of the
    same op-set from different callers should still dedup to one file."""

    id: str
    op_ids: tuple[str, ...]
    scope: str
    note: str | None
    created_ts: float


def _to_body(r: ReviewRecord) -> dict:
    return {
        "id": r.id, "op_ids": list(r.op_ids), "scope": r.scope, "note": r.note,
        "created_ts": r.created_ts,
    }


def _from_body(body: dict) -> ReviewRecord:
    return ReviewRecord(
        id=body["id"], op_ids=tuple(body["op_ids"]), scope=body.get("scope", ""),
        note=body.get("note"), created_ts=body["created_ts"],
    )


def _mint_id(op_ids) -> str:
    """Content-addressed by the sorted op-id set alone (not `scope`/`note`/time) -- two acks of the
    same op-set converge on one file regardless of who wrote it or what they called it, mirroring
    a claim's `(ideal_key, runner)` keying and a proposal's base+Δ keying."""
    blob = ",".join(sorted(op_ids))
    return sha256(blob.encode("utf-8")).hexdigest()[:12]


def ack(repo: str | Path, op_ids, scope: str, note: str | None = None) -> ReviewRecord:
    """Mark `op_ids` reviewed. Content-addressed by the sorted op-id set, so re-acking the same
    set is a no-op (overwrites the identical file with identical content). Raises `ValueError` on
    an empty op-set -- there is nothing to review."""
    repo = Path(repo)
    ids = tuple(sorted(set(op_ids)))
    if not ids:
        raise ValueError("cannot ack an empty op-set")
    r = ReviewRecord(id=_mint_id(ids), op_ids=ids, scope=scope, note=note, created_ts=time.time())
    state.save_review(repo, f"{r.id}.json", _to_body(r))
    return r


def load(repo: str | Path, review_id: str) -> ReviewRecord | None:
    """The review record with this id from the working tree, or `None` if absent (a pure read)."""
    body = state.load_review(Path(repo), f"{review_id}.json")
    return _from_body(body) if body is not None else None


def all_records(repo: str | Path) -> list[ReviewRecord]:
    """Every review record in the working tree, sorted by id (a pure read)."""
    repo = Path(repo)
    out = [
        _from_body(body)
        for name in state.list_review_files(repo)
        if (body := state.load_review(repo, name)) is not None
    ]
    return sorted(out, key=lambda r: r.id)


def reviewed_op_ids(repo: str | Path) -> frozenset[str]:
    """Every op id covered by any review record -- what `trust_view` dequeues."""
    ids: set[str] = set()
    for r in all_records(repo):
        ids.update(r.op_ids)
    return frozenset(ids)
