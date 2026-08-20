"""Reproduce the published corpus figures from this directory, and fail if they move.

A referee who opens this directory finds 30 `run.json` payloads and a paper that quotes 33
repositories, and `run.json`'s own stored rate is the harness's flattering one. `recompute.py`
fixes the rate; `settled.json` fixes the population and records the two payloads that predate
sgt's F72 path-lister fix. This script puts the three together and prints published against
recomputed, so the headline is checkable without reading the ledger.

Read-only: `git ls-files`, `git rev-list --count`, and `lstat` on the clones. No mining.

    python -u docs/eval/v3-corpus/verify.py [--corpus docs/eval/v3-corpus] [--work /tmp/v3]

Exits non-zero if any published figure is not reproduced to the precision the paper quotes it at.
"""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from recompute import recompute  # noqa: E402
from retention import retention  # noqa: E402


def spearman(a: list[float], b: list[float]) -> float:
    def rank(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        out = [0.0] * len(v)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
                j += 1
            for k in range(i, j + 1):
                out[order[k]] = (i + j) / 2 + 1
            i = j + 1
        return out

    ra, rb = rank(a), rank(b)
    n = len(a)
    ma, mb = sum(ra) / n, sum(rb) / n
    num = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    den = (sum((x - ma) ** 2 for x in ra) * sum((y - mb) ** 2 for y in rb)) ** 0.5
    return num / den


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", default="docs/eval/v3-corpus")
    ap.add_argument("--work", default="/tmp/v3")
    args = ap.parse_args()
    corpus, work = Path(args.corpus), Path(args.work)
    meta = json.loads((corpus / "settled.json").read_text())

    rows: list[tuple[str, float, int]] = []
    for d in sorted(corpus.iterdir()):
        run = d / "run.json"
        if not run.is_file() or d.name in meta["excluded"]:
            continue
        repo = work / d.name
        if not (repo / ".git").exists():
            print(f"missing clone: {repo}")
            return 2
        r = recompute(json.loads(run.read_text()), repo)
        if "error" in r:
            print(f"{d.name}: {r['error']}")
            return 2
        rate = meta["corrections"].get(d.name, {}).get("honest", r["honest_rate"])
        commits = int(subprocess.run(
            ["git", "rev-list", "--count", "HEAD"], cwd=repo,
            capture_output=True, text=True, check=True).stdout)
        rows.append((d.name, rate, commits, retention(repo)["retention"]))

    for name, row in meta["settled_rows"].items():
        rows.append((name, row["honest"], row["commits"], row["retention"]))

    pub = meta["published"]
    honest = [r[1] for r in rows]
    commits = [r[2] for r in rows]
    mature = [r for r in rows if r[2] >= pub["mature_threshold_commits"]]

    got = {
        "n": len(rows),
        "median_honest": round(statistics.median(honest), 4),
        "rho_honest_commits": round(spearman(honest, commits), 3),
        "rho_honest_commits_mature": round(
            spearman([r[1] for r in mature], [r[2] for r in mature]), 3),
    }

    bad = 0
    print(f"{'figure':32s} {'published':>10} {'recomputed':>11}")
    for k, want in ((k, pub[k]) for k in got):
        ok = got[k] == want
        bad += not ok
        print(f"{k:32s} {want:>10} {got[k]:>11}  {'' if ok else '<-- MOVED'}")
    tol = meta["published_within_tolerance"]
    ret = [r[3] for r in rows]
    approx = {
        "rho_honest_retention": round(spearman(honest, ret), 3),
        "median_retention": round(statistics.median(ret), 3),
    }
    print(f"\nwithin +/-{tol['tolerance']} (see settled.json for why these are not exact)")
    for k, got_v in approx.items():
        ok = abs(got_v - tol[k]) <= tol["tolerance"]
        bad += not ok
        print(f"{k:32s} {tol[k]:>10} {got_v:>11}  {'' if ok else '<-- OUT OF TOLERANCE'}")

    print(f"\n{len(mature)} repositories at or above "
          f"{pub['mature_threshold_commits']} commits")
    if bad:
        print(f"\n{bad} published figure(s) not reproduced.")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
