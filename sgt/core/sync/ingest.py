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

import json
from dataclasses import dataclass, field, replace
from pathlib import Path

from sgt import state
from sgt.core import lens
from sgt.core.ideal import Ideal
from sgt.core.mine import mine
from sgt.core.op import MINER_VERSION, Op, merge_attribution
from sgt.core.store import Store, _deserialize
from sgt.intent import prompts as intent_prompts
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
    # U7/R12: the merge-base's recovered *full* ideal (for U8's three-way subtraction) and how it
    # was recovered -- `trailers` | `ideal-record` | `mined` | `none`. `none` (no merge-base, or a
    # base sgt can't witness) means the base degrades to ∅ and resolve keeps today's union semantics.
    base_ideal_ids: frozenset[str] = frozenset()
    base_recovery: str = "none"
    # How *theirs' tip* ideal was recovered, same vocabulary. `none` here is the footgun state: the
    # tip carries `.sgt/ops` blobs (it ran sgt) but lost its trailers and has no witnessed record,
    # so it degrades to ∅ with a remedy rather than mis-mining a coarse squash.
    theirs_recovery: str = "mined"
    # Intent overlay prompt sidecar (U3/KTD5), unioned by key in `resolve` (U5) -- defaulted like
    # every other post-U15 addition here so a pre-existing direct `Ingested(...)` construction
    # (tests) doesn't need to know about it.
    ours_prompts: dict[str, str] = field(default_factory=dict)
    theirs_prompts: dict[str, str] = field(default_factory=dict)


def _pins_at(gb: GitBinding, sha: str) -> Pins:
    body = state.load_blob_json(gb, sha, "pins")
    return Pins() if body is None else _pins_from_payload(body)


def ingest(repo: Path, gb: GitBinding, theirs_sha: str, ours_sha: str) -> Ingested:
    ours_ops = Store(repo).all_ops()
    theirs_ops: list[Op] = []
    for path in gb.list_tree(theirs_sha, ".sgt/ops/"):
        raw = gb.blob_bytes(theirs_sha, path)
        if raw is None:
            continue
        try:
            theirs_ops.append(_deserialize(raw))
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            continue  # a corrupt op blob in theirs' tree degrades to a read-side skip (R1)

    # Recover theirs' ideal, and mine foreign commits when there's no sgt record to read (C3/C5).
    theirs_ideal_ids, mined_ops, theirs_recovery = _theirs_ideal(repo, gb, theirs_sha, ours_sha)

    # Recover the *merge-base's* full ideal for U8's three-way subtraction (R12). Distinct from
    # theirs' divergent contribution above: used as a base, the divergent set would mass-delete, so
    # a base must be a *full* ideal or ∅. Same witness discipline as the tip.
    base_sha = gb.merge_base(ours_sha, theirs_sha)
    base_ideal_ids, base_recovery = recover_base(repo, gb, base_sha)

    # Miner-version handshake (C6): a precondition, before any union is built. Theirs' op files
    # (mined by whatever sgt version committed them) are the only ones that can carry a foreign
    # version -- the mined ops are minted by this process -- but both are checked.
    _check_miner_versions(ours_ops, [*theirs_ops, *mined_ops])

    # Mirror `Store.add`'s provenance union on an id collision, in memory -- so `all_ops` matches
    # what `materialize` will persist, without any op file being written yet. Theirs' op files and
    # its mined foreign commits both fold in here; content-addressing dedups an op present in both.
    union = {op.id: op for op in ours_ops}
    for op in [*theirs_ops, *mined_ops]:
        existing = union.get(op.id)
        if existing is None:
            union[op.id] = op
        else:
            merged_prov = tuple(sorted(set(existing.provenance) | set(op.provenance)))
            merged_attr = merge_attribution(existing.attribution, op.attribution)
            union[op.id] = replace(existing, provenance=merged_prov, attribution=merged_attr)
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
        ours_prompts=intent_prompts.load_prompts(repo),
        theirs_prompts=intent_prompts.prompts_at(gb, theirs_sha),
        ours_tree=tree.load(repo),
        ours_ideal=lens.current_ideal(repo),
        theirs_ideal_ids=theirs_ideal_ids,
        all_ops=all_ops,
        theirs_ops=theirs_ops,
        mined_ops=mined_ops,
        ops_added=ops_added,
        base_ideal_ids=base_ideal_ids,
        base_recovery=base_recovery,
        theirs_recovery=theirs_recovery,
    )


