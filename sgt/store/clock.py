"""Version vectors — the causality primitive.

A ``VersionVector`` maps ``replica_id -> counter``: how many effects this replica has
*observed* from each peer. It tells concurrent edits apart from sequential ones, which is
the whole basis of a sound merge: causally-ordered effects just replay in order, while
concurrent ones must go through the confluence gate.

Total order (the SEC backbone): ``sum(counts)`` is a **linear extension of causal
happens-before** — if ``b`` descends from ``a`` then every entry of ``b`` is ``>=`` ``a``'s
and at least one is strictly greater, so ``sum(b) > sum(a)``. Ties (genuinely concurrent
vectors) are broken at the effect level by the unique ``(replica_id, counter)`` of the
effect id. So sorting effects by ``(vv.rank, replica_id, counter)`` is deterministic on
every replica *and* never places a cause after its effect.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class VersionVector:
    counts: dict[str, int] = field(default_factory=dict)

    # -- queries -----------------------------------------------------------
    def get(self, replica: str) -> int:
        return self.counts.get(replica, 0)

    def dominates(self, other: "VersionVector") -> bool:
        """True if ``self`` is causally at-or-after ``other`` (``self >= other`` pointwise)."""
        return all(self.get(r) >= c for r, c in other.counts.items())

    def strictly_dominates(self, other: "VersionVector") -> bool:
        return self.dominates(other) and self.counts != other.counts

    def concurrent(self, other: "VersionVector") -> bool:
        """Neither vector dominates the other — the edits are concurrent."""
        return not self.dominates(other) and not other.dominates(self)

    @property
    def rank(self) -> int:
        """Linear-extension key of the happens-before partial order (see module docstring)."""
        return sum(self.counts.values())

    # -- transforms (pure; vectors are frozen) -----------------------------
    def increment(self, replica: str) -> "VersionVector":
        c = dict(self.counts)
        c[replica] = c.get(replica, 0) + 1
        return VersionVector(c)

    def merge(self, other: "VersionVector") -> "VersionVector":
        """Pointwise max — the observed frontier after seeing both."""
        c = dict(self.counts)
        for r, n in other.counts.items():
            if n > c.get(r, 0):
                c[r] = n
        return VersionVector(c)

    # -- persistence -------------------------------------------------------
    def to_dict(self) -> dict[str, int]:
        # Sorted for stable, diff-friendly serialization across replicas.
        return {r: self.counts[r] for r in sorted(self.counts)}

    @classmethod
    def from_dict(cls, d: dict[str, int] | None) -> "VersionVector":
        return cls({str(r): int(n) for r, n in (d or {}).items()})
