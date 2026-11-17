"""Taking steps out of the code and bringing them back (docs/design/2026-11-16-removal-sets.md §6).

Taking a step out is a new edit: the step's change un-applied onto the definition as it is now.
Bringing a step back is taking out the edit that took it out. `plan` computes that edit from the
record alone; `apply` writes it, runs the judge, holds what the judge points at, and commits once.
The `out` table records, for each step that is out, the ops that took it out.
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass, field, replace
from pathlib import Path

from ugh import state
from ugh.core import lens, opindex, order
from ugh.core.fold import _ANCHOR_FIRST, _RESIDUE_HEAD, _order_entities, code
from ugh.core.ideal import Ideal
from ugh.core.mine import (
    _content_version, _positional_version, _residue_segments, _top_level_anchor_facts,
)
from ugh.core.op import Op, _symbol_kind, is_bottom, make_op, salted_bottom
from ugh.core.patch import merge3
from ugh.core.store import Store, locked_section

_STEP_KINDS = ("entity", "import", "whole_file", "residue")


class PlanError(Exception):
    """`apply` was given a plan that cannot be applied."""


@dataclass(frozen=True, order=True)
class Step:
    """One op's entry for one content-bearing symbol; `kind` is create, change or delete."""

    op: str
    symbol: str
    kind: str
    carries: tuple[tuple[str, str], ...] = ()  # (op, gap symbol): the gap before a definition
    # the step creates or removes, which travels with it


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
    before_ids: frozenset[str] = frozenset()
    after_ids: frozenset[str] = frozenset()
    new_ops: tuple[Op, ...] = ()
    takers: dict[str, set[str]] = field(default_factory=dict)
    saves: dict[str, tuple[str, str]] = field(default_factory=dict)
    check: object = "not_run"
    kept: dict[str, bytes] = field(default_factory=dict, repr=False)


def load_out(repo) -> dict[str, list[str]]:
    """The out table: step op id -> the ids of the ops that took it out."""
    return state.load_json(repo, "out", default={}) or {}


def _born(version) -> bool:
    return version is None or is_bottom(version)


def _top(sym: str) -> str:
    """The top-level definition a symbol belongs to (`a.py::C.m` -> `a.py::C`)."""
    path, sep, name = sym.partition("::")
    if not sep or name.startswith("__import__::"):
        return sym
    return f"{path}::{name.split('.', 1)[0]}"


