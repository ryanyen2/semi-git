"""U29: closure-explanation UX (`sgt select` / `sgt why`).

The design doc (docs/design/2026-07-10-sgt-as-version-control.md S2) originally specified `select`
as branch materialization from a feature-tree selection via requires-closure. The U25 BET-C
measurement gate came back RED (median closure 34 ops, over the 25-op threshold; only 46% of
feature nodes within bounds -- see docs/plans/2026-07-10-002-feat-product-surface-plan.md's U29
GATE RESULT), so this ships explanation-only: `select` reports a selection's induced closure
without materializing anything, and `why` traces the exact chain that pulled one op into it.

Closure follows chain + reference (`requires`) edges only, never feature-clustering co-membership
-- the design doc's distinction between true semantic coupling (an honest reason to include
something) and incidental coupling (shared feature only via clustering, which must never force
inclusion). The closure *set* itself is computed with `order.downset_in`, the same collision-safe,
ideal-relative primitive the ideal-edit verbs use; only the explanatory path (which edge, in which
order, pulled a given op in) is this module's own BFS, scoped to the already-computed closure.
"""

from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass
from pathlib import Path

from sgt.core import lens as kernel_lens
from sgt.core import order
from sgt.core import verbs as core_verbs
from sgt.core.store import Store
from sgt.lens import tree
from sgt.lens.verbs import resolve_feature


@dataclass(frozen=True)
class PulledGroup:
    feature_id: str | None  # None means "no feature attribution" (op_leaf has no entry)
    op_count: int
    chain: tuple[dict, ...]  # one representative requires/chain path, root -> the pulled op


@dataclass(frozen=True)
class SelectionResult:
    ok: bool
    message: str = ""
    feature_ids: tuple[str, ...] = ()
    files: tuple[str, ...] = ()
    direct_op_count: int = 0
    closure_op_count: int = 0
    pulled: tuple[PulledGroup, ...] = ()
    hub: dict | None = None  # {"symbol", "pulled_op_count"}


@dataclass(frozen=True)
class WhyResult:
    ok: bool
    message: str = ""
    op_id: str = ""
    feature_id: str | None = None
    votes: tuple[dict, ...] = ()
    for_feature: str | None = None
    chain: tuple[dict, ...] = ()


def _resolve_selection(repo: str | Path, refs) -> tuple[frozenset[str], tuple[str, ...], str]:
    """Each `ref` resolved via `resolve_feature`, unioned into one op-set. Returns
    `(direct_ops, feature_ids, error)`; `error` is set (and the other fields empty) on the first
    unresolvable ref."""
    direct: set[str] = set()
    feature_ids: list[str] = []
    for ref in refs:
        resolved = resolve_feature(repo, ref)
        if resolved is None:
            return frozenset(), (), f"feature {ref!r} not found; run `sgt map`"
        op_ids, feature_id, _label = resolved
        direct |= op_ids
        feature_ids.append(feature_id)
    return frozenset(direct), tuple(feature_ids), ""


def _closure_edges(ops_by_id: dict, closure: frozenset[str], declared) -> dict[str, list[tuple[str, str]]]:
    """Predecessor adjacency (`b -> [(a, kind), ...]`) over exactly `closure`, from chain +
    reference + declared edges -- the same three sources `order.downset_in` closes over, restricted
    up front to `closure` rather than filtered after, so an edge naming an op outside it never
    appears."""
    closure_ops = [ops_by_id[oid] for oid in closure]
    preds: dict[str, list[tuple[str, str]]] = {}
    for a, b in order.chain_edges(closure_ops):
        preds.setdefault(b, []).append((a, "chain"))
    for a, b in order.reference_edges(closure_ops):
        preds.setdefault(b, []).append((a, "requires"))
    for a, b in declared:
        if a in closure and b in closure:
            preds.setdefault(b, []).append((a, "declared"))
    return preds


