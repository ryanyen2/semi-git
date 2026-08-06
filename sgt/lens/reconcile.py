"""Merge-time reconciliation of pins and the feature tree (plan U15, R19/AE4): the two pieces of
`sgt sync` that aren't just "union the op store and re-fold" (`sgt.core.sync` handles that part).

Pins are unioned structurally -- a dict/set merge, latest-wins on a genuine key collision (D3) --
then checked for contradictions the same way a single clone's pin edit already is
(`sgt.lens.pins.find_contradictions`), never raising. The tree is *rebuilt*, not merged: it is a
deterministic clustering overlay derived from the op store, not a source of truth, so once the op
store is unioned the correct tree is whatever `tree.build` would produce from that union -- Greene
member-overlap matching against the caller's own last-known tree (`previous`) is what keeps our
own feature ids stable across the sync rather than being replaced by a coincidentally-numbered
`theirs` id.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Callable

from sgt.core.op import Op
from sgt.lens import tree
from sgt.lens.pins import Contradiction, Pins, find_contradictions

IsAncestor = Callable[[str, str], bool]  # is_ancestor(a, b): True iff commit a is an ancestor of b


def _hash_key(*parts: str) -> str:
    """A pure content hash over stable inputs -- the wall-clock-free, replica-independent tie-break
    both `assign` and `labels` fall back to when the git DAG can't order two curations (concurrent
    or witness-less). Deliberately excludes the witness SHA: witnesses differ across independently
    built repos, so only stable content (member/feature/label) may decide the winner, or two
    schedules over separate clones would converge to *different* answers (LAW-U)."""
    return hashlib.sha256("\x00".join(parts).encode("utf-8")).hexdigest()


def _later_witness(a: str | None, b: str | None, is_ancestor: IsAncestor | None) -> str | None:
    """The causally-later of two witness SHAs (the descendant), or a deterministic pick when they
    are concurrent/unknown -- so a merged pin's stored witness is itself schedule-independent."""
    if not a:
        return b
    if not b:
        return a
    if a == b:
        return a
    if is_ancestor is not None:
        if is_ancestor(a, b):
            return b
        if is_ancestor(b, a):
            return a
    return max(a, b)


def _assign_winner(
    member: str, ours_fid: str, ours_w: str | None, theirs_fid: str, theirs_w: str | None,
    is_ancestor: IsAncestor | None,
) -> tuple[str, str | None]:
    """Decide a same-member `assign` collision (D6): the assignment whose introducing witness is
    causally *later* in the git DAG wins (a deliberate re-pin beats the stale one it was made on
    top of); when the two witnesses are concurrent or absent, a content-hash tie-break decides,
    deterministically and symmetrically in ours/theirs. Returns `(feature_id, witness)`."""
    if ours_fid == theirs_fid:
        return ours_fid, _later_witness(ours_w, theirs_w, is_ancestor)
    if ours_w and theirs_w and is_ancestor is not None:
        ours_older = is_ancestor(ours_w, theirs_w)
        theirs_older = is_ancestor(theirs_w, ours_w)
        if theirs_older and not ours_older:
            return ours_fid, ours_w
        if ours_older and not theirs_older:
            return theirs_fid, theirs_w
    # Concurrent (or witness-less): the feature id with the larger content hash wins -- pure,
    # order-independent, and identical across replicas that never shared a commit clock.
    if _hash_key(member, ours_fid) >= _hash_key(member, theirs_fid):
        return ours_fid, ours_w
    return theirs_fid, theirs_w


def union_pins(
    ours: Pins, theirs: Pins, is_ancestor: IsAncestor | None = None,
) -> tuple[Pins, list[Contradiction]]:
    """Reconcile two clones' pins into a commutative semilattice join (D6/C1, LAW-U). `must_link`/
    `cannot_link` are plain set unions (already associative/commutative/idempotent). `assign` is a
    function member->feature, so a same-member collision must pick one side: it is decided by the
    witness-topological tie-break (`_assign_winner`) -- causally-later curation wins, hash tie-break
    when concurrent -- never by which side happens to be `theirs` in this sync (the pre-U21
    latest-wins bug). `labels` (feature->label) is the same shape and gets the same content-hash
    tie-break. Any contradiction the union introduces is surfaced via `find_contradictions`, never
    raised. `is_ancestor` (a `GitBinding.is_ancestor`-shaped callable) supplies the DAG order; when
    None, every collision falls through to the hash tie-break -- still order-independent."""
    assign: dict[str, str] = {}
    assign_witness: dict[str, str] = {}
    for member in set(ours.assign) | set(theirs.assign):
        in_ours, in_theirs = member in ours.assign, member in theirs.assign
        if in_ours and in_theirs:
            fid, witness = _assign_winner(
                member, ours.assign[member], ours.assign_witness.get(member),
                theirs.assign[member], theirs.assign_witness.get(member), is_ancestor,
            )
        elif in_ours:
            fid, witness = ours.assign[member], ours.assign_witness.get(member)
        else:
            fid, witness = theirs.assign[member], theirs.assign_witness.get(member)
        assign[member] = fid
        if witness:
            assign_witness[member] = witness

    labels: dict[str, str] = {}
    for fid in set(ours.labels) | set(theirs.labels):
        o, t = ours.labels.get(fid), theirs.labels.get(fid)
        if o is not None and t is not None and o != t:
            labels[fid] = o if _hash_key(fid, o) >= _hash_key(fid, t) else t
        else:
            labels[fid] = o if o is not None else t

    merged = Pins(
        assign=assign,
        assign_witness=assign_witness,
        must_link=ours.must_link | theirs.must_link,
        cannot_link=ours.cannot_link | theirs.cannot_link,
        labels=labels,
    )
    return merged, find_contradictions(merged)


def reconcile_tree(
    repo: Path, ops: list[Op], ideal, pins: Pins, ours_tree: dict | None,
) -> dict:
    """Rebuild the feature tree from the unioned op store/ideal, Greene-matched against
    `ours_tree` (our own last-committed tree, not a merge of both sides' trees) so a feature id we
    already assigned stays stable across the sync. Mirrors `sgt.lens.map.build_map`'s
    build-then-label sequence, but takes `ops`/`ideal`/`pins` explicitly rather than reading them
    off disk -- mid-sync, the unioned op store and ideal exist only in memory until the merge
    commit lands."""
    result = tree.build(repo, ops, ideal, pins=pins, previous=ours_tree)
    # Label the tree in-memory but don't persist the cache: this function is only ever called from
    # `land`'s speculative attempt loop (via `resolve.resolve`), which may discard the result on a
    # red oracle or lost CAS. The label cache is `committed=False` (not git-tracked), so a rolled-
    # back attempt's `restore_worktree_to` can't undo a write here -- it would leak past R7's "no
    # trace" guarantee. `result`'s nodes already carry the labels regardless of this save.
    tree.label_tree(result, repo, pins=pins, ops=ops)
    return result
