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


def _add_view_flags(p, *, paged: bool = False) -> None:
    """`--full` (every compact-by-default `sgt.api` view) and, for a view whose compact shape is
    itself paginated (`oplog_view`/`history_view`), `--limit`/`--offset`. Attached per-subparser
    (not the shared `parent` in `cli/__init__.py`) -- most verbs never take these."""
    p.add_argument("--full", action="store_true",
                    help="the full (uncompacted) payload instead of the compact default")
    if paged:
        p.add_argument("--limit", type=int, default=None)
        p.add_argument("--offset", type=int, default=0)
