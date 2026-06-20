"""The append-only effect log — the authored source of truth for *what changed*.

Every effect a replica lands is recorded as a ``LogEntry`` stamped with a globally-unique
``eid``, the authoring ``replica``, and the version vector at authoring time. The node
grouping (``bundles``) and the eventual materialization order are *projections* of this
log, so a merge between replicas reconciles exactly one structure: union the entries (by
``eid``), then re-project.

Phase A (this module) is behavior-identical to the pre-log store: ``bundles()`` reproduces
the old per-node effect lists, and node *ordering* / *metadata* still live on the
``Project`` (``order``/``graph``). Phase A2 will replace the explicit order with a
topological + total-order replay derived from the log (the SEC guarantee).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sgt.effects.model import Effect
from sgt.store.clock import VersionVector


@dataclass
class LogEntry:
    eid: str
    node_id: str
    effect: Effect
    author: str
    vv: VersionVector

    @property
    def order_key(self) -> tuple:
        """Total order consistent with causality: (vv.rank, author, counter).

        ``vv.rank`` is a linear extension of happens-before; the unique ``(author,
        counter)`` of the eid breaks ties between concurrent effects deterministically.
        """
        from sgt.store.replica import ReplicaIdentity

        _, counter = ReplicaIdentity.parse(self.eid)
        return (self.vv.rank, self.author, counter)

    def to_dict(self) -> dict:
        return {
            "eid": self.eid,
            "node_id": self.node_id,
            "effect": self.effect.to_dict(),
            "author": self.author,
            "vv": self.vv.to_dict(),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "LogEntry":
        return cls(
            eid=d["eid"],
            node_id=d["node_id"],
            effect=Effect.from_dict(d["effect"]),
            author=d["author"],
            vv=VersionVector.from_dict(d.get("vv")),
        )


class EffectLog:
    """Append-only log of stamped effects, plus node tombstones for removals."""

    def __init__(self) -> None:
        self.entries: list[LogEntry] = []
        self.tombstones: set[str] = set()  # node_ids removed (revert); excluded from projections

    # -- append / mutate ---------------------------------------------------
    def append(self, entry: LogEntry) -> None:
        self.entries.append(entry)

    def tombstone(self, node_ids: set[str]) -> None:
        """Mark nodes removed; their entries drop out of every projection."""
        self.tombstones |= set(node_ids)

    def replace_node_effects(self, node_id: str, entries: list[LogEntry]) -> None:
        """Drop ``node_id``'s current entries and append fresh ones (reconcile-in-place).

        Phase A keeps this a physical replace to stay behavior-identical with the old
        ``resolve_quarantine``. #003 Phase E reframes resolution as *new superseding
        effects* (so it survives a CRDT merge); until then this is the local store op.
        """
        self.entries = [e for e in self.entries if e.node_id != node_id]
        self.entries.extend(entries)

    # -- projections -------------------------------------------------------
    def _live(self) -> list[LogEntry]:
        return [e for e in self.entries if e.node_id not in self.tombstones]

    def live_entries(self, node_ids: set[str] | None = None) -> list[LogEntry]:
        """Non-tombstoned entries (optionally restricted to ``node_ids``), append order."""
        out = self._live()
        if node_ids is not None:
            out = [e for e in out if e.node_id in node_ids]
        return out

    def bundles(self) -> dict[str, list[Effect]]:
        """Per-node effect lists in append (seq) order — the old ``Project.bundles``."""
        out: dict[str, list[Effect]] = {}
        for e in self._live():
            out.setdefault(e.node_id, []).append(e.effect)
        return out

    def node_ids(self) -> list[str]:
        """Distinct non-tombstoned node ids, in first-appearance order."""
        seen: list[str] = []
        s: set[str] = set()
        for e in self._live():
            if e.node_id not in s:
                s.add(e.node_id)
                seen.append(e.node_id)
        return seen

    def frontier(self) -> VersionVector:
        """The observed causal frontier — pointwise max of every entry's vv."""
        f = VersionVector()
        for e in self.entries:
            f = f.merge(e.vv)
        return f

    # -- persistence -------------------------------------------------------
    def to_dict(self) -> dict:
        return {
            "version": 1,
            "entries": [e.to_dict() for e in self.entries],
            "tombstones": sorted(self.tombstones),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "EffectLog":
        log = cls()
        log.entries = [LogEntry.from_dict(e) for e in d.get("entries", [])]
        log.tombstones = set(d.get("tombstones", []))
        return log
