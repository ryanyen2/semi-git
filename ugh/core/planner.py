"""Taking steps out of the code and bringing them back (docs/design/2026-11-16-removal-sets.md §6).

A step is one save's change to one unit. A file's units are its head (everything before the first
definition), each top-level definition with the blank lines before it and the commented lines after
it, or the whole file. A step exists where its unit's text differs between the save and its parent;
its ops are that save's ops whose change shows in the unit. Taking a step out is a new edit: its
change un-applied onto the unit as it is now, by a three-way merge. `plan` computes the edit from
the record alone; `apply` writes it, judges it and commits it with an `Ugh-Out:` trailer naming
every op of the steps it takes out, and the commit is mined like any other. Which steps are out is
derived from those trailers (`out_table`), so a clone knows. Bringing a step back is taking out the
take-out's step on the same unit. The check's run on the candidate is recorded as the take-out
commit's observation (`ugh.core.observe`).
"""

from __future__ import annotations

import os
import re
import subprocess
from collections import Counter
from dataclasses import dataclass, field, replace
from pathlib import Path

from ugh.core import lens, observe, opindex, tiers
from ugh.core.mine import _is_unparseable_whole_file
from ugh.core.op import Op, _symbol_kind, step_address, unit_of
from ugh.core.patch import merge3
from ugh.store.gitbind import GitBinding, trailer_values

OUT_TRAILER = "Ugh-Out"
_HEAD = "\x00HEAD\x00"


@dataclass(frozen=True, order=True)
class Step:
    """One save's change to one unit."""

    sha: str
    unit: str


@dataclass
class Plan:
    verb: str
    target: str
    ok: bool = True
    message: str = ""
    steps: tuple[Step, ...] = ()
    undone: list[Step] = field(default_factory=list)
    held: list[dict] = field(default_factory=list)
    removal_set: dict | None = None
    files: dict[str, tuple[bytes | None, bytes | None]] = field(default_factory=dict)
    saves: dict[str, str] = field(default_factory=dict)
    check: object = "not_run"
    ran: dict[Step, bool | None] = field(default_factory=dict)
    observed: dict | None = None


def _head_sym(path: str) -> str:
    return f"{path}::__residue__::{_HEAD}"


@dataclass
class _File:
    """A file split into units: the head, then each top-level definition with the blank lines
    before it and the commented lines after it. A whole-file path is one unit, the path itself."""

    whole: bool
    head: bytes = b""
    units: list[tuple[str, bytes]] = field(default_factory=list)

    def get(self, sym: str) -> bytes | None:
        return next((t for s, t in self.units if s == sym), None)

    def render(self) -> bytes:
        return self.head + b"".join(t for _s, t in self.units)


def _keep_trailing(gap: bytes, line_ended: bool) -> int:
    """Where a gap splits between the definition before it and the one after: the definition
    before keeps the rest of its last line and every line up to the last one with text; the blank
    lines after that separate the next definition and go with it."""
    rest = 0 if line_ended else (gap.find(b"\n") + 1 or len(gap))
    lines = gap[rest:].splitlines(keepends=True)
    last = max((i for i, line in enumerate(lines) if line.strip()), default=-1)
    return rest + sum(len(line) for line in lines[:last + 1])


def _body(text: bytes | None) -> bytes | None:
    """A unit's text without the blank lines that separate it from the one before."""
    if text is None:
        return None
    lines = text.splitlines(keepends=True)
    skip = next((i for i, line in enumerate(lines) if line.strip()), len(lines))
    return b"".join(lines[skip:])