def _witnessed(gb: GitBinding, sha: str, ids: frozenset[str]) -> bool:
    """True iff every op-id in `ids` is present as a ``.sgt/ops/<id>`` blob in `sha`'s tree (R12).
    A recovered ideal is trusted only when the commit claiming it actually carries every op it
    names, so a *forged* trailer (an id the tree never produced) or a stale record whose ops were
    squashed away is rejected -- a recovered ideal is full-and-witnessed or unusable, never partial.
    The empty ideal is vacuously witnessed."""
    if not ids:
        return True
    present = {p.rsplit("/", 1)[-1] for p in gb.list_tree(sha, ".sgt/ops/")}
    return ids <= present


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


def _check_miner_versions(ours_ops: list[Op], theirs_ops: list[Op]) -> None:
    """Refuse the sync unless both stores share one miner version (C6/R12): a union across versions
    is exactly the mixed-version store `fsck` flags. Post-U9 *either* side can be behind -- an
    un-migrated clone carries v2 ops while its binary is v3 -- so both stores are checked, and which
    side to migrate is named. A pre-v3 store is fixed by `sgt migrate ops-v3` (not an sgt upgrade);
    a store *newer* than this binary means this side must upgrade sgt first."""
    ours = {op.miner_version for op in ours_ops if op.miner_version != MINER_VERSION}
    theirs = {op.miner_version for op in theirs_ops if op.miner_version != MINER_VERSION}
    if not ours and not theirs:
        return
    foreign = sorted(ours | theirs)
    if all(v < MINER_VERSION for v in foreign):
        # A pre-v3 store on one (or both) side(s): a re-key migration, not an sgt upgrade, is the fix.
        side = "ours" if ours else "theirs"
        raise MinerVersionMismatch(
            f"refusing to union op stores across miner versions: the {side} store carries "
            f"{', '.join(foreign)} but this sgt mines {MINER_VERSION}. Run `sgt migrate ops-v3` on "
            f"the {side} clone to re-key it to v3, then sync again -- and on a team, migrate the "
            f"shared branch's landing clone first, then have the other clones re-sync after."
        )
    raise MinerVersionMismatch(
        f"refusing to union op stores across miner versions: theirs carries {', '.join(foreign)}, "
        f"this sgt mines {MINER_VERSION} -- theirs was mined by a newer sgt. Upgrade sgt on our side, "
        f"then run `sgt migrate ops-v3` and sync again."
    )


