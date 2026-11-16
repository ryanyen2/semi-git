"""The one point every reader hesitated on, measured both ways. When a request is off and a request
that is on changed a definition the off request created:
  A (the prototype): the definition goes, with the later changes inside it
  B (the alternative): the definition stays, because something that is on changed it
usage: alt_rule.py <bikecount|footfall|ts>"""
HERE = __import__("os").path.dirname(__import__("os").path.abspath(__file__))   # this folder
ROOT = __import__("os").path.abspath(__import__("os").path.join(HERE, "..", "..", ".."))   # the repository
import sys
S = HERE
which = sys.argv[1]
if which == "ts":
    exec(compile(open(S + "/compile_steps.py").read().split("SPARE = []")[0], "compile_steps.py", "exec"))
    names = [s[:28] for s in SUBJ]
    n_req = len(SUBJ)
else:
    sys.argv = ["x", which]
    exec(compile(open(S + "/check_py.py").read().split('if __name__ == "__main__":')[0], "check_py.py", "exec"))
    names, n_req = NAMES, N
BIRTH = g["BIRTH"]
head = [l for l in lines.values() if l.delh is None and units[unit_of(l.id)].died is None]
tot = {"A": [0, 0, 0], "B": [0, 0, 0]}
rows = []
for X in range(1, n_req):
    on = ALL - {X}
    touched = {h.unit for h in hunks.values() if h.req in on and h.req != units[h.unit].born}
    keepers = frozenset(BIRTH[u.uid] for u in units.values() if u.born == X and u.uid in touched and BIRTH.get(u.uid) is not None)
    out = {}
    for tag, forced in (("A", frozenset()), ("B", keepers)):
        files = program(on, forced=forced)[0]
        shown = {x for p, ids in LAST_MAP.items() if p in files for x in ids}
        own = sum(1 for l in head if l.born == X)
        gone = sum(1 for l in head if l.born == X and l.id not in shown)
        others = sum(1 for l in head if l.born != X and l.id not in shown)
        out[tag] = (own, gone, others)
        tot[tag][0] += own; tot[tag][1] += gone; tot[tag][2] += others
    if out["A"] != out["B"]:
        rows.append(f"  {names[X]:28s} of its {out['A'][0]:4d} lines: A removes {out['A'][1]:4d} (+{out['A'][2]} of others), B removes {out['B'][1]:4d} (+{out['B'][2]} of others)")
print(f"{which}: {n_req - 1} requests or commits, each taken out alone; the two rules differ for {len(rows)}")
print(f"  A: {tot['A'][1]} of {tot['A'][0]} own lines removed, {tot['A'][2]} lines of others removed with them")
print(f"  B: {tot['B'][1]} of {tot['B'][0]} own lines removed, {tot['B'][2]} lines of others removed with them")
print("\n".join(rows[:12]))
