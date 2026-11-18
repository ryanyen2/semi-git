"""The order over ops: an op stands on the op that produced each version it starts from (its
footprint's `before`) and each version it `requires`.

A ref's ideal is the ops mined from its history, grounded: every op's prerequisites are produced
by ops in the set. Two ops can start from one version where two lines of history changed a symbol,
and the merge that joined those lines joins both into one version again (`ugh.core.mine`, rule 4),
so every symbol in a ref's ideal has one tip. There is no other conflict.
"""

from __future__ import annotations

from collections import OrderedDict

from ugh.core.op import Op

# Memos keyed by the present-op id set: grounding reads only the ops in the set, and ops are
# content-addressed, so the ids fix the answer.
_REDUCE_CACHE: "OrderedDict[frozenset, frozenset[str]]" = OrderedDict()
_REDUCE_CACHE_MAX = 64

_VALID_CACHE: "OrderedDict[frozenset, bool]" = OrderedDict()
_VALID_CACHE_MAX = 64

# `frontier`'s memo (see its docstring): id-set -> {symbol: tip op id}. The returned dict is
# copied per call; the memoized one is never handed out.
_FRONTIER_CACHE: "OrderedDict[frozenset, dict[str, str]]" = OrderedDict()
_FRONTIER_CACHE_MAX = 32
# `ordered_chains` memo, same key and argument as `_FRONTIER_CACHE`; kept small because a chain
# map holds every in-ideal (op, symbol) pair, not one tip per symbol.
_CHAINS_CACHE: "OrderedDict[frozenset, dict[str, list[str]]]" = OrderedDict()
_CHAINS_CACHE_MAX = 4


def reduce_to_ideal(ideal_ids, ops: list[Op]) -> frozenset[str]:
    """The grounded part of a raw provenance-derived op set: the ops all of whose prerequisites
    are produced within it. Memoized on the present ids."""
    present = {op.id for op in ops}
    key = frozenset(ideal_ids) & present
    cached = _REDUCE_CACHE.get(key)
    if cached is not None:
        _REDUCE_CACHE.move_to_end(key)
        return cached
    result = _grounded(key, ops)
    _REDUCE_CACHE[key] = result
    if len(_REDUCE_CACHE) > _REDUCE_CACHE_MAX:
        _REDUCE_CACHE.popitem(last=False)
    return result


def _grounded(ideal_ids, ops: list[Op]) -> frozenset[str]:
    """The largest *well-founded* downward-closed subset of `ideal_ids`: ops reachable from chain
    heads (`before_version` None) by actual production. An op joins once every one of its
    before_versions is None or already produced by a grounded op, and every `requires` version is
    produced by a grounded op.

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
    `(symbol, version)` requirement is a *key* shared by every op that needs it, resolved the
    instant any op in its producer group grounds; an op's indegree is its count of distinct
    unresolved keys. A key with no producer simply never resolves -- so ops that depend on it
    (directly or via a cycle) are correctly left ungrounded, same as the fixpoint this replaces. Previously a `while changed:`
    loop rescanning every remaining op each pass -- O(ops^2) on a long, mostly-linear chain."""
    ideal_set = set(ideal_ids)
    by_id = {op.id: op for op in ops if op.id in ideal_set}

    # producer_of[key] = every in-ideal op producing that (symbol, version) pair as its
    # footprint `after` -- a group, not a single op, per the after-value-collision note above.
    producer_of: dict[tuple, set[str]] = {}
    for op_id, op in by_id.items():
        for sym, (_before, after) in op.footprint.items():
            producer_of.setdefault((sym, after), set()).add(op_id)

    needed: dict[str, set[tuple]] = {}
    for op_id, op in by_id.items():
        keys = {(sym, before) for sym, (before, _after) in op.footprint.items()
                if before is not None}
        keys.update((sym, ver) for sym, ver in op.requires)
        needed[op_id] = keys

    key_dependents: dict[tuple, set[str]] = {}
    for op_id, keys in needed.items():
        for key in keys:
            key_dependents.setdefault(key, set()).add(op_id)

    producer_dependents: dict[str, set[tuple]] = {}
    for key in key_dependents:
        for member in producer_of.get(key, ()):
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


