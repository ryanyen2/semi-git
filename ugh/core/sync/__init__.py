"""`ugh sync`: bring a teammate's branch in by a merge built unit by unit.

Sync fetches the branch and finds the merge base. A path only one side changed takes that side. A
path both sides changed is split into units (the planner's split: the file head, each top-level
definition, or the whole file), and each unit compares base, ours and theirs: ours equal to base
takes theirs; theirs equal to base, or ours equal to theirs, keeps ours; otherwise `merge3`, and
where that conflicts ours stays and the unit is a two-version choice. A unit added on one side is
taken, units added on both sides at one place keep ours first and then theirs, and a unit deleted
on one side and changed on the other is a choice. The tree is committed as a merge (parents ours,
theirs) with one `Ugh-Choice: <unit>` trailer per choice; theirs is the merge's second parent, so
both versions can always be read from git. A choice is pending until a commit that descends from
its merge names the unit in `Ugh-Resolves: <unit>`.

Tradeoff: the merge is by unit, not by line hunk across a file, so two sides adding different
definitions at one place merge cleanly where git would conflict, in the order ours then theirs. A
person who merges with plain git gets git's result, and ugh accepts it as settled.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ugh.core import lens, oplog, planner, tiers
from ugh.core.op import unit_address
from ugh.core.patch import merge3
from ugh.store.gitbind import GitBinding, GitError, _argv_batches, trailer_values

from . import state_ref

CHOICE_TRAILER = "Ugh-Choice"
RESOLVES_TRAILER = "Ugh-Resolves"


def _trailer(unit: str) -> str:
    return unit.replace(planner._HEAD, "HEAD")


def _unit(value: str) -> str:
    return value[:-4] + planner._HEAD if value.endswith("::__residue__::HEAD") else value


@dataclass(frozen=True)
class Choice:
    """A unit whose merge kept ours while theirs differed. `merge` is that merge commit."""

    unit: str
    merge: str

    @property
    def address(self) -> str:
        return unit_address(self.unit)


@dataclass(frozen=True)
class SyncReport:
    remote: str
    branch: str
    message: str  # "already up to date" | "fast-forwarded" | "merged"
    head: str | None = None
    choices: tuple[str, ...] = ()  # the units this merge left as two-version choices
    name_conflicts: tuple[tuple[str, str, str], ...] = ()  # (op, ours, theirs); ours was kept


def sync(repo, remote: str | None = None, branch: str | None = None) -> SyncReport:
    """Fetch `remote`/`branch` and bring it in: nothing when it is already in HEAD, a fast-forward
    when HEAD is in it, otherwise the merge unit by unit. Names and prompts on the state ref merge
    either way. Refuses a dirty working tree."""
    repo = Path(repo)
    gb = GitBinding(repo)
    if not gb.is_clean():
        raise lens.DirtyWorkingTreeError("ugh sync needs a clean working tree -- `ugh save` first")
    remote = remote or gb.default_remote()
    branch = branch or gb.default_branch()
    if branch is None:
        raise ValueError("no branch to sync -- HEAD has no upstream and isn't on a named branch")
    ours = gb.head()
    if ours is None:
        raise ValueError("ugh sync needs at least one commit")
    gb.fetch(remote, branch)
    theirs = gb.rev_parse("FETCH_HEAD")
    if theirs is None:
        raise GitError(f"fetch of {remote}/{branch} produced no FETCH_HEAD")
    fetched = gb.fetch_ref(remote, f"+{state_ref.STATE_REF}:{state_ref.FETCH_STATE_REF}")
    conflicts = state_ref.merge_remote(gb, repo, gb.rev_parse(state_ref.FETCH_STATE_REF) if fetched else None)

    units: list[str] = []
    if gb.is_ancestor(theirs, ours):
        message = "already up to date"
    elif gb.is_ancestor(ours, theirs):
        gb._git("merge", "-q", "--ff-only", theirs)
        message = "fast-forwarded"
    else:
        units = _merge(repo, gb, ours, theirs, f"ugh sync: merge {remote}/{branch}")
        message = "merged"
    lens.get(repo, unbounded=True)
    return SyncReport(remote, branch, message, gb.head(), tuple(units), tuple(conflicts))


def _merge(repo: Path, gb: GitBinding, ours: str, theirs: str, message: str) -> list[str]:
    """Write the merged tree of `ours` and `theirs` and commit it as their merge. Returns the units
    left as two-version choices."""
    base = gb.merge_base(ours, theirs)
    changed_ours = {p for p in gb.changed_paths(base, ours) if not p.startswith(".sgt/")}
    changed_theirs = {p for p in gb.changed_paths(base, theirs) if not p.startswith(".sgt/")}
    take = sorted(changed_theirs - changed_ours)
    present = [p for p in take if gb.blob_oid(theirs, p) is not None]
    for batch in _argv_batches(present):
        gb._git("checkout", theirs, "--", *batch)
    for batch in _argv_batches(sorted(set(take) - set(present))):
        gb._git("rm", "-q", "--", *batch)

    cfgs = [tiers.load_tiers_at(gb, sha) if sha else None for sha in (base, ours, theirs)]
    units: list[str] = []
    for path in sorted(changed_ours & changed_theirs):
        raws = [gb.blob_bytes(sha, path) if sha else None for sha in (base, ours, theirs)]
        if raws[1] != raws[2]:
            text, chosen = _merge_file(path, raws, cfgs)
            units += chosen
            planner._put_bytes(repo / path, text)
    trailers = "\n".join(f"{CHOICE_TRAILER}: {_trailer(u)}" for u in units) or None
    gb.complete_merge(message, theirs, trailers=trailers)
    return units


def _texts(path: str, f) -> dict[str, bytes]:
    """`unit -> text` of a split file, the head included; empty when the file is absent."""
    if f is None:
        return {}
    if f.whole:
        return dict(f.units)
    return {planner._head_sym(path): f.head, **dict(f.units)}


def _merge_file(path: str, raws, cfgs) -> tuple[bytes | None, list[str]]:
    """One file both sides changed, merged unit by unit: its bytes (None: absent) and the units
    left as choices. A file whole on any side is one unit on every side."""
    files = [planner.split(path, raw, cfg) for raw, cfg in zip(raws, cfgs)]
    if any(f is not None and f.whole for f in files):
        files = [planner._split(path, raw, True) for raw in raws]
    texts = [_texts(path, f) for f in files]
    merged: dict[str, bytes | None] = {}
    chosen: list[str] = []
    for u in dict.fromkeys(u for t in texts for u in t):
        b, o, t = (side.get(u) for side in texts)
        if o == b:
            merged[u] = t
        elif t == b or o == t:
            merged[u] = o
        else:
            m = None if o is None or t is None else merge3(b or b"", o, t)
            if m is None or m.conflicted:
                merged[u] = o
                chosen.append(u)
            else:
                merged[u] = m.merged
    if all(v is None for v in merged.values()):
        return None, chosen
    if path in merged:
        return merged[path], chosen
    order = [u for u, _t in (files[1].units if files[1] else []) if merged.get(u) is not None]
    theirs_order = [u for u, _t in files[2].units] if files[2] else []
    for i, u in enumerate(theirs_order):
        if u in order or merged.get(u) is None:
            continue
        prev = theirs_order[i - 1] if i else None
        at = order.index(prev) + 1 if prev in order else 0
        while at < len(order) and order[at] not in texts[2]:  # ours' own additions here go first
            at += 1
        order.insert(at, u)
    return (merged.get(planner._head_sym(path)) or b"") + b"".join(merged[u] for u in order), chosen


# -- pending choices --------------------------------------------------------------------------

def _trailered(gb: GitBinding, key: str) -> list[tuple[str, list[str]]]:
    """`(sha, units)` for each commit in HEAD's history with a `key:` trailer, newest first."""
    proc = gb._git("log", "--topo-order", "-E", f"--grep=^{key}:", "--format=%H%x1f%B%x1e", "HEAD",
                   check=False)
    out = []
    for record in proc.stdout.split("\x1e")[:-1] if proc.returncode == 0 else ():
        sha, _, body = record.strip("\n").partition("\x1f")
        out.append((sha, [_unit(v) for v in trailer_values(body, key)]))
    return out


