"""Names a person or an agent gave to steps: one table `{op id: label}`, one label per op, none
allowed.

A name never grows by itself. A later edit to a named definition carries no name unless it was saved
with `--as` or named with `ugh name`; what stands on a name is the planner's answer, never stored
membership. The table is written by `save --as`, `name` and `rename` only, lives at
`.sgt/names.json`, travels on `refs/ugh/state`, and sync merges it three-way per op.
"""

from __future__ import annotations

from pathlib import Path

from ugh import state


def load(repo: str | Path) -> dict[str, str]:
    """The names table, `{op id: label}`."""
    return state.load_json(repo, "names", default={})


def at(gb, sha: str | None) -> dict[str, str]:
    """The names table as published at state-ref commit `sha` (empty for None)."""
    return state.load_blob_json(gb, sha, "names", default={}) if sha else {}


def save(repo: str | Path, table: dict[str, str]) -> None:
    state.save_json(repo, "names", dict(sorted(table.items())))


def name(repo: str | Path, op_ids, label: str) -> None:
    """Give `label` to every op in `op_ids`, moving it from any label it had."""
    from ugh.core import oplog
    from ugh.core.store import locked_section

    oplog.append(repo, {"kind": "names", "verb": "name", "snapshot": oplog.snapshot(repo, ["names"])})
    with locked_section(repo):
        table = load(repo)
        table.update(dict.fromkeys(op_ids, label))
        save(repo, table)


def rename(repo: str | Path, old: str, new: str) -> None:
    """Move every op labelled `old` to `new` (an existing `new` absorbs them)."""
    from ugh.core import oplog
    from ugh.core.store import locked_section

    oplog.append(repo, {"kind": "names", "verb": "rename", "snapshot": oplog.snapshot(repo, ["names"])})
    with locked_section(repo):
        table = load(repo)
        moved = [o for o, label in table.items() if label == old]
        table.update(dict.fromkeys(moved, new))
        save(repo, table)


def merge(base: dict[str, str], ours: dict[str, str], theirs: dict[str, str]):
    """Three-way per op: the side that changed an op's label wins; when both changed it
    differently, ours stays and `(op, ours, theirs)` is reported. Returns (merged, conflicts)."""
    merged: dict[str, str] = {}
    conflicts = []
    for op in sorted(set(base) | set(ours) | set(theirs)):
        b, o, t = base.get(op), ours.get(op), theirs.get(op)
        label = t if o == b else o
        if o not in (b, t) and t != b:
            conflicts.append((op, o, t))
        if label is not None:
            merged[op] = label
    return merged, conflicts


def declare(repo: str | Path, labels) -> dict:
    """Add `labels` to the plan, a list of names declared before their edits exist. The baseline is
    HEAD when the plan was first declared."""
    from ugh.store.gitbind import GitBinding

    plan = state.load_json(repo, "plan", default=None) or {"names": [], "baseline": GitBinding(repo).head()}
    plan["names"] += [label for label in dict.fromkeys(labels) if label not in plan["names"]]
    state.save_json(repo, "plan", plan)
    return plan


def plan_done(repo: str | Path) -> None:
    """Clear the plan."""
    state.path(repo, "plan").unlink(missing_ok=True)
