"""Take a commit out, edit code that came back, then bring the commit back, with the compiler as judge.

  1. take commit X out (the rule in compile_steps.py); some of its steps stay
  2. change up to three lines that are only there because X is off; save that as a new step
  3. bring X back: the steps the save was written in place of stay off (the saved version stays);
     of the rest, as many come back as leave a program the compiler accepts
"""
HERE = __import__("os").path.dirname(__import__("os").path.abspath(__file__))   # this folder
ROOT = __import__("os").path.abspath(__import__("os").path.join(HERE, "..", "..", ".."))   # the repository
import os, sys, time
S = HERE
exec(compile(open(S + "/compile_steps.py").read().split("SPARE = []")[0], "compile_steps.py", "exec"))
SPARE = []
_nl = open(S + "/nonlinear.py").read()
exec(compile(_nl[_nl.index("def record_on("):_nl.index("\n\ndef same(")], "nonlinear.py", "exec"))
head = program(ALL, keep=False)[0]


def render(off, steps_on, extra=()):
    g["STAY_DEAD"].clear(); g["STAY_DEAD"].update(uid for (i, uid) in steps_on if units[uid].died == i)
    g["KEEP_PLACES"].clear(); g["KEEP_PLACES"].update(steps_on)
    files = program((ALL - off) | frozenset(extra), forced=frozenset(h.id for (i, uid) in steps_on for h in UNIT_HUNKS.get(uid, ()) if h.req == i))[0]
    rows = {p: list(v) for p, v in LAST_MAP.items() if p in files}
    g["STAY_DEAD"].clear(); g["KEEP_PLACES"].clear()
    return files, rows


def clean(files, tag):
    e, loose = compile_errors(files, tag)
    return e, not e and not loose


k = len(SUBJ)
tot = dict(commits=0, edited=0, saved_ok=0, saved_compiles=0, back_compiles=0, steps=0, already=0, clash=0, back=0, by_compiler=0, version_kept=0, unexplained=0)
t0 = time.time()
for X in ([int(x) for x in os.environ["ONLY"].split(",")] if os.environ.get("ONLY") else range(1, len(SUBJ))):
    off = frozenset({X})
    ok = take_out(off, f"b{X}")[0]
    K = set(g["LAST_KEPT"])
    if not ok:
        continue
    cur, rows = render(off, K)
    new, want = {}, []
    for p, ids in rows.items():
        text = cur[p].splitlines(keepends=True)
        new[p] = cur[p]
        if len(text) != len(ids):
            continue
        for r, x in enumerate(ids):
            ln = lines[x]
            if (len(want) < 3 and ln.delh is not None and hunks[ln.delh].req == X and text[r].strip()
                    and not text[r].rstrip().endswith(("\\", ",", "(", "[", "{", "`", "*/")) and "`" not in text[r]
                    and not text[r].lstrip().startswith(("*", "/*", "//"))):
                text[r] = text[r].rstrip("\n") + "  // edited while off\n"
                want.append(x)
        new[p] = "".join(text)
    if os.environ.get("ONLY"):
        back = [x for ids in rows.values() for x in ids if lines[x].delh is not None and hunks[lines[x].delh].req == X]
        print(f"   commit {X}: take-out ok {ok}, steps kept {len(K)}, lines that came back {len(back)}, chosen {len(want)}, "
              f"files with rows not matching {sum(1 for p, ids in rows.items() if len(cur[p].splitlines(keepends=True)) != len(ids))}")
    if not want:
        continue
    spots = record_on(g, ALL - off, new, k, same_rows=True, state=(cur, rows))
    if spots is None:
        continue
    tot["commits"] += 1; tot["edited"] += len(want)
    v2, _ = render(off, K, {k})
    tot["saved_ok"] += {p: t for p, t in v2.items() if t.strip()} == {p: t for p, t in new.items() if t.strip()}
    tot["saved_compiles"] += clean(v2, f"b{X}_s")[1]
    mine = steps_of(off)
    clash = {(X, unit_of(x)) for x in spots if not isinstance(x, tuple)} | {(X, x[1]) for x in spots if isinstance(x, tuple)}
    R = mine - K - clash
    dropped = set()
    for rnd in range(30):
        files, rws = render(off, K | R, {k})
        errs, good = clean(files, f"b{X}_{rnd}")
        if good:
            break
        shown = {x for ids in rws.values() for x in ids}
        more = set()
        for path, row, code, msg in errs:
            ids = rws.get(path, [])
            if not 0 < row <= len(ids):
                continue
            uid = unit_of(ids[row - 1])
            here = {s for s in R if s[1] == uid}
            if here:
                more |= here
                continue
            seen, level = {uid}, {uid}
            while level:
                nxt = set()
                for v in level:
                    nxt |= uses_of(v, shown)
                nxt -= seen
                hit = {s for s in R if s[1] in nxt}
                if hit:
                    more |= hit
                    break
                seen |= nxt
                level = nxt
        if not more:
            tot["unexplained"] += 1
            more = set(R)
            if not more:
                break
        R -= more
        dropped |= more
    if good:
        for s in sorted(dropped):
            f2, _ = render(off, K | R | {s}, {k})
            if clean(f2, f"b{X}_g")[1]:
                R.add(s); dropped.discard(s)
        files, _ = render(off, K | R, {k})
    tot["back_compiles"] += good
    tot["steps"] += len(mine); tot["already"] += len(K); tot["clash"] += len(clash); tot["back"] += len(R); tot["by_compiler"] += len(dropped)
    tot["version_kept"] += sum(t.count("// edited while off") for t in files.values())
    print(f"  {X:3d} {'ok  ' if good else 'FAIL'} steps {len(mine):3d}: stayed on all along {len(K):3d}, came back {len(R):3d}, "
          f"stay off because the save replaced them {len(clash)}, because the compiler said so {len(dropped)} [{SUBJ[X][:40]}]", flush=True)
    k += 1
print(f"commits tried {tot['commits']}, lines edited {tot['edited']}")
print(f"  after the save: program equals what was saved {tot['saved_ok']} of {tot['commits']}; it compiles {tot['saved_compiles']} of {tot['commits']}")
print(f"  commit brought back: compiles {tot['back_compiles']} of {tot['commits']}; the saved version of the edited lines is still there {tot['version_kept']} of {tot['edited']}")
print(f"  steps of these commits {tot['steps']}: on all along {tot['already']}, came back {tot['back']}, stay off because the save replaced them {tot['clash']}, "
      f"stay off because the compiler said so {tot['by_compiler']}; rounds where no step explained an error {tot['unexplained']}")
print(f"took {time.time() - t0:.0f}s")