def _split(path: str, raw: bytes | None, whole: bool) -> _File | None:
    from ugh.entities.extract import extract_file

    if raw is None:
        return None
    if whole:
        return _File(True, units=[(path, raw)])
    tops = sorted((e for e in extract_file(path, raw) if e.container is None), key=lambda e: e.start_byte)
    if not tops:
        return _File(False, head=raw)
    head_end = tops[0].start_byte
    head_text = raw[:head_end]
    head_lines = head_text.splitlines(keepends=True)
    last = max((i for i, line in enumerate(head_lines) if line.strip()), default=-1)
    starts = [sum(len(line) for line in head_lines[:last + 1])]
    for prev, e in zip(tops, tops[1:]):
        line_ended = raw[prev.end_byte - 1:prev.end_byte] == b"\n"
        starts.append(prev.end_byte + _keep_trailing(raw[prev.end_byte:e.start_byte], line_ended))
    ends = starts[1:] + [len(raw)]
    return _File(False, head=raw[:starts[0]],
                 units=[(e.id, raw[st:end]) for e, st, end in zip(tops, starts, ends)])


def split(path: str, raw: bytes | None, cfg) -> _File | None:
    """`raw` read as `path` under tier config `cfg`, split into units; None when absent."""
    if raw is None:
        return None
    whole = tiers.resolve_tier(path, cfg) == "opaque" or _is_unparseable_whole_file(path, raw)
    return _split(path, raw, whole)


def _spans(path: str, f: _File) -> dict[str, range]:
    """The 1-based lines each unit of `f` occupies, the head under its own symbol."""
    spans, start = {}, 1
    for sym, text in [(_head_sym(path), f.head), *f.units]:
        end = start + text.count(b"\n") + (bool(text) and not text.endswith(b"\n"))
        spans[sym] = range(start, end)
        start = end
    return spans


class _Ctx:
    """The record the planner reads: every step of the commits in HEAD's history with its ops, the
    units of files at commits, and which steps are out."""

    def __init__(self, repo):
        self.repo = Path(repo)
        self.gb = GitBinding(self.repo)
        self.by_id = {op.id: op for op in opindex.index_ops(self.repo)}
        rows = self.gb.history()
        self.index = {sha: i for i, (sha, _p, _s) in enumerate(rows)}
        self.parent = {sha: p for sha, p, _s in rows}
        self.subject = {sha: s for sha, _p, s in rows}
        self._files: dict[tuple[str | None, str], _File | None] = {}
        self._tiers: dict[str, object] = {}
        commit = {}
        for op_id in sorted(lens.current_ideal(self.repo).op_ids):
            shas = [s for s in self.by_id[op_id].provenance if s in self.index]
            if shas:
                commit[op_id] = min(shas, key=self.index.__getitem__)
        if commit:  # the record starts at the oldest mined commit (a horizon), mined against nothing
            self.parent[min(commit.values(), key=self.index.__getitem__)] = None
        self.steps: dict[Step, list[str]] = {}
        # ponytail: splits every file an op touched at its commit and its parent; record steps at
        # mine time if large histories make this slow.
        for op_id, sha in commit.items():
            for u in self._units(self.by_id[op_id], sha):
                if self.text(self.parent[sha], u) != self.text(sha, u):
                    self.steps.setdefault(Step(sha, u), []).append(op_id)
        self.of_commit: dict[str, list[Step]] = {}
        self.unit_steps: dict[str, list[Step]] = {}
        for st in sorted(self.steps, key=lambda st: (self.index[st.sha], st.unit)):
            self.of_commit.setdefault(st.sha, []).append(st)
            self.unit_steps.setdefault(st.unit, []).append(st)
        self.out, self.takeouts = _out(self)
        effective = {t.sha for takers in self.out.values() for t in takers}
        self.cancelled = set(self.out) | {st for sha in effective for st in self.of_commit[sha]}

    def _units(self, op: Op, sha: str) -> set[str]:
        """The units an op's change can show in: the unit of each symbol it touched, and for a gap,
        the definition after it too (the blank lines before a definition are part of it)."""
        units = {unit_of(s) for s in op.footprint if _symbol_kind(s) != "anchor"}
        for sym in op.footprint:
            if _symbol_kind(sym) != "residue":
                continue
            path = sym.split("::", 1)[0]
            prev = None if sym == _head_sym(path) else unit_of(sym)
            for at in (sha, self.parent[sha]):
                f = self.file(at, path)
                names = [s for s, _t in f.units] if f is not None and not f.whole else []
                i = 0 if prev is None else names.index(prev) + 1 if prev in names else len(names)
                units |= set(names[i:i + 1])
        return units

    def file(self, sha: str | None, path: str) -> _File | None:
        """`path` split into units at commit `sha` (None: before history)."""
        key = (sha, path)
        if key not in self._files:
            raw = self.gb.blob_bytes(sha, path) if sha is not None else None
            if raw is not None and sha not in self._tiers:
                self._tiers[sha] = tiers.load_tiers_at(self.gb, sha)
            self._files[key] = split(path, raw, self._tiers.get(sha))
        return self._files[key]

    def text(self, sha: str | None, unit: str) -> bytes | None:
        """The text of `unit` at commit `sha`, or None where it does not exist."""
        return _current(self.file(sha, unit.split("::", 1)[0]), unit)

    def before(self, st: Step) -> bytes | None:
        return self.text(self.parent[st.sha], st.unit)

    def after(self, st: Step) -> bytes | None:
        return self.text(st.sha, st.unit)

    def kind(self, st: Step) -> str:
        """create, change or delete, from the unit's text at the save's parent and at the save."""
        return "create" if self.before(st) is None else "delete" if self.after(st) is None else "change"

    def later(self, st: Step, exclude) -> list[Step]:
        """The steps on `st`'s unit after it: not in `exclude`, not out, not a take-out. A change to
        the blank lines before the definition only (a neighbour inserted or removed before it) is not
        a change to it."""
        return [s for s in self.unit_steps[st.unit]
                if self.index[s.sha] > self.index[st.sha] and s not in exclude and s not in self.cancelled
                and _body(self.before(s)) != _body(self.after(s))]

    def save_steps(self, sha: str) -> list[Step]:
        """The steps of save `sha` that are in the code."""
        return [s for s in self.of_commit.get(sha, ()) if s not in self.out]


