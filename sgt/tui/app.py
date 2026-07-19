"""A compact Textual TUI over the feature lens (plan U13): browse the hierarchical feature tree,
inspect a feature, preview/apply a feature revert, and rename a feature — all driven through the
same ``sgt.api`` projection (``map_view``/``status_view``) the CLI and the VS Code extension
consume, plus ``sgt.lens.verbs``/``sgt.core.verbs`` for the two mutations.

Design is in-situ and consistent with the other surfaces: a feature's **hue is its identity**
(the same OKLCH color as the editor gutter and the graph webview); a node's **kind is a glyph**
(``●`` feature, ``▸`` subsystem), never a hue, so a hue always means one specific feature. The
detail pane is the only prose. Reads are offline (`sgt map`'s LLM call has an offline fallback);
mutations confirm, then commit exactly as the CLI would. The detail pane folds into a modal on a
narrow terminal, matching the other surfaces' responsive discipline.
"""

from __future__ import annotations

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import DataTable, Footer, Header, Input, Label, Static

from sgt.tui.color import color_for

_KIND_GLYPH = {"feature": "●", "subsystem": "▸"}
_NARROW = 100  # below this terminal width, fold the detail pane into a modal


def _flatten(view: dict) -> list[dict]:
    """DFS `map_view`'s `nodes`/`roots` into a display-ordered list, each row carrying its
    tree depth (for indentation) alongside the node's own fields."""
    by_id = {n["id"]: n for n in view["nodes"]}
    rows: list[dict] = []

    def visit(nid: str, depth: int) -> None:
        node = by_id[nid]
        rows.append({**node, "depth": depth})
        for child in node["children"]:
            visit(child, depth + 1)

    for root in view["roots"]:
        visit(root, 0)
    return rows


def _glyph(kind: str, nid: str) -> Text:
    return Text(_KIND_GLYPH.get(kind, "●"), style=color_for(nid))


def _detail_text(n: dict) -> Text:
    """Render a node's detail view (shared by the side pane and the narrow-mode modal)."""
    ident = color_for(n["id"])
    t = Text()
    t.append(f"{n['label']}\n\n", style=f"bold {ident}")
    t.append(f"{n['kind']} · {n['id']}\n", style="dim")
    t.append(f"dir: {n.get('dir') or '—'}\n", style="dim")
    t.append(f"\n{n['size']} member(s) · {n['op_count']} op(s)\n")
    if n.get("why"):
        t.append(f"\n{n['why']}\n")
    if n.get("split_reason"):
        t.append(f"\nsplit reason: {n['split_reason']}\n", style="dim")
    return t


class ConfirmScreen(ModalScreen[bool]):
    """A yes/no modal returned to the caller as a bool."""

    BINDINGS = [Binding("y", "yes", "Yes"), Binding("n,escape", "no", "No")]

    def __init__(self, prompt: str) -> None:
        super().__init__()
        self._prompt = prompt

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label(self._prompt, id="prompt")
            yield Label("[b]y[/b] apply   ·   [b]n[/b] cancel", id="hint")

    def action_yes(self) -> None:
        self.dismiss(True)

    def action_no(self) -> None:
        self.dismiss(False)


