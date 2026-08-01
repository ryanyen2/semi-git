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
from sgt.lens import authored, tree
from sgt.lens.authored import AuthoredFeature
from sgt.lens.pins import Pins, _pins_from_payload, load_pins
from sgt.store.gitbind import GitBinding, parse_op_ids

from . import log as _log


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
    ours_tree: dict | None
    ours_ideal: Ideal
    theirs_ideal_ids: frozenset[str]
    all_ops: list[Op]  # in-memory union of ours' store and theirs' op files, sorted by id
    theirs_ops: list[Op]  # theirs' op files as parsed, for `materialize` to persist for real
    mined_ops: list[Op]  # theirs' foreign commits mined on contact (C3), for `materialize` too
    ops_added: int
    # U7/R12: the merge-base's recovered *full* ideal (for U8's three-way subtraction) and how it
    # was recovered -- `log` | `trailers` | `mined` | `none`. `none` (no merge-base,
    # or a base sgt can't witness) means the base degrades to ∅ and resolve keeps today's union
    # semantics. `log` (D1) is the append-only land log's record for that sha -- checked first, since
    # it survives squashes and never goes stale the way an inherited `.sgt/ideal.json` can.
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
    # Authored features (U6/R3/KTD3), merged field-by-field in `resolve` -- defaulted like the
    # other post-U15 additions so a direct `Ingested(...)` construction (tests) needn't supply them.
    ours_authored: dict[str, AuthoredFeature] = field(default_factory=dict)
    theirs_authored: dict[str, AuthoredFeature] = field(default_factory=dict)
    # Shared exclusion OR-Sets (Phase 1.2 §E): the per-ref-key {adds, tombstones} record of the ops an
    # explicit revert/pin removed, unioned by tag in `resolve` so a teammate's revert survives our sync
    # (F20). Defaulted like the other post-U15 additions so a direct `Ingested(...)` (tests) needn't
    # supply them.
    ours_exclusions: dict[str, lens.ExclusionORSet] = field(default_factory=dict)
    theirs_exclusions: dict[str, lens.ExclusionORSet] = field(default_factory=dict)


def _pins_at(gb: GitBinding, sha: str) -> Pins:
    body = state.load_blob_json(gb, sha, "pins")
    return Pins() if body is None else _pins_from_payload(body)


def ingest(
    repo: Path, gb: GitBinding, theirs_sha: str, ours_sha: str, *, branch: str | None = None,
    theirs_state_sha: str | None = None,
) -> Ingested:
    # Phase 1.2: theirs' committed *tables*, op blobs, and content-addressed file-sets are read from
    # the fetched `refs/sgt/state` tip when transport supplies one. A `None` here means either the
    # transition (the ref isn't published yet) or a pre-1.2 / absent-ref teammate; both fall back to
    # theirs' branch tree, which still carries that state -- so this read is byte-identical until
    # Step 6 wires the fetch. The trailer/log ideal recovery below deliberately stays on `theirs_sha`:
    # `Sgt-Op:` trailers live in the commit *message*, untouched by the move.
    state_sha = theirs_state_sha if theirs_state_sha is not None else theirs_sha
    ours_ops = Store(repo).all_ops()
    theirs_ops: list[Op] = []
    for path in gb.list_tree(state_sha, ".sgt/ops/"):
        raw = gb.blob_bytes(state_sha, path)
        if raw is None:
            continue
        try:
            theirs_ops.append(_deserialize(raw))
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            continue  # a corrupt op blob in theirs' tree degrades to a read-side skip (R1)

    # The set of op-ids we can actually fold: our store plus theirs' ops carried on the fetched
    # state ref. `_witnessed` uses it for the presence half of its check, now that the op store no
    # longer lives in the branch tree (Phase 1.2) -- an ideal naming an op we don't hold can't be
    # trusted regardless of what a trailer/record claims.
    present_ids = frozenset({op.id for op in ours_ops} | {op.id for op in theirs_ops})

    # Recover theirs' ideal, and mine foreign commits when there's no witnessed record to read (C3).
    theirs_ideal_ids, mined_ops, theirs_recovery = _theirs_ideal(
        repo, gb, theirs_sha, ours_sha, state_sha, present_ids, branch
    )

    # Recover the *merge-base's* full ideal for U8's three-way subtraction (R12). Distinct from
    # theirs' divergent contribution above: used as a base, the divergent set would mass-delete, so
    # a base must be a *full* ideal or ∅. Same witness discipline as the tip.
    base_sha = gb.merge_base(ours_sha, theirs_sha)
    base_ideal_ids, base_recovery = recover_base(repo, gb, base_sha, branch, present_ids=present_ids)

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
        theirs_pins=_pins_at(gb, state_sha),
        ours_declared_orset=lens.load_declared_orset(repo),
        theirs_declared_orset=lens.declared_orset_at(gb, state_sha),
        ours_prompts=intent_prompts.load_prompts(repo),
        theirs_prompts=intent_prompts.prompts_at(gb, state_sha),
        ours_authored=authored.load_authored(repo),
        theirs_authored=authored.authored_at(gb, state_sha),
        ours_exclusions=lens.load_exclusions(repo),
        theirs_exclusions=lens.exclusions_at(gb, state_sha),
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


