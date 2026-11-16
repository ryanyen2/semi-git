"""Steps held only because a later request rewrote some of their lines: how many can be untangled?

For each such step, undo it inside its definition by a three-way text merge (the definition as it is
now, minus what the step did to it), and accept the result only if the merge is clean and the
project's check still passes.
usage: untangle.py <bikecount|footfall|ts>"""
HERE = __import__("os").path.dirname(__import__("os").path.abspath(__file__))   # this folder
ROOT = __import__("os").path.abspath(__import__("os").path.join(HERE, "..", "..", ".."))   # the repository
import subprocess, sys, tempfile
from pathlib import Path
S = HERE
which = sys.argv[1]
if which == "ts":
    exec(compile(open(S + "/compile_steps.py").read().split("SPARE = []")[0], "compile_steps.py", "exec"))
    n_req = len(SUBJ)
    passes = lambda files, tag: (lambda e: not e[0] and not e[1])(compile_errors(files, tag))
else:
    sys.argv = ["x", which]
    exec(compile(open(S + "/check_py.py").read().split('if __name__ == "__main__":')[0], "check_py.py", "exec"))
    n_req = N
    passes = lambda files, tag: failing_frames(files, work / tag) is None
mt = Path(tempfile.mkdtemp(prefix="m3-"))
_state = {}


def unit_text(k, uid):
    """the definition's text when the commits 0..k are in"""
    if k not in _state:
        files = program(frozenset(range(k + 1)), keep=False)[0]
        _state[k] = (files, {p: list(v) for p, v in LAST_MAP.items() if p in files})
        if len(_state) > 3:
            _state.pop(next(iter(_state)))
    files, rows = _state[k]
    out = []
    for p, ids in rows.items():
        text = files[p].splitlines(keepends=True)
        if len(text) == len(ids):
            out += [text[r] for r, x in enumerate(ids) if unit_of(x) == uid]
    return "".join(out)


def merge3(ours, base, theirs):
    for n, t in (("o", ours), ("b", base), ("t", theirs)):
        (mt / n).write_text(t)
    r = subprocess.run(["git", "merge-file", "-p", str(mt / "o"), str(mt / "b"), str(mt / "t")], capture_output=True, text=True)
    return r.stdout, r.returncode


tot = dict(held=0, overlap=0, clean=0, accepted=0, commits=0, commits_with=0)
for X in range(1, n_req):
    on = ALL - {X}
    files, kept, kept_h, _ = program(on)
    rows = {p: list(v) for p, v in LAST_MAP.items() if p in files}
    shown = {x for ids in rows.values() for x in ids}
    tot["commits"] += 1
    cands = []
    for (i, uid) in steps_of({X}):
        u = units[uid]
        gone = any(l.born == X and l.id not in shown and l.delh is None for l in u.lines)
        if gone or not any(l.id in shown for l in u.lines):
            continue
        if not any(l.born == X and l.id in shown for l in u.lines) and not any(l.delh is not None and hunks[l.delh].req == X for l in u.lines):
            continue
        tot["held"] += 1
        rewritten = any(hunks[a].req == X and hunks[a].unit == uid and hunks[b].req in on for a, b in kept_h.items())
        if rewritten and u.born != X and uid not in kept:
            cands.append(uid)
    if not cands:
        continue
    tot["commits_with"] += 1
    tot["overlap"] += len(cands)
    merged = {}
    for uid in cands:
        now, after, before = unit_text(n_req - 1, uid), unit_text(X, uid), unit_text(X - 1, uid)
        if not now or not after:
            continue
        text, c = merge3(now, after, before)
        if c == 0 and text != now:
            merged[uid] = text
    tot["clean"] += len(merged)

    def apply(uids):
        out = dict(files)
        for p, ids in rows.items():
            text = files[p].splitlines(keepends=True)
            if len(text) != len(ids) or not any(unit_of(x) in uids for x in ids):
                continue
            new, done = [], set()
            for r, x in enumerate(ids):
                uid = unit_of(x)
                if uid in uids:
                    if uid not in done:
                        new.append(merged[uid]); done.add(uid)
                else:
                    new.append(text[r])
            out[p] = "".join(new)
        return out

    if merged:
        if passes(apply(set(merged)), f"u{X}"):
            tot["accepted"] += len(merged)
        else:
            tot["accepted"] += sum(1 for uid in merged if passes(apply({uid}), f"u{X}_{uid}"))
print(f"{which}: {tot['commits']} requests or commits taken out one at a time; steps of theirs that are held: {tot['held']}")
print(f"  held only because a later request rewrote some of their lines: {tot['overlap']} (in {tot['commits_with']} of the commits)")
print(f"  of these, the text merge is clean for {tot['clean']}; clean and the check still passes for {tot['accepted']}")