def choices(repo) -> list[Choice]:
    """The pending two-version choices, from HEAD's history: each unit a merge named in
    `Ugh-Choice:` that no commit descending from that merge names in `Ugh-Resolves:`."""
    gb = GitBinding(repo)
    if gb.head() is None:
        return []
    made = _trailered(gb, CHOICE_TRAILER)
    resolved = _trailered(gb, RESOLVES_TRAILER) if made else []
    pending: dict[str, Choice] = {}
    for sha, units in made:
        for u in units:
            if u not in pending and not any(u in us and gb.is_ancestor(sha, r) for r, us in resolved):
                pending[u] = Choice(u, sha)
    return list(pending.values())


def versions(repo, c: Choice) -> tuple[bytes | None, bytes | None]:
    """A choice's two versions: the unit as the working tree has it now (ours), and as the
    merge's second parent has it (theirs). None where the unit is absent."""
    gb = GitBinding(repo)
    path = c.unit.split("::", 1)[0]
    full = Path(repo) / path
    now = full.read_bytes() if full.is_file() else None
    theirs = gb.parents(c.merge)[1]
    then = gb.blob_bytes(theirs, path)
    if c.unit == path:
        return now, then
    return (planner._current(planner.split(path, now, tiers.load_tiers(repo)), c.unit),
            planner._current(planner.split(path, then, tiers.load_tiers_at(gb, theirs)), c.unit))