def _witnessed(ids: frozenset[str], present_ids: frozenset[str], attributed_ids: frozenset[str]) -> bool:
    """True iff a recovered ideal is trustworthy, under the two post-Phase-1.2 checks that together
    replace the old per-commit tree-blob presence check (R12):

    * **present** -- every op it names is an op we actually hold, as a blob in the local store or in
      theirs' fetched `refs/sgt/state` tree, so the ideal can be folded. The op store no longer lives
      in the branch tree, so this is now a per-*store* fact, not a per-commit one.
    * **attributed within this lineage** -- every op it names was stamped by a `Sgt-Op:` trailer on
      some commit reachable from the claiming sha (`_attributed_ids`). This restores the per-commit
      integrity the tree-blob check gave: a *forged* trailer/record naming a store-present op that no
      commit in this history ever produced, or a stale record whose ops were squashed out of this
      lineage, is rejected -- full-and-witnessed or unusable, never partial.

    The empty ideal is vacuously witnessed."""
    if not ids:
        return True
    return ids <= present_ids and ids <= attributed_ids


def _attributed_ids(gb: GitBinding, sha: str) -> frozenset[str]:
    """Every op-id stamped by a `Sgt-Op:` trailer on any commit reachable from `sha` (one `git log`
    over its full history, `op_ids_by_commit`). A recovered ideal must be a subset of this to be
    *attributed to this lineage* -- the per-commit integrity half of `_witnessed`. `Sgt-Op:` trailers
    live in the commit message, untouched by the Phase-1.2 move of the op store off the branch tree,
    so this is exactly as reliable after the move as the tree-blob check was before it."""
    attributed: set[str] = set()
    for op_ids in gb.op_ids_by_commit(sha).values():
        attributed |= op_ids
    return frozenset(attributed)


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
    repo: Path, gb: GitBinding, theirs_sha: str, ours_sha: str, state_sha: str,
    present_ids: frozenset[str], branch: str | None = None
) -> tuple[frozenset[str], list[Op], str]:
    """Theirs' committed ideal, recovered by the most authoritative *witnessed* record available
    and, when none exists, by mining theirs' foreign commits (C3, the "adoption ⊂ sync, one code
    path" the remote side dropped). Returns `(ideal_ids, mined_ops, method)`; `mined_ops` is
    non-empty only on the mine path, so a squash-merged sgt branch reads its fine-grained ops from
    `.sgt/ops/` blobs rather than re-mining the coarse squash (§2.1 path-dependence). Every claimed
    source is witness-checked (R12): a trailer or log record naming ops we don't hold, or that no
    commit in theirs' lineage stamped, is *not* trusted and falls through (see `_witnessed`)."""
    attributed = _attributed_ids(gb, theirs_sha)
    if branch is not None:
        logged_ids = _log.ideal_for_sha(gb, branch, theirs_sha)
        if logged_ids is not None and _witnessed(logged_ids, present_ids, attributed):
            return logged_ids, [], "log"  # D1: the land log's own record for this sha

    trailer_ids = frozenset(parse_op_ids(gb.commit_message(theirs_sha)))
    if trailer_ids and _witnessed(trailer_ids, present_ids, attributed):
        return trailer_ids, [], "trailers"  # sgt-native tip, trailers witnessed by store + lineage

    # No trustworthy trailers or log record (GitHub squash-merges destroy the trailers by default;
    # forged trailers name ops no commit stamped). The in-tree `.sgt/ideal.json` recovery rung (C5)
    # is gone with Phase 1.2 -- the op store and that record no longer live in the branch tree, so a
    # server-side squash (where sgt never ran to push the log ref) recovers by mining below, coarser
    # but reproducible (LAW-0), rather than from a tree-resident record that would reintroduce F10.
    #
    # Mine theirs' divergent commits as if sgt had tracked theirs' branch all along (adoption ⊂ sync,
    # C3/AE8): LAW-0 makes these byte-identical to the ops theirs' own `sgt init`/`put` would mint,
    # so a normal sgt branch's fine `.sgt/ops` blobs (now carried on theirs' state ref at `state_sha`)
    # are *reproduced* by the mine and dedup. Theirs' divergent ops alone form its ideal contribution
    # -- the shared base below `merge_base` already rides in `ours_ideal`, so the union covers it.
    base = gb.merge_base(ours_sha, theirs_sha)
    mined, _last_sha = mine(repo, since=base, target=theirs_sha)
    mined_ids = frozenset(op.id for op in mined)

    # Footgun (scenario 5): theirs' state carries *new* fine `.sgt/ops` blobs that the mine did NOT
    # reproduce -- a squash that collapsed an sgt branch into one commit (mining yields *coarse* ops
    # that fork the fine blobs, §2.1), or a raw fixture that wrote op blobs with no matching source.
    # Those orphan blobs mean the mine can't be trusted, so degrade to ∅ with a remedy rather than
    # mis-mining. Read theirs' blob ids from `state_sha` (where the op store now lives, off the branch
    # tree). Blobs an earlier `put`/`init` in theirs' own history witnessed are reproduced by the mine
    # (not orphan), and blobs ours already has are merely inherited (a plain-git hotfix on top of an
    # sgt branch, C3) -- both fall through to the trusted mine above.
    theirs_blob_ids = {p.rsplit("/", 1)[-1] for p in gb.list_tree(state_sha, ".sgt/ops/")}
    orphan = theirs_blob_ids - {op.id for op in Store(repo).all_ops()} - mined_ids
    if orphan:
        return frozenset(), [], "none"
    return mined_ids, mined, "mined"


