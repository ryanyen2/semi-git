"""Replica identity and the monotonic authoring counter.

Every effect a replica authors is stamped with a globally-unique id ``"{replica}:{n}"``
and (later) the replica's version vector. The *replica* half is a stable uuid minted once
per clone — it survives moves/renames because it is persisted, not derived from host/path.
The *counter* half is strictly monotonic and crash-safe (persisted on each advance), so two
effects from the same replica never share an id even across process restarts.

This is the shared foundation for both the causal layer (version vectors, the effect log)
and statement identity (tree-CRDT positional ids tie-break on the same ``(replica, counter)``
author stamp), so the two never disagree about who authored what or in what order.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from pathlib import Path

REPLICA_FILE = "replica.json"


@dataclass
class ReplicaIdentity:
    """A clone's stable id plus its next authoring counter.

    ``mint()`` returns the next effect id and advances+persists the counter. The save
    path is held so each advance is durable (monotonicity must survive a crash).
    """

    replica_id: str
    next_counter: int = 0
    _path: Path | None = None

    # -- lifecycle ---------------------------------------------------------
    @classmethod
    def load_or_create(cls, sgt_dir: str | Path, replica_id: str | None = None) -> "ReplicaIdentity":
        """Load the replica identity from ``.sgt/replica.json`` or mint a fresh one.

        ``replica_id`` is injectable for deterministic tests; in normal use it is a uuid4
        minted once and never changed.
        """
        sgt_dir = Path(sgt_dir)
        path = sgt_dir / REPLICA_FILE
        if path.exists():
            d = json.loads(path.read_text(encoding="utf-8"))
            return cls(replica_id=d["replica_id"], next_counter=int(d["next_counter"]), _path=path)
        ident = cls(replica_id=replica_id or uuid.uuid4().hex, next_counter=0, _path=path)
        ident.save()
        return ident

    def save(self) -> None:
        if self._path is None:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps({"replica_id": self.replica_id, "next_counter": self.next_counter}, indent=2),
            encoding="utf-8",
        )

    # -- minting -----------------------------------------------------------
    def mint(self) -> str:
        """Return the next globally-unique effect id and persist the advance."""
        n = self.next_counter
        self.next_counter = n + 1
        self.save()
        return f"{self.replica_id}:{n}"

    @staticmethod
    def parse(eid: str) -> tuple[str, int]:
        """Split an effect id back into ``(replica_id, counter)``.

        The counter is the suffix after the final ``:``; replica ids never contain ``:``
        (uuid4 hex). Returns counter ``-1`` for ids that predate this scheme (legacy/empty),
        so they sort before any authored effect under the total order.
        """
        if ":" not in eid:
            return eid, -1
        rid, _, n = eid.rpartition(":")
        try:
            return rid, int(n)
        except ValueError:
            return eid, -1
