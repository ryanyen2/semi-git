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

import difflib
import fnmatch
from collections import Counter, deque
from dataclasses import dataclass
from pathlib import Path

from sgt.core import lens as kernel_lens
from sgt.core import order
from sgt.core import verbs as core_verbs
from sgt.core.op import _symbol_kind, is_bottom
from sgt.core.store import Store
from sgt.lens import tree
from sgt.lens.authored import load_authored
from sgt.lens.verbs import resolve_feature

# Below this fuzzy ratio an NL phrase is treated as "no match" rather than a weak guess.
_NL_CUTOFF = 0.5


@dataclass(frozen=True)
class PulledGroup:
    feature_id: str | None  # None means "no feature attribution" (op_leaf has no entry)
    op_count: int
    chain: tuple[dict, ...]  # one representative requires/chain path, root -> the pulled op


@dataclass(frozen=True)
class SelectionResult:
    ok: bool
    message: str = ""
    label: str = ""  # resolved display label (the spec / feature label / matched symbol)
    feature_ids: tuple[str, ...] = ()
    files: tuple[str, ...] = ()
    direct_ops: frozenset[str] = frozenset()  # the resolved direct op set (verbs consume this)
    closure: frozenset[str] = frozenset()  # the full induced closure
    direct_op_count: int = 0
    closure_op_count: int = 0
    pulled: tuple[PulledGroup, ...] = ()
    hub: dict | None = None  # {"symbol", "pulled_op_count"}
    candidates: tuple[dict, ...] = ()  # ranked {"id","label","score"} on an ambiguous NL phrase


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


def _closure_result(
    repo: str | Path, direct_ops, feature_ids: tuple[str, ...], label: str, *, empty_message: str,
) -> SelectionResult:
    """The shared, report-only closure body behind both `select` and `resolve`: given a set of
    directly-selected op-ids (pre-ideal-filter), the clustering `feature_ids` used only for the
    hub diagnosis, and a display `label`, compute the induced closure (`order.downset_in`, chain +
    reference edges only -- never clustering co-membership), the touched files, the ops the closure
    additionally pulled in grouped by their own feature (each with one representative path), and the
    hub symbol when a pull crosses a feature boundary. Materializes nothing (U25 BET-C gate)."""
    repo = Path(repo)
    ops = Store(repo).all_ops()
    ops_by_id = {op.id: op for op in ops}
    ideal = kernel_lens.current_ideal(repo)
    declared = kernel_lens._load_declared(repo)

    direct_ops = frozenset(direct_ops) & ideal.op_ids
    if not direct_ops:
        return SelectionResult(ok=True, label=label, feature_ids=feature_ids, message=empty_message)

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
        ok=True, label=label, feature_ids=feature_ids, files=tuple(files),
        direct_ops=direct_ops, closure=closure,
        direct_op_count=len(direct_ops), closure_op_count=len(closure),
        pulled=tuple(pulled), hub=hub,
    )


def select(repo: str | Path, feature_refs) -> SelectionResult:
    """The closure induced by selecting `feature_refs` (plurality-voted `op_leaf` membership,
    plan U13): direct ops, the ops the closure additionally pulls in grouped by their own feature
    (each group with one representative requires/chain path), and -- when the pull crosses a
    feature boundary -- the hub symbol responsible."""
    repo = Path(repo)
    direct_ops, feature_ids, error = _resolve_selection(repo, feature_refs)
    if error:
        return SelectionResult(ok=False, message=error)
    return _closure_result(
        repo, direct_ops, feature_ids, ", ".join(feature_ids),
        empty_message="no op in the current ideal is attributed to this selection",
    )


def _authored_result(repo: str | Path, feat) -> SelectionResult:
    """The closure for an authored feature: its live members' frontier tips, under its own label."""
    return _closure_result(repo, _symbols_to_tips(repo, feat.live_members()), (), feat.label,
                           empty_message=f"feature {feat.label!r} has no live op in the ideal")


def resolve(repo: str | Path, spec: str) -> SelectionResult:
    """The one universal selection resolver (plan U1/R1/KTD1): turn any `sgt select <spec>` form
    into the same op/symbol closure `select` reports plus a resolved display label -- the argument
    type every operating verb and the TUI consume. Dispatch is by spec *shape*, first match wins:

      1. explicit id set   -- a comma-separated list of op-ids / `file::symbol`s (unambiguous syntax)
      2. glob              -- contains `*`, `?`, or `[`; matched against live symbol names → tips
      3. exact symbol      -- contains `::`; the symbol's frontier tip op (`resolve_target` parity)
      4. authored feature  -- an `af-` id, or an exact authored-feature label (`load_authored`, U6)
      5. clustered feature -- a clustering leaf id / label, via `resolve_feature`
      6. NL phrase         -- a fuzzy `difflib` match over authored-feature label + id (the fallback;
                              no NL/embedding index exists today, so this is a new fuzzy match, not a
                              reuse). An unmatched phrase returns `ok=False`; an ambiguous one returns
                              the ranked `candidates`. Both are results, never exceptions.

    Report-only: it computes the closure but materializes nothing (the U25 BET-C constraint)."""
    repo = Path(repo)
    spec = spec.strip()

    if "," in spec:
        return _resolve_id_set(repo, spec)
    if any(ch in spec for ch in "*?["):
        return _resolve_glob(repo, spec)
    if "::" in spec:
        return _resolve_symbol(repo, spec)

    authored = load_authored(repo)
    if spec in authored:  # an `af-` id
        feat = authored[spec]
        return _authored_result(repo, feat)
    by_label = [f for f in authored.values() if f.label == spec]
    if len(by_label) == 1:
        feat = by_label[0]
        return _authored_result(repo, feat)
    if len(by_label) > 1:
        return _ambiguous([(1.0, f.id, f.label) for f in by_label], spec)

    resolved = resolve_feature(repo, spec)
    if resolved is not None:
        op_ids, feature_id, label = resolved
        return _closure_result(repo, op_ids, (feature_id,), label,
                               empty_message=f"feature {label!r} has no op in the current ideal")

    return _resolve_nl(repo, spec, authored)


