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
from sgt.core.op import MINER_VERSION, Op
from sgt.core.store import Store, _deserialize
from sgt.lens import reconcile, tree
from sgt.lens.pins import Pins, _pins_from_payload, load_pins
from sgt.store.gitbind import GitBinding, parse_op_ids


class MinerVersionMismatch(Exception):
    """Theirs' ops were mined by a different `miner_version` than ours (design doc §2.1, §5.1.5,
    C6). `miner_version` is inside `compute_id`, so the two sides mint *different* ids for the same
    edit -- uniting the stores would alias incompatible op semantics. A version skew is a protocol
    event, not silent corruption: sync refuses the whole union with instructions rather than
    merging across it. Not a `GitError`/`ValueError` -- it is neither a git failure nor bad input,
    so the CLI catches it distinctly."""


@dataclass(frozen=True)
class Ingested:
    ours_pins: Pins
    theirs_pins: Pins
    ours_declared_orset: lens.DeclaredORSet
    theirs_declared_orset: lens.DeclaredORSet
    ours_aliases: frozenset[tuple[str, str]]
    theirs_aliases: frozenset[tuple[str, str]]
    ours_tree: dict | None
    ours_ideal: Ideal
    theirs_ideal_ids: frozenset[str]
    all_ops: list[Op]  # in-memory union of ours' store and theirs' op files, sorted by id
    theirs_ops: list[Op]  # theirs' op files as parsed, for `materialize` to persist for real
    mined_ops: list[Op]  # theirs' foreign commits mined on contact (C3), for `materialize` too
    ops_added: int


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

    # Miner-version handshake (C6): a precondition, before any union is built. Theirs' op files
    # (mined by whatever sgt version committed them) are the only ones that can carry a foreign
    # version -- the mined ops are minted by this process -- but both are checked.
    _check_miner_versions([*theirs_ops, *mined_ops])

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
        ours_declared_orset=lens.load_declared_orset(repo),
        theirs_declared_orset=lens.declared_orset_at(gb, theirs_sha),
        ours_aliases=reconcile.load_aliases(repo),
        theirs_aliases=reconcile.aliases_at(gb, theirs_sha),
        ours_tree=tree.load(repo),
        ours_ideal=lens.current_ideal(repo),
        theirs_ideal_ids=theirs_ideal_ids,
        all_ops=all_ops,
        theirs_ops=theirs_ops,
        mined_ops=mined_ops,
        ops_added=ops_added,
    )


def _tip_witnesses_ideal(gb: GitBinding, sha: str) -> bool:
    """True iff commit `sha` actually wrote `.sgt/ideal.json` (its blob differs from the first
    parent's, or is newly added). Distinguishes a squash-merge -- whose tree carries a real
    witness's ideal record forward, so the record describes `sha`'s own code -- from a plain-git
    commit that merely inherited a *stale* record from an earlier witness without touching it."""
    cur = gb.blob_oid(sha, state.rel("ideal"))
    if cur is None:
        return False
    parent = gb.parent_of(sha)
    prev = gb.blob_oid(parent, state.rel("ideal")) if parent is not None else None
    return cur != prev


def _check_miner_versions(ops: list[Op]) -> None:
    """Refuse the sync if any of `ops` was mined by a version other than ours (C6). Reports every
    foreign version seen and which side is behind, so the user knows exactly what to upgrade."""
    foreign = sorted({op.miner_version for op in ops if op.miner_version != MINER_VERSION})
    if not foreign:
        return
    behind = "theirs" if all(v < MINER_VERSION for v in foreign) else "ours"
    raise MinerVersionMismatch(
        f"refusing to union op stores across miner versions: theirs carries "
        f"{', '.join(foreign)}, ours is {MINER_VERSION} -- the {behind} side is behind. "
        f"Upgrade sgt on the {behind} side, re-run `sgt log` to re-mine, then sync again."
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

    # No trailers (GitHub squash-merges destroy them by default). Recover from the committed
    # in-tree `.sgt/ideal.json` (C5) when theirs' tip is a *witness* of it -- i.e. the tip commit
    # actually wrote that ideal, so the record describes the tip's own code (a squash carries the
    # branch's witness tree forward). The fine-grained ops still live in `.sgt/ops/` blobs (read as
    # `theirs_ops`), so this identifies rather than re-mining -- re-mining a squash would mint
    # *coarse* ops (§2.1 path-dependence) that fork against those fine ops. A stale record inherited
    # by a later foreign commit (a plain-git hotfix that never touched `.sgt/ideal.json`) does not
    # describe that commit's code, so it is *not* trusted -- that falls through to mining below.
    if _tip_witnesses_ideal(gb, theirs_sha):
        recovered = state.load_blob_json(gb, theirs_sha, "ideal")
        if recovered is not None:
            return frozenset(recovered), []

    # Neither trailers nor an in-tree ideal record: theirs never ran sgt. Mine theirs' commits as
    # if sgt had been tracking theirs' branch all along. LAW-0 makes these byte-identical to the
    # ops theirs' own `sgt init` would mint, so a later adoption self-dedups (AE8). Theirs'
    # divergent ops alone form its ideal contribution -- the shared base below `merge_base` already
    # rides in `ours_ideal`, so the union covers it.
    base = gb.merge_base(ours_sha, theirs_sha)
    mined = mine(repo, since=base, target=theirs_sha)
    return frozenset(op.id for op in mined), mined