def out_table(repo) -> dict[Step, set[Step]]:
    """Which steps of saves are out, from history: step -> the take-out steps holding it out. A
    take-out's own step that is out was brought back, so it is not listed."""
    ctx = _Ctx(repo)
    return {st: t for st, t in ctx.out.items() if st.sha not in ctx.takeouts}


def _out(ctx: _Ctx) -> tuple[dict[Step, set[Step]], set[str]]:
    """A take-out commit names ops in its `Ugh-Out:` trailer. A step with one of them is out while
    that commit is in effect on its unit: the commit has a step there that is not itself out. Newest
    commit first, so each take-out is judged after every take-out that could have taken it out.
    Returns the out steps with their takers, and the take-out commits."""
    proc = ctx.gb._git("log", "--topo-order", "-E", f"--grep=^{OUT_TRAILER}:",
                       "--format=%H%x1f%B%x1e", "HEAD", check=False)
    out: dict[Step, set[Step]] = {}
    takeouts: set[str] = set()
    for record in proc.stdout.split("\x1e")[:-1] if proc.returncode == 0 else ():
        sha, _, body = record.strip("\n").partition("\x1f")
        named = set(trailer_values(body, OUT_TRAILER))
        takeouts.add(sha)
        for t in ctx.of_commit.get(sha, ()):
            if t not in out:
                for s in ctx.unit_steps[t.unit]:
                    if named.intersection(ctx.steps[s]):
                        out.setdefault(s, set()).add(t)
    return out, takeouts


def _hold(step: Step, reason: str, holders, now: bytes | None = None,
          would: bytes | None = None) -> dict:
    return {"step": step, "reason": reason, "holders": list(dict.fromkeys(holders)),
            "now": now, "would": would}


def _removed_by(ctx: _Ctx, st: Step) -> list[Step]:
    """Who stands in the way of a step whose unit is gone: the later step that removed it, and for
    a removal that was itself a take-out, the steps it took out."""
    gone = [s for s in ctx.unit_steps[st.unit]
            if ctx.index[s.sha] > ctx.index[st.sha] and ctx.after(s) is None]
    return [n for s in gone[-1:] for n in
            ([s] if s not in ctx.cancelled else [o for o, takers in ctx.out.items() if s in takers])]


