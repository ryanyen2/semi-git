"""A codebase state is an order ideal (ADR S3.4; plan R3): a downward-closed set of ops in
which every symbol's chain restricted to the set has a unique maximal element. `Ideal` is the
validated, immutable wrapper around such a set -- the only way to construct one is
`Ideal.from_ops`, which refuses (raises) anything `sgt.core.order.is_valid_ideal` rejects, so an
ill-formed state is unconstructible through this API rather than merely discouraged.

The explicit `op_ids` set is this kernel's canonical in-memory representation -- it makes
membership, union, and symmetric difference (`diff`, U4's semantic-diff-for-free) trivial and
obviously correct. `frontier`/`covered_paths` derive the ADR's compact per-chain frontier view
on demand; U5's fold and U6/U9's on-disk ref->ideal persistence are the places that should
actually store the frontier form rather than a full op-id set.
"""

from __future__ import annotations

from dataclasses import dataclass

from sgt.core import order
from sgt.core.op import BOTTOM, Op


@dataclass(frozen=True)
class Ideal:
    op_ids: frozenset[str]

    @staticmethod
    def from_ops(op_ids, ops: list[Op], declared: order.Declared = frozenset()) -> Ideal:
        ids = frozenset(op_ids)
        if not order.is_valid_ideal(ops, ids, declared):
            raise ValueError(f"not a valid ideal (downward-closure or fork-freedom violated): {sorted(ids)}")
        return Ideal(op_ids=ids)

    def frontier(self, ops: list[Op]) -> dict[str, str]:
        """Per-symbol frontier: symbol -> id of its maximal in-ideal op."""
        return order.frontier(self.op_ids, ops)

    def covered_paths(self, ops: list[Op]) -> frozenset[str]:
        """Every path with a *live* (non-removed) symbol at this ideal's frontier -- R7's
        coverage law. A path whose only in-ideal symbol tip is a removal (`BOTTOM`) is not
        covered: the path doesn't exist in `code(I)`."""
        by_id = {op.id: op for op in ops}
        paths: set[str] = set()
        for sym, op_id in self.frontier(ops).items():
            after = by_id[op_id].footprint[sym][1]
            if after != BOTTOM:
                paths.add(sym.split("::", 1)[0])
        return frozenset(paths)

    def diff(self, other: Ideal) -> frozenset[str]:
        """Symmetric difference of op ids -- semantic diff for free (ADR S3.4)."""
        return self.op_ids ^ other.op_ids
