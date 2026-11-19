"""Addresses: how a verb is told which steps it acts on, and how every step is printed.

    <label>                 the steps named label
    <label>@<rev>           those steps in one save
    <path>                  the steps on one file's units
    <path>::<def>           the steps on one top-level definition
    <path>::<def>@<rev>     the one step on that definition in that save
    <rev>                   one save's steps (anything `git rev-parse --verify <rev>^{commit}` takes)
    <rev>..<rev>, <rev>..   the steps of the saves in that git range (`<rev>..` runs through HEAD)

An address resolves over every step in the record, in the code or out. A token with two readings is
refused naming both, a reading with no step (a save that did not change the definition) is refused,
and anything else is refused with the nearest `find` hits. A step prints as `path::def@<short sha>`,
or `path@<short sha>` when it is on a whole file, a file's head or an import.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

from ugh import names
from ugh.core.op import _symbol_kind, step_address, unit_of


class AddressError(Exception):
    """A token that names nothing, or names two things. `hits` are the nearest `find` hits."""

    def __init__(self, message: str, hits=()):
        super().__init__(message)
        self.hits = list(hits)


class Record:
    """Every step of the commits in HEAD's history (`planner.Step`): its save, its name, and whether
    it is out."""

    def __init__(self, repo):
        from ugh.core import planner

        self.repo = Path(repo)
        self.ctx = planner._Ctx(self.repo)
        self.gb = self.ctx.gb
        self.names = names.load(self.repo)
        self.meta = {sha: (subject, ts, bk) for sha, _p, subject, ts, bk in self.gb.history_meta()}

    def ops(self, steps) -> list[str]:
        """The ops under `steps`; naming a step labels all of them."""
        return sorted({o for st in steps for o in self.ctx.steps[st]})

    def carriers(self, st) -> list[str]:
        """The ops whose label is the step's name: its op on the unit's own symbol (the definition,
        import or whole file), or for a step with no such op (a gap change only), its gap op."""
        ops = self.ctx.steps[st]
        return [o for o in ops if st.unit in self.ctx.by_id[o].footprint] or ops

    def name(self, st) -> str | None:
        return next((self.names[o] for o in self.carriers(st) if o in self.names), None)

    def step(self, st) -> dict:
        return {"address": step_address(st.unit, st.sha), "kind": self.ctx.kind(st),
                "name": self.name(st), "out": st in self.ctx.out}

    def steps(self, steps, *, detail: bool = False) -> list[dict]:
        """The rows of `steps`, oldest save first. `detail` adds each step's `unit`, its text
        `before` and `after` the save, and the `lines` (first, last) its unit spans at HEAD."""
        ordered = sorted(steps, key=lambda st: (self.ctx.index[st.sha], step_address(st.unit, st.sha)))
        if not detail:
            return [self.step(st) for st in ordered]
        from ugh.core.planner import _spans

        head = self.gb.head()

        def text(data: bytes | None) -> str | None:
            return None if data is None else data.decode("utf-8", "replace")

        def lines(unit: str) -> list[int] | None:
            path = unit.split("::", 1)[0]
            f = self.ctx.file(head, path)
            span = _spans(path, f).get(unit) if f is not None else None
            return [span.start, span.stop - 1] if span else None

        return [{**self.step(st), "unit": st.unit, "before": text(self.ctx.before(st)),
                 "after": text(self.ctx.after(st)), "lines": lines(st.unit)} for st in ordered]

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


def _named(rec: Record, token: str) -> dict[str, frozenset]:
    """The label, file and definition readings of `token`."""
    out = {}
    if token in rec.names.values():
        out[f"the name {token!r}"] = frozenset(st for st in rec.ctx.steps if rec.name(st) == token)
    on_file = frozenset(st for st in rec.ctx.steps if st.unit.split("::", 1)[0] == token)
    if on_file:
        out[f"the file {token}"] = on_file
    if "::" in token:
        unit = unit_of(token)
        if unit in rec.ctx.unit_steps:
            out[f"the definition {unit}"] = frozenset(rec.ctx.unit_steps[unit])
    return out


def _readings(rec: Record, token: str) -> dict[str, frozenset]:
    out = _named(rec, token)
    if ".." in token:
        start, _, end = token.partition("..")
        a, b = rec.rev(start), rec.rev(end or "HEAD")
        if a and b:
            shas = {sha for sha, _p, _s in rec.gb.history(a, b)}
            out[f"the saves {start}..{end}"] = frozenset(st for st in rec.ctx.steps if st.sha in shas)
        return out
    sha = rec.rev(token)
    if sha:
        out[f"the save {sha[:7]}"] = frozenset(rec.ctx.of_commit.get(sha, ()))
    base, at, rev = token.rpartition("@")
    sha = rec.rev(rev) if at and base else None
    if sha:
        for what, steps in _named(rec, base).items():
            out[f"{what} in {sha[:7]}"] = frozenset(st for st in steps if st.sha == sha)
    return out


def resolve(repo, token: str, record: Record | None = None) -> frozenset:
    """The steps `token` addresses. Raises `AddressError` when it names nothing, two things, or no
    step (a save that did not change the definition named)."""
    rec = record or Record(repo)
    token = token.strip()
    found = _readings(rec, token)
    if len(found) > 1:
        raise AddressError(f"{token!r} is ambiguous: it is {' and '.join(found)}")
    if not found:
        hits = [h["address"] for h in find(repo, token, limit=5, record=rec)]
        raise AddressError(f"no such address: {token!r}", hits)
    ((what, steps),) = found.items()
    if not steps:
        raise AddressError(f"no step at {token!r}: {what} has none")
    return steps


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
    for unit in sorted(u for u in rec.ctx.unit_steps if _symbol_kind(u) == "entity"):
        consider(unit, "definition", unit)
    captured = (set(load_manifests(repo)) | set(load_prompts(repo))
                | {t["key"] for t in load_turns(repo).values() if t["key_kind"] == "sha"})
    for sha in rec.saves():
        consider(sha[:7], "subject", rec.meta[sha][0])
        if sha in captured:
            # ponytail: one manifest read per captured save; one pass over the manifests if slow.
            ask = dominant_ask(asks_for_ops(repo, rec.ops(rec.ctx.of_commit.get(sha, ())), [sha]))
            if ask and ask["text"] != rec.meta[sha][0]:
                consider(sha[:7], "ask", ask["text"])
    return [h for _s, _i, h in sorted(hits, key=lambda h: (h[0], h[1]))][:limit]
