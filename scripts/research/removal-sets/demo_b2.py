"""Second version of what the tool says (default B), rewritten after the fresh users' criticism:
verdict first, what the project's own check observes, reasons grouped by the request that holds a
step, small items folded, what is left unused, and the set that removes the thing completely with
its cost. Real output of the prototype on bikecount."""
HERE = __import__("os").path.dirname(__import__("os").path.abspath(__file__))   # this folder
ROOT = __import__("os").path.abspath(__import__("os").path.join(HERE, "..", "..", ".."))   # the repository
import re, subprocess, sys
from collections import defaultdict
S = HERE
sys.argv = ["x", "bikecount"]
exec(compile(open(S + "/check_py.py").read().split('if __name__ == "__main__":')[0], "check_py.py", "exec"))
SUBJ, closure, materialize, SMOKE = g["SUBJ"], g["closure"], g["materialize"], g["SMOKE"]
head = [l for l in lines.values() if l.delh is None and units[unit_of(l.id)].died is None]


def what(u):
    kind, _, name = u.keys[-1][1].partition(" ")
    f = u.places[0][1].split("/", 1)[-1]
    if kind == "def":
        return "function", f, name + "()"
    if kind == "assign":
        return "constant", f, name
    if kind in ("import", "from"):
        return "import", f, u.keys[-1][1]
    if kind == "docstring":
        return "file header", f, "header"
    return "statement", f, u.keys[-1][1][:30]


def fold(uids):
    """functions by name, small items counted per file"""
    by_file = defaultdict(lambda: defaultdict(list))
    for uid in uids:
        k, f, n = what(units[uid])
        by_file[f][k].append(n)
    parts = []
    for f in sorted(by_file):
        bits = [", ".join(sorted(by_file[f]["function"]))] if by_file[f]["function"] else []
        for k in ("constant", "import", "file header", "statement"):
            v = by_file[f][k]
            if v:
                bits.append(f"{len(v)} {k}{'s' if len(v) > 1 and k != 'file header' else ''}" + (f" ({', '.join(sorted(v))})" if k == "constant" else ""))
        parts.append(f"{f}: " + ", ".join(bits))
    return "; ".join(parts)


def check(files, tag):
    d = work / tag
    materialize(files, d)
    p = subprocess.run([sys.executable, "-B", "-s", "-E", "-c", SMOKE, str(d)], capture_output=True, text=True, cwd=str(d.parent), timeout=120)
    subprocess.run(["rm", "-rf", str(d)])
    m = re.search(r"render: (.*)$", p.stdout.strip().splitlines()[-1] if p.stdout.strip() else "")
    return p.returncode == 0, (m.group(1).split(", ") if m else [])


BASE_OK, BASE_PAGES = check(program(ALL, keep=False)[0], "base")


def pages_line(ok, pages):
    if not ok:
        return "the check FAILS"
    gone = [p for p in BASE_PAGES if p not in pages]
    return f"the check passes; pages it renders: {len(pages)} of {len(BASE_PAGES)}" + (f" ({', '.join(gone)} no longer there)" if gone else " (the same pages as before)")


def show(off_names):
    off = frozenset(NAMES.index(n) for n in off_names)
    on = ALL - off
    files, kept, kept_h, dormant = program(on)
    shown = {x for p, ids in LAST_MAP.items() if p in files for x in ids}
    own = [l for l in head if l.born in off]
    gone = [l for l in own if l.id not in shown]
    ok, pages = check(files, "t")
    steps = steps_of(off)
    go = [s for s in steps if undone(s, shown, set(), on)]
    stay = [s for s in steps if s not in go]
    verdict = ("removed completely" if not stay else "NOT removed" if not gone else "only partly removed")
    out = [f"TAKE OUT: {', '.join(off_names)}",
           f"  VERDICT: {verdict}. {len(gone)} of its {len(own)} lines go" + (f", {len(own) - len(gone)} stay." if stay else "."),
           f"  The project's check (check.py, renders every page): {pages_line(ok, pages)}."]
    made = [uid for (i, uid) in go if units[uid].born == i]
    chg = [uid for (i, uid) in go if units[uid].born != i]
    if made:
        out.append(f"  Removed (it created them): {fold(made)}")
    if chg:
        out.append(f"  Its changes undone inside: {fold(chg)}")
    held, inner = defaultdict(lambda: defaultdict(set)), []
    for (i, uid) in stay:
        u = units[uid]
        reasons = set()
        if uid in kept and lines[kept[uid]].born not in off:
            reasons.add((NAMES[lines[kept[uid]].born], "uses"))
        if u.born == i:
            for r in {h.req for h in UNIT_HUNKS.get(uid, ()) if h.req in on and h.req != 0}:
                reasons.add((NAMES[r], "changed it later"))
        for a, b in kept_h.items():
            if hunks[a].req == i and hunks[a].unit == uid and hunks[b].req in on:
                reasons.add((NAMES[hunks[b].req], "rewrote lines of it"))
        if not reasons:
            inner.append(uid)
        for r, how in reasons:
            held[r][how].add(uid)
    if held:
        out.append("  Held in place by other requests:")
        for r in sorted(held, key=lambda r: NAMES.index(r)):
            for how, uids in sorted(held[r].items()):
                mk = [x for x in uids if units[x].born in off]
                ch = [x for x in uids if units[x].born not in off]
                if mk:
                    out.append(f"    {r} {how}: {fold(mk)}")
                if ch:
                    out.append(f"    {r} {how}: its change inside {fold(ch)}")
        if inner:
            out.append(f"    and, needed by what is held: {fold(inner)}")
    used_by_on = {t for l in head if l.id in shown and l.born not in off for t in l.uses}
    unused = [uid for (i, uid) in stay if units[uid].born == i and what(units[uid])[0] == "function" and uid not in used_by_on]
    if unused:
        out.append(f"  Left in place, but nothing that remains calls it: {fold(unused)}")
    if stay:
        F = closure(frozenset(h.id for h in hunks.values() if h.req in off))
        cost = defaultdict(lambda: [0, set()])
        for l in head:
            if l.addh in F and l.born not in off:
                cost[NAMES[l.born]][0] += 1
                cost[NAMES[l.born]][1].add(unit_of(l.id))
        files2 = program(ALL, strict=True, off_hunks=F)[0]
        shown2 = {x for p, ids in LAST_MAP.items() if p in files2 for x in ids}
        ok2, pages2 = check(files2, "c")
        left = sum(1 for l in own if l.id in shown2)
        total = sum(v[0] for v in cost.values())
        out.append(f"  TO REMOVE IT COMPLETELY you would also take out {total} lines of {len(cost)} other request(s):")
        for r in sorted(cost, key=lambda r: NAMES.index(r)):
            out.append(f"    {r}: {cost[r][0]} lines in {fold(cost[r][1])}")
        out.append(f"    after that: {len(own) - left} of its {len(own)} lines are gone; {pages_line(ok2, pages2)}.")
    return "\n".join(out)


print("THE 12 REQUESTS, oldest first:")
for i in range(1, N):
    print(f"  {i:2d}. {NAMES[i]}: {SUBJ[i][:90]}")
print(f"\nWith everything in, the project's check passes and renders {len(BASE_PAGES)} pages: {', '.join(BASE_PAGES)}\n")
for names in (["year-table"], ["event-days"], ["event-days", "event-annotation", "event-exclusion"], ["csv-download"], ["hour-profile"], ["event-annotation"]):
    print(show(names)); print()
