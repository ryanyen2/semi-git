"""Take out, edit what came back, bring back -- on the Python sgt/ history, judged by ruff plus
importing every module. Same steps as bring_back.py (TypeScript, compiler).
usage: bring_back_py.py <repo> <srcroot> <pkg> <rev> [every-nth-commit]"""
HERE = __import__("os").path.dirname(__import__("os").path.abspath(__file__))   # this folder
ROOT = __import__("os").path.abspath(__import__("os").path.join(HERE, "..", "..", ".."))   # the repository
import os, sys
S = HERE
_ci = open(S + "/check_import.py").read()
exec(compile(_ci[:_ci.index("t1 = time.time()")], "check_import.py", "exec"))
assert not import_all(head, "base"), "the current tree does not import"
_nl = open(S + "/nonlinear.py").read()
exec(compile(_nl[_nl.index("def record_on("):_nl.index("\n\ndef same(")], "nonlinear.py", "exec"))
_bb = open(S + "/bring_back.py").read()
_bb = _bb[_bb.index("def render(off, steps_on, extra=()):"):]
for old, new in (('"  // edited while off\\n"', '"  # edited while off\\n"'), ('"// edited while off"', '"# edited while off"'),
                 ('range(1, len(SUBJ))', 'range(1, len(SUBJ), NTH)'),
                 ('not text[r].lstrip().startswith(("*", "/*", "//"))', 'not text[r].lstrip().startswith(("#", \'"\', "\'"))')):
    assert _bb.count(old) == 1, old
    _bb = _bb.replace(old, new)
exec(compile(_bb, "bring_back.py", "exec"))
