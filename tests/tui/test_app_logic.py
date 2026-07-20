"""Unit tests for the TUI's pure logic (plan U9): `fuzzy_rank`, `frontier_counts`, and
`selection_specs` are module-level functions with no textual-runtime dependency, so they are tested
directly (fast, no pilot). The widget render is smoke-tested separately in `test_app_interactions`.
Textual is imported by `sgt.tui.app` at module load, so this still skips when it is absent."""

import pytest

pytest.importorskip("textual")

from sgt.tui.app import frontier_counts, fuzzy_rank, selection_specs  # noqa: E402


def _rows(*pairs):
    return [{"label": label, "id": nid} for label, nid in pairs]


# -- fuzzy_rank ---------------------------------------------------------------


def test_fuzzy_rank_empty_query_returns_all_rows_unchanged():
    rows = _rows(("Payments", "F1"), ("Auth", "F2"))
    assert fuzzy_rank(rows, "") == rows
    assert fuzzy_rank(rows, "   ") == rows


def test_fuzzy_rank_narrows_to_subsequence_matches():
    rows = _rows(("Payments", "F1"), ("Auth", "F2"), ("Display", "F3"))
    # "pay" is a subsequence of "payments f1" and "display f3" but not "auth f2".
    got = {r["id"] for r in fuzzy_rank(rows, "pay")}
    assert got == {"F1", "F3"}


def test_fuzzy_rank_ranks_a_contiguous_hit_above_a_scattered_one():
    rows = _rows(("Display", "F3"), ("Payments", "F1"))  # both contain a "pay" subsequence
    ranked = fuzzy_rank(rows, "pay")
    # "payments" holds "pay" contiguously; "display" only as a scattered subsequence.
    assert ranked[0]["id"] == "F1"


def test_fuzzy_rank_no_match_returns_empty_not_a_crash():
    rows = _rows(("Payments", "F1"), ("Auth", "F2"))
    assert fuzzy_rank(rows, "zzzznomatch") == []


def test_fuzzy_rank_matches_over_id_too():
    rows = _rows(("Payments", "pkg.py::compute"), ("Auth", "F2"))
    assert [r["id"] for r in fuzzy_rank(rows, "compute")] == ["pkg.py::compute"]


# -- frontier_counts ----------------------------------------------------------


def _frontier():
    return [
        {"op_id": "a", "bucket": "blast", "toggleable": True},
        {"op_id": "b", "bucket": "carry", "toggleable": True},
        {"op_id": "c", "bucket": "foundation", "toggleable": False},
    ]


def test_frontier_counts_default_kept_removes_every_toggleable_dependent():
    c = frontier_counts(_frontier(), kept=set())
    assert c["removed"] == 2 and c["kept"] == 0
    assert c["toggleable"] == ["a", "b"]
    assert c["foundation"] == ["c"]


def test_frontier_counts_keeping_a_dependent_moves_it_out_of_removed():
    c = frontier_counts(_frontier(), kept={"a"})
    assert c["removed"] == 1 and c["kept"] == 1


def test_frontier_counts_ignores_a_kept_id_that_is_not_toggleable():
    # foundation ("c") is read-only: it can never be "kept" and never counts as removed.
    c = frontier_counts(_frontier(), kept={"c", "stale"})
    assert c["removed"] == 2 and c["kept"] == 0


def test_frontier_counts_empty_rows():
    c = frontier_counts([], kept=set())
    assert c == {"toggleable": [], "foundation": [], "kept": 0, "removed": 0}


# -- selection_specs ----------------------------------------------------------


def test_selection_specs_dedups_sorts_and_drops_empties():
    assert selection_specs(["F2", "F1", "F2", "", "pkg.py::compute"]) == [
        "F1", "F2", "pkg.py::compute",
    ]
