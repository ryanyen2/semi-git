"""A function body as a CRDT sequence of PosId-keyed statements.

This is where the effect log (identity + causality) and the tree-CRDT positional ids fuse.
Today ``replace_def`` rewrites a whole unit, so two people editing different lines of one
function needlessly conflict. The fix is to address *statements*, but a statement's identity
cannot live in the source text (it would not survive a parse/unparse round-trip). So a body
becomes a **replayed sequence**: each statement is a slot keyed by a ``PosId``, edits are
operations on slots, and rendering sorts slots by position. Then:

* two inserts/edits to **distinct** slots commute by construction (the merge-quality win);
* two edits to the **same** slot are a real, detectable conflict (handed to merge policy);
* the rendered text is a deterministic function of the slot set (SEC for bodies).

Prototype scope (origin merge-plan C2): the sequence model + its commute property, proven in
isolation before it is wired into the effect ops / materialization.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field

from sgt.effects.stmt import PosId, between


@dataclass
class Slot:
    """One statement: its position, its source text, and a tombstone for removal."""

    pos: PosId
    source: str
    deleted: bool = False
    # The (author, counter) of the edit that last set `source` — the LWW tie-break for
    # concurrent replaces on the same slot, mirroring the effect-id total order.
    edit_author: str = ""
    edit_counter: int = -1

    def to_dict(self) -> dict:
        return {"pos": self.pos.to_dict(), "source": self.source, "deleted": self.deleted,
                "edit_author": self.edit_author, "edit_counter": self.edit_counter}

    @classmethod
    def from_dict(cls, d: dict) -> "Slot":
        return cls(PosId.from_dict(d["pos"]), d["source"], d.get("deleted", False),
                   d.get("edit_author", ""), d.get("edit_counter", -1))


class StatementSeq:
    """An order-independent set of statement slots keyed by ``PosId``."""

    def __init__(self) -> None:
        self._slots: dict[tuple, Slot] = {}  # pos.key -> Slot

    # -- construction ------------------------------------------------------
    @classmethod
    def from_source(cls, body_src: str, author: str, base_counter: int = 0) -> "StatementSeq":
        """Seed a sequence from a function body's source, one slot per top-level statement.

        Positions are allocated left-to-right; their tie-break is ``(author, base_counter+i)``
        so a body authored by an ``add_def`` derives its slot identities from that effect's id.
        """
        seq = cls()
        tree = ast.parse(body_src)
        prev: PosId | None = None
        for i, stmt in enumerate(tree.body):
            pos = between(prev, None, author, base_counter + i)
            # The seeded source is the *original*: any real edit must supersede it under LWW,
            # so its edit identity is the minimal sentinel ("", -1). Two consequences this gets
            # right that ``(author, base_counter+i)`` got wrong: (1) a later same-replica edit
            # (counter > base_counter) still wins even on a high-index statement; (2) a pending,
            # not-yet-stamped candidate (eid="" → ("", -1)) applies during gating, so the
            # confluence gate validates the *real* change instead of a no-op. Position identity
            # still derives from the defining effect (the PosId above), unchanged.
            seq._slots[pos.key] = Slot(pos, ast.unparse(stmt), edit_author="", edit_counter=-1)
            prev = pos
        return seq

    # -- operations (each returns the affected PosId) ----------------------
    def insert(self, after: PosId | None, before: PosId | None,
               source: str, author: str, counter: int) -> PosId:
        pos = between(after, before, author, counter)
        self._slots[pos.key] = Slot(pos, source, edit_author=author, edit_counter=counter)
        return pos

    def replace(self, pos: PosId, source: str, author: str, counter: int) -> None:
        """Set a slot's source. Concurrent replaces on one slot resolve by LWW (author, counter)."""
        cur = self._slots.get(pos.key)
        if cur is None:
            self._slots[pos.key] = Slot(pos, source, edit_author=author, edit_counter=counter)
            return
        if (cur.edit_author, cur.edit_counter) <= (author, counter):
            cur.source, cur.edit_author, cur.edit_counter = source, author, counter

    def remove(self, pos: PosId) -> None:
        cur = self._slots.get(pos.key)
        if cur is not None:
            cur.deleted = True  # tombstone, not drop — survives a merge with a replica that didn't see it

    # -- projection --------------------------------------------------------
    def ordered(self) -> list[Slot]:
        return sorted((s for s in self._slots.values() if not s.deleted), key=lambda s: s.pos.key)

    def positions(self) -> list[PosId]:
        return [s.pos for s in self.ordered()]

    def render(self, indent: str = "    ") -> str:
        """Render the body text. ``pass`` when empty, so the function stays parseable."""
        lines = [s.source for s in self.ordered()]
        if not lines:
            lines = ["pass"]
        out: list[str] = []
        for src in lines:
            for line in src.splitlines() or [""]:
                out.append(indent + line if line else line)
        return "\n".join(out)

    def conflicts(self, other: "StatementSeq") -> list[PosId]:
        """Slots both sequences edited to *different* sources — the same-slot conflict set."""
        bad: list[PosId] = []
        for k, s in self._slots.items():
            o = other._slots.get(k)
            if o is not None and not s.deleted and not o.deleted and s.source != o.source:
                bad.append(s.pos)
        return bad
