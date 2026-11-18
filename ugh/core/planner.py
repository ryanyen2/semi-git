"""Taking steps out of the code and bringing them back (docs/design/2026-11-16-removal-sets.md §6).

A step is one op's change to one unit: a top-level definition with the gap that follows it, the
file head (everything before the first definition), or a whole file. Taking a step out is a new
edit: its change un-applied onto the unit as it is now, by a three-way merge. `plan` computes the
edit from the record alone; `apply` writes it, judges it and commits it with an `Ugh-Out:` trailer
naming the steps it takes out, and the commit is mined like any other. Which steps are out is
derived from those trailers (`out_table`), so a clone knows. Bringing steps back is taking out the
ops of the commits that took them out.
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass, field, replace
from pathlib import Path

from ugh.core import lens, opindex, tiers
from ugh.core.mine import _is_unparseable_whole_file
from ugh.core.op import Op, _symbol_kind, unit_of
from ugh.core.patch import merge3
from ugh.store.gitbind import GitBinding, parse_trailer_ids

OUT_TRAILER = "Ugh-Out"
_HEAD = "\x00HEAD\x00"


@dataclass(frozen=True, order=True)
class Step:
    """One op's change to one unit; `kind` is create, change or delete."""

    op: str
    symbol: str
    kind: str


@dataclass
class Plan:
    verb: str
    target: str
    ok: bool = True
    message: str = ""
    steps: tuple[Step, ...] = ()
    ops: frozenset[str] = frozenset()
    undone: list[Step] = field(default_factory=list)
    held: list[dict] = field(default_factory=list)
    removal_set: dict | None = None
    files: dict[str, tuple[bytes | None, bytes | None]] = field(default_factory=dict)
    saves: dict[str, tuple[str, str]] = field(default_factory=dict)
    check: object = "not_run"


def _head_sym(path: str) -> str:
    return f"{path}::__residue__::{_HEAD}"


def units_of(op: Op) -> set[str]:
    """The units an op touched (an anchor fact alone changes no text, so it is not one)."""
    return {unit_of(s) for s in op.footprint if _symbol_kind(s) != "anchor"}


@dataclass
class _File:
    """A file split into units: the head, then each top-level definition with its trailing gap.
    A whole-file path is one unit, the path itself."""

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


