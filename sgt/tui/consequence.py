"""The **consequence focus pane**: the interactive confirm step a mutating verb shows on a tty,
in place of the plain `[y/N]` prompt. It answers "so what?" -- leads with a one-line consequence
(``so_what``), draws where the edit lands as a zoomed region of the log
(``render_verb_preview_lines``), and lists only the *act-required* fallout (the toggleable ``blast``
dependents). ``enter`` applies, ``space`` toggles a dependent into the kept-set (adjust), ``esc``
aborts.

A small value-returning ``App[Decision]`` -- NOT the heavy ``SgtTui`` browser (which boots
mining/clustering on mount and returns ``None``). Textual's idiom is ``App[T].run() -> return_value``
set by ``App.exit(value)``. The CLI launches it lazily (so ``textual`` stays an optional extra) and
applies the returned ``Decision`` through its existing apply path; a kept-set is exactly the CLI's
``--keep`` continuation-hollow frontier.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.widgets import DataTable, Label, Static

from sgt.api import so_what_for
from sgt.tui.app import frontier_counts
from sgt.tui.color import color_for
from sgt.tui.graph import render_verb_preview_lines


@dataclass(frozen=True)
class Decision:
    """The pane's outcome handed back to the CLI: whether to apply, and which fallout op-ids the
    user chose to keep alive (the ``--keep`` frontier). Empty ``kept`` = a plain apply."""

    apply: bool
    kept: frozenset[str] = field(default_factory=frozenset)


class ConsequenceApp(App[Decision]):
    """The focus pane for one verb preview. ``run()`` returns a :class:`Decision`."""

    CSS = """
    Screen { layout: vertical; align: center middle; }
    #consequence-modal { width: 90%; height: 90%; padding: 1 2; border: thick $accent; background: $surface; }
    #so-what { height: auto; margin-bottom: 1; }
    #rail-scroll { height: 1fr; }
    #rail-body { width: auto; }
    #fallout-table { height: auto; max-height: 40%; border: round $panel; margin-top: 1; }
    #fallout-counts { height: 1; color: $text-muted; }
    #hint { color: $text-muted; margin-top: 1; }
    """

    # `enter` is priority so the DataTable does not swallow it as a row-selection.
    BINDINGS = [
        Binding("enter", "apply", "Apply", priority=True),
        Binding("space", "toggle", "Keep/drop"),
        Binding("escape", "abort", "Leave"),
    ]

    def __init__(self, pview: dict, map_view: dict | None = None, grid_view: dict | None = None,
                 segments: list[dict] | None = None, *, focus_fid: str | None = None) -> None:
        super().__init__()
        self._pview = pview
        self._map_view = map_view
        self._grid_view = grid_view
        self._segments = segments or []
        self._focus_fid = focus_fid
        # Only toggleable blast rows are the checklist; forks/foundation are not user-adjustable.
        self._blast = [r for r in pview.get("fallout", []) if r.get("kind") == "blast"]
        self._kept: set[str] = set()

    def compose(self) -> ComposeResult:
        with Vertical(id="consequence-modal"):
            yield Static(self._so_what_text(), id="so-what")
            with VerticalScroll(id="rail-scroll"):
                yield Static(self._rail_text(), id="rail-body")
            if self._blast:
                yield DataTable(id="fallout-table", cursor_type="row")
                yield Static("", id="fallout-counts")
            yield Label(self._hint(), id="hint")

    def on_mount(self) -> None:
        if self._blast:
            table = self.query_one("#fallout-table", DataTable)
            table.add_columns("", "op", "")
            self._fill()
            table.focus()

    # -- rendering ----------------------------------------------------------
    def _rail_text(self) -> Text:
        # A metadata verb (merge/rename/move/split) touches no code, so it carries a precomputed
        # `summary` instead of a code rail; render that in the same slot.
        summary = self._pview.get("summary")
        if summary is not None:
            return Text.from_ansi("\n".join(summary))
        lines = render_verb_preview_lines(
            self._map_view, self._grid_view, self._segments, self._pview,
            focus_fid=self._focus_fid, color=True,
        )
        return Text.from_ansi("\n".join(lines))

    def _so_what_text(self) -> Text:
        return Text(so_what_for(self._pview, frozenset(self._kept)), style="bold")

    def _hint(self) -> str:
        # "space keep" only makes sense when there's a toggleable dependent to keep.
        keep = "   ·   [b]space[/b] keep" if self._blast else ""
        carry = self._pview.get("carry_count", 0)
        tail = f"   ·   {carry} auto-repoint" if carry else ""
        return f"[b]enter[/b] apply{keep}   ·   [b]esc[/b] leave{tail}"

    def _fill(self) -> None:
        table = self.query_one("#fallout-table", DataTable)
        table.clear()
        for r in self._blast:
            oid = r["op_id"]
            if oid in self._kept:
                marker, status = Text("✓", style=color_for(oid)), Text("keep as draft", style="dim")
            else:
                marker, status = Text(" "), Text("will break", style="yellow")
            table.add_row(marker, Text(oid[:12], style="dim"), status, key=oid)
        counts = frontier_counts(self._blast, self._kept)
        self.query_one("#fallout-counts", Static).update(Text.assemble(
            (f"{counts['removed']} will break", "bold"), f"  ·  keeps {counts['kept']}",
        ))

    def _refresh_so_what(self) -> None:
        self.query_one("#so-what", Static).update(self._so_what_text())

    # -- actions ------------------------------------------------------------
    def action_toggle(self) -> None:
        if not self._blast:
            return
        table = self.query_one("#fallout-table", DataTable)
        row = table.cursor_row
        if row is None or not (0 <= row < len(self._blast)):
            return
        self._kept.symmetric_difference_update({self._blast[row]["op_id"]})
        self._fill()
        table.move_cursor(row=row)
        self._refresh_so_what()

    def action_apply(self) -> None:
        self.exit(Decision(True, frozenset(self._kept)))

    def action_abort(self) -> None:
        self.exit(Decision(False))


def run_consequence(pview: dict, map_view: dict | None = None, grid_view: dict | None = None,
                    segments: list[dict] | None = None, *, focus_fid: str | None = None) -> Decision:
    """Blocking: show the pane for an already-resolved verb preview, return the user's Decision.
    The CLI passes the views it already fetched for the code rail (revert/restore); a metadata verb
    passes none and the pane renders `pview['summary']` instead."""
    return ConsequenceApp(pview, map_view, grid_view, segments, focus_fid=focus_fid).run()
