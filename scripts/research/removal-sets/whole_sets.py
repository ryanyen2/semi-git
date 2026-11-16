HERE = __import__("os").path.dirname(__import__("os").path.abspath(__file__))   # this folder
ROOT = __import__("os").path.abspath(__import__("os").path.join(HERE, "..", "..", ".."))   # the repository
import sys
S = HERE
_b5 = open(S + "/demo_b5.py").read()
exec(compile(_b5[:_b5.index("\ndef overlap_text(")], "demo_b5.py", "exec"))
for name in ("hour-profile", "event-days", "csv-download"):
    off = frozenset({NAMES.index(name)})
    R, clean = whole_request_set(off)
    files, kept, kept_h, shown = render_with(ALL - R, set())
    ok, pages = observe(files, "w")
    lost = [p for p in BASE if p not in pages]
    ch = changes(BASE, pages)
    print(f"{name}: with whole requests only, comes out together with {[NAMES[r] for r in sorted(R - off)]}; clean: {clean}; check passes: {ok}; "
          f"pages lost {lost}; other visible changes {len(ch)}")