class _Ctx:
    """The record the planner reads: the current ideal, the commit each op was mined from, the
    units of files at commits, and which steps are out."""

    def __init__(self, repo):
        self.repo = Path(repo)
        self.gb = GitBinding(self.repo)
        self.ops = opindex.index_ops(self.repo)
        self.by_id = {op.id: op for op in self.ops}
        self.ideal = lens.current_ideal(self.repo).op_ids
        rows = self.gb.history()
        self.index = {sha: i for i, (sha, _p, _s) in enumerate(rows)}
        self.parent = {sha: p for sha, p, _s in rows}
        self.subject = {sha: s for sha, _p, s in rows}
        self.commit: dict[str, str] = {}
        self.of_commit: dict[str, list[str]] = {}
        self._files: dict[tuple[str | None, str], _File | None] = {}
        self._tiers: dict[str, object] = {}
        self._units: dict[str, set[str]] = {}
        self.unit_ops: dict[str, list[str]] = {}
        for op_id in sorted(self.ideal, key=lambda o: (self._when_of(o), o)):
            sha = self._first_commit(op_id)
            if sha is None:
                continue
            self.commit[op_id] = sha
            self.of_commit.setdefault(sha, []).append(op_id)
        for op_ids in self.of_commit.values():
            for op_id in op_ids:
                for u in self.units(op_id):
                    self.unit_ops.setdefault(u, []).append(op_id)
        for ops in self.unit_ops.values():
            ops.sort(key=lambda o: (self.when(o), o))
        self.out = _out(self)
        effective = {self.commit[t] for takers in self.out.values() for t in takers}
        self.cancelled = set(self.out) | {o for sha in effective for o in self.of_commit[sha]}

    def _first_commit(self, op_id: str) -> str | None:
        shas = [s for s in self.by_id[op_id].provenance if s in self.index]
        return min(shas, key=self.index.__getitem__) if shas else None

    def _when_of(self, op_id: str) -> int:
        sha = self._first_commit(op_id)
        return self.index[sha] if sha is not None else -1

    def when(self, op_id: str) -> int:
        return self.index[self.commit[op_id]]

    def units(self, op_id: str) -> set[str]:
        """The units an op's change can show in: the miner's units, and for a gap it changed, the
        definition after the gap too (the blank lines before a definition are part of it)."""
        if op_id not in self._units:
            op = self.by_id[op_id]
            out = units_of(op)
            sha = self.commit.get(op_id)
            for sym in op.footprint if sha is not None else ():
                if _symbol_kind(sym) != "residue":
                    continue
                path = sym.split("::", 1)[0]
                prev = None if sym == _head_sym(path) else unit_of(sym)
                # ponytail: splits every file a gap op touched at its commit and parent; index
                # gap ownership at mine time if large histories make this slow.
                for at in (sha, self.parent[sha]):
                    f = self.file(at, path)
                    names = [s for s, _t in f.units] if f is not None and not f.whole else []
                    i = 0 if prev is None else names.index(prev) + 1 if prev in names else len(names)
                    out |= set(names[i:i + 1])
            self._units[op_id] = out
        return self._units[op_id]

    def file(self, sha: str | None, path: str) -> _File | None:
        """`path` split into units at commit `sha` (None: before history)."""
        key = (sha, path)
        if key not in self._files:
            raw = self.gb.blob_bytes(sha, path) if sha is not None else None
            if raw is None:
                self._files[key] = None
            else:
                if sha not in self._tiers:
                    self._tiers[sha] = tiers.load_tiers_at(self.gb, sha)
                tier = tiers.resolve_tier(path, self._tiers[sha])
                whole = tier == "opaque" or _is_unparseable_whole_file(path, raw)
                self._files[key] = _split(path, raw, whole)
        return self._files[key]

    def text(self, sha: str | None, unit: str) -> bytes | None:
        """The text of `unit` at commit `sha`, or None where it does not exist."""
        path = unit.split("::", 1)[0]
        f = self.file(sha, path)
        if f is None:
            return None
        if unit == path:
            return f.units[0][1] if f.whole else None
        if f.whole:
            return None
        return f.head if unit == _head_sym(path) else f.get(unit)

    def before(self, op_id: str, unit: str) -> bytes | None:
        return self.text(self.parent[self.commit[op_id]], unit)

    def after(self, op_id: str, unit: str) -> bytes | None:
        return self.text(self.commit[op_id], unit)

    def later(self, step: Step, exclude) -> list[str]:
        """The ops after `step` that changed its unit: not in `exclude`, not out, not a take-out.
        A change to the blank lines before the definition only (a neighbour inserted or removed
        before it) is not a change to it."""
        start = self.when(step.op)
        return [o for o in self.unit_ops.get(step.symbol, ())
                if self.when(o) > start and o not in exclude and o not in self.cancelled
                and _body(self.before(o, step.symbol)) != _body(self.after(o, step.symbol))]

    def save(self, op_id: str) -> tuple[str, str]:
        sha = self.commit[op_id]
        return sha[:7], self.subject.get(sha, "")

    def save_ops(self, op_id: str) -> set[str]:
        return {o for o in self.of_commit[self.commit[op_id]] if o not in self.out}


def out_table(repo) -> dict[str, set[str]]:
    """Which steps are out, from history: step op id -> the ops that took it out."""
    return _Ctx(repo).out


