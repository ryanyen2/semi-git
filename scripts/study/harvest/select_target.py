"""Measure every landed session and say which ones could carry a study task.

The history is harvested, not authored, so we do not get to choose in advance
which piece of work becomes the target. This reads what actually landed, then
tries each candidate for real in a throwaway copy: revert the session, run the
smoke check, re-render every page, and see what moved. Pick from what it reports.
If nothing qualifies, harvest more work rather than loosening the criteria.

    python3 scripts/study/harvest/select_target.py <repo> [--quick]

The criteria, and why each one is there:

  differentiates  Plain `git revert` of the session's commits has to hit a
                conflict. If it applies cleanly then both tools do the same amount
                of work and there is nothing to compare, however good the piece of
                work looks otherwise.

                This replaced an earlier proxy that required the work to be spread
                over some minimum number of files and symbols. The proxy stood in
                for "git will struggle with this", which is now measured directly,
                and on the first full run the proxy was the only criterion failing
                the strongest candidate. A redundant stand-in that overrules the
                real measurement is worse than no criterion. Recorded here rather
                than quietly dropped, because loosening a gate after seeing which
                candidate it rejected is exactly the move that needs justifying.
  clean         Reverting has to leave a repo that still runs. A target that
                breaks the app is measuring demolition, not maintenance.
  visible       Something a person looks at has to change, and not everything.
                A target that changes nothing cannot be seen; one that changes
                every page cannot be told apart from a broken checkout.

A copy is made with `cp -R`, not `git clone`. The op store under `.sgt/ops` is
gitignored, so a clone arrives without the thing that makes any of this work.
"""
import re
import shutil
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

HERE = Path(__file__).resolve().parent


def load_sessions(repo):
    """session name -> what it owns, read from op attribution."""
    from sgt.core.store import Store

    out = defaultdict(lambda: {"ops": set(), "symbols": set(), "files": set(), "commits": set()})
    for op in Store(repo).all_ops():
        for name in {a.session for a in op.attribution if a.session}:
            rec = out[name]
            rec["ops"].add(op.id)
            # Anchors and residues are sgt's own positioning records, not code a
            # person wrote. Counting them makes every session look four times the
            # size it is.
            real = [s for s in op.footprint if "::__anchor__::" not in s and "::__residue__::" not in s]
            rec["symbols"].update(real)
            rec["files"].update(s.split("::")[0] for s in real)
            rec["commits"].update(op.provenance)
    return out


def commit_order(repo):
    out = subprocess.run(["git", "log", "--format=%H", "--reverse"], cwd=repo,
                         capture_output=True, text=True, check=True).stdout.split()
    return {sha: i for i, sha in enumerate(out)}


def snapshot(repo, out_dir):
    r = subprocess.run([sys.executable, str(HERE / "snap.py"), str(repo), str(out_dir)],
                       capture_output=True, text=True)
    return r.returncode == 0, (r.stdout + r.stderr).strip()


def biggest_number_shift(before_text, after_text):
    """The largest relative move of any number that appears on both versions of a page.

    "The page changed" is not the same as "a person would notice". A cleaning rule
    that shifts an average by one percent changes the text and would pass a
    diff-based check, while a participant comparing it against a published figure
    would have to be reading to four digits to catch it. This puts a number on it
    so visibility is judged rather than assumed.
    """
    def numbers(text):
        out = []
        for token in re.findall(r"-?[\d,]*\.?\d+", text):
            try:
                out.append(float(token.replace(",", "")))
            except ValueError:
                pass
        return out

    a, b = numbers(before_text), numbers(after_text)
    if len(a) != len(b):
        # Different counts mean rows or a whole section came or went, so position
        # n on one side is not position n on the other and comparing them pairwise
        # invents enormous shifts out of unrelated figures. Say so instead of
        # reporting a number that looks like a result.
        return None
    best = 0.0
    for x, y in zip(a, b):
        if abs(x) > 1 and x != y:
            best = max(best, abs(y - x) / abs(x))
    return best


def pages_that_moved(before, after):
    names = {p.name for p in Path(before).glob("*.txt")} | {p.name for p in Path(after).glob("*.txt")}
    moved, gone = [], []
    for name in sorted(names):
        a, b = Path(before) / name, Path(after) / name
        if not b.is_file():
            gone.append(name)
        elif not a.is_file():
            moved.append(name + " (new)")
        elif a.read_text() != b.read_text():
            shift = biggest_number_shift(a.read_text(), b.read_text())
            if shift is None:
                label = "rows added or removed"
            elif shift:
                label = f"{shift*100:.0f}% max number shift"
            else:
                label = "text only"
            moved.append(f"{name} ({label})")
    return moved, gone


