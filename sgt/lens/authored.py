"""Authored features as first-class merged state (plan U6, R3/KTD3).

A pin steers *clustering*; an authored feature is the feature object itself -- a user-authored,
named selection over the symbol graph that survives sync/merge as merged state, not a `tree.build`
output. It is exactly three CRDT components, each merged by the primitive that already exists for
its shape (the whole risk mitigation is reuse, not new merge logic):

1. **Membership is an OR-Set** -- the same pattern as `DeclaredORSet` (`sgt.core.lens`): every `add`
   of a member carries a globally-unique tag, a `remove` tombstones the tags it locally *observed*,
   and the live member set is every member whose tag is un-tombstoned. Merge is by tag (adds and
   tombstones both union), so a concurrent create-elsewhere and delete-here converge: the delete
   wins only over the tags it actually saw, and a member added on a tag this clone never saw
   survives.

2. **The label is a witness-topological LWW register** -- merged by `reconcile._assign_winner`, the
   same rule `Pins.assign` uses: the rename whose introducing witness is causally *later* in the
   git DAG wins (a deliberate re-name beats the stale one it was made on top of), and a content-hash
   tie-break decides only when the two witnesses are truly concurrent or absent. This is *not* the
   weaker hash-only `reconcile.union_pins` `labels` merge, which has no witness input and so cannot
   express "latest rename wins".

3. **The id is a carried `af-` UUID** -- minted once at feature-creation time (`af-<uuid4>`) and
   never re-derived from content. Content-addressing would make two clones authoring *different*
   features over the same seed collide into one id with a label fight; a carried UUID gives two
   distinct features (no accidental merge), matching user-owned-selection semantics. The `af-`
   namespace can never collide with the clustering layer's `f-<op-id>` ids. It travels verbatim and
   is `protected` from a `tree.build` rebuild (that wiring is U7).

The collection is a `dict[af-id -> AuthoredFeature]`, persisted as one committed artifact
(`.sgt/authored/features.json`), read from historical blobs by `sync` like `pins`/`aliases`.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, replace
from pathlib import Path

from sgt import state
from sgt.lens.reconcile import IsAncestor, _assign_winner


@dataclass(frozen=True)
class AuthoredFeature:
    """One user-authored feature. `id` is a carried `af-` UUID (protected, never re-derived).
    Membership is an OR-Set: `member_adds` are `(member_id, tag)` pairs, `member_tombstones` are the
    retracted tags; `live_members()` resolves them. `label`/`label_witness` are the LWW register --
    `label_witness` is the commit SHA the current label was recorded against (the "introducing
    witness" `_assign_winner` orders by), `None` when unknown (falls straight through to the hash
    tie-break, exactly as a witness-less pin does)."""

    id: str
    label: str
    label_witness: str | None = None
    member_adds: frozenset[tuple[str, str]] = frozenset()  # (member_id, tag)
    member_tombstones: frozenset[str] = frozenset()  # tombstoned tags

    def live_members(self) -> frozenset[str]:
        """Every member with at least one un-tombstoned tag -- what a selection actually consumes."""
        dead = self.member_tombstones
        return frozenset(m for (m, tag) in self.member_adds if tag not in dead)


# -- operations (pure) ----------------------------------------------------------------------------


def create(members, label: str, *, witness: str | None = None) -> AuthoredFeature:
    """Mint a new authored feature from a selection: a fresh carried `af-` UUID (the codebase's
    globally-unique-tag mint, `sgt/core/lens.py:197`) and one fresh OR-Set tag per member."""
    return AuthoredFeature(
        id=f"af-{uuid.uuid4().hex}",
        label=label,
        label_witness=witness,
        member_adds=frozenset((m, uuid.uuid4().hex) for m in members),
    )


def rename(feature: AuthoredFeature, label: str, *, witness: str | None = None) -> AuthoredFeature:
    """Overwrite the label register with a new label + its introducing witness (LWW)."""
    return replace(feature, label=label, label_witness=witness)


def add_member(feature: AuthoredFeature, member: str) -> AuthoredFeature:
    """OR-Set add: a fresh globally-unique tag for `member` (a re-add after a delete is a *new* tag,
    so it is not suppressed by the old tombstone)."""
    return replace(feature, member_adds=feature.member_adds | {(member, uuid.uuid4().hex)})


def remove_member(feature: AuthoredFeature, member: str) -> AuthoredFeature:
    """OR-Set remove: tombstone every tag *currently observed locally* for `member`. A concurrent
    add elsewhere, with a tag this clone hasn't seen, is not tombstoned and survives the sync."""
    observed = frozenset(tag for (m, tag) in feature.member_adds if m == member)
    return replace(feature, member_tombstones=feature.member_tombstones | observed)


