"""Sync stage 2 -- capture both sides and union the op store *in memory* (plan U19, D4).

Everything here is read-only against disk: ours' pins/declared/tree/ideal from the working tree,
theirs' from blobs at `theirs_sha` (no checkout). The op-store union is built in memory too --
theirs' op files are read as raw blobs and parsed (never `store.add`-ed here) -- so the downstream
fork check can run before anything is persisted. That ordering is load-bearing: the old pipeline
leaned on `git merge --abort` to roll back a real merge on a fork; explicit tree construction has
no merge to abort, so a fork must be detected before any disk write, leaving nothing to undo.

The same `ingest` stage is reused standalone as adoption-on-contact (U20) and, with a local source
in place of a remote fetch, as the first half of `land` (U23) -- so it reads its inputs only from
what it's handed (`repo`, `gb`, `theirs_sha`), never assuming a network fetch ran.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

from sgt import state
from sgt.core import lens
from sgt.core.ideal import Ideal
from sgt.core.mine import mine
from sgt.core.op import Op
from sgt.core.store import Store, _deserialize
from sgt.lens import tree
from sgt.lens.pins import Pins, _pins_from_payload, load_pins
from sgt.store.gitbind import GitBinding, parse_op_ids


@dataclass(frozen=True)
class Ingested:
    ours_pins: Pins
    theirs_pins: Pins
    ours_declared: frozenset[tuple[str, str]]
    theirs_declared: frozenset[tuple[str, str]]
    ours_tree: dict | None
    ours_ideal: Ideal
    theirs_ideal_ids: frozenset[str]
    all_ops: list[Op]  # in-memory union of ours' store and theirs' op files, sorted by id
    theirs_ops: list[Op]  # theirs' op files as parsed, for `materialize` to persist for real
    mined_ops: list[Op]  # theirs' foreign commits mined on contact (C3), for `materialize` too
    ops_added: int


def _declared_at(gb: GitBinding, sha: str) -> frozenset[tuple[str, str]]:
    body = state.load_blob_json(gb, sha, "declared")
    return frozenset() if body is None else frozenset(tuple(pair) for pair in body)


def _pins_at(gb: GitBinding, sha: str) -> Pins:
    body = state.load_blob_json(gb, sha, "pins")
    return Pins() if body is None else _pins_from_payload(body)


def ingest(repo: Path, gb: GitBinding, theirs_sha: str, ours_sha: str) -> Ingested:
    ours_ops = Store(repo).all_ops()
    theirs_ops: list[Op] = []
    for path in gb.list_tree(theirs_sha, ".sgt/ops/"):
        raw = gb.blob_bytes(theirs_sha, path)
        if raw is not None:
            theirs_ops.append(_deserialize(raw))

    # Recover theirs' ideal, and mine foreign commits when there's no sgt record to read (C3/C5).
    theirs_ideal_ids, mined_ops = _theirs_ideal(repo, gb, theirs_sha, ours_sha)

    # Mirror `Store.add`'s provenance union on an id collision, in memory -- so `all_ops` matches
    # what `materialize` will persist, without any op file being written yet. Theirs' op files and
    # its mined foreign commits both fold in here; content-addressing dedups an op present in both.
    union = {op.id: op for op in ours_ops}
    for op in [*theirs_ops, *mined_ops]:
        existing = union.get(op.id)
        if existing is None:
            union[op.id] = op
        else:
            merged = tuple(sorted(set(existing.provenance) | set(op.provenance)))
            union[op.id] = replace(existing, provenance=merged)
    all_ops = [union[k] for k in sorted(union)]
    theirs_all_ids = {op.id for op in theirs_ops} | {op.id for op in mined_ops}
    ops_added = len(theirs_all_ids - {op.id for op in ours_ops})

    return Ingested(
        ours_pins=load_pins(repo),
        theirs_pins=_pins_at(gb, theirs_sha),
        ours_declared=lens._load_declared(repo),
        theirs_declared=_declared_at(gb, theirs_sha),
        ours_tree=tree.load(repo),
        ours_ideal=lens.current_ideal(repo),
        theirs_ideal_ids=theirs_ideal_ids,
        all_ops=all_ops,
        theirs_ops=theirs_ops,
        mined_ops=mined_ops,
        ops_added=ops_added,
    )


def _theirs_ideal(
    repo: Path, gb: GitBinding, theirs_sha: str, ours_sha: str
) -> tuple[frozenset[str], list[Op]]:
    """Theirs' committed ideal, recovered by the most authoritative record available and, when
    none exists, by mining theirs' foreign commits (C3, the "adoption ⊂ sync, one code path" the
    remote side dropped). Returns `(ideal_ids, mined_ops)`; `mined_ops` is non-empty only on the
    mine path, so a squash-merged sgt branch reads its fine-grained ops from `.sgt/ops/` blobs
    rather than re-mining the coarse squash (§2.1 path-dependence)."""
    trailer_ids = frozenset(parse_op_ids(gb.commit_message(theirs_sha)))
    if trailer_ids:
        return trailer_ids, []  # theirs' tip is sgt-native -- trailers are authoritative

    # No trailers (a squash-merge destroyed them, or theirs never ran sgt): theirs' commits are
    # mined as if sgt had been tracking theirs' branch all along. LAW-0 makes these byte-identical
    # to the ops theirs' own `sgt init` would mint, so a later adoption self-dedups (AE8). Theirs'
    # divergent ops alone form its ideal contribution -- the shared base below `merge_base` already
    # rides in `ours_ideal`, so the union covers it.
    base = gb.merge_base(ours_sha, theirs_sha)
    mined = mine(repo, since=base, target=theirs_sha)
    return frozenset(op.id for op in mined), mined
