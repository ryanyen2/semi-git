"""Write the answer key from the repositories, rather than by hand.

Everything the key asserts is measured here: which chapter is the target, what a
participant may reasonably type to name it, and which parts of the dashboard the
removal actually reaches. A key typed by hand goes stale the moment a testbed is
rebuilt, and a stale key scores every participant against work that no longer
exists.

    python3 scripts/study/harvest/write_answer_key.py <bikecount-repo> <footfall-repo> <out.json>
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
HERE = Path(__file__).resolve().parent
FULL_RANGE = "start=2000-01-01&end=2030-01-01"

# The chapter whose label says it keeps unusual days out of the averages. Matched on the
# label rather than an index, because a rebuild renumbers chapters and renames features but
# keeps saying what the work was.
WANTED = (
    # Most specific first. Both testbeds spread the event-day idea over several chapters, and the
    # card asks about one of them: the work that keeps those days out of the averages. Matching
    # "event day" first picked the chapter that merely tracks them in one project and the one that
    # excludes them in the other, so the two arms were answering different questions.
    "exclude event",
    "event-day average",
    "event day average",
    "quiet day",
    "snowstorm",
    "event day",
    "event days",
)


def _all_chapters(repo):
    from sgt import state
    from sgt.intent.segment import feature_runs, overlay_persisted
    from sgt.lens.tree import load as load_tree

    tree = load_tree(repo)
    runs = feature_runs(repo, tree["op_leaf"])
    persisted = state.load_json(repo, "intent_segments", default={})
    for fid, fruns in runs.items():
        flabel = tree["nodes"].get(fid, {}).get("label", fid)
        for i, seg in enumerate(overlay_persisted(fruns, persisted.get(fid))):
            yield {"feature_id": fid, "feature_label": flabel, "seg_index": i,
                   "label": seg.label, "op_ids": sorted(seg.op_ids)}


def event_theme(repo):
    """The cross-feature theme covering the event-day work, if the overlay found one.

    Preferred over any single chapter. The card asks about a job that was done over three
    afternoons with unrelated work in between, and a theme is the only grouping sgt builds that
    holds all three. Both testbeds produced one unaided, which is also the evidence that the two
    are isomorphic at the level the task is stated at.
    """
    from sgt import state

    themes = state.load_json(repo, "intent_themes", default={}) or {}
    best = None
    for tid, entry in themes.items():
        label = (entry or {}).get("label") or ""
        low = label.lower()
        if "event" not in low:
            continue
        spread = len((entry or {}).get("atom_shas") or ())
        if best is None or spread > best[2]:
            best = (tid, label, spread)
    if best is None:
        return None
    return {"theme_id": best[0], "label": best[1], "atoms": best[2]}


def try_selector(repo, selector, work):
    """Revert it, run the checks, render, restore. Returns the pages that moved, or None."""
    copy = work / "try"
    shutil.rmtree(copy, ignore_errors=True)
    shutil.copytree(repo, copy, symlinks=True)
    before, after = work / "b", work / "a"
    shutil.rmtree(before, ignore_errors=True)
    shutil.rmtree(after, ignore_errors=True)
    if not snapshot(copy, before):
        return None
    rv = subprocess.run(["sgt", "revert", selector, "--yes"], cwd=copy, capture_output=True)
    smoke = subprocess.run([sys.executable, "check.py"], cwd=copy, capture_output=True)
    ok_after = snapshot(copy, after)
    # Card 4 asks for the work back "exactly as it was", so that is what gets checked: not that
    # restore exited zero and the app still starts, but that every page renders identically to
    # before the revert. A restore that comes most of the way back leaves the app running and the
    # participant with no way to tell they are not finished.
    back = subprocess.run(["sgt", "restore", selector, "--yes"], cwd=copy, capture_output=True)
    restored = work / "r"
    shutil.rmtree(restored, ignore_errors=True)
    back_ok = back.returncode == 0 and subprocess.run(
        [sys.executable, "check.py"], cwd=copy, capture_output=True).returncode == 0
    if back_ok:
        back_ok = snapshot(copy, restored)
    if back_ok:
        for path in sorted(before.glob("*.txt")):
            other = restored / path.name
            if not other.is_file() or other.read_text() != path.read_text():
                back_ok = False
                break
    moved = []
    if ok_after:
        for path in sorted(before.glob("*.txt")):
            other = after / path.name
            if not other.is_file() or other.read_text() != path.read_text():
                moved.append(path.stem)
    total = len(list(before.glob("*.txt")))
    shutil.rmtree(copy, ignore_errors=True)
    if (rv.returncode == 0 and smoke.returncode == 0 and ok_after and back_ok
            and 0 < len(moved) < total):
        return moved
    return None


def usable_target(repo, work):
    """The first chapter about event days that a participant could actually complete the task on.

    Name matching alone is not enough. Both testbeds spread the event-day idea over several
    chapters, and in bikecount the one whose label says it best ("Exclude Event Days") takes an
    import down with it and leaves the app dead, while a differently named one does the same job
    cleanly. A key that named the first label match would have sent every participant on that arm
    at a task whose own answer breaks the program.

    So each candidate is tried for real: revert it, run the checks, render every page, restore it.
    """
    theme = event_theme(repo)
    if theme is not None:
        pages = try_selector(repo, theme["theme_id"], work)
        if pages is not None:
            return {"kind": "theme", "feature_id": theme["theme_id"],
                    "feature_label": theme["label"], "seg_index": None,
                    "label": theme["label"], "op_ids": [], "pages": pages,
                    "atoms": theme["atoms"]}

    chapters = list(_all_chapters(repo))

    def rank(ch):
        label = ch["label"].lower()
        for i, word in enumerate(WANTED):
            if word in label:
                return i
        return len(WANTED)

    for chapter in sorted(chapters, key=rank):
        if rank(chapter) == len(WANTED):
            continue
        selector = f"{chapter['feature_id'][:10]}@{chapter['seg_index']}"
        copy = work / f"try-{chapter['feature_id'][2:8]}-{chapter['seg_index']}"
        shutil.rmtree(copy, ignore_errors=True)
        shutil.copytree(repo, copy, symlinks=True)
        before, after = work / "b", work / "a"
        shutil.rmtree(before, ignore_errors=True)
        shutil.rmtree(after, ignore_errors=True)
        ok_before = snapshot(copy, before)
        rv = subprocess.run(["sgt", "revert", selector, "--yes"], cwd=copy,
                            capture_output=True, text=True)
        smoke = subprocess.run([sys.executable, "check.py"], cwd=copy, capture_output=True)
        ok_after = snapshot(copy, after)
        back = subprocess.run(["sgt", "restore", selector, "--yes"], cwd=copy,
                              capture_output=True, text=True)
        back_ok = back.returncode == 0 and subprocess.run(
            [sys.executable, "check.py"], cwd=copy, capture_output=True).returncode == 0
        moved = []
        if ok_before and ok_after:
            for path in sorted(before.glob("*.txt")):
                other = after / path.name
                if not other.is_file() or other.read_text() != path.read_text():
                    moved.append(path.stem)
        shutil.rmtree(copy, ignore_errors=True)
        total = len(list(before.glob("*.txt")))
        if (rv.returncode == 0 and smoke.returncode == 0 and ok_after and back_ok
                and 0 < len(moved) < total):
            chapter["pages"] = moved
            return chapter
    return None


def target_chapter(repo):
    from sgt import state
    from sgt.intent.segment import feature_runs, overlay_persisted
    from sgt.lens.tree import load as load_tree

    tree = load_tree(repo)
    runs = feature_runs(repo, tree["op_leaf"])
    persisted = state.load_json(repo, "intent_segments", default={})
    for fid, fruns in runs.items():
        flabel = tree["nodes"].get(fid, {}).get("label", fid)
        for i, seg in enumerate(overlay_persisted(fruns, persisted.get(fid))):
            hay = seg.label.lower()
            if any(w in hay for w in WANTED):
                return {"feature_id": fid, "feature_label": flabel,
                        "seg_index": i, "label": seg.label, "op_ids": sorted(seg.op_ids)}
    for fid, fruns in runs.items():
        flabel = tree["nodes"].get(fid, {}).get("label", fid)
        for i, seg in enumerate(overlay_persisted(fruns, persisted.get(fid))):
            if any(w in seg.label.lower() for w in WANTED):
                return {"feature_id": fid, "feature_label": flabel,
                        "seg_index": i, "label": seg.label, "op_ids": sorted(seg.op_ids)}
    return None


def _theme_saves(repo, theme_id):
    """The commits a theme groups, with their subjects: what a git participant would type."""
    from sgt import state

    themes = state.load_json(repo, "intent_themes", default={}) or {}
    shas = (themes.get(theme_id) or {}).get("atom_shas") or []
    out = []
    for sha in shas:
        r = subprocess.run(["git", "log", "-1", "--format=%h%x00%s", sha],
                           cwd=repo, capture_output=True, text=True)
        if r.returncode == 0 and r.stdout.strip():
            short, _, subject = r.stdout.strip().partition("\x00")
            if "sgt land" not in subject:
                out.append({"sha": short, "subject": subject})
    return out


def saves_for(repo, op_ids):
    """The commits that built those ops, with their subjects: what a git participant would type."""
    from sgt.core.store import Store

    shas = set()
    for op in Store(repo).all_ops():
        if op.id in op_ids:
            shas.update(op.provenance)
    out = []
    for sha in sorted(shas):
        r = subprocess.run(["git", "log", "-1", "--format=%h%x00%s", sha],
                           cwd=repo, capture_output=True, text=True)
        if r.returncode == 0 and r.stdout.strip():
            short, _, subject = r.stdout.strip().partition("\x00")
            if "sgt land" not in subject:
                out.append({"sha": short, "subject": subject})
    return out


def episodes_of(repo):
    """The jobs that actually landed, oldest first.

    The old key carried a hand-written list of twenty-two episodes because the testbed was built
    from a spec. This history was harvested instead, so the equivalent is what the sessions did,
    read back off the repository. Nobody has to keep it in step with anything.
    """
    from sgt.core.store import Store

    order = subprocess.run(["git", "log", "--format=%H%x00%s", "--reverse", "--no-merges"],
                           cwd=repo, capture_output=True, text=True).stdout.splitlines()
    by_sha = {}
    for op in Store(repo).all_ops():
        for a in op.attribution:
            if a.session and a.sha:
                by_sha.setdefault(a.sha, set()).add(a.session)
    out = []
    for line in order:
        sha, _, subject = line.partition("\x00")
        if "sgt land" in subject:
            continue
        sessions = sorted(by_sha.get(sha, ()))
        out.append({"sha": sha[:7], "subject": subject,
                    "session": sessions[0] if sessions else None})
    return out


def snapshot(repo, out_dir):
    r = subprocess.run([sys.executable, str(HERE / "snap.py"), str(repo), str(out_dir), FULL_RANGE],
                       capture_output=True, text=True)
    return r.returncode == 0


def measured_reach(repo, selector, work):
    """Which pages actually move when the target is removed. Measured, never written."""
    before, after = work / "before", work / "after"
    copy = work / "copy"
    shutil.rmtree(copy, ignore_errors=True)
    shutil.copytree(repo, copy, symlinks=True)
    if not snapshot(copy, before):
        return None
    subprocess.run(["sgt", "revert", selector, "--yes"], cwd=copy, capture_output=True)
    if not snapshot(copy, after):
        return None
    moved = []
    for path in sorted(before.glob("*.txt")):
        other = after / path.name
        if not other.is_file() or other.read_text() != path.read_text():
            moved.append(path.stem)
    shutil.rmtree(copy, ignore_errors=True)
    return moved


# Page name -> the options the reach trial offers. Two spellings per row where the two harvested
# apps named the same page differently ("/years" against "/yearly"): the agents chose their own
# routes, and a map that knew only one of them silently produced an empty reach key for that
# project, which scores every participant on it zero for a prediction they actually made.
PAGE_TO_BEHAVIOUR = {
    "overview": ["busiestDay", "recentChart"],
    "hourly": ["hourWeekday", "hourWeekend", "busiestHour"],
    "hour-of-day": ["hourWeekday", "hourWeekend", "busiestHour"],
    "monthly": ["monthly", "eventMarks"],
    "yearly": ["yearTable"],
    "years": ["yearTable"],
    "by-year": ["yearTable"],
    "sides": ["sideSplit"],
    "east-west": ["sideSplit"],
    "daily.csv": ["csv"],
}


def main(bike, foot, out_path):
    work = Path("/private/tmp/claude-501/-Users-r4yen-repos-semi-git/"
                "98ec4b59-83fc-485f-855a-1a547335d911/scratchpad/key")
    work.mkdir(parents=True, exist_ok=True)

    locate, reach_by_project = {}, {}
    for project, repo in (("bikecount", bike), ("footfall", foot)):
        repo = str(Path(repo).resolve())
        chapter = usable_target(repo, work / project)
        if chapter is None:
            sys.exit(f"{project}: no chapter about event days reverts cleanly, keeps the app "
                     f"running, changes something visible and restores. The harvest did not "
                     f"produce a usable target; harvest more work rather than relaxing this.")
        if chapter.get("kind") == "theme":
            selector = chapter["feature_id"]
            accepted = [chapter["label"], chapter["feature_id"]]
            for save in _theme_saves(repo, chapter["feature_id"]):
                accepted.extend([save["sha"], save["subject"]])
        else:
            selector = f"{chapter['feature_id'][:10]}@{chapter['seg_index']}"
            accepted = [
                chapter["label"],
                f"{chapter['feature_label']}@{chapter['label']}",
                chapter["feature_id"][:10],
                f"{chapter['feature_id'][:10]}@{chapter['seg_index']}",
            ]
            for save in saves_for(repo, set(chapter["op_ids"])):
                accepted.extend([save["sha"], save["subject"]])
        locate[project] = sorted({a for a in accepted if a})

        pages = chapter["pages"]
        ids = sorted({b for page in pages for b in PAGE_TO_BEHAVIOUR.get(page, [])})
        reach_by_project[project] = {"pages": pages, "behaviours": ids, "chapter": chapter}

    for project, rec in reach_by_project.items():
        unmapped = [p for p in rec["pages"] if p not in PAGE_TO_BEHAVIOUR]
        if unmapped:
            sys.exit(f"{project}: pages {unmapped} are not in PAGE_TO_BEHAVIOUR, so the reach key "
                     f"would silently omit them and score everyone short. Add them.")
        if not rec["behaviours"]:
            sys.exit(f"{project}: the target reaches no option the trial offers.")

    key = {
        "version": "answer-key-v4",
        "episodes": {p: episodes_of(str(Path(r).resolve())) for p, r in
                     (("bikecount", bike), ("footfall", foot))},
        "_note": ("Generated by scripts/study/harvest/write_answer_key.py from the built "
                  "testbeds. Regenerate it whenever either testbed is rebuilt: chapter indexes "
                  "and commit shas both move, and a key that names work the repository no "
                  "longer contains scores everyone as wrong."),
        "reachKeyVersion": "reach-key-v2",
        "requestKeys": {
            # Every card the study asks gets an entry, including the two that are scored by a
            # person rather than by a string match. A key that simply omits them reads to the
            # upload check as a key from an older design.
            "d1": {
                "_note": ("Observation only, nothing scored. The participant is asked which days "
                          "the averages leave out and why. Any wording that names the unusual "
                          "days and the reason on the page is right."),
            },
            "d4": {
                "_note": ("Scored from the repository, not from a string: the removed work is "
                          "back and every page matches the state at the start. Run "
                          "scripts/study/score_dashboard.py against the original snapshot."),
            },
            "d2": {
                "bikecount": reach_by_project["bikecount"]["chapter"]["label"],
                "footfall": reach_by_project["footfall"]["chapter"]["label"],
                "_locateNote": ("Accepted answers, not one correct string. The two arms name work "
                                "differently, so a commit sha and a chapter name are both right. "
                                "The match is provisional and the experimenter reads the answer."),
                "locate": locate,
            },
            "d3": {
                "reach": {p: r["behaviours"] for p, r in reach_by_project.items()},
                "_reachNote": ("Measured, not written: each testbed was copied, the target chapter "
                               "reverted, every page re-rendered, and the pages that moved mapped "
                               "onto the options the trial offers."),
                "bikecount": reach_by_project["bikecount"]["pages"],
                "footfall": reach_by_project["footfall"]["pages"],
            },
        },
        "rubrics": {
            "d4": [
                {"id": "back", "label": "The work is back and the dashboard matches the start", "points": 1},
                {"id": "intact", "label": "Nothing else changed, and the smoke check passes", "points": 1},
            ],
        },
    }
    Path(out_path).write_text(json.dumps(key, indent=2) + "\n")
    print(f"wrote {out_path}")
    for project, rec in reach_by_project.items():
        c = rec["chapter"]
        print(f"  {project}: {c['feature_label']}@{c['label']} -> pages {rec['pages']}")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2], sys.argv[3]))
