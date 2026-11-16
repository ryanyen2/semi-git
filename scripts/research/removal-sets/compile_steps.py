"""One rule, checked with the TypeScript compiler on the extension's real history.

A step is what one commit did to one top-level definition. To take a commit out, undo as many of
its steps as leave a program the compiler accepts; a step that cannot be undone stays whole.

Which steps cannot be undone is found in one loop: undo, compile, and for every error keep the step
the error sits in, or, when the error sits in code the commit never touched, the nearest steps that
code uses. Nothing else decides (no name matching, no special cases for restored lines or deleted
definitions).

env: ONLY=<i,j,...> commits to try; SHOW=1 prints what was kept and why; MULTI=<n> also tries n
random sets of 2-4 commits.
"""
HERE = __import__("os").path.dirname(__import__("os").path.abspath(__file__))   # this folder
ROOT = __import__("os").path.abspath(__import__("os").path.join(HERE, "..", "..", ".."))   # the repository
import json, os, random, re, shutil, subprocess, sys, tempfile, time
from collections import Counter
from pathlib import Path

S = HERE
os.environ.update(GITLOG="1", PKG="src", SRCROOT="editor/vscode/", EXT=".ts")
REPO = ROOT
sys.argv = ["weave.py", "none", REPO]
src = open(S + "/weave.py").read().replace('if mode == "scale":', 'if mode == "none":\n    EXPECTED, HEADSTATE = build_incremental()\n    sys.exit(0)\nif mode == "scale":', 1)
g = {"__name__": "__main__"}
t0 = time.time()
try:
    exec(compile(src, "weave.py", "exec"), g)
except SystemExit:
    pass
SUBJ, program, ALL, lines, LAST_MAP, units, hunks = (g[k] for k in ("SUBJ", "program", "ALL", "lines", "LAST_MAP", "units", "hunks"))
EXT = Path(REPO) / "editor/vscode"
tmp = Path(tempfile.mkdtemp(prefix="tsc-"))


def compile_errors(files, name):
    """[(path, row, code, message)]"""
    d = tmp / name
    (d / "src").mkdir(parents=True)
    shutil.copy(EXT / "tsconfig.json", d / "tsconfig.json")
    shutil.copy(EXT / "package.json", d / "package.json")
    (d / "node_modules").symlink_to(EXT / "node_modules", target_is_directory=True)
    for p, t_ in files.items():
        fp = d / p.replace("editor/vscode/", "", 1)
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(t_)
    r = subprocess.run([str(EXT / "node_modules/.bin/tsc"), "--noEmit", "-p", str(d)], capture_output=True, text=True, cwd=str(d))
    shutil.rmtree(d, ignore_errors=True)
    out = [("editor/vscode/src/" + re.split(r"(?:^|/)src/", m.group(1), maxsplit=1)[-1], int(m.group(2)), m.group(3), m.group(4))
           for m in re.finditer(r"^(.+?)\((\d+),\d+\): error (TS\d+): ([^\n]*)", r.stdout, re.M)]
    loose = len(re.findall(r"error TS\d+", r.stdout)) - len(out)
    return out, loose


program(ALL, keep=False)
HEAD_ROWS = {p: list(v) for p, v in LAST_MAP.items()}
n_ = 0
for f, ln_, df, dl, *_rest in json.load(open(S + "/ts-uses.json")):
    if f in HEAD_ROWS and df in HEAD_ROWS and 0 < ln_ <= len(HEAD_ROWS[f]) and 0 < dl <= len(HEAD_ROWS[df]):
        g["USES_LINES"].setdefault(HEAD_ROWS[f][ln_ - 1], set()).add(HEAD_ROWS[df][dl - 1])
        n_ += 1
UNIT_HUNKS = g["UNIT_HUNKS"]
unit_of = lambda x: hunks[lines[x].addh].unit
print(f"built in {time.time() - t0:.0f}s; uses from the compiler: {n_}; steps in the history: "
      f"{len({(h.req, h.unit) for h in hunks.values()})}", flush=True)


def steps_of(off):
    return ({(h.req, h.unit) for h in hunks.values() if h.req in off} | {(u.died, u.uid) for u in units.values() if u.died in off}
            | {(k, u.uid) for u in units.values() for k, _ in u.places[1:] if k in off})   # moving a definition is part of the step