def _resolve_id_set(repo: Path, spec: str) -> SelectionResult:
    """A comma-separated explicit set: each element resolved to a single op via `resolve_target`
    (op-id / prefix / `file::symbol`), unioned. Refuses on the first unresolvable element."""
    ops = Store(repo).all_ops()
    ideal = kernel_lens.current_ideal(repo)
    direct: set[str] = set()
    for part in (p.strip() for p in spec.split(",")):
        if not part:
            continue
        op_id, err = core_verbs.resolve_target(ideal, ops, part)
        if op_id is None:
            return SelectionResult(ok=False, message=err, label=spec)
        direct.add(op_id)
    if not direct:
        return SelectionResult(ok=False, message="empty selection", label=spec)
    return _closure_result(repo, frozenset(direct), (), spec, empty_message="empty selection")


def _live_tips(repo: Path) -> dict[str, str]:
    """Frontier tips restricted to the *live, user-facing* symbols a glob/authored-member selection
    resolves against: the tip must not be `BOTTOM` (mirroring `covered_paths`' liveness rule) and
    the symbol must be one a user names -- `__residue__`/`__anchor__` are internal fold-ordering
    pseudo-symbols and are never selectable, so a glob like `*::pay_*` matches the real entity, not
    its residue twin."""
    ops = Store(repo).all_ops()
    ops_by_id = {op.id: op for op in ops}
    tips = order.frontier(kernel_lens.current_ideal(repo).op_ids, ops)
    return {
        sym: tip for sym, tip in tips.items()
        if _symbol_kind(sym) not in ("residue", "anchor")
        and not is_bottom(ops_by_id[tip].footprint[sym][1])
    }


def _resolve_glob(repo: Path, spec: str) -> SelectionResult:
    tips = _live_tips(repo)
    matched = sorted(sym for sym in tips if fnmatch.fnmatch(sym, spec))
    if not matched:
        return SelectionResult(ok=False, message=f"glob {spec!r} matched no live symbol", label=spec)
    return _closure_result(repo, frozenset(tips[sym] for sym in matched), (), spec,
                           empty_message=f"glob {spec!r} matched no op in the current ideal")


def _resolve_symbol(repo: Path, spec: str) -> SelectionResult:
    ops = Store(repo).all_ops()
    tip = order.frontier(kernel_lens.current_ideal(repo).op_ids, ops).get(spec)
    if tip is None:
        return SelectionResult(ok=False, message=f"symbol {spec!r} is not live in the ideal", label=spec)
    return _closure_result(repo, frozenset({tip}), (), spec,
                           empty_message=f"symbol {spec!r} is not live in the ideal")


def _symbols_to_tips(repo: Path, symbols) -> frozenset[str]:
    """The frontier tip op of each live symbol in `symbols` (dead / absent symbols drop out; the
    closure body's `& ideal.op_ids` guard reports the empty case)."""
    tips = _live_tips(repo)
    return frozenset(tips[sym] for sym in symbols if sym in tips)


def _resolve_nl(repo: Path, spec: str, authored: dict) -> SelectionResult:
    """Fuzzy fallback over authored-feature label + id (stdlib `difflib`, no new dependency). A
    unique best above the cutoff resolves; a tie at the top returns ranked candidates; nothing above
    the cutoff returns `ok=False` -- never a silent weak guess."""
    key = spec.lower()
    scored = sorted(
        (
            (max(difflib.SequenceMatcher(None, key, f.label.lower()).ratio(),
                 difflib.SequenceMatcher(None, key, fid.lower()).ratio()), fid, f.label)
            for fid, f in authored.items()
        ),
        key=lambda c: (-c[0], c[1]),
    )
    above = [c for c in scored if c[0] >= _NL_CUTOFF]
    if not above:
        return SelectionResult(ok=False, message=f"no feature matches {spec!r}", label=spec)
    if len(above) > 1 and above[0][0] == above[1][0]:
        return _ambiguous(above, spec)
    feat = authored[above[0][1]]
    return _authored_result(repo, feat)


def _ambiguous(scored, spec: str) -> SelectionResult:
    """An ambiguous phrase/label: `ok=False` carrying the ranked candidates so a caller can
    disambiguate, rather than picking one silently."""
    candidates = tuple(
        {"id": fid, "label": label, "score": round(score, 3)} for score, fid, label in scored
    )
    return SelectionResult(ok=False, label=spec, candidates=candidates,
                           message=f"{spec!r} is ambiguous ({len(candidates)} candidates)")


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
        member_leaf = tree.leaf_member_index(nodes)
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
