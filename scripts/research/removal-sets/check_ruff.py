"""The one rule on large Python histories, with a static checker (ruff: syntax errors, undefined
names, names used before assignment) as judge, counted relative to the current tree.

usage: check_ruff.py <repo> <srcroot> <pkg> <rev> [every-nth-commit]
"""
HERE = __import__("os").path.dirname(__import__("os").path.abspath(__file__))   # this folder
ROOT = __import__("os").path.abspath(__import__("os").path.join(HERE, "..", "..", ".."))   # the repository
import os, re, shutil, subprocess, sys, tempfile, time
from collections import Counter
from pathlib import Path

S = HERE
REPO, SRCROOT, PKG, REV = sys.argv[1:5]
NTH = int(sys.argv[5]) if len(sys.argv) > 5 else 1
os.environ.update(GITLOG="1", PKG=PKG, SRCROOT=SRCROOT, EXT=".py", REV=REV)
sys.argv = ["weave.py", "none", REPO]
src = open(S + "/weave.py").read().replace('if mode == "scale":', 'if mode == "none":\n    EXPECTED, HEADSTATE = build_incremental()\n    sys.exit(0)\nif mode == "scale":', 1)
g = {"__name__": "__main__"}
t0 = time.time()
try:
    exec(compile(src, "weave.py", "exec"), g)
except SystemExit:
    pass
SUBJ, program, ALL, lines, LAST_MAP, units, hunks = (g[k] for k in ("SUBJ", "program", "ALL", "lines", "LAST_MAP", "units", "hunks"))
tmp = Path(tempfile.mkdtemp(prefix="ruff-"))
BASE = None


def ruff(files, name):
    d = tmp / name
    for p, t in files.items():
        fp = d / p
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(t)
    r = subprocess.run(["ruff", "check", "--isolated", "--no-cache", "--select", "F821,F822,F823,E9", "--output-format", "concise", "."],
                       capture_output=True, text=True, cwd=str(d))
    shutil.rmtree(d, ignore_errors=True)
    out = []
    for m in re.finditer(r"^(.+?):(\d+):\d+: (\S+) (.*)$", r.stdout, re.M):
        out.append((m.group(1), int(m.group(2)), m.group(3).rstrip(":"), m.group(4)))
    return out


def compile_errors(files, name):
    """Errors the checker reports that the current tree does not have."""
    seen = Counter()
    new = []
    for e in ruff(files, name):
        key = (e[0], e[2], e[3])
        seen[key] += 1
        if seen[key] > BASE.get(key, 0):
            new.append(e)
    return new, 0


head = program(ALL, keep=False)[0]
BASE = Counter((e[0], e[2], e[3]) for e in ruff(head, "base"))
UNIT_HUNKS = g["UNIT_HUNKS"]
unit_of = lambda x: hunks[lines[x].addh].unit
cs = open(S + "/compile_steps.py").read()
exec(compile(cs[cs.index("def steps_of(off):"):cs.index("SPARE = []")], "compile_steps.py", "exec"))
SPARE = []
print(f"{REPO.split('/')[-1]}/{SRCROOT}{PKG}: {len(SUBJ)} commits, built in {time.time() - t0:.0f}s; the current tree has {sum(BASE.values())} checker findings of its own", flush=True)
res = []
for i in ([int(x) for x in os.environ["ONLY"].split(",")] if os.environ.get("ONLY") else range(1, len(SUBJ), NTH)):
    t1 = time.time()
    files0 = program(ALL - {i})[0]
    first = not compile_errors(files0, f"f{i}")[0]
    r = take_out({i}, f"t{i}")
    res.append((i, first) + r)
    if not r[0]:
        print(f"  commit {i} still fails [{SUBJ[i][:50]}]", flush=True)
n = len(res)
print(f"commits tried {n}: with the recorded rule alone the checker finds nothing new in {sum(1 for r in res if r[1])}; after the loop in {sum(1 for r in res if r[2])} "
      f"({100 * sum(1 for r in res if r[2]) // n}%); rounds at most {max(r[3] for r in res)}; commits where an error was explained by no step: {sum(1 for r in res if r[8])}")
print(f"steps {sum(r[4] for r in res)}: undone {sum(r[5] for r in res)}, kept by the record {sum(r[6] for r in res)}, kept by the checker {sum(r[7] for r in res)}; took {time.time() - t0:.0f}s")
shutil.rmtree(tmp, ignore_errors=True)