def git_revert_conflicts(repo, commits, order, workdir):
    """Does undoing this work with plain git hit a conflict?

    The decisive measurement. If plain `git revert` applies cleanly then both
    tools do the same amount of work and the piece of work cannot carry a task,
    however good it looks on every other criterion. Done newest first, which is
    the order a person would try, in a copy so the real repo is never touched.
    """
    copy = workdir / "gitprobe"
    shutil.rmtree(copy, ignore_errors=True)
    subprocess.run(["git", "clone", "-q", repo, str(copy)], check=True, capture_output=True)
    conflicted = []
    for sha in sorted(commits, key=lambda s: order.get(s, 0), reverse=True):
        r = subprocess.run(["git", "revert", "--no-edit", "--no-commit", sha],
                           cwd=copy, capture_output=True, text=True)
        if r.returncode != 0 or "CONFLICT" in r.stdout + r.stderr:
            files = [ln.split("in ")[-1] for ln in (r.stdout + r.stderr).splitlines()
                     if "CONFLICT" in ln]
            conflicted.append((sha[:7], files))
        subprocess.run(["git", "revert", "--quit"], cwd=copy, capture_output=True)
        subprocess.run(["git", "reset", "--hard", "-q", "HEAD"], cwd=copy, capture_output=True)
    shutil.rmtree(copy, ignore_errors=True)
    return conflicted


def try_revert(repo, name, baseline, workdir):
    """Revert the session in a copy and report what survived."""
    copy = workdir / f"revert-{name}"
    shutil.rmtree(copy, ignore_errors=True)
    shutil.copytree(repo, copy, symlinks=True)

    r = subprocess.run(["sgt", "revert", "--session", name, "--yes"],
                       cwd=copy, capture_output=True, text=True)
    applied = r.returncode == 0
    preview = (r.stdout + r.stderr).strip()

    smoke = subprocess.run([sys.executable, "check.py"], cwd=copy, capture_output=True, text=True)
    ok, snap_msg = snapshot(copy, workdir / f"snap-{name}")
    moved, gone = pages_that_moved(baseline, workdir / f"snap-{name}") if ok else ([], [])

    shutil.rmtree(copy, ignore_errors=True)
    return {
        "applied": applied,
        "preview": preview,
        "smoke_ok": smoke.returncode == 0,
        "smoke": (smoke.stdout + smoke.stderr).strip().splitlines()[-1:] or [""],
        "renders": ok,
        "render_error": "" if ok else snap_msg[-300:],
        "moved": moved,
        "gone": gone,
    }


def main(repo, quick=False):
    repo = str(Path(repo).resolve())
    sessions = load_sessions(repo)
    if not sessions:
        print("no landed sessions found")
        return 1
    order = commit_order(repo)
    latest = {n: max((order.get(c, 0) for c in r["commits"]), default=0) for n, r in sessions.items()}

    workdir = Path("/private/tmp/claude-501/-Users-r4yen-repos-semi-git/"
                   "98ec4b59-83fc-485f-855a-1a547335d911/scratchpad/select")
    workdir.mkdir(parents=True, exist_ok=True)
    baseline = workdir / "snap-baseline"
    ok, msg = snapshot(repo, baseline)
    if not ok:
        print("the repo does not render as it stands, so nothing can be compared against it")
        print(msg[-500:])
        return 1
    print(f"baseline: {msg}\n")

    total_pages = len(list(baseline.glob("*.txt")))
    print(f"{len(sessions)} landed session(s), {total_pages} page(s)\n")

    for name in sorted(sessions, key=lambda n: latest[n]):
        rec = sessions[name]
        later = sorted(o for o in sessions
                       if latest[o] > latest[name] and (sessions[o]["files"] & rec["files"]))

        print(f"── {name}")
        print(f"   {len(rec['ops'])} ops · {len(rec['symbols'])} symbols · "
              f"{len(rec['files'])} files · {len(rec['commits'])} commits")
        print(f"   symbols: {', '.join(sorted(rec['symbols'])[:6])}"
              f"{' …' if len(rec['symbols']) > 6 else ''}")
        print(f"   later work in the same files: {', '.join(later) or 'none'}")

        if quick:
            print()
            continue

        t = try_revert(repo, name, baseline, workdir)
        conflicts = git_revert_conflicts(repo, rec["commits"], order, workdir)
        visible = 0 < len(t["moved"]) < total_pages
        checks = {
            "plain git revert conflicts": bool(conflicts),
            "sgt revert applies": t["applied"],
            "app still runs after": t["smoke_ok"] and t["renders"],
            "changes some pages, not all": visible,
        }
        for sha, files in conflicts:
            print(f"   git revert {sha}: conflicts in {', '.join(f.strip() for f in files) or '?'}")
        print(f"   revert: {'applied' if t['applied'] else 'REFUSED'} · "
              f"smoke {'ok' if t['smoke_ok'] else 'FAILED'} · "
              f"renders {'ok' if t['renders'] else 'FAILED'}")
        if t["render_error"]:
            print(f"     {t['render_error'].splitlines()[-1][:160]}")
        print(f"   pages that moved: {', '.join(t['moved']) or 'none'}")
        if t["gone"]:
            print(f"   pages that vanished: {', '.join(t['gone'])}")
        verdict = "CANDIDATE" if all(checks.values()) else "no"
        for label, good in checks.items():
            print(f"     {'yes' if good else ' no'}  {label}")
        print(f"   => {verdict}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], "--quick" in sys.argv))
