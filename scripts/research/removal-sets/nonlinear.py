"""Edits made while a commit is off, then bringing the commit back.

Test 1 (answer from git, not from this model). For neighbouring commits i and j of a real history:
  A = the tree before i, B = after i, C = after j.
  T = j's change made without i   (git's three-way merge of A and C over B; only pairs where it is clean)
  Turn i off (the program is A), save T as a new step, then:
    saved      the program with i off and the step on equals T
    untouched  the program with i on and the step off still equals B
    back       the program with both on equals git's merge of T and B over A (when git merges cleanly)

Test 2 (edits on code that is only there because a commit is off). On the full history, for each
commit: turn it off, change up to three lines that came back, save that as a step, then:
    saved      the program with the commit off and the step on equals the edited text
    untouched  with the step off, everything is as before
    seen       with both on, every changed line is reported as "written in place of that commit"

usage: nonlinear.py <repo> <srcroot> <pkg> <ext> [rev]     env: PAIRS=<max pairs>, SHOW=1
"""
HERE = __import__("os").path.dirname(__import__("os").path.abspath(__file__))   # this folder
ROOT = __import__("os").path.abspath(__import__("os").path.join(HERE, "..", "..", ".."))   # the repository
import os, subprocess, sys, tempfile, time
from pathlib import Path

S = HERE
REPO, SRCROOT, PKG, EXT = sys.argv[1:5]
REV = sys.argv[5] if len(sys.argv) > 5 else "HEAD"
SRC = open(S + "/weave.py").read().replace('if mode == "scale":', 'if mode == "none":\n    EXPECTED, HEADSTATE = build_incremental()\n    sys.exit(0)\nif mode == "scale":', 1)
CODE = compile(SRC, "weave.py", "exec")
tmp = Path(tempfile.mkdtemp(prefix="nl-"))


def git(*a):
    return subprocess.run(["git", "-C", REPO, *a], capture_output=True, text=True)


VERS = [l for l in git("log", "--first-parent", "--reverse", "--format=%H", REV, "--", SRCROOT + PKG).stdout.split()]


def load(rev):
    os.environ.update(GITLOG="1", PKG=PKG, SRCROOT=SRCROOT, EXT=EXT, REV=rev)
    sys.argv = ["weave.py", "none", REPO]
    g = {"__name__": "__main__"}
    try:
        exec(CODE, g)
    except SystemExit:
        pass
    return g


def files_at(rev):
    out = {}
    for p in git("ls-tree", "-r", "--name-only", rev, "--", SRCROOT + PKG).stdout.split("\n"):
        if p.startswith(SRCROOT + PKG + "/") and p.endswith(tuple(EXT.split(","))):
            t = git("show", f"{rev}:{p}").stdout
            if t.strip():
                out[p] = t
    return out


def merge3(ours, base, theirs):
    for n, t in (("o", ours), ("b", base), ("t", theirs)):
        (tmp / n).write_text(t)
    r = subprocess.run(["git", "merge-file", "-p", str(tmp / "o"), str(tmp / "b"), str(tmp / "t")], capture_output=True, text=True)
    return r.stdout, r.returncode


def merge_tree(ours, base, theirs):
    out, clean = {}, True
    for p in set(ours) | set(base) | set(theirs):
        o, b, t = ours.get(p), base.get(p), theirs.get(p)
        if t == b:
            res = o
        elif o == b or o == t:
            res = t
        elif None in (o, b, t):
            return out, False
        else:
            res, c = merge3(o, b, t)
            if c:
                return out, False
        if res is not None and res.strip():
            out[p] = res
    return out, clean


