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

import difflib

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import DataTable, Footer, Header, Input, Label, Static

from sgt.tui.color import color_for

_KIND_GLYPH = {"feature": "●", "subsystem": "▸", "symbol": "◦"}
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


# -- pure logic (no textual runtime; unit-tested directly) ---------------------


def _subseq(needle: str, hay: str) -> bool:
    """True when `needle`'s characters appear in order in `hay` (the classic fuzzy-finder match)."""
    it = iter(hay)
    return all(ch in it for ch in needle)


def fuzzy_rank(rows: list[dict], query: str) -> list[dict]:
    """Rank/filter display rows by a fuzzy match over ``label + id`` (plan U9/R8). An empty query
    returns every row unchanged; otherwise a row survives only when the query is a subsequence of
    ``f"{label} {id}"``, and survivors are ordered by a `difflib` similarity ratio (best first,
    original order breaking ties). A query that matches nothing returns ``[]`` -- the caller's
    empty-state, never a crash. Stdlib `difflib` only, mirroring U1's resolver (no new dependency)."""
    q = query.strip().lower()
    if not q:
        return list(rows)
    scored: list[tuple[float, int, dict]] = []
    for i, r in enumerate(rows):
        hay = f"{r['label']} {r['id']}".lower()
        if not _subseq(q, hay):
            continue
        score = difflib.SequenceMatcher(None, q, hay).ratio()
        if q in hay:  # a contiguous hit outranks a scattered subsequence
            score += 1.0
        scored.append((score, i, r))
    scored.sort(key=lambda t: (-t[0], t[1]))
    return [r for _, _, r in scored]


def frontier_counts(rows: list[dict], kept: set[str]) -> dict:
    """Classify a verb preview's ``frontier`` rows (``{op_id, bucket, toggleable}``) against the
    user's kept-set (plan U9/U3, R4). ``blast``/``carry`` rows are toggleable -- keeping one
    retains that op, so the revert removes the rest; ``foundation`` rows are read-only
    prerequisites, never removed and never valid in the kept-set. Returns the live removed/kept
    dependent counts plus which op-ids are toggleable/foundation, for the checklist header. A
    kept-id that is not a toggleable row (a stale or foundation id) is ignored."""
    toggleable = sorted(r["op_id"] for r in rows if r.get("toggleable"))
    foundation = sorted(r["op_id"] for r in rows if not r.get("toggleable"))
    kept_valid = set(kept) & set(toggleable)
    return {
        "toggleable": toggleable,
        "foundation": foundation,
        "kept": len(kept_valid),
        "removed": len(toggleable) - len(kept_valid),
    }


def selection_specs(node_ids) -> list[str]:
    """Map a multi-select set of table node-ids to the universal resolver's specs (plan U1). A
    symbol row's id is already a ``file::symbol`` (the resolver's exact-symbol form); a feature
    row's id is a clustering/authored feature ref the resolver resolves directly -- so the mapping
    is one spec per selection, in a stable order, and the caller resolves each independently and
    reports which succeeded/refused (the partial state)."""
    return sorted({nid for nid in node_ids if nid})


def _glyph(kind: str, nid: str) -> Text:
    return Text(_KIND_GLYPH.get(kind, "●"), style=color_for(nid))


def _detail_text(n: dict) -> Text:
    """Render a node's detail view (shared by the side pane and the narrow-mode modal)."""
    ident = color_for(n["id"])
    if n.get("kind") == "symbol":
        t = Text()
        t.append(f"{n['label']}\n\n", style=f"bold {ident}")
        t.append(f"symbol · {n['id']}\n", style="dim")
        t.append(f"member of {n.get('parent_feature', '—')}\n", style="dim")
        return t
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


