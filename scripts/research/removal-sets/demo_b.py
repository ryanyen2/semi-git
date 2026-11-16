"""Real output of the prototype under default B on bikecount, written the way the tool would say it."""
HERE = __import__("os").path.dirname(__import__("os").path.abspath(__file__))   # this folder
ROOT = __import__("os").path.abspath(__import__("os").path.join(HERE, "..", "..", ".."))   # the repository
import sys
S = HERE
sys.argv = ["x", "bikecount"]
exec(compile(open(S + "/check_py.py").read().split('if __name__ == "__main__":')[0], "check_py.py", "exec"))
SUBJ = g["SUBJ"]


def label(u):
    kind, _, name = u.keys[-1][1].partition(" ")
    nm = {"def": name + "()", "assign": name}.get(kind, u.keys[-1][1])
    return u.places[0][1].split("/", 1)[-1] + " · " + nm


def show(off_names):
    off = frozenset(NAMES.index(n) for n in off_names)
    on = ALL - off
    files, kept, kept_h, dormant = program(on)
    rows = {p: list(v) for p, v in LAST_MAP.items() if p in files}
    shown = {x for ids in rows.values() for x in ids}
    head = [l for l in lines.values() if l.delh is None and units[unit_of(l.id)].died is None]
    own = [l for l in head if l.born in off]
    gone = [l for l in own if l.id not in shown]
    others = [l for l in head if l.born not in off and l.id not in shown]
    runs = failing_frames(files, work / "demo") is None
    out = [f"TAKE OUT: {', '.join(off_names)}",
           f"  result: {len(gone)} of their {len(own)} lines go; {len(others)} lines written by other requests go; the app {'still runs' if runs else 'FAILS its check'}"]
    goes, stays = [], []
    for (i, uid) in sorted(steps_of(off), key=lambda s: (s[0], label(units[s[1]]))):
        u = units[uid]
        what = f"{NAMES[i]} {'created' if u.born == i else 'changed'} {label(u)}"
        if undone((i, uid), shown, set(), on):
            goes.append("    " + what)
            continue
        why = []
        if uid in kept:
            by = lines[kept[uid]]
            why.append(f"{NAMES[by.born]}'s code in {label(units[unit_of(by.id)])} uses it")
        if u.born == i:
            ch = sorted({NAMES[h.req] for h in UNIT_HUNKS.get(uid, ()) if h.req in on and h.req != i})
            if ch:
                why.append(f"{', '.join(ch)} changed it later")
        rw = sorted({NAMES[hunks[b].req] for a, b in kept_h.items() if hunks[a].req == i and hunks[a].unit == uid and hunks[b].req != i})
        if rw:
            why.append(f"{', '.join(rw)} rewrote lines of it")
        stays.append("    " + what + "  <- stays because " + ("; ".join(why) if why else "something that stays needs it"))
    out.append(f"  goes ({len(goes)} steps):")
    out += goes[:14] + ([f"    ... and {len(goes) - 14} more"] if len(goes) > 14 else [])
    out.append(f"  stays ({len(stays)} steps):")
    out += stays or ["    (nothing)"]
    return "\n".join(out)


print("THE 12 REQUESTS, oldest first:")
for i in range(1, N):
    print(f"  {i:2d}. {NAMES[i]}: {SUBJ[i][:90]}")
print()
for names in (["year-table"], ["event-days"], ["event-days", "event-annotation", "event-exclusion"], ["csv-download"], ["hour-profile"]):
    print(show(names)); print()
