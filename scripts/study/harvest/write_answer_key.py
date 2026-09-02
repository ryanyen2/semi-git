"""Write the answer key from the repositories, rather than by hand.

Everything the key asserts is measured here: which chapter is the target, what a
participant may reasonably type to name it, and which parts of the dashboard the
removal actually reaches. A key typed by hand goes stale the moment a testbed is
rebuilt, and a stale key scores every participant against work that no longer
exists.

    python3 scripts/study/harvest/write_answer_key.py <bikecount-repo> <footfall-repo> <out.json>

Point it at the two SGT BUNDLES' `work/` directories, not at the source testbeds: the bundle build
finishes with `sgt log --rebuild`, so the graph a participant navigates is not necessarily the one
the source repo carries, and the key has to name the one they will see. `ANSWER_KEY_BASELINES` says
where the two `baseline-<project>` git-arm repos live (default: beside the first argument), and
`ANSWER_KEY_WORK` where to do the reverting (default: a fresh temp directory).
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
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


def theme_vocabulary(repo, theme_id):
    """Every name the sgt arm can read off the screen for one group.

    The group's own label and id are what `sgt log` prints on the ◆ row, and the saves' shas and
    subjects are what `--rail` and `git log` print. Neither is the whole vocabulary: a participant
    who runs `sgt log --focus "<group>"` is shown the group's CHECKPOINTS, listed under the map with
    the feature each one sits on, and naming one of those is naming the work. A key without them
    marks that answer wrong -- which is what the shipped key did until it was patched by hand, and
    a hand-patched key goes stale at the next rebuild.

    Derived rather than listed, and the rule is WHOLLY rather than partly: a checkpoint counts when
    every op in it is one of the group's, which means the checkpoint is this group's work under
    another name. Merely sharing an op is not enough -- the event-day work landed inside footfall's
    `Monthly Totals Page` chapter (3 of its 30 ops) and bikecount's (2 of 17), and accepting those
    would mark "Monthly Totals" a correct answer to "which work made the 2018 average wrong". On
    both testbeds the rule selects exactly the event-day chapters and nothing else.

    The feature labels those checkpoints sit on are deliberately NOT accepted, for the same reason:
    a lane the work touches is a different granularity from the work.
    """
    from sgt.core.store import Store

    themes = state_of(repo).load_json(repo, "intent_themes", default={}) or {}
    entry = themes.get(theme_id) or {}
    shas = {a[:7] for a in (entry.get("atom_shas") or ())}
    if not shas:
        return []
    ops = set()
    for op in Store(repo).all_ops():
        for prov in op.provenance:
            sha = prov if isinstance(prov, str) else getattr(prov, "sha", "")
            if sha and sha[:7] in shas:
                ops.add(op.id)
                break
    names = set()
    for chapter in _all_chapters(repo):
        held = set(chapter["op_ids"])
        if held and held <= ops:
            names.add(chapter["label"])
    return sorted(n for n in names if n)


def state_of(repo):
    """`sgt.state`, imported late like every other sgt import in this file: the module is imported
    against whichever checkout is on the path, and importing at module scope made this script
    unusable from a bundle's own interpreter."""
    from sgt import state

    return state


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
    # `restore` first, then `undo`, and either counts. Card 4 asks for the work back and names no
    # verb, so what has to exist is a reliable way, not a particular one. Measured on the shipped
    # footfall bundle: `restore <chapter>` brings the pages back exactly for 2 of 21 chapters, and
    # `undo` does it for the ones tried. Restore is not the inverse of revert at this granularity;
    # undo is (finding 56). Requiring restore alone would have failed a task participants can
    # complete, and accepting "restore exited zero" would have passed one they cannot.
    restored = work / "r"

    def _pages_match_before():
        shutil.rmtree(restored, ignore_errors=True)
        if not snapshot(copy, restored):
            return False
        for path in sorted(before.glob("*.txt")):
            other = restored / path.name
            if not other.is_file() or other.read_text() != path.read_text():
                return False
        return True

    back = subprocess.run(["sgt", "restore", selector, "--yes"], cwd=copy, capture_output=True)
    back_ok = back.returncode == 0 and subprocess.run(
        [sys.executable, "check.py"], cwd=copy, capture_output=True).returncode == 0
    back_ok = back_ok and _pages_match_before()
    if not back_ok:
        # Put the restore attempt back before trying the other route, so `undo` is undoing the
        # revert and not the failed restore.
        subprocess.run(["sgt", "undo"], cwd=copy, capture_output=True)
        undo = subprocess.run(["sgt", "undo"], cwd=copy, capture_output=True)
        back_ok = undo.returncode == 0 and subprocess.run(
            [sys.executable, "check.py"], cwd=copy, capture_output=True).returncode == 0
        back_ok = back_ok and _pages_match_before()
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
        # The one place a candidate is tried, shared with the theme path above. It used to be
        # duplicated here, and the copy never grew the exact-restore check the other one has, so
        # every chapter was certified on "restore exited zero and the app still runs" while the
        # criterion card 4 actually states went unmeasured.
        moved = try_selector(repo, selector, work / f"c{chapter['seg_index']}-{chapter['feature_id'][2:8]}")
        if moved is not None:
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