def _trace_from_roots(direct_ops: frozenset[str], preds: dict) -> dict[str, tuple[str | None, str | None]]:
    """BFS from every directly-selected op, walking backward over `preds` (root -> ... ->
    prerequisite) -- a parent map `node -> (parent, edge_kind)`, `(None, None)` for a root itself.
    Since `preds` is scoped to the closure, this reaches every op the closure set contains."""
    parent: dict[str, tuple[str | None, str | None]] = {d: (None, None) for d in direct_ops}
    queue = deque(direct_ops)
    while queue:
        b = queue.popleft()
        for a, kind in preds.get(b, ()):
            if a not in parent:
                parent[a] = (b, kind)
                queue.append(a)
    return parent


def _path_to(parent: dict, target: str) -> tuple[dict, ...]:
    """The explanatory chain root -> ... -> `target`, root first. Empty if `target` isn't
    reachable from any root (shouldn't happen for an op the closure actually contains, since
    `preds` covers the same three edge sources `downset_in` used to admit it)."""
    if target not in parent:
        return ()
    seq: list[dict] = []
    node = target
    while True:
        p, kind = parent[node]
        seq.append({"op_id": node, "via": kind})
        if p is None:
            break
        node = p
    seq.reverse()
    return tuple(seq)


def _hub_diagnosis(ops_by_id: dict, closure: frozenset[str], feature_ids: tuple[str, ...], op_leaf: dict) -> dict | None:
    """Among requires edges crossing from a foreign-feature op into the selected feature(s), the
    most-referenced symbol -- the design doc's "names the hub" diagnosis (S2 point 5). Only
    `requires` edges count (true semantic coupling); a symbol reached solely by chain edges never
    crosses a feature boundary (it's the same symbol) and isn't a coupling culprit."""
    selected = set(feature_ids)
    producer_after: dict[tuple[str, str], str] = {}
    for oid in closure:
        for sym, (_before, after) in ops_by_id[oid].footprint.items():
            producer_after[(sym, after)] = oid

    by_symbol: dict[str, set[str]] = {}
    for oid in closure:
        if op_leaf.get(oid) not in selected:
            continue
        for req_sym, req_ver in ops_by_id[oid].requires:
            producer = producer_after.get((req_sym, req_ver))
            if producer is None or producer not in closure:
                continue
            producer_feature = op_leaf.get(producer)
            if producer_feature is not None and producer_feature not in selected:
                by_symbol.setdefault(req_sym, set()).add(producer)

    if not by_symbol:
        return None
    symbol, producers = max(by_symbol.items(), key=lambda kv: (len(kv[1]), kv[0]))
    return {"symbol": symbol, "pulled_op_count": len(producers)}


def select(repo: str | Path, feature_refs) -> SelectionResult:
    """The closure induced by selecting `feature_refs` (plurality-voted `op_leaf` membership,
    plan U13): direct ops, the ops the closure additionally pulls in grouped by their own feature
    (each group with one representative requires/chain path), and -- when the pull crosses a
    feature boundary -- the hub symbol responsible."""
    repo = Path(repo)
    direct_ops, feature_ids, error = _resolve_selection(repo, feature_refs)
    if error:
        return SelectionResult(ok=False, message=error)

    ops = Store(repo).all_ops()
    ops_by_id = {op.id: op for op in ops}
    ideal = kernel_lens.current_ideal(repo)
    declared = kernel_lens._load_declared(repo)

    direct_ops = frozenset(direct_ops & ideal.op_ids)
    if not direct_ops:
        return SelectionResult(
            ok=True, feature_ids=feature_ids,
            message="no op in the current ideal is attributed to this selection",
        )

    closure: set[str] = set()
    for op_id in direct_ops:
        closure |= order.downset_in(op_id, ideal.op_ids, ops, declared)
    closure = frozenset(closure)

    tree_result = tree.load(repo)
    op_leaf = tree_result["op_leaf"] if tree_result else {}

    files = sorted({sym.split("::", 1)[0] for oid in closure for sym in ops_by_id[oid].footprint})

    pulled_in = closure - direct_ops
    groups: dict[str | None, set[str]] = {}
    for oid in pulled_in:
        groups.setdefault(op_leaf.get(oid), set()).add(oid)

    preds = _closure_edges(ops_by_id, closure, declared)
    parent = _trace_from_roots(direct_ops, preds)

    pulled: list[PulledGroup] = []
    for group_feature, members in groups.items():
        best_path: tuple[dict, ...] = ()
        for oid in sorted(members):
            path = _path_to(parent, oid)
            if path and (not best_path or len(path) < len(best_path)):
                best_path = path
        pulled.append(PulledGroup(feature_id=group_feature, op_count=len(members), chain=best_path))
    pulled.sort(key=lambda g: (-g.op_count, g.feature_id or ""))

    hub = _hub_diagnosis(ops_by_id, closure, feature_ids, op_leaf)

    return SelectionResult(
        ok=True, feature_ids=feature_ids, files=tuple(files),
        direct_op_count=len(direct_ops), closure_op_count=len(closure),
        pulled=tuple(pulled), hub=hub,
    )


