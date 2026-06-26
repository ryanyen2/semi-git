"""A compact Textual TUI over the semantic graph: browse the DAG, inspect a node, preview a
plug-out, and apply graph ops — all driven through the same ``sgt.api`` projection the CLI and
the VSCode extension use, plus the orchestrator for mutations.

Design is in-situ and consistent with the other surfaces: a feature's **hue is its identity**
(the same OKLCH color as the editor gutter and the graph webview); **status is a glyph + dim**,
never a hue — so green never means "active" here while meaning "feature X" over there. The detail
pane is the only prose. Reads are offline; mutations confirm, then re-materialize + commit through
the orchestrator exactly as the CLI would. Layout is responsive: the detail pane folds into a modal
on a narrow terminal, and the intent column tracks the available width.
"""

from __future__ import annotations

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import DataTable, Footer, Header, Input, Label, Static

from sgt.api import export_view, show_view, status_view
from sgt.project import Project
from sgt.tui.color import color_for

# Status -> glyph. The glyph (shape) carries status; the hue carries identity. `quarantined`
# additionally flags red; `suspended` additionally dims the row.
_GLYPH = {"active": "●", "planned": "○", "suspended": "◐", "quarantined": "⚠"}
_NARROW = 100  # below this terminal width, fold the detail pane into a modal


def _glyph(status: str, nid: str) -> Text:
    """The status glyph, hued by identity (quarantined overrides to red, suspended dims)."""
    g = _GLYPH.get(status, "●")
    if status == "quarantined":
        return Text(g, style="red")
    style = color_for(nid)
    if status == "suspended":
        style += " dim"
    return Text(g, style=style)


def _detail_text(v: dict) -> Text:
    """Render a node's detail view (shared by the side pane and the narrow-mode modal)."""
    if "error" in v:
        return Text(v["error"], style="red")
    nid = v["id"]
    ident = color_for(nid)
    g = _GLYPH.get(v["status"], "●")
    t = Text()
    t.append(f"{g} ", style="red" if v["status"] == "quarantined" else ident)
    t.append(f"{v['intent']}\n\n", style=f"bold {ident}")
    t.append(f"{v['kind']} · {v['status']} · {v['id']}\n", style="dim")
    t.append("\ndepends on: ", style="bold")
    t.append((", ".join(v["depends_on"]) or "—") + "\n")
    t.append("dependents: ", style="bold")
    t.append((", ".join(v["dependents"]) or "—") + "\n")
    if v.get("provenance"):
        t.append("\nprovenance:\n", style="bold")
        for p in v["provenance"]:
            t.append(f"  · {p}\n", style="dim")
    if isinstance(v.get("conflict"), dict):
        t.append(f"\n⚠ {v['conflict']['reason']}\n", style="red")
        for h in v["conflict"].get("held", []):
            t.append(f"  held: {h}\n", style="red dim")
    t.append("\neffects:\n", style="bold")
    for e in v.get("effects", []):
        t.append(f"  {e['op']} {e['target']} ({e['file']})\n", style="dim")
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


class DetailScreen(ModalScreen[None]):
    """Full-screen detail, used on narrow terminals where the side pane is folded away."""

    BINDINGS = [Binding("escape,q,enter", "close", "Close")]

    def __init__(self, view: dict) -> None:
        super().__init__()
        self._view = view

    def compose(self) -> ComposeResult:
        with Vertical(id="detail-modal"):
            yield Static(_detail_text(self._view))
            yield Label("[b]esc[/b] close", id="hint")

    def action_close(self) -> None:
        self.dismiss(None)


