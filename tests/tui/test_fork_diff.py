"""Tests for sgt.tui.fork_diff -- the plain side-by-side fork-tip diff `sgt resolve` renders."""

from __future__ import annotations

from sgt.tui.fork_diff import side_by_side


def test_side_by_side_shows_both_columns_and_a_gutter_on_a_divergent_hunk():
    a = {"m.py": "def foo():\n    return 999\n"}
    b = {"m.py": "def foo():\n    return 42\n"}
    lines = side_by_side(a, b, width=80, color=False)
    text = "\n".join(lines)
    # the shared header line, then both tips' distinct bodies with a `│` replace-gutter between them
    assert "── m.py" in text
    body = next(l for l in lines if "return 999" in l)
    assert "return 999" in body and "return 42" in body and "│" in body


def test_side_by_side_collapses_a_long_equal_run():
    common = "\n".join(f"line{i}" for i in range(20))
    a = {"m.py": f"start_a\n{common}\nend\n"}
    b = {"m.py": f"start_b\n{common}\nend\n"}
    lines = side_by_side(a, b, width=80, color=False)
    text = "\n".join(lines)
    # the 20-line identical middle collapses to a single `… N unchanged …` marker rather than 20 rows
    assert "unchanged …" in text
    assert sum(1 for l in lines if "line" in l) < 20


def test_side_by_side_marks_a_deletion_left_only():
    a = {"m.py": "keep\ngone\nend\n"}  # `gone` present only on the left
    b = {"m.py": "keep\nend\n"}
    text = "\n".join(side_by_side(a, b, width=60, color=False))
    assert "<" in text  # a delete gutter (left-only)


def test_side_by_side_marks_an_insertion_right_only():
    a = {"m.py": "keep\nend\n"}
    b = {"m.py": "keep\nadded\nend\n"}  # `added` present only on the right
    text = "\n".join(side_by_side(a, b, width=60, color=False))
    assert ">" in text  # an insert gutter (right-only)
