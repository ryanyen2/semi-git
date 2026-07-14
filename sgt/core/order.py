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
    """`op_id` and everything that builds on it, over the *whole* op universe -- `↑X` in the
    ADR's set language.

    VALUE-COLLISION CAVEAT: this resolves chain edges via `_adjacency`'s bare
    `(symbol, after_version)` producer map, which picks *one* producer on a collision (e.g. a
    revert landing back on an earlier version's exact bytes) rather than checking existentially.
    U8's `revert`/`pin` verbs therefore do NOT use this; they use the collision-safe,
    ideal-relative `upset_in` below (which reasons existentially, the way `is_valid_ideal` and
    `frontier` were hardened in U7.5). This universe-level form is kept for `tests/core/test_order.py`
    and any future caller whose input is known collision-free; harden it the same way before
    using it on inputs that can revert to an earlier version's exact bytes."""
    _, successors = _adjacency(ops, declared)
    return _reachable(op_id, successors)


def downset(op_id: str, ops: list[Op], declared: Declared = frozenset()) -> frozenset[str]:
    """`op_id` and everything it builds on, over the whole op universe -- `↓X`.

    Same value-collision caveat as `upset` above; U8's `restore`/`cherry-pick` verbs use the
    collision-safe, ideal-relative `downset_in` below instead."""
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


def forks(ops: list[Op], ideal_ids) -> list[tuple[str, str, str]]:
    """Collecting form of `is_fork_free` above: every `(symbol, op_a, op_b)` triple where two
    in-ideal ops claim the same `(symbol, before_version)` chain step, instead of just a bool.
    `ideal_ids` is walked in sorted order for deterministic output -- unlike `is_fork_free`, which
    only needs to notice *that* a collision exists, this needs to report *which* pair, so the
    iteration order is no longer irrelevant. Used by `sgt sync` (U15) to surface same-symbol
    chain forks with a concrete `merge-op`/`pin` remedy instead of silently picking a side."""
    by_id = {op.id: op for op in ops}
    claimed: dict[tuple[str, str | None], str] = {}
    found: list[tuple[str, str, str]] = []
    for op_id in sorted(ideal_ids):
        op = by_id[op_id]
        for sym, (before, _after) in op.footprint.items():
            key = (sym, before)
            claimant = claimed.get(key)
            if claimant is not None and claimant != op_id:
                found.append((sym, claimant, op_id))
            claimed[key] = op_id
    return found


def fork_free(ideal_ids, ops: list[Op], declared: Declared = frozenset()) -> frozenset[str]:
    """The largest fork-free subset of `ideal_ids`: drop every forked symbol's *two* tips together
    with their up-sets. Neither tip can be included without deciding which side wins, so nothing
    transitively built on either can be either. A downward-closed `ideal_ids` minus these
    upward-closed up-sets stays downward-closed, and having removed both claimants of every forked
    step it is now fork-free -- a valid ideal by construction (divergence-as-state, U20/C4). Used by
    `sgt sync`'s resolve to advance a branch by only the fork-free part, and by `lens.ideal_for_ref`
    so a ref whose committed history contains both tips of a fork still projects a valid ideal (the
    forked tip never surfaces in `sgt state`/`sgt map`; only the common ancestor does)."""
    excluded: set[str] = set()
    for _sym, tip_a, tip_b in forks(ops, ideal_ids):
        excluded |= upset(tip_a, ops, declared)
        excluded |= upset(tip_b, ops, declared)
    return frozenset(ideal_ids) - excluded


def reduce_to_ideal(ideal_ids, ops: list[Op], declared: Declared = frozenset()) -> frozenset[str]:
    """The largest *valid ideal* contained in a raw provenance-derived op set: ground it (keep only
    ops all of whose chain/reference/declared prerequisites are also present -- downward-closure),
    then drop forked tips and their up-sets (fork-freedom). One pass suffices, in that order:
    `fork_free` removes only upward-closed up-sets, and removing an upward-closed set from a
    downward-closed one leaves it downward-closed, so grounding survives fork-freeing.

    A raw provenance scan of real history is not directly a valid ideal for two reasons a
    single-clone `sgt` still hits (not just sync): a symbol added, deleted, then re-added rebirths
    with `before=None` both times, so both births claim `(symbol, None)` -- a fork; and an op whose
    chain predecessor's provenance fell outside this ref (a squashed/rebased-away branch) is
    ungrounded. `fork_free` alone leaves the ungrounded op in; `_grounded` alone leaves the fork in;
    only the composition is constructible. This is the reduction every provenance-derived ideal
    goes through -- `lens._sync` on the mine-on-contact write path, and the pure-read
    `_committed_ids_by_provenance`/`ideal_for_ref` -- so all three agree on what a ref's history
    means (divergence, historical or concurrent, is state, never a crash; U20/C4)."""
    return fork_free(_grounded(ideal_ids, ops, declared), ops, declared)