class DecisionScreen(ModalScreen[None]):
    """The decision graph: decisions grouped by feature lane, with the in-force frontier marked.

    Status is a glyph (● in force / ◇ not), never hue; the lane keeps its feature identity color.
    """

    BINDINGS = [Binding("escape,q,m", "close", "Close")]

    def __init__(self, view: dict) -> None:
        super().__init__()
        self._view = view

    def compose(self) -> ComposeResult:
        in_force = set(self._view.get("frontier", {}).values())
        decisions = sorted(self._view.get("decisions", []), key=lambda d: (d["feature"], d["landing"]))
        with Vertical(id="detail-modal"):
            yield Label(
                f"Decision graph — {self._view.get('count', 0)} decisions, "
                f"{len(self._view.get('frontier', {}))} lanes in force", id="map-title")
            table = DataTable(id="map-table", cursor_type="row", zebra_stripes=True)
            yield table
            yield Label("[b]esc[/b] close · ● in force  ◇ not", id="hint")
        self._rows = [(in_force, d) for d in decisions]

    def on_mount(self) -> None:
        from sgt.tui.color import color_for

        table = self.query_one("#map-table", DataTable)
        table.add_columns("", "lane", "decision", "kind", "intent")
        for in_force, d in self._rows:
            mark = "●" if d["id"] in in_force else "◇"
            lane = f"[{color_for(d['feature'])}]{d['feature']}[/]"
            table.add_row(mark, lane, d["id"], d["lifecycle"]["kind"], d["intent"]["decision"][:48])

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
    ConfirmScreen { align: center middle; }
    #dialog { width: 60; height: auto; padding: 1 2; border: thick $accent; background: $surface; }
    DetailScreen { align: center middle; }
    #detail-modal { width: 80%; height: 80%; padding: 1 2; border: thick $accent; background: $surface; }
    #hint { color: $text-muted; margin-top: 1; }
    """
    BINDINGS = [
        Binding("f5", "refresh", "Refresh"),
        Binding("slash", "focus_filter", "Filter"),
        Binding("r", "preview_revert", "Preview revert"),
        Binding("t", "preview_restore", "Preview restore"),
        # Apply (mutating) ops are uppercase, to set them apart from the safe previews above.
        Binding("X", "apply_revert", "Revert!"),
        Binding("U", "apply_restore", "Restore!"),
        Binding("m", "show_decisions", "Decisions"),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(self, repo: str = ".") -> None:
        super().__init__()
        self.repo = repo
        self._ids: list[str] = []
        self._node_views: list[dict] = []
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
        table.add_columns("", "kind", "id", "intent", "deps")
        self._apply_responsive()
        self.action_refresh()

    # -- layout ------------------------------------------------------------
    def on_resize(self) -> None:
        self._apply_responsive()

    def _apply_responsive(self) -> None:
        narrow = (self.size.width or 80) < _NARROW
        if narrow != self._narrow:
            self._narrow = narrow
            self.query_one("#side", Static).display = not narrow
            if self._node_views:
                self._populate()  # re-truncate intent to the new budget
        else:
            self._narrow = narrow

    def _intent_budget(self) -> int:
        w = self.size.width or 80
        table_w = w if self._narrow else int(w * 0.6)
        # subtract glyph(1) + kind(~8) + id(~12) + deps(~18) + borders/gaps(~12)
        return max(12, table_w - 51)

    # -- data --------------------------------------------------------------
    def _project(self) -> Project:
        return Project.open(self.repo)

    def action_show_decisions(self) -> None:
        """Open the decision graph: decisions by feature lane with the in-force frontier marked."""
        from sgt.api import decision_graph_view

        view = decision_graph_view(self._project())
        self.push_screen(DecisionScreen(view))

    def action_refresh(self) -> None:
        proj = self._project()
        self._node_views = export_view(proj)["nodes"]
        self._last_status = status_view(proj)
        self._populate()
        self._render_status(self._last_status)

    def _populate(self) -> None:
        """(Re)fill the table from the cached nodes, honoring the active filter + width."""
        table = self.query_one("#nodes", DataTable)
        prev = self._selected_id()
        table.clear()
        self._ids = []
        budget = self._intent_budget()
        f = self._filter
        for n in self._node_views:
            if f and f not in f"{n['intent']} {n['id']} {n['kind']}".lower():
                continue
            ident = color_for(n["id"])
            dim = " dim" if n["status"] == "suspended" else ""
            intent = (n["intent"] or "")
            if len(intent) > budget:
                intent = intent[: budget - 1] + "…"
            table.add_row(
                _glyph(n["status"], n["id"]),
                Text(n["kind"][:6], style="dim"),
                Text(n["id"][:10], style=ident + dim),
                Text(intent, style=("dim" if dim else "")),
                Text(",".join(n["depends_on"])[:18], style="dim"),
                key=n["id"],
            )
            self._ids.append(n["id"])
        if prev and prev in self._ids:
            table.move_cursor(row=self._ids.index(prev))
        if not self._narrow:
            self._render_detail()

    def _render_status(self, st: dict) -> None:
        line = self.query_one("#status-line", Static)
        if "error" in st:
            line.update(Text(f"⚠ {st['error']}", style="red"))
            return
        drift = st["drift"]
        drift_txt = (
            Text(f"  ⚠ drift: {drift['summary']}", style="yellow")
            if drift["any"]
            else Text("  ✓ in sync", style="green")
        )
        shown = f"  ·  showing {len(self._ids)}/{st['nodes']}" if self._filter else ""
        msg = Text.assemble(
            (f"{st['nodes']} features", "bold"),
            f"  ·  {len(st['files'])} files  ·  {st['effects']} effects",
            shown,
            drift_txt,
        )
        line.update(msg)

    def _selected_id(self) -> str | None:
        table = self.query_one("#nodes", DataTable)
        if table.cursor_row is None or not (0 <= table.cursor_row < len(self._ids)):
            return None
        return self._ids[table.cursor_row]

    def _render_detail(self) -> None:
        side = self.query_one("#side", Static)
        nid = self._selected_id()
        if not nid:
            side.update("Select a feature.")
            return
        side.update(_detail_text(show_view(self._project(), nid)))

    def on_data_table_row_highlighted(self, _event: DataTable.RowHighlighted) -> None:
        if not self._narrow:
            self._render_detail()

    def on_data_table_row_selected(self, _event: DataTable.RowSelected) -> None:
        # On a narrow terminal the side pane is folded away; show detail on demand as a modal.
        if self._narrow:
            nid = self._selected_id()
            if nid:
                self.push_screen(DetailScreen(show_view(self._project(), nid)))

    # -- filter ------------------------------------------------------------
    def action_focus_filter(self) -> None:
        self.query_one("#filter", Input).focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "filter":
            self._filter = event.value.lower().strip()
            self._populate()
            if self._last_status:
                self._render_status(self._last_status)  # cached; no per-keystroke project reopen

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "filter":
            self.query_one("#nodes", DataTable).focus()

    # -- previews (dry-run, nothing written) -------------------------------
    def _preview(self, action: str) -> None:
        nid = self._selected_id()
        if not nid:
            return
        from sgt.orchestrate.loop import Orchestrator

        res = Orchestrator(self._project(), repo_path=self.repo).emit_payload(action, nid)
        if not res.get("ok"):
            self.notify(res.get("message") or res.get("error") or "refused", severity="warning", title="Would be refused")
            return
        files = res.get("files", {})
        detail = "; ".join(f"{f}: {len(v['before'].splitlines())}→{len(v['after'].splitlines())} ln" for f, v in files.items()) or "no file changes"
        removed = f"  lanes: {', '.join(res.get('removed', []))}" if res.get("removed") else ""
        self.notify(f"{detail}{removed}", title=f"Preview {action} (nothing written)")

    def action_preview_revert(self) -> None:
        self._preview("revert")

    def action_preview_restore(self) -> None:
        self._preview("restore")

    # -- mutations (confirm, then re-materialize + commit) -----------------
    def _apply(self, action: str) -> None:
        nid = self._selected_id()
        if not nid:
            return
        verb = {"revert": "Revert (plug out)", "restore": "Restore (plug in)"}[action]

        def done(confirmed: bool | None) -> None:
            if not confirmed:
                return
            from sgt.orchestrate.loop import Orchestrator

            orch = Orchestrator(self._project(), repo_path=self.repo)
            rep = orch.revert(nid) if action == "revert" else orch.restore(nid)
            self.notify(rep.message, severity="information" if rep.ok else "error",
                        title="✓ done" if rep.ok else "✗ refused")
            self.action_refresh()

        self.push_screen(ConfirmScreen(f"{verb} feature {nid}?\nThis rewrites the tree and commits."), done)

    def action_apply_revert(self) -> None:
        self._apply("revert")

    def action_apply_restore(self) -> None:
        self._apply("restore")


def run(repo: str = ".") -> None:
    SgtTui(repo).run()
