"""Tests for sgt.core.patch -- the three-way subtraction merge behind safe revert."""

from __future__ import annotations

from sgt.core.patch import merge3


BASE = b"def build():\n    a()\n    waitlist()\n    b()\n"        # after the removed op
THEIRS = b"def build():\n    a()\n    b()\n"                      # before the removed op
OURS = b"def build():\n    a()\n    waitlist()\n    b()\n    c()\n"  # later work added c()


def test_disjoint_subtraction_keeps_later_work():
    result = merge3(BASE, OURS, THEIRS)
    assert not result.conflicted and result.changed
    assert result.merged == b"def build():\n    a()\n    b()\n    c()\n"


def test_no_later_work_returns_theirs():
    result = merge3(BASE, BASE, THEIRS)
    assert not result.conflicted and result.changed
    assert result.merged == THEIRS


def test_noop_when_base_equals_theirs():
    result = merge3(BASE, OURS, BASE)
    assert not result.conflicted and not result.changed
    assert result.merged == OURS


def test_same_line_overlap_conflicts_and_keeps_ours():
    base = b"def f():\n    return 2\n"
    ours = b"def f():\n    return 3\n"     # later work rewrote the same line
    theirs = b"def f():\n    return 1\n"   # subtracting would also rewrite it
    result = merge3(base, ours, theirs)
    assert result.conflicted and not result.changed
    assert result.merged == ours


def test_insertion_at_edited_seam_conflicts():
    base = b"a\nb\nc\n"
    ours = b"a\nB\nc\n"      # ours replaced line b
    theirs = b"a\nb\nX\nc\n"  # theirs inserts right at b's trailing seam
    result = merge3(base, ours, theirs)
    assert result.conflicted


def test_multiple_disjoint_hunks_compose():
    base = b"one\ntwo\nthree\nfour\nfive\n"
    theirs = b"one\nthree\nfour\n"          # removed "two" and "five"
    ours = b"one\ntwo\nthree\nFOUR\nfive\n"  # later work rewrote "four"
    result = merge3(base, ours, theirs)
    assert not result.conflicted
    assert result.merged == b"one\nthree\nFOUR\n"