def _out(ctx: _Ctx) -> dict[str, set[str]]:
    """A commit's `Ugh-Out:` trailer takes out the steps it names; a named step stays out while
    that commit is in effect: its op on the step's unit (or, when it changed nothing there, any of
    its ops) is not itself out. Newest commit first, so each commit is judged after every commit
    that could have taken its own ops out."""
    proc = ctx.gb._git("log", "--topo-order", "-E", f"--grep=^{OUT_TRAILER}:",
                       "--format=%H%x1f%B%x1e", "HEAD", check=False)
    out: dict[str, set[str]] = {}
    for record in proc.stdout.split("\x1e") if proc.returncode == 0 else ():
        sha, _, body = record.strip("\n").partition("\x1f")
        own = ctx.of_commit.get(sha, [])
        for named in parse_trailer_ids(body, OUT_TRAILER):
            if named not in ctx.by_id:
                continue
            units = ctx.units(named) if named in ctx.commit else units_of(ctx.by_id[named])
            touching = {o for o in own if ctx.units(o) & units}
            live = {o for o in touching or own if o not in out}
            if live:
                out.setdefault(named, set()).update(live)
    return out


def steps_of(ctx: _Ctx, op_ids) -> tuple[list[Step], frozenset[str]]:
    """The steps of `op_ids` that are in the code (in the ideal and not out), and those ops."""
    ids = [o for o in sorted(op_ids) if o in ctx.commit and o not in ctx.out]
    found: dict[tuple[str, str], Step] = {}
    for o in ids:
        for u in sorted(ctx.units(o)):
            b, a = ctx.before(o, u), ctx.after(o, u)
            key = (ctx.commit[o], u)
            if b != a and (key not in found or u in units_of(ctx.by_id[o])):
                found[key] = Step(o, u, "create" if b is None else "delete" if a is None else "change")
    return sorted(found.values(), key=lambda st: (st.symbol, st.op)), frozenset(ids)


def _hold(step: Step, reason: str, holders, now: bytes | None = None,
          would: bytes | None = None) -> dict:
    return {"step": step, "reason": reason, "holders": list(dict.fromkeys(holders)),
            "now": now, "would": would}


def _removed_by(ctx: _Ctx, step: Step) -> list[str]:
    """Who stands in the way of a step whose unit is gone: the later ops that removed it, and for
    a removal that was itself a take-out, the steps it took out."""
    gone = [o for o in ctx.unit_ops.get(step.symbol, ())
            if ctx.when(o) > ctx.when(step.op) and ctx.after(o, step.symbol) is None]
    return [n for o in gone[-1:] for n in
            ([o] if o not in ctx.cancelled else
             [s for s, t in ctx.out.items() if o in t and step.symbol in ctx.units(s)])]


def _current(now: _File | None, unit: str) -> bytes | None:
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
    cur = _current(now, st.symbol)
    before, after = ctx.before(st.op, st.symbol), ctx.after(st.op, st.symbol)
    if st.kind == "create":
        later = ctx.later(st, exclude)
        if later:
            return _hold(st, "changed_later", later)
        return ("remove", None) if cur is not None else ("keep", None)
    if st.kind == "delete":
        if cur is None:
            return ("insert", before)
        return _hold(st, "changed_later", ctx.later(st, exclude), cur, before)
    if cur is None:
        return _hold(st, "changed_later", _removed_by(ctx, st))
    m = merge3(after, cur, before)
    if not m.conflicted:
        return ("replace", m.merged)
    holder = next((o for o in ctx.later(st, exclude)
                   if merge3(after, ctx.after(o, st.symbol) or b"", before).conflicted), None)
    return _hold(st, "same_lines", [holder] if holder else [], cur, before)


