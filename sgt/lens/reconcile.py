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
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from sgt import state
from sgt.core.op import Op
from sgt.lens import tree
from sgt.lens.pins import Contradiction, Pins, find_contradictions, load_pins, save_pins

IsAncestor = Callable[[str, str], bool]  # is_ancestor(a, b): True iff commit a is an ancestor of b

Alias = tuple[str, str]  # (old_id, new_id): a feature id re-mint recorded by the U21 migration


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
    tree.label_tree(result, repo, pins=pins)
    return result


# --- feature-id alias G-Set + birth-id migration (U21/D6) --------------------------------------


def load_aliases(repo: str | Path) -> frozenset[Alias]:
    """The committed feature-id alias G-Set (`.sgt/aliases.json`): every `(old, new)` re-mint the
    migration recorded. Empty when absent -- the documented default, like every other artifact."""
    body = state.load_json(repo, "aliases", default=[])
    return frozenset((pair[0], pair[1]) for pair in body)


def save_aliases(repo: str | Path, aliases: frozenset[Alias]) -> None:
    state.save_json(repo, "aliases", sorted([old, new] for old, new in aliases))


def aliases_at(gb, sha: str) -> frozenset[Alias]:
    """A teammate's alias G-Set as committed at `sha` -- the historical-blob read `sync` unions so a
    stale reference from their un-migrated history resolves on our side after contact."""
    body = state.load_blob_json(gb, sha, "aliases")
    return frozenset() if body is None else frozenset((pair[0], pair[1]) for pair in body)


def union_aliases(ours: frozenset[Alias], theirs: frozenset[Alias]) -> frozenset[Alias]:
    """Union two alias G-Sets and apply the alias-merge rule (D6): when divergent unsynced curation
    minted two different new ids for one old id, the union holds both `(old, new1)` and
    `(old, new2)` -- a genuine collision. Pick a deterministic winner (smallest content hash) and
    add explicit `loser -> winner` edges, so *every* reference (the old id, or either minted new id)
    resolves to the single canonical feature. Commutative, idempotent, and identical on every
    replica -- no wall clock, no sync-order dependence."""
    merged = set(ours) | set(theirs)
    by_old: dict[str, set[str]] = defaultdict(set)
    for old, new in merged:
        by_old[old].add(new)
    for news in by_old.values():
        if len(news) > 1:
            winner = min(news, key=_hash_key)
            merged.update((loser, winner) for loser in news if loser != winner)
    return frozenset(merged)


def resolve_alias(aliases: frozenset[Alias], fid: str) -> str:
    """Follow `old -> new` alias edges from `fid` to its canonical current id. On a collision (an id
    with several targets) the smallest-content-hash target wins -- the same rule `union_aliases`
    applies, so resolution agrees with the merge. Cycle-guarded (a G-Set can, pathologically, hold a
    loop); returns `fid` unchanged when it is already canonical (the common case)."""
    amap: dict[str, list[str]] = defaultdict(list)
    for old, new in aliases:
        amap[old].append(new)
    seen: set[str] = set()
    cur = fid
    while cur in amap and cur not in seen:
        seen.add(cur)
        cur = min(amap[cur], key=_hash_key)
    return cur


@dataclass(frozen=True)
class MigrationReport:
    """The birth-id migration's re-mint map (`old F<n> -> new f-<founding op>`) and whether it was a
    dry run. `remap` empty means nothing to do -- a fresh repo, an already-migrated one, or a second
    run (idempotence)."""

    remap: dict[str, str]
    dry_run: bool

    @property
    def changed(self) -> bool:
        return bool(self.remap) and not self.dry_run


def plan_feature_id_migration(repo: str | Path) -> dict[str, str]:
    """The `old -> new` re-mint map for every legacy sequential `F<n>` leaf in the committed tree:
    `f-<min founding op id>`, the same content-addressed id `tree.build` now mints. Empty when the
    tree is absent or already fully modern -- which is what makes the migration idempotent (a second
    run finds no legacy leaf and returns `{}`). Pure: reads the tree, writes nothing."""
    result = tree.load(repo)
    if result is None:
        return {}
    founding = tree._founding_ops(result.get("op_leaf", {}))
    used = set(result["nodes"])
    remap: dict[str, str] = {}
    for nid in sorted(result["nodes"]):
        nd = result["nodes"][nid]
        if nd["children"] or not tree._is_legacy_id(nid):
            continue
        new_id = tree._content_birth_id(frozenset(nd["members"]), founding.get(nid), used)
        used.add(new_id)
        remap[nid] = new_id
    return remap


def migrate_feature_ids(repo: str | Path, *, dry_run: bool = False) -> MigrationReport:
    """Re-mint every legacy `F<n>` feature id to its content-addressed `f-<founding op>` form as one
    atomic write set (D6): the tree ids, the pin references that name them (`assign` values and
    `labels` keys), and the alias G-Set all move together, so no pin is left keyed to a vanished id.
    `dry_run` computes and returns the map without writing -- the reviewable preview a destructive
    re-mint must offer. Idempotent: a repo with no legacy ids is a no-op."""
    remap = plan_feature_id_migration(repo)
    if dry_run or not remap:
        return MigrationReport(remap=remap, dry_run=dry_run)

    result = tree.load(repo)
    tree._apply_id_map(result, remap)
    tree.save(repo, result)

    pins = load_pins(repo)
    save_pins(repo, Pins(
        assign={m: remap.get(fid, fid) for m, fid in pins.assign.items()},
        assign_witness=pins.assign_witness,
        must_link=pins.must_link,
        cannot_link=pins.cannot_link,
        labels={remap.get(fid, fid): label for fid, label in pins.labels.items()},
    ))

    save_aliases(repo, load_aliases(repo) | frozenset(remap.items()))
    return MigrationReport(remap=remap, dry_run=False)