def find_declared_cycles(ops: list[Op], declared: Declared) -> list[tuple[str, str]]:
    """Declared edges (`sgt after`) that lie on a cycle in the full chain+reference+declared
    adjacency -- e.g. two clones each declare an edge that, unioned, contradicts the other.
    Iterative DFS over `_adjacency`'s successors, tracking the current path to find back-edges;
    every declared edge on a detected cycle is returned (sorted, for deterministic output). An
    empty result means the union is still a valid partial order. Used by `sgt sync` (U15) to
    surface which `after` declarations need retracting rather than committing a cyclic union."""
    _, successors = _adjacency(ops, declared)
    ids = sorted(successors.keys())

    WHITE, GRAY, BLACK = 0, 1, 2
    color = {op_id: WHITE for op_id in ids}
    cyclic_edges: set[tuple[str, str]] = set()

    for start in ids:
        if color[start] != WHITE:
            continue
        color[start] = GRAY
        path = [start]
        stack = [iter(sorted(successors.get(start, ())))]
        while stack:
            nxt = next(stack[-1], None)
            if nxt is None:
                color[path.pop()] = BLACK
                stack.pop()
                continue
            if color[nxt] == GRAY:
                idx = path.index(nxt)
                cycle_nodes = path[idx:] + [nxt]
                for a, b in zip(cycle_nodes, cycle_nodes[1:]):
                    if (a, b) in declared:
                        cyclic_edges.add((a, b))
            elif color[nxt] == WHITE:
                color[nxt] = GRAY
                path.append(nxt)
                stack.append(iter(sorted(successors.get(nxt, ()))))
    return sorted(cyclic_edges)


def _grounded(ideal_ids, ops: list[Op], declared: Declared = frozenset()) -> frozenset[str]:
    """The largest *well-founded* downward-closed subset of `ideal_ids`: ops reachable from chain
    heads (`before_version` None) by actual production, computed as a least fixpoint. An op joins
    once every one of its before_versions is None or already produced by a grounded op, every
    `requires` version is produced by a grounded op, and every declared predecessor is grounded.

    Grounding -- not mere existence of *some* producer -- is the correct downward-closure. The
    existential check alone accepts an originless cycle (e.g. `{modify, revert}` lifted out of
    add->modify->revert: modify's `before` is produced by revert and revert's by modify, but
    neither bottoms out at the add), which is not a real ideal -- it has no frontier head to fold.
    Reasoning about *versions* produced (never picking a single canonical producer op) keeps it
    immune to the after-value collision that the graph-edge form mis-resolves."""
    ideal_set = set(ideal_ids)
    by_id = {op.id: op for op in ops if op.id in ideal_set}
    declared_preds: dict[str, set[str]] = {}
    for a, b in declared:
        if b in by_id:
            declared_preds.setdefault(b, set()).add(a)

    grounded: set[str] = set()
    produced: set[tuple[str, str]] = set()
    changed = True
    while changed:
        changed = False
        for op_id, op in by_id.items():
            if op_id in grounded:
                continue
            if (
                all(before is None or (sym, before) in produced
                    for sym, (before, _a) in op.footprint.items())
                and all(req in produced for req in op.requires)
                and all(p in grounded for p in declared_preds.get(op_id, ()))
            ):
                grounded.add(op_id)
                produced.update((sym, after) for sym, (_b, after) in op.footprint.items())
                changed = True
    return frozenset(grounded)


def is_valid_ideal(ops: list[Op], ideal_ids, declared: Declared = frozenset()) -> bool:
    """R3: downward-closed under chain+reference+declared edges, and fork-free. Every op id in
    `ideal_ids` must also exist in `ops` -- referencing an unknown op is never valid.

    Downward-closure is checked by *grounding* (`_grounded`): every op must bottom out at a chain
    head through real production, so the check reasons about which `(symbol, version)` pairs the
    ideal produces rather than picking one canonical producer op -- immune to the after-value
    collision (add->modify->revert) that a graph-edge form mis-resolves, and rejecting an
    originless cycle that a purely-existential check would wrongly accept. Declared edges fold
    into the same grounding (an op grounds only once its declared predecessors do)."""
    ids = set(ideal_ids)
    if not ids <= {op.id for op in ops}:
        return False
    if _grounded(ids, ops, declared) != ids:
        return False

    return is_fork_free(ops, ids)