def _current(now: _File | None, unit: str) -> bytes | None:
    """The text of `unit` in the split file `now`, or None where it does not exist."""
    path = unit.split("::", 1)[0]
    if now is None:
        return None
    if unit == path:
        return now.units[0][1] if now.whole else None
    if now.whole:
        return None
    return now.head if unit == _head_sym(path) else now.get(unit)


def _undo(ctx: _Ctx, st: Step, now: _File | None, exclude) -> tuple[str, bytes | None] | dict:
    """Un-apply one step onto `now`: `(action, text)` with action remove, keep, replace or insert,
    or a hold."""
    cur = _current(now, st.unit)
    before, after = ctx.before(st), ctx.after(st)
    if before is None:
        later = ctx.later(st, exclude)
        if later:
            return _hold(st, "changed_later", later)
        return ("remove", None) if cur is not None else ("keep", None)
    if after is None:
        if cur is None:
            return ("insert", before)
        return _hold(st, "changed_later", ctx.later(st, exclude), cur, before)
    if cur is None:
        return _hold(st, "changed_later", _removed_by(ctx, st))
    m = merge3(after, cur, before)
    if not m.conflicted:
        return ("replace", m.merged)
    holder = next((s for s in ctx.later(st, exclude)
                   if merge3(after, ctx.after(s) or b"", before).conflicted), None)
    return _hold(st, "same_lines", [holder] if holder else [], cur, before)


def _candidate(ctx: _Ctx, steps, forced: dict[Step, dict], keep: set[str] = frozenset()):
    """Undo `steps` newest first, except those in `forced`. Returns (undone, held, files, removed,
    stays) where `files` is path -> (now, candidate) for every path that changes, `removed` the
    units whose creation was undone, and `stays` the holds of steps that created a file other work
    keeps. A unit in `keep` is left in place though its creation is undone."""
    exclude = set(steps)
    held = {s: forced[s] for s in steps if s in forced}
    undone: list[Step] = []
    removed: set[str] = set()
    created_file: dict[str, Step] = {}
    recreated: set[str] = set()
    stays: dict[Step, dict] = {}
    by_path: dict[str, list[Step]] = {}
    for st in steps:
        by_path.setdefault(st.unit.split("::", 1)[0], []).append(st)
    files: dict[str, tuple[bytes | None, bytes | None]] = {}
    for path, group in sorted(by_path.items()):
        now = ctx.file("HEAD", path)
        work = None if now is None else _File(now.whole, now.head, list(now.units))
        inserts = []
        file_level = (path, _head_sym(path))  # the file comes and goes before its definitions
        for st in sorted(group, key=lambda s: (s.unit not in file_level, s.unit, -ctx.index[s.sha])):
            if st in held:
                continue
            result = _undo(ctx, st, work, exclude)
            if isinstance(result, dict):
                held[st] = result
                continue
            action, text = result
            undone.append(st)
            if action == "keep":
                continue
            if st.unit == path:
                work = None if action == "remove" else _File(True, units=[(path, text)])
                if action == "remove":
                    created_file[path] = st
            elif st.unit == _head_sym(path):
                if work is None:
                    work = _File(False)
                if action == "remove":
                    work.head = b""
                    created_file[path] = st
                else:
                    work.head = text
                    recreated.add(path)
            elif action == "remove":
                removed.add(st.unit)
                if st.unit not in keep:
                    work.units = [(s, t) for s, t in work.units if s != st.unit]
            elif action == "replace":
                work.units = [(s, text if s == st.unit else t) for s, t in work.units]
            elif action == "insert":
                if work is None:
                    work = _File(False)
                work.units.append((st.unit, text))
                inserts.append(st)
        # A definition brought back goes after the nearest one that preceded it and is still there.
        for st in sorted(inserts, key=lambda st: (ctx.index[st.sha], len(_preceding(ctx, st)))):
            unit = next((u for u in work.units if u[0] == st.unit), None)
            if unit is None:
                continue
            work.units.remove(unit)
            names = [s for s, _t in work.units]
            at = next((names.index(s) + 1 for s in reversed(_preceding(ctx, st)) if s in names), 0)
            work.units.insert(at, unit)
        after = None if work is None else work.render()
        if after and path in created_file:
            st = created_file[path]
            stays[st] = _hold(st, "changed_later", [
                s for u, _t in work.units for s in ctx.unit_steps.get(u, ())
                if ctx.index[s.sha] > ctx.index[st.sha] and s not in exclude and s not in ctx.cancelled])
        if after == b"" and path not in recreated and (path in created_file
                                                       or _creator(ctx, path) in ctx.out):
            after = None
        before = None if now is None else now.render()
        if before != after:
            files[path] = (before, after)
    ordered = [s for s in steps if s in held]
    return [s for s in steps if s in undone], [held[s] for s in ordered], files, removed, stays