def _write_theirs(repo: Path, gb: GitBinding, c: Choice) -> None:
    """Put theirs' version of the choice's unit into the working file."""
    path = c.unit.split("::", 1)[0]
    _ours, text = versions(repo, c)
    full = repo / path
    if c.unit == path:
        planner._put_bytes(full, text)
        return
    now = planner.split(path, full.read_bytes() if full.is_file() else None, tiers.load_tiers(repo))
    now = now or planner._File(False)
    if c.unit == planner._head_sym(path):
        now.head = text or b""
    elif text is None:
        now.units = [(u, t) for u, t in now.units if u != c.unit]
    elif any(u == c.unit for u, _t in now.units):
        now.units = [(u, text if u == c.unit else t) for u, t in now.units]
    else:
        theirs = gb.parents(c.merge)[1]
        then = planner.split(path, gb.blob_bytes(theirs, path), tiers.load_tiers_at(gb, theirs))
        names = [u for u, _t in then.units]
        prev = names[:names.index(c.unit)]
        here = [u for u, _t in now.units]
        at = next((here.index(u) + 1 for u in reversed(prev) if u in here), 0)
        now.units.insert(at, (c.unit, text))
    planner._put_bytes(full, now.render() if now.head or now.units else None)


def resolve(repo, picked: list[Choice], keep: str, target: str) -> str:
    """Settle the choices `picked`: `keep="theirs"` writes each unit as the merge's second parent
    has it; `"ours"` and `"apply"` keep the file as it is. Commits with one `Ugh-Resolves:`
    trailer per unit and returns the commit. Refused when files other than the units' have
    uncommitted changes."""
    repo = Path(repo)
    gb = GitBinding(repo)
    paths = {c.unit.split("::", 1)[0] for c in picked}
    others = sorted(set(planner._dirty(repo)) - paths)
    if others:
        raise lens.DirtyWorkingTreeError(
            f"resolve would commit other uncommitted changes: {others} -- `ugh save` them first")
    if keep == "theirs":
        for c in picked:
            _write_theirs(repo, gb, c)
    trailers = "\n".join(f"{RESOLVES_TRAILER}: {_trailer(c.unit)}" for c in picked)
    sha = lens.commit(repo, f"ugh resolve {target} ({keep})", trailers)
    oplog.append(repo, {"kind": "commit", "sha": sha, "verb": "resolve", "target": target})
    return sha
