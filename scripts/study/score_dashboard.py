"""Score one participant's dashboard repo against what the card asked for.

The old scorer counted pytest markers and drove a command line app. The study is a
web dashboard now, and what a card asks for is stated in terms of what the pages
show, so that is what gets measured: render every page and compare it against the
state the card was asking for.

    python3 scripts/study/score_dashboard.py <repo> --expect <golden-dir>

`--expect` is a directory of page snapshots produced by `snap.py`, one per page.
Two are needed per project and both are generated from the built testbed rather
than written by hand:

    removed/   the dashboard with the target work taken out    (card 3)
    original/  the dashboard as the participant first saw it   (card 4)

Three things come back, and they are deliberately separate.

  runs        whether the app starts at all. A repository that will not render is
              not a wrong answer, it is a different kind of outcome, and a scorer
              that folds the two together hides the most important failure.
  target      whether the pages the work was supposed to reach now match.
  collateral  whether every other page is untouched. This is the one the study is
              really about: removing the right thing while breaking something else
              is the failure mode the whole design exists to detect.
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent / "harvest"
FULL_RANGE = "start=2000-01-01&end=2030-01-01"


def render(repo, out_dir):
    """Snapshot every page. False if the app would not start."""
    r = subprocess.run(
        [sys.executable, str(HERE / "snap.py"), str(repo), str(out_dir), FULL_RANGE],
        capture_output=True, text=True,
    )
    return r.returncode == 0, (r.stdout + r.stderr).strip()


def compare(got_dir, want_dir):
    """(matching, differing, missing) page names."""
    want = {p.name for p in Path(want_dir).glob("*.txt")}
    got = {p.name for p in Path(got_dir).glob("*.txt")}
    matching, differing = [], []
    for name in sorted(want & got):
        a = (Path(want_dir) / name).read_text()
        b = (Path(got_dir) / name).read_text()
        (matching if a == b else differing).append(name[:-4])
    return matching, differing, sorted((want - got) | (got - want))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("repo")
    ap.add_argument("--expect", required=True, help="directory of golden page snapshots")
    ap.add_argument("--target-pages", default="",
                    help="comma-separated pages the work was supposed to reach")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    work = Path(args.repo).resolve().parent / f"_score-{Path(args.repo).name}"
    ok, message = render(args.repo, work)
    targets = [p for p in args.target_pages.split(",") if p]

    if not ok:
        out = {"runs": False, "why": message[-400:], "target": None, "collateral": None}
    else:
        matching, differing, absent = compare(work, args.expect)
        reached = [p for p in targets if p in matching] if targets else []
        missed = [p for p in targets if p not in matching] if targets else []
        others = [p for p in differing if p not in targets]
        out = {
            "runs": True,
            "target": {"expected": targets, "matching": reached, "not_matching": missed,
                       "ok": not missed} if targets else None,
            "collateral": {"pages_changed_that_should_not_have": others,
                           "pages_missing_or_extra": absent,
                           "ok": not others and not absent},
            "matching": matching,
            "differing": differing,
        }

    if args.json:
        print(json.dumps(out, indent=2))
    else:
        print(f"runs: {'yes' if out['runs'] else 'NO'}")
        if not out["runs"]:
            print(out["why"])
            return 1
        if out["target"]:
            t = out["target"]
            print(f"target: {'ok' if t['ok'] else 'NOT MET'} "
                  f"({', '.join(t['matching']) or 'none'} matching"
                  f"{'; missing ' + ', '.join(t['not_matching']) if t['not_matching'] else ''})")
        c = out["collateral"]
        print(f"collateral: {'none' if c['ok'] else 'DAMAGE'}"
              + (f" -> {', '.join(c['pages_changed_that_should_not_have'])}"
                 if c["pages_changed_that_should_not_have"] else "")
              + (f" -> pages missing or extra: {', '.join(c['pages_missing_or_extra'])}"
                 if c["pages_missing_or_extra"] else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