def _creator(ctx: _Ctx, path: str) -> Step | None:
    """The step that last created the file (a whole file, or its head)."""
    created = [s for unit in (path, _head_sym(path)) for s in ctx.unit_steps.get(unit, ())
               if ctx.before(s) is None and ctx.after(s) is not None]
    return max(created, key=lambda s: ctx.index[s.sha], default=None)


def _preceding(ctx: _Ctx, st: Step) -> list[str]:
    """The units before `st`'s unit in its file, as the file was before the step."""
    prior = ctx.file(ctx.parent[st.sha], st.unit.split("::", 1)[0])
    names = [s for s, _t in prior.units] if prior else []
    return names[:names.index(st.unit)] if st.unit in names else []


def _codebase(ctx: _Ctx, files) -> dict[str, str]:
    """The HEAD tree with `files`' candidate texts, for the entity graph."""
    tree = ctx.gb.tree_at("HEAD")
    for path, (_b, a) in files.items():
        if a is None:
            tree.pop(path, None)
        else:
            try:
                tree[path] = a.decode("utf-8")
            except UnicodeDecodeError:
                tree.pop(path, None)
    return tree


def _compute(ctx: _Ctx, steps, forced: dict[Step, dict]):
    """Undo `steps`, then hold every step that created a file other work keeps and every created
    definition that remaining code still uses, and undo again until nothing more is held. Returns
    (undone, held, files)."""
    from ugh.entities.graph import build_entity_graph

    forced = dict(forced)
    while True:
        undone, held, files, removed, stays = _candidate(ctx, steps, forced)
        if stays:
            forced.update(stays)
            continue
        if not removed:
            return undone, held, files
        _, _, kept, _, _ = _candidate(ctx, steps, forced, keep=removed)
        refs: dict[str, set[str]] = {}
        for e in build_entity_graph(_codebase(ctx, kept)).edges:
            if e.type == "uses" and unit_of(e.dst) in removed and unit_of(e.src) != unit_of(e.dst):
                refs.setdefault(unit_of(e.dst), set()).add(unit_of(e.src))
        standing: dict[str, list[str]] = {}
        moving = True
        while moving:
            moving = False
            for d in sorted(removed):
                by = sorted(r for r in refs.get(d, ()) if r not in removed)
                if by:
                    standing[d] = by
                    removed.discard(d)
                    moving = True
        if not standing:
            return undone, held, files
        for st in undone:
            if st.unit in standing and ctx.kind(st) == "create":
                forced[st] = _hold(st, "uses", [h for r in standing[st.unit]
                                                for h in _introducer(ctx, r, st.unit)])


def _introducer(ctx: _Ctx, referrer: str, unit: str) -> list[Step]:
    """The earliest step on the referrer's definition with an op whose `requires` names the unit
    (any version)."""
    for s in ctx.unit_steps.get(referrer, ()):
        if s not in ctx.cancelled and any(unit_of(sym) == unit for o in ctx.steps[s]
                                          for sym, _v in ctx.by_id[o].requires):
            return [s]
    return []


def _plan_steps(ctx: _Ctx, steps, verb: str, target: str, forced: dict[Step, dict]) -> Plan:
    undone, held, files = _compute(ctx, steps, forced)
    return Plan(verb, target, steps=tuple(steps), undone=undone, held=held, files=files)