def recover_base(
    repo: Path, gb: GitBinding, base_sha: str | None, branch: str | None = None,
    *, present_ids: frozenset[str] | None = None,
) -> tuple[frozenset[str], str]:
    """The merge-base's *full* committed ideal for U8's three-way subtraction (R12), recovered
    under the same witness discipline as a tip and returning `(ideal_ids, method)`:

    * no merge-base (disjoint histories, or a base sgt can't reach) -> `(∅, "none")`. Three-way
      over a ∅ base reproduces today's plain union exactly, so pre-sgt/degraded history costs
      nothing new -- resolve just keeps union semantics.
    * the D1 land log's own record for `base_sha`, witnessed -> `(ids, "log")` -- checked first,
      since it survives squashes.
    * witnessed trailers -> `(ids, "trailers")`.
    * otherwise a full-range mine of the base -> `(ids, "mined")`. Unlike `_theirs_ideal`'s
      divergent (`merge_base..theirs`) mine, this mines the base's whole history so the result is a
      full ideal, not a contribution. The in-tree `.sgt/ideal.json` rung is gone with Phase 1.2 (see
      `_theirs_ideal`); a base with no witnessed log/trailer record now mines directly.

    `present_ids` is the set of op-ids we hold (local store ∪ theirs' fetched state ref); it defaults
    to the local store alone for a direct call, which is sufficient for a base (a base is an ancestor
    of ours, so every base op is already in our store)."""
    if base_sha is None:
        return frozenset(), "none"

    present = present_ids if present_ids is not None else frozenset(op.id for op in Store(repo).all_ops())
    attributed = _attributed_ids(gb, base_sha)

    if branch is not None:
        logged_ids = _log.ideal_for_sha(gb, branch, base_sha)
        if logged_ids is not None and _witnessed(logged_ids, present, attributed):
            return logged_ids, "log"

    trailer_ids = frozenset(parse_op_ids(gb.commit_message(base_sha)))
    if trailer_ids and _witnessed(trailer_ids, present, attributed):
        return trailer_ids, "trailers"

    mined, _last_sha = mine(repo, target=base_sha)  # full range (since=None) -> the base's whole ideal
    return frozenset(op.id for op in mined), "mined"
