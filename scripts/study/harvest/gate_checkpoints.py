"""Try every checkpoint's revert for real, the way a participant would type it.

`select_target.py` measures `sgt revert --session <name>`, which is the unit the
harvest lands work in. It is not the unit anyone types. The task hands people a
feature and a chapter name read off `sgt log --map`, so the operation that has to
work is `sgt revert "<Feature>@<Checkpoint>"`, and it is a different operation:
a session revert removes ops, a checkpoint revert usually rewrites symbols and
keeps later work.

Measuring one and shipping the other is how a task set passes its own gate and then
fails in front of a participant.

    python3 scripts/study/harvest/gate_checkpoints.py <repo>
"""
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
HERE = Path(__file__).resolve().parent
WORK = Path("/private/tmp/claude-501/-Users-r4yen-repos-semi-git/"
            "98ec4b59-83fc-485f-855a-1a547335d911/scratchpad/ckpt")


def checkpoints(repo):
    """(feature label, feature id, index, checkpoint label) for every chapter."""
    from sgt import state
    from sgt.intent.segment import feature_runs, overlay_persisted
    from sgt.lens.tree import load as load_tree

    tree = load_tree(repo)
    runs = feature_runs(repo, tree["op_leaf"])
    persisted = state.load_json(repo, "intent_segments", default={})
    out = []
    for fid, fruns in runs.items():
        label = tree["nodes"].get(fid, {}).get("label", fid)
        for i, seg in enumerate(overlay_persisted(fruns, persisted.get(fid))):
            out.append((label, fid, i, seg.label))
    return out


# Every page is snapshotted over the whole file, not the default window. One
# harvested job made the pages default to the last complete year, and that year is
# quiet enough that a feature can be removed without a single number moving inside
# it. Measuring the default view answered "no change" for work that visibly changes
# the app.
FULL_RANGE = "start=2013-09-01&end=2022-09-30"


def snapshot(repo, out_dir):
    r = subprocess.run([sys.executable, str(HERE / "snap.py"), str(repo), str(out_dir), FULL_RANGE],
                       capture_output=True, text=True)
    return r.returncode == 0


def moved(before, after):
    names = {p.name for p in Path(before).glob("*.txt")}
    out = []
    for name in sorted(names):
        a, b = Path(before) / name, Path(after) / name
        if not b.is_file():
            out.append(name + " (gone)")
        elif a.read_text() != b.read_text():
            out.append(name)
    return out


def main(repo):
    repo = str(Path(repo).resolve())
    # Cleared, not reused. The work directory is a fixed path, so running the gate on the second
    # project compared it against the first project's baseline and reported pages as "gone" that
    # the second project never had. A stale baseline does not fail, it lies.
    shutil.rmtree(WORK, ignore_errors=True)
    WORK.mkdir(parents=True, exist_ok=True)
    baseline = WORK / "baseline"
    if not snapshot(repo, baseline):
        print("the repo does not render as it stands")
        return 1
    total = len(list(baseline.glob("*.txt")))
    print(f"{total} page(s)\n")

    for label, fid, idx, ckpt in checkpoints(repo):
        selector = f"{fid[:10]}@{ckpt}"
        copy = WORK / f"c{idx}-{fid[2:8]}"
        shutil.rmtree(copy, ignore_errors=True)
        shutil.copytree(repo, copy, symlinks=True)

        rv = subprocess.run(["sgt", "revert", selector, "--yes"],
                            cwd=copy, capture_output=True, text=True)
        smoke = subprocess.run([sys.executable, "check.py"], cwd=copy,
                               capture_output=True, text=True)
        snap_dir = WORK / f"snap-{idx}-{fid[2:8]}"
        renders = snapshot(copy, snap_dir)
        changed = moved(baseline, snap_dir) if renders else []

        # Restoring with the same string is half the workflow, so it is measured too -- and
        # measured by card 4's own words, "check the dashboard matches what it showed at the
        # start", rather than by an exit status. Asking only for exit 0 and a running app is the
        # same mistake this file's docstring is about, one rung down: a restore that leaves the
        # work missing exits 0 over a dashboard that still renders, so the weak check passed it
        # green. Measured against the baseline instead, 12 of 18 footfall chapters that used to
        # pass did not put the pages back at all (findings 56 and 59).
        back = subprocess.run(["sgt", "restore", selector, "--yes"],
                              cwd=copy, capture_output=True, text=True)
        back_dir = WORK / f"back-{idx}-{fid[2:8]}"
        back_renders = snapshot(copy, back_dir)
        back_moved = moved(baseline, back_dir) if back_renders else ["(does not render)"]
        back_ok = (back.returncode == 0 and back_renders and not back_moved
                   and subprocess.run([sys.executable, "check.py"], cwd=copy,
                                      capture_output=True).returncode == 0)

        shutil.rmtree(copy, ignore_errors=True)

        ok = rv.returncode == 0 and smoke.returncode == 0 and renders
        good = ok and 0 < len(changed) < total and back_ok
        print(f"── {label} @ {ckpt}")
        print(f"   {selector}")
        print(f"   revert {'ok' if rv.returncode == 0 else 'FAILED'} · "
              f"app {'ok' if smoke.returncode == 0 else 'BROKEN'} · "
              f"renders {'ok' if renders else 'no'} · "
              f"restore {'ok' if back_ok else 'FAILED'}")
        print(f"   pages changed: {', '.join(changed) or 'none'}")
        if back_moved:
            print(f"   restore left wrong: {', '.join(back_moved)}")
        print(f"   => {'CANDIDATE' if good else 'no'}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
