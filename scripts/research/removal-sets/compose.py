"""Do results found one commit at a time combine? For each commit, the compiler loop finds the steps
that must stay. Take two (or three) commits out together using only what was found for each alone,
with no further compiling to decide anything, and see whether the result compiles."""
HERE = __import__("os").path.dirname(__import__("os").path.abspath(__file__))   # this folder
ROOT = __import__("os").path.abspath(__import__("os").path.join(HERE, "..", "..", ".."))   # the repository
import os, random, sys
S = HERE
exec(compile(open(S + "/compile_steps.py").read().split("SPARE = []")[0], "compile_steps.py", "exec"))
SPARE = []
K = {}
for X in range(1, len(SUBJ)):
    if take_out({X}, f"c{X}")[0]:
        K[X] = set(g["LAST_KEPT"])
rng = random.Random(11)
good = total = 0
for n in range(60):
    off = frozenset(rng.sample(sorted(K), rng.choice((2, 2, 3))))
    kept = set().union(*(K[x] for x in off))
    g["STAY_DEAD"].clear(); g["STAY_DEAD"].update(uid for (i, uid) in kept if units[uid].died == i)
    g["KEEP_PLACES"].clear(); g["KEEP_PLACES"].update(kept)
    files = program(ALL - off, forced=frozenset(h.id for (i, uid) in kept for h in UNIT_HUNKS.get(uid, ()) if h.req == i))[0]
    errs, loose = compile_errors(files, f"cc{n}")
    ok = not errs and not loose
    good += ok; total += 1
    if not ok:
        print(f"  {sorted(off)}: {len(errs) + loose} errors, e.g. {errs[0][3][:100] if errs else ''}", flush=True)
print(f"commits taken out together, using only what was found for each alone: compiles in {good} of {total}")
