#!/usr/bin/env python3
"""WP-V1 validity check: do the stored plan records claim work they did not do?

    python docs/eval/v1-census/plan_records.py <repo> --out <dir>

`census.py` compares episodes to features and never opens `.sgt/local/plan_sessions.json`,
so three of the build log's four admitted blemishes (E7/E9/E17 over-claiming ops, E16's two
never-matched steps) were invisible to it. The build log says a census that finds fewer
problems than the log already admits is broken, so this is the missing half.

Metric, declared before running (R2). For each plan step:

  granularity   symbol  -- every predicted footprint entry names `file::Symbol`
                file    -- at least one entry is a bare path, which matches any symbol in it
  over_claimed  matched ops none of whose edited symbols fall inside the predicted
                footprint. A file-level entry counts as covering every symbol in that file,
                so this is the *generous* reading: an op is only over-claimed if it touched
                nothing the step said it would touch.
  never_matched a step left `pending` -- the plan predicted work that no op ever fulfilled.

Anchor and residue records are ignored, the same as in `census.py`: they are bookkeeping,
and counting them makes every step look like an over-claim.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def edited_symbols(repo: Path, op_id: str) -> list[str]:
    path = repo / ".sgt" / "ops" / op_id
    if not path.exists():
        return []
    return [s for s in json.load(open(path)).get("footprint", {})
            if "__anchor__" not in s and "__residue__" not in s]


def covers(footprint: list[str], symbol: str) -> bool:
    """Is `symbol` inside the predicted footprint? A bare path covers its whole file."""
    file_part = symbol.split("::", 1)[0]
    for entry in footprint:
        if entry == symbol or entry == file_part:
            return True
        if "::" not in entry and file_part.endswith(entry):  # directory-less prediction
            return True
    return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("repo", type=Path)
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()

    repo = args.repo.expanduser().resolve()
    sessions = json.loads((repo / ".sgt/local/plan_sessions.json").read_text())["data"]

    rows = []
    for sid, sess in sorted(sessions.items(), key=lambda kv: kv[1].get("created_ts", 0)):
        for step in sess["steps"]:
            fp = step["predicted_footprint"]
            matched = step["matched_op_ids"]
            outside = 0
            for op in matched:
                syms = edited_symbols(repo, op)
                if syms and not any(covers(fp, s) for s in syms):
                    outside += 1
            rows.append({
                "session": sid[:8],
                "plan": sess["plan_text"][:60],
                "title": step["title"],
                "status": step["status"],
                "granularity": "symbol" if all("::" in e for e in fp) else "file",
                "predicted_footprint": fp,
                "n_matched": len(matched),
                "n_outside_footprint": outside,
            })

    flags = []
    for r in rows:
        if r["status"] != "matched":
            flags.append({"flag": "never-matched", "step": r["title"],
                          "detail": f"predicted {r['predicted_footprint']}, matched nothing"})
        elif r["n_outside_footprint"]:
            flags.append({"flag": "over-claim", "step": r["title"],
                          "detail": f"{r['n_outside_footprint']} of {r['n_matched']} matched ops "
                                    f"touched nothing in {r['predicted_footprint']}"})
    file_level = [r for r in rows if r["granularity"] == "file"]
    if file_level:
        flags.append({"flag": "file-level-footprint",
                      "detail": f"{len(file_level)} of {len(rows)} steps predicted a bare path, so "
                                f"any edit to that file fulfils them"})

    report = {"repo": str(repo), "n_sessions": len(sessions), "n_steps": len(rows),
              "rows": rows, "flags": flags}
    if args.out:
        out = args.out.expanduser().resolve()
        out.mkdir(parents=True, exist_ok=True)
        (out / "plan-records.json").write_text(json.dumps(report, indent=1))

    print(f"{repo.name}: {len(sessions)} plan session(s), {len(rows)} step(s)")
    print(f"{'sess':<9}{'gran':<8}{'status':<9}{'match':>6}{'outside':>8}  step")
    for r in rows:
        print(f"{r['session']:<9}{r['granularity']:<8}{r['status']:<9}{r['n_matched']:>6}"
              f"{r['n_outside_footprint']:>8}  {r['title']}")
    print()
    for f in flags:
        print(f"[{f['flag']}] {f.get('step', '')} — {f['detail']}")
    print(f"\n{len(flags)} flags.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