def _theirs_ideal(
    repo: Path, gb: GitBinding, theirs_sha: str, ours_sha: str
) -> tuple[frozenset[str], list[Op], str]:
    """Theirs' committed ideal, recovered by the most authoritative *witnessed* record available
    and, when none exists, by mining theirs' foreign commits (C3, the "adoption ⊂ sync, one code
    path" the remote side dropped). Returns `(ideal_ids, mined_ops, method)`; `mined_ops` is
    non-empty only on the mine path, so a squash-merged sgt branch reads its fine-grained ops from
    `.sgt/ops/` blobs rather than re-mining the coarse squash (§2.1 path-dependence). Every claimed
    source is witness-checked (R12): a trailer or record naming an op the tip's tree never produced
    is *not* trusted and falls through."""
    trailer_ids = frozenset(parse_op_ids(gb.commit_message(theirs_sha)))
    if trailer_ids and _witnessed(gb, theirs_sha, trailer_ids):
        return trailer_ids, [], "trailers"  # sgt-native tip, trailers witnessed by its own tree

    # No trustworthy trailers (GitHub squash-merges destroy them by default; forged trailers name
    # ops the tree lacks). Recover from the committed in-tree `.sgt/ideal.json` (C5) when theirs'
    # tip is a *witness* of it -- the tip commit actually wrote that ideal (a squash carries the
    # branch's witness tree forward), and every op it names is present as a blob. A stale record
    # inherited by a later foreign commit (a plain-git hotfix that never touched `.sgt/ideal.json`)
    # does not describe that commit's code, so it is *not* trusted -- it falls through.
    if _tip_witnesses_ideal(gb, theirs_sha):
        recovered = state.load_blob_json(gb, theirs_sha, "ideal")
        if recovered is not None and _witnessed(gb, theirs_sha, frozenset(recovered)):
            return frozenset(recovered), [], "ideal-record"

    # No witnessed record. Mine theirs' divergent commits as if sgt had tracked theirs' branch all
    # along (adoption ⊂ sync, C3/AE8): LAW-0 makes these byte-identical to the ops theirs' own
    # `sgt init`/`put` would mint, so a normal sgt branch's fine `.sgt/ops` blobs are *reproduced*
    # by the mine and dedup. Theirs' divergent ops alone form its ideal contribution -- the shared
    # base below `merge_base` already rides in `ours_ideal`, so the union covers it.
    base = gb.merge_base(ours_sha, theirs_sha)
    mined = mine(repo, since=base, target=theirs_sha)
    mined_ids = frozenset(op.id for op in mined)

    # Footgun (scenario 5): the tip carries *new* fine `.sgt/ops` blobs that the mine did NOT
    # reproduce -- a squash that collapsed an sgt branch into one commit (mining yields *coarse* ops
    # that fork the fine blobs, §2.1), or a raw fixture that wrote op blobs with no matching source.
    # Those orphan blobs mean the mine can't be trusted, so degrade to ∅ with a remedy rather than
    # mis-mining. Blobs an earlier `put`/`init` in theirs' own history witnessed are reproduced by
    # the mine (not orphan), and blobs ours already has are merely inherited (a plain-git hotfix on
    # top of an sgt branch, C3) -- both fall through to the trusted mine above.
    theirs_blob_ids = {p.rsplit("/", 1)[-1] for p in gb.list_tree(theirs_sha, ".sgt/ops/")}
    orphan = theirs_blob_ids - {op.id for op in Store(repo).all_ops()} - mined_ids
    if orphan:
        return frozenset(), [], "none"
    return mined_ids, mined, "mined"


def recover_base(
    repo: Path, gb: GitBinding, base_sha: str | None
) -> tuple[frozenset[str], str]:
    """The merge-base's *full* committed ideal for U8's three-way subtraction (R12), recovered
    under the same witness discipline as a tip and returning `(ideal_ids, method)`:

    * no merge-base (disjoint histories, or a base sgt can't reach) -> `(∅, "none")`. Three-way
      over a ∅ base reproduces today's plain union exactly, so pre-sgt/degraded history costs
      nothing new -- resolve just keeps union semantics.
    * witnessed trailers -> `(ids, "trailers")`.
    * a committed `.sgt/ideal.json` the base commit *witnessed* (wrote, every op present) ->
      `(ids, "ideal-record")` -- an inherited stale record is rejected, exactly as for a tip.
    * otherwise a full-range mine of the base -> `(ids, "mined")`. Unlike `_theirs_ideal`'s
      divergent (`merge_base..theirs`) mine, this mines the base's whole history so the result is a
      full ideal, not a contribution.
    """
    if base_sha is None:
        return frozenset(), "none"

    trailer_ids = frozenset(parse_op_ids(gb.commit_message(base_sha)))
    if trailer_ids and _witnessed(gb, base_sha, trailer_ids):
        return trailer_ids, "trailers"

    if _tip_witnesses_ideal(gb, base_sha):
        recovered = state.load_blob_json(gb, base_sha, "ideal")
        if recovered is not None and _witnessed(gb, base_sha, frozenset(recovered)):
            return frozenset(recovered), "ideal-record"

    mined = mine(repo, target=base_sha)  # full range (since=None) -> the base's whole ideal
    return frozenset(op.id for op in mined), "mined"
