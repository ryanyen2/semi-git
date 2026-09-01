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
    # "Not reversible" tells the user this is one-way but not what their next move can be. When the
    # projection knows what `sgt undo` will do afterward (land), say it here, right above the
    # prompt where the decision is actually made.
    note = pview.get("undo_note") if isinstance(pview, dict) else None
    if note:
        print(f"\n  {note}")
    try:
        reply = input(f"\n{question} [y/N] ").strip().lower()
    except EOFError:
        reply = ""
    return reply in ("y", "yes")


def confirm_summary(pview, question: str) -> bool:
    """The confirm step for a verb whose consequence is already a short human `summary` -- the
    metadata feature verbs (merge/rename/move/split). Returns True to apply.

    Preview symmetry: `confirm_collab` degrades to a printed feedforward graph plus `[y/N]` when
    `textual` isn't installed, and the ideal edits degrade to their own `[y/N]`. The feature verbs
    had no degrade at all -- `maybe_confirm` returns `None` without `textual`, which the caller read
    as "proceed", so on any machine without that *optional* dependency `sgt feature regroup merge A B`
    re-cut two features with nothing shown and nothing asked. The summary is the same text the pane
    would have shown, so the degraded path answers the same question the pane does.

    Only call on an interactive tty: the caller gates `isatty`/`--json` first, keeping the
    machine/CI immediate-apply contract byte-for-byte."""
    decision = maybe_confirm(pview)
    if decision is not None:  # tty + textual: the pane is the confirm step
        return decision.apply

    verb = pview.get("verb", "this")
    print(f"\n  {verb}:")
    for line in pview.get("summary") or []:
        print(f"    {line}")
    if pview.get("reversible"):
        # Worth saying: it changes how carefully the user needs to read the rest.
        print("    (metadata only — no code changes, reversible)")
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


import contextlib


@contextlib.contextmanager
def busy(text: str, *, delay: float = 0.4):
    """A single dim spinner line on stderr while a slow verb works, erased when it finishes.

    The verbs that mine or recluster (`save`, `revert`, `restore`, `log --refresh`) can sit
    silent for several seconds, and a silent prompt reads as a hang -- the study's pilots
    re-typed the command or killed it. Shows nothing at all for fast calls (`delay` passes
    first), for non-ttys, and for `--json` pipelines (stderr piped): the machine contract and
    the scrollback stay byte-identical."""
    import sys
    import threading

    if not sys.stderr.isatty():
        yield
        return
    stop = threading.Event()
    drew = threading.Event()

    def run():
        if stop.wait(delay):
            return
        frames = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
        i = 0
        while not stop.is_set():
            drew.set()
            sys.stderr.write(f"\r\x1b[2m{frames[i % len(frames)]} {text}\x1b[0m\x1b[K")
            sys.stderr.flush()
            i += 1
            if stop.wait(0.1):
                break

    t = threading.Thread(target=run, daemon=True)
    t.start()
    try:
        yield
    finally:
        stop.set()
        t.join(timeout=1)
        if drew.is_set():
            sys.stderr.write("\r\x1b[K")
            sys.stderr.flush()
