"""Fourth version: the question is "what would it take to make X gone", answered with three checked
options side by side, each saying first whether a visitor still sees X, and a recommendation with
its reason. Real prototype output on bikecount (default B)."""
HERE = __import__("os").path.dirname(__import__("os").path.abspath(__file__))   # this folder
ROOT = __import__("os").path.abspath(__import__("os").path.join(HERE, "..", "..", ".."))   # the repository
import sys
S = HERE
_b3 = open(S + "/demo_b3.py").read()
exec(compile(_b3[:_b3.index("def show(off_names):")], "demo_b3.py", "exec"))
from collections import Counter


def page_lines(pages):
    return {p: [t for t in (text_of(x) for x in html.splitlines()) if t] for p, html in pages.items()}


def changes(before, after):
    """[(pages, old text or None, new text or None)] with the same change on several pages merged"""
    seen = defaultdict(list)
    for p in before:
        if p not in after:
            continue
        a, b = before[p].splitlines(), after[p].splitlines()
        sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag == "equal":
                continue
            old, new = [text_of(x) for x in a[i1:i2]], [text_of(x) for x in b[j1:j2]]
            old, new = [x for x in old if x], [x for x in new if x]
            for k in range(max(len(old), len(new))):
                o = old[k] if k < len(old) else None
                n = new[k] if k < len(new) else None
                if o != n:
                    seen[(o, n)].append(p)
    return [(ps, o, n) for (o, n), ps in seen.items()]


def say(ch, limit=3):
    out = []
    for ps, o, n in ch[:limit]:
        where = "on every page" if len(set(ps)) >= len(BASE) - 1 else "on " + ", ".join(sorted(set(ps)))
        cut = lambda t: t if len(t) <= 100 else t[:97] + "..."
        if o and n:
            k = next((i for i, (x, y) in enumerate(zip(o, n)) if x != y), min(len(o), len(n)))
            a0 = max(0, k - 25)   # show the part that differs, not the common beginning
            win = lambda t: ("..." if a0 else "") + t[a0:a0 + 80] + ("..." if len(t) > a0 + 80 else "")
            out.append(f'{where}: "{win(o)}" becomes "{win(n)}"')
        elif o:
            out.append(f'{where}: "{cut(o)}" disappears')
        else:
            out.append(f'{where}: "{cut(n)}" appears')
    if len(ch) > limit:
        out.append(f"and {len(ch) - limit} more change(s) in what the pages show")
    return out


_obs = {}


def obs_at(k):
    if k not in _obs:
        _obs[k] = observe(program(frozenset(range(k + 1)), keep=False)[0], f"h{k}")[1]
    return _obs[k]


def introduced(off):
    """what a visitor got when these requests were added: pages that appeared, lines that appeared"""
    new_pages, new_lines = set(), set()
    for X in sorted(off):
        b, a = obs_at(X - 1), obs_at(X)
        new_pages |= set(a) - set(b)
        bl, al = page_lines(b), page_lines(a)
        for p in a:
            if p in b:
                gained = Counter(al[p]) - Counter(bl[p])
                new_lines |= set(gained)
    return new_pages, new_lines


def gone_for_visitor(off, pages, new_pages, new_lines):
    if not new_pages and not new_lines:
        return "it never showed a visitor anything by itself"
    still_pages = [p for p in new_pages if p in pages]
    shown_now = set(t for ls in page_lines(pages).values() for t in ls)
    still_lines = [t for t in new_lines if t in shown_now]
    if not still_pages and not still_lines:
        return "YES"
    if len(still_pages) == len(new_pages) and len(still_lines) == len(new_lines):
        return "NO"
    return f"PARTLY ({len(new_lines) - len(still_lines)} of the {len(new_lines)} things it added to the pages are gone" + (f", page {', '.join(still_pages)} is still there" if still_pages else "") + ")"


