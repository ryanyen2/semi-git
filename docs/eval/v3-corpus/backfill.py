"""Drive sgt's genesis backfill to completion in one repo, and report what it cost.

`sgt init` mines one 10-second chunk backward from HEAD (`sgt/core/lens.py:709`); each later `get()`
on an unchanged HEAD continues the walk one chunk further (`lens.py:717`). Nothing in `sgt/cli/`
drives that to completion, so a repo with more than ~10s of minable history sits partial, and every
sgt command silently advances it -- which means any coverage or reconstruction number taken before
`reached_genesis` is both wrong and irreproducible (it moves each time you look).

This is the loop the CLI does not have. Used two ways: as the required pre-measurement step in the
WP-V3 harness, and standalone to report onboarding cost per repo.

    python -u docs/eval/v3-corpus/backfill.py /tmp/v3/pudo__dataset --cap 1800
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path


def drive(repo: Path, cap_s: float, quiet: bool = False) -> dict:
    """Call `get()` until the backward walk reaches genesis or `cap_s` elapses.

    Returns the cost, and `reached` so a caller can refuse to report metrics from a partial mine.
    """
    from sgt.core.lens import get, sync_status
    from sgt.core.store import Store

    t0 = time.monotonic()
    chunks, history = 0, []
    while True:
        st = sync_status(repo)
        if st.get("reached_genesis"):
            break
        if time.monotonic() - t0 > cap_s:
            break
        get(repo)
        chunks += 1
        ops = len(Store(repo).all_ops())
        elapsed = round(time.monotonic() - t0, 1)
        history.append({"chunk": chunks, "seconds": elapsed, "ops": ops})
        if not quiet:
            print(f"  chunk {chunks:3d}  {elapsed:7.1f}s  {ops:6d} ops", flush=True)

    final = sync_status(repo)
    return {
        "reached_genesis": bool(final.get("reached_genesis")),
        "complete": bool(final.get("complete")),
        "chunks": chunks,
        "seconds": round(time.monotonic() - t0, 1),
        "capped": not final.get("reached_genesis"),
        "cap_s": cap_s,
        "progress": history,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("repo")
    ap.add_argument("--cap", type=float, default=1800.0, help="wall-clock cap in seconds")
    args = ap.parse_args()

    repo = Path(args.repo)
    res = drive(repo, args.cap)
    print(json.dumps({k: v for k, v in res.items() if k != "progress"}, indent=2))
    if res["capped"]:
        print(f"CAPPED at {args.cap}s without reaching genesis -- metrics from this repo are partial")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
