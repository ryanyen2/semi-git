"""BET-C (plan U12 / R22): MoJoFM of the feature tree's leaf partition against a hand-labeled
gold partition of this repo's own top-level packages. A single documented dogfood measurement,
not a benchmark harness -- mirrors how BET-A recorded one threshold in FINDINGS.md.

Gold partition: every alive content-bearing symbol -> its top-level package (`sgt/<pkg>`, with
`cli`+`api` merged per the plan; non-`sgt/` paths by their top-level dir). MoJo distance uses the
accepted greedy (Wen & Tzerpos 2004): each source cluster is assigned to the gold cluster it most
overlaps; `moves` = objects outside that majority; `joins` = source clusters collapsed onto a
shared gold target. MoJoFM = 1 - mno(tree, gold) / mno(singletons, gold), with the denominator the
all-singletons distance `n - |gold|` (the farthest reasonable partition) -- documented so the
number is reproducible.

Run: `uv run python scripts/bet_c_mojofm.py` (requires `sgt init .` to have mined `.sgt/ops/`).
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from sgt.core.lens import _committed_ids_by_provenance
from sgt.core.op import BOTTOM, is_content_bearing
from sgt.core.store import Store
from sgt.lens import tree
from sgt.store.gitbind import GitBinding


class _FrontierIdeal:
    """A minimal ideal whose `.frontier` tolerates the forks and gaps in sgt's own 67-commit
    history. That history is *not* a valid ideal (~440 forked symbol chains: functions deleted in
    U10 then similar names re-added -> two competing tips; plus symbols whose genesis op isn't in
    the provenance-reconstructed set), so `lens.get()` and `order.frontier` correctly refuse it.
    This measurement only needs the current codebase's live symbol set, so it takes each symbol's
    tip directly: the in-set op whose after-version no other in-set op consumes as a before
    (fork tie-break: largest op id). `cluster.signals` uses the ideal only for `alive_nodes`;
    co-change still reads the full op store, structural edges the current tree at HEAD."""

    def __init__(self, ids: set[str]):
        self._ids = frozenset(ids)

    def frontier(self, ops):
        by_id = {op.id: op for op in ops}
        sym_ops: dict[str, list[str]] = defaultdict(list)
        for oid in self._ids:
            for sym in by_id[oid].footprint:
                sym_ops[sym].append(oid)
        front: dict[str, str] = {}
        for sym, oplist in sym_ops.items():
            befores = {by_id[o].footprint[sym][0] for o in oplist}
            tips = [o for o in oplist if by_id[o].footprint[sym][1] not in befores] or oplist
            front[sym] = max(tips)  # deterministic fork resolution
        return front


def gold_package(sym: str) -> str:
    """Hand-labeled gold group for a symbol id: its top-level package. `sgt/<pkg>` (with `cli`+`api`
    merged per the plan); a repo-root file (README, pyproject, ...) is grouped as `meta`; any other
    top-level dir (docs, tests, ...) is its own group."""
    path = sym.split("::", 1)[0]
    parts = path.split("/")
    if len(parts) == 1:
        return "meta"  # a repo-root doc/config file -- one human group, not one per file
    if parts[0] == "sgt":
        pkg = parts[1] if len(parts) >= 3 else parts[1].removesuffix(".py")
        return f"sgt/{'cli_api' if pkg in ('cli', 'api') else pkg}"
    return parts[0]


def _mno(source: dict[str, str], target: dict[str, str]) -> int:
    """Minimum Move+Join operations to transform partition `source` into `target` (greedy, the
    accepted MoJo algorithm). Both map object -> cluster label."""
    by_src: dict[str, list[str]] = defaultdict(list)
    for obj, c in source.items():
        by_src[c].append(obj)

    moves = 0
    assigned: dict[str, str] = {}
    for c, objs in by_src.items():
        overlap: dict[str, int] = defaultdict(int)
        for o in objs:
            overlap[target[o]] += 1
        best = max(sorted(overlap), key=lambda t: overlap[t])
        assigned[c] = best
        moves += len(objs) - overlap[best]
    joins = len(by_src) - len(set(assigned.values()))
    return moves + joins


def mojofm(source: dict[str, str], gold: dict[str, str]) -> float:
    n = len(source)
    k = len(set(gold.values()))
    denom = n - k  # mno(all-singletons -> gold): 0 moves, n-k joins
    if denom <= 0:
        return 100.0
    return (1 - _mno(source, gold) / denom) * 100.0


def _alive(ideal, ops) -> set[str]:
    by_id = {op.id: op for op in ops}
    frontier = ideal.frontier(ops)
    return {
        sym for sym, oid in frontier.items()
        if by_id[oid].footprint[sym][1] != BOTTOM and is_content_bearing(sym)
    }


def main() -> None:
    repo = Path(".")
    store = Store(repo)
    ops = store.all_ops()
    ideal = _FrontierIdeal(_committed_ids_by_provenance(GitBinding(repo), store))
    alive = _alive(ideal, ops)

    result = tree.build(repo, ops, ideal)
    leaf_of = {m: nid for nid, nd in result["nodes"].items() if not nd["children"] for m in nd["members"]}

    source = {sym: leaf_of[sym] for sym in alive if sym in leaf_of}
    gold = {sym: gold_package(sym) for sym in source}

    # product-focused view: only sgt/ symbols, gold = sgt subpackage (the plan's "6-8 packages")
    sgt_source = {s: c for s, c in source.items() if s.startswith("sgt/")}
    sgt_gold = {s: gold[s] for s in sgt_source}

    leaves = [nd for nd in result["nodes"].values() if not nd["children"]]
    internal = [nd for nd in result["nodes"].values() if nd["children"]]
    child_counts = [len(nd["children"]) for nd in internal]

    print(f"alive content-bearing symbols: {len(alive)}")
    print(f"clustered (partitioned) symbols: {len(source)}")
    print(f"tree: {len(leaves)} leaves, {len(internal)} internal nodes, max_depth={result['max_depth']}")
    if child_counts:
        print(f"child counts per internal node: {sorted(child_counts)}")
    print()
    print(f"[all symbols]  {len(source)} syms, gold groups ({len(set(gold.values()))}): {sorted(set(gold.values()))}")
    print(f"[all symbols]  MoJoFM(tree, gold) = {mojofm(source, gold):.1f}%")
    print()
    print(f"[sgt/ only]    {len(sgt_source)} syms, gold groups ({len(set(sgt_gold.values()))}): {sorted(set(sgt_gold.values()))}")
    print(f"[sgt/ only]    MoJoFM(tree, gold) = {mojofm(sgt_source, sgt_gold):.1f}%")


if __name__ == "__main__":
    main()
