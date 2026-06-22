"""U6 — TUI code-entity map render helpers (pure; no Textual app needed)."""

from __future__ import annotations

from sgt.tui.mapview import MapRow, build_map_rows, entity_marker, map_columns


def _view():
    return {
        "entities": [
            {"id": "m.py::caller", "name": "caller", "file": "m.py", "kind": "function", "node_id": "A"},
            {"id": "m.py::callee", "name": "callee", "file": "m.py", "kind": "function", "node_id": None},
        ],
        "components": [["m.py::caller", "m.py::callee"]],
        "count": 2,
    }


def test_build_map_rows_groups_and_preserves_owner():
    rows = build_map_rows(_view())
    assert len(rows) == 2
    assert all(isinstance(r, MapRow) for r in rows)
    by_label = {r.label: r for r in rows}
    assert by_label["m.py::caller"].node_id == "A"
    assert by_label["m.py::callee"].node_id is None
    # Both in component 0, sorted by label (callee before caller).
    assert [r.label for r in rows] == ["m.py::callee", "m.py::caller"]
    assert all(r.component == 0 for r in rows)


def test_entity_marker_owned_vs_dim():
    owned = entity_marker("A")
    assert owned.plain == "●" and "dim" not in str(owned.style)
    unowned = entity_marker(None)
    assert unowned.plain == "○" and "dim" in str(unowned.style)


def test_empty_view_renders_no_rows_without_raising():
    assert build_map_rows({}) == []
    assert build_map_rows({"entities": [], "components": []}) == []


def test_map_columns_narrow_drops_owner_and_component():
    wide = map_columns(200)
    narrow = map_columns(80)
    assert "owner" in wide and "comp" in wide
    assert "owner" not in narrow and "comp" not in narrow
    assert len(narrow) < len(wide)