def _removal_set(ctx: _Ctx, x: list[Step]) -> list[Step]:
    """X plus everything that stands on it, closed, never cutting another save in two: another
    save's step joins alone only when it sits on a unit X created; otherwise all that save's steps
    join. The steps of the set that are in the code."""
    born = {st.unit for st in x if ctx.before(st) is None}
    members = set(x)
    while True:
        steps = sorted(st for st in members if st not in ctx.out)
        _, held, _ = _compute(ctx, steps, {})
        grow: set[Step] = set()
        for h in {h for hd in held for h in hd["holders"]} - members:
            grow |= {h} if h.unit in born else set(ctx.save_steps(h.sha))
        if grow <= members:
            return steps
        members |= grow


def ran(ctx: _Ctx, obs: dict | None, st: Step, at: str | None) -> bool | None:
    """Whether observation `obs` of commit `at` ran a line of `st`'s unit in that commit's tree
    (`observe.ran`)."""
    path = st.unit.split("::", 1)[0]
    f = ctx.file(at, path)
    return observe.ran(obs, path, None if f is None else _spans(path, f).get(st.unit))


def _observe(ctx: _Ctx, p: Plan, obs: dict | None, at: str | None, **after) -> Plan:
    """Mark each undone and held step with whether `obs`, the observation of commit `at`, ran it;
    `after` joins `observed` once the plan was applied. `observed` is None when the project
    configures no check."""
    p.ran = {st: ran(ctx, obs, st, at) for st in [*p.undone, *(hd["step"] for hd in p.held)]}
    p.observed = {"head": observe.summary(obs), **after} if observe.configured(ctx.repo) else None
    return p


def plan(repo, steps, *, verb: str = "revert", target: str = "", whole: bool = False,
         removal_set: bool = False) -> Plan:
    """The plan for taking `steps` out (`revert`) or bringing them back (`restore`), from the
    record alone; nothing is written. `whole` takes out the removal set; `removal_set` reports it.
    Each undone and held step says whether HEAD's recorded observation ran it."""
    ctx = _Ctx(repo)
    head = ctx.gb.head()
    p = _answer(ctx, steps, verb, target, whole, removal_set)
    return _observe(ctx, p, observe.load(repo, head), head)


def _answer(ctx: _Ctx, steps, verb: str, target: str, whole: bool, removal_set: bool) -> Plan:
    steps = sorted(st for st in steps if st in ctx.steps)
    if verb == "restore":
        takers = sorted({t for st in steps for t in ctx.out.get(st, ())})
        if not takers:
            return Plan(verb, target, message=f"nothing of {target} is out; nothing to restore")
        p = _plan_steps(ctx, takers, verb, target, {})
    else:
        x = [st for st in steps if st not in ctx.out]
        if not x:
            return Plan(verb, target, message=f"{target} is already out; nothing to revert"
                        if steps else f"none of {target} is in the code; nothing to revert")
        rs = _removal_set(ctx, x) if whole or removal_set else None
        p = _plan_steps(ctx, rs if whole else x, verb, target, {})
        if rs is not None:
            counts = Counter(st.sha for st in rs)
            p.removal_set = {"saves": [{"sha": sha[:7], "subject": ctx.subject[sha], "steps": n}
                                       for sha, n in sorted(counts.items())], "steps": len(rs)}
    p.saves = {st.sha: ctx.subject[st.sha] for st in [*p.steps, *(h for hd in p.held for h in hd["holders"])]}
    return p


# -- the judge -------------------------------------------------------------------------------

_LOCATIONS = (re.compile(r'File "([^"]+)", line (\d+)'),
              re.compile(r"([\w./\\-]+\.[A-Za-z]+)\((\d+),\d+\)"),
              re.compile(r"([\w./\\-]+\.[A-Za-z]+):(\d+)"))