def _ordered_chains(ideal_ids, ops: list[Op]) -> dict[str, list[str]]:
    """Per symbol, the in-ideal op ids in chain order (head -> tip). Walks forward from the chain
    head (the op whose `before_version` is None) via a `before_value -> op_id` map, rather than
    testing `after`-value membership: two ops can legitimately share an after-value (a revert to
    an earlier version's exact bytes), but fork-freedom guarantees `before_value` stays a unique
    key per symbol, so this map is unambiguous. The `visited` guard stops the walk the moment it
    would revisit an op -- the only way that happens is a later op's after coincidentally matching
    an earlier before already passed through, i.e. the tip has been reached.

    The shared spine of `frontier` (tip = last of each chain), `downset_in`'s chain prerequisites,
    and any verb that needs a symbol's positional order within a valid ideal."""
    by_id = {op.id: op for op in ops}

    next_step: dict[str, dict[str | None, str]] = {}
    for op_id in set(ideal_ids):
        for sym, (before, _after) in by_id[op_id].footprint.items():
            next_step.setdefault(sym, {})[before] = op_id

    chains: dict[str, list[str]] = {}
    for sym, steps in next_step.items():
        op_id = steps[None]  # the chain head -- downward-closure guarantees it's in the ideal
        seq = [op_id]
        visited = {op_id}
        after = by_id[op_id].footprint[sym][1]
        while after in steps and steps[after] not in visited:
            op_id = steps[after]
            seq.append(op_id)
            visited.add(op_id)
            after = by_id[op_id].footprint[sym][1]
        chains[sym] = seq
    return chains


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
    sets. The tip is the last op of each symbol's ordered chain (`_ordered_chains`)."""
    return {sym: seq[-1] for sym, seq in _ordered_chains(ideal_ids, ops).items()}


def upset_in(target: str, ideal_ids, ops: list[Op], declared: Declared = frozenset()) -> frozenset[str]:
    """`target` and everything in the (fork-free) ideal `ideal_ids` that transitively builds on it
    -- `revert(target) = ideal \\ upset_in(target, ideal)`. The collision-safe, ideal-relative
    counterpart to `upset`: the up-set is exactly what stops being grounded once `target` is
    removed, i.e. `ideal \\ _grounded(ideal - {target})`. Grounding (not existential production)
    is what makes reverting a chain head remove the whole chain rather than leave an originless
    modify->revert cycle behind, and what keeps a declared successor or an op that only `target`
    could reach out of the result. The complement `_grounded(...)` is a valid ideal by
    construction (well-founded, and fork-free as a subset of a fork-free ideal), so
    `ideal \\ upset_in(...)` never needs a further validity check."""
    return upset_in_many({target}, ideal_ids, ops, declared)


def upset_in_many(removed, ideal_ids, ops: list[Op], declared: Declared = frozenset()) -> frozenset[str]:
    """The set generalization of `upset_in` (which is the single-target case): everything in
    `ideal_ids` that stops being grounded once *all* of `removed` is taken out -- `ideal \\
    _grounded(ideal - removed)`. Used by U8's three-way sync resolve: `removed` is the base ops one
    side reverted, and this is exactly what must leave the union so a revert travels instead of
    being resurrected. Collision-safe (grounding, not graph adjacency), and the complement is a
    valid downward-closed ideal by construction, so the subtraction needs no further check."""
    ids = set(ideal_ids)
    return frozenset(ids - _grounded(ids - set(removed), ops, declared))


def downset_in(target: str, ideal_ids, ops: list[Op], declared: Declared = frozenset()) -> frozenset[str]:
    """`target` and everything it builds on, within the (fork-free) source ideal `ideal_ids` --
    `restore/cherry-pick(target) = current_ideal | downset_in(target, source_ideal)`. Chain
    prerequisites come from `_ordered_chains` (positional order, immune to the after-value
    collision that `downset`'s adjacency mis-resolves); `requires` and declared prerequisites are
    closed transitively. Reference prerequisites include *every* in-ideal producer of a required
    version -- a safe over-approximation when a dependency itself was reverted to earlier bytes
    (two producers of one version); the final `Ideal.from_ops` still rejects any union that forks."""
    by_id = {op.id: op for op in ops}
    chains = _ordered_chains(ideal_ids, ops)
    pos = {sym: {oid: i for i, oid in enumerate(seq)} for sym, seq in chains.items()}
    producers: dict[tuple[str, str], set[str]] = {}
    for oid in set(ideal_ids):
        for sym, (_before, after) in by_id[oid].footprint.items():
            producers.setdefault((sym, after), set()).add(oid)

    result: set[str] = set()
    stack = [target]
    while stack:
        oid = stack.pop()
        if oid in result or oid not in by_id:
            continue
        result.add(oid)
        op = by_id[oid]
        for sym in op.footprint:  # chain prerequisites: everything before oid in this symbol's chain
            i = pos.get(sym, {}).get(oid)
            if i is not None:
                stack.extend(chains[sym][:i])
        for req in op.requires:  # reference prerequisites
            stack.extend(producers.get(req, ()))
        stack.extend(a for a, b in declared if b == oid)  # declared prerequisites
    return frozenset(result)
