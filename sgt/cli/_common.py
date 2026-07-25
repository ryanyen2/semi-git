"""Shared CLI helpers used across the verb-family modules: the `--json` emitter and the
short failure printer. Family-specific printers live with their family."""

from __future__ import annotations


def _emit_json(payload) -> int:
    import json

    print(json.dumps(payload, indent=2))
    return 1 if isinstance(payload, dict) and "error" in payload else 0


def _fail(message: str) -> int:
    print(f"✗ {message}")
    return 1


def _fail_json(message: str, as_json: bool) -> int:
    """A failure rendered per the caller's `--json`: the `{"ok": False, "error": ...}` envelope
    or the short text printer. The shared shape repeated across the verb-family modules."""
    return _emit_json({"ok": False, "error": message}) if as_json else _fail(message)


def maybe_confirm(pview, map_view=None, grid_view=None, segments=None, *, focus_fid=None):
    """The shared consequence-pane gate for mutating verbs. On an interactive tty with `textual`
    installed, launch the pane and return the user's `Decision`; otherwise return `None` so the
    caller falls back to its own path (the ideal-edit `[y/N]` degrade, or a feature verb's
    immediate apply). Keeps the isatty + optional-import dance in one place so every verb gates
    identically. `map_view`/`grid_view`/`segments` are only used for the code rail (revert/restore);
    a metadata verb passes none and the pane renders its `summary` instead."""
    import sys

    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return None
    try:
        from sgt.tui.consequence import run_consequence
    except ImportError:
        return None
    return run_consequence(pview, map_view, grid_view, segments, focus_fid=focus_fid)


def confirm_collab(pview, question: str) -> bool:
    """The interactive confirm step for a shared-state collaboration verb (`sync`/`land`/`propose
    land`/`resolve`). On a tty with `textual` the consequence pane *is* the confirm (its `summary`
    holds the precomputed collaboration graph); otherwise it degrades to the printed feedforward
    graph (`render_collab_preview_lines`) plus a `[y/N]` prompt. Returns True to apply. Only call on
    an interactive tty -- the caller gates `isatty`/`--json` first, so the machine/CI immediate-apply
    contract never reaches here. Shared across the collab verbs so the pane/degrade dance lives once."""
    import sys

    decision = maybe_confirm(pview)
    if decision is not None:  # tty + textual: the pane is the confirm step
        return decision.apply

    from sgt.tui.graph import render_collab_preview_lines

    for line in render_collab_preview_lines(pview, color=sys.stdout.isatty()):
        print(line)
    try:
        reply = input(f"\n{question} [y/N] ").strip().lower()
    except EOFError:
        reply = ""
    return reply in ("y", "yes")


def _add_view_flags(p, *, paged: bool = False) -> None:
    """`--full` (every compact-by-default `sgt.api` view) and, for a view whose compact shape is
    itself paginated (`oplog_view`/`history_view`), `--limit`/`--offset`. Attached per-subparser
    (not the shared `parent` in `cli/__init__.py`) -- most verbs never take these."""
    p.add_argument("--full", action="store_true",
                    help="the full (uncompacted) payload instead of the compact default")
    if paged:
        p.add_argument("--limit", type=int, default=None)
        p.add_argument("--offset", type=int, default=0)
