"""Third version of what the tool says (default B), after the second round of fresh-user criticism:
what a visitor sees first (from the pages the project itself renders), then the code; holds labelled
by kind; cost of the complete removal in whole steps of named requests, with what a visitor would
see after it and a warning when the check itself changes; what is left unused. Real prototype output."""
HERE = __import__("os").path.dirname(__import__("os").path.abspath(__file__))   # this folder
ROOT = __import__("os").path.abspath(__import__("os").path.join(HERE, "..", "..", ".."))   # the repository
import difflib, json, re, subprocess, sys
from collections import defaultdict
S = HERE
sys.argv = ["x", "bikecount"]
exec(compile(open(S + "/check_py.py").read().split('if __name__ == "__main__":')[0], "check_py.py", "exec"))
SUBJ, materialize, SMOKE, closure = g["SUBJ"], g["materialize"], g["SMOKE"], g["closure"]
head = [l for l in lines.values() if l.delh is None and units[unit_of(l.id)].died is None]
RENDER = ("import sys, json; d = sys.argv[1]; sys.path.insert(0, d)\n"
          "from bikecount import data, pages, server\n"
          "r = data.load_readings(server.DATA)\n"
          "try:\n    from bikecount import window\n    s, e = window.default(r); w = window.apply(r, s, e)\n"
          "    out = {m.PATH: m.render(w, s, e) for m in pages.discover()}\n"
          "except Exception:\n    out = {m.PATH: m.render(r) for m in pages.discover()}\n"
          "print(json.dumps(out))\n")


def what(u):
    kind, _, name = u.keys[-1][1].partition(" ")
    f = u.places[0][1].split("/", 1)[-1]
    return ({"def": "function", "assign": "constant", "import": "import", "from": "import", "docstring": "module"}.get(kind, "statement"), f,
            name + "()" if kind == "def" else name if kind == "assign" else u.keys[-1][1][:30])


def fold(uids):
    by_file = defaultdict(lambda: defaultdict(list))
    for uid in uids:
        k, f, n = what(units[uid])
        by_file[f][k].append(n)
    parts = []
    for f in sorted(by_file):
        bits = [", ".join(sorted(by_file[f]["function"]))] if by_file[f]["function"] else []
        if by_file[f]["constant"]:
            bits.append("constants " + ", ".join(sorted(by_file[f]["constant"])))
        if by_file[f]["import"]:
            bits.append(f"{len(by_file[f]['import'])} import line{'s' if len(by_file[f]['import']) > 1 else ''}")
        if by_file[f]["module"]:
            bits.append("the file itself (other code imports it)")
        if by_file[f]["statement"]:
            bits.append(f"{len(by_file[f]['statement'])} other statement(s)")
        parts.append(f"{f}: " + ", ".join(bits))
    return "; ".join(parts)


def observe(files, tag):
    d = work / tag
    materialize(files, d)
    ok = subprocess.run([sys.executable, "-B", "-s", "-E", "-c", SMOKE, str(d)], capture_output=True, text=True, cwd=str(d.parent), timeout=120).returncode == 0
    p = subprocess.run([sys.executable, "-B", "-s", "-E", "-c", RENDER, str(d)], capture_output=True, text=True, cwd=str(d.parent), timeout=120)
    subprocess.run(["rm", "-rf", str(d)])
    try:
        pages = json.loads(p.stdout.strip().splitlines()[-1])
    except Exception:
        pages = {}
    return ok, pages


BASE_OK, BASE = observe(program(ALL, keep=False)[0], "base")
text_of = lambda line: re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", line)).strip()


def visitor(ok, pages):
    if not ok:
        return ["the project's check FAILS"]
    out = []
    gone = [p for p in BASE if p not in pages]
    if gone:
        out.append("page(s) no longer there: " + ", ".join(gone))
    same = []
    for p in BASE:
        if p not in pages:
            continue
        a, b = BASE[p].splitlines(), pages[p].splitlines()
        if a == b:
            same.append(p)
            continue
        minus = [text_of(x[1:]) or x[1:].strip()[:60] for x in difflib.unified_diff(a, b, lineterm="", n=0) if x.startswith("-") and not x.startswith("---")]
        plus = sum(1 for x in difflib.unified_diff(a, b, lineterm="", n=0) if x.startswith("+") and not x.startswith("+++"))
        shown = "; ".join(f'"{m[:50]}"' for m in [m for m in minus if m][:2])
        out.append(f"page {p}: {len(minus)} line(s) of its output change" + (f", for example what read {shown}" if shown else ""))
    if same:
        out.append(("all pages" if len(same) == len(BASE) else "page(s) " + ", ".join(same)) + " look exactly as before")
    return out


def render_with(on, extra_off_steps):
    off_h = frozenset(h.id for (r, uid) in extra_off_steps for h in UNIT_HUNKS.get(uid, ()) if h.req == r)
    files, kept, kept_h, _ = program(on, off_hunks=off_h)
    shown = {x for p, ids in LAST_MAP.items() if p in files for x in ids}
    ROWS.clear(); ROWS.update({p: list(v) for p, v in LAST_MAP.items() if p in files})
    return files, kept, kept_h, shown


ROWS = {}