def record_on(g, on, new_files, k, same_rows=False, state=None):
    """Save `new_files`, written while only the commits in `on` are on, as commit k.
    -> ids of lines the save replaced that an off commit had already replaced, or None when the save
    does something this prototype cannot record (deleting a definition an off commit had deleted)."""
    program, lines, hunks, units, fileweave = (g[x] for x in ("program", "lines", "hunks", "units", "fileweave"))
    split_units, update_lines, update_struct, Unit = (g[x] for x in ("split_units", "update_lines", "update_struct", "Unit"))
    if state is None:
        old_files = program(frozenset(on), keep=False)[0]
        rows = {p: list(g["LAST_MAP"][p]) for p in old_files}
    else:                   # the program the save was made in, when it is not simply "these commits on"
        old_files, rows = state
    in_place = []

    def edit(u, seg, ids):
        if u.died is not None and u.died not in on:
            in_place.append(("definition", u.uid, u.died))   # the save changed a definition that an off commit deletes
        shown = set(ids)
        before = {}
        for ln in u.lines:
            ln.alive = ln.id in shown
            if ln.alive and ln.delh is not None:
                before[ln.id] = ln.delh   # here only because its remover is off
        update_lines(u, seg, k)
        for x, d in before.items():
            if lines[x].delh != d:        # the save replaced it too: keep both removers
                g["ALSO_DEL"].setdefault(x, []).append(lines[x].delh)
                lines[x].delh = d
                in_place.append(x)
        update_struct(u, k, p)
        for ln in u.lines:   # a line whose place in the structure is what it was before this save: the save did not set it
            hist = g["slot_struct"].get(ln.slot)
            if ln.alive and ln.born != k and hist and hist[-1][0] == k:
                prev = next((v for v in reversed(hist[:-1]) if v[0] in on or v[0] == ln.born), None)
                if prev is not None and prev[1:] == hist[-1][1:]:
                    hist.pop()
        for ln in u.lines:   # a line whose text is what it was before this save: the save did not respace it
            if ln.alive and ln.born != k and ln.texts[-1][0] == k:
                prev = next((v for v in reversed(ln.texts[:-1]) if v[0] in on or v[0] == ln.born), None)
                if prev is not None and prev[1] == ln.texts[-1][1]:
                    ln.texts.pop()
        for ln in u.lines:
            ln.alive = ln.delh is None and u.died is None

    for p in sorted(set(old_files) | set(new_files)):
        old, new = old_files.get(p, ""), new_files.get(p, "")
        if old == new:
            continue
        if same_rows:       # the save changed lines in place: each row keeps the definition it is in
            ids = rows[p]
            new_rows = new.splitlines(keepends=True)
            assert len(new_rows) == len(ids)
            r = 0
            while r < len(ids):
                uid = hunks[lines[ids[r]].addh].unit
                e = r
                while e < len(ids) and hunks[lines[ids[e]].addh].unit == uid:
                    e += 1
                if "".join(new_rows[r:e]) != "".join(old.splitlines(keepends=True)[r:e]):
                    edit(units[uid], "".join(new_rows[r:e]), ids)
                r = e
            continue
        try:
            new_segs = split_units(p, new) if new else []
        except (SyntaxError, ValueError):
            new_segs = [("file", new, 1)]
        try:
            old_segs = split_units(p, old) if old else []
        except (SyntaxError, ValueError):
            old_segs = [("file", old, 1)]
        if p.endswith(".py") and new:
            hist = g["indent_hist"].setdefault(p, [])
            width = g["indent_width"](new, hist[-1][1] if hist else 4)
            if not hist or hist[-1][1] != width:
                hist.append((k, width))
        ids = rows.get(p, [])
        # which definition is which: a piece of the new text is the definition it shares the most lines
        # with; a piece that shares none keeps the definition of the same name, if one is left; else it is new
        import difflib
        from collections import Counter
        old_rows, new_rows = old.splitlines(keepends=True), new.splitlines(keepends=True)
        seg_of_old = {}
        owner = []
        for s_, (key, seg, first) in enumerate(old_segs):
            n = len(seg.splitlines(keepends=True))
            for r in range(first - 1, first - 1 + n):
                seg_of_old[r] = s_
            owners = [hunks[lines[x].addh].unit for x in ids[first - 1:first - 1 + n]]
            owner.append(max(set(owners), key=owners.count) if owners else None)
        old_of_new = {}
        for a_, b_, n in difflib.SequenceMatcher(None, [g["ws"](x) for x in old_rows], [g["ws"](x) for x in new_rows], autojunk=False).get_matching_blocks():
            for d in range(n):
                if new_rows[b_ + d].strip():
                    old_of_new[b_ + d] = a_ + d
        votes = []
        for t_, (key, seg, first) in enumerate(new_segs):
            cnt = Counter(seg_of_old[old_of_new[r]] for r in range(first - 1, first - 1 + len(seg.splitlines(keepends=True)))
                          if r in old_of_new and old_of_new[r] in seg_of_old)
            votes += [(n, -t_, s_) for s_, n in cnt.items()]
        pair, taken = {}, set()
        for n, nt, s_ in sorted(votes, reverse=True):
            if -nt not in pair and s_ not in taken and owner[s_] is not None:
                pair[-nt] = s_
                taken.add(s_)
        for t_, (key, seg, first) in enumerate(new_segs):
            if t_ not in pair:
                left = [s_ for s_, (k2, _, _) in enumerate(old_segs) if k2 == key and s_ not in taken and owner[s_] is not None]
                if len(left) == 1:
                    pair[t_] = left[0]
                    taken.add(left[0])
        weave = fileweave.setdefault(p, [])
        last = None
        was = [owner[s_] for s_ in range(len(old_segs)) if s_ in taken]
        now = [owner[pair[t_]] for t_ in range(len(new_segs)) if t_ in pair]
        stay = set()
        for a_, b_, n in difflib.SequenceMatcher(None, was, now, autojunk=False).get_matching_blocks():
            stay.update(was[a_:a_ + n])
        for t_, (key, seg, first) in enumerate(new_segs):
            if t_ in pair:
                u = units[owner[pair[t_]]]
                fresh = old_segs[pair[t_]][1] != seg
                if u.uid not in stay:   # the save moved this definition within the file
                    u.places.append((k, p))
                    weave.insert(weave.index(last) + 1 if last in weave else 0, (u.uid, k))
            else:
                u = Unit(len(units), key, k, p)
                units[u.uid] = u
                weave.insert(weave.index(last) + 1 if last in weave else 0, (u.uid, k))
                fresh = True
            if fresh:
                edit(u, seg, ids)
            place = max(kk for kk, _ in u.places if kk in on or kk == u.born or kk == k)
            last = next((e for e in weave if e[0] == u.uid and e[1] == place), last)
        for s_ in range(len(old_segs)):
            if s_ not in taken and owner[s_] is not None:
                u = units[owner[s_]]
                if u.died is not None:
                    return None
                u.died = k
    return in_place