def undone(step, shown):
    """Is this step absent from the program shown?"""
    i, uid = step
    u = units[uid]
    if u.died != i and not any(l.id in shown for l in u.lines):
        return True         # the definition itself is not in the program
    if any(k == i for k, _ in u.places[1:]) and step not in g["KEEP_PLACES"]:
        return True         # it moved the definition, and the move is not kept
    if step not in g["KEEP_PLACES"] and any(l.id in shown and any(k == i for k, _ in l.texts[1:]) for l in u.lines):
        return True         # it respaced or re-indented a line, and that is not kept
    for l in u.lines:
        if l.born == i and l.id not in shown and (l.delh is None or hunks[l.delh].req == i):
            return True     # a line it wrote is not there
        if l.delh is not None and hunks[l.delh].req == i and l.born != i and l.id in shown:
            return True     # a line it removed is back
    return u.died == i and any(l.id in shown for l in u.lines)   # a definition it deleted is back


def uses_of(uid, shown):
    out = set()
    for l in units[uid].lines:
        if l.id in shown:
            out |= {unit_of(t) for t in g["USES_LINES"].get(l.id, ())} | set(l.uses)
    out.discard(uid)
    return out


def take_out(off, tag, show=False):
    """-> (compiles, rounds, steps, steps undone, steps kept by the record, steps kept by the compiler,
    errors nothing explained, share of the commits' own lines that go)"""
    off = frozenset(off)
    mine = steps_of(off)
    kept = set()            # steps the compiler said must stay
    why = {}
    unexplained = 0
    for rnd in range(40):
        forced = frozenset(h.id for (i, uid) in kept for h in UNIT_HUNKS.get(uid, ()) if h.req == i)
        g["STAY_DEAD"].clear()
        g["STAY_DEAD"].update(uid for (i, uid) in kept if units[uid].died == i)
        g["KEEP_PLACES"].clear()
        g["KEEP_PLACES"].update(kept)
        files = program(ALL - off, forced=forced)[0]
        rows = {p: list(v) for p, v in LAST_MAP.items() if p in files}
        shown = {x for ids in rows.values() for x in ids}
        gone = {s for s in mine if undone(s, shown)}
        if rnd == 0:
            by_record = len(mine) - len(gone)
        errs, loose = compile_errors(files, f"{tag}_{rnd}")
        if not errs and not loose:
            break
        more = set()
        cand = mine - kept      # any step not explicitly kept may be the one that is missing
        for path, row, code, msg in errs:
            ids = rows.get(path, [])
            if not 0 < row <= len(ids):
                continue
            uid = unit_of(ids[row - 1])
            here = {s for s in cand if s[1] == uid}
            if here:        # the error sits in a definition whose step was undone: that step stays
                for s in here:
                    why.setdefault(s, (msg, "in it"))
                more |= here
                continue
            seen, level = {uid}, {uid}
            while level:    # otherwise: the nearest undone steps that this code uses
                nxt = set()
                for v in level:
                    nxt |= uses_of(v, shown)
                nxt -= seen
                hit = {s for s in cand if s[1] in nxt}
                if hit:
                    for s in hit:
                        why.setdefault(s, (msg, "used by " + str(units[uid].keys[-1][1])[:40]))
                    more |= hit
                    break
                seen |= nxt
                level = nxt
        more -= kept
        if not more:        # an error no step explains: keep every step still undone in the files with errors
            unexplained += len(errs) + loose
            paths = {p for p, *_ in errs}
            more = {s for s in cand if any(p in paths for _, p in units[s[1]].places)} or set(cand)
            if not more:
                break
        kept |= more
    ok = not errs and not loose
    by_compiler = len(kept)
    if ok and not os.environ.get("NO_GIVEBACK"):   # give back, one at a time, any step that turns out not to be needed
        for s in sorted(kept):
            less = kept - {s}
            g["STAY_DEAD"].clear(); g["STAY_DEAD"].update(uid for (i, uid) in less if units[uid].died == i)
            g["KEEP_PLACES"].clear(); g["KEEP_PLACES"].update(less)
            f2 = program(ALL - off, forced=frozenset(h.id for (i, uid) in less for h in UNIT_HUNKS.get(uid, ()) if h.req == i))[0]
            e2, l2 = compile_errors(f2, f"{tag}_min")
            if not e2 and not l2:
                kept = less
                shown = {x for ids in LAST_MAP.values() for x in ids}
        SPARE.append((by_compiler, by_compiler - len(kept)))
        g["STAY_DEAD"].clear(); g["STAY_DEAD"].update(uid for (i, uid) in kept if units[uid].died == i)
        g["KEEP_PLACES"].clear(); g["KEEP_PLACES"].update(kept)
        files = program(ALL - off, forced=frozenset(h.id for (i, uid) in kept for h in UNIT_HUNKS.get(uid, ()) if h.req == i))[0]
        shown = {x for p, ids in LAST_MAP.items() if p in files for x in ids}
        gone = {s for s in mine if undone(s, shown)}
    own = [l.id for l in lines.values() if l.born in off and l.delh is None]
    share = sum(1 for x in own if x not in shown) / len(own) if own else None
    g["STAY_DEAD"].clear()
    g["KEEP_PLACES"].clear()
    g["LAST_KEPT"] = set(kept)
    if show:
        for s, (msg, how) in sorted(why.items()):
            print(f"      kept: commit {s[0]} in {str(units[s[1]].keys[-1][1])[:50]} ({how}): {msg[:110]}")
        for path, row, code, msg in errs[:6]:
            print(f"      still: {path.split('/')[-1]}:{row} {code} {msg[:120]}")
    return ok, rnd, len(mine), len(gone), by_record, len(kept), unexplained, share


