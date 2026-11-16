"""When the removal set of one request takes steps of other requests with it, are those steps inside
definitions the request itself created (they go with the thing being removed), or elsewhere (the
other request is taken apart)?   usage: collateral.py <bikecount|footfall>"""
HERE = __import__("os").path.dirname(__import__("os").path.abspath(__file__))   # this folder
ROOT = __import__("os").path.abspath(__import__("os").path.join(HERE, "..", "..", ".."))   # the repository
import sys
S = HERE
sys.argv = ["x", sys.argv[1]]
exec(compile(open(S + "/check_py.py").read().split('if __name__ == "__main__":')[0], "check_py.py", "exec"))
closure = g["closure"]
rows = []
for X in range(1, N):
    F = closure(frozenset(h.id for h in hunks.values() if h.req == X))
    for _ in range(20):
        touched = {(hunks[x].req, hunks[x].unit) for x in F}
        whole = frozenset(h.id for h in hunks.values() if (h.req, h.unit) in touched)
        if whole <= F:
            break
        F = closure(whole)
    extra = {(hunks[x].req, hunks[x].unit) for x in F if hunks[x].req != X}
    inside = {s for s in extra if units[s[1]].born == X}
    # a step of another request in a definition that request created only to serve X's code counts as elsewhere
    elsewhere = extra - inside
    others = {r for r, _ in extra}
    apart = {r for r, _ in elsewhere}
    whole_gone = {r for r in others if {s for s in steps_of({r})} <= extra}
    rows.append((NAMES[X], len(extra), len(inside), len(elsewhere), len(others), len(apart - whole_gone), len(whole_gone)))
print(f"{PKG}: removal set of each request taken alone")
alone = sum(1 for r in rows if r[1] == 0)
only_inside = sum(1 for r in rows if r[1] and not r[3])
print(f"  takes nothing of other requests: {alone} of {len(rows)}")
print(f"  takes only other requests' changes inside definitions it created: {only_inside} of {len(rows)}")
print(f"  also takes other requests' steps elsewhere: {len(rows) - alone - only_inside} of {len(rows)}")
print(f"  steps of other requests taken, over all: {sum(r[1] for r in rows)}; inside its own definitions {sum(r[2] for r in rows)}; elsewhere {sum(r[3] for r in rows)}")
for r in rows:
    if r[3]:
        print(f"    {r[0]}: {r[3]} steps elsewhere, {r[5]} other request(s) left taken apart, {r[6]} gone whole")
