"""The one rule on the Python study projects, with the project's own check (it runs the app) as judge.

Every on/off combination of the 12 requests: undo every step of the requests that are off; while the
check fails, keep the step the failing line sits in (innermost frame first), or the nearest undone
steps that code uses; then give back any step that turns out not to be needed.

usage: check_py.py <bikecount|footfall> [sample]
"""
HERE = __import__("os").path.dirname(__import__("os").path.abspath(__file__))   # this folder
ROOT = __import__("os").path.abspath(__import__("os").path.join(HERE, "..", "..", ".."))   # the repository
import json, multiprocessing as mp, os, random, sys, tempfile, time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

S = HERE
PKG = sys.argv[1]
SAMPLE = int(sys.argv[2]) if len(sys.argv) > 2 else 0
os.environ.pop("GITLOG", None)
os.environ.update(PKG=PKG, KEY=ROOT + "/docs/study/answer-key.json")
sys.argv = ["weave.py", "none", f"{S}/exp-{PKG}"]
g = {"__name__": "__main__"}
exec(compile(open(S + "/weave.py").read(), "weave.py", "exec"), g)
program, ALL, N, NAMES, lines, hunks, units, LAST_MAP, failing_frames = (g[k] for k in (
    "program", "ALL", "N", "NAMES", "lines", "hunks", "units", "LAST_MAP", "failing_frames"))
program(ALL)
UNIT_HUNKS = g["UNIT_HUNKS"]
work = Path(tempfile.mkdtemp(prefix="chk-"))
unit_of = lambda x: hunks[lines[x].addh].unit


def steps_of(off):
    return ({(h.req, h.unit) for h in hunks.values() if h.req in off} | {(u.died, u.uid) for u in units.values() if u.died in off}
            | {(k, u.uid) for u in units.values() for k, _ in u.places[1:] if k in off})


def undone(step, shown, kept, on):
    i, uid = step
    u = units[uid]
    if u.died != i and not any(l.id in shown for l in u.lines):
        return True         # the definition itself is not in the program
    if any(k == i for k, _ in u.places[1:]) and step not in kept:
        return True
    if step not in kept and any(l.id in shown and any(k == i for k, _ in l.texts[1:]) for l in u.lines):
        return True
    for l in u.lines:
        if l.born == i and l.id not in shown:
            j = None if l.delh is None else hunks[l.delh].req
            if j is None or j == i or (j not in on and (j, uid) not in kept):   # nothing that is in effect removed it
                return True
        if l.delh is not None and hunks[l.delh].req == i and l.born != i and l.id in shown:
            return True
    return u.died == i and any(l.id in shown for l in u.lines)


def render(on, kept):
    g["STAY_DEAD"].clear(); g["STAY_DEAD"].update(uid for (i, uid) in kept if units[uid].died == i)
    g["KEEP_PLACES"].clear(); g["KEEP_PLACES"].update(kept)
    files = program(on, forced=frozenset(h.id for (i, uid) in kept for h in UNIT_HUNKS.get(uid, ()) if h.req == i))[0]
    return files, {p: list(v) for p, v in LAST_MAP.items() if p in files}


def solve(mask):
    on = frozenset([0] + [i for i in range(1, N) if mask >> (i - 1) & 1])
    mine = steps_of(ALL - on)
    kept, unexplained, first, why = set(), 0, True, []
    for rnd in range(40):
        files, rows = render(on, kept)
        frames = failing_frames(files, work / f"m{mask}")
        if frames is None:
            break
        first = False
        shown = {x for ids in rows.values() for x in ids}
        gone = {s for s in mine if undone(s, shown, kept, on)} - kept
        more = set()
        for path, row in reversed(frames):          # innermost frame first
            ids = rows.get(path, [])
            if not 0 < row <= len(ids):
                continue
            uid = unit_of(ids[row - 1])
            more = {s for s in gone if s[1] == uid}
            seen, level = {uid}, {uid}
            while level and not more:
                nxt = set()
                for v in level:
                    for l in units[v].lines:
                        if l.id in shown:
                            nxt |= set(l.uses)
                nxt -= seen
                more = {s for s in gone if s[1] in nxt}
                seen |= nxt
                level = nxt
            if more:
                break
        if not more:
            unexplained += 1
            more = set(gone)
            if not more:
                break
        kept |= more
    ok = frames is None
    if ok:
        for s in sorted(kept):
            files, _ = render(on, kept - {s})
            if failing_frames(files, work / f"m{mask}g") is None:
                kept = kept - {s}
        why = sorted(f"{NAMES[i]}'s change to {units[uid].keys[-1][1]}" for i, uid in kept)
    return mask, first, ok, rnd, len(mine), len(kept), unexplained, why


if __name__ == "__main__":
    t0 = time.time()
    masks = list(range(2 ** (N - 1)))
    if SAMPLE:
        masks = random.Random(5).sample(masks, SAMPLE)
    with ProcessPoolExecutor(max_workers=8, mp_context=mp.get_context("fork")) as ex:
        res = list(ex.map(solve, masks, chunksize=8))
    n = len(res)
    first = sum(1 for r in res if r[1])
    ok = sum(1 for r in res if r[2])
    print(f"{PKG}: {n} on/off combinations; the app runs with the recorded rule alone in {first}; after the loop in {ok} ({100 * ok // n}%)")
    print(f"  rounds at most {max(r[3] for r in res)}; combinations where a failure was explained by no step: {sum(1 for r in res if r[6])}; "
          f"steps kept because of the check, per combination that needed any: mean {sum(r[5] for r in res if r[5]) / max(1, sum(1 for r in res if r[5])):.1f}")
    what = Counter(w for r in res for w in r[7])
    for w, c in what.most_common(6):
        print(f"  {c:5d}  kept: {w}")
    for r in [r for r in res if not r[2]][:5]:
        print(f"  still failing: off = {[NAMES[i] for i in range(1, N) if not r[0] >> (i - 1) & 1]}")
    print(f"took {time.time() - t0:.0f}s")
