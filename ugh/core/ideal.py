"""A codebase state is an ideal: a set of ops in which every op's prerequisites are produced.
`Ideal.from_ops` is the way to build one, and it raises on a set `order.is_valid_ideal` rejects.
"""

from __future__ import annotations

from dataclasses import dataclass

from ugh.core import order
from ugh.core.op import Op, is_bottom, is_content_bearing


@dataclass(frozen=True)
class Ideal:
    """A valid ideal as a frozen set of op ids."""

    op_ids: frozenset[str]

    @staticmethod
    def from_ops(op_ids, ops: list[Op]) -> Ideal:
        """The ideal of `op_ids` over `ops`; raises `ValueError` when the set is not valid."""
        ids = frozenset(op_ids)
        if not order.is_valid_ideal(ops, ids):
            # a person reads this message, so it shows a count and a few ids, not all of them
            shown = sorted(ids)
            head = ", ".join(shown[:4]) + (f", +{len(shown) - 4} more" if len(shown) > 4 else "")
            raise ValueError(
                "not a valid ideal (an op's prerequisites are missing): "
                f"{len(shown)} op(s) [{head}]"
            )
        return Ideal(op_ids=ids)

    def frontier(self, ops: list[Op]) -> dict[str, str]:
        """Per-symbol frontier: symbol -> id of its maximal in-ideal op."""
        return order.frontier(self.op_ids, ops)

    def covered_paths(self, ops: list[Op]) -> frozenset[str]:
        """Every path with a live, content-bearing symbol at this ideal's frontier: exactly the
        paths `code(I)` materializes. A path whose tips are removals or anchors is not covered."""
        by_id = {op.id: op for op in ops}
        paths: set[str] = set()
        for sym, op_id in self.frontier(ops).items():
            after = by_id[op_id].footprint[sym][1]
            if not is_bottom(after) and is_content_bearing(sym):
                paths.add(sym.split("::", 1)[0])
        return frozenset(paths)