class FrontierScreen(ModalScreen[None]):
    """The revert frontier as a checkable list (plan U9/U3, R8/R4). Each ``blast``/``carry``
    dependent is a row ``space`` toggles into the kept-set (keeping it retains that op, so the
    revert removes the rest); ``foundation`` prerequisites render read-only (a revert cannot drop
    an upstream prerequisite). The removed/kept counts recompute live off ``frontier_counts`` --
    the same ``verb_preview_view`` frontier the CLI reads. The header shows the combined selection
    closure (``resolve_selection``) and, for a multi-select, which targets refused: an all-refused
    selection is the **refused** state (its message mirrors ``action_preview_revert``'s notify), a
    mix is the **partial** state."""

    BINDINGS = [Binding("space", "toggle", "Keep/drop"), Binding("escape,q,f", "close", "Close")]

    def __init__(self, rows: list[dict], *, ok_labels: list[str], refused: list, closure_count: int) -> None:
        super().__init__()
        self._rows = rows
        self._ok_labels = ok_labels
        self._refused = refused
        self._closure_count = closure_count
        self._kept: set[str] = set()

    def compose(self) -> ComposeResult:
        with Vertical(id="frontier-modal"):
            yield Static(self._header(), id="frontier-header")
            if self._ok_labels:
                yield DataTable(id="frontier-table", cursor_type="row")
                yield Static("", id="frontier-counts")
            if self._refused:
                yield Static(self._refused_text(), id="frontier-refused")
            yield Label("[b]space[/b] keep/drop   ·   [b]esc[/b] close", id="hint")

    def on_mount(self) -> None:
        if self._ok_labels:
            table = self.query_one("#frontier-table", DataTable)
            table.add_columns("", "op", "bucket")
            self._fill()
            table.focus()

    def _header(self) -> Text:
        t = Text()
        if self._ok_labels:
            t.append(f"{len(self._ok_labels)} selection(s) → {self._closure_count} op(s) in closure\n",
                     style="bold")
            t.append("  ".join(self._ok_labels) + "\n", style="dim")
        if self._refused:
            t.append(f"⚠ {len(self._refused)} refused", style="yellow")
        return t

    def _refused_text(self) -> Text:
        t = Text()
        t.append(("refused:\n" if not self._ok_labels else "partially refused:\n"), style="bold yellow")
        for spec, msg in self._refused:
            t.append(f"  ✗ {spec}: {msg or 'refused'}\n", style="yellow")
        return t

    def _fill(self) -> None:
        table = self.query_one("#frontier-table", DataTable)
        table.clear()
        for r in self._rows:
            oid, bucket = r["op_id"], r["bucket"]
            if not r.get("toggleable"):
                marker = Text("·", style="dim")  # foundation: read-only prerequisite
            elif oid in self._kept:
                marker = Text("✓", style=color_for(oid))  # kept (retained by the revert)
            else:
                marker = Text(" ")
            table.add_row(marker, Text(oid[:12], style="dim"), Text(bucket, style="dim"), key=oid)
        self._render_counts()

    def _render_counts(self) -> None:
        line = self.query_one("#frontier-counts", Static)
        if not self._rows:
            line.update(Text("no per-op dependents to toggle (feature-level revert)", style="dim"))
            return
        counts = frontier_counts(self._rows, self._kept)
        line.update(Text.assemble(
            (f"removes {counts['removed']} dependent(s)", "bold"),
            f"  ·  keeps {counts['kept']}  ·  {len(counts['foundation'])} foundation (read-only)",
        ))

    def action_toggle(self) -> None:
        table = self.query_one("#frontier-table", DataTable)
        row = table.cursor_row
        if row is None or not (0 <= row < len(self._rows)):
            return
        r = self._rows[row]
        if not r.get("toggleable"):
            return  # foundation is read-only, never toggled
        self._kept.symmetric_difference_update({r["op_id"]})
        self._fill()
        table.move_cursor(row=row)

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
    DetailScreen, FrontierScreen { align: center middle; }
    #detail-modal { width: 80%; height: 80%; padding: 1 2; border: thick $accent; background: $surface; }
    #frontier-modal { width: 80%; height: 80%; padding: 1 2; border: thick $accent; background: $surface; }
    #frontier-header { height: auto; }
    #frontier-table { height: 1fr; border: round $panel; }
    #frontier-counts { height: 1; color: $text-muted; }
    #frontier-refused { height: auto; margin-top: 1; }
    #hint { color: $text-muted; margin-top: 1; }
    """
    BINDINGS = [
        Binding("f5", "refresh", "Refresh"),
        Binding("slash", "focus_filter", "Filter"),
        Binding("space", "toggle_select", "Select"),
        Binding("e", "expand", "Expand"),
        Binding("f", "frontier", "Frontier"),
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
        self._shown_rows: list[dict] = []  # the rows currently rendered, aligned to `_ids`
        self._selected: set[str] = set()  # multi-select set (feature + symbol ids)
        self._expanded: set[str] = set()  # feature ids expanded into their member symbols
        self._n_display = 0  # unfiltered display-row count (for the "showing X/Y" indicator)
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
        table.add_columns("", "", "feature", "size", "ops")  # select-marker, kind-glyph, ...
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

    def _display_rows(self) -> list[dict]:
        """The base display list: every feature/subsystem row, plus -- for each expanded feature --
        its member entity symbols as deeper, selectable, fuzzy-matchable rows (R8's "and symbols").
        A symbol row carries the member's ``file::symbol`` id (the universal resolver's exact-symbol
        form), so a selected symbol resolves without a lookup table. Only ``entity`` members are
        surfaced -- whole-file and residue/anchor pseudo-symbols are not user-selectable (the same
        liveness rule the resolver's `_live_tips` applies)."""
        from sgt.lens.select import _symbol_kind

        out: list[dict] = []
        for n in self._rows:
            out.append(n)
            if n["kind"] != "feature" or n["id"] not in self._expanded:
                continue
            for sym in n.get("members", []):
                if _symbol_kind(sym) != "entity":
                    continue
                out.append({
                    "id": sym,
                    "label": sym.split("::")[-1] or sym,
                    "kind": "symbol",
                    "depth": n["depth"] + 1,
                    "size": 0,
                    "op_count": 0,
                    "parent_feature": n["id"],
                })
        return out

    def _populate(self) -> None:
        """(Re)fill the table from the display rows, fuzzy-ranked by the active filter and marked
        with the multi-select set."""
        table = self.query_one("#nodes", DataTable)
        prev = self._selected_id()
        table.clear()
        self._ids = []
        self._shown_rows = []
        all_rows = self._display_rows()
        self._n_display = len(all_rows)
        for n in fuzzy_rank(all_rows, self._filter):
            nid = n["id"]
            indent = "  " * n["depth"]
            ident = color_for(nid)
            marker = Text("✓", style=ident) if nid in self._selected else Text(" ")
            label_style = "dim" if n["kind"] == "subsystem" else ident
            is_sym = n["kind"] == "symbol"
            table.add_row(
                marker,
                _glyph(n["kind"], nid),
                Text(f"{indent}{n['label']}", style=label_style),
                Text("" if is_sym else str(n["size"]), style="dim"),
                Text("" if is_sym else str(n["op_count"]), style="dim"),
                key=nid,
            )
            self._ids.append(nid)
            self._shown_rows.append(n)
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
        shown = f"  ·  showing {len(self._ids)}/{self._n_display}" if self._filter else ""
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
        table = self.query_one("#nodes", DataTable)
        row = table.cursor_row
        if row is None or not (0 <= row < len(self._shown_rows)):
            return None
        return self._shown_rows[row]

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

    # -- multi-select + expand ----------------------------------------------
    def action_toggle_select(self) -> None:
        """Toggle the highlighted row into/out of the multi-select set (the ✓ marker column)."""
        nid = self._selected_id()
        if nid is None:
            return
        self._selected.symmetric_difference_update({nid})
        self._populate()

    def action_expand(self) -> None:
        """Expand/collapse the highlighted feature into its member entity symbols (deeper rows)."""
        row = self._current_row()
        if row is None or row["kind"] != "feature":
            return
        self._expanded.symmetric_difference_update({row["id"]})
        self._populate()

    # -- frontier panel -----------------------------------------------------
    def action_frontier(self) -> None:
        """Open the revert frontier as a checkable list for the multi-select set (or, if none, the
        highlighted row). Each selection is resolved through the U1 resolver for the combined
        closure + refused summary, and each symbol target's per-dependent ``frontier`` is pulled
        from the same ``verb_preview_view`` projection the CLI reads."""
        cur = self._selected_id()
        ids = sorted(self._selected) if self._selected else ([cur] if cur else [])
        if not ids:
            self.notify("select (space) or highlight a row first", severity="warning", title="Frontier")
            return
        from sgt.api import resolve_selection
        from sgt.core.lens import get

        get(self.repo)
        resolved = [(s, resolve_selection(self.repo, s)) for s in selection_specs(ids)]
        ok = [(s, r) for s, r in resolved if r["ok"]]
        refused = [(s, r["message"]) for s, r in resolved if not r["ok"]]
        rows: list[dict] = []
        seen: set[str] = set()
        for spec, _ in ok:
            for fr in self._preview_for_id(spec).get("frontier", []):
                if fr["op_id"] not in seen:
                    seen.add(fr["op_id"])
                    rows.append(fr)
        closure: set[str] = set()
        for _, r in ok:
            closure.update(r["closure"])
        self.push_screen(FrontierScreen(
            rows, ok_labels=[r["label"] for _, r in ok], refused=refused, closure_count=len(closure)))

    def _preview_for_id(self, node_id: str) -> dict:
        """The revert preview for one selection id, via the same projection the CLI reads: a symbol
        (``file::symbol``) reverts at op granularity (``verb_preview_view`` -- a real per-dependent
        ``frontier``); a feature id reverts its whole op-set (``plan_revert_feature``), whose
        frontier is aggregate, so the per-op checklist is empty (the header still shows the closure)."""
        if "::" in node_id:
            from sgt.api import verb_preview_view

            return verb_preview_view(self.repo, "revert", node_id)
        from sgt.lens.verbs import plan_revert_feature

        p = plan_revert_feature(self.repo, node_id)
        return {"ok": p.ok, "message": p.message, "removed": sorted(p.removed), "frontier": []}

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