def matching_shas(baseline_repo, subjects):
    """The git arm's shas for the same commits, found by subject.

    The two arms are separate builds: the git one is rendered from the sgt one with sgt's trailers
    and plumbing commits stripped, which rewrites every sha. So the sha a git participant reads off
    `git log` is not the sha the sgt repo carries, and a key holding only one arm's shas marks the
    other arm wrong for typing exactly what its own tool showed it. Subjects survive the rewrite,
    so they are what the two are matched on.
    """
    if not baseline_repo or not Path(baseline_repo).is_dir():
        return []
    out = []
    r = subprocess.run(["git", "log", "--format=%h%x00%s", "--no-merges"],
                       cwd=baseline_repo, capture_output=True, text=True)
    for line in r.stdout.splitlines():
        short, _, subject = line.partition("\x00")
        if subject in subjects:
            out.append(short)
    return out


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


# Page name -> the options the reach checklist offers. Two spellings per row where the two harvested
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

# `dateWindow` is deliberately absent. It is the one checklist option that is not a page -- the same
# control at the top of all of them -- so no page-level diff can name it and no measured reach set
# can contain it. That is correct for both of this study's targets, neither of which touches the
# window, and it is why the option exists at all: a checklist whose every option is in the key
# measures nothing. A future target that DOES move the window would need a within-page probe here,
# and `web/tests/answer-key.test.ts` asserts the option stays out of every reach set so the
# assumption cannot rot quietly.