def is_valid_ideal(ops: list[Op], ideal_ids) -> bool:
    """Every op in `ideal_ids` exists in `ops` and is grounded (`_grounded`)."""
    ids = frozenset(ideal_ids)
    if not ids <= {op.id for op in ops}:
        return False
    cached = _VALID_CACHE.get(ids)
    if cached is not None:
        _VALID_CACHE.move_to_end(ids)
        return cached
    result = _grounded(ids, ops) == ids
    _VALID_CACHE[ids] = result
    if len(_VALID_CACHE) > _VALID_CACHE_MAX:
        _VALID_CACHE.popitem(last=False)
    return result


def _ordered_chains(ideal_ids, ops: list[Op]) -> dict[str, list[str]]:
    """Per symbol, the in-ideal op ids in chain order (head -> tip): the walk from the op whose
    before is None (or, in a store slice that starts mid-chain, the earliest version no op in the
    slice produces) along after -> before. Where two lines of history changed the symbol, the walk
    takes one of them; the merge joined both into one version, so either reaches the same tip."""
    by_id = {op.id: op for op in ops}
    starts: dict[str, dict[str | None, str]] = {}
    for op_id in sorted(set(ideal_ids)):
        for sym, (before, _after) in by_id[op_id].footprint.items():
            starts.setdefault(sym, {}).setdefault(before, op_id)
    chains: dict[str, list[str]] = {}
    for sym, steps in starts.items():
        head = None
        if None not in steps:
            produced = {by_id[o].footprint[sym][1] for o in steps.values()}
            roots = sorted(b for b in steps if b not in produced)
            if not roots:
                continue
            head = roots[0]
        seq: list[str] = []
        version = head
        while version in steps and steps[version] not in seq:
            seq.append(steps[version])
            version = by_id[steps[version]].footprint[sym][1]
        chains[sym] = seq
    return chains


def ordered_chains(ideal_ids, ops: list[Op]) -> dict[str, list[str]]:
    """`_ordered_chains`, memoised on the id set exactly as `frontier` is (chains read only the
    in-ideal ops' footprints, which their ids fix). `frontier` and the planner share one walk per
    ideal. The lists are shared: callers
    must not mutate them."""
    key = frozenset(ideal_ids)
    hit = _CHAINS_CACHE.get(key)
    if hit is not None:
        _CHAINS_CACHE.move_to_end(key)
        return hit
    result = _ordered_chains(key, ops)
    _CHAINS_CACHE[key] = result
    if len(_CHAINS_CACHE) > _CHAINS_CACHE_MAX:
        _CHAINS_CACHE.popitem(last=False)
    return result


def frontier(ideal_ids, ops: list[Op]) -> dict[str, str]:
    """Per-symbol frontier (the ADR's per-chain frontier vector): for each symbol touched
    anywhere in the ideal, the id of the *maximal* in-ideal op for that symbol's chain -- the
    tip `code(I)` (U5) splices from. A symbol whose tip's `after_version` is `op.BOTTOM` is
    present in this map but no longer alive; callers that want only live symbols must check
    that themselves (this module doesn't import mine.py's BOTTOM-producing call sites, only the
    sentinel value lives in `ugh.core.op`).

    The tip is the last of each symbol's ordered chain (`_ordered_chains`).

    Memoized on the id set alone (same content-addressing argument as `_REDUCE_CACHE`): chains
    read only in-ideal ops' *footprints*, identical across every ops-list variant that carries
    the id -- footprint-only index ops and full image-bearing ops agree. One command computes
    the same ideal's frontier through several views; each distinct id set pays once."""
    key = frozenset(ideal_ids)
    hit = _FRONTIER_CACHE.get(key)
    if hit is not None:
        _FRONTIER_CACHE.move_to_end(key)
        return dict(hit)
    result = {sym: seq[-1] for sym, seq in ordered_chains(key, ops).items()}
    _FRONTIER_CACHE[key] = result
    if len(_FRONTIER_CACHE) > _FRONTIER_CACHE_MAX:
        _FRONTIER_CACHE.popitem(last=False)
    return dict(result)
