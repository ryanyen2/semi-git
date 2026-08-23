#!/usr/bin/env python3
"""Score one participant's study repo after a removal task.

    scripts/score_study_repo.py <repo> --expect-removed waitlist,promotion,notify

Each test in the study projects carries a pytest marker naming the feature it
covers. After a participant removes a feature, the tests for that feature are
expected to fail or be gone, and every other feature's tests are expected to
pass. A test that breaks outside the expected set is collateral damage, which is
the measure the study reports.

The script also starts the program. A pilot participant finished with 29 passing
tests and an application that could not start, because nothing in the suite
builds the command line parser. Tests alone do not tell you the program works.
"""
from __future__ import annotations

import argparse
import configparser
import json
import re
import subprocess
import sys
from pathlib import Path

RESULT = re.compile(r"(\d+) (passed|failed|error|errors|deselected|skipped)")


def markers_in(repo: Path) -> list[str]:
    """The feature markers the project declares in pytest.ini."""
    parser = configparser.ConfigParser()
    parser.read(repo / "pytest.ini")
    raw = parser.get("pytest", "markers", fallback="")
    return [line.split(":", 1)[0].strip() for line in raw.splitlines() if line.strip()]


def run_marker(repo: Path, python: Path, marker: str) -> dict:
    """Run the tests carrying one marker. Reports counts, and whether the suite
    could be imported at all, which is a different failure from a failing test."""
    proc = subprocess.run(
        [str(python), "-m", "pytest", "-q", "-m", marker, "--tb=no", "-p", "no:cacheprovider"],
        cwd=repo, capture_output=True, text=True,
    )
    counts = {kind: int(n) for n, kind in RESULT.findall(proc.stdout)}
    collected = counts.get("passed", 0) + counts.get("failed", 0) + counts.get("error", 0) \
        + counts.get("errors", 0)
    return {
        "marker": marker,
        "passed": counts.get("passed", 0),
        "failed": counts.get("failed", 0) + counts.get("error", 0) + counts.get("errors", 0),
        "collected": collected,
        "import_error": "errors during collection" in proc.stdout,
    }


# What "two bookings that meet exactly" is called in each project. The two are
# isomorphic (docs/study/testbed-spec.md 1), so this is a vocabulary map and not
# two different checks.
_PROBE_SHAPES = {
    "coursecraft": {
        "setup": [("course", "add", "CS101", "Intro"),
                  ("section", "add", "CS101", "ada", "--slot", "Mon 09:00-10:30",
                   "--room", "R1", "--capacity", "5"),
                  ("section", "add", "CS101", "bob", "--slot", "Mon 10:30-12:00",
                   "--room", "R1", "--capacity", "5"),
                  ("student", "add", "Ada", "ada@example.org")],
        "join_a": ("enroll", "1", "1"),
        "join_b": ("enroll", "1", "2"),
    },
    "confplan": {
        "setup": [("talk", "add", "T1", "Opening"),
                  ("session", "add", "T1", "ada", "--slot", "Sat 09:00-10:30",
                   "--room", "R1", "--capacity", "5"),
                  ("session", "add", "T1", "bob", "--slot", "Sat 10:30-12:00",
                   "--room", "R1", "--capacity", "5"),
                  ("attendee", "add", "Ada", "ada@example.org")],
        "join_a": ("register", "1", "1"),
        "join_b": ("register", "1", "2"),
    },
}


def program_starts(repo: Path, python: Path, package: str) -> tuple[bool, str]:
    """Build the command line parser and list its subcommands. This is the check
    the test suite does not do."""
    code = (
        f"from {package} import cli\n"
        "p = cli.build_parser()\n"
        "import json\n"
        "print(json.dumps(sorted(p._subparsers._group_actions[0].choices)))\n"
    )
    proc = subprocess.run([str(python), "-c", code], cwd=repo, capture_output=True, text=True)
    if proc.returncode != 0:
        return False, proc.stderr.strip().splitlines()[-1] if proc.stderr.strip() else "failed"
    return True, proc.stdout.strip()


