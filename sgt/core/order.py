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

from collections import OrderedDict

from sgt.core.op import Op

Edge = tuple[str, str]  # (A, B) meaning A <= B: A must be in the ideal whenever B is
Declared = frozenset[Edge]

# Bounded LRU memo for `reduce_to_ideal` (U8). Its result is a pure function of the *present-op* id
# set and `declared`: grounding and fork-freedom only ever read ops *in* `ideal_ids` (a path from a
# forked tip to an ideal op through a missing op leaves that op ungrounded, so it is gone before
# fork-freeing runs), and every op is content-addressed, so a set of *materialized* ids fixes the
# answer. `reduce_to_ideal` intersects `ideal_ids` with the present ops before keying, so an id that
# no longer has an op in `ops` (a `git switch` can swap the committed store out from under a surviving
# gitignored ideal table) does not collide with a hit computed while it was still present.
# Bounded (not `functools.lru_cache`, whose args must be hashable and which never evicts by size)
# so a long-running MCP/TUI session over an append-only store doesn't accumulate an entry per save.
_REDUCE_CACHE: "OrderedDict[tuple, frozenset[str]]" = OrderedDict()
_REDUCE_CACHE_MAX = 64


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


# Bounded memo for `chain_edges | reference_edges`, keyed by the op-id set -- the same
# "content-addressed ids fix the answer" invariant `_REDUCE_CACHE` documents (both edge sets
# read only footprint/requires, which the id hashes). One `compose`/`intent` read used to
# recompute the full-store union dozens of times (once per scope bundle / checkpoint tier).
_EDGES_CACHE: "OrderedDict[frozenset, frozenset[Edge]]" = OrderedDict()
_EDGES_CACHE_MAX = 16


def _chain_ref_edges(ops: list[Op]) -> frozenset[Edge]:
    key = frozenset(op.id for op in ops)
    cached = _EDGES_CACHE.get(key)
    if cached is not None:
        _EDGES_CACHE.move_to_end(key)
        return cached
    edges = chain_edges(ops) | reference_edges(ops)
    _EDGES_CACHE[key] = edges
    if len(_EDGES_CACHE) > _EDGES_CACHE_MAX:
        _EDGES_CACHE.popitem(last=False)
    return edges


def _adjacency(
    ops: list[Op], declared: Declared = frozenset()
) -> tuple[dict[str, frozenset[str]], dict[str, frozenset[str]]]:
    """(predecessors, successors) over the union of all three edge sources. Edges naming an op
    id outside `ops` are dropped rather than raising -- a caller building an adjacency for a
    smaller universe (e.g. one commit's worth of ops) shouldn't have to pre-filter `declared`."""
    ids = {op.id for op in ops}
    predecessors: dict[str, set[str]] = {op.id: set() for op in ops}
    successors: dict[str, set[str]] = {op.id: set() for op in ops}
    for a, b in _chain_ref_edges(ops) | declared:
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


def components_in(
    op_ids, ops: list[Op], declared: Declared = frozenset(),
) -> list[frozenset[str]]:
    """Connected components of `op_ids` under the *undirected* union of chain/reference/declared
    edges, restricted to edges whose both endpoints are in `op_ids`. Unlike every other function
    in this module, this makes no downward-closure assumption about `op_ids` -- it never calls
    `_ordered_chains`/`_grounded`, so it has no ideal-validity precondition an arbitrary commit's
    or scope-bundle's op-set (not a valid ideal) can violate. Answers "are these ops linked at
    all" (connectivity), not "does A build on B" (`upset`/`downset`'s directed reachability,
    which requires a genuine ideal to mean anything)."""
    ids = frozenset(op_ids)
    adjacency: dict[str, set[str]] = {oid: set() for oid in ids}
    for a, b in _chain_ref_edges(ops) | declared:
        if a in ids and b in ids and a != b:
            adjacency[a].add(b)
            adjacency[b].add(a)
    frozen_adjacency = {k: frozenset(v) for k, v in adjacency.items()}

    seen: set[str] = set()
    components: list[frozenset[str]] = []
    for start in sorted(ids):
        if start in seen:
            continue
        comp = _reachable(start, frozen_adjacency)
        seen |= comp
        components.append(comp)
    return components


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


