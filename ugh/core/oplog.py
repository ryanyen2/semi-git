"""The operation log and `ugh undo`.

One per-ref log every mutating verb appends to: `{"kind": "commit", "sha", "verb", "target"}` for a
commit ugh made (a save, a take-out, a bring-back, a resolve), and `names` snapshots taken before a
`name` or `rename`. `undo` pops the newest event and reverses it: a commit is reversed by taking out
the ops mined from it (`ugh.core.planner`), which is itself a commit and is not logged, so repeated
undos walk back; a names event restores its snapshot.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ugh import state
from ugh.core.store import locked_section

_SLOT = "ideal_journal"  # the unified log reuses (repurposes) the ideal_journal slot -- one store.


# -- store (per-ref list of events) ---------------------------------------------------------------


def load(repo: str | Path) -> dict[str, list[dict]]:
    """The log: `{ref_key: [event, ...]}`, empty when absent."""
    return state.load_json(repo, _SLOT, default={})


def save(repo: str | Path, table: dict[str, list[dict]]) -> None:
    state.save_json(repo, _SLOT, table)


def _ref_key(repo: Path) -> str | None:
    from ugh.core.lens import _ref_key as ref_key
    from ugh.store.gitbind import GitBinding

    return ref_key(GitBinding(repo))


def append(repo: str | Path, event: dict, *, ref_key: str | None = None) -> None:
    """Append `event` to the log for `ref_key` (the current ref by default). One locked
    read-modify-write, so a concurrent append never loses an entry. Callers must not already hold
    `locked_section` (the flock is non-reentrant)."""
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


def _drop_event(repo: str | Path, key: str | None, event: dict) -> bool:
    """Remove the newest occurrence of `event` from `key`'s stack under the lock, and persist.
    Returns True if an entry was removed. The reload happens inside the lock, so an append that
    landed while `undo` applied is preserved."""
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


# -- undo -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class UndoOutcome:
    """What one `undo` did: `empty` (nothing to undo), `commit` (`plan` is the applied take-out of
    that commit's ops), `refused` (the take-out applied nothing; `plan` says why), or `names` (a
    names snapshot was restored)."""

    status: str
    message: str
    plan: object | None = None
    kind: str | None = None


def _commit_plan(repo: Path, event: dict):
    from ugh.core import opindex, planner

    ops = {op.id for op in opindex.index_ops(repo) if event["sha"] in op.provenance}
    return planner.plan(repo, ops, target=f"{event['verb']} {event['target']}".strip())


def _what(event: dict) -> str:
    return f"the {event['verb']} {event['target']}".strip()


def preview(repo: str | Path) -> dict:
    """What the next `undo` would do, computed without doing it: `{kind, ok, message, undone,
    held, steps}`, `steps` being the addresses that would go."""
    from ugh.core.op import step_address

    repo = Path(repo)
    event = tail(repo)
    if event is None:
        return {"kind": None, "ok": True, "message": "nothing to undo", "undone": 0, "held": 0,
                "steps": []}
    if event["kind"] == "names":
        return {"kind": "names", "ok": True, "undone": 0, "held": 0, "steps": [],
                "message": f"restores the names from before the {event['verb']} (no code changes)"}
    p = _commit_plan(repo, event)
    return {"kind": "commit", "ok": p.ok and bool(p.files),
            "message": f"takes out {_what(event)}" + (f" -- {p.message}" if p.message else ""),
            "undone": len(p.undone), "held": len(p.held),
            "steps": sorted({step_address(st.symbol, p.saves[st.op][0]) for st in p.undone})[:12]}


def undo(repo: str | Path) -> UndoOutcome:
    """Pop the current ref's newest event and reverse it."""
    from ugh.core import lens, planner

    repo = Path(repo)
    lens.get(repo)
    key = _ref_key(repo)
    event = tail(repo, ref_key=key)
    if event is None:
        return UndoOutcome("empty", "nothing to undo")
    if event["kind"] == "names":
        _restore(repo, event["snapshot"])
        _drop_event(repo, key, event)
        return UndoOutcome("names", f"reverted the {event['verb']}", kind="names")
    p = _commit_plan(repo, event)
    done = planner.apply(repo, p, message=f"ugh undo {event['verb']} {event['target']}".strip(), log=False)
    if done.check != "not_run" and done.check["passed"] and done.files:
        _drop_event(repo, key, event)
        return UndoOutcome("commit", f"took out {_what(event)}", plan=done, kind="commit")
    return UndoOutcome("refused", done.message or f"could not take out {_what(event)}", plan=done,
                       kind="commit")