def main(bike, foot, out_path):
    # The git arm of each project, rendered by render_git_arm.sh. Optional: if it is not built yet
    # the key still generates, without that arm's shas, and says so.
    #
    # Sibling of the sgt repo by default, overridable because the repo this runs against is now an
    # UNPACKED BUNDLE rather than the source testbed. The bundle build ends with `sgt log --rebuild`,
    # which can renumber features and rename groups, so a key generated from the source repo can
    # name work by an id the shipped bundle does not use. The bundle is what a participant runs, so
    # the bundle is what the key is measured against -- and an unpacked bundle has no `baseline-*`
    # beside it.
    default_baselines = Path(os.environ.get("ANSWER_KEY_BASELINES") or Path(bike).resolve().parent)
    baselines = {
        "bikecount": str(default_baselines / "baseline-bikecount"),
        "footfall": str(default_baselines / "baseline-footfall"),
    }
    # A scratch directory, not a session-scoped one: this used to be hardcoded to one agent
    # session's scratchpad, which no longer exists, so the script could not be re-run by anybody.
    work = Path(os.environ.get("ANSWER_KEY_WORK") or tempfile.mkdtemp(prefix="sgt-answer-key-"))
    work.mkdir(parents=True, exist_ok=True)
    print(f"working in {work}", file=sys.stderr)

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
            # The checkpoint and feature names the same group is listed under.
            accepted.extend(theme_vocabulary(repo, chapter["feature_id"]))
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
        subjects = {a for a in accepted if " " in a and not a.startswith("f-")}
        arm_shas = matching_shas(baselines[project], subjects)
        if not arm_shas:
            print(f"  note: no git-arm repo at {baselines[project]}, so that arm's shas are not "
                  f"in the key. Build it with render_git_arm.sh and regenerate.", file=sys.stderr)
        accepted.extend(arm_shas)
        locate[project] = sorted({a for a in accepted if a})

        pages = chapter["pages"]
        ids = sorted({b for page in pages for b in PAGE_TO_BEHAVIOUR.get(page, [])})
        reach_by_project[project] = {"pages": pages, "behaviours": ids, "chapter": chapter}

    # Stages 2 and 3 are the only scored checklists. Stage 1 used to be a third, against the newest
    # piece of work in the history; it is orientation now and asks no checklist, so nothing here
    # measures it and `requestKeys` carries no `s1`.
    for project, rec in reach_by_project.items():
        unmapped = [p for p in rec["pages"] if p not in PAGE_TO_BEHAVIOUR]
        if unmapped:
            sys.exit(f"{project} s2/s3: pages {unmapped} are not in PAGE_TO_BEHAVIOUR, so the "
                     f"reach key would silently omit them and score everyone short. Add them.")
        if not rec["behaviours"]:
            sys.exit(f"{project} s2/s3: the target reaches no option the checklist offers.")

    key = {
        # Bumped here, in the generator, and not in the file it writes. The shipped key said v7
        # while this constant said v6, because a past bump was made by hand -- so the next
        # regeneration silently *downgraded* the version, which is the one thing this field exists
        # to prevent: it is how two keys are told apart when a rebuilt testbed changes the answers
        # while every question stays the same. v8 is the first key generated against chapters
        # re-cut with the replayed asks weighing on their boundaries.
        "version": "answer-key-v8",
        "episodes": {p: episodes_of(str(Path(r).resolve())) for p, r in
                     (("bikecount", bike), ("footfall", foot))},
        "_note": ("Generated by scripts/study/harvest/write_answer_key.py from the built "
                  "testbeds. Regenerate it whenever either testbed is rebuilt: chapter indexes "
                  "and commit shas both move, and a key that names work the repository no "
                  "longer contains scores everyone as wrong."),
        # Versioned separately from the file on purpose (protocol.md): the reach answers are
        # unchanged by the re-cut -- the target reaches the same pages and the same checklist
        # behaviours -- so this stays where the published key had it rather than moving with the
        # file. Two sessions scored against v5 are comparable, which is the whole claim.
        "reachKeyVersion": "reach-key-v5",
        "requestKeys": {
            # One entry per stage that is scored against a key, under the stage's own id. They
            # were d1..d4 through two redesigns of what sat under them; the ids are s1..s4 now, and
            # a key carrying the old ones uploads clean and scores nothing. There is no `s1`: stage
            # 1 is orientation and asks nothing with a right answer.
            "s2": {
                "bikecount": reach_by_project["bikecount"]["chapter"]["label"],
                "footfall": reach_by_project["footfall"]["chapter"]["label"],
                "_locateNote": ("Accepted answers, not one correct string. The two arms name work "
                                "differently, so a commit sha and a chapter name are both right. "
                                "The match is provisional and the experimenter reads the answer."),
                "locate": locate,
                "reach": {p: r["behaviours"] for p, r in reach_by_project.items()},
                "_reachNote": ("Measured, not written: each testbed was copied, the target group "
                               "reverted, every page re-rendered, and the pages that moved mapped "
                               "onto the options the checklist offers. Stage 2's checklist (what "
                               "the found work reaches) and stage 3's (what the removal changed) "
                               "are scored against this same set, so `gain` compares like with "
                               "like."),
            },
            "s3": {
                "reach": {p: r["behaviours"] for p, r in reach_by_project.items()},
                "_reachNote": ("Identical to s2's by construction: one measurement, written to "
                               "both, so the prediction and the outcome report are scored against "
                               "the same truth."),
                "markers": {p: r["pages"] for p, r in reach_by_project.items()},
                "_note": ("Also scored from the repository, not from a string: ./check 3 and "
                          "scripts/study/score_dashboard.py compare every rendered page against "
                          "the post-removal snapshot. Collateral damage is pages moved and checks "
                          "failing outside the target."),
            },
            "s4": {
                "_note": ("Scored from the repository: the removed work is back and every page "
                          "matches the pre-removal snapshot. Run "
                          "scripts/study/score_dashboard.py against the original snapshot."),
            },
        },
        "rubrics": {
            "s3": [
                {"id": "back",
                 "label": ("The averages count every day again: the number the report quotes is "
                           "what the dashboard shows"),
                 "points": 1},
                {"id": "intact", "label": "Nothing else changed, and the smoke check passes",
                 "points": 1},
            ],
            "s4": [
                {"id": "restored",
                 "label": "The work is back and every page matches the pre-removal snapshot",
                 "points": 1},
            ],
        },
    }
    Path(out_path).write_text(json.dumps(key, indent=2) + "\n")
    print(f"wrote {out_path}")
    for project, rec in reach_by_project.items():
        c = rec["chapter"]
        print(f"  {project} s2/s3: {c['feature_label']}@{c['label']} -> pages {rec['pages']}"
              f" -> {rec['behaviours']}")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2], sys.argv[3]))
