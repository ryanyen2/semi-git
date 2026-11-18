"""`ugh resolve`: close a same-symbol fork a sync recorded in `.sgt/forks.json`.

The working tree holds the user's reconciliation of every symbol the two tips fork on. It is
gated on the project's check (or a recorded override), then committed with an `Ugh-Resolves:`
trailer naming both tips. The commit is mined like any other; the miner sets the op's `resolves`
from the trailer, and the order drops the tip the resolution does not descend from.
"""

from __future__ import annotations

from pathlib import Path

from ugh import state
from ugh.core import lens, oplog, oracle, order
from ugh.core.mine import RESOLVES_TRAILER
from ugh.core.store import Store
from ugh.entities.extract import extract_file


class ResolveError(Exception):
    """The fork could not be resolved: no forked symbol, a symbol missing from the tree, other
    uncommitted edits, or a check verdict that is not "pass"."""


def _close_resolved_forks(repo: Path) -> None:
    """Drop every recorded fork the current ideal no longer parks."""
    records = state.load_json(repo, "forks", default=[])
    if not records:
        return
    ops = Store(repo).all_ops()
    ideal = lens.current_ideal(repo).op_ids
    open_forks = {(sym, a, b) for sym, a, b in order.parked_forks(
        ideal | {t for r in records for t in r["tips"]}, ops)}
    remaining = [r for r in records if (r["symbol"], *r["tips"]) in open_forks]
    if len(remaining) != len(records):
        state.save_json(repo, "forks", remaining)


def resolve_fork(
    repo: str | Path, tip_a: str, tip_b: str, *, message: str,
    override: tuple[str, str, str | None] | None = None,
) -> str:
    """Commit the working tree's version of every symbol `tip_a` and `tip_b` fork on, with an
    `Ugh-Resolves:` trailer, and return the commit sha. Refused unless the check passes on the
    tree, or `override` (status, reason, by) records a passing verdict."""
    from ugh.core.planner import _dirty

    repo = Path(repo)
    lens.get(repo)
    by_id = {op.id: op for op in Store(repo).all_ops()}
    a, b = by_id[tip_a], by_id[tip_b]
    forked = sorted(
        sym for sym in set(a.footprint) & set(b.footprint)
        if a.footprint[sym][0] == b.footprint[sym][0] and a.footprint[sym][1] != b.footprint[sym][1]
    )
    if not forked:
        raise ResolveError("the two tips share no forked symbol -- nothing to merge")
    for sym in forked:
        path = sym.split("::", 1)[0]
        full = repo / path
        if not full.is_file():
            raise ResolveError(f"{path!r} not found in the working tree -- author {sym} there first")
        if "::" in sym and not any(e.id == sym for e in extract_file(path, full.read_bytes())):
            raise ResolveError(f"{sym!r} not found in the working tree's current {path} -- author it there first")
    others = set(_dirty(repo)) - {sym.split("::", 1)[0] for sym in forked}
    if others:
        raise lens.DirtyWorkingTreeError(
            f"resolve would commit other uncommitted changes: {sorted(others)} "
            f"-- record them with `ugh save`, or commit / `git restore` those files, then re-run")

    if override is None:
        result = oracle.check(repo)
        status = "pending" if result is None else "pass" if result[0] else "fail"
    else:
        status = override[0]
    if status != "pass":
        raise ResolveError(f"oracle verdict is {status!r} -- resolve with `--override pass` to land anyway")

    sha = lens.commit(repo, message, f"{RESOLVES_TRAILER}: {tip_a} {tip_b}")
    if override is not None:
        oracle.override(repo, *override)
    _close_resolved_forks(repo)
    oplog.append(repo, {"kind": "commit", "sha": sha, "verb": "resolve", "target": message})
    return sha
