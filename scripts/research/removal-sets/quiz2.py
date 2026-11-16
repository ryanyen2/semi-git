"""Materials and answers for a reading test of the one rule (bikecount): can someone who is told the
rule and the recorded facts, and sees no code, predict what stays when requests are switched off?"""
HERE = __import__("os").path.dirname(__import__("os").path.abspath(__file__))   # this folder
ROOT = __import__("os").path.abspath(__import__("os").path.join(HERE, "..", "..", ".."))   # the repository
import json, sys
S = HERE
src = open(S + "/check_py.py").read().split('if __name__ == "__main__":')[0]
sys.argv = ["x", "bikecount"]
exec(compile(src, "check_py.py", "exec"))


def label(u):
    kind, _, name = u.keys[-1][1].partition(" ")
    nm = {"def": name + "()", "assign": name}.get(kind, u.keys[-1][1])
    return u.places[0][1].split("/", 1)[-1] + " · " + nm


out = []
out.append("REQUESTS, oldest first, and the steps of each (a step = everything one request did to one function or other top-level definition):")
for i in range(1, N):
    rows = []
    for (_, uid) in sorted(steps_of({i}), key=lambda s: label(units[s[1]])):
        u = units[uid]
        rows.append(f"    {'created' if u.born == i else 'deleted' if u.died == i else 'changed'} {label(u)}")
    out.append(f"  {i}. {NAMES[i]}\n" + "\n".join(rows))
rw = set()
for h in hunks.values():
    for d in h.dels:
        x = lines[d].born
        if x != h.req and x != 0:
            rw.add((h.req, x, label(units[h.unit])))
out.append("\nREWRITES (a later request rewrote lines that an earlier request's step wrote, in the same definition):")
for y, x, lab in sorted(rw):
    out.append(f"  {NAMES[y]}'s step on [{lab}] rewrote lines of {NAMES[x]}'s step on [{lab}]")
us = set()
for u in units.values():
    for l in u.lines:
        if l.delh is None and l.born != 0:
            for w in l.uses:
                if units[w].born != 0:
                    us.add((l.born, label(u), label(units[w]), units[w].born))
out.append("\nUSES (code written by a request uses a definition that a request created; the two can be the same request):")
for j, v, w, i in sorted(us):
    out.append(f"  {NAMES[j]}'s code in [{v}] uses [{w}], created by {NAMES[i]}")
open(S + "/quiz2-materials.txt", "w").write("\n".join(out) + "\n")

scen = [["hour-profile"], ["monthly-trend"], ["event-days"], ["event-days", "event-annotation"],
        ["event-days", "event-annotation", "event-exclusion"], ["csv-download"], ["date-window", "csv-download"],
        ["monthly-trend", "event-annotation"], ["monthly-trend", "date-window"], ["hour-profile", "monthly-trend"]]
scen = [s for s in scen if all(n in NAMES for n in s)]
truth = []
for off_names in scen:
    off = frozenset(NAMES.index(n) for n in off_names)
    on = ALL - off
    files, rows = render(on, set())
    shown = {x for ids in rows.values() for x in ids}
    stay = sorted(f"{NAMES[i]} | {label(units[uid])}" for (i, uid) in steps_of(off) if not undone((i, uid), shown, set(), on))
    truth.append({"off": off_names, "stays": stay, "steps": len(steps_of(off))})
json.dump(truth, open(S + "/quiz2-truth.json", "w"), indent=1)
print(open(S + "/quiz2-materials.txt").read()[:200])
print("lines of material:", len(out), "| requests:", NAMES[1:])
for t in truth:
    print(t["off"], "->", len(t["stays"]), "of", t["steps"], "steps stay:", t["stays"][:6])
