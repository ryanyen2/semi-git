"""The order over ops: an op stands on the op that produced each version it starts from (its
footprint's `before`) and each version it `requires`.

A ref's ideal is the ops mined from its history, grounded: every op's prerequisites are produced
by ops in the set. Two ops can start from one version where two lines of history changed a symbol,
and the merge that joined those lines joins both into one version again (`ugh.core.mine`),
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
    """The largest well-founded downward-closed subset of `ideal_ids`: an op joins once each of its
    `before` versions is None or produced by a grounded op, and so is each version it `requires`.
    Requiring production, not mere existence of a producer, rejects cycles that never reach a
    chain head. A `(symbol, version)` key is satisfied by any one of the ops producing it. One
    Kahn pass, O(ops + edges)."""
    ideal_set = set(ideal_ids)
    by_id = {op.id: op for op in ops if op.id in ideal_set}

    # several ops can produce one (symbol, version), so each key has a group of producers
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
    """`_ordered_chains`, memoised on the id set, which fixes the footprints it reads. The lists
    are shared: callers must not mutate them."""
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
    """Symbol -> the id of the last in-ideal op on its chain, the tip `code(I)` splices from. A
    symbol whose tip removed it (`op.BOTTOM`) is still listed; callers check liveness. Memoized
    on the id set, so index ops and full ops share an entry."""
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
