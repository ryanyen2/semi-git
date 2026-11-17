"""Feature verbs (plan U13, R16): `merge`/`split`/`rename`/`move` over the feature tree, plus
`resolve_feature`, which `revert`/`restore` use to read a feature name. Each verb here is an
**in-place patch of the loaded `tree.json` + one durable pin write** -- instant (no re-cluster), reversible, and, critically,
content-untouched: `code(I)` is a pure function of ops + ideal and never reads pins or the tree
(``ugh.core.fold.code``), so every verb below changes zero bytes of any file by construction --
the "fold before == fold after" byte-neutrality property is guaranteed, not merely tested.

Each verb writes the `assign`/`labels`/`cannot_link` pin that keeps the metadata edit stable
across the *next* `ugh map` re-cluster (`tree.build`'s Greene matching + `_apply_assign_pins`
otherwise has no reason to reproduce a hand edit). Patterns reused rather than duplicated:
`tree._dedup`'s in-place `op_leaf` remap, `tree.enforce_cannot_link`'s in-place member moves, and
`tree._content_birth_id`'s content-addressed `f-<founding op>` minting.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

from ugh.lens import authored as authored_features
from ugh.lens import tree
from ugh.lens.pins import Pins, load_pins, save_pins
from ugh.store.gitbind import GitBinding

class VerbError(Exception):
    """`apply_*` was called on a refused plan."""


@dataclass(frozen=True)
class RenamePreview:
    ok: bool
    feature_id: str
    old_label: str = ""
    new_label: str = ""
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
    captured *before* the mutation -- the inverse-descriptor `ugh undo` restores (U8/KTD6). A reorg
    is byte-neutral for `code(I)` (it touches no op), so restoring these three metadata artifacts is
    the whole inverse: prior membership, label, and pins all come back."""
    from ugh.core import oplog

    return oplog.snapshot(repo, ["tree", "pins", "authored_features"])


def _journal_reorg(repo: str | Path, verb: str, snapshot: dict) -> None:
    """Append the reorg to the unified operation log (U8) so `ugh undo` can reverse it -- the gap
    the log closes (feature-reorg was previously un-journaled)."""
    from ugh.core import oplog

    oplog.append(repo, {"kind": "feature_reorg", "verb": verb, "snapshot": snapshot})


def _begin_reorg(repo: str | Path, verb: str) -> None:
    """Snapshot the three reorg-mutated artifacts (`tree`/`pins`/`authored_features`) and record the
    `feature_reorg` undo event *before* the mutation runs. Recording the inverse first is what makes
    a reorg crash-recoverable: the artifact writes that follow are not one atomic unit, so if one
    fails partway (I/O error between `tree.save` and `save_authored`), the undo event already exists
    and `ugh undo` restores all three artifacts to the consistent pre-reorg state. On the success
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


# -- move -------------------------------------------------------------------------------------


# -- rename -----------------------------------------------------------------------------------


def plan_rename(repo: str | Path, feature_id: str, new_label: str) -> RenamePreview:
    result = _require_tree(repo)
    if result is None:
        return RenamePreview(False, feature_id, message="no feature tree; run `ugh log --refresh`")
    node = _leaf(result["nodes"], feature_id) or result["nodes"].get(feature_id)
    if node is None:
        # Fall back to the same matcher `ugh revert` uses (id-prefix, `f-`-prefix, or exact label),
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
