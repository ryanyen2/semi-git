"""Checkpoint: match real ops against a plan session's predicted hollow ops (plan U14, R18/R21).

`compute_checkpoint` is pure and offline (no mining) -- it never writes anything, so it's safe to
call from a read view (`sgt.api.plan_view`) on every request. For every active session, ops mined
since that session's `baseline_op_ids` are candidate matches for its still-pending steps;
footprint-overlap (the overlap coefficient -- see `_overlap` for why it is *not* Jaccard -- over a
two-level match-key join: `_step_keys` predicts at the granularity the planner stated (a qualname,
file dropped as the planner's guess; or a bare file, matched on file scope) and `_op_keys` indexes
each real op at both granularities so either prediction finds it) at or above `THRESHOLD` is a
candidate edge, and candidate edges union-find
into n:m groups -- naturally producing the "one
commit fulfills two steps" or "two commits fulfill one step" shapes. An op that's new to a session
but joins no group at all is drift *for that session* -- unless it is pure ordering/positioning
metadata (a standalone anchor or residue op, see `_is_ordering_only`), which is the companion of
some save's content op and never counts as unplanned work on its own; an op counts as global drift only if it's
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

import time
from dataclasses import dataclass
from pathlib import Path

from sgt import state
from sgt.core.op import Attribution, _symbol_kind
from sgt.core.store import Store
from sgt.loop.plan import _load_sessions, _save_sessions

THRESHOLD = 0.3  # overlap-coefficient floor (see `_overlap`) for a step<->op candidate edge


def _load_matches(repo: Path) -> dict:
    return state.load_json(repo, "plan_matches", default={})


def _save_matches(repo: Path, table: dict) -> None:
    state.save_json(repo, "plan_matches", table)


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


def _overlap(a: frozenset, b: frozenset) -> float:
    """The overlap coefficient (Szymkiewicz–Simpson): ``|a ∩ b| / min(|a|, |b|)``.

    Deliberately *not* Jaccard. Whether an op fulfilled a plan step must not depend on how much
    *other* work that op also did -- a coarse "add a whole file" op carries every entity in the
    file, and Jaccard's ``|a ∪ b|`` denominator would let that unrelated bulk dilute the score of
    every step the op genuinely fulfills, below ``THRESHOLD`` (a fully-built plan then reads as
    100% drift -- the exact bug this replaced). The overlap coefficient divides by the smaller
    footprint instead, so "does the op cover the step's entities (or vice-versa)" is answered on
    its own terms; the op's extra entities are accounted for separately -- as other steps' matches,
    or as drift."""
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


def _is_ordering_only(footprint) -> bool:
    """True iff every symbol is pure ordering/positioning metadata -- an ``anchor`` that places an
    entity or ``residue`` gap-bytes -- with no content the plan is written in.

    Such an op is the *companion* of whatever save placed the entity (the miner emits a per-entity
    anchor beside the coarse content op that carries the bytes); it is never behavioral work done
    *outside* a plan, so it is invisible to the drift layer -- exactly as pure residue already was.
    Anchors still contribute *matching* edges (``_op_keys`` keeps them: the finest per-entity
    signal when a batch save folds every entity's bytes into one whole-file content op), so this
    predicate governs only *drift*, where a standalone anchor would otherwise masquerade as
    unplanned work and report a fully-planned build as mostly-drift.

    Narrower than ``sgt.core.op.is_behavioral`` (which also drops ``nested``): a method-only edit
    (``Document.insert`` with no top-level ``Document`` symbol) is real content-drift we must keep,
    so ``nested`` stays; only the two positional-artifact kinds are stripped."""
    return all(_symbol_kind(sym) in ("anchor", "residue") for sym in footprint)


_MARKERS = ("__anchor__", "__residue__")


def _entity_name(sym: str) -> str | None:
    """The behavioral entity a footprint symbol is about — the join key the plan predicts against.

    Unlike ``sgt.lens.label._clean_symbol_name`` (which drops ``__anchor__``/``__residue__``
    symbols entirely, because a *label* shouldn't list a fold-ordering artifact as a member), the
    matcher keeps the entity *named by* the marker: ``crdt.py::__anchor__::RGA`` is precisely "the
    op that placed entity RGA", the per-entity granularity a plan step is written at. Dropping it
    would leave only the fat whole-file aggregate op, whose 20-symbol footprint dilutes any
    single-symbol step's Jaccard below ``THRESHOLD``. So we strip the marker *segment* and return
    the qualname it decorates."""
    if "::" not in sym:
        return sym.rsplit("/", 1)[-1] or None  # bare whole-file member -> its basename
    _, _, rest = sym.partition("::")
    for marker in _MARKERS:  # drop a leading fold-ordering segment, keep the entity it names
        if rest.startswith(marker + "::"):
            rest = rest[len(marker) + 2:]
    name = rest.split("::")[-1].replace("\x00", "").strip("_")
    return name or None


def _file_of(sym: str) -> str:
    """The file a footprint symbol lives in -- everything left of the first ``::`` (a bare
    whole-file symbol IS its own file). This is the scope a *file-granularity* plan prediction
    joins on (see ``_step_keys``)."""
    return sym.split("::", 1)[0]


def _op_keys(footprint) -> frozenset[str]:
    """A real op's match keys, indexed at BOTH granularities so a step predicted at either finds
    it: the entity *qualname* of each symbol (``ent:`` keys) AND the *file* each touches (``file:``
    keys). The two namespaces are prefix-disjoint, so a file path can never collide with a qualname.

    An op is *reality* -- it always knows both its entities and their file -- so it is indexed every
    way a prediction might name it. Whether the file actually gates a match is decided entirely on
    the plan side (``_step_keys``): a symbol-level prediction contributes no ``file:`` key, so an
    op's ``file:`` keys sit inert against it and file drift stays free.

    ``__plan__::`` sentinels drop out. So does ``residue`` (positional gap-bytes named after the
    *preceding* entity -- ``__residue__::RGA`` is the gap that follows RGA, not RGA itself, so
    matching a "build RGA" step to it would attribute the work to a neighbour; the kernel's
    ``is_behavioral`` excludes residue for the same reason). An ``anchor`` is kept: it is the
    genuine per-entity creation marker, and the finest-grained "this entity was touched here"
    signal when a batch save folds the actual bytes into one coarse whole-file content op."""
    keys = set()
    for sym in footprint:
        if sym.startswith("__plan__::") or _symbol_kind(sym) == "residue":
            continue
        name = _entity_name(sym)
        if name is not None:
            keys.add("ent:" + name)
        keys.add("file:" + _file_of(sym))
    return frozenset(keys)


def _step_keys(footprint) -> frozenset[str]:
    """A plan step's match keys, at the granularity the planner *stated* -- the crux of the
    two-level join.

    A ``file::qualname`` step entry keeps only its qualname (``ent:`` key). The entities are the
    stated intent (``RGA``, ``Element``, ``Op.__post_init__``); the *file* they land in is the
    planner's guess -- it drifts freely (planned ``rga.py::RGA`` vs built ``crdt.py::RGA``) and
    gating on it would report a fulfilled-but-moved entity as drift, the opposite of the truth. So
    identity is the qualname, matching the codebase's own scheme (``file::qualname`` is a surface
    lookup, not the join key). Qualnames stay *qualified* (``RGA.apply``, never bare ``apply``), so
    two methods of different classes never collide; only two top-level entities sharing a name
    across files could, and a checkpoint match is preview-only -- never written without an explicit
    ``confirm``.

    A *bare-file* entry (no ``::``) is a genuine file-granularity predicate: the file IS the stated
    intent (the LLM decomposer's usual output -- "work in ``livehub/server.py``"), so it becomes a
    ``file:`` key that matches any op touching that file. Without this, a file-level prediction --
    which carries no qualname -- could never join the symbol-level ops (``server.py::Server``) that
    implement it, and a faithfully-built file-level plan read as 100% drift.

    ``__plan__::`` sentinels and ``residue`` drop out, as in ``_op_keys``."""
    keys = set()
    for sym in footprint:
        if sym.startswith("__plan__::") or _symbol_kind(sym) == "residue":
            continue
        if "::" in sym:  # entity-level prediction: the qualname is identity, the file is dropped
            name = _entity_name(sym)
            if name is not None:
                keys.add("ent:" + name)
        else:  # bare file: a file-granularity predicate joins on file scope
            keys.add("file:" + sym)
    return frozenset(keys)


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
    from sgt.core import opindex

    ops = opindex.index_ops(repo)  # footprint-overlap matching only -- never reads op.images
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
            step_syms = _step_keys(hollow.footprint)
            for op in new_ops:
                if _overlap(step_syms, _op_keys(op.footprint)) >= THRESHOLD:
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

        # Drift is *unplanned work*, not bookkeeping: an op made purely of positional/ordering
        # metadata (residue gap-bytes or a per-entity anchor) did no nameable behavioral work of
        # its own -- it is the companion of whatever save placed the entity -- so it is neither a
        # match nor drift, invisible to the plan layer (see `_is_ordering_only`). Only ops that
        # carry real content (an entity, a nested method, or a whole file) and matched no step
        # count as drift.
        drift_candidates.update(
            op.id for op in new_ops if op.id not in edge_ops and not _is_ordering_only(op.footprint)
        )

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
    # A plan with no step left pending is done: flip it to the terminal `completed` status so it
    # leaves the active review surface (`plan.active_sessions`) instead of lingering as an
    # "unresolved" plan forever -- nothing closed a session before this. The record is kept for
    # provenance; only its status changes.
    if all(step["status"] != "pending" for step in record["steps"]):
        record["status"] = "completed"
    _save_sessions(repo, sessions)

    matches = _load_matches(repo)
    intent = "; ".join(titles)
    for op_id in op_ids:
        matches[op_id] = {"session_id": session_id, "hollow_ids": sorted(hollow_ids), "intent": intent}
    _save_matches(repo, matches)

    for hollow_id in hollow_ids:
        (store.hollow_dir / hollow_id).unlink(missing_ok=True)

    _stamp_session(store, session_id, op_ids)  # D7: the fulfilling session onto the op's provenance

    # Intent-ledger M1 (planned path): the alignment just computed -- these ops fulfilled these
    # steps -- is exactly what reflection transcribes into a local rationale record, keyed to the
    # plan's intake evidence. Guarded so a reflection hiccup never fails a confirm (capture/derive
    # is always subordinate to the op algebra).
    try:
        from sgt.intent.rationale import reflect_planned_match
        reflect_planned_match(repo, session_id, list(op_ids))
    except Exception:  # noqa: BLE001 -- deriving rationale must never break plan-matching
        pass


def _stamp_session(store: Store, session_id: str, op_ids) -> None:
    """Stamp `session=session_id` onto the structured attribution of each op's provenance SHAs
    (D7). The immutable `Op` payload is untouched -- only its excluded-from-id provenance shape
    grows -- so no id moves. A hollow op (not committed) or an unknown id is skipped."""
    for op_id in op_ids:
        op = store.get(op_id)
        if op is None:
            continue
        entries = tuple(Attribution(sha=sha, session=session_id) for sha in op.provenance)
        if entries:
            store.attribute(op_id, entries)


def stamp_drift(repo: str | Path, session_id: str, op_ids) -> None:
    """The sibling writer to `confirm_match` for drift ops a caller explicitly names: stamp the
    naming session onto each op's structured provenance (D7). `compute_checkpoint` stays pure --
    stamping is never done inside it, only by an explicit call here."""
    _stamp_session(Store(Path(repo)), session_id, op_ids)
