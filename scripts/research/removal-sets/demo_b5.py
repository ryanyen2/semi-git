"""Fifth version. Adds, after round 4: which of a request's lines the project's check actually runs
(so the tool can say what it cannot observe), the state of every other request an option touches,
the combined question to ask when a request is only a base for others, dead code followed to the end,
and for option 3 the overlapping lines and a ready brief. "Feature" is called a removal set."""
HERE = __import__("os").path.dirname(__import__("os").path.abspath(__file__))   # this folder
ROOT = __import__("os").path.abspath(__import__("os").path.join(HERE, "..", "..", ".."))   # the repository
import sys
S = HERE
_b4 = open(S + "/demo_b4.py").read()
exec(compile(_b4[:_b4.index("\ndef show(off_names):")], "demo_b4.py", "exec"))

TRACE = r'''
import sys, json, os
d = os.path.realpath(sys.argv[1]); sys.path.insert(0, d)
hits, cur = {}, ["(loading)"]
def local(frame, event, arg):
    if event == "line":
        hits.setdefault(cur[0], {}).setdefault(os.path.relpath(os.path.realpath(frame.f_code.co_filename), d), set()).add(frame.f_lineno)
    return local
def tracer(frame, event, arg):
    f = os.path.realpath(frame.f_code.co_filename)
    if not f.startswith(d + os.sep):
        return None
    hits.setdefault(cur[0], {}).setdefault(os.path.relpath(f, d), set()).add(frame.f_lineno)
    return local
sys.settrace(tracer)
from bikecount import data, pages, server, window
r = data.load_readings(server.DATA); s, e = window.default(r); w = window.apply(r, s, e)
for m in pages.discover():
    cur[0] = m.PATH
    m.render(w, s, e)
sys.settrace(None)
print(json.dumps({k: {f: sorted(v) for f, v in fs.items()} for k, fs in hits.items()}))
'''
head_files = program(ALL, keep=False)[0]
HEAD_ROWS = {p: list(v) for p, v in LAST_MAP.items() if p in head_files}
_d = work / "trace"
materialize(head_files, _d)
_p = subprocess.run([sys.executable, "-B", "-s", "-E", "-c", TRACE, str(_d)], capture_output=True, text=True, cwd=str(_d.parent), timeout=300)
subprocess.run(["rm", "-rf", str(_d)])
RUN_BY = defaultdict(set)   # line id -> pages whose rendering runs it
for page, fs in (json.loads(_p.stdout.strip().splitlines()[-1]) if _p.stdout.strip() else {}).items():
    if page == "(loading)":
        continue
    for f, nums in fs.items():
        ids = HEAD_ROWS.get(f, [])
        for n in nums:
            if 0 < n <= len(ids):
                RUN_BY[ids[n - 1]].add(page)


def coverage(off):
    own = [l for l in head if l.born in off]
    body = [l for l in own if what(units[unit_of(l.id)])[0] == "function"]
    run = [l for l in body if RUN_BY.get(l.id)]
    pages_ = sorted({p for l in run for p in RUN_BY[l.id]})
    never = sorted({unit_of(l.id) for l in body} - {unit_of(l.id) for l in run})
    return len(body), len(run), pages_, never


def dead_closure(off, held, shown):
    users = defaultdict(set)
    for l in head:
        if l.id in shown:
            for t in l.uses:
                users[t].add(unit_of(l.id))
    mine = {uid for (i, uid) in held if units[uid].born == i and what(units[uid])[0] == "function"}
    dead = {uid for uid in mine if not (users[uid] - {uid})}
    while True:
        more = {uid for uid in mine - dead if users[uid] - {uid} and (users[uid] - {uid}) <= dead}
        if not more:
            return dead
        dead |= more


