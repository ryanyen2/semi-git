"""Acceptance of the kernel's take-out/bring-back planner on the two study projects.

For each project: unpack its history, `ugh init`, configure `python3 check.py` as the check, then
for each of the 12 requests in a fresh copy take the save out (`ugh revert <sha> --yes`), run the
check, bring it back (`ugh restore <sha> --yes`) and compare the tree with the original byte for
byte. Also the three event requests together, and the newest request against the tree before it.

    uv run python scripts/research/removal-sets/kernel_acceptance.py
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tarfile
import tempfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
EVENTS = ("event-days", "event-annotation", "event-exclusion")


def run(repo: Path, *argv: str) -> subprocess.CompletedProcess:
    return subprocess.run(argv, cwd=repo, capture_output=True, text=True)


def ugh(repo: Path, *argv: str) -> dict:
    proc = run(repo, sys.executable, "-m", "ugh.cli", *argv, "--json", "--yes")
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"ok": False, "message": (proc.stdout + proc.stderr).strip()[-300:]}


def tree(repo: Path) -> dict[str, bytes]:
    paths = run(repo, "git", "ls-files", "-z").stdout.split("\0")
    return {p: (repo / p).read_bytes() for p in paths
            if p and not p.startswith(".sgt/") and (repo / p).is_file()}


def checks(repo: Path) -> bool:
    return run(repo, sys.executable, "check.py").returncode == 0


def reasons(view: dict) -> str:
    held = Counter(h["reason"] for h in view.get("held", ()))
    return ", ".join(f"{n} {r}" for r, n in sorted(held.items())) or "-"


def take_out(repo: Path, op_ids, target: str) -> dict:
    """Plan and apply a take-out of an op set through the planner (the CLI takes one reference)."""
    from ugh.core import planner
    from ugh.core.lens import get

    get(repo)
    done = planner.apply(repo, planner.plan(repo, op_ids, target=target))
    return planner.to_json(done, applied=True)


def bring_back(repo: Path, op_ids, target: str) -> dict:
    from ugh.core import planner
    from ugh.core.lens import get

    get(repo)
    done = planner.apply(repo, planner.plan(repo, op_ids, verb="restore", target=target))
    return planner.to_json(done, applied=True)


def project(name: str, episodes: list[dict], scratch: Path) -> list[str]:
    from ugh.select.resolve import _save

    with tarfile.open(HERE / f"exp-{name}.tgz") as tar:
        members = [m for m in tar if not Path(m.name).name.startswith("._")]  # macOS resource forks
        tar.extractall(scratch / "src", members=members, filter="data")
    template = scratch / "src" / f"exp-{name}"
    shutil.rmtree(template / ".sgt" / "local", ignore_errors=True)
    assert run(template, sys.executable, "-m", "ugh.cli", "init").returncode == 0
    from ugh.core.lens import get, sync_status

    while not sync_status(template)["complete"]:
        get(template, unbounded=True)
    (template / ".sgt" / "oracle.json").write_text(json.dumps(
        {"tiers": [{"name": "check", "command": "python3 check.py"}]}))

    rows = [f"### {name}", "",
            "| request | save | check after out | steps went | held | back = original |",
            "|---|---|---|---|---|---|"]
    requests = [e for e in episodes if e["session"]]
    for i, ep in enumerate(requests):
        repo = scratch / f"{name}-{i}"
        shutil.copytree(template, repo, symlinks=True)
        original = tree(repo)
        out = ugh(repo, "revert", ep["sha"])
        passed = "pass" if checks(repo) else "FAIL"
        back = ugh(repo, "restore", ep["sha"]) if out.get("undone") else {}
        same = "yes" if tree(repo) == original else "NO"
        rows.append(f"| {ep['session']} | {ep['sha']} | {passed} | {len(out.get('undone', ()))} "
                    f"| {len(out.get('held', ()))} ({reasons(out)}) | {same} |"
                    + (f" {out.get('message')}" if not out.get("ok", True) else "")
                    + (f" restore: {back.get('message')}" if back and not back.get("ok", True) else ""))

    repo = scratch / f"{name}-events"
    shutil.copytree(template, repo, symlinks=True)
    original = tree(repo)
    events = [e for e in requests if e["session"] in EVENTS]
    op_ids = frozenset().union(*(_save(repo, e["sha"]).op_ids for e in events))
    from ugh.core import planner

    p = planner.plan(repo, op_ids, target="events", removal_set=True)
    shas = sorted(s["sha"] for s in p.removal_set["saves"])
    out = take_out(repo, op_ids, "events")
    passed = "pass" if checks(repo) else "FAIL"
    bring_back(repo, op_ids, "events")
    same = "yes" if tree(repo) == original else "NO"
    rows.append(f"| the three event requests | {', '.join(e['sha'] for e in events)} | {passed} "
                f"| {len(out['undone'])} | {len(out['held'])} ({reasons(out)}) | {same} |")
    rows.append(f"|  removal set | {', '.join(shas)} | | {p.removal_set['steps']} | "
                f"{'exactly the three' if shas == sorted(e['sha'] for e in events) else 'DIFFERS'} | |")

    newest = requests[-1]
    repo = scratch / f"{name}-newest"
    shutil.copytree(template, repo, symlinks=True)
    before = run(repo, "git", "rev-parse", f"{newest['sha']}^").stdout.strip()
    out = ugh(repo, "revert", newest["sha"])
    wanted = {p: subprocess.run(["git", "show", f"{before}:{p}"], cwd=repo, capture_output=True).stdout
              for p in run(repo, "git", "ls-tree", "-r", "-z", "--name-only", before).stdout.split("\0")
              if p and not p.startswith(".sgt/")}
    now = tree(repo)
    same_as_before = all(now.get(p) == b for p, b in wanted.items()) and set(now) == set(wanted)
    rows.append(f"| newest ({newest['session']}) out = tree before it | {newest['sha']} | "
                f"{'pass' if checks(repo) else 'FAIL'} | {len(out.get('undone', ()))} | "
                f"{len(out.get('held', ()))} ({reasons(out)}) | {'yes' if same_as_before else 'NO'} |")
    return rows


def main() -> int:
    key = json.loads((ROOT / "docs" / "study" / "answer-key.json").read_text())
    with tempfile.TemporaryDirectory() as tmp:
        for name in ("bikecount", "footfall"):
            print("\n".join(project(name, key["episodes"][name], Path(tmp) / name)))
            print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
