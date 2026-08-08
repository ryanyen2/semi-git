#!/usr/bin/env python3
"""Measure what each sgt read costs an agent, in bytes and tokens and milliseconds.

The `sgt-agent` skill tells agents which read to reach for based on cost. Numbers in prose go stale
silently, and a stale cost table is worse than none because it teaches confident wrong choices. This
regenerates them against a real repo so the table can be re-taken whenever the projections change.

    python -m scripts.measure_agent_cost                 # this repo
    python -m scripts.measure_agent_cost --repo PATH
    python -m scripts.measure_agent_cost --scaling        # how each read grows with history

Token counts are bytes/4, the usual rough approximation. The point is the ratios between reads and
which ones stay flat as history grows, not four significant figures.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import tempfile
import time

REPO = pathlib.Path(__file__).resolve().parent.parent

# The reads an agent actually chooses between. `sgt_show` needs a target, supplied per repo below.
READS = ("sgt_now", "sgt_log", "sgt_status", "sgt_drift", "sgt_recall", "sgt_advanced_fsck")


def _time(fn):
    start = time.perf_counter()
    payload = fn()
    return payload, (time.perf_counter() - start) * 1000


def _a_symbol(repo: str) -> str | None:
    """Any real symbol in the repo, for `sgt_show`. None when nothing is mined yet."""
    from sgt.core import opindex, order
    from sgt.core.lens import current_ideal

    ops = opindex.index_ops(pathlib.Path(repo))
    frontier = order.frontier(current_ideal(repo).op_ids, ops)
    return next((s for s in sorted(frontier)
                 if "::" in s and "__residue__" not in s and "__anchor__" not in s), None)


def measure(repo: str) -> list[tuple[str, int, float]]:
    """[(name, bytes, ms)] for each read, cheapest first."""
    from sgt.mcp.server import call_tool

    sys.path.insert(0, str(REPO))
    rows: list[tuple[str, int, float]] = []

    brief_out, brief_ms = _time(lambda: _brief_text(repo))
    rows.append(("scripts/sgt_brief", len(brief_out), brief_ms))

    symbol = _a_symbol(repo)
    if symbol:
        out, ms = _time(lambda: call_tool(repo, "sgt_show", {"sel": symbol}))
        rows.append(("sgt_show", len(json.dumps(out)), ms))

    for name in READS:
        try:
            out, ms = _time(lambda n=name: call_tool(repo, n, {}))
            rows.append((name, len(json.dumps(out)), ms))
        except Exception as exc:  # noqa: BLE001 -- a read that errors is data, not a crash
            rows.append((f"{name} (error: {type(exc).__name__})", -1, 0.0))

    rows.sort(key=lambda r: r[1])
    return rows


def _brief_text(repo: str) -> str:
    from scripts.sgt_brief import collect, render

    return render(collect(repo))


def _print(rows) -> None:
    print(f"{'read':<28}{'bytes':>9}{'~tokens':>9}{'ms':>8}")
    for name, size, ms in rows:
        if size < 0:
            print(f"{name:<28}{'—':>9}{'—':>9}{'—':>8}")
        else:
            print(f"{name:<28}{size:>9}{size // 4:>9}{ms:>8.0f}")


def scaling(sizes=(10, 30, 60)) -> None:
    """How each read grows with history. This is the part that decides the guidance: a read that is
    flat stays safe on a big repo, a read that grows linearly does not."""
    from sgt.core.lens import get
    from sgt.lens import map as lensmap
    from sgt.mcp.server import call_tool
    from sgt.store.gitbind import init_store

    print(f"{'commits':>8}  " + "  ".join(f"{n:>16}" for n in ("sgt_brief", "sgt_now", "sgt_log")))
    for n in sizes:
        d = pathlib.Path(tempfile.mkdtemp())
        gb, _ = init_store(d)
        for i in range(n):
            path = d / "api.py"
            prev = path.read_text() if path.exists() else "def base():\n    return 0\n"
            path.write_text(prev + f"\ndef fn{i}(x):\n    return x + {i}\n")
            gb.commit_all(f"step {i}")
        get(d)
        lensmap.build_map(d)
        cells = [
            len(_brief_text(str(d))),
            len(json.dumps(call_tool(str(d), "sgt_now", {}))),
            len(json.dumps(call_tool(str(d), "sgt_log", {}))),
        ]
        print(f"{n:>8}  " + "  ".join(f"{c:>7} (~{c // 4:>4}t)" for c in cells))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", default=".")
    parser.add_argument("--scaling", action="store_true",
                        help="measure growth against synthetic histories instead")
    args = parser.parse_args(argv)

    sys.path.insert(0, str(REPO))
    if args.scaling:
        scaling()
        return 0
    if not (pathlib.Path(args.repo) / ".sgt").is_dir():
        print(f"{args.repo} is not sgt-tracked; run `sgt init` there or pass --repo")
        return 2
    _print(measure(args.repo))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