def same(a, b):
    a = {p: t for p, t in a.items() if t.strip()}
    b = {p: t for p, t in b.items() if t.strip()}
    return a == b


def why(a, b):
    for p in sorted(set(a) | set(b)):
        if a.get(p, "") != b.get(p, ""):
            import difflib
            d = [x for x in difflib.unified_diff(a.get(p, "").splitlines(), b.get(p, "").splitlines(), lineterm="", n=0) if not x.startswith(("---", "+++"))]
            return p.split("/")[-1] + ": " + " | ".join(x[:70] for x in d[:5])
    return ""


t0 = time.time()
# ---- test 1
limit = int(os.environ.get("PAIRS", "40"))
told = 0
tried = saved = untouched = back = back_tried = equals_c = skipped_conflict = skipped_same = cannot = 0
pairs = list(range(1, len(VERS) - 1))
step = max(1, len(pairs) // limit)
for i in ([] if os.environ.get("SKIP1") else [int(x) for x in os.environ["ONLY"].split(",")] if os.environ.get("ONLY") else pairs[::step]):
    A, B, C = files_at(VERS[i - 1]), files_at(VERS[i]), files_at(VERS[i + 1])
    T, clean = merge_tree(A, B, C)
    if not clean:
        skipped_conflict += 1
        continue
    if same(T, A) or same(A, B):
        skipped_same += 1
        continue
    g = load(VERS[i])
    program, ALL = g["program"], g["ALL"]
    if not same(program(ALL - {i}, keep=False)[0], A):
        print(f"  pair {i}: turning the newest commit off does not give the tree before it")
        continue
    k = i + 1
    spots = record_on(g, ALL - {i}, T, k)
    if spots is None:
        cannot += 1
        continue
    tried += 1
    got = program((ALL - {i}) | {k}, keep=False)[0]
    ok1 = same(got, T)
    saved += ok1
    if not ok1 and os.environ.get("SHOW"):
        print(f"  pair {i} saved differs: {why(T, got)}")
        if os.environ.get("ONLY"):
            for u in g["units"].values():
                if any(os.environ.get("NAME", "BlameSpan") in str(kk) for _, kk in u.keys):
                    print(f"   definition {u.uid} names {u.keys} born {u.born} died {u.died} places {u.places}; lines {len(u.lines)}, "
                          f"removed by {sorted({(None if l.delh is None else g['hunks'][l.delh].req) for l in u.lines}, key=str)}; written by {sorted({l.born for l in u.lines})}")
    ok2 = same(program(ALL, keep=False)[0], B)
    untouched += ok2
    M, mclean = merge_tree(T, A, B)
    if spots:
        told += 1
    elif mclean:
        back_tried += 1
        got = program(ALL | {k}, keep=False)[0]
        ok3 = same(got, M)
        back += ok3
        equals_c += same(M, C)
        if not ok3 and os.environ.get("SHOW"):
            print(f"  pair {i} back differs: {why(M, got)}")
            if os.environ.get("ONLY"):
                import difflib
                for ln in g["lines"].values():
                    if any("file_name" in t for _, t in ln.texts) and os.environ.get("FIND"):
                        hk = g["hunks"]
                        print(f"   line {ln.id} unit {hk[ln.addh].unit} [{g['units'][hk[ln.addh].unit].keys[-1][1][:20]}] born {ln.born} removed by {None if ln.delh is None else hk[ln.delh].req} also {[hk[d].req for d in g['ALSO_DEL'].get(ln.id, [])]} texts {[(kk, t.strip()[:40]) for kk, t in ln.texts]}")
                for nm, x, y in (("i: A->B", A, B), ("j: B->C", B, C), ("saved: A->T", A, T)):
                    for p in sorted(set(x) | set(y)):
                        if x.get(p) != y.get(p) and p.endswith(why(M, got).split(":")[0]):
                            d = [l for l in difflib.unified_diff((x.get(p) or "").splitlines(), (y.get(p) or "").splitlines(), lineterm="", n=1)][2:]
                            print(f"   {nm} {p.split('/')[-1]}:"); print("\n".join("      " + l[:120] for l in d[:40]))
print(f"{REPO.split('/')[-1]}/{SRCROOT}{PKG} ({len(VERS)} commits) test 1: pairs tried {tried} "
      f"(left out: {skipped_conflict} where git cannot move the later change, {skipped_same} with nothing to move, {cannot} not recordable)")
print(f"  saved while the commit is off, the program equals what was saved: {saved} of {tried}")
print(f"  with the new step off, the program is as before: {untouched} of {tried}")
print(f"  commit brought back, the program equals git's merge: {back} of {back_tried} (git's merge equals the real later tree in {equals_c}); "
      f"pairs where the save touched something the commit replaces or deletes, reported instead of merged: {told}")

# ---- test 2
g = load(VERS[-1])
program, ALL, lines, hunks, units = (g[x] for x in ("program", "ALL", "lines", "hunks", "units"))
head = program(ALL, keep=False)[0]
mark = "  # edited while off\n" if EXT == ".py" else "  // edited while off\n"
k = len(VERS)
tried = saved = untouched = seen = edited = joined = 0
for i in ([] if os.environ.get("SKIP2") else [int(x) for x in os.environ["T2"].split(",")] if os.environ.get("T2") else range(1, len(VERS))):
    on = ALL - {i}
    cur = program(on, keep=False)[0]
    rows = {p: list(g["LAST_MAP"][p]) for p in cur}
    new, want = {}, []
    for p, ids in rows.items():
        text = cur[p].splitlines(keepends=True)
        new[p] = cur[p]
        if len(text) != len(ids):
            joined += 1
            continue
        for r, x in enumerate(ids):
            ln = lines[x]
            if (len(want) < 3 and ln.delh is not None and hunks[ln.delh].req == i and text[r].strip()
                    and not text[r].rstrip().endswith(("\\", ",", "(", "[", "{", '"""', "'''", "`"))):
                text[r] = text[r].rstrip("\n") + mark
                want.append(x)
        new[p] = "".join(text)
    if not want:
        continue
    spots = record_on(g, on, new, k, same_rows=True)
    if spots is None:
        continue
    tried += 1
    edited += len(want)
    got2 = program(on | {k}, keep=False)[0]
    ok1 = same(got2, new)
    saved += ok1
    if not ok1 and os.environ.get("T2"):
        res_ = program(on | {k}, keep=False)
        kh = res_[2]
        for l in lines.values():
            if any("def _on_audit_request" in t for _, t in l.texts) and l.delh is not None and hunks[l.delh].req == i:
                d = l.delh
                print(f"   the header line {l.id}: written by commit {l.born}, removed by commit {hunks[d].req}; with the edit on, that removal is in effect: {d in kh}"
                      + (f", because the step of commit {hunks[kh[d]].req} was written on top of commit {hunks[d].req}'s work in this definition" if d in kh else ""))
                ss = g["slot_struct"]
                print(f"   its slot {l.slot}; records for that slot (commit, parent slot, indent, lead, is header, is continuation): {ss.get(l.slot)}")
                same = [(x.id, x.born, None if x.delh is None else hunks[x.delh].req, x.texts[-1][1].strip()[:50]) for x in lines.values() if x.slot == l.slot]
                print(f"   lines that share this slot (id, written by, removed by, text): {same}")
                kids = sorted({(v[0], v[4]) for sl, hist in ss.items() for v in hist if v[1] == l.slot and sl != l.slot})[:8]
                print(f"   records of other lines that name this slot as their parent (commit, is header): {kids}")
                mine_ = [h for h in hunks.values() if h.req == k and h.unit == hunks[l.addh].unit]
                for h in mine_:
                    print(f"   the edit's change in that definition replaced lines written by commits {sorted({lines[x].born for x in h.dels})}, which commits {sorted({hunks[lines[x].delh].req for x in h.dels if lines[x].delh is not None})} had removed")
        for p in new:
            if new[p] != got2.get(p):
                nl = new[p].splitlines()
                for n_, t_ in enumerate(nl):
                    if "def _on_audit_request" in t_:
                        print("   what was saved, around the missing line:"); print("\n".join("      " + x[:110] for x in nl[n_ - 2:n_ + 7]))
                        gl = got2[p].splitlines()
                        at = next((m for m, x in enumerate(gl) if x == nl[n_ - 2]), None)
                        print("   the result there:"); print("\n".join("      " + x[:110] for x in gl[at:at + 8]) if at is not None else "      (not found)")
        print("   only in the result:", sorted(set(got2) - set(new)), "only in what was saved:", sorted(set(new) - set(got2)))
        for p in sorted(set(got2) - set(new)):
            us = sorted({hunks[lines[x].addh].unit for x in g["LAST_MAP"][p]})
            print("   ", p, [(units[u].keys, units[u].born, units[u].died, units[u].places) for u in us][:4])
    if not ok1 and os.environ.get("SHOW"):
        print(f"  commit {i} saved differs: {why(new, program(on | {k}, keep=False)[0])}")
    untouched += same(program(ALL, keep=False)[0], head) and same(program(on, keep=False)[0], cur)
    seen += len({x for x in spots if not isinstance(x, tuple)} & set(want))
    k += 1
print(f"test 2: commits with lines that come back {tried}; lines edited {edited}")
print(f"  the program with the commit off and the edit on equals the edited text: {saved} of {tried}")
print(f"  with the edit off, everything is as before: {untouched} of {tried}")
print(f"  edited lines reported as written in place of the off commit: {seen} of {edited}")
print(f"  files left alone because two lines came out joined on one row: {joined}")
print(f"took {time.time() - t0:.0f}s")
