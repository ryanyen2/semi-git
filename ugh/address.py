"""Addresses: how a verb is told which steps it acts on, and how every step is printed.

    <label>                 the ops named label
    <label>@<rev>           those ops in one save
    <path>                  the ops on one file
    <path>::<def>           the ops on one top-level definition
    <path>::<def>@<rev>     one op
    <rev>                   one save's ops (anything `git rev-parse --verify <rev>^{commit}` takes)
    <rev>..<rev>, <rev>..   the saves in that git range (`<rev>..` runs through HEAD)

An address resolves over every op in the record, in the code or out. A token with two readings is
refused naming both, and anything else is refused with the nearest `find` hits. A step prints as
`path::def@<short sha>`, or `path@<short sha>` when it is on a whole file, a file's head or an import.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

from ugh import names
from ugh.core.op import _symbol_kind, is_bottom, step_address, unit_of

_KIND = {"add": "create", "prune": "delete", "move": "move"}


class AddressError(Exception):
    """A token that names nothing, or names two things. `hits` are the nearest `find` hits."""

    def __init__(self, message: str, hits=()):
        super().__init__(message)
        self.hits = list(hits)


def _units(op) -> set[str]:
    return {unit_of(s) for s in op.footprint if _symbol_kind(s) != "anchor"}


def _shown_unit(op) -> str:
    """The unit a step is printed under: for a move, the one it moved to."""
    units = _units(op)
    live = {unit_of(s) for s, (_b, a) in op.footprint.items()
            if _symbol_kind(s) != "anchor" and not is_bottom(a)}
    return min(live & units or units)


def plan_json(repo, p, *, applied: bool = False) -> dict:
    """`planner.to_json` with an `address` on every step it names."""
    from ugh.core import opindex, planner

    view = planner.to_json(p, applied=applied)
    by_id = {op.id: op for op in opindex.index_ops(repo)}
    for row in view["undone"] + view["held"]:
        row["address"] = step_address(row["symbol"], row["save"])
        for h in row.get("holders", ()):
            op = by_id.get(h["op"])
            shown = op is not None and _units(op)
            h["address"] = step_address(_shown_unit(op), h["save"]) if shown else h["save"]
    return view


class Record:
    """Every step mined from a commit in HEAD's history: its save, its unit, its name, and whether
    it is out. A step is an op that changed some text (an op that only moved an anchor is none)."""

    def __init__(self, repo):
        from ugh.core import planner

        self.repo = Path(repo)
        self.ctx = planner._Ctx(self.repo)
        self.gb = self.ctx.gb
        self.names = names.load(self.repo)
        self.units = {o: u for o in self.ctx.commit if (u := _units(self.ctx.by_id[o]))}
        self.meta = {sha: (subject, ts, bk) for sha, _p, subject, ts, bk in self.gb.history_meta()}

    def address(self, op_id: str) -> str:
        return step_address(_shown_unit(self.ctx.by_id[op_id]), self.ctx.commit[op_id])

    def step(self, op_id: str) -> dict:
        return {"address": self.address(op_id),
                "kind": _KIND.get(self.ctx.by_id[op_id].kind, "change"),
                "name": self.names.get(op_id), "out": op_id in self.ctx.out}

    def steps(self, op_ids) -> list[dict]:
        """The steps among `op_ids`, oldest save first."""
        ids = [o for o in op_ids if o in self.units]
        return [self.step(o) for o in sorted(ids, key=lambda o: (self.ctx.when(o), self.address(o)))]

    def save_ops(self, sha: str) -> list[str]:
        return [o for o in self.ctx.of_commit.get(sha, ()) if o in self.units]

    def saves(self) -> list[str]:
        """The saves, newest first: every commit in HEAD's history that is not ugh's own."""
        return [sha for sha, (_s, _t, bk) in reversed(self.meta.items()) if not bk]

    def save_row(self, sha: str) -> dict:
        subject, ts, _bk = self.meta.get(sha, ("", None, False))
        date = datetime.fromtimestamp(ts, timezone.utc).isoformat() if ts is not None else None
        return {"sha": sha, "subject": subject, "date": date}

    def rev(self, token: str) -> str | None:
        """The commit `token` names, or None."""
        if not token or token.startswith("-"):
            return None
        return self.gb.rev_parse(f"{token}^{{commit}}")


def _named(rec: Record, token: str) -> dict[str, frozenset[str]]:
    """The label, file and definition readings of `token`."""
    out = {}
    labelled = frozenset(o for o, label in rec.names.items() if label == token and o in rec.units)
    if token in rec.names.values():
        out[f"the name {token!r}"] = labelled
    on_file = frozenset(o for o, us in rec.units.items() if any(u.split("::", 1)[0] == token for u in us))
    if on_file:
        out[f"the file {token}"] = on_file
    if "::" in token:
        unit = unit_of(token)
        on_def = frozenset(o for o, us in rec.units.items() if unit in us)
        if on_def:
            out[f"the definition {unit}"] = on_def
    return out


def _readings(rec: Record, token: str) -> dict[str, frozenset[str]]:
    out = _named(rec, token)
    if ".." in token:
        start, _, end = token.partition("..")
        a, b = rec.rev(start), rec.rev(end or "HEAD")
        if a and b:
            shas = {sha for sha, _p, _s in rec.gb.history(a, b)}
            out[f"the saves {start}..{end}"] = frozenset(o for o in rec.units if rec.ctx.commit[o] in shas)
        return out
    sha = rec.rev(token)
    if sha:
        out[f"the save {sha[:7]}"] = frozenset(rec.save_ops(sha))
    base, at, rev = token.rpartition("@")
    sha = rec.rev(rev) if at and base else None
    if sha:
        for what, ops in _named(rec, base).items():
            out[f"{what} in {sha[:7]}"] = frozenset(o for o in ops if rec.ctx.commit[o] == sha)
    return out


def resolve(repo, token: str, record: Record | None = None) -> frozenset[str]:
    """The op ids `token` addresses. Raises `AddressError` when it names nothing or two things."""
    rec = record or Record(repo)
    token = token.strip()
    found = _readings(rec, token)
    if len(found) > 1:
        raise AddressError(f"{token!r} is ambiguous: it is {' and '.join(found)}")
    if not found:
        hits = [h["address"] for h in find(repo, token, limit=5, record=rec)]
        raise AddressError(f"no such address: {token!r}", hits)
    return next(iter(found.values()))


def find(repo, query: str, *, limit: int = 10, record: Record | None = None) -> list[dict]:
    """Names, save subjects, definition names and captured asks containing the query's words, as
    `{address, matched, text}`, the most words matched first."""
    from ugh.intent.manifest import load_manifests
    from ugh.intent.prompts import load_prompts
    from ugh.intent.stint import asks_for_ops, dominant_ask
    from ugh.intent.turns import load_turns

    rec = record or Record(repo)
    words = re.findall(r"\w+", query.lower())
    if not words:
        return []
    hits = []

    def consider(address: str, matched: str, text: str) -> None:
        score = sum(w in text.lower() for w in words)
        if score:
            hits.append((-score, len(hits), {"address": address, "matched": matched, "text": text}))

    for label in sorted(set(rec.names.values())):
        consider(label, "name", label)
    for unit in sorted({u for us in rec.units.values() for u in us if _symbol_kind(u) == "entity"}):
        consider(unit, "definition", unit)
    captured = (set(load_manifests(repo)) | set(load_prompts(repo))
                | {t["key"] for t in load_turns(repo).values() if t["key_kind"] == "sha"})
    for sha in rec.saves():
        consider(sha[:7], "subject", rec.meta[sha][0])
        if sha in captured:
            # ponytail: one manifest read per captured save; one pass over the manifests if slow.
            ask = dominant_ask(asks_for_ops(repo, rec.save_ops(sha), [sha]))
            if ask and ask["text"] != rec.meta[sha][0]:
                consider(sha[:7], "ask", ask["text"])
    return [h for _s, _i, h in sorted(hits, key=lambda h: (h[0], h[1]))][:limit]
