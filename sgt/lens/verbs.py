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
`tree._fresh_id_gen`'s collision-free `F<n>` minting.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sgt.core import lens as kernel_lens
from sgt.core import order
from sgt.core import verbs as core_verbs
from sgt.core.store import Store
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


# -- merge ------------------------------------------------------------------------------------


def plan_merge(repo: str | Path, survivor_id: str, absorbed_id: str) -> MergePreview:
    result = _require_tree(repo)
    if result is None:
        return MergePreview(False, survivor_id, absorbed_id, message="no feature tree; run `sgt map`")
    nodes = result["nodes"]
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
        return MovePreview(False, message="no feature tree; run `sgt map`")
    nodes = result["nodes"]
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
    return result


# -- rename -----------------------------------------------------------------------------------


def plan_rename(repo: str | Path, feature_id: str, new_label: str) -> RenamePreview:
    result = _require_tree(repo)
    if result is None:
        return RenamePreview(False, feature_id, message="no feature tree; run `sgt map`")
    node = _leaf(result["nodes"], feature_id) or result["nodes"].get(feature_id)
    if node is None:
        return RenamePreview(False, feature_id, message=f"feature {feature_id!r} not found")
    return RenamePreview(True, feature_id, old_label=node.get("label", ""), new_label=new_label)


def apply_rename(repo: str | Path, preview: RenamePreview) -> dict:
    if not preview.ok:
        raise VerbError(preview.message or "rename refused")
    repo = Path(repo)
    result = tree.load(repo)
    result["nodes"][preview.feature_id]["label"] = preview.new_label
    tree.save(repo, result)

    pins = load_pins(repo)
    labels = dict(pins.labels)
    labels[preview.feature_id] = preview.new_label
    _save_pins(repo, pins, labels=labels)
    return result


# -- split ------------------------------------------------------------------------------------


def plan_split(repo: str | Path, feature_id: str) -> SplitPreview:
    repo = Path(repo)
    result = _require_tree(repo)
    if result is None:
        return SplitPreview(False, feature_id, message="no feature tree; run `sgt map`")
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

    result_split = tree._split_once(node["members"], fused, adj, min_lane=1, target=(2, 2))
    if result_split.groups is None:
        return SplitPreview(False, feature_id, reason=result_split.reason,
                            message=f"cannot split {feature_id!r}: {result_split.reason}")

    groups = result_split.groups
    if len(groups) > 2:  # this verb is always binary: largest community vs. the rest, folded
        groups = [groups[0], [m for g in groups[1:] for m in g]]
    keep, new = (tuple(sorted(groups[0])), tuple(sorted(groups[1])))
    return SplitPreview(True, feature_id, groups=(keep, new), reason=result_split.reason)


def apply_split(repo: str | Path, preview: SplitPreview, *, confirm: bool = False) -> dict:
    if not preview.ok:
        raise VerbError(preview.message or "split refused")
    if not confirm:
        raise VerbError("split requires confirm=True")
    repo = Path(repo)
    result = tree.load(repo)
    nodes = result["nodes"]
    old_id = preview.feature_id
    old_node = nodes[old_id]
    keep, new = (list(preview.groups[0]), list(preview.groups[1]))

    new_id = next(tree._fresh_id_gen(set(nodes)))
    old_node["members"] = sorted(keep)
    old_node["size"] = len(keep)
    new_set = set(new)
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

    ops_by_id = {op.id: op for op in Store(repo).all_ops()}
    for op_id, leaf in list(result["op_leaf"].items()):
        if leaf != old_id:
            continue
        op = ops_by_id.get(op_id)
        if op is None:
            continue
        votes_new = sum(1 for sym in op.footprint if sym in new_set)
        votes_keep = sum(1 for sym in op.footprint if sym in keep)
        if votes_new > votes_keep:
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
    return result


# -- resolve + revert ---------------------------------------------------------------------------


def resolve_feature(repo: str | Path, ref: str) -> tuple[frozenset[str], str, str] | None:
    """Match `ref` against a feature id (`f-<op>`/legacy `F<n>`) or an exact leaf label -- the
    feature's op-set (from `op_leaf`), its id, and its label; `None` if `ref` names no leaf feature.
    A raw id miss is retried through the alias G-Set (`reconcile.resolve_alias`, U21/D6), so a
    reference to a pre-migration id (or another clone's colliding birth id) still resolves to the
    feature it was re-minted to."""
    from sgt.lens import reconcile

    result = tree.load(repo)
    if result is None:
        return None
    nodes = result["nodes"]
    feature_id = ref if _leaf(nodes, ref) is not None else None
    if feature_id is None:
        aliased = reconcile.resolve_alias(reconcile.load_aliases(repo), ref)
        if aliased != ref and _leaf(nodes, aliased) is not None:
            feature_id = aliased
    if feature_id is None:
        feature_id = next(
            (nid for nid, nd in nodes.items() if not nd["children"] and nd.get("label") == ref), None,
        )
    if feature_id is None:
        return None
    op_ids = frozenset(op for op, leaf in result["op_leaf"].items() if leaf == feature_id)
    return op_ids, feature_id, nodes[feature_id].get("label", feature_id)


def plan_revert_feature(repo: str | Path, ref: str) -> core_verbs.VerbPreview:
    """Resolve `ref` to a feature's op-set X, then the exact ideal edit `I \\ (∪ upset_in(x))`
    over `x∈X` -- the feature-grouped generalization of `core.verbs.plan_revert`'s single-op
    case, reusing its collision-safe up-set and `Ideal.from_ops` fork validation verbatim so a
    feature revert refuses on a chain fork exactly as a single-op revert would."""
    repo = Path(repo)
    ops = Store(repo).all_ops()
    ideal = kernel_lens.current_ideal(repo)
    declared = kernel_lens._load_declared(repo)

    resolved = resolve_feature(repo, ref)
    if resolved is None:
        return core_verbs._preview("revert", ref, ideal.op_ids, ideal.op_ids, ops, ok=False,
                                    message=f"feature {ref!r} not found; run `sgt map`")
    op_ids, feature_id, label = resolved
    if not op_ids:
        return core_verbs._preview("revert", feature_id, ideal.op_ids, ideal.op_ids, ops,
                                    message=f"feature {label!r} has no ops in the current ideal; no change")

    upset_union: set[str] = set()
    for op_id in op_ids:
        upset_union |= order.upset_in(op_id, ideal.op_ids, ops, declared)
    after = ideal.op_ids - frozenset(upset_union)
    return core_verbs._validated("revert", feature_id, ideal.op_ids, after, ops, declared)