def holders(off, on, steps, kept, kept_h, shown, extra):
    """for each held step: [(request, kind, the holding step)]"""
    out = {}
    for (i, uid) in steps:
        if undone((i, uid), shown, set(), on):
            continue
        u = units[uid]
        rs = set()
        if uid in kept and lines[kept[uid]].born not in off:
            by = lines[kept[uid]]
            rs.add((by.born, "uses it", (by.born, unit_of(by.id))))
        if u.born == i:
            for r in {h.req for h in UNIT_HUNKS.get(uid, ()) if h.req in on and h.req != 0 and (h.req, uid) not in extra}:
                rs.add((r, "changed it later", (r, uid)))
        for a, b in kept_h.items():
            if hunks[a].req == i and hunks[a].unit == uid and hunks[b].req in on:
                rs.add((hunks[b].req, "rewrote the same lines", (hunks[b].req, hunks[b].unit)))
        out[(i, uid)] = rs
    return out


def show(off_names):
    off = frozenset(NAMES.index(n) for n in off_names)
    on = ALL - off
    steps = steps_of(off)
    files, kept, kept_h, shown = render_with(on, set())
    own = [l for l in head if l.born in off]
    gone = [l for l in own if l.id not in shown]
    ok, pages = observe(files, "t")
    held = holders(off, on, steps, kept, kept_h, shown, set())
    go = [s for s in steps if s not in held]
    used = {t for l in head if l.id in shown for t in l.uses}
    unused = [uid for (i, uid) in held if units[uid].born == i and what(units[uid])[0] == "function" and uid not in used]
    out = [f"TAKE OUT: {', '.join(off_names)}"]
    out.append("  FOR A VISITOR (the pages the project renders, before and after):")
    out += ["    " + x for x in visitor(ok, pages)]
    code = ("all of it is removed" if not held else "none of it is removed" if not gone else "part of it is removed")
    out.append(f"  IN THE CODE: {code}: {len(gone)} of its {len(own)} lines go" + (f", {len(own) - len(gone)} stay" if held else "") + ".")
    made = [uid for (i, uid) in go if units[uid].born == i]
    chg = [uid for (i, uid) in go if units[uid].born != i]
    if made:
        out.append(f"    removed (it created them): {fold(made)}")
    if chg:
        out.append(f"    its changes undone inside: {fold(chg)}")
    if held:
        out.append("  HELD IN PLACE, and by whom:")
        by = defaultdict(lambda: defaultdict(set))
        inner = []
        for s_, rs in held.items():
            if not rs:
                inner.append(s_[1])
            for r, how, _ in rs:
                by[r][how].add(s_[1])
        for r in sorted(by):
            for how, uids in sorted(by[r].items()):
                mk = [x for x in uids if units[x].born in off]
                ch = [x for x in uids if units[x].born not in off]
                tgt = "; ".join(filter(None, [fold(mk), ("its change inside " + fold(ch)) if ch else ""]))
                out.append(f"    {NAMES[r]} {how}: {tgt}")
        if inner:
            out.append(f"    kept with them because the held code needs it: {fold(inner)}")
        if unused:
            out.append(f"    of what is held, nothing that remains calls: {fold(unused)}")
        # the complete set: everything that stands on it, in whole steps, closed until nothing outside needs it
        F = closure(frozenset(h.id for h in hunks.values() if h.req in off))
        for _ in range(20):
            touched = {(hunks[x].req, hunks[x].unit) for x in F}
            whole = frozenset(h.id for h in hunks.values() if (h.req, h.unit) in touched)
            if whole <= F:
                break
            F = closure(whole)
        extra = {(hunks[x].req, hunks[x].unit) for x in F if hunks[x].req not in off}
        f2 = program(ALL, strict=True, off_hunks=F)[0]
        sh2 = {x for p, ids in LAST_MAP.items() if p in f2 for x in ids}
        ok2, pages2 = observe(f2, "c")
        left = sum(1 for l in own if l.id in sh2)
        out.append("  TO REMOVE IT COMPLETELY you would also take out:")
        per = defaultdict(set)
        for r, uid in extra:
            per[r].add(uid)
        for r in sorted(per):
            total = len(steps_of({r}))
            n_lines = sum(1 for l in head if l.born == r and unit_of(l.id) in per[r])
            share = "ALL of it" if len(per[r]) == total else f"{len(per[r])} of its {total} steps, so it becomes partly removed"
            out.append(f"    {NAMES[r]} (\"{SUBJ[r][:60]}\"): {share}; {n_lines} lines in {fold(per[r])}")
        if any(units[uid].places[0][1].endswith("check.py") for _, uid in extra):
            out.append("    WARNING: this changes check.py, the check itself.")
        out.append(f"    after that, for a visitor: " + "; ".join(visitor(ok2, pages2)) + f". In the code: {len(own) - left} of its {len(own)} lines are gone.")
    return "\n".join(out)


print("THE 12 REQUESTS, oldest first:")
for i in range(1, N):
    print(f"  {i:2d}. {NAMES[i]}: {SUBJ[i][:90]}")
print(f"\nWith everything in, the project's check (check.py, which renders every page) passes; the pages are: {', '.join(BASE)}\n")
for names in (["year-table"], ["event-days"], ["event-days", "event-annotation", "event-exclusion"], ["csv-download"], ["hour-profile"], ["event-annotation"]):
    print(show(names)); print()
