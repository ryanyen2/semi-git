"""Tree-CRDT positional identity for statements inside a function body.

The merge-quality lever: today ``replace_def`` rewrites a whole unit, so two people editing
*different lines of the same function* needlessly conflict. To let those commute we must
address individual statements — and the hard part is **stable identity under concurrent
insertion**: two replicas inserting "at the same place" must get distinct, deterministically
ordered ids without coordination.

This is a fractional-indexing / Logoot-style allocator. A ``PosId`` is a tuple of digits
that orders densely (you can always allocate strictly between any two), plus the authoring
``(replica, counter)`` as the collision tie-break. Crucially that tie-break is the *same*
identity the effect log stamps onto every effect (``ReplicaIdentity.mint`` →
``"replica:counter"``), so positions and causal stamps never disagree about authorship or
order. Two concurrent allocations between the same neighbours produce identical digits but
distinct ``PosId``s (different author), ordered identically on every replica.

Prototype scope (origin redesign R1 / merge-plan C1): the allocator + total order, proven
in isolation before it is wired into effect ops.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# A wide base keeps allocations shallow (more room before we must descend a level).
BASE = 1 << 16


@dataclass(frozen=True)
class PosId:
    """A dense, totally-ordered position with an authorship tie-break.

    Order is: ``digits`` lexicographically (Python tuple order already matches the
    0-padding convention, since allocated digits are never trailing zeros), then
    ``author``, then ``counter``. ``author``/``counter`` are the minting effect's
    ``(replica_id, counter)`` — see ``ReplicaIdentity``.
    """

    digits: tuple[int, ...]
    author: str = ""
    counter: int = -1

    @property
    def key(self) -> tuple:
        """Sort key giving a deterministic total order across replicas."""
        return (self.digits, self.author, self.counter)

    def __lt__(self, other: "PosId") -> bool:
        return self.key < other.key

    def to_dict(self) -> dict:
        return {"digits": list(self.digits), "author": self.author, "counter": self.counter}

    @classmethod
    def from_dict(cls, d: dict) -> "PosId":
        return cls(tuple(d["digits"]), d.get("author", ""), int(d.get("counter", -1)))


def _alloc_digits(lo: tuple[int, ...], hi: tuple[int, ...]) -> tuple[int, ...]:
    """Digits strictly between ``lo`` and ``hi`` (lexicographic, 0-padded low / BASE-padded high).

    ``lo``/``hi`` are the neighbouring positions' digits; an empty ``lo`` is the start of the
    body (pads with 0), an empty ``hi`` is the end (pads with ``BASE``). Descends a level when
    two neighbours are adjacent at the current level, so the space is effectively unbounded.
    """
    out: list[int] = []
    i = 0
    while True:
        low = lo[i] if i < len(lo) else 0
        high = hi[i] if i < len(hi) else BASE
        if high - low > 1:
            out.append((low + high) // 2)
            return tuple(out)
        out.append(low)  # no gap here — fix this digit and descend
        i += 1


def between(lo: PosId | None, hi: PosId | None, author: str, counter: int) -> PosId:
    """Allocate a ``PosId`` strictly between ``lo`` and ``hi`` for ``(author, counter)``.

    ``lo=None`` means "before the first statement", ``hi=None`` means "after the last".
    Concurrent calls with the same neighbours yield equal digits but distinct ``PosId``s,
    ordered by ``(author, counter)``.
    """
    lo_d = lo.digits if lo is not None else ()
    hi_d = hi.digits if hi is not None else ()
    return PosId(_alloc_digits(lo_d, hi_d), author, counter)


def from_eid(lo: PosId | None, hi: PosId | None, eid: str) -> PosId:
    """Allocate using an effect id's ``(replica, counter)`` as the tie-break (the unifying path)."""
    from sgt.store.replica import ReplicaIdentity

    rid, ctr = ReplicaIdentity.parse(eid)
    return between(lo, hi, rid, ctr)


def sorted_positions(positions: list[PosId]) -> list[PosId]:
    """Body statement order: the one true order every replica agrees on."""
    return sorted(positions, key=lambda p: p.key)


@dataclass
class Body:
    """A convenience view: an ordered list of (PosId, payload) slots for one function body.

    Not yet wired into effects — this is the prototype's exercising surface. ``insert``
    returns the new ``PosId`` so a caller (later, an ``insert_stmt`` effect) can record it.
    """

    slots: list[tuple[PosId, object]] = field(default_factory=list)

    def _ordered(self) -> list[tuple[PosId, object]]:
        return sorted(self.slots, key=lambda s: s[0].key)

    def positions(self) -> list[PosId]:
        return [p for p, _ in self._ordered()]

    def payloads(self) -> list[object]:
        return [v for _, v in self._ordered()]

    def insert(self, index: int, payload: object, author: str, counter: int) -> PosId:
        """Insert ``payload`` at ordered position ``index`` and return its allocated ``PosId``."""
        ordered = self._ordered()
        lo = ordered[index - 1][0] if index > 0 else None
        hi = ordered[index][0] if index < len(ordered) else None
        pid = between(lo, hi, author, counter)
        self.slots.append((pid, payload))
        return pid