def _candidate(ctx: _Ctx, steps, forced: dict[Step, dict], keep: set[str] = frozenset()):
    """Undo `steps` newest first, except those in `forced`. Returns (undone, held, files, removed)
    where `files` is path -> (now, candidate) for every path that changes and `removed` the units
    whose creation was undone. A unit in `keep` is left in place though its creation is undone."""
    exclude = {s.op for s in steps}
    held = {s: forced[s] for s in steps if s in forced}
    undone: list[Step] = []
    removed: set[str] = set()
    created_file: set[str] = set()
    recreated: set[str] = set()
    by_path: dict[str, list[Step]] = {}
    for st in steps:
        by_path.setdefault(st.symbol.split("::", 1)[0], []).append(st)
    files: dict[str, tuple[bytes | None, bytes | None]] = {}
    for path, group in sorted(by_path.items()):
        now = ctx.file("HEAD", path)
        work = None if now is None else _File(now.whole, now.head, list(now.units))
        inserts = []
        file_level = (path, _head_sym(path))  # the file comes and goes before its definitions
        for st in sorted(group, key=lambda s: (s.symbol not in file_level, s.symbol, -ctx.when(s.op))):
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
            if st.symbol == path:
                work = None if action == "remove" else _File(True, units=[(path, text)])
                if action == "remove":
                    created_file.add(path)
            elif st.symbol == _head_sym(path):
                if work is None:
                    work = _File(False)
                if action == "remove":
                    work.head = b""
                    created_file.add(path)
                else:
                    work.head = text
                    recreated.add(path)
            elif action == "remove":
                removed.add(st.symbol)
                if st.symbol not in keep:
                    work.units = [(s, t) for s, t in work.units if s != st.symbol]
            elif action == "replace":
                work.units = [(s, text if s == st.symbol else t) for s, t in work.units]
            elif action == "insert":
                if work is None:
                    work = _File(False)
                work.units.append((st.symbol, text))
                inserts.append(st)
        # A definition brought back goes after the nearest one that preceded it and is still there.
        for st in sorted(inserts, key=lambda st: (ctx.when(st.op), len(_preceding(ctx, st)))):
            unit = next((u for u in work.units if u[0] == st.symbol), None)
            if unit is None:
                continue
            work.units.remove(unit)
            names = [s for s, _t in work.units]
            at = next((names.index(s) + 1 for s in reversed(_preceding(ctx, st)) if s in names), 0)
            work.units.insert(at, unit)
        if (work is not None and now is not None and not work.whole and not work.head and now.units
                and work.units and work.units[0][0] != now.units[0][0] and now.units[0][0] in removed):
            first, text = work.units[0]  # a file's first definition has no blank lines before it
            work.units[0] = (first, text[len(text) - len(_body(text)):] if _body(text) else text)
        after = None if work is None else work.render()
        if after == b"" and path not in recreated and (path in created_file
                                                       or _creator(ctx, path) in ctx.out):
            after = None
        before = None if now is None else now.render()
        if before != after:
            files[path] = (before, after)
    ordered = [s for s in steps if s in held]
    return [s for s in steps if s in undone], [held[s] for s in ordered], files, removed


def _creator(ctx: _Ctx, path: str) -> str | None:
    """The op that last created the file (a whole file, or its head)."""
    created = [o for unit in (path, _head_sym(path)) for o in ctx.unit_ops.get(unit, ())
               if ctx.before(o, unit) is None and ctx.after(o, unit) is not None]
    return max(created, key=ctx.when, default=None)


def _preceding(ctx: _Ctx, st: Step) -> list[str]:
    """The units before `st`'s unit in its file, as the file was before the step."""
    prior = ctx.file(ctx.parent[ctx.commit[st.op]], st.symbol.split("::", 1)[0])
    names = [s for s, _t in prior.units] if prior else []
    return names[:names.index(st.symbol)] if st.symbol in names else []


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
    """Undo `steps`, then hold every created definition that remaining code still uses, and undo
    again until nothing more is held. Returns (undone, held, files)."""
    from ugh.entities.graph import build_entity_graph

    forced = dict(forced)
    while True:
        undone, held, files, removed = _candidate(ctx, steps, forced)
        if not removed:
            return undone, held, files
        _, _, kept, _ = _candidate(ctx, steps, forced, keep=removed)
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
            if st.kind == "create" and st.symbol in standing:
                forced[st] = _hold(st, "uses", [h for r in standing[st.symbol]
                                                for h in _introducer(ctx, r, st.symbol)])


