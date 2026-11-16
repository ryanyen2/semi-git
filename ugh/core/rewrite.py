"""`ugh resolve`: close a same-symbol fork a sync recorded in `.sgt/forks.json`.

Every symbol the two tips fork on is rebuilt from the working tree as one op that chain-extends
`tip_a` and names both tips in `resolves`. The candidate is the current ideal plus `tip_a`'s
downset plus those ops; it is written to the tree, gated on the oracle, and committed.
"""

from __future__ import annotations

from pathlib import Path

from ugh import state
from ugh.core import lens, oracle, order
from ugh.core.fold import code
from ugh.core.ideal import Ideal
from ugh.core.mine import _content_version, _entity_bytes, _positional_version
from ugh.core.op import Op, make_op
from ugh.core.store import Store
from ugh.entities.extract import extract_file
from ugh.store.gitbind import GitBinding


class ResolveError(Exception):
    """The fork could not be resolved: no forked symbol, an invalid candidate, a symbol missing
    from the tree, or an oracle verdict that is not "pass"."""


def _entity_bytes_from_tree(repo: Path, sym: str) -> bytes:
    path, _, _name = sym.partition("::")
    full = repo / path
    if not full.is_file():
        raise ResolveError(f"{path!r} not found in the working tree -- author {sym} there first")
    source = full.read_bytes()
    ent = next((e for e in extract_file(path, source) if e.id == sym), None)
    if ent is None:
        raise ResolveError(f"{sym!r} not found in the working tree's current {path} -- author it there first")
    return _entity_bytes(source, ent)


def _close_resolved_forks(repo: Path, candidate: Ideal, ops: list[Op]) -> None:
    """Drop every recorded fork that `candidate` holds an op chained onto one of its tips."""
    records = state.load_json(repo, "forks", default=[])
    if not records:
        return
    by_id = {op.id: op for op in ops}

    def resolved(rec: dict) -> bool:
        sym = rec["symbol"]
        tip_afters = {
            by_id[t].footprint[sym][1]
            for t in rec["tips"] if t in by_id and sym in by_id[t].footprint
        }
        return any(
            sym in by_id[oid].footprint and by_id[oid].footprint[sym][0] in tip_afters
            for oid in candidate.op_ids if oid in by_id
        )

    remaining = [r for r in records if not resolved(r)]
    if len(remaining) != len(records):
        state.save_json(repo, "forks", remaining)


def resolve_fork(
    repo: str | Path, tip_a: str, tip_b: str, *, message: str,
    override: tuple[str, str, str | None] | None = None,
) -> str:
    """Commit the working tree's version of every symbol `tip_a` and `tip_b` fork on, and return
    the commit sha. Refused unless the oracle passes for the candidate, or `override`
    (status, reason, by) records a passing verdict."""
    repo = Path(repo)
    lens.get(repo)
    store = Store(repo)
    ops = store.all_ops()
    by_id = {op.id: op for op in ops}
    a, b = by_id[tip_a], by_id[tip_b]
    forked = sorted(
        sym for sym in set(a.footprint) & set(b.footprint)
        if a.footprint[sym][0] == b.footprint[sym][0] and a.footprint[sym][1] != b.footprint[sym][1]
    )
    if not forked:
        raise ResolveError("the two tips share no forked symbol -- nothing to merge")

    merged = []
    for sym in forked:
        image = _entity_bytes_from_tree(repo, sym)
        merged.append(make_op(
            {sym: (a.footprint[sym][1], _positional_version(sym, _content_version(image)))},
            {sym: image}, kind="merge",
            intent=f"merge-op: reconcile {tip_a[:12]} and {tip_b[:12]} on {sym}",
            resolves=frozenset({tip_a, tip_b}),
        ))
    all_ops = ops + merged
    candidate_ids = lens.current_ideal(repo).op_ids | order.downset(tip_a, ops) | {op.id for op in merged}
    try:
        candidate = Ideal.from_ops(candidate_ids, all_ops)
    except ValueError as e:
        raise ResolveError(f"resolving would leave an invalid ideal, refused: {e}") from e

    materialized = code(candidate, all_ops)
    authored = {sym.split("::", 1)[0] for sym in forked}
    conflicts = lens._dirty_conflicts(repo, GitBinding(repo), materialized) - authored
    if conflicts:
        raise lens.DirtyWorkingTreeError(
            f"resolve would overwrite uncommitted changes: {sorted(conflicts)} "
            f"-- record them with `ugh save`, or commit / `git restore` those files, then re-run"
        )

    for op in merged:
        store.add(op)
    lens._write_working_tree(repo, materialized, all_ops)

    if override is None:
        oracle.run(repo, ideal=candidate)
    else:
        status, reason, by = override
        oracle.override(repo, status, reason, by, ideal=candidate)
    status = oracle.overall_status(oracle.verdict_for(repo, candidate))
    if status != "pass":
        raise ResolveError(f"oracle verdict is {status!r} -- resolve with `--override pass` to land anyway")

    _close_resolved_forks(repo, candidate, all_ops)
    sha = lens.commit_materialized(repo, candidate, message)
    lens.record_ideal(repo, candidate, sha)
    return sha
