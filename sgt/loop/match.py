"""Checkpoint: match real ops against a plan session's predicted hollow ops (plan U14, R18/R21).

`compute_checkpoint` is pure and offline (no mining) -- it never writes anything, so it's safe to
call from a read view (`sgt.api.plan_view`) on every request. For every active session, ops mined
since that session's `baseline_op_ids` are candidate matches for its still-pending steps;
footprint-overlap (Jaccard over real, non-`__plan__::` symbols) at or above `THRESHOLD` is a
candidate edge, and candidate edges union-find into n:m groups -- naturally producing the "one
commit fulfills two steps" or "two commits fulfill one step" shapes. An op that's new to a session
but joins no group at all is drift *for that session*; an op counts as global drift only if it's
unmatched drift in every session that considers it new (so a real match in session A isn't
overridden by a stale, unrelated session B whose baseline happens to predate the same op). An op
already recorded in `.sgt/local/plan_matches.json` by a prior `confirm_match` is excluded from
consideration entirely, so a confirmed match never resurfaces as drift once its step leaves
`pending`.

`confirm_match` is the only writer: it records `.sgt/local/plan_matches.json` (op -> session/
hollow/intent, a pure side-table -- the immutable, content-addressed `Op` itself is never
rewritten), marks the confirmed steps `matched`, and deletes their now-consumed hollow files.
Nothing is confirmed unless a specific group is named, mirroring `sgt.core.rewrite.apply_split`'s
explicit `confirm=True` discipline.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

from sgt.core.store import Store
from sgt.loop.plan import _load_sessions, _save_sessions

_MATCHES_FILE = "plan_matches.json"
THRESHOLD = 0.3  # Jaccard footprint-overlap floor for a step<->op candidate edge


def _matches_path(repo: Path) -> Path:
    return repo / ".sgt" / "local" / _MATCHES_FILE


def _load_matches(repo: Path) -> dict:
    path = _matches_path(repo)
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def _save_matches(repo: Path, table: dict) -> None:
    path = _matches_path(repo)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(table, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def recorded_matches(repo: str | Path) -> dict:
    """Every confirmed match, keyed by op id -- a pure side-table read a future consumer joins
    against."""
    return _load_matches(Path(repo))


@dataclass(frozen=True)
class CheckpointGroup:
    session_id: str
    hollow_ids: tuple[str, ...]
    op_ids: tuple[str, ...]


@dataclass(frozen=True)
class CheckpointResult:
    matches: tuple[CheckpointGroup, ...]
    drift_op_ids: tuple[str, ...]


def _jaccard(a: frozenset, b: frozenset) -> float:
    union = a | b
    return len(a & b) / len(union) if union else 0.0


def _real_symbols(footprint: dict) -> frozenset[str]:
    return frozenset(sym for sym in footprint if not sym.startswith("__plan__::"))


class _UnionFind:
    def __init__(self) -> None:
        self._parent: dict[str, str] = {}

    def find(self, x: str) -> str:
        self._parent.setdefault(x, x)
        while self._parent[x] != x:
            x = self._parent[x]
        return x

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self._parent[ra] = rb


def compute_checkpoint(repo: str | Path) -> CheckpointResult:
    """Pure, offline (no mining -- callers `get()` first, R9). See module docstring for the
    per-session grouping / global-drift reconciliation."""
    repo = Path(repo)
    store = Store(repo)
    ops = store.all_ops()
    sessions = _load_sessions(repo)

    groups: list[CheckpointGroup] = []
    matched_op_ids: set[str] = set()
    drift_candidates: set[str] = set()
    already_matched = set(_load_matches(repo))  # confirmed in a prior checkpoint -- never drift again

    for session_id, rec in sorted(sessions.items()):
        if rec["status"] != "active":
            continue
        baseline = frozenset(rec["baseline_op_ids"])
        new_ops = [op for op in ops if op.id not in baseline and op.id not in already_matched]
        pending_steps = [s for s in rec["steps"] if s["status"] == "pending"]
        hollows = {s["hollow_id"]: store.get_hollow(s["hollow_id"]) for s in pending_steps}

        uf = _UnionFind()
        edge_ops: set[str] = set()
        edge_hollows: set[str] = set()
        for hollow_id, hollow in hollows.items():
            if hollow is None:
                continue
            step_syms = _real_symbols(hollow.footprint)
            for op in new_ops:
                if _jaccard(step_syms, frozenset(op.footprint)) >= THRESHOLD:
                    uf.union(f"h:{hollow_id}", f"o:{op.id}")
                    edge_hollows.add(hollow_id)
                    edge_ops.add(op.id)

        clusters: dict[str, dict[str, set]] = {}
        for h in edge_hollows:
            clusters.setdefault(uf.find(f"h:{h}"), {"hollows": set(), "ops": set()})["hollows"].add(h)
        for o in edge_ops:
            clusters.setdefault(uf.find(f"o:{o}"), {"hollows": set(), "ops": set()})["ops"].add(o)

        for cluster in clusters.values():
            if not cluster["hollows"] or not cluster["ops"]:
                continue
            groups.append(CheckpointGroup(
                session_id=session_id,
                hollow_ids=tuple(sorted(cluster["hollows"])),
                op_ids=tuple(sorted(cluster["ops"])),
            ))
            matched_op_ids.update(cluster["ops"])

        drift_candidates.update(op.id for op in new_ops if op.id not in edge_ops)

    drift_op_ids = tuple(sorted(drift_candidates - matched_op_ids))
    return CheckpointResult(matches=tuple(groups), drift_op_ids=drift_op_ids)


def confirm_match(repo: str | Path, session_id: str, hollow_ids: list[str], op_ids: list[str]) -> None:
    """The explicit, caller-named write: records `plan_matches.json` entries for `op_ids`, marks
    the steps owning `hollow_ids` as `matched`, and deletes their now-consumed hollow files."""
    repo = Path(repo)
    store = Store(repo)
    sessions = _load_sessions(repo)
    record = sessions[session_id]

    titles = []
    for step in record["steps"]:
        if step["hollow_id"] in hollow_ids:
            step["status"] = "matched"
            step["matched_op_ids"] = sorted(op_ids)
            titles.append(step["title"])
    record["last_activity_ts"] = time.time()
    _save_sessions(repo, sessions)

    matches = _load_matches(repo)
    intent = "; ".join(titles)
    for op_id in op_ids:
        matches[op_id] = {"session_id": session_id, "hollow_ids": sorted(hollow_ids), "intent": intent}
    _save_matches(repo, matches)

    for hollow_id in hollow_ids:
        (store.hollow_dir / hollow_id).unlink(missing_ok=True)
