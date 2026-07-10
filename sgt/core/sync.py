"""`sgt sync`: bring a teammate's work in through git without a textual merge (plan U15, R19/AE4).

Source files are *derived* (`code(I)`), so sync never merges them textually -- it fetches the
remote branch, unions the op store (near-free: `Store.add_bytes` unions provenance on any
content-address collision, R8), reconciles the ideal, pins, declared edges, and feature tree, and
then re-folds the working tree from the union. Footprint-disjoint work merges with zero
interaction; a same-symbol chain fork is *surfaced* (with the exact `sgt merge-op`/`sgt pin`
remedy) rather than silently resolved -- the ADR's "the only possible conflict is chain
divergence" holds at sync time exactly as it does for a single clone's ideal.

Pipeline:
  1. `lens.get(repo)` absorbs any dirty edit or foreign local commit first (R9); sync then
     refuses if the tree is still dirty (mirrors `put`'s guard) -- a clean tree is what `git
     merge` needs anyway.
  2. Fetch `remote/branch` (defaulting to HEAD's upstream, else `origin`/the current branch).
     `theirs` already being an ancestor of `ours` (nothing new, or we're already ahead) is a
     no-op -- this is what makes a second `sync` idempotent.
  3. Capture both sides' pins/declared/tree/ideal *before* merging touches disk: ours from the
     working tree, theirs by reading blobs at `theirs_sha` directly (no checkout needed).
  4. `git merge --no-commit -X ours theirs_sha` stages the file-level union. `-X ours` only
     matters for a path both sides changed identically-named but differently-contented (an op
     both sides independently mined, whose provenance lists differ, or `.sgt/pins`/`tree`/
     `declared`/source) -- sgt overwrites every one of those with the real reconciliation next,
     so which side git's own resolution picked never surfaces.
  5. Re-union every op's provenance explicitly (`Store.add_bytes` over every op path in theirs'
     tree) -- undoes `-X ours` silently dropping theirs' witness commits on a same-id collision.
  6. `order.forks` over the unioned ideal. Forked -> abort the merge and report the exact
     `merge-op`/`pin` remedy, uncommitted. Fork-free -> the union is a valid `Ideal`.
  7. Union pins (`reconcile.union_pins`, latest-wins with contradictions reported, never raised)
     and declared edges (with a cycle check, `order.find_declared_cycles`); save both.
  8. Rebuild the feature tree from the union (`reconcile.reconcile_tree`), Greene-matched against
     our own last-committed tree so our feature ids stay stable.
  9. Fold (`code`), write the working tree, complete the merge commit with `Sgt-Op:` trailers,
     and persist the merged ideal as this ref's committed set (`lens.record_ideal`).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from sgt.core import lens, order
from sgt.core.fold import code
from sgt.core.ideal import Ideal
from sgt.core.store import Store
from sgt.lens import reconcile, tree
from sgt.lens.pins import Contradiction, Pins, _pins_from_payload, load_pins, save_pins
from sgt.store.gitbind import GitBinding, GitError, format_op_trailers, parse_op_ids


@dataclass(frozen=True)
class SyncReport:
    remote: str
    branch: str
    merged: bool  # a merge commit landed; False means nothing new, or the merge was aborted
    message: str
    fetched_sha: str | None = None
    merge_sha: str | None = None
    ops_added: int = 0
    forks: tuple[tuple[str, str, str], ...] = ()
    pin_contradictions: tuple[Contradiction, ...] = ()
    declared_cycles: tuple[tuple[str, str], ...] = ()
    identity_events: tuple[dict, ...] = field(default_factory=tuple)


def _declared_at(gb: GitBinding, sha: str) -> frozenset[tuple[str, str]]:
    raw = gb.blob_bytes(sha, ".sgt/declared.json")
    if raw is None:
        return frozenset()
    return frozenset(tuple(pair) for pair in json.loads(raw.decode("utf-8")))


def _pins_at(gb: GitBinding, sha: str) -> Pins:
    raw = gb.blob_bytes(sha, ".sgt/pins/pins.json")
    if raw is None:
        return Pins()
    return _pins_from_payload(json.loads(raw.decode("utf-8")))


def sync(repo: str | Path, remote: str | None = None, branch: str | None = None) -> SyncReport:
    repo = Path(repo)
    gb = GitBinding(repo)

    lens.get(repo)  # absorb local reality first (R9)
    if not gb.is_clean():
        raise lens.DirtyWorkingTreeError(
            "sgt sync requires a clean working tree -- `sgt put` or commit first"
        )

    remote = remote or gb.default_remote()
    branch = branch or gb.default_branch()
    if branch is None:
        raise ValueError("no branch to sync -- HEAD has no upstream and isn't on a named branch")

    ours_sha = gb.head()
    if ours_sha is None:
        raise ValueError("sgt sync requires at least one commit")

    gb.fetch(remote, branch)
    theirs_sha = gb.rev_parse("FETCH_HEAD")
    if theirs_sha is None:
        raise GitError(f"fetch of {remote}/{branch} produced no FETCH_HEAD")

    if theirs_sha in set(gb.commit_shas(ours_sha)):
        return SyncReport(
            remote=remote, branch=branch, merged=False, fetched_sha=theirs_sha,
            message="already up to date",
        )

    # Capture both sides' metadata before the merge touches disk.
    ours_pins = load_pins(repo)
    theirs_pins = _pins_at(gb, theirs_sha)
    ours_declared = lens._load_declared(repo)
    theirs_declared = _declared_at(gb, theirs_sha)
    ours_tree = tree.load(repo)
    ours_ideal = lens.current_ideal(repo)
    theirs_ideal_ids = set(parse_op_ids(gb.commit_message(theirs_sha)))

    if not gb.merge_ours_no_commit(theirs_sha):
        gb.merge_abort()
        raise GitError(
            f"could not merge {remote}/{branch}: unresolved conflict outside sgt's own paths"
        )

    store = Store(repo)
    store.init()
    before_ids = {op.id for op in store.all_ops()}
    for path in gb.list_tree(theirs_sha, ".sgt/ops/"):
        raw = gb.blob_bytes(theirs_sha, path)
        if raw is not None:
            store.add_bytes(raw)  # re-unions provenance `-X ours` may have dropped
    all_ops = store.all_ops()
    ops_added = len({op.id for op in all_ops} - before_ids)

    union_ids = ours_ideal.op_ids | theirs_ideal_ids
    declared = ours_declared | theirs_declared

    fork_triples = order.forks(all_ops, union_ids)
    if fork_triples:
        gb.merge_abort()
        remedies = "; ".join(f"sgt merge-op {a[:8]} {b[:8]}" for _sym, a, b in fork_triples)
        return SyncReport(
            remote=remote, branch=branch, merged=False, fetched_sha=theirs_sha,
            forks=tuple(fork_triples),
            message=f"fork(s) detected, not merged -- resolve with: {remedies}",
        )

    # A cyclic declared union can never be honored -- fold without the offending edges (report
    # them for `sgt after` retraction) rather than letting `Ideal.from_ops` raise on it.
    declared_cycles = order.find_declared_cycles(all_ops, declared)
    usable_declared = declared - set(declared_cycles)
    merged_ideal = Ideal.from_ops(union_ids, all_ops, usable_declared)

    unioned_pins, pin_contradictions = reconcile.union_pins(ours_pins, theirs_pins)
    save_pins(repo, unioned_pins)

    lens._save_declared(repo, declared)

    tree_result = reconcile.reconcile_tree(repo, all_ops, merged_ideal, unioned_pins, ours_tree)
    tree.save(repo, tree_result)

    materialized = code(merged_ideal, all_ops)
    lens._write_working_tree(repo, materialized)
    trailers = format_op_trailers(sorted(merged_ideal.op_ids))
    merge_sha = gb.complete_merge(f"sgt sync: merge {remote}/{branch}", trailers=trailers)
    lens.record_ideal(repo, merged_ideal, merge_sha)

    return SyncReport(
        remote=remote, branch=branch, merged=True, fetched_sha=theirs_sha, merge_sha=merge_sha,
        ops_added=ops_added, pin_contradictions=tuple(pin_contradictions),
        declared_cycles=tuple(declared_cycles),
        identity_events=tuple(tree_result.get("identity_events", [])),
        message="merged",
    )
