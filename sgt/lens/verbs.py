"""Feature verbs (plan U13, R16): `merge`/`split`/`rename`/`move` over the feature tree, plus
`revert <feature>` which bridges into the kernel's ideal-edit algebra. Mirrors the `plan_* /
apply` shape of `sgt.core.verbs`, but each verb here is an **in-place patch of the loaded
`tree.json` + one durable pin write** -- instant (no re-cluster), reversible, and, critically,
content-untouched: `code(I)` is a pure function of ops + ideal and never reads pins or the tree
(``sgt.core.fold.code``), so every verb below changes zero bytes of any file by construction --
the "fold before == fold after" byte-neutrality property is guaranteed, not merely tested.

Each verb writes the `assign`/`labels`/`cannot_link` pin that keeps the metadata edit stable
across the *next* `sgt map` re-cluster (`tree.build`'s Greene matching + `_apply_assign_pins`
otherwise has no reason to reproduce a hand edit). Patterns reused rather than duplicated:
`tree._dedup`'s in-place `op_leaf` remap, `tree.enforce_cannot_link`'s in-place member moves, and
`tree._content_birth_id`'s content-addressed `f-<founding op>` minting.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

from sgt.core import lens as kernel_lens
from sgt.core import opindex
from sgt.core import order
from sgt.core import verbs as core_verbs
from sgt.core.store import Store
from sgt.lens import authored as authored_features
from sgt.lens import tree
from sgt.lens.cluster import _dominant_dir
from sgt.lens.pins import Pins, load_pins, save_pins
from sgt.store.gitbind import GitBinding

VerbError = core_verbs.VerbError


@dataclass(frozen=True)
class MergePreview:
    ok: bool
    survivor_id: str
    absorbed_id: str
    message: str = ""
    op_count: int = 0
    member_count: int = 0


@dataclass(frozen=True)
class MovePreview:
    ok: bool
    op_ids: tuple[str, ...] = ()
    target_id: str = ""
    message: str = ""


@dataclass(frozen=True)
class RenamePreview:
    ok: bool
    feature_id: str
    old_label: str = ""
    new_label: str = ""
    message: str = ""


@dataclass(frozen=True)
class SplitPreview:
    ok: bool
    feature_id: str
    groups: tuple[tuple[str, ...], tuple[str, ...]] | None = None
    reason: str | None = None
    message: str = ""
    new_id: str = ""  # the content-addressed id the split will mint for the new group (KTD4)


def _leaf(nodes: dict, feature_id: str) -> dict | None:
    node = nodes.get(feature_id)
    return node if node is not None and not node["children"] else None


def _require_tree(repo: str | Path) -> dict | None:
    return tree.load(repo)


def _save_pins(repo: str | Path, pins: Pins, **overrides) -> None:
    assign = overrides.get("assign", pins.assign)
    witness = dict(pins.assign_witness)
    if "assign" in overrides:
        # Stamp the introducing witness (D6): the head this curation was recorded against, so a
        # later re-pin (from history that already contains a teammate's pin) reconciles as causally
        # later. head() before this verb's own commit is the right causal anchor -- the decision was
        # made *on top of* that state. Only changed/new members get a fresh stamp; a member dropped
        # from `assign` drops its (now meaningless) witness.
        head = GitBinding(repo).head()
        if head is not None:
            for member, fid in assign.items():
                if pins.assign.get(member) != fid:
                    witness[member] = head
        witness = {m: w for m, w in witness.items() if m in assign}
    save_pins(repo, Pins(
        assign=assign,
        assign_witness=witness,
        must_link=overrides.get("must_link", pins.must_link),
        cannot_link=overrides.get("cannot_link", pins.cannot_link),
        labels=overrides.get("labels", pins.labels),
    ))


# -- authored features (U6/U7, R3): reorg verbs write authored ops beside their pins --------------


def _authored_id_for(feature_id: str) -> str:
    """The authored-feature (`af-`) id a reorg verb maintains for a cluster feature: a deterministic
    ``af-<feature-id>`` handle so re-running a verb *updates* the same feature (idempotent) rather
    than duplicating it. This is intentionally distinct from U6's `authored.create` uuid4 mint, whose
    carried-UUID identity is for free-standing features a user authors directly and must not collide
    when two clones author *different* features over the same seed. A reorg verb, by contrast, always
    targets one specific existing cluster feature, so a content-derived handle is the correct, always-
    collision-free correspondence -- and two clones renaming that same feature reconcile through the
    LWW label register exactly as intended.

    Idempotent by contract: the authored id *for something that is already an authored feature* is
    itself. A cluster id (`N42`) gets the handle (`af-N42`), but an `af-<uuid>` lane -- which
    `ledger.assign_at_save` can pass in when a new symbol attaches to a previously-minted lane, and
    which any reorg verb sees when it targets an authored feature directly -- is returned unchanged.
    Re-wrapping it (`af-af-<uuid>`) would mint a phantom lane no pin or tree node references, so its
    member_adds would accrete under an id the assign pin never uses and the two stores would diverge."""
    if feature_id.startswith("af-"):
        return feature_id
    return f"af-{feature_id}"


def _reorg_snapshot(repo: str | Path) -> dict:
    """A snapshot of the three artifacts a reorg verb mutates (`tree`/`pins`/`authored_features`),
    captured *before* the mutation -- the inverse-descriptor `sgt undo` restores (U8/KTD6). A reorg
    is byte-neutral for `code(I)` (it touches no op), so restoring these three metadata artifacts is
    the whole inverse: prior membership, label, and pins all come back."""
    from sgt.core import oplog

    return oplog.snapshot(repo, ["tree", "pins", "authored_features"])


def _journal_reorg(repo: str | Path, verb: str, snapshot: dict) -> None:
    """Append the reorg to the unified operation log (U8) so `sgt undo` can reverse it -- the gap
    the log closes (feature-reorg was previously un-journaled)."""
    from sgt.core import oplog

    oplog.append(repo, {"kind": "feature_reorg", "verb": verb, "snapshot": snapshot})


def _begin_reorg(repo: str | Path, verb: str) -> None:
    """Snapshot the three reorg-mutated artifacts (`tree`/`pins`/`authored_features`) and record the
    `feature_reorg` undo event *before* the mutation runs. Recording the inverse first is what makes
    a reorg crash-recoverable: the artifact writes that follow are not one atomic unit, so if one
    fails partway (I/O error between `tree.save` and `save_authored`), the undo event already exists
    and `sgt undo` restores all three artifacts to the consistent pre-reorg state. On the success
    path this is identical to journaling last."""
    _journal_reorg(repo, verb, _reorg_snapshot(repo))


def _open_authored(repo: str | Path, feature_id: str, *, label: str, seed_members) -> tuple[dict, str]:
    """Load the authored-feature collection and ensure the feature for `feature_id` exists (creating
    it from `seed_members`+`label`, stamped with the current head as its introducing witness, if
    absent). Returns `(collection, af_id)`; the caller mutates `collection[af_id]` with the U6 ops
    (`rename`/`add_member`/`remove_member`) and then `authored.save_authored`."""
    af = authored_features.load_authored(repo)
    aid = _authored_id_for(feature_id)
    if aid not in af:
        witness = GitBinding(repo).head()
        af[aid] = replace(authored_features.create(seed_members, label, witness=witness), id=aid)
    return af, aid


# -- merge ------------------------------------------------------------------------------------


def plan_merge(repo: str | Path, survivor_id: str, absorbed_id: str) -> MergePreview:
    result = _require_tree(repo)
    if result is None:
        return MergePreview(False, survivor_id, absorbed_id, message="no feature tree; run `sgt log --refresh`")
    nodes = result["nodes"]
    # Accept the abbreviated handle every surface prints, same as `rename`/`revert`: canonicalize
    # each ref through `resolve_feature` (id-prefix / `f-` prefix / exact label) when exact-key misses.
    for _attr, _ref in (("survivor_id", survivor_id), ("absorbed_id", absorbed_id)):
        if _leaf(nodes, _ref) is None:
            _resolved = resolve_feature(repo, _ref)
            if _resolved is not None:
                if _attr == "survivor_id":
                    survivor_id = _resolved[1]
                else:
                    absorbed_id = _resolved[1]
    survivor, absorbed = _leaf(nodes, survivor_id), _leaf(nodes, absorbed_id)
    if survivor_id == absorbed_id:
        return MergePreview(False, survivor_id, absorbed_id, message="cannot merge a feature into itself")
    if survivor is None or absorbed is None:
        missing = survivor_id if survivor is None else absorbed_id
        return MergePreview(False, survivor_id, absorbed_id, message=f"{missing!r} is not a leaf feature")
    op_count = sum(1 for leaf in result["op_leaf"].values() if leaf in (survivor_id, absorbed_id))
    member_count = len(set(survivor["members"]) | set(absorbed["members"]))
    return MergePreview(True, survivor_id, absorbed_id, op_count=op_count, member_count=member_count)


def apply_merge(repo: str | Path, preview: MergePreview) -> dict:
    if not preview.ok:
        raise VerbError(preview.message or "merge refused")
    repo = Path(repo)
    _begin_reorg(repo, "merge")  # record the inverse before mutating (crash-recoverable via undo)
    result = tree.load(repo)
    nodes = result["nodes"]
    survivor, absorbed = nodes[preview.survivor_id], nodes[preview.absorbed_id]

    result["op_leaf"] = {
        op: (preview.survivor_id if leaf == preview.absorbed_id else leaf)
        for op, leaf in result["op_leaf"].items()
    }

    survivor["members"] = sorted(set(survivor["members"]) | set(absorbed["members"]))
    survivor["size"] = len(survivor["members"])

    parent_id = absorbed["parent"]
    if parent_id is not None:
        nodes[parent_id]["children"] = [c for c in nodes[parent_id]["children"] if c != preview.absorbed_id]
    else:
        result["roots"] = [r for r in result["roots"] if r != preview.absorbed_id]
    del nodes[preview.absorbed_id]
    tree.save(repo, result)

    pins = load_pins(repo)
    assign = dict(pins.assign)
    for member in survivor["members"]:
        assign[member] = preview.survivor_id
    _save_pins(repo, pins, assign=assign)

    # authored-feature op (R3): the survivor's authored feature absorbs the members; the absorbed
    # feature's authored record (if any) is tombstoned (CRDT delete), its members now the survivor's.
    af, aid = _open_authored(
        repo, preview.survivor_id, label=survivor.get("label", preview.survivor_id),
        seed_members=survivor["members"],
    )
    feat = af[aid]
    live = feat.live_members()
    for member in survivor["members"]:
        if member not in live:
            feat = authored_features.add_member(feat, member)
    af[aid] = feat
    absorbed_aid = _authored_id_for(preview.absorbed_id)
    if absorbed_aid in af:
        af[absorbed_aid] = authored_features.delete(af[absorbed_aid])
    authored_features.save_authored(repo, af)
    return result


# -- move -------------------------------------------------------------------------------------


def _resolve_op_ref(op_leaf: dict[str, str], ref: str) -> str | None:
    if ref in op_leaf:
        return ref
    matches = sorted(oid for oid in op_leaf if oid.startswith(ref))
    return matches[0] if len(matches) == 1 else None


def plan_move(repo: str | Path, op_refs: list[str], target_id: str) -> MovePreview:
    result = _require_tree(repo)
    if result is None:
        return MovePreview(False, message="no feature tree; run `sgt log --refresh`")
    nodes = result["nodes"]
    if _leaf(nodes, target_id) is None:
        # Accept the same selectors every other surface prints -- the short handle and the label,
        # not just the full 64-hex id. `plan_merge` already does this; `move`/`split` did not, so
        # `regroup split "Time Slots"` and `regroup split 044954f3` both failed with "not a leaf
        # feature" while the id the tree, `show`, `--focus` and every `next:` footer show is the
        # short one. A pilot participant found the full hash by hand to get past it.
        _resolved = resolve_feature(repo, target_id)
        if _resolved is not None:
            target_id = _resolved[1]
    if _leaf(nodes, target_id) is None:
        return MovePreview(False, target_id=target_id, message=f"{target_id!r} is not a leaf feature")
    resolved: list[str] = []
    for ref in op_refs:
        op_id = _resolve_op_ref(result["op_leaf"], ref)
        if op_id is None:
            return MovePreview(False, target_id=target_id, message=f"op {ref!r} not found in the feature tree")
        resolved.append(op_id)
    return MovePreview(True, op_ids=tuple(sorted(resolved)), target_id=target_id)


def apply_move(repo: str | Path, preview: MovePreview) -> dict:
    if not preview.ok:
        raise VerbError(preview.message or "move refused")
    repo = Path(repo)
    _begin_reorg(repo, "move")  # record the inverse before mutating (crash-recoverable via undo)
    result = tree.load(repo)
    nodes = result["nodes"]
    ops_by_id = {op.id: op for op in Store(repo).all_ops()}
    member_leaf = {m: nid for nid, nd in nodes.items() if not nd["children"] for m in nd["members"]}

    moved_members: set[str] = set()
    for op_id in preview.op_ids:
        result["op_leaf"][op_id] = preview.target_id
        op = ops_by_id.get(op_id)
        if op is not None:
            moved_members.update(sym for sym in op.footprint if sym in member_leaf)

    for sym in moved_members:
        old_leaf = member_leaf.get(sym)
        if old_leaf is not None and old_leaf != preview.target_id:
            nodes[old_leaf]["members"] = [m for m in nodes[old_leaf]["members"] if m != sym]
            nodes[old_leaf]["size"] = len(nodes[old_leaf]["members"])

    target = nodes[preview.target_id]
    target["members"] = sorted(set(target["members"]) | moved_members)
    target["size"] = len(target["members"])
    tree.save(repo, result)

    pins = load_pins(repo)
    assign = dict(pins.assign)
    for sym in moved_members:
        assign[sym] = preview.target_id
    _save_pins(repo, pins, assign=assign)

    # authored-feature op (R3): the target's authored feature gains the moved members; any authored
    # feature for a source leaf drops them (OR-Set remove of just the tags it observed).
    af, aid = _open_authored(
        repo, preview.target_id, label=target.get("label", preview.target_id),
        seed_members=target["members"],
    )
    feat = af[aid]
    live = feat.live_members()
    for sym in moved_members:
        if sym not in live:
            feat = authored_features.add_member(feat, sym)
    af[aid] = feat
    for sym in moved_members:
        old_leaf = member_leaf.get(sym)
        if old_leaf is None or old_leaf == preview.target_id:
            continue
        src_aid = _authored_id_for(old_leaf)
        src = af.get(src_aid)
        if src is not None and sym in src.live_members():
            af[src_aid] = authored_features.remove_member(src, sym)
    authored_features.save_authored(repo, af)
    return result


# -- rename -----------------------------------------------------------------------------------


def plan_rename(repo: str | Path, feature_id: str, new_label: str) -> RenamePreview:
    result = _require_tree(repo)
    if result is None:
        return RenamePreview(False, feature_id, message="no feature tree; run `sgt log --refresh`")
    node = _leaf(result["nodes"], feature_id) or result["nodes"].get(feature_id)
    if node is None:
        # Fall back to the same matcher `sgt revert` uses (id-prefix, `f-`-prefix, or exact label),
        # so the short handle the graph/tree/save-hint prints resolves here too -- it is a full id
        # only under exact-key lookup, which every display abbreviates.
        resolved = resolve_feature(repo, feature_id)
        if resolved is not None:
            feature_id = resolved[1]
            node = result["nodes"].get(feature_id)
    if node is None:
        return RenamePreview(False, feature_id, message=f"feature {feature_id!r} not found")
    return RenamePreview(True, feature_id, old_label=node.get("label", ""), new_label=new_label)


def apply_rename(repo: str | Path, preview: RenamePreview) -> dict:
    if not preview.ok:
        raise VerbError(preview.message or "rename refused")
    repo = Path(repo)
    _begin_reorg(repo, "rename")  # record the inverse before mutating (crash-recoverable via undo)
    result = tree.load(repo)
    result["nodes"][preview.feature_id]["label"] = preview.new_label
    tree.save(repo, result)

    pins = load_pins(repo)
    labels = dict(pins.labels)
    labels[preview.feature_id] = preview.new_label
    _save_pins(repo, pins, labels=labels)

    # authored-feature op (R3): naming a cluster feature is an authoring act -- record the label on
    # its authored feature (LWW register) beside the labels pin, seeding membership from the leaf.
    af, aid = _open_authored(
        repo, preview.feature_id, label=preview.new_label,
        seed_members=result["nodes"][preview.feature_id]["members"],
    )
    af[aid] = authored_features.rename(af[aid], preview.new_label, witness=GitBinding(repo).head())
    authored_features.save_authored(repo, af)
    return result


# -- split ------------------------------------------------------------------------------------


def _split_new_group_ops(result: dict, feature_id: str, keep, new, ops) -> list[str]:
    """The ops that leave `feature_id` for the new group on a split: every op currently in the
    feature whose footprint plurality-votes for `new` over `keep`. The single source of truth
    shared by the id mint (`_mint_split_id`) and `apply_split`'s reassignment, so the previewed id
    and the committed reassignment can never disagree."""
    keep_set, new_set = set(keep), set(new)
    ops_by_id = {op.id: op for op in ops}
    moving: list[str] = []
    for op_id, leaf in result["op_leaf"].items():
        if leaf != feature_id:
            continue
        op = ops_by_id.get(op_id)
        if op is None:
            continue
        votes_new = sum(1 for sym in op.footprint if sym in new_set)
        votes_keep = sum(1 for sym in op.footprint if sym in keep_set)
        if votes_new > votes_keep:
            moving.append(op_id)
    return moving


def _mint_split_id(result: dict, feature_id: str, keep, new, ops) -> str:
    """The content-addressed id a split mints for the new group (KTD4): ``f-<founding-op>`` where
    the founding op is the lexicographically-smallest op reassigned to the new group (a pure
    function of the shared op store). Two replicas splitting the identical members over a byte-
    identical store mint the identical id -- closing the replica-local ``F<n>`` hazard."""
    moving = _split_new_group_ops(result, feature_id, keep, new, ops)
    founding = min(moving) if moving else None
    return tree._content_birth_id(frozenset(new), founding, used=set(result["nodes"]))


def plan_split(repo: str | Path, feature_id: str) -> SplitPreview:
    repo = Path(repo)
    result = _require_tree(repo)
    if result is None:
        return SplitPreview(False, feature_id, message="no feature tree; run `sgt log --refresh`")
    node = _leaf(result["nodes"], feature_id)
    if node is None:  # same short-handle/label acceptance as `merge` (see `plan_move`)
        _resolved = resolve_feature(repo, feature_id)
        if _resolved is not None:
            feature_id = _resolved[1]
            node = _leaf(result["nodes"], feature_id)
    if node is None:
        return SplitPreview(False, feature_id, message=f"{feature_id!r} is not a leaf feature")
    if len(node["members"]) < 2:
        return SplitPreview(False, feature_id, reason="stop_split",
                            message=f"{feature_id!r} has too few members to split")

    ops = Store(repo).all_ops()
    ideal = kernel_lens.current_ideal(repo)
    _, fused = tree.fused_graph(repo, ops, ideal)
    adj = tree._adjacency(fused)

    result_split = tree._split_once(node["members"], adj, min_lane=1, target=(2, 2))
    if result_split.groups is None:
        return SplitPreview(False, feature_id, reason=result_split.reason,
                            message=f"cannot split {feature_id!r}: {result_split.reason}")

    groups = result_split.groups
    if len(groups) > 2:  # this verb is always binary: largest community vs. the rest, folded
        groups = [groups[0], [m for g in groups[1:] for m in g]]
    keep, new = (tuple(sorted(groups[0])), tuple(sorted(groups[1])))
    new_id = _mint_split_id(result, feature_id, keep, new, ops)
    return SplitPreview(True, feature_id, groups=(keep, new), reason=result_split.reason, new_id=new_id)


def apply_split(repo: str | Path, preview: SplitPreview, *, confirm: bool = False) -> dict:
    if not preview.ok:
        raise VerbError(preview.message or "split refused")
    if not confirm:
        raise VerbError("split requires confirm=True")
    repo = Path(repo)
    _begin_reorg(repo, "split")  # record the inverse before mutating (crash-recoverable via undo)
    result = tree.load(repo)
    nodes = result["nodes"]
    old_id = preview.feature_id
    old_node = nodes[old_id]
    keep, new = (list(preview.groups[0]), list(preview.groups[1]))

    new_id = preview.new_id  # content-addressed (KTD4), computed in plan_split from the same
    # new-group op reassignment applied below -- replica-independent, not a local `F<n>`.
    old_node["members"] = sorted(keep)
    old_node["size"] = len(keep)
    new_node = {
        "id": new_id, "parent": old_node["parent"], "depth": old_node["depth"],
        "members": sorted(new), "size": len(new), "dir": _dominant_dir(new),
        "children": [], "split_reason": None,
        "label": f"{old_node.get('label', old_id)} (split)", "why": old_node.get("why", ""),
    }
    nodes[new_id] = new_node
    if old_node["parent"] is not None:
        nodes[old_node["parent"]]["children"].append(new_id)
    else:
        result["roots"].append(new_id)

    for op_id in _split_new_group_ops(result, old_id, keep, new, Store(repo).all_ops()):
        result["op_leaf"][op_id] = new_id
    tree.save(repo, result)

    pins = load_pins(repo)
    assign = dict(pins.assign)
    for member in new:
        assign[member] = new_id
    cannot_link = set(pins.cannot_link)
    if keep and new:
        cannot_link.add(tuple(sorted((min(keep), min(new)))))
    _save_pins(repo, pins, assign=assign, cannot_link=frozenset(cannot_link))

    # authored-feature op (R3): the new group is a fresh authored feature (`af-<new_id>`); its
    # members leave the old feature's authored record (if any) via an OR-Set remove.
    af, aid = _open_authored(repo, new_id, label=new_node["label"], seed_members=new)
    old_aid = _authored_id_for(old_id)
    old_af = af.get(old_aid)
    if old_af is not None:
        for member in new:
            if member in old_af.live_members():
                old_af = authored_features.remove_member(old_af, member)
        af[old_aid] = old_af
    authored_features.save_authored(repo, af)
    return result


# -- resolve + revert ---------------------------------------------------------------------------


def resolve_feature(repo: str | Path, ref: str) -> tuple[frozenset[str], str, str] | None:
    """Match `ref` against a content-addressed feature id (`f-<op>`) or an exact leaf label -- the
    feature's op-set (from `op_leaf`), its id, and its label; `None` if `ref` names no leaf
    feature."""
    result = tree.load(repo)
    if result is None:
        return None
    nodes = result["nodes"]
    feature_id = ref if _leaf(nodes, ref) is not None else None
    if feature_id is None:
        feature_id = next(
            (nid for nid, nd in nodes.items() if not nd["children"] and nd.get("label") == ref), None,
        )
    if feature_id is None and ref:  # a unique id-prefix -- `f-`-prefixed or the bare hex the graph prints
        hits = [
            nid for nid, nd in nodes.items()
            if not nd["children"] and (nid.startswith(ref) or nid.startswith("f-" + ref))
        ]
        if len(hits) == 1:
            feature_id = hits[0]
    if feature_id is None and ref:
        # A unique *label* prefix, for the same reason ids accept one. Labels get a
        # disambiguating suffix when two features share a name -- "Waitlist Promotion" is stored as
        # "Waitlist Promotion · notify.py" -- so a user typing the part they were shown, and the
        # part that means anything to them, matched nothing. Ambiguity still declines to guess.
        needle = ref.casefold()
        hits = [
            nid for nid, nd in nodes.items()
            if not nd["children"] and str(nd.get("label", "")).casefold().startswith(needle)
        ]
        if len(hits) == 1:
            feature_id = hits[0]
    if feature_id is None:
        return None
    op_ids = frozenset(op for op, leaf in result["op_leaf"].items() if leaf == feature_id)
    return op_ids, feature_id, nodes[feature_id].get("label", feature_id)


def plan_revert_feature(repo: str | Path, ref: str, *,
                        take_dependents: bool = False) -> core_verbs.VerbPreview:
    """Resolve `ref` to a feature's op-set X, then plan its removal through the same
    `core.verbs._plan_removal` every revert shape uses: the safe default subtracts X from
    shared symbols at their tips and keeps interleaved later work; `take_dependents` is the
    explicit old `I \\ upset_in_many(X)` demolition."""
    repo = Path(repo)
    ops = opindex.index_ops(repo)  # previews never materialize bytes -- footprints suffice
    ideal = kernel_lens.current_ideal(repo)
    declared = kernel_lens._load_declared(repo)

    resolved = resolve_feature(repo, ref)
    if resolved is None:
        return core_verbs._preview("revert", ref, ideal.op_ids, ideal.op_ids, ops, ok=False,
                                    message=f"feature {ref!r} not found; run `sgt log --refresh`")
    op_ids, feature_id, label = resolved
    if not op_ids:
        return core_verbs._preview("revert", feature_id, ideal.op_ids, ideal.op_ids, ops,
                                    message=f"feature {label!r} has no ops in the current ideal; no change")

    return core_verbs._plan_removal(repo, "revert", feature_id, op_ids, ops, ideal, declared,
                                    take_dependents=take_dependents)


def plan_revert_lane_to_commit(
    repo: str | Path, ref: str, commit_index: int, keep: tuple[str, ...] = (),
) -> core_verbs.VerbPreview:
    """`revert <lane> --to <commit>` (plan U11/R9): truncate a lane at a commit boundary -- drop the
    lane's ops *after* `commit_index` (and everything built on them), keeping the lane's shape at or
    before it. `keep` names other lanes whose ops must survive even where the up-set would otherwise
    sweep them: their op-sets are subtracted from the removal before validating, so `_validated`
    refuses (rather than silently dropping) if that would leave an invalid ideal. Reuses
    `plan_revert_feature`'s exact `upset_in` + `Ideal.from_ops` algebra -- only the *seed* op-set
    (the lane's post-`commit_index` ops) is new; the coupling a truncation cuts through surfaces in
    the preview via U4's `_coupling_rows`, unchanged."""
    from sgt.api import history_view

    repo = Path(repo)
    ops = opindex.index_ops(repo)  # previews never materialize bytes -- footprints suffice
    ideal = kernel_lens.current_ideal(repo)
    declared = kernel_lens._load_declared(repo)

    resolved = resolve_feature(repo, ref)
    if resolved is None:
        return core_verbs._preview("revert", ref, ideal.op_ids, ideal.op_ids, ops, ok=False,
                                    message=f"feature {ref!r} not found; run `sgt log --refresh`")
    op_ids, feature_id, label = resolved
    # `@c<N>` (commit-index) notation, deliberately distinct from the `@<seg_index>` checkpoint
    # notation the graph/log show -- `revert --to` truncates at a *global commit* boundary, not a
    # per-feature checkpoint, and the two number-spaces must not be misread for each other.
    target = f"{feature_id}@c{commit_index}"

    ci = {o["id"]: o["commit_index"] for o in history_view(repo, full=True)["ops"]}
    seed = {oid for oid in op_ids if oid in ideal.op_ids and ci.get(oid, -1) > commit_index}
    if not seed:
        live = sorted({ci[oid] for oid in op_ids if oid in ideal.op_ids and oid in ci})
        where = f" (its ops are at commit {', '.join(map(str, live))})" if live else ""
        return core_verbs._preview("revert", target, ideal.op_ids, ideal.op_ids, ops,
                                    message=f"{label!r} has no ops after commit {commit_index}; no change{where}")

    removal = set(order.upset_in_many(seed, ideal.op_ids, ops, declared))
    for keep_ref in keep:  # a kept lane's ops survive even where the up-set would sweep them
        kept = resolve_feature(repo, keep_ref)
        if kept is not None:
            removal -= set(kept[0])
    after = ideal.op_ids - frozenset(removal)
    return core_verbs._validated("revert", target, ideal.op_ids, after, ops, declared)


def plan_restore_feature(repo: str | Path, ref: str) -> core_verbs.VerbPreview:
    """`revert`'s inverse: resolve `ref` to a feature's op-set X (via `op_leaf`, which still
    names a reverted feature's ops -- it's built from every mined op, not just the ones live in
    the current ideal), then the exact ideal edit `I ∪ downset_in_many(X)` against the full
    provenance ideal (`HEAD`, which still holds reverted ops) -- the feature-grouped
    generalization of `core.verbs.plan_restore`'s single-op case. `downset_in_many` equals the
    per-op union exactly (reachability distributes over union) but builds its chain/producer
    indexes once for the whole set instead of once per op."""
    repo = Path(repo)
    ops = opindex.index_ops(repo)  # previews never materialize bytes -- footprints suffice
    ideal = kernel_lens.current_ideal(repo)
    declared = kernel_lens._load_declared(repo)
    source = kernel_lens.ideal_for_ref(repo, "HEAD")

    resolved = resolve_feature(repo, ref)
    if resolved is None:
        return core_verbs._preview("restore", ref, ideal.op_ids, ideal.op_ids, ops, ok=False,
                                    message=f"feature {ref!r} not found; run `sgt log --refresh`")
    op_ids, feature_id, label = resolved
    if not op_ids:
        return core_verbs._preview("restore", feature_id, ideal.op_ids, ideal.op_ids, ops,
                                    message=f"feature {label!r} has no ops; no change")

    after = ideal.op_ids | order.downset_in_many(op_ids, source.op_ids, ops, declared)
    return core_verbs._validated("restore", feature_id, ideal.op_ids, after, ops, declared)