def _introducer(ctx: _Ctx, referrer: str, unit: str) -> list[str]:
    """The earliest op on the referrer's definition whose `requires` names the unit (any
    version)."""
    for o in ctx.unit_ops.get(referrer, ()):
        if (o not in ctx.cancelled and referrer in units_of(ctx.by_id[o])
                and any(unit_of(s) == unit for s, _v in ctx.by_id[o].requires)):
            return [o]
    return []


def _plan_steps(ctx: _Ctx, steps, op_ids, verb: str, target: str, forced: dict[Step, dict]) -> Plan:
    undone, held, files = _compute(ctx, steps, forced)
    return Plan(verb, target, steps=tuple(steps), ops=frozenset(op_ids), undone=undone, held=held,
                files=files)


def _removal_set(ctx: _Ctx, x_ops: set[str]) -> dict:
    """X plus everything that stands on it, closed, never cutting another save in two: another
    save's step joins alone only when it sits on a unit X created."""
    born = {u for o in x_ops for u in ctx.units(o) if ctx.before(o, u) is None}
    members = set(x_ops)
    while True:
        steps, _ = steps_of(ctx, members)
        _, held, _ = _compute(ctx, steps, {})
        grow: set[str] = set()
        for h in {h for hd in held for h in hd["holders"]} - members:
            grow |= {h} if ctx.units(h) <= born else ctx.save_ops(h)
        if grow <= members:
            break
        members |= grow
    counts: dict[tuple[str, str], int] = {}
    for st in steps:
        counts[ctx.save(st.op)] = counts.get(ctx.save(st.op), 0) + 1
    return {"saves": [{"sha": k[0], "subject": k[1], "steps": n} for k, n in sorted(counts.items())],
            "steps": len(steps), "ops": sorted(members)}