def back_to_back_allowed(repo: Path, python: Path, package: str) -> tuple[bool, str]:
    """Whether two bookings that meet exactly are still treated as a clash.

    The behavioural check the marker scores cannot make. Episode 17's regression
    is invisible to the suite -- `test_back_to_back_is_fine` calls
    `slots.overlaps`, and the program calls `slots.ranges_clash` -- so a
    participant who reverses it and a participant who does not both finish with
    the same green markers. This drives the program instead, the way the
    participant's own `check.sh` does.

    Returns (allowed, detail). `allowed` True means the defect is gone.
    """
    code = (
        "import json, tempfile, os\n"
        f"from {package} import cli\n"
        # A path that does not exist yet. `mkstemp` leaves an EMPTY file behind,
        # and the storage layer json.loads whatever is at the path, so the probe
        # died on the empty string before it had run a single command.
        "path = os.path.join(tempfile.mkdtemp(), 'store.json')\n"
        "def run(*a):\n"
        "    try:\n"
        "        return cli.main(['--data', path, *a])\n"
        "    except SystemExit as e:\n"
        "        return e.code\n"
        "cmds = " + repr(_PROBE_SHAPES[package]) + "\n"
        "run('init')\n"
        "for c in cmds['setup']:\n"
        "    run(*c)\n"
        "first = run(*cmds['join_a'])\n"
        "second = run(*cmds['join_b'])\n"
        "audit = run('room', 'audit')\n"
        "os.path.exists(path) and os.unlink(path)\n"
        "print(json.dumps({'first': first, 'second': second, 'audit': audit}))\n"
    )
    # The probe writes to stdout, and so does the app. Only the last line is ours.
    proc = subprocess.run([str(python), "-c", code], cwd=repo, capture_output=True, text=True)
    if proc.returncode != 0:
        last = proc.stderr.strip().splitlines()
        return False, last[-1] if last else "the probe would not run"
    lines = [ln for ln in proc.stdout.strip().splitlines() if ln.startswith("{")]
    if not lines:
        return False, "the probe printed nothing"
    got = json.loads(lines[-1])
    # A non-zero exit from the second join is the regression: the two bookings
    # meet exactly and the program refuses the pair.
    if got["second"] not in (0, None):
        return False, "the second booking is still refused as a clash"
    if got["audit"] not in (0, None):
        return False, "the room audit still reports a clash"
    return True, "two bookings that meet exactly are both accepted"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("repo", type=Path)
    ap.add_argument("--baseline", type=Path, required=True,
                    help="a pristine copy of the same project, to compare against")
    ap.add_argument("--expect-removed", default="",
                    help="comma separated markers whose tests may fail or be gone")
    ap.add_argument("--expect-gone", default="",
                    help="comma separated command names that must no longer be offered")
    ap.add_argument("--expect-behaviour", default="", choices=["", "back-to-back-allowed"],
                    help="a behavioural check the markers cannot make. "
                         "back-to-back-allowed: episode 17 has been reversed")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    repo = args.repo.expanduser().resolve()
    python = repo / ".venv" / "bin" / "python"
    if not python.exists():
        print(f"no test environment at {python}", file=sys.stderr)
        return 2
    package = next((d.name for d in repo.iterdir()
                    if d.is_dir() and (d / "cli.py").exists()), None)
    if package is None:
        print(f"no package with a cli.py under {repo}", file=sys.stderr)
        return 2

    expect_removed = {m.strip() for m in args.expect_removed.split(",") if m.strip()}
    expect_gone = {c.strip() for c in args.expect_gone.split(",") if c.strip()}

    # Score against a pristine copy, not against the marker list. A participant may
    # delete a marker as part of a clean removal, and some markers have no tests even
    # before the session, so the marker list alone cannot say what they broke.
    base_repo = args.baseline.expanduser().resolve()
    base_python = base_repo / ".venv" / "bin" / "python"
    if not base_python.exists():
        print(f"no test environment at {base_python}", file=sys.stderr)
        return 2
    every_marker = sorted(set(markers_in(repo)) | set(markers_in(base_repo)) | expect_removed)
    baseline = {m: run_marker(base_repo, base_python, m) for m in every_marker}

    results = [run_marker(repo, python, m) for m in every_marker]
    started, detail = program_starts(repo, python, package)

    # `None` where no behavioural check was asked for, so "not checked" stays a
    # different thing from "checked and wrong" in both the JSON and the verdict.
    behaviour_ok: bool | None = None
    behaviour_detail = ""
    if args.expect_behaviour == "back-to-back-allowed":
        behaviour_ok, behaviour_detail = back_to_back_allowed(repo, python, package)

    def had_tests(marker: str) -> bool:
        return baseline[marker]["collected"] > 0

    collateral = [r for r in results
                  if r["marker"] not in expect_removed and r["failed"] > 0]
    missing = [r for r in results
               if r["marker"] not in expect_removed and r["collected"] == 0
               and had_tests(r["marker"])]
    removed_ok = [r for r in results
                  if r["marker"] in expect_removed and (r["collected"] == 0 or r["failed"] > 0)]
    still_there = [r for r in results
                   if r["marker"] in expect_removed and r["collected"] > 0 and r["failed"] == 0
                   and had_tests(r["marker"])]
    offered = json.loads(detail) if started and detail.startswith("[") else []
    not_gone = sorted(expect_gone & set(offered))

    if args.json:
        print(json.dumps({
            "markers": results, "collateral": [r["marker"] for r in collateral],
            "missing": [r["marker"] for r in missing],
            "removed": [r["marker"] for r in removed_ok],
            "still_present": [r["marker"] for r in still_there],
            "program_starts": started, "subcommands": offered,
            "commands_not_removed": not_gone,
            "behaviour": args.expect_behaviour or None,
            "behaviour_ok": behaviour_ok, "behaviour_detail": behaviour_detail,
        }, indent=2))
    else:
        print(f"{'feature':<14} {'tests':>6} {'pass':>6} {'fail':>6}   verdict")
        for r in sorted(results, key=lambda r: r["marker"]):
            if r["marker"] in expect_removed:
                verdict = "removed, as asked" if r in removed_ok else "STILL PRESENT"
            elif r["collected"] == 0:
                verdict = "TESTS GONE" if had_tests(r["marker"]) else "none to begin with"
            elif r["failed"]:
                verdict = "COLLATERAL DAMAGE"
            else:
                verdict = "kept"
            print(f"{r['marker']:<14} {r['collected']:>6} {r['passed']:>6} {r['failed']:>6}   {verdict}")

        print()
        if started:
            print(f"the program starts, and offers: {', '.join(offered)}")
        else:
            print(f"THE PROGRAM DOES NOT START: {detail}")
        if not_gone:
            print(f"commands that should be gone but are still offered: {', '.join(not_gone)}")
        if behaviour_ok is not None:
            print(f"{'behaviour fixed' if behaviour_ok else 'BEHAVIOUR NOT FIXED'}: "
                  f"{behaviour_detail}")

        print()
        print(f"collateral damage: {len(collateral)} feature(s) "
              f"{[r['marker'] for r in collateral] or ''}")
        if missing:
            print(f"tests removed that should have been kept: {[r['marker'] for r in missing]}")
        if still_there:
            print(f"asked to be removed but still passing: {[r['marker'] for r in still_there]}")
        if any(r["import_error"] for r in results):
            print("the test suite could not be imported, so these counts understate the damage")

    ok = (not collateral and not missing and not still_there and started and not not_gone
          and behaviour_ok is not False)
    print(f"\n{'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