def whole_request_set(off):
    R = set(off)
    for _ in range(12):
        on = ALL - R
        files, kept, kept_h, shown = render_with(on, set())
        h = holders(frozenset(R), on, steps_of(R), kept, kept_h, shown, set())
        more = {r for rs in h.values() for r, _, _ in rs}
        if not more:
            return R, not h
        R |= more
    return R, False


def overlap_text(off, kept_h, limit=3):
    out = []
    for a, b in kept_h.items():
        if hunks[a].req in off and hunks[b].req not in off:
            u = units[hunks[a].unit]
            was = [l.texts[0][1].strip() for l in u.lines if l.addh == a and l.delh == b][:limit]
            now = [l.texts[-1][1].strip() for l in u.lines if l.addh == b and l.delh is None][:limit]
            if was and now:
                out.append((what(u)[1] + ": " + what(u)[2], NAMES[hunks[b].req], was, now))
    return out[:2]


def show(off_names):
    off = frozenset(NAMES.index(n) for n in off_names)
    on = ALL - off
    steps = steps_of(off)
    own = [l for l in head if l.born in off]
    new_pages, new_lines = introduced(off)
    nb, nr, cov_pages, never = coverage(off)
    out = [f"WHAT WOULD IT TAKE TO MAKE {', '.join(off_names)} GONE?"]
    got = []
    if new_pages:
        got.append("new page(s) " + ", ".join(sorted(new_pages)))
    if new_lines:
        got.append(f"{len(new_lines)} new line(s) on the pages, for example " + "; ".join(f'"{t[:70]}"' for t in sorted(new_lines, key=len)[:2]))
    out.append("  What it showed when it was added: " + ("; ".join(got) if got else "nothing on any page; it only added code that later requests use") + ".")
    obs = f"  What the check can observe of it: rendering the pages runs {nr} of the {nb} lines it has inside functions" + (f" (pages {', '.join(cov_pages)})" if cov_pages else "") + "."
    if never:
        obs += f" NEVER RUN by the check: {fold(never)}. Whatever those do cannot be confirmed here."
    out.append(obs)
    files, kept, kept_h, shown = render_with(on, set())
    ok1, pages1 = observe(files, "o1")
    held = holders(off, on, steps, kept, kept_h, shown, set())
    gone1 = [l for l in own if l.id not in shown]
    dead = dead_closure(off, held, shown)
    g1 = gone_for_visitor(off, pages1, new_pages, new_lines)
    label = lambda g: g if not never or g.startswith("it never") else g + ", as far as the pages show; the code the check never runs is not confirmed"
    ch1 = changes(BASE, pages1)
    lost1 = [p for p in BASE if p not in pages1]
    out.append(f"  OPTION 1, take out only what nothing else stands on.   Gone from the pages: {label(g1)}")
    vis1 = ([f"page {', '.join(lost1)} no longer there"] if lost1 else []) + say(ch1, 6)
    out.append("    the pages: " + ("; ".join(vis1) if vis1 else "no difference at all"))
    out.append(f"    the code: {len(gone1)} of its {len(own)} lines go" + (f"; left behind and reached by nothing: {fold(dead)}" if dead else ""))
    if not held:
        out.append("  Nothing else stands on it, so there is nothing more to weigh. RECOMMENDED: option 1.")
        return "\n".join(out)
    by = defaultdict(lambda: defaultdict(set))
    for s_, rs in held.items():
        for r, how, _ in rs:
            by[r][how].add(s_[1])
    out.append("    held by: " + ", ".join(f"{NAMES[r]} ({how})" for r in sorted(by) for how in sorted(by[r])))
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
    ok2, pages2 = observe(f2, "o2")
    g2 = gone_for_visitor(off, pages2, new_pages, new_lines)
    ch2 = changes(BASE, pages2)
    lost2 = [p for p in BASE if p not in pages2]
    per = defaultdict(set)
    for r, uid in extra:
        per[r].add(uid)
    same = (lost1 == lost2 and sorted(map(str, ch1)) == sorted(map(str, ch2)))
    out.append(f"  OPTION 2, take out the removal set (the request and everything that stands on it).   Gone from the pages: {label(g2)}")
    out.append("    what happens to the other requests:")
    for r in sorted(per):
        total = len(steps_of({r}))
        np_, nl_ = introduced({r})
        if len(per[r]) == total:
            state = "GONE entirely"
        else:
            gr = gone_for_visitor({r}, pages2, np_, nl_)
            state = f"PARTLY removed ({len(per[r])} of its {total} steps); of what it showed on the pages: " + (
                "all still shown" if gr == "NO" else "none still shown" if gr == "YES" else gr.replace("PARTLY (", "").rstrip(")") if gr.startswith("PARTLY") else "it showed nothing by itself")
        out.append(f"      {NAMES[r]} (\"{SUBJ[r][:50]}\"): {state}; loses its changes in {fold(per[r])}")
    vis2 = ([f"page {', '.join(lost2)} no longer there"] if lost2 else []) + say(ch2, 6)
    out.append("    the pages: " + ("exactly what option 1 shows, nothing more" if same else "; ".join(vis2) if vis2 else "no difference at all") + ("" if ok2 else "  [the check FAILS]"))
    left = sum(1 for l in own if l.id in sh2)
    out.append(f"    the code: {len(own) - left} of its {len(own)} lines go; nothing is left that nothing reaches")
    if any(units[uid].places[0][1].endswith("check.py") for _, uid in extra):
        out.append("    WARNING: this option changes check.py, the project's check itself. It needs your explicit confirmation.")
    keepers = sorted({NAMES[r] for rs in held.values() for r, _, _ in rs})
    out.append("  OPTION 3, remove what is held by hand or with your coding agent (a new save).")
    out.append(f"    where: {fold([s_[1] for s_ in held])}  ({len(own) - len(gone1)} lines of this request are there)")
    for place, who, was, now in overlap_text(off, kept_h):
        out.append(f"    the lines both wrote, in {place}: this request wrote  {' | '.join(was)[:150]}")
        out.append(f"                                         {who} made them  {' | '.join(now)[:150]}")
    want = "; ".join(f'"{t[:60]}"' for t in sorted(new_lines, key=len)[:2])
    out.append(f"    brief for the agent: remove \"{'; '.join(SUBJ[i][:60] for i in sorted(off))}\" from the places above; keep {', '.join(keepers)} working; "
               + (f"afterwards the pages must no longer show {want} and must otherwise be unchanged. " if new_lines else "the pages must be unchanged. ")
               + "The tool reruns this same comparison on the agent's save.")
    R, clean = whole_request_set(off)
    if same:
        rec = "option 2. The pages end up exactly as with option 1, and no code is left that nothing reaches."
    elif g1 == "YES":
        rec = "option 1. It is already gone from the pages; option 2 would change more of what the pages show."
    elif g1.startswith("it never"):
        rec = ("none of these. This request shows nothing by itself; it is a base that other requests use. Ask instead about "
               + ", ".join(NAMES[r] for r in sorted(R)) + " together" + (": that set comes out cleanly." if clean else "."))
    else:
        rec = "option 3. Option 1 leaves it on the pages, and option 2 would also change other things the pages show."
    out.append("  RECOMMENDED: " + rec)
    return "\n".join(out)


print("THE 12 REQUESTS, oldest first:")
for i in range(1, N):
    print(f"  {i:2d}. {NAMES[i]}: {SUBJ[i][:90]}")
print(f"\nWith everything in, the project's check (check.py, which renders every page) passes; the pages are: {', '.join(BASE)}")
print("The tool observes the project only through that check: the text of those pages, and which lines of code rendering them runs.\n")
for names in (["year-table"], ["csv-download"], ["event-days"], ["event-days", "event-annotation", "event-exclusion"], ["hour-profile"]):
    print(show(names)); print()