class RenameScreen(ModalScreen[str | None]):
    """A single-line input modal, pre-filled with the current label; returns the new label, or
    `None` on cancel/empty submit."""

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, current_label: str) -> None:
        super().__init__()
        self._current_label = current_label

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label("New label:", id="prompt")
            yield Input(value=self._current_label, id="rename-input")
            yield Label("[b]enter[/b] confirm   ·   [b]esc[/b] cancel", id="hint")

    def on_mount(self) -> None:
        self.query_one("#rename-input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip() or None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class DetailScreen(ModalScreen[None]):
    """Full-screen detail, used on narrow terminals where the side pane is folded away."""

    BINDINGS = [Binding("escape,q,enter", "close", "Close")]

    def __init__(self, node: dict) -> None:
        super().__init__()
        self._node = node

    def compose(self) -> ComposeResult:
        with Vertical(id="detail-modal"):
            yield Static(_detail_text(self._node))
            yield Label("[b]esc[/b] close", id="hint")

    def action_close(self) -> None:
        self.dismiss(None)


class SgtTui(App[None]):
    TITLE = "semi-git"
    CSS = """
    Screen { layout: vertical; }
    #body { height: 1fr; }
    #nodes-pane { width: 3fr; }
    #filter { border: round $panel; height: 3; }
    #nodes { height: 1fr; border: round $panel; }
    #side { width: 2fr; border: round $panel; padding: 0 1; }
    #status-line { height: 1; background: $boost; color: $text; padding: 0 1; }
    ConfirmScreen, RenameScreen { align: center middle; }
    #dialog { width: 60; height: auto; padding: 1 2; border: thick $accent; background: $surface; }
    DetailScreen { align: center middle; }
    #detail-modal { width: 80%; height: 80%; padding: 1 2; border: thick $accent; background: $surface; }
    #hint { color: $text-muted; margin-top: 1; }
    """
    BINDINGS = [
        Binding("f5", "refresh", "Refresh"),
        Binding("slash", "focus_filter", "Filter"),
        Binding("r", "preview_revert", "Preview revert"),
        Binding("X", "apply_revert", "Revert!"),  # mutating ops are uppercase, apart from previews
        Binding("R", "rename", "Rename"),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(self, repo: str = ".") -> None:
        super().__init__()
        self.repo = repo
        self._ids: list[str] = []
        self._rows: list[dict] = []
        self._last_status: dict = {}
        self._filter = ""
        self._narrow = False

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield Static("", id="status-line")
        with Horizontal(id="body"):
            with Vertical(id="nodes-pane"):
                yield Input(placeholder="Filter features…  (press / )", id="filter")
                yield DataTable(id="nodes", cursor_type="row", zebra_stripes=True)
            yield Static("", id="side", expand=True)
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#nodes", DataTable)
        table.add_columns("", "feature", "size", "ops")
        self._apply_responsive()
        self.action_refresh()
        table.focus()  # the table (not the filter input) owns letter-key bindings by default

    # -- layout ------------------------------------------------------------
    def on_resize(self) -> None:
        self._apply_responsive()

    def _apply_responsive(self) -> None:
        narrow = (self.size.width or 80) < _NARROW
        if narrow != self._narrow:
            self._narrow = narrow
            self.query_one("#side", Static).display = not narrow
        else:
            self._narrow = narrow

    # -- data ----------------------------------------------------------------
    def action_refresh(self) -> None:
        """(Re)build the feature tree from the live op store, then re-read the projection --
        mirrors `sgt map`'s CLI behavior exactly (mine-on-contact, cluster, label, save)."""
        from sgt.api import map_view, status_view
        from sgt.core.lens import get
        from sgt.lens.map import build_map

        get(self.repo)
        build_map(self.repo)
        self._rows = _flatten(map_view(self.repo))
        self._last_status = status_view(self.repo)
        self._populate()
        self._render_status(self._last_status)

    def _populate(self) -> None:
        """(Re)fill the table from the cached rows, honoring the active filter."""
        table = self.query_one("#nodes", DataTable)
        prev = self._selected_id()
        table.clear()
        self._ids = []
        f = self._filter
        for n in self._rows:
            if f and f not in f"{n['label']} {n['id']}".lower():
                continue
            indent = "  " * n["depth"]
            ident = color_for(n["id"])
            table.add_row(
                _glyph(n["kind"], n["id"]),
                Text(f"{indent}{n['label']}", style=ident if n["kind"] == "feature" else "dim"),
                Text(str(n["size"]), style="dim"),
                Text(str(n["op_count"]), style="dim"),
                key=n["id"],
            )
            self._ids.append(n["id"])
        if prev and prev in self._ids:
            table.move_cursor(row=self._ids.index(prev))
        if not self._narrow:
            self._render_detail()

    def _render_status(self, st: dict) -> None:
        line = self.query_one("#status-line", Static)
        drift = st["drift"]
        drift_txt = (
            Text(f"  ⚠ drift: {len(drift['paths'])} path(s)", style="yellow")
            if drift["any"]
            else Text("  ✓ in sync", style="green")
        )
        oracle_txt = f"  ·  oracle: {st['oracle']['status']}" if st["oracle"]["configured"] else ""
        indexing_txt = (
            ("  ·  ⟳ indexing history", "yellow") if not st["sync_status"]["complete"] else ""
        )
        shown = f"  ·  showing {len(self._ids)}/{len(self._rows)}" if self._filter else ""
        msg = Text.assemble(
            (f"{st['features']} feature(s)", "bold"),
            f"  ·  {st['files']} file(s)  ·  {st['symbols']} symbol(s)  ·  "
            f"{st['coverage_fraction'] * 100:.0f}% coverage",
            oracle_txt,
            indexing_txt,
            shown,
            drift_txt,
        )
        line.update(msg)

    def _selected_id(self) -> str | None:
        table = self.query_one("#nodes", DataTable)
        if table.cursor_row is None or not (0 <= table.cursor_row < len(self._ids)):
            return None
        return self._ids[table.cursor_row]

    def _current_row(self) -> dict | None:
        nid = self._selected_id()
        if nid is None:
            return None
        return next((n for n in self._rows if n["id"] == nid), None)

    def _render_detail(self) -> None:
        side = self.query_one("#side", Static)
        row = self._current_row()
        if row is None:
            side.update("Select a feature.")
            return
        side.update(_detail_text(row))

    def on_data_table_row_highlighted(self, _event: DataTable.RowHighlighted) -> None:
        if not self._narrow:
            self._render_detail()

    def on_data_table_row_selected(self, _event: DataTable.RowSelected) -> None:
        # On a narrow terminal the side pane is folded away; show detail on demand as a modal.
        if self._narrow:
            row = self._current_row()
            if row is not None:
                self.push_screen(DetailScreen(row))

    # -- filter ------------------------------------------------------------
    def action_focus_filter(self) -> None:
        self.query_one("#filter", Input).focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "filter":
            self._filter = event.value.lower().strip()
            self._populate()
            if self._last_status:
                self._render_status(self._last_status)  # cached; no per-keystroke rebuild

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "filter":
            self.query_one("#nodes", DataTable).focus()

    # -- preview (dry-run, nothing written) ---------------------------------
    def action_preview_revert(self) -> None:
        row = self._current_row()
        if row is None:
            return
        from sgt.core.lens import get
        from sgt.lens.verbs import plan_revert_feature

        get(self.repo)
        preview = plan_revert_feature(self.repo, row["id"])
        if not preview.ok:
            self.notify(preview.message or "refused", severity="warning", title="Would be refused")
            return
        self.notify(
            f"removes {len(preview.removed)} op(s); affects {len(preview.affected_symbols)} symbol(s)",
            title=f"Preview revert {row['id']} (nothing written)",
        )

    # -- mutations (confirm, then commit) -----------------------------------
    def action_apply_revert(self) -> None:
        row = self._current_row()
        if row is None:
            return

        def done(confirmed: bool | None) -> None:
            if not confirmed:
                return
            from sgt.core import verbs
            from sgt.core.lens import get
            from sgt.lens.verbs import plan_revert_feature

            get(self.repo)
            preview = plan_revert_feature(self.repo, row["id"])
            if not preview.ok:
                self.notify(preview.message or "refused", severity="error", title="✗ refused")
                return
            verbs.apply(self.repo, preview)
            self.notify(f"reverted {len(preview.removed)} op(s)", title="✓ done")
            self.action_refresh()

        self.push_screen(
            ConfirmScreen(f"Revert feature {row['id']} ({row['label']})?\nThis is an exact ideal edit and commits."),
            done,
        )

    def action_rename(self) -> None:
        row = self._current_row()
        if row is None:
            return

        def done(new_label: str | None) -> None:
            if not new_label:
                return
            from sgt.lens.verbs import apply_rename, plan_rename

            preview = plan_rename(self.repo, row["id"], new_label)
            if not preview.ok:
                self.notify(preview.message or "refused", severity="error", title="✗ refused")
                return
            apply_rename(self.repo, preview)
            self.notify(f"renamed to {new_label!r}", title="✓ done")
            self.action_refresh()

        self.push_screen(RenameScreen(row["label"]), done)


def run(repo: str = ".") -> None:
    SgtTui(repo).run()