def _error_lines(path: str, data: bytes | None) -> list[int]:
    from tree_sitter import Parser

    from ugh.entities.extract import _language, _language_for

    lang = _language_for(path)
    if lang is None or not data:
        return []
    lines, stack = [], [Parser(_language(lang)).parse(data).root_node]
    while stack:
        node = stack.pop()
        if node.type == "ERROR" or node.is_missing:
            lines.append(node.start_point[0] + 1)
        elif node.has_error:
            stack.extend(node.children)
    return sorted(set(lines))


def _judge(repo: Path, p: Plan, checked: bool) -> dict:
    """The parser (no file that parsed may stop parsing), then the project's check, on the tree
    `p` wrote, with what the check observed. `locations` are the failing (path, line) pairs on
    paths the plan changed."""
    fails = [(path, line) for path, (b, a) in sorted(p.files.items())
             if b is not None and not _error_lines(path, b) for line in _error_lines(path, a)]
    if fails:
        return {"judge": "parser", "passed": False, "locations": fails,
                "output": "".join(f"{path}:{line}: does not parse\n" for path, line in fails)}
    if not checked:
        return {"judge": "parser", "passed": True, "locations": [], "output": ""}
    obs = observe.check(repo)
    passed, output = obs["exit"] == 0, obs["output"]
    root = os.path.realpath(repo)
    locs = []
    for pattern in _LOCATIONS:
        for raw, line in pattern.findall(output):
            rel = os.path.relpath(os.path.realpath(os.path.join(root, raw)), root)
            if rel in p.files:
                locs.append((rel, int(line)))
    return {"judge": "check", "passed": passed, "locations": [] if passed else locs,
            "output": output[-4000:], "observation": obs}


def _attribute(p: Plan, verdict: dict) -> set[Step]:
    """The undone steps whose unit holds a failing line in the candidate."""
    going: dict[str, list[Step]] = {}
    for st in p.undone:
        going.setdefault(st.unit, []).append(st)
    hits: set[Step] = set()
    for path, line in verdict["locations"]:
        text = p.files[path][1]
        f = _split(path, text, False) if text is not None else None
        if f is None:
            continue
        unit = next((sym for sym, span in _spans(path, f).items() if line in span), None)
        hits |= set(going.get(unit, ()))
    return hits


def _dirty(repo: Path) -> list[str]:
    """Uncommitted paths ugh mines (ignored-tier paths and `.sgt/` do not count)."""
    proc = subprocess.run(["git", "-C", str(repo), "status", "--porcelain", "-z", "--untracked-files=all"],
                          capture_output=True, text=True, check=True)
    cfg = tiers.load_tiers(repo)
    paths = [entry[3:] for entry in proc.stdout.split("\0") if len(entry) > 3]
    return sorted(p for p in paths if not p.startswith(".sgt/") and tiers.resolve_tier(p, cfg) != "ignored")