def parked_forks(ideal_ids, ops: list[Op], declared: Declared = frozenset()) -> list[tuple[str, str, str]]:
    """The `(symbol, op_a, op_b)` fork triples `reduce_to_ideal` silently drops: `fork_free` grounds
    `ideal_ids` first, then removes both tips of every same-`(symbol, before)` collision among the
    *grounded* ops. Reporting forks on the grounded set (not the raw `ideal_ids`) is the point --
    it keeps a mere reduction-drop (an ungrounded op that only *looks* like it collides with a live
    one) from surfacing as an open fork; only a divergence both of whose tips are grounded is real.
    The collecting counterpart of the drop `fork_free` performs, so `lens._sync`'s rebuild can record
    what it parked in the shared fork store (1.4) instead of excluding it in silence."""
    return forks(ops, _grounded(ideal_ids, ops, declared))


def fork_free(ideal_ids, ops: list[Op], declared: Declared = frozenset()) -> frozenset[str]:
    """The largest fork-free subset of `ideal_ids`: drop every forked symbol's *two* tips together
    with their up-sets. Neither tip can be included without deciding which side wins, so nothing
    transitively built on either can be either. A downward-closed `ideal_ids` minus these
    upward-closed up-sets stays downward-closed, and having removed both claimants of every forked
    step it is now fork-free -- a valid ideal by construction (divergence-as-state, U20/C4). Used by
    `sgt sync`'s resolve to advance a branch by only the fork-free part, and by `lens.ideal_for_ref`
    so a ref whose committed history contains both tips of a fork still projects a valid ideal (the
    forked tip never surfaces in `sgt state`/`sgt map`; only the common ancestor does).

    The tip up-sets are removed by *re-grounding* the remainder (`_grounded(ideal_ids - tips)`), the
    same collision-safe way `upset_in_many` computes what stops being grounded once a set is taken
    out -- NOT by `_adjacency`/`_reachable` graph-edge reachability. Those bare
    `(symbol, after_version)` edges pick one producer on an after-value collision (a revert landing
    on an earlier version's exact bytes, or an add/delete/re-add rebirth), so a graph-edge up-set can
    leave behind an op that actually lost its footing when a tip was removed -- yielding a set that
    is fork-free but no longer downward-closed, which `Ideal.from_ops` then rejects. Grounding reasons
    about which `(symbol, version)` pairs survive rather than following a single mis-resolved edge, so
    the result is downward-closed and fork-free (removing ops never creates a new `(symbol, before)`
    collision) -- a valid ideal by construction. One O(ops) grounding pass, same order as before."""
    tips = {tip for _sym, tip_a, tip_b in forks(ops, ideal_ids) for tip in (tip_a, tip_b)}
    if not tips:
        return frozenset(ideal_ids)
    return _grounded(set(ideal_ids) - tips, ops, declared)


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
    means (divergence, historical or concurrent, is state, never a crash; U20/C4).

    Memoized (U8): the ~28s-per-call cost on a large store is the dominant `sgt status` bottleneck,
    and `status_view` invokes this reduction two-to-three times per call on the same op set. The
    memo is keyed by the *present-op* id set (see `_REDUCE_CACHE`).

    Ids in `ideal_ids` with no op in `ops` are dropped up front, before the memo key is built.
    `_grounded` already excludes them (its by-id map only holds present ops), so this changes no
    result -- but it makes the memo key sound: the cache's "id set fixes the answer" invariant
    holds only when every keyed id is materialized. A `git switch` can swap the committed `.sgt/ops`
    store while a gitignored ideal table survives, referencing ops the new ref doesn't have; without
    this filter the same `ideal_ids` maps to a result computed against the *old* universe, which then
    fails `Ideal.from_ops` against the new one."""
    present = {op.id for op in ops}
    key = (frozenset(ideal_ids) & present, declared)
    cached = _REDUCE_CACHE.get(key)
    if cached is not None:
        _REDUCE_CACHE.move_to_end(key)
        return cached
    result = fork_free(_grounded(key[0], ops, declared), ops, declared)
    _REDUCE_CACHE[key] = result
    if len(_REDUCE_CACHE) > _REDUCE_CACHE_MAX:
        _REDUCE_CACHE.popitem(last=False)
    return result


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
    heads (`before_version` None) by actual production. An op joins once every one of its
    before_versions is None or already produced by a grounded op, every `requires` version is
    produced by a grounded op, and every declared predecessor is grounded.

    Grounding -- not mere existence of *some* producer -- is the correct downward-closure. The
    existential check alone accepts an originless cycle (e.g. `{modify, revert}` lifted out of
    add->modify->revert: modify's `before` is produced by revert and revert's by modify, but
    neither bottoms out at the add), which is not a real ideal -- it has no frontier head to fold.
    Reasoning about *versions* produced (never picking a single canonical producer op) keeps it
    immune to the after-value collision that the graph-edge form mis-resolves: two ops (e.g. an
    add and a later revert) can legitimately produce the same `(symbol, version)` pair, so a
    prerequisite on that pair is satisfied the moment *any one* of its producers grounds -- an
    OR-group, not a fixed single edge.

    Computed as a single Kahn's-algorithm topological pass, O(ops + edges): each distinct
    `(symbol, version)` or declared-predecessor requirement is a *key* shared by every op that
    needs it, resolved the instant any op in its producer group grounds; an op's indegree is its
    count of distinct unresolved keys. A key with no producer, or a declared predecessor outside
    `ideal_ids`, simply never resolves -- so ops that depend on it (directly or via a cycle) are
    correctly left ungrounded, same as the fixpoint this replaces. Previously a `while changed:`
    loop rescanning every remaining op each pass -- O(ops^2) on a long, mostly-linear chain."""
    ideal_set = set(ideal_ids)
    by_id = {op.id: op for op in ops if op.id in ideal_set}
    declared_preds: dict[str, set[str]] = {}
    for a, b in declared:
        if b in by_id:
            declared_preds.setdefault(b, set()).add(a)

    # producer_of[key] = every in-ideal op producing that (symbol, version) pair as its
    # footprint `after` -- a group, not a single op, per the after-value-collision note above.
    producer_of: dict[tuple, set[str]] = {}
    for op_id, op in by_id.items():
        for sym, (_before, after) in op.footprint.items():
            producer_of.setdefault(("v", sym, after), set()).add(op_id)

    def group_of(key: tuple) -> set[str]:
        if key[0] == "d":
            return {key[1]} if key[1] in by_id else set()
        return producer_of.get(key, set())

    needed: dict[str, set[tuple]] = {}
    for op_id, op in by_id.items():
        keys = {("v", sym, before) for sym, (before, _after) in op.footprint.items() if before is not None}
        keys.update(("v", sym, ver) for sym, ver in op.requires)
        keys.update(("d", p) for p in declared_preds.get(op_id, ()))
        needed[op_id] = keys

    key_dependents: dict[tuple, set[str]] = {}
    for op_id, keys in needed.items():
        for key in keys:
            key_dependents.setdefault(key, set()).add(op_id)

    producer_dependents: dict[str, set[tuple]] = {}
    for key in key_dependents:
        for member in group_of(key):
            producer_dependents.setdefault(member, set()).add(key)

    indegree = {op_id: len(keys) for op_id, keys in needed.items()}
    resolved_keys: set[tuple] = set()
    grounded: list[str] = [op_id for op_id, deg in indegree.items() if deg == 0]
    for op_id in grounded:
        for key in producer_dependents.get(op_id, ()):
            if key in resolved_keys:
                continue
            resolved_keys.add(key)
            for dep in key_dependents.get(key, ()):
                indegree[dep] -= 1
                if indegree[dep] == 0:
                    grounded.append(dep)
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
    return downset_in_many({target}, ideal_ids, ops, declared)


def downset_in_many(targets, ideal_ids, ops: list[Op], declared: Declared = frozenset()) -> frozenset[str]:
    """The set generalization of `downset_in`: everything any of `targets` builds on, within
    `ideal_ids`. Reachability closures distribute over union, so this equals
    `∪ downset_in(t)` exactly -- but pays the by-id / chain / producer index construction once
    for the whole set instead of once per target (which made a feature-sized restore preview
    O(|X|·|ops|))."""
    by_id = {op.id: op for op in ops}
    chains = _ordered_chains(ideal_ids, ops)
    pos = {sym: {oid: i for i, oid in enumerate(seq)} for sym, seq in chains.items()}
    producers: dict[tuple[str, str], set[str]] = {}
    for oid in set(ideal_ids):
        for sym, (_before, after) in by_id[oid].footprint.items():
            producers.setdefault((sym, after), set()).add(oid)

    result: set[str] = set()
    stack = list(targets)
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