def _blob_oid(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\x00" % len(data) + data).hexdigest()


class _Ctx:
    """The record the planner reads: the current ideal, its chains, images on demand, the out
    table restricted to entries that are in effect, and which save each op belongs to."""

    def __init__(self, repo):
        self.repo = Path(repo)
        self.ops = opindex.index_ops(self.repo)
        self.by_id = {op.id: op for op in self.ops}
        self.ideal = lens.current_ideal(self.repo).op_ids
        self.chains = order.ordered_chains(self.ideal, self.ops)
        self.pos = {sym: {oid: i for i, oid in enumerate(seq)} for sym, seq in self.chains.items()}
        self.by_path: dict[str, dict[str, str]] = {}
        for sym, seq in self.chains.items():
            self.by_path.setdefault(sym.split("::", 1)[0], {})[sym] = seq[-1]
        self.store = Store(self.repo)
        self._full: dict[str, Op] = {}
        table = load_out(self.repo)
        self.out = {k: v for k, v in table.items()
                    if k in self.ideal and v and all(t in self.ideal for t in v)}
        self.cancelled = set(self.out) | {t for v in self.out.values() for t in v}
        self._code: dict[str, bytes] | None = None
        self._saves: dict[str, tuple[str, str]] | None = None

    def full(self, op_id: str) -> Op:
        if op_id not in self._full:
            self._full[op_id] = self.store.get(op_id) or self.by_id[op_id]
        return self._full[op_id]

    def image(self, op_id: str, sym: str) -> bytes | None:
        return self.full(op_id).images.get(sym)

    def after(self, op_id: str, sym: str) -> str:
        return self.by_id[op_id].footprint[sym][1]

    def live(self, sym: str) -> str | None:
        """The tip op of `sym` when it is alive, else None."""
        tip = self.chains.get(sym, [None])[-1]
        return tip if tip is not None and not is_bottom(self.after(tip, sym)) else None

    def before_image(self, op_id: str, sym: str) -> bytes | None:
        i = self.pos[sym][op_id]
        return self.image(self.chains[sym][i - 1], sym) if i else None

    def last_image(self, sym: str) -> bytes | None:
        """The newest text `sym` had while alive."""
        for oid in reversed(self.chains.get(sym, ())):
            if not is_bottom(self.after(oid, sym)):
                return self.image(oid, sym)
        return None

    def birth(self, sym: str) -> str | None:
        """The op that last brought `sym` into being."""
        for oid in reversed(self.chains.get(sym, ())):
            if _born(self.by_id[oid].footprint[sym][0]):
                return oid
        return None

    def birth_image(self, sym: str) -> bytes | None:
        oid = self.birth(sym)
        return self.image(oid, sym) if oid else None

    def later(self, step: Step, exclude) -> list[str]:
        """Ops after `step` on its symbol that stand on it: not in `exclude`, not out, and not an
        edit that took something out."""
        seq = self.chains.get(step.symbol, [])
        i = self.pos.get(step.symbol, {}).get(step.op)
        tail = seq[i + 1:] if i is not None else []
        return [o for o in tail if o not in exclude and o not in self.cancelled]

    def code(self) -> dict[str, bytes]:
        if self._code is None:
            ideal = Ideal(op_ids=self.ideal)
            self._code = code(ideal, lens.ops_with_frontier_images(self.repo, ideal))
        return self._code

    def saves(self) -> dict[str, tuple[str, str]]:
        """op id -> (short sha, subject) of the save that introduced it."""
        if self._saves is None:
            from ugh.store.gitbind import GitBinding

            gb = GitBinding(self.repo)
            rows = gb.history()
            subject = {sha: subj for sha, _parent, subj in rows}
            index = {sha: i for i, (sha, _parent, _subj) in enumerate(rows)}
            sha_of = opindex.earliest_commit_sha(gb, rows, [self.by_id[o] for o in self.ideal])
            self._saves = {oid: (sha[:7], subject.get(sha, "")) for oid, sha in sha_of.items()}
            self._when = {oid: index[sha] for oid, sha in sha_of.items()}
        return self._saves

    def when(self, op_id: str) -> int:
        """The position in history of the save that introduced `op_id`."""
        self.saves()
        return self._when.get(op_id, len(self._when) + 1)

    def save_ops(self, op_id: str) -> set[str]:
        sha = self.saves().get(op_id)
        return {o for o, s in self.saves().items() if s == sha and o not in self.out} if sha else {op_id}


def steps_of(ctx: _Ctx, op_ids) -> tuple[list[Step], frozenset[str]]:
    """The steps of `op_ids` that are in effect, and those ops. A definition's anchor and the gaps
    around it are not steps when the same ops create or remove the definition: they travel with
    it (its own trailing gap, and the change to the gap before it)."""
    ops = [ctx.by_id[o] for o in sorted(op_ids) if o in ctx.ideal and o not in ctx.out]
    moved = {s for op in ops for s, (b, a) in op.footprint.items()
             if _symbol_kind(s) in ("entity", "import") and (_born(b) or is_bottom(a))}
    anchors = {s: op.id for op in ops for s in op.footprint if _symbol_kind(s) == "anchor"}
    out = []
    for op in ops:
        for sym, (before, after) in sorted(op.footprint.items()):
            kind = _symbol_kind(sym)
            if kind not in _STEP_KINDS:
                continue
            if kind == "residue" and sym.replace("::__residue__::", "::", 1) in moved:
                continue
            out.append(Step(op.id, sym, "create" if _born(before)
                            else "delete" if is_bottom(after) else "change"))
    preceding: dict[str, str] = {}
    for sym in moved:
        path, name = sym.split("::", 1)
        anchor = f"{path}::__anchor__::{name}"
        holder = anchors.get(anchor) or (ctx.chains.get(anchor) or [None])[-1]
        if holder is not None:
            marker = (ctx.image(holder, anchor) or b"").decode("utf-8")
            pred = _RESIDUE_HEAD if marker == _ANCHOR_FIRST else marker
            preceding.setdefault(f"{path}::__residue__::{pred}", sym)
    carried = {s: preceding[s.symbol] for s in out
               if s.kind != "delete" and s.symbol in preceding}
    steps = []
    for st in out:
        if st in carried:
            continue
        carries = tuple((c.op, c.symbol) for c in carried if carried[c] == st.symbol)
        steps.append(replace(st, carries=carries) if carries else st)
    return steps, frozenset(op.id for op in ops)


# -- a file as the fold lays it out ----------------------------------------------------------

@dataclass
class _Item:
    name: str
    image: bytes
    gap: bytes
    gone: bool = False
    leftover: bytes = b""
    after: str | None = None  # where a definition brought back belongs: after this one


@dataclass
class _Layout:
    head: bytes | None = None
    items: list[_Item] = field(default_factory=list)
    whole: bool = False
    text: bytes | None = None  # a whole-file path's bytes
    head_out: bool = False  # the edit that created the file is out

    def find(self, name: str) -> _Item | None:
        return next((it for it in self.items if it.name == name), None)

    def render(self, keep_gone: bool = False) -> bytes | None:
        if self.whole:
            return self.text
        kept = [it for it in self.items if keep_gone or not it.gone]
        if not kept and (self.head is None or self.head_out and not self.head.strip()):
            return None
        parts = [self.head or b""]
        for it in self.items:
            parts.append(it.image + it.gap if keep_gone or not it.gone else it.leftover)
        return b"".join(parts)


def _layout(ctx: _Ctx, path: str) -> _Layout:
    live = {s: o for s, o in ctx.by_path.get(path, {}).items() if ctx.live(s) == o
            or _symbol_kind(s) == "anchor"}
    if path in live:
        return _Layout(whole=True, text=ctx.image(live[path], path))
    if path in ctx.chains and not any(_symbol_kind(s) != "anchor" for s in live):
        return _Layout(whole=True)
    anchor_of: dict[str, str | None] = {}
    images: dict[str, bytes] = {}
    gaps: dict[str, bytes] = {}
    for sym, oid in live.items():
        kind, name = _symbol_kind(sym), sym.split("::", 1)[1]
        if kind == "anchor":
            marker = (ctx.image(oid, sym) or b"").decode("utf-8")
            anchor_of[name.removeprefix("__anchor__::")] = None if marker == _ANCHOR_FIRST else marker
        elif kind in ("entity", "import"):
            images[name] = ctx.image(oid, sym) or b""
        elif kind == "residue":
            gaps[name.removeprefix("__residue__::")] = ctx.image(oid, sym) or b""
    head = gaps.pop(_RESIDUE_HEAD, None)
    items = [_Item(n, images[n], gaps.pop(n, b"")) for n in _order_entities(set(images), anchor_of)]
    orphans = b"".join(gaps[k] for k in sorted(gaps) if gaps[k])
    if orphans and items:
        items[-1].gap += orphans
    elif orphans:
        head = (head or b"") + orphans
    return _Layout(head=head, items=items,
                   head_out=ctx.birth(f"{path}::__residue__::{_RESIDUE_HEAD}") in ctx.out)


def _place(lay: _Layout) -> None:
    """Put each definition brought back after the one its anchor names, once all are back."""
    for _ in range(len(lay.items)):
        moved = False
        for it in [it for it in lay.items if it.after is not None]:
            names = [x.name for x in lay.items]
            at = 0 if it.after == _ANCHOR_FIRST else (
                names.index(it.after) + 1 if it.after in names else len(names))
            here = lay.items.index(it)
            if at != here and at != here + 1:
                lay.items.remove(it)
                lay.items.insert(at if at < here else at - 1, it)
                moved = True
        if not moved:
            return


def _hold(step: Step, reason: str, holders, now: bytes | None = None,
          would: bytes | None = None) -> dict:
    out = {"step": step, "reason": reason, "holders": list(dict.fromkeys(holders))}
    if reason == "two_versions":
        out["now"], out["would"] = now, would
    return out


def _undo(ctx: _Ctx, st: Step, verb: str, lay: _Layout, exclude) -> dict | set[str]:
    """Un-apply one step onto `lay`. Returns the symbols it changed, or a hold."""
    path, _, name = st.symbol.partition("::")
    kind = _symbol_kind(st.symbol)
    conflict = "two_versions" if verb == "restore" else "same_lines"

    def merged(now: bytes):
        theirs = ctx.before_image(st.op, st.symbol) or b""
        m = merge3(ctx.image(st.op, st.symbol) or b"", now, theirs)
        if m.conflicted:
            return _hold(st, conflict, ctx.later(st, exclude), now, theirs)
        return m.merged

    if kind == "whole_file" or lay.whole:
        if not lay.whole:
            return _hold(st, "changed_later", ctx.later(st, exclude))
        if st.kind == "delete":
            if lay.text is not None:
                return _hold(st, "two_versions", [ctx.chains[st.symbol][-1]], lay.text,
                             ctx.before_image(st.op, st.symbol))
            lay.text = ctx.before_image(st.op, st.symbol)
            return {st.symbol}
        if lay.text is None:
            later = ctx.later(st, exclude)
            return _hold(st, "changed_later", later) if later or st.kind != "create" else set()
        if st.kind == "create":
            later = ctx.later(st, exclude)
            if later:
                return _hold(st, "changed_later", later)
            lay.text = None
            return {st.symbol}
        result = merged(lay.text)
        if isinstance(result, dict):
            return result
        lay.text = result
        return {st.symbol}

    if kind == "residue":
        gap = name.removeprefix("__residue__::")
        item = None if gap == _RESIDUE_HEAD else lay.find(gap)
        now = lay.head if gap == _RESIDUE_HEAD else (item.gap if item and not item.gone else None)
        if st.kind == "delete":
            if now is None and gap == _RESIDUE_HEAD:
                lay.head, lay.head_out = ctx.before_image(st.op, st.symbol), False
                return {st.symbol}
            return set()
        if now is None:
            return set()
        if st.kind == "create":
            later = ctx.later(st, exclude)
            if later:
                return _hold(st, "changed_later", later)
            result = None if gap == _RESIDUE_HEAD else b""
        else:
            result = merged(now)
            if isinstance(result, dict):
                return result
        if gap == _RESIDUE_HEAD:
            lay.head = result
        else:
            item.gap = result
        return {st.symbol}

    item = lay.find(name)
    alive = item is not None and not item.gone
    if st.kind == "delete":
        before = ctx.before_image(st.op, st.symbol) or b""
        if alive:
            return _hold(st, "two_versions", [ctx.chains[st.symbol][-1]], item.image, before)
        if item is not None:
            item.gone, item.image = False, before
            return {st.symbol} | _carry(ctx, st, lay)
        residue, anchor = f"{path}::__residue__::{name}", f"{path}::__anchor__::{name}"
        marker = (ctx.image(ctx.chains[anchor][-1], anchor) or b"").decode() if anchor in ctx.chains else ""
        lay.items.append(_Item(name, before, ctx.last_image(residue) or b"",
                               after=_ANCHOR_FIRST if marker in ("", _ANCHOR_FIRST) else marker))
        return {st.symbol, residue, anchor} | _carry(ctx, st, lay)
    if not alive:
        later = ctx.later(st, exclude)
        return _hold(st, "changed_later", later) if later or st.kind != "create" else set()
    if st.kind == "create":
        later = ctx.later(st, exclude)
        if later:
            return _hold(st, "changed_later", later)
        residue = f"{path}::__residue__::{name}"
        item.gone = True
        item.leftover = b""
        if item.gap.strip():
            birth = ctx.birth_image(residue)
            m = merge3(birth, item.gap, b"") if birth is not None else None
            item.leftover = m.merged if m is not None and not m.conflicted else item.gap
        touched = {st.symbol, residue}
        if item.leftover:
            prev = [it for it in lay.items[:lay.items.index(item)] if not it.gone]
            touched.add(f"{path}::__residue__::{prev[-1].name if prev else _RESIDUE_HEAD}")
        return touched | _carry(ctx, st, lay)
    result = merged(item.image)
    if isinstance(result, dict):
        return result
    item.image = result
    return {st.symbol}


def _carry(ctx: _Ctx, st: Step, lay: _Layout) -> set[str]:
    """Un-apply the change a step made to the gap before its definition."""
    touched = set()
    for op_id, sym in st.carries:
        gap = sym.split("::__residue__::", 1)[1]
        item = None if gap == _RESIDUE_HEAD else lay.find(gap)
        now = lay.head if gap == _RESIDUE_HEAD else (item.gap if item and not item.gone else None)
        if now is None:
            continue
        m = merge3(ctx.image(op_id, sym) or b"", now, ctx.before_image(op_id, sym) or b"")
        if m.conflicted:
            continue
        if gap == _RESIDUE_HEAD:
            lay.head = None if _born(ctx.by_id[op_id].footprint[sym][0]) else m.merged
        else:
            item.gap = m.merged
        touched.add(sym)
    return touched


def _uses(ctx: _Ctx, kept: dict[str, bytes]):
    from ugh.entities.graph import build_entity_graph

    return [(e.src, e.dst) for e in build_entity_graph(kept).edges if e.type == "uses"]


def _introducer(ctx: _Ctx, ref: str, name: str, since: str) -> str:
    """The op on `ref`'s chain that wrote the name it still uses: the start of the newest stretch
    of versions, saved no earlier than `since`, whose text carries the name; else its tip."""
    pattern = re.compile(rb"(?<![A-Za-z0-9_])" + re.escape(name.encode()) + rb"(?![A-Za-z0-9_])")
    intro = None
    start = ctx.when(since)
    for oid in ctx.chains.get(ref, ()):
        if oid in ctx.cancelled:
            continue
        img = ctx.image(oid, ref)
        if ctx.when(oid) >= start and img and pattern.search(img):
            intro = intro or oid
        else:
            intro = None
    return intro or ctx.chains[ref][-1]


def _compute(ctx: _Ctx, steps, verb: str, forced: dict[Step, dict]):
    """Undo `steps` symbol by symbol, newest first, holding what cannot go; a definition that
    remaining code uses is held and the rest is undone again without it. Returns
    (undone, held, layouts, changed, kept codebase)."""
    forced = dict(forced)
    while True:
        undone, held, layouts, changed, kept = _undo_all(ctx, steps, verb, forced)
        removed = {f"{p}::{it.name}" for p, lay in layouts.items() if not lay.whole
                   for it in lay.items if it.gone}
        if not removed:
            return undone, held, layouts, changed, kept
        refs: dict[str, set[str]] = {}
        for src, dst in _uses(ctx, kept):
            if _top(dst) in removed and _top(src) != _top(dst):
                refs.setdefault(_top(dst), set()).add(_top(src))
        standing: dict[str, list[str]] = {}
        moving = True
        while moving:
            moving = False
            for d in sorted(removed):
                by = sorted(r for r in refs.get(d, ()) if r not in removed and r in ctx.chains)
                if by:
                    standing[d] = by
                    removed.discard(d)
                    moving = True
        if not standing:
            return undone, held, layouts, changed, kept
        for st in undone:
            if st.kind == "create" and st.symbol in standing:
                name = st.symbol.split("::", 1)[1].removeprefix("__import__::")
                forced[st] = _hold(st, "uses", [_introducer(ctx, r, name, st.op)
                                                for r in standing[st.symbol]])


def _undo_all(ctx: _Ctx, steps, verb: str, forced: dict[Step, dict]):
    exclude = {s.op for s in steps}
    held = {s: forced[s] for s in steps if s in forced}
    layouts: dict[str, _Layout] = {}
    changed: dict[Step, set[str]] = {}
    by_sym: dict[str, list[Step]] = {}
    for st in steps:
        by_sym.setdefault(st.symbol, []).append(st)
    for sym in sorted(by_sym):
        path = sym.split("::", 1)[0]
        lay = layouts.get(path) or layouts.setdefault(path, _layout(ctx, path))
        for st in sorted(by_sym[sym], key=lambda s: ctx.pos[sym].get(s.op, -1), reverse=True):
            if st in held:
                continue
            result = _undo(ctx, st, verb, lay, exclude)
            if isinstance(result, dict):
                held[st] = result
            else:
                changed[st] = result
    for lay in layouts.values():
        _place(lay)
    kept = dict(ctx.code())
    for path, lay in layouts.items():
        text = lay.render(keep_gone=True)
        if text is None:
            kept.pop(path, None)
        else:
            kept[path] = text
    undone = [s for s in steps if s in changed]
    return undone, [held[s] for s in steps if s in held], layouts, changed, kept


def _segments(path: str, text: bytes) -> dict[str, tuple[bytes, str]]:
    """`text` as the miner splits it: symbol -> (image, version)."""
    from ugh.entities.extract import extract_file

    ents = extract_file(path, text)
    out = {e.id: (text[e.start_byte:e.end_byte], _positional_version(e.id, e.content_hash))
           for e in ents}
    for name, seg in _residue_segments(text, ents).items():
        out[f"{path}::__residue__::{name}"] = (seg, _content_version(seg))
    for name, pred in _top_level_anchor_facts(ents).items():
        marker = (pred or _ANCHOR_FIRST).encode("utf-8")
        out[f"{path}::__anchor__::{name}"] = (marker, _content_version(marker))
    return out


def _mint(ctx: _Ctx, files, intent: str) -> tuple[list[Op], dict[str, str]]:
    """Ops that move each changed file from its tips to its new text, chained on the tips and
    split the way the miner splits a file. Returns (ops, symbol -> op id)."""
    ops: list[Op] = []
    op_of: dict[str, str] = {}
    for path, (_before, after) in sorted(files.items()):
        tips = ctx.by_path.get(path, {})
        if after is None:
            new = {}
        elif path in tips and ctx.live(path):
            new = {path: (after, _blob_oid(after))}
        else:
            new = _segments(path, after)
        groups: dict[str, dict[str, tuple]] = {}

        def group(sym: str) -> str:
            return _top(sym) if _symbol_kind(sym) in ("entity", "nested", "import") else sym

        for sym, (img, version) in new.items():
            tip = tips.get(sym)
            prior = ctx.after(tip, sym) if tip else None
            if prior is not None and not is_bottom(prior) and order.link(prior) == version:
                continue
            groups.setdefault(group(sym), {})[sym] = (prior, version, img)
        for sym, tip in tips.items():
            if sym not in new and _symbol_kind(sym) != "anchor" and ctx.live(sym):
                groups.setdefault(group(sym), {})[sym] = (ctx.after(tip, sym), None, None)
        for members in groups.values():
            salt = hashlib.sha1("\0".join(
                f"{s}={','.join(ctx.chains.get(s, ()))}" for s in sorted(members)).encode()
            ).hexdigest()[:16]
            footprint = {s: (b, f"{v}#{salt}" if v is not None else salted_bottom(salt))
                         for s, (b, v, _i) in members.items()}
            afters = [v for _b, v, _i in members.values()]
            kind = ("prune" if all(v is None for v in afters)
                    else "add" if all(_born(b) for b, _v, _i in members.values()) else "rework")
            op = make_op(footprint, {s: i for s, (_b, _v, i) in members.items()},
                         kind=kind, intent=intent)
            ops.append(op)
            op_of.update(dict.fromkeys(members, op.id))
    return ops, op_of


def _plan_steps(ctx: _Ctx, steps, op_ids, verb: str, target: str,
                forced: dict[Step, dict]) -> Plan:
    undone, held, layouts, changed, kept = _compute(ctx, steps, verb, forced)
    files = {}
    for path, lay in layouts.items():
        before, after = ctx.code().get(path), lay.render()
        if before != after:
            files[path] = (before, after)
    new_ops, op_of = _mint(ctx, files, f"{verb} {target}")
    takers: dict[str, set[str]] = {}
    for st in undone:
        mine = {op_of[s] for s in changed[st] if s in op_of}
        if mine:
            takers.setdefault(st.op, set()).update(mine)
    carried: dict[str, set[str]] = {}
    for st in undone:
        for s in changed[st]:
            carried.setdefault(s, set()).add(st.op)
    for o in op_ids - {st.op for st in steps}:
        syms = [s for s in ctx.by_id[o].footprint if s in carried]
        mine = {op_of[s] for s in syms if s in op_of} or {
            t for s in syms for owner in carried[s] for t in takers.get(owner, ())}
        if mine:
            takers[o] = mine
    after_ids = frozenset(ctx.ideal | {op.id for op in new_ops})
    p = Plan(verb, target, steps=tuple(steps), ops=frozenset(op_ids), undone=undone, held=held, files=files,
             before_ids=frozenset(ctx.ideal), after_ids=after_ids, new_ops=tuple(new_ops),
             takers=takers, kept=kept)
    if new_ops:
        all_ops = ctx.ops + new_ops
        if not order.is_valid_ideal(all_ops, after_ids):
            return replace(p, ok=False, message=f"{verb} {target}: the edit would not chain onto "
                                                f"the record; nothing written")
        fresh = {op.id: op for op in new_ops}
        imaged = [fresh.get(o.id) or (ctx.full(o.id) if o.id in ctx.ideal else o) for o in all_ops]
        folded = code(Ideal(op_ids=after_ids), imaged, only_paths=set(files))
        bad = sorted(p for p, (_b, a) in files.items() if folded.get(p) != a)
        if bad:
            return replace(p, ok=False, message=f"{verb} {target}: the record cannot lay out "
                                                f"{', '.join(bad)} as planned; nothing written")
    return p


def _removal_set(ctx: _Ctx, x_ops: set[str], verb: str, target: str) -> tuple[set[str], Plan]:
    """X plus everything that stands on it, closed, never cutting another save in two: another
    save's step joins alone only when it sits on a definition X created."""
    born = {s for o in x_ops for s, (b, _a) in ctx.by_id[o].footprint.items() if _born(b)}
    born |= {f"{s.split('::', 1)[0]}::__residue__::{s.split('::', 1)[1]}" for s in born if "::" in s}
    members = set(x_ops)
    while True:
        p = _plan_steps(ctx, *steps_of(ctx, members), verb, target, {})
        holders = {h for hd in p.held for h in hd["holders"]} - members
        grow: set[str] = set()
        for h in holders:
            syms = {s for s in ctx.by_id[h].footprint if _symbol_kind(s) in _STEP_KINDS}
            grow |= {h} if syms and syms <= born else ctx.save_ops(h)
        if grow <= members:
            return members, p
        members |= grow


def plan(repo, op_ids, *, verb: str = "revert", target: str = "", whole: bool = False) -> Plan:
    """The plan for taking `op_ids` out (`revert`) or bringing them back (`restore`), from the
    record alone; nothing is written."""
    ctx = _Ctx(repo)
    op_ids = frozenset(op_ids)
    base = Plan(verb, target, before_ids=frozenset(ctx.ideal), after_ids=frozenset(ctx.ideal))
    if verb == "restore":
        takers = {t for o in sorted(op_ids) for t in ctx.out.get(o, ())}
        if not takers:
            return replace(base, message=f"nothing of {target} is out; nothing to restore")
        p = _plan_steps(ctx, *steps_of(ctx, takers), verb, target, {})
    else:
        x = {o for o in op_ids if o in ctx.ideal and o not in ctx.out}
        if not x:
            gone = op_ids & set(ctx.out)
            return replace(base, message=f"{target} is already out; nothing to revert" if gone
                           else f"none of {target} is in the code; nothing to revert")
        members, whole_plan = _removal_set(ctx, x, verb, target)
        p = whole_plan if whole else _plan_steps(ctx, *steps_of(ctx, x), verb, target, {})
        counts: dict[tuple[str, str], int] = {}
        for st in whole_plan.steps:
            key = ctx.saves().get(st.op, (st.op[:7], ""))
            counts[key] = counts.get(key, 0) + 1
        p.removal_set = {"saves": [{"sha": k[0], "subject": k[1], "steps": n}
                                   for k, n in sorted(counts.items())],
                         "steps": len(whole_plan.steps)}
    if not p.steps:
        p.message = p.message or f"{target} has no steps in the code"
    mentioned = {s.op for s in p.steps} | {h for hd in p.held for h in hd["holders"]}
    p.saves = {o: ctx.saves()[o] for o in mentioned if o in ctx.saves()}
    return p


def plan_many(repo, op_sets) -> list[Plan]:
    """Revert plans for several op sets over one read of the record: the steps, what goes and
    what is held, without files or ops."""
    ctx = _Ctx(repo)
    plans = []
    for op_ids in op_sets:
        steps, ids = steps_of(ctx, op_ids)
        undone, held, *_ = _compute(ctx, steps, "revert", {})
        plans.append(Plan("revert", "", steps=tuple(steps), ops=ids, undone=undone, held=held))
    return plans


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


def _judge(ctx: _Ctx, p: Plan) -> dict:
    """Run the parser, then the project's check, on the tree `p` wrote. A file that parsed
    before must still parse; the check must pass."""
    fails = [(path, line) for path, (b, a) in sorted(p.files.items())
             if b is not None and not _error_lines(path, b) for line in _error_lines(path, a)]
    if fails:
        return {"judge": "parser", "passed": False, "locations": fails, "named": [], "output": ""}
    from ugh.core import oracle

    result = oracle.check(ctx.repo)
    if result is None:
        return {"judge": "parser", "passed": True, "locations": [], "named": [], "output": ""}
    passed, output = result
    paths = set(ctx.code()) | set(p.files)
    root = os.path.realpath(ctx.repo)
    locs, named = [], set()
    for pattern in _LOCATIONS:
        for raw, line in pattern.findall(output):
            rel = os.path.relpath(os.path.realpath(os.path.join(root, raw)), root)
            if rel in paths:
                locs.append((rel, int(line)))
    for path in paths:
        if path in output:
            named.add(path)
    return {"judge": "check", "passed": passed, "locations": [] if passed else locs,
            "named": sorted(named), "output": output[-4000:]}


def _locate(ctx: _Ctx, p: Plan, verdict: dict) -> set[Step]:
    """The undone steps the failures point at: the step that wrote the definition at a failing
    line, else the undone steps that definition uses; else every undone step in the files the
    failures name; else all of them."""
    from ugh.entities.extract import extract_file

    going = set(p.undone)
    text_of = {**ctx.code(), **{path: a for path, (_b, a) in p.files.items() if a is not None}}
    edges = None
    hits: set[Step] = set()
    for path, line in verdict["locations"]:
        text = text_of.get(path)
        if text is None:
            continue
        tops = [e for e in extract_file(path, text) if e.container is None]
        at = [e for e in tops if e.start_line <= line <= e.end_line]
        if not at:
            before = [e for e in tops if e.end_line < line]
            gap = f"{path}::__residue__::{before[-1].name if before else _RESIDUE_HEAD}"
            hits |= {s for s in going if s.symbol == gap}
            continue
        d = at[0].id
        own = {s for s in going if _top(s.symbol) == d
               or s.symbol == f"{path}::__residue__::{at[0].name}"}
        if own:
            hits |= own
            continue
        if edges is None:
            edges = _uses(ctx, p.kept)
        used = {_top(dst) for src, dst in edges if _top(src) == d}
        used |= {_top(dst) for src, dst in edges if src in used and "::__import__::" in src}
        hits |= {s for s in going if _top(s.symbol) in used}
    if not hits:
        files = {path for path, _line in verdict["locations"]} | set(verdict["named"])
        hits = {s for s in going if s.symbol.split("::", 1)[0] in files} or going
    return hits


def apply(repo, p: Plan, message: str | None = None) -> Plan:
    """Write the plan's candidate, judge it, hold what the judge points at and try again, give
    back what turned out not to be needed, then commit once and record. On any error or
    interruption the working tree is put back exactly as it was."""
    if not p.ok:
        raise PlanError(p.message or f"{p.verb} refused")
    if not p.files:
        return p
    from ugh.store.gitbind import GitBinding

    repo = Path(repo)
    ctx = _Ctx(repo)
    gb = GitBinding(repo)
    after_tree = dict(ctx.code())
    for path, (_b, a) in p.files.items():
        if a is None:
            after_tree.pop(path, None)
        else:
            after_tree[path] = a
    dirty = lens._dirty_conflicts(repo, gb, after_tree)
    if dirty:
        raise lens.DirtyWorkingTreeError(
            f"{p.verb} would overwrite uncommitted changes: {sorted(dirty)}")

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
        while True:
            write(current.files)
            verdict = _judge(ctx, current)
            rounds += 1
            if verdict["passed"] or not current.undone:
                break
            for st in _locate(ctx, current, verdict) - set(forced):
                forced[st] = _hold(st, "check", [])
            current = _plan_steps(ctx, p.steps, p.ops, p.verb, p.target, forced)
            if not current.ok:
                break
        if verdict["passed"]:
            for st in sorted(forced):
                trial_forced = {k: v for k, v in forced.items() if k != st}
                trial = _plan_steps(ctx, p.steps, p.ops, p.verb, p.target, trial_forced)
                if not trial.ok:
                    continue
                write(trial.files)
                rounds += 1
                if _judge(ctx, trial)["passed"]:
                    forced, current = trial_forced, trial
            write(current.files)
        check = {"judge": verdict["judge"], "passed": verdict["passed"], "rounds": rounds,
                 "output": verdict["output"]}
        current = replace(current, check=check, removal_set=p.removal_set,
                          saves={**p.saves, **current.saves})
        if not verdict["passed"] or not current.ok or not current.files:
            write({})
            return current
        _commit(repo, current, message)
        return current
    except BaseException:
        write({})
        raise


def _put_bytes(full: Path, data: bytes | None) -> None:
    if data is None:
        if full.is_file():
            full.unlink()
        return
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_bytes(data)


def _commit(repo: Path, p: Plan, message: str | None) -> None:
    store = Store(repo)
    stale = opindex.is_stale(repo)
    stored = [store.add(op) for op in p.new_ops]
    with locked_section(repo):
        if stale:
            opindex.rebuild(repo, store)
        else:
            opindex.apply_delta(repo, stored)
    after = Ideal.from_ops(p.after_ids, opindex.index_ops(repo))
    sha = lens.commit_materialized(repo, after, message or f"ugh {p.verb} {p.target}",
                                   bookkeeping=message is None)
    prior = load_out(repo)
    table = {k: list(v) for k, v in prior.items()}
    for op_id, takers in p.takers.items():
        table[op_id] = sorted(set(table.get(op_id, ())) | takers)
    for key in sorted(table):
        rest = [t for t in table[key] if t not in table]
        if rest:
            table[key] = rest
        else:
            del table[key]
    lens.record_ideal(repo, after, sha, before=p.before_ids,
                      meta={"verb": p.verb, "target": p.target, "out": prior})
    state.save_json(repo, "out", table)


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
        if hd["reason"] == "two_versions":
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