def show(off_names):
    off = frozenset(NAMES.index(n) for n in off_names)
    on = ALL - off
    steps = steps_of(off)
    own = [l for l in head if l.born in off]
    new_pages, new_lines = introduced(off)
    out = [f"WHAT WOULD IT TAKE TO MAKE {', '.join(off_names)} GONE?"]
    got = []
    if new_pages:
        got.append("new page(s) " + ", ".join(sorted(new_pages)))
    if new_lines:
        ex = sorted(new_lines, key=len)[:2]
        got.append(f"{len(new_lines)} new line(s) of output, for example " + "; ".join(f'"{t[:70]}"' for t in ex))
    out.append("  When it was added, a visitor got: " + ("; ".join(got) if got else "nothing visible; it only added code that later requests use") + ".")
    # option 1
    files, kept, kept_h, shown = render_with(on, set())
    ok1, pages1 = observe(files, "o1")
    held = holders(off, on, steps, kept, kept_h, shown, set())
    gone1 = [l for l in own if l.id not in shown]
    used = {t for l in head if l.id in shown for t in l.uses}
    unused = [uid for (i, uid) in held if units[uid].born == i and what(units[uid])[0] == "function" and uid not in used]
    g1 = gone_for_visitor(off, pages1, new_pages, new_lines)
    ch1 = changes(BASE, pages1)
    lost1 = [p for p in BASE if p not in pages1]
    out.append(f"  OPTION 1, take out only what nothing else stands on.   Gone for a visitor: {g1}")
    vis1 = ([f"page {', '.join(lost1)} no longer there"] if lost1 else []) + say(ch1)
    out.append("    a visitor sees: " + ("; ".join(vis1) if vis1 else "no difference at all"))
    out.append(f"    in the code: {len(gone1)} of its {len(own)} lines go" + (f"; left behind and called by nothing: {fold(unused)}" if unused else ""))
    if held:
        by = defaultdict(lambda: defaultdict(set))
        for s_, rs in held.items():
            for r, how, _ in rs:
                by[r][how].add(s_[1])
        hs = [f"{NAMES[r]} ({how})" for r in sorted(by) for how in sorted(by[r])]
        out.append("    held by: " + ", ".join(hs))
    if not held:
        out.append("  Nothing else stands on it, so there is no other option to weigh. RECOMMENDED: option 1.")
        return "\n".join(out)
    # option 2
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
    out.append(f"  OPTION 2, take out the complete set (everything that stands on it goes too).   Gone for a visitor: {g2}")
    names = []
    for r in sorted(per):
        total = len(steps_of({r}))
        names.append(f"{NAMES[r]} ({'all of it' if len(per[r]) == total else f'{len(per[r])} of its {total} steps'}: {fold(per[r])})")
    out.append("    also comes out: " + "; ".join(names))
    extra_vis = [x for x in ([f"page {', '.join(lost2)} no longer there"] if lost2 else []) + say(ch2, 4)]
    same = (lost1 == lost2 and sorted(map(str, ch1)) == sorted(map(str, ch2)))
    out.append("    a visitor sees: " + ("exactly what option 1 shows, nothing more" if same else "; ".join(extra_vis) if extra_vis else "no difference at all")
               + ("" if ok2 else "  [the check FAILS]"))
    left = sum(1 for l in own if l.id in sh2)
    out.append(f"    in the code: {len(own) - left} of its {len(own)} lines go, and no code is left that nothing calls")
    if any(units[uid].places[0][1].endswith("check.py") for _, uid in extra):
        out.append("    WARNING: this option changes check.py, the project's check itself. Confirm before using it.")
    # option 3
    places = fold([s_[1] for s_ in held])
    keepers = sorted({NAMES[r] for rs in held.values() for r, _, _ in rs})
    out.append("  OPTION 3, remove what is held by hand or with your coding agent (a new save).")
    out.append(f"    where: {places}")
    out.append(f"    must keep working: {', '.join(keepers)}")
    # recommendation
    if same:
        rec = "option 2. A visitor sees the same as with option 1, and it leaves no code behind that nothing calls."
    elif g1 == "YES":
        rec = "option 1. It is already gone for a visitor; option 2 would change more of what a visitor sees."
    elif g1.startswith("it never"):
        rec = "none by default. This request shows nothing by itself; it is a base that other requests use. Taking it out means option 2, which changes what a visitor sees as listed."
    else:
        rec = "option 3. Option 1 leaves it visible, and option 2 would also change other things a visitor sees."
    out.append("  RECOMMENDED: " + rec)
    return "\n".join(out)


print("THE 12 REQUESTS, oldest first:")
for i in range(1, N):
    print(f"  {i:2d}. {NAMES[i]}: {SUBJ[i][:90]}")
print(f"\nWith everything in, the project's check (check.py, which renders every page) passes; the pages are: {', '.join(BASE)}")
print("What the tool can observe here is the output of those pages. Anything else (other addresses such as a download, files written) it cannot see.\n")
for names in (["year-table"], ["csv-download"], ["event-days"], ["event-days", "event-annotation", "event-exclusion"], ["event-annotation"], ["hour-profile"]):
    print(show(names)); print()
