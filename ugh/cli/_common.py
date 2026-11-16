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


def confirm(lines: list[str], question: str) -> bool:
    """Print a preview and ask `[y/N]`. Only call on an interactive tty: the caller gates
    `isatty`/`--json` first, so the machine contract applies immediately."""
    for line in lines:
        print(line)
    try:
        reply = input(f"\n{question} [y/N] ").strip().lower()
    except EOFError:
        reply = ""
    return reply in ("y", "yes")


def fork_lines(forks: list[dict]) -> list[str]:
    """Open forks, one line each with the command that closes it. A fork whose tips were mined
    under two miner versions is the same commit stored twice, which `ugh resolve` cannot close."""
    from ugh.api import plural

    real = [f for f in forks if not f.get("cross_version")]
    stale = [f for f in forks if f.get("cross_version")]
    lines = []
    if real:
        lines.append(f"⚠ {plural(len(real), 'open fork')} — divergent edits to one symbol:")
        lines += [f"    {f.get('symbol', '?')}  →  ugh resolve {f.get('symbol', '?')}" for f in real]
    if stale:
        lines.append(f"⚠ {plural(len(stale), 'fork')} are two mining generations of the same "
                     "commit — `ugh check` shows the miner versions present")
    return lines


def _add_view_flags(p, *, paged: bool = False) -> None:
    """`--full` (every compact-by-default `ugh.api` view) and, for a view whose compact shape is
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