def apply(repo, p: Plan, message: str | None = None, *, log: bool = True) -> Plan:
    """Write the plan's candidate, judge it, hold the steps the failures sit in and try again, then
    commit once with an `Ugh-Out:` trailer. The commit is mined like any other. The check must pass
    on the current tree first; a failure that sits in no undone step applies nothing. On any error
    before the commit returns, every touched file is put back. What the check observed on the
    current tree and on the commit is recorded as their observations, and `observed` gives the
    output delta between them and whether it reverses the deltas of the saves taken out."""
    if not p.files:
        return p
    from ugh.core import oplog
    from ugh.core.sync import state_ref

    repo = Path(repo)
    dirty = _dirty(repo)
    if dirty:
        raise lens.DirtyWorkingTreeError(f"{p.verb} would overwrite uncommitted changes: {dirty}")
    ctx = _Ctx(repo)
    head = ctx.gb.head()
    baseline = observe.check(repo)
    if baseline is not None and baseline["exit"]:
        refused = replace(p, ok=False, message="the check fails on the current tree; nothing applied",
                          check={"judge": "check", "passed": False, "rounds": 0,
                                 "output": baseline["output"][-4000:]})
        return _observe(ctx, refused, baseline, head, delta=None, saves_delta=None, reverses=None)
    saved: dict[str, bytes | None] = {}

    def write(files) -> None:
        for path in list(saved):
            if path not in files:
                _put_bytes(repo / path, saved.pop(path))
        for path, (_b, a) in files.items():
            if path not in saved:
                full = repo / path
                saved[path] = full.read_bytes() if full.is_file() else None
            _put_bytes(repo / path, a)

    forced: dict[Step, dict] = {}
    current = p
    rounds = 0
    try:
        while current.files:
            write(current.files)
            verdict = _judge(repo, current, baseline is not None)
            rounds += 1
            if verdict["passed"]:
                break
            hits = _attribute(current, verdict) - set(forced)
            if not hits:
                break
            forced.update((st, _hold(st, "check", [])) for st in hits)
            current = _plan_steps(ctx, p.steps, p.verb, p.target, forced)
        check = {"judge": verdict["judge"], "passed": verdict["passed"], "rounds": rounds,
                 "output": verdict["output"]}
        current = replace(current, check=check, removal_set=p.removal_set,
                          saves={**p.saves, **{h.sha: ctx.subject[h.sha] for hd in current.held
                                               for h in hd["holders"]}})
        _observe(ctx, current, baseline, head, delta=None, saves_delta=None, reverses=None)
        if not verdict["passed"] or not current.files:
            write({})
            if not verdict["passed"]:
                current.message = "the check fails in code this plan does not undo; nothing applied"
            return current
        trailers = "\n".join(f"{OUT_TRAILER}: {o}" for o in sorted({o for st in current.undone
                                                                     for o in ctx.steps[st]}))
        from ugh.store.gitbind import format_bookkeeping_trailer

        sha = ctx.gb.commit_all(message or f"ugh {p.verb} {p.target}",
                                trailers=f"{trailers}\n{format_bookkeeping_trailer()}")
    except BaseException:
        write({})
        raise
    lens.get(repo, unbounded=True)
    state_ref.publish_from_local(ctx.gb, repo)
    if log:
        oplog.append(repo, {"kind": "commit", "sha": sha, "verb": p.verb, "target": p.target})
    after = verdict.get("observation")
    if after is not None:
        d = observe.delta(baseline, after)
        saves = [(ctx.parent[s], s) for s in sorted({st.sha for st in current.undone})]
        own = observe.saves_delta(repo, saves, {head: baseline})
        current.observed.update(delta=observe.counts(d),
                                saves_delta=None if own is None else observe.counts(own),
                                reverses=observe.reverses(d, own))
        try:
            observe.record(repo, head, baseline)
            observe.record(repo, sha, after)
        except OSError as e:
            current.observed["not_recorded"] = str(e)
    return current


def _put_bytes(full: Path, data: bytes | None) -> None:
    if data is None:
        if full.is_file():
            full.unlink()
        return
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_bytes(data)


def to_json(p: Plan, *, applied: bool = False) -> dict:
    """The machine-readable plan: what goes, what is held and why (every step and holder with its
    address), whether the check ran each step, the removal set, the files before and after, the
    check, and what it observed."""
    def step(st: Step) -> dict:
        return {"address": step_address(st.unit, st.sha), "save": st.sha[:7],
                "subject": p.saves.get(st.sha, "")}

    def text(data: bytes | None) -> str | None:
        return None if data is None else data.decode("utf-8", "replace")

    held = []
    for hd in p.held:
        st = hd["step"]
        row = {**step(st), "unit": st.unit, "ran": p.ran.get(st), "reason": hd["reason"],
               "holders": [step(h) for h in hd["holders"]]}
        if hd["now"] is not None or hd["would"] is not None:
            row["now"], row["would"] = text(hd["now"]), text(hd["would"])
        held.append(row)
    return {
        "ok": p.ok, "applied": applied, "verb": p.verb, "target": p.target, "message": p.message,
        "undone": [{**step(st), "unit": st.unit, "ran": p.ran.get(st)} for st in p.undone],
        "held": held,
        "removal_set": p.removal_set,
        "files": {path: {"before": text(b), "after": text(a)} for path, (b, a) in sorted(p.files.items())},
        "check": p.check,
        "observed": p.observed,
    }
