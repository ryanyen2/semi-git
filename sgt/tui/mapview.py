"""Pure render helpers for the TUI code-entity map (consumes ``entity_graph_view``).

Kept separate from ``app.py`` so the row-building and column logic are testable without
spinning a Textual app. Same contract as every surface: a feature's hue is its identity
(``color_for``), status/ownership is a glyph — an owned entity is a filled dot in its
feature's hue, an unowned one (untracked / TypeScript / module-level) is a dim hollow dot.
"""

from __future__ import annotations

from dataclasses import dataclass

from rich.text import Text

from sgt.tui.color import color_for

_NARROW = 100  # below this width, drop the owner/component columns


@dataclass(frozen=True)
class MapRow:
    entity_id: str
    label: str  # "file::name"
    kind: str
    node_id: str | None  # owning feature, or None when unattributed
    component: int


def build_map_rows(view: dict) -> list[MapRow]:
    """Entities as rows, grouped by connected component then label (deterministic)."""
    comp_of: dict[str, int] = {}
    for i, comp in enumerate(view.get("components", [])):
        for eid in comp:
            comp_of[eid] = i
    rows = [
        MapRow(
            entity_id=e["id"],
            label=f"{e['file']}::{e['name']}",
            kind=e["kind"],
            node_id=e.get("node_id"),
            component=comp_of.get(e["id"], -1),
        )
        for e in view.get("entities", [])
    ]
    rows.sort(key=lambda r: (r.component, r.label))
    return rows


def entity_marker(node_id: str | None) -> Text:
    """Filled dot in the owning feature's hue, or a dim hollow dot when unattributed."""
    if node_id is None:
        return Text("○", style="dim")
    return Text("●", style=color_for(node_id))


def map_columns(width: int) -> list[str]:
    """Column set for the given terminal width — narrow drops owner/component."""
    if width < _NARROW:
        return ["", "kind", "entity"]
    return ["", "kind", "entity", "owner", "comp"]
