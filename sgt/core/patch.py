"""Three-way line merge for forward subtraction (safe revert, U8 follow-up 2026-08-09).

`merge3(base, ours, theirs)` applies the change `base -> theirs` onto `ours`, where all three
are byte images of one symbol:

    base   = the symbol right AFTER the op being subtracted (the removed contribution's result)
    ours   = the symbol's live tip today (later work included)
    theirs = the symbol right BEFORE the op being subtracted

The classic diff3 shape, used here in exactly one direction: subtracting a mid-chain op's
contribution from the tip without touching the later work layered above it. A region changed
only in `theirs` is the removed op's own contribution -- inverted. A region changed only in
`ours` is later work -- kept verbatim. A region changed in both is a genuine overlap the fold
must never guess about -- reported as a conflict, and the caller keeps `ours` unchanged and
surfaces the symbol for a human edit (never a silent demolition, never a refusal deadlock).

Deterministic (SequenceMatcher with autojunk off), pure, and line-based: symbol images are
source text, and line granularity is what a developer can review in a preview.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass


@dataclass(frozen=True)
class Merge3Result:
    merged: bytes  # `ours` verbatim when `conflicted`
    conflicted: bool
    changed: bool  # False when the subtraction is a no-op (nothing of base->theirs applies)


def _lines(data: bytes) -> list[bytes]:
    return data.splitlines(keepends=True)


def _changes(base_lines: list[bytes], other_lines: list[bytes]) -> list[tuple[int, int, list[bytes]]]:
    """Non-equal opcodes of base -> other, as (base_start, base_end, replacement_lines)."""
    matcher = difflib.SequenceMatcher(a=base_lines, b=other_lines, autojunk=False)
    return [
        (i1, i2, other_lines[j1:j2])
        for tag, i1, i2, j1, j2 in matcher.get_opcodes()
        if tag != "equal"
    ]


def _overlaps(a: tuple[int, int], b: tuple[int, int]) -> bool:
    """Whether two base-coordinate regions collide. Zero-width regions (pure insertions) collide
    with anything they sit inside or exactly on: two edits inserting at the same seam, or an
    insertion into a replaced region, cannot be ordered mechanically."""
    (a1, a2), (b1, b2) = a, b
    if a1 == a2 and b1 == b2:
        return a1 == b1
    if a1 == a2:
        return b1 <= a1 <= b2
    if b1 == b2:
        return a1 <= b1 <= a2
    return a1 < b2 and b1 < a2


def merge3(base: bytes, ours: bytes, theirs: bytes) -> Merge3Result:
    if base == theirs:
        return Merge3Result(merged=ours, conflicted=False, changed=False)
    if base == ours:
        # No later work on this symbol: the subtraction is exactly `theirs`.
        return Merge3Result(merged=theirs, conflicted=False, changed=True)

    base_lines = _lines(base)
    ours_changes = _changes(base_lines, _lines(ours))
    theirs_changes = _changes(base_lines, _lines(theirs))

    for t_start, t_end, _ in theirs_changes:
        for o_start, o_end, _ in ours_changes:
            if _overlaps((t_start, t_end), (o_start, o_end)):
                return Merge3Result(merged=ours, conflicted=True, changed=False)

    # Disjoint: walk base once, taking each side's replacement at its own regions.
    regions = sorted(
        [(start, end, repl, "theirs") for start, end, repl in theirs_changes]
        + [(start, end, repl, "ours") for start, end, repl in ours_changes],
        key=lambda r: (r[0], r[1]),
    )
    merged: list[bytes] = []
    cursor = 0
    for start, end, replacement, _side in regions:
        merged.extend(base_lines[cursor:start])
        merged.extend(replacement)
        cursor = end
    merged.extend(base_lines[cursor:])
    out = b"".join(merged)
    return Merge3Result(merged=out, conflicted=False, changed=out != ours)
