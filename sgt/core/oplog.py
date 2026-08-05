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

**`land` is undoable or not depending on where HEAD was, and that is deliberate but easy to
misread.** A land of the *checked-out* branch journals an ordinary `ideal_edit` (via `record_ideal`
with `journal=True`), so `sgt undo` rewinds it like any other local edit -- the shared ref moved,
but so did this clone's HEAD, and undoing produces a normal forward edit on top. A land of a branch
that is *not* checked out appends `kind="land"` instead, and undo refuses: this clone's tree was
restored and the only thing that changed is a ref other people read. So "can I undo a land?" has
one answer per case, not one answer overall -- read the two branches around
`sgt/core/sync/land.py:317` together, and note that `kind="propose"` is accepted here defensively
but is never actually appended (`propose land` delegates to `sync.land` and inherits its
journaling).

This is distinct from `sgt.api.oplog_view`, which projects the *content* op-DAG (the mined ops),
not this user-action history.
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


def _casualty_symbols(repo: str | Path, op_ids: frozenset[str]) -> str:
    """The symbols an `undo` snapshot restore would drop, for the F3 refusal message (0.2c). Maps
    the intervening op ids to their footprint symbols; falls back to the short op ids if an op is no
    longer in the store."""
    from sgt.core.store import Store

    by_id = {op.id: op for op in Store(repo).all_ops()}
    symbols = sorted({sym for oid in op_ids if oid in by_id for sym in by_id[oid].footprint})
    return ", ".join(symbols) if symbols else ", ".join(sorted(o[:8] for o in op_ids))


@dataclass(frozen=True)
class UndoOutcome:
    """What one `undo` did. `status` selects the report: `empty` (nothing to undo), `ideal_edit`
    (an ideal was restored -- `ideal` carries the `lens.UndoResult`), `reorg`/`after` (a metadata
    snapshot was restored), or `refused` (a shared-out land/propose whose inverse is not applied)."""

    status: str
    message: str
    ideal: object | None = None  # lens.UndoResult, populated for kind="ideal_edit"
    kind: str | None = None


def undo(repo: str | Path, force: bool = False) -> UndoOutcome:
    """Pop the current ref's tail event and apply its inverse (KTD6). Reverse-chronological: each
    call reverses exactly one event. A shared-out `land`/`propose` is *refused* (and left in place,
    so the provenance record survives -- there is no way past it without a rebase). Absorbs current
    reality first (R9).

    Two Phase-0 guards protect against the snapshot inverse destroying work (0.2):
      * an `ideal_edit` entry a crashed verb left unapplied (`applied=False`, F6) is *discarded*
        (its edit never landed, so re-materializing its phantom prior ideal would clobber protected
        state) and the real edit beneath it is inverted instead;
      * re-materializing a prior ideal silently drops any op mined into the tree *after* the entry
        was recorded -- a raw commit between the edit and this undo (F3). Undo detects that casualty
        and *refuses* rather than destroy it, unless `force=True`."""
    from sgt.core import lens

    repo = Path(repo)
    lens.get(repo)  # mine-on-contact: absorb any dirty tree / foreign commit first (R9)
    key = _ref_key(repo)

    # Discard any unapplied `ideal_edit` entries a crashed verb left on top (0.2a/F6) before
    # reaching the newest genuine event to reverse.
    while True:
        event = tail(repo, ref_key=key)
        if event is None:
            return UndoOutcome("empty", "nothing to undo -- no recorded ideal edits")
        if event.get("kind") == "ideal_edit" and event.get("applied") is False:
            _drop_event(repo, key, event)
            continue
        break

    kind = event["kind"]
    if kind in ("land", "propose"):
        return UndoOutcome(
            "refused",
            f"cannot undo `{kind}` -- it advanced a shared branch that already left this clone; "
            f"undo only reverses local, not-yet-shared operations",
            kind=kind,
        )

    # F3 guard (0.2c): the ideal_edit inverse re-materializes an absolute snapshot of the prior
    # ideal, dropping any op present now but absent from the edit's own `result` -- i.e. work mined
    # after this entry was recorded. Name it and refuse rather than clobber it, unless forced.
    if kind == "ideal_edit" and not force and "result" in event:
        current = lens.current_ideal(repo)
        intervening = current.op_ids - set(event["result"])
        if intervening:
            return UndoOutcome(
                "refused",
                f"undo would drop work committed after this edit was recorded: "
                f"{_casualty_symbols(repo, intervening)} -- re-run with force to drop it anyway",
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
