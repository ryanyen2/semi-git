"""Tests for the tiered entity matcher (ported from
``experiments/patch_clustering/test_identity_match.py`` per plan U2 -- no path shim needed here,
pytest discovers it under ``testpaths`` directly). The first two guard the robustness sem gave
us (renames/moves link); the last three guard the safety sem's guards give us (unrelated
same-name entities and size-mismatched bodies must NOT link).
"""

from __future__ import annotations

import dataclasses

from sgt.core.identity import detect_splits_merges, link_residual, match_pair, snapshot
from sgt.entities.extract import extract_file

_BODY = """def {name}(nodes):
    total = 0
    accumulator = []
    for n in nodes:
        total = total + n
        accumulator.append(total)
    return accumulator"""


def _snap(name, file, kind, body):
    # Extract for real so the entity carries the content/structural hashes that extract.py now
    # computes from the AST, then relabel it to the id/name/file/kind the test wants.
    (base,) = extract_file("t.py", body)  # each test body is exactly one top-level def
    e = dataclasses.replace(base, id=f"{file}::{name}", name=name, file=file, kind=kind)
    return snapshot([e], body)[0]


def test_within_file_rename_links():
    """foo -> bar (same body) in one file was delete+add; must now be a single link."""
    before = [_snap("foo", "x.py", "function", _BODY.format(name="foo"))]
    after = [_snap("bar", "x.py", "function", _BODY.format(name="bar"))]
    m = match_pair(before, after)
    assert len(m.links) == 1, m.links
    assert m.added == [] and m.removed == [] and m.modified == []
    old, new = m.links[0]
    assert (old.ent.id, new.ent.id) == ("x.py::foo", "x.py::bar")


def test_cross_file_move_links():
    """A function cut from a.py and pasted verbatim into b.py links by body hash."""
    body = _BODY.format(name="layout")
    removed = [_snap("layout", "a.py", "function", body)]
    added = [_snap("layout", "b.py", "function", body)]
    links, mr, ma = link_residual(removed, added)
    assert len(links) == 1, links
    assert mr == {"a.py::layout"} and ma == {"b.py::layout"}


def test_unrelated_same_name_not_linked():
    """Two unrelated __init__ (the collision that made name-matching unsafe) must not link."""
    a = "def __init__(self):\n    self.alpha = 1\n    self.beta = compute(2)\n    self.gamma = 3"
    b = "def __init__(self):\n    self.socket = connect(url)\n    self.buffer = read(s)\n    self.parser = P()"
    removed = [_snap("A.__init__", "a.py", "method", a)]
    added = [_snap("B.__init__", "b.py", "method", b)]
    links, mr, ma = link_residual(removed, added)
    assert links == [] and mr == set() and ma == set()


def test_size_guard_rejects_mismatch():
    """Same name but a tiny body vs a huge one: the size-ratio guard must reject the link."""
    tiny = "def helper():\n    return 1"
    huge = _BODY.format(name="helper") + "\n    " + "\n    ".join(f"step{i}()" for i in range(40))
    removed = [_snap("helper", "a.py", "function", tiny)]
    added = [_snap("helper", "b.py", "function", huge)]
    links, mr, ma = link_residual(removed, added)
    assert links == [], links


def test_exact_id_modified_and_unchanged():
    """Same surface id: content change -> modified; identical content -> no patch."""
    changed = match_pair(
        [_snap("foo", "x.py", "function", _BODY.format(name="foo"))],
        [_snap("foo", "x.py", "function", _BODY.format(name="foo") + "\n    return 0")],
    )
    assert len(changed.modified) == 1 and changed.links == []
    assert changed.added == [] and changed.removed == []

    same = _BODY.format(name="foo")
    unchanged = match_pair(
        [_snap("foo", "x.py", "function", same)],
        [_snap("foo", "x.py", "function", same)],
    )
    assert unchanged.modified == [] and unchanged.added == [] and unchanged.removed == []


def test_split_detects_one_to_many():
    """A function whose two halves are extracted into two new functions is a SPLIT, not add+delete."""
    acc = ("    total = 0\n    accumulator = []\n    for n in items:\n"
           "        total = total + n\n        accumulator.append(total)\n")
    ddp = ("    seen = set()\n    unique = []\n    for x in items:\n"
           "        if x not in seen:\n            seen.add(x)\n            unique.append(x)\n")
    process = f"def process(items):\n{acc}{ddp}    return accumulator, unique"
    a1 = f"def accumulate(items):\n{acc}    return accumulator"
    a2 = f"def dedupe(items):\n{ddp}    return unique"
    removed = [_snap("process", "m.py", "function", process)]
    added = [_snap("accumulate", "m.py", "function", a1), _snap("dedupe", "m.py", "function", a2)]
    splits, merges = detect_splits_merges(removed, added)
    assert len(splits) == 1 and merges == [], (splits, merges)
    assert splits[0]["from"] == "m.py::process"
    assert set(splits[0]["to"]) == {"m.py::accumulate", "m.py::dedupe"}


def test_unrelated_residuals_are_not_a_split():
    """Two unrelated new functions in a file that lost one unrelated function must NOT be a split."""
    removed = [_snap("teardown", "m.py", "function", "def teardown(self):\n    self.conn.close()\n    self.pool.drain()")]
    added = [
        _snap("parse", "m.py", "function", "def parse(text):\n    tokens = text.split()\n    return [t.lower() for t in tokens]"),
        _snap("render", "m.py", "function", "def render(node):\n    html = node.tag\n    return f'<{html}>{node.body}</{html}>'"),
    ]
    splits, merges = detect_splits_merges(removed, added)
    assert splits == [] and merges == [], (splits, merges)