SPARE = []
todo = [int(x) for x in os.environ["ONLY"].split(",")] if os.environ.get("ONLY") else range(1, len(SUBJ))
head_files = program(ALL, keep=False)[0]
same = 0
for i in range(1, len(SUBJ)):   # a commit that is off with every one of its steps kept must give the current program
    g["STAY_DEAD"].clear(); g["STAY_DEAD"].update(u.uid for u in units.values() if u.died == i)
    g["KEEP_PLACES"].clear(); g["KEEP_PLACES"].update((i, u.uid) for u in units.values())
    got = program(ALL - {i}, forced=frozenset(h.id for h in hunks.values() if h.req == i))[0]
    same += got == head_files
    if got != head_files:
        print(f"  commit {i}: off with every step kept differs from the current program in "
              f"{sorted(p.split('/')[-1] for p in set(got) | set(head_files) if got.get(p) != head_files.get(p))[:5]}")
g["STAY_DEAD"].clear(); g["KEEP_PLACES"].clear()
print(f"a commit off with all its steps kept equals the current program: {same} of {len(SUBJ) - 1}", flush=True)
res = {}
for i in todo:
    t1 = time.time()
    res[i] = take_out({i}, f"t{i}", show=os.environ.get("SHOW") == "1")
    ok, rnd, n, gone, by_record, by_compiler, unexplained, share = res[i]
    print(f"  {i:3d} {'ok  ' if ok else 'FAIL'} rounds {rnd} steps {n:3d}: undone {gone:3d}, kept by the record {by_record:3d}, by the compiler {by_compiler:3d}"
          f"{f', errors nothing explained {unexplained}' if unexplained else ''}; own lines that go {'-' if share is None else f'{100 * share:.0f}%'}"
          f" [{SUBJ[i][:44]}] {time.time() - t1:.0f}s", flush=True)
n = len(res)
oks = sum(1 for r in res.values() if r[0])
first = sum(1 for r in res.values() if r[0] and r[1] == 0)
shares = [r[7] for r in res.values() if r[7] is not None]
print(f"one commit taken out: compiles at once in {first} of {n}; after the loop in {oks} of {n} ({100 * oks // n}%); "
      f"rounds at most {max(r[1] for r in res.values())}; commits where an error was explained by no step: {sum(1 for r in res.values() if r[6])}; "
      f"own lines that go: mean {100 * sum(shares) / len(shares):.0f}%")
tot = Counter()
for r in res.values():
    tot["steps"] += r[2]; tot["undone"] += r[3]; tot["record"] += r[4]; tot["compiler"] += r[5]
print(f"steps of the commits tried: {tot['steps']}; undone {tot['undone']}; kept because the record says something stands on them {tot['record']}; "
      f"kept because the compiler said so {tot['compiler']}")
if SPARE:
    print(f"steps the compiler kept at first: {sum(a for a, _ in SPARE)}; given back because not needed after all: {sum(b for _, b in SPARE)}")
if os.environ.get("MULTI"):
    rng = random.Random(7)
    good = total = 0
    for k in range(int(os.environ["MULTI"])):
        off = set(rng.sample(range(1, len(SUBJ)), rng.choice((2, 3, 4))))
        r = take_out(off, f"m{k}")
        good += r[0]; total += 1
        print(f"  set {sorted(off)}: {'ok' if r[0] else 'FAIL'} rounds {r[1]}, steps {r[2]}, undone {r[3]}", flush=True)
    print(f"several commits taken out together: compiles in {good} of {total}")
shutil.rmtree(tmp, ignore_errors=True)
