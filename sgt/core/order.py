"""The order `<=` over ops, from three edge sources (ADR S3.3; plan R3, R4, R10).

    1. chain edges     -- same symbol: op A's after_version matches op B's before_version.
                           Ops touching one symbol are totally ordered along its chain (unless
                           forked -- see below).
    2. reference edges -- op B's `requires` names an exact (symbol, version) pair; whichever op
                           produced that version precedes B. Mined def/use, resolved to the
                           *specific* version B saw when mined (sgt.core.mine records this),
                           not merely "some op that ever touched this symbol".
    3. declared edges   -- an explicit `(A, B)` pair meaning A <= B (`sgt after`, U8's escape
                           hatch for edges the analyzer can't see). Passed in by the caller; this
                           module doesn't own where declared edges are persisted.

All three collapse into one predecessor/successor adjacency; `upset`/`downset` are BFS over it.
Validity (R3) is two independent checks: downward-closure over that adjacency, and fork-freedom
(no two in-ideal ops share a `(symbol, before_version)` -- the ADR's "the only possible conflict
is chain divergence"). Fork-freedom is *not* implied by downward-closure: a fork's two tips have
no edge between each other (both descend from the same predecessor), so an ideal could be
downward-closed and still contain both.
"""

from __future__ import annotations

from sgt.core.op import Op

Edge = tuple[str, str]  # (A, B) meaning A <= B: A must be in the ideal whenever B is
Declared = frozenset[Edge]


def chain_edges(ops: list[Op]) -> frozenset[Edge]:
    """(A, B): some symbol's after_version in A equals its before_version in B."""
    producer_after: dict[tuple[str, str], str] = {}
    consumer_before: dict[tuple[str, str], list[str]] = {}
    for op in ops:
        for sym, (before, after) in op.footprint.items():
            producer_after[(sym, after)] = op.id
            if before is not None:
                consumer_before.setdefault((sym, before), []).append(op.id)

    edges: set[Edge] = set()
    for (sym, version), producer_id in producer_after.items():
        for consumer_id in consumer_before.get((sym, version), ()):
            if consumer_id != producer_id:
                edges.add((producer_id, consumer_id))
    return frozenset(edges)


def reference_edges(ops: list[Op]) -> frozenset[Edge]:
    """(A, B): B requires an exact (symbol, version) pair that A produced."""
    producer_after: dict[tuple[str, str], str] = {}
    for op in ops:
        for sym, (_before, after) in op.footprint.items():
            producer_after[(sym, after)] = op.id

    edges: set[Edge] = set()
    for op in ops:
        for req in op.requires:
            producer_id = producer_after.get(req)
            if producer_id is not None and producer_id != op.id:
                edges.add((producer_id, op.id))
    return frozenset(edges)


def _adjacency(
    ops: list[Op], declared: Declared = frozenset()
) -> tuple[dict[str, frozenset[str]], dict[str, frozenset[str]]]:
    """(predecessors, successors) over the union of all three edge sources. Edges naming an op
    id outside `ops` are dropped rather than raising -- a caller building an adjacency for a
    smaller universe (e.g. one commit's worth of ops) shouldn't have to pre-filter `declared`."""
    ids = {op.id for op in ops}
    predecessors: dict[str, set[str]] = {op.id: set() for op in ops}
    successors: dict[str, set[str]] = {op.id: set() for op in ops}
    for a, b in chain_edges(ops) | reference_edges(ops) | declared:
        if a in ids and b in ids and a != b:
            predecessors[b].add(a)
            successors[a].add(b)
    return (
        {k: frozenset(v) for k, v in predecessors.items()},
        {k: frozenset(v) for k, v in successors.items()},
    )


def _reachable(start: str, adjacency: dict[str, frozenset[str]]) -> frozenset[str]:
    seen = {start}
    stack = [start]
    while stack:
        cur = stack.pop()
        for nxt in adjacency.get(cur, ()):
            if nxt not in seen:
                seen.add(nxt)
                stack.append(nxt)
    return frozenset(seen)


def upset(op_id: str, ops: list[Op], declared: Declared = frozenset()) -> frozenset[str]:
    """`op_id` and everything that builds on it -- `revert(X) = I \\ upset(X)`."""
    _, successors = _adjacency(ops, declared)
    return _reachable(op_id, successors)


def downset(op_id: str, ops: list[Op], declared: Declared = frozenset()) -> frozenset[str]:
    """`op_id` and everything it builds on -- `restore/cherry-pick(X) = I | downset(X)`."""
    predecessors, _ = _adjacency(ops, declared)
    return _reachable(op_id, predecessors)


def is_fork_free(ops: list[Op], ideal_ids) -> bool:
    """No two in-ideal ops share a `(symbol, before_version)` pair -- the ADR's "the only
    possible conflict is chain divergence"."""
    by_id = {op.id: op for op in ops}
    claimed: dict[tuple[str, str | None], str] = {}
    for op_id in ideal_ids:
        op = by_id[op_id]
        for sym, (before, _after) in op.footprint.items():
            key = (sym, before)
            claimant = claimed.get(key)
            if claimant is not None and claimant != op_id:
                return False
            claimed[key] = op_id
    return True


def is_valid_ideal(ops: list[Op], ideal_ids, declared: Declared = frozenset()) -> bool:
    """R3: downward-closed under chain+reference+declared edges, and fork-free. Every op id in
    `ideal_ids` must also exist in `ops` -- referencing an unknown op is never valid."""
    by_id = {op.id: op for op in ops}
    ids = set(ideal_ids)
    if not ids <= by_id.keys():
        return False
    predecessors, _ = _adjacency(ops, declared)
    for op_id in ids:
        if not predecessors.get(op_id, frozenset()) <= ids:
            return False
    return is_fork_free(ops, ids)


def frontier(ideal_ids, ops: list[Op]) -> dict[str, str]:
    """Per-symbol frontier (the ADR's per-chain frontier vector): for each symbol touched
    anywhere in the ideal, the id of the *maximal* in-ideal op for that symbol's chain -- the
    tip `code(I)` (U5) splices from. A symbol whose tip's `after_version` is `op.BOTTOM` is
    present in this map but no longer alive; callers that want only live symbols must check
    that themselves (this module doesn't import mine.py's BOTTOM-producing call sites, only the
    sentinel value lives in `sgt.core.op`).

    Computed, not stored -- an explicit `ideal_ids` set is still this kernel's canonical
    in-memory representation (validity, upset/downset, and diff all operate on it directly);
    this is the compact *view* the ADR's frontier-vector KTD calls for, and the one U5's fold
    and U6/U9's on-disk ref->ideal persistence should use rather than serializing full op-id
    sets.
    """
    by_id = {op.id: op for op in ops}
    ids = set(ideal_ids)
    superseded: set[tuple[str, str]] = set()
    for op_id in ids:
        for sym, (before, _after) in by_id[op_id].footprint.items():
            if before is not None:
                superseded.add((sym, before))

    tip: dict[str, str] = {}
    for op_id in ids:
        for sym, (_before, after) in by_id[op_id].footprint.items():
            if (sym, after) not in superseded:
                tip[sym] = op_id  # nothing in the ideal builds further on this version
    return tip