def why(repo: str | Path, op_ref: str, for_feature: str | None = None) -> WhyResult:
    """Explain one op's feature attribution: with no `for_feature`, the plurality vote
    (`assign_ops_to_leaves`) that assigned it -- every leaf its footprint touched, and how many
    symbols voted for each. With `for_feature`, instead trace why it's part of *that* feature's
    selection closure (the same chain `select` would report for its group) -- refusing if the op
    isn't actually in that closure rather than guessing."""
    repo = Path(repo)
    ops = Store(repo).all_ops()
    ops_by_id = {op.id: op for op in ops}
    ideal = kernel_lens.current_ideal(repo)

    op_id, error = core_verbs.resolve_target(ideal, ops, op_ref)
    if op_id is None:
        return WhyResult(ok=False, message=error)

    tree_result = tree.load(repo)
    if tree_result is None:
        return WhyResult(ok=False, message="no feature tree; run `sgt map`", op_id=op_id)
    nodes, op_leaf = tree_result["nodes"], tree_result["op_leaf"]
    own_feature = op_leaf.get(op_id)

    if for_feature is None:
        member_leaf = tree._leaf_member_index(nodes)
        op = ops_by_id[op_id]
        votes = Counter(member_leaf[sym] for sym in op.footprint if sym in member_leaf)
        vote_list = tuple(
            {"feature_id": leaf, "count": count}
            for leaf, count in sorted(votes.items(), key=lambda kv: (-kv[1], kv[0]))
        )
        return WhyResult(ok=True, op_id=op_id, feature_id=own_feature, votes=vote_list)

    direct_ops, feature_ids, error = _resolve_selection(repo, [for_feature])
    if error:
        return WhyResult(ok=False, message=error, op_id=op_id, feature_id=own_feature)

    declared = kernel_lens._load_declared(repo)
    direct_ops = frozenset(direct_ops & ideal.op_ids)
    if op_id in direct_ops:
        return WhyResult(
            ok=True, op_id=op_id, feature_id=own_feature, for_feature=feature_ids[0],
            chain=({"op_id": op_id, "via": None},),
        )

    closure: set[str] = set()
    for oid in direct_ops:
        closure |= order.downset_in(oid, ideal.op_ids, ops, declared)
    closure = frozenset(closure)
    if op_id not in closure:
        return WhyResult(
            ok=False, message=f"{op_id} is not part of {feature_ids[0]}'s selection closure",
            op_id=op_id, feature_id=own_feature, for_feature=feature_ids[0],
        )

    preds = _closure_edges(ops_by_id, closure, declared)
    parent = _trace_from_roots(direct_ops, preds)
    chain = _path_to(parent, op_id)
    return WhyResult(ok=True, op_id=op_id, feature_id=own_feature, for_feature=feature_ids[0], chain=chain)
