#!/usr/bin/env python3
"""Drive the pre-registered WP-V4 sweep: many short sequences, one system version, one pooled table.

    python docs/eval/v4-robustness/sweep.py --out /tmp/v4-final --jobs 4 --target 10000
    python docs/eval/v4-robustness/sweep.py --out /tmp/v4-final --dry-run

Why a driver instead of a shell loop. Three things went wrong the last time this sweep was run by hand, and
each is a guard here rather than a habit:

1. **The runs spanned five edits to `sgt/`.** Their numbers were void by construction and only the artifact
   mtimes said so. So this driver samples the system version before every run and *aborts the whole sweep*
   the moment it changes. Pooling honestly is not a property you can add at aggregation time; either every
   artifact tested the same thing or none of them count.
2. **Two runs shared a `--work` path**, and the second deleted the first's repo mid-run, which the first
   correctly reported as lost data. Every run here gets its own work directory named after its own label.
3. **One long sequence per shape.** Throughput degrades as the repo grows and a single 2,500-op run took
   sixteen hours; worse, a hard stop at op 199 ends the sequence and buys no further coverage. Many short
   sequences (20-50 ops, the pre-registered design) are faster per op, independent, and a stop costs one
   sequence instead of the sweep.

Op counts and the shape order are drawn from `--plan-seed`, so the plan itself is reproducible: the same
plan seed enumerates the same (shape, seed, ops) triples in the same order.
"""

from __future__ import annotations

import argparse
import json
import random
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT))

import harness  # noqa: E402  -- for system_version() and the corpus shape list


def shapes() -> list[str]:
    from tests.laws import corpus
    return sorted(n[6:] for n in dir(corpus) if n.startswith("_case_"))


def real_repos(pool: Path, n: int, plan_seed: int) -> list[Path]:
    """`n` real clones sampled from the V3 corpus with the recorded plan seed.

    The pre-registered design says the sweep runs on real repositories as well as fixtures, and the version
    of it that produced the published table used fixtures only. That omission is why this is a parameter with
    a default rather than something to remember: the corpus shapes are hand-built to be interesting, so a
    violation rate measured only on them says nothing about a repository somebody actually wrote.
    """
    if not pool.is_dir():
        return []
    cands = sorted(d for d in pool.iterdir() if (d / ".git").is_dir())
    return random.Random(plan_seed + 1).sample(cands, min(n, len(cands)))