def plan(repo, op_ids, *, verb: str = "revert", target: str = "", whole: bool = False,
         removal_set: bool = False) -> Plan:
    """The plan for taking `op_ids` out (`revert`) or bringing them back (`restore`), from the
    record alone; nothing is written. `whole` takes out the removal set; `removal_set` reports it."""
    ctx = _Ctx(repo)
    op_ids = frozenset(op_ids)
    if verb == "restore":
        takers = {t for o in sorted(op_ids) for t in ctx.out.get(o, ())}
        if not takers:
            return Plan(verb, target, message=f"nothing of {target} is out; nothing to restore")
        p = _plan_steps(ctx, *steps_of(ctx, takers), verb, target, {})
    else:
        x = {o for o in op_ids if o in ctx.commit and o not in ctx.out}
        if not steps_of(ctx, x)[0]:
            return Plan(verb, target, message=f"{target} is already out; nothing to revert"
                        if op_ids & set(ctx.out) else f"none of {target} is in the code; nothing to revert")
        rs = _removal_set(ctx, x) if whole or removal_set else None
        p = _plan_steps(ctx, *steps_of(ctx, rs["ops"] if whole else x), verb, target, {})
        if rs is not None:
            p.removal_set = {k: v for k, v in rs.items() if k != "ops"}
    if not p.steps:
        p.message = p.message or f"{target} has no steps in the code"
    mentioned = {s.op for s in p.steps} | {h for hd in p.held for h in hd["holders"]}
    p.saves = {o: ctx.save(o) for o in mentioned if o in ctx.commit}
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
    `p` wrote. `locations` are the failing (path, line) pairs on paths the plan changed."""
    fails = [(path, line) for path, (b, a) in sorted(p.files.items())
             if b is not None and not _error_lines(path, b) for line in _error_lines(path, a)]
    if fails:
        return {"judge": "parser", "passed": False, "locations": fails,
                "output": "".join(f"{path}:{line}: does not parse\n" for path, line in fails)}
    if not checked:
        return {"judge": "parser", "passed": True, "locations": [], "output": ""}
    from ugh.core import oracle

    passed, output = oracle.check(repo)
    root = os.path.realpath(repo)
    locs = []
    for pattern in _LOCATIONS:
        for raw, line in pattern.findall(output):
            rel = os.path.relpath(os.path.realpath(os.path.join(root, raw)), root)
            if rel in p.files:
                locs.append((rel, int(line)))
    return {"judge": "check", "passed": passed, "locations": [] if passed else locs,
            "output": output[-4000:]}


def _attribute(p: Plan, verdict: dict) -> set[Step]:
    """The undone steps whose unit holds a failing line in the candidate."""
    going: dict[str, list[Step]] = {}
    for st in p.undone:
        going.setdefault(st.symbol, []).append(st)
    hits: set[Step] = set()
    for path, line in verdict["locations"]:
        text = p.files[path][1]
        f = _split(path, text, False) if text is not None else None
        if f is None:
            continue
        start = f.head.count(b"\n") + 1
        if line < start:
            hits |= set(going.get(_head_sym(path), ()))
            continue
        for sym, t in f.units:
            end = start + t.count(b"\n")
            if line < end or (line == end and not t.endswith(b"\n")):
                hits |= set(going.get(sym, ()))
                break
            start = end
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
    before the commit returns, every touched file is put back."""
    if not p.files:
        return p
    from ugh.core import oplog, oracle
    from ugh.core.sync import state_ref

    repo = Path(repo)
    dirty = _dirty(repo)
    if dirty:
        raise lens.DirtyWorkingTreeError(f"{p.verb} would overwrite uncommitted changes: {dirty}")
    baseline = oracle.check(repo)
    if baseline is not None and not baseline[0]:
        return replace(p, ok=False, message="the check fails on the current tree; nothing applied",
                       check={"judge": "check", "passed": False, "rounds": 0, "output": baseline[1][-4000:]})
    ctx = _Ctx(repo)
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
            current = _plan_steps(ctx, p.steps, p.ops, p.verb, p.target, forced)
        check = {"judge": verdict["judge"], "passed": verdict["passed"], "rounds": rounds,
                 "output": verdict["output"]}
        current = replace(current, check=check, removal_set=p.removal_set,
                          saves={**p.saves, **{o: ctx.save(o) for hd in current.held
                                               for o in hd["holders"] if o in ctx.commit}})
        if not verdict["passed"] or not current.files:
            write({})
            if not verdict["passed"]:
                current.message = "the check fails in code this plan does not undo; nothing applied"
            return current
        trailers = "\n".join(f"{OUT_TRAILER}: {o}" for o in sorted({s.op for s in current.undone}))
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
    return current


def _put_bytes(full: Path, data: bytes | None) -> None:
    if data is None:
        if full.is_file():
            full.unlink()
        return
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_bytes(data)


def to_json(p: Plan, *, applied: bool = False) -> dict:
    """The machine-readable plan: what goes, what is held and why, the removal set, the files
    before and after, and the check."""
    def save(op_id: str) -> dict:
        sha, subject = p.saves.get(op_id, (op_id[:7], ""))
        return {"op": op_id, "save": sha, "subject": subject}

    def text(data: bytes | None) -> str | None:
        return None if data is None else data.decode("utf-8", "replace")

    held = []
    for hd in p.held:
        st = hd["step"]
        row = {**save(st.op), "symbol": st.symbol, "reason": hd["reason"],
               "holders": [save(h) for h in hd["holders"]]}
        if hd["now"] is not None or hd["would"] is not None:
            row["now"], row["would"] = text(hd["now"]), text(hd["would"])
        held.append(row)
    return {
        "ok": p.ok, "applied": applied, "verb": p.verb, "target": p.target, "message": p.message,
        "undone": [{**save(st.op), "symbol": st.symbol} for st in p.undone],
        "held": held,
        "removal_set": p.removal_set,
        "files": {path: {"before": text(b), "after": text(a)} for path, (b, a) in sorted(p.files.items())},
        "check": p.check,
    }