def delete(feature: AuthoredFeature) -> AuthoredFeature:
    """Delete the feature by tombstoning every member tag observed locally (the feature goes to zero
    live members). Modeled as an OR-Set remove of the whole membership, so a concurrent add on
    another clone -- a tag this delete never saw -- survives, and the delete wins only over what it
    observed."""
    observed = frozenset(tag for (_m, tag) in feature.member_adds)
    return replace(feature, member_tombstones=feature.member_tombstones | observed)


# -- merge (reuses the exact existing primitives) -------------------------------------------------


def merge_feature(
    ours: AuthoredFeature, theirs: AuthoredFeature, is_ancestor: IsAncestor | None = None
) -> AuthoredFeature:
    """Merge two versions of the *same* authored feature (same `af-` id). Membership is an OR-Set
    union (adds and tombstones both union -- commutative, associative, idempotent). The label is
    decided by `reconcile._assign_winner` (causally-later witness wins, hash tie-break when
    concurrent), keyed by the feature id so the tie-break is deterministic and symmetric. The id is
    carried, never recomputed."""
    label, label_witness = _assign_winner(
        ours.id, ours.label, ours.label_witness, theirs.label, theirs.label_witness, is_ancestor,
    )
    return AuthoredFeature(
        id=ours.id,
        label=label,
        label_witness=label_witness,
        member_adds=ours.member_adds | theirs.member_adds,
        member_tombstones=ours.member_tombstones | theirs.member_tombstones,
    )


def merge(
    ours: dict[str, AuthoredFeature],
    theirs: dict[str, AuthoredFeature],
    is_ancestor: IsAncestor | None = None,
) -> dict[str, AuthoredFeature]:
    """Merge two clones' authored-feature collections into a commutative semilattice join (LAW-U): a
    feature present on both sides is merged component-wise (`merge_feature`); one present on a single
    side is carried unchanged. Commutative and idempotent because every component is."""
    merged: dict[str, AuthoredFeature] = {}
    for fid in set(ours) | set(theirs):
        o, t = ours.get(fid), theirs.get(fid)
        if o is not None and t is not None:
            merged[fid] = merge_feature(o, t, is_ancestor)
        else:
            merged[fid] = o if o is not None else t
    return merged


# -- persistence (committed artifact, like pins/aliases) ------------------------------------------


def _feature_from_body(fid: str, body: dict) -> AuthoredFeature:
    return AuthoredFeature(
        id=fid,
        label=body.get("label", ""),
        label_witness=body.get("label_witness"),
        member_adds=frozenset((m, tag) for m, tag in body.get("member_adds", [])),
        member_tombstones=frozenset(body.get("member_tombstones", [])),
    )


def _features_from_payload(payload: dict) -> dict[str, AuthoredFeature]:
    return {fid: _feature_from_body(fid, body) for fid, body in payload.items()}


def load_authored(repo: str | Path = ".") -> dict[str, AuthoredFeature]:
    """The committed authored-feature collection from the working tree, empty when absent -- every
    caller treats "no file" the same as "no authored features", like `load_pins`/`load_aliases`."""
    return _features_from_payload(state.load_json(repo, "authored_features", default={}))


def authored_at(gb, sha: str) -> dict[str, AuthoredFeature]:
    """A teammate's authored-feature collection as committed at `sha` -- the historical-blob read
    `sync` merges, the same version dispatch as a working-tree read (like `aliases_at`)."""
    return _features_from_payload(state.load_blob_json(gb, sha, "authored_features", default={}))


def save_authored(repo: str | Path, features: dict[str, AuthoredFeature]) -> None:
    """Write `.sgt/authored/features.json` -- committed, team-shared current state. The id is the
    key (never re-derived); adds/tombstones are sorted for a deterministic blob, and `label_witness`
    is emitted only when present so a witness-less feature keeps a minimal byte format (mirroring
    `save_pins`'s `assign_witness` discipline)."""
    payload: dict[str, dict] = {}
    for fid in sorted(features):
        f = features[fid]
        body: dict = {
            "label": f.label,
            "member_adds": sorted([m, tag] for m, tag in f.member_adds),
            "member_tombstones": sorted(f.member_tombstones),
        }
        if f.label_witness:
            body["label_witness"] = f.label_witness
        payload[fid] = body
    state.save_json(repo, "authored_features", payload)