def plan(target: int, plan_seed: int, lo: int, hi: int) -> list[tuple[str, int, int]]:
    """(shape, run seed, ops) triples until the op budget is met.

    Every shape appears in each round before any shape appears twice, so a sweep that is cut short is still
    balanced across shapes rather than being deep on whichever shape sorted first.
    """
    rng = random.Random(plan_seed)
    names, out, total, rnd = shapes(), [], 0, 0
    while total < target:
        order = names[:]
        rng.shuffle(order)
        for name in order:
            ops = rng.randint(lo, hi)
            out.append((name, 1000 + len(out), ops))
            total += ops
            if total >= target:
                break
        rnd += 1
        if rnd > 10000:                      # cannot happen with lo >= 1; a guard, not a policy
            break
    return out


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--work", type=Path, default=Path("/tmp/v4-sweep-work"))
    ap.add_argument("--target", type=int, default=10000, help="total operations to request")
    ap.add_argument("--min-ops", type=int, default=20)
    ap.add_argument("--max-ops", type=int, default=50)
    ap.add_argument("--plan-seed", type=int, default=20260817)
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--repo-pool", type=Path, default=Path("/tmp/v3"),
                    help="directory of real clones to sample from (the V3 corpus)")
    ap.add_argument("--repos", type=int, default=5, help="how many real clones to include; 0 for fixtures only")
    ap.add_argument("--repo-ops", type=int, default=50)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv[1:])

    triples = plan(args.target, args.plan_seed, args.min_ops, args.max_ops)
    reals = real_repos(args.repo_pool, args.repos, args.plan_seed) if args.repos else []
    # Real clones run *first*, for two reasons found by smoke-testing this path. They are the
    # highest-yield arm -- five operations on one real repository produced three findings that 600
    # operations on fixtures did not -- so they must not be the sequences a cut-short sweep drops. And
    # they are ~20x slower per operation, so appending them left them running alone at the end on one
    # core each; started first they overlap with the fixtures and cost almost nothing in wall clock.
    # This changes execution order only: the same shapes, seeds and op counts as the pre-registered plan.
    triples = [(str(r), 2000 + i, args.repo_ops) for i, r in enumerate(reals)] + triples
    requested = sum(t[2] for t in triples)
    print(f"plan seed {args.plan_seed}: {len(triples)} sequences, {requested} operations requested, "
          f"{len(shapes())} shapes, {args.min_ops}-{args.max_ops} ops each")
    if reals:
        print(f"  plus {len(reals)} real clones from {args.repo_pool} at {args.repo_ops} ops each: "
              f"{', '.join(r.name for r in reals)}")
    elif args.repos:
        print(f"  WARNING: --repos {args.repos} requested but {args.repo_pool} holds no clones; this sweep "
              f"is fixtures only, which is the limitation the published table already had")
    if args.dry_run:
        for name, seed, ops in triples[:12]:
            print(f"  {name:26} seed={seed} ops={ops}")
        print(f"  ... ({len(triples) - 12} more)" if len(triples) > 12 else "")
        return 0

    args.out.mkdir(parents=True, exist_ok=True)
    baseline = harness.system_version()
    (args.out / "sweep-plan.json").write_text(json.dumps(
        {"plan_seed": args.plan_seed, "target": args.target, "min_ops": args.min_ops,
         "max_ops": args.max_ops, "jobs": args.jobs, "requested": requested,
         "system_at_plan": baseline,
         "repo_pool": str(args.repo_pool), "repos": [str(r) for r in reals],
         "sequences": [{"case": c, "seed": s, "ops": o} for c, s, o in triples]}, indent=1))
    print(f"baseline system: {json.dumps(baseline)}")

    running: list[tuple[subprocess.Popen, str, Path]] = []
    done = failed = 0

    def reap(block: bool) -> bool:
        """Returns False if the sweep must abort."""
        nonlocal done, failed
        for entry in list(running):
            proc, label, log = entry
            if proc.poll() is None:
                if not block:
                    continue
                proc.wait()
            running.remove(entry)
            done += 1
            if proc.returncode != 0:
                failed += 1
                print(f"  [{done}/{len(triples)}] {label}: EXIT {proc.returncode} (see {log})")
            else:
                print(f"  [{done}/{len(triples)}] {label}: ok")
        return True

    for i, (case, seed, ops) in enumerate(triples):
        while len(running) >= args.jobs:
            reap(block=True)
        now = harness.system_version()
        if now != baseline:
            print("\nABORTING the sweep: the system changed while it was running.")
            print(f"  at plan time: {json.dumps(baseline)}")
            print(f"  now         : {json.dumps(now)}")
            print("Artifacts written so far tested the earlier version. Finish or discard them as a group; "
                  "do not pool them with a restart. (This is the defect that voided the previous sweep.)")
            for proc, label, _ in running:
                proc.terminate()
            return 3
        source = ["--repo", case] if case.startswith("/") else ["--case", case]
        label = f"{Path(case).name if case.startswith('/') else case}-s{seed}"
        log = args.out / f"log-{label}.txt"
        cmd = [sys.executable, "-u", str(HERE / "harness.py"), *source, "--seed", str(seed),
               "--ops", str(ops), "--work", str(args.work / label), "--out", str(args.out)]
        proc = subprocess.Popen(cmd, cwd=ROOT, stdout=log.open("w"), stderr=subprocess.STDOUT)
        running.append((proc, label, log))
        print(f"  [{i + 1}/{len(triples)}] started {label} ({ops} ops)")

    while running:
        reap(block=True)
    print(f"\n{done} sequences finished, {failed} exited non-zero.")
    print(f"Now pool: python {HERE / 'aggregate.py'} {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
