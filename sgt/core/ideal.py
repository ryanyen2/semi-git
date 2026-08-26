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
from sgt.core.op import Op, is_bottom, is_content_bearing


@dataclass(frozen=True)
class Ideal:
    op_ids: frozenset[str]

    @staticmethod
    def from_ops(op_ids, ops: list[Op], declared: order.Declared = frozenset()) -> Ideal:
        ids = frozenset(op_ids)
        if not order.is_valid_ideal(ops, ids, declared):
            # Bounded on purpose. This message is not developer-only: it travels up through
            # `_plan_removal`'s refusal into `--emit`'s `message`, which the TUI and the VS Code
            # workbench render verbatim to a person. Dumping every id in the ideal produced a wall
            # of hex a reader could neither read nor act on, and buried the one sentence that
            # mattered. The count is the diagnostic; a handful of ids keeps it debuggable.
            shown = sorted(ids)
            head = ", ".join(shown[:4]) + (f", +{len(shown) - 4} more" if len(shown) > 4 else "")
            raise ValueError(
                "not a valid ideal (downward-closure or fork-freedom violated): "
                f"{len(shown)} op(s) [{head}]"
            )
        return Ideal(op_ids=ids)

    def frontier(self, ops: list[Op]) -> dict[str, str]:
        """Per-symbol frontier: symbol -> id of its maximal in-ideal op."""
        return order.frontier(self.op_ids, ops)

    def covered_paths(self, ops: list[Op]) -> frozenset[str]:
        """Every path with a *live*, content-bearing symbol at this ideal's frontier -- R7's
        coverage law. A path whose only in-ideal symbol tip is a removal (`BOTTOM`) is not
        covered: the path doesn't exist in `code(I)`. Neither is a path kept alive only by an
        `anchor` pseudo-symbol (pure ordering metadata mining never revises to BOTTOM) after its
        entity and residue were pruned -- an anchor produces no bytes, so `covered_paths` and
        `code` agree exactly on which paths materialize (a fully-removed file can't resurrect as
        an empty `b''`)."""
        by_id = {op.id: op for op in ops}
        paths: set[str] = set()
        for sym, op_id in self.frontier(ops).items():
            after = by_id[op_id].footprint[sym][1]
            if not is_bottom(after) and is_content_bearing(sym):
                paths.add(sym.split("::", 1)[0])
        return frozenset(paths)

    def diff(self, other: Ideal) -> frozenset[str]:
        """Symmetric difference of op ids -- semantic diff for free (ADR S3.4)."""
        return self.op_ids ^ other.op_ids
