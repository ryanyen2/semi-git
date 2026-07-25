"""The unified operation-event log + universal undo (plan U8, R7/KTD6).

One append-only, per-ref event log that every mutating verb appends to; `undo` pops the tail event
and applies its inverse uniformly, walking arbitrarily far back. "Arbitrarily far back" means the
*depth of sequential undo* -- repeated `undo` pops one tail event at a time, reverse-chronologically
-- NOT random-access undo of a non-tail op (undoing an op later ops built on would need a rebase
story, which is explicitly out of scope).

**Subsumes `ideal_journal` (KTD6).** This is a single store: it reuses the existing
`.sgt/local/ideal_journal.json` slot, now holding a list of typed events per ref-key rather than
only `{ideal, witness}` dicts. Every event carries an explicit `kind`: `record_ideal` pushes the
outgoing ideal tagged `kind="ideal_edit"`, and that restore is folded in as one event kind here.
There is one pop per undo, so an ideal-edit event and a journal entry can never be popped by two
mechanisms.

**Inverse-descriptor = a snapshot of the affected artifact(s) captured at append time**, restored
on undo -- the simplest robust inverse:
  * `ideal_edit`    -- the prior ideal (+ its witness); restored by re-materializing it
                       (`lens._apply_ideal_edit_inverse`), the ideal-edit restore.
  * `feature_reorg` -- a snapshot of `tree`/`pins`/`authored_features` before the reorg
                       (merge/split/rename/move are byte-neutral for `code(I)`, so restoring the
                       three metadata artifacts is the whole inverse).
  * `after`         -- a snapshot of the declared-edge OR-Set before the edge was added.
  * `land`/`propose`-- provenance-only: logged, but `apply_inverse` REFUSES. A shared-out mutation
                       already left the local clone (mirrors the `journal=checked_out` guard,
                       `sgt/core/sync/land.py:207`), so its inverse is never applied.

This is distinct from `sgt.api.oplog_view`, which projects the *content* op-DAG (the mined ops),
not this user-action history; the action projection is `sgt.api.oplog_actions_view`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sgt import state
from sgt.core.store import locked_section

_SLOT = "ideal_journal"  # the unified log reuses (repurposes) the ideal_journal slot -- one store.


# -- store (per-ref list of events) ---------------------------------------------------------------


def load(repo: str | Path) -> dict[str, list[dict]]:
    """The unified log: `{ref_key: [event, ...]}`, empty when absent. Lock-free (callers that must
    stay consistent with a table+witness write hold the lock themselves, like `record_ideal`)."""
    return state.load_json(repo, _SLOT, default={})


def save(repo: str | Path, table: dict[str, list[dict]]) -> None:
    state.save_json(repo, _SLOT, table)


def _ref_key(repo: Path) -> str | None:
    from sgt.core.lens import _ref_key as ref_key
    from sgt.store.gitbind import GitBinding

    return ref_key(GitBinding(repo))


def append(repo: str | Path, event: dict, *, ref_key: str | None = None) -> None:
    """Append `event` to the log for `ref_key` (the current ref by default). One locked
    read-modify-write, so a concurrent append never loses an entry (R5/R6). Callers must not
    already hold `locked_section` -- the flock is non-reentrant (see `record_ideal`, which does its
    own inline push under the lock it already holds)."""
    repo = Path(repo)
    with locked_section(repo):
        table = load(repo)
        key = ref_key if ref_key is not None else (_ref_key(repo) or "HEAD")
        table.setdefault(key, []).append(event)
        save(repo, table)


def tail(repo: str | Path, *, ref_key: str | None = None) -> dict | None:
    """The newest event for `ref_key` (the current ref by default), or None if the log is empty."""
    repo = Path(repo)
    key = ref_key if ref_key is not None else _ref_key(repo)
    stack = load(repo).get(key, []) if key is not None else []
    return stack[-1] if stack else None


def pop(repo: str | Path, *, ref_key: str | None = None) -> dict | None:
    """Remove and return the newest event for `ref_key`, or None if the log is empty."""
    repo = Path(repo)
    with locked_section(repo):
        table = load(repo)
        key = ref_key if ref_key is not None else _ref_key(repo)
        stack = table.get(key, []) if key is not None else []
        if not stack:
            return None
        event = stack.pop()
        table[key] = stack
        save(repo, table)
        return event


def _drop_event(repo: str | Path, key: str | None, event: dict) -> bool:
    """Remove the newest occurrence of `event` from `key`'s stack under the lock, and persist.
    Returns True if an entry was removed. The reload happens *inside* the lock so that a concurrent
    `append`/`record_ideal` which landed after `undo` read the tail is preserved rather than
    clobbered (R5/R6): `undo` cannot hold the lock across `apply_inverse` (its ideal_edit path
    re-enters `record_ideal`, and the flock is non-reentrant), so it drops the event it just
    inverted in this separate, lock-held read-modify-write. Normally `event` is the tail; if a
    concurrent append pushed a newer event during the apply window, `event` sits below the tail and
    is removed from there, leaving the concurrent event intact."""
    repo = Path(repo)
    with locked_section(repo):
        table = load(repo)
        stack = table.get(key, []) if key is not None else []
        for i in range(len(stack) - 1, -1, -1):
            if stack[i] == event:
                del stack[i]
                table[key] = stack
                save(repo, table)
                return True
        return False


# -- snapshot inverse-descriptor ------------------------------------------------------------------


def snapshot(repo: str | Path, names) -> dict:
    """A snapshot of the named `.sgt` artifacts' bodies (None for one that doesn't exist yet) --
    the inverse-descriptor for a metadata mutation, captured *before* the mutation runs."""
    return {name: state.load_json(repo, name, default=None) for name in names}


def _restore(repo: str | Path, snap: dict) -> None:
    """Restore a `snapshot`: rewrite each artifact to its captured body, or delete it if it did not
    exist at snapshot time (body is None), so the undo reproduces "absent" faithfully too."""
    for name, body in snap.items():
        if body is None:
            p = state.path(repo, name)
            if p.is_file():
                p.unlink()
        else:
            state.save_json(repo, name, body)


# -- undo (reverse-chronological, one kind at a time) ---------------------------------------------


@dataclass(frozen=True)
class UndoOutcome:
    """What one `undo` did. `status` selects the report: `empty` (nothing to undo), `ideal_edit`
    (an ideal was restored -- `ideal` carries the `lens.UndoResult`), `reorg`/`after` (a metadata
    snapshot was restored), or `refused` (a shared-out land/propose whose inverse is not applied)."""

    status: str
    message: str
    ideal: object | None = None  # lens.UndoResult, populated for kind="ideal_edit"
    kind: str | None = None


def undo(repo: str | Path) -> UndoOutcome:
    """Pop the current ref's tail event and apply its inverse (KTD6). Reverse-chronological: each
    call reverses exactly one event. A shared-out `land`/`propose` is *refused* (and left in place,
    so the provenance record survives -- there is no way past it without a rebase). Absorbs current
    reality first (R9)."""
    from sgt.core import lens

    repo = Path(repo)
    lens.get(repo)  # mine-on-contact: absorb any dirty tree / foreign commit first (R9)
    key = _ref_key(repo)
    event = tail(repo, ref_key=key)
    if event is None:
        return UndoOutcome("empty", "nothing to undo -- no recorded ideal edits")

    kind = event["kind"]
    if kind in ("land", "propose"):
        return UndoOutcome(
            "refused",
            f"cannot undo `{kind}` -- it advanced a shared branch that already left this clone; "
            f"undo only reverses local, not-yet-shared operations",
            kind=kind,
        )

    outcome = apply_inverse(repo, event)
    # Applied cleanly -- now drop exactly the event we inverted, in a separate lock-held
    # read-modify-write (`apply_inverse`'s ideal_edit path re-enters `record_ideal`, whose flock is
    # non-reentrant, so we cannot hold the lock across the apply). `_drop_event` reloads inside the
    # lock, so a concurrent append during the apply window is preserved, not clobbered (R5/R6).
    _drop_event(repo, key, event)
    return outcome


def apply_inverse(repo: str | Path, event: dict) -> UndoOutcome:
    """Apply one event's inverse, dispatching on `kind`. A snapshot kind restores its artifacts; an
    ideal_edit re-materializes its prior ideal; a land/propose refuses (its caller in `undo`
    short-circuits before reaching here, so this is defensive)."""
    from sgt.core import lens

    kind = event["kind"]
    if kind == "ideal_edit":
        res = lens._apply_ideal_edit_inverse(repo, event)
        return UndoOutcome("ideal_edit", f"restored {len(res.ideal.op_ids)} op(s)", ideal=res, kind=kind)
    if kind == "feature_reorg":
        _restore(repo, event.get("snapshot", {}))
        return UndoOutcome("reorg", f"reverted feature {event.get('verb', 'reorg')}", kind=kind)
    if kind == "after":
        _restore(repo, event.get("snapshot", {}))
        return UndoOutcome("after", "retracted the declared edge", kind=kind)
    if kind in ("land", "propose"):
        raise ValueError(f"a shared-out `{kind}` cannot be inverted (already left this clone)")
    raise ValueError(f"unknown oplog event kind {kind!r}")
