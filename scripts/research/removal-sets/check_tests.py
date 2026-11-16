"""Tests are steps too. Track the package together with two of its test folders; taking a commit out
also undoes the tests it added. Judge: ruff, importing every module, running the tool, and then the
tests that remain (tests/entities and tests/store).
usage: ALSO=tests/entities,tests/store check_tests.py <repo> "" sgt <rev> [every-nth-commit]"""
HERE = __import__("os").path.dirname(__import__("os").path.abspath(__file__))   # this folder
ROOT = __import__("os").path.abspath(__import__("os").path.join(HERE, "..", "..", ".."))   # the repository
import os, re, shutil, subprocess, sys, time
S = HERE
_cs = open(S + "/check_smoke.py").read()
exec(compile(_cs[:_cs.index("\nt1 = time.time()\n")], "check_smoke.py", "exec"))
three_judges = compile_errors
ALSO = [x for x in os.environ["ALSO"].split(",") if x]
subprocess.run(f"git -C {REPO} archive {REV} tests/__init__.py tests/conftest.py {' '.join(ALSO)} | tar -x -C {base}", shell=True, check=True)
extra = [p for p in base.rglob("*") if p.is_file() and (p.suffix != ".py" or str(p.relative_to(base)) in ("tests/__init__.py", "tests/conftest.py"))]


def tests_run(files, name):
    d = tmp / (name + "_tst")
    for p in extra:
        q = d / p.relative_to(base)
        q.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(p, q)
    for p, t in files.items():
        fp = d / p
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(t)
    dirs = [x for x in ALSO if (d / x).exists()]
    out = []
    if dirs:
        r = subprocess.run([sys.executable, "-B", "-m", "pytest", *dirs, "-q", "-x", "--tb=short", "-p", "no:cacheprovider", "-o", "addopts="],
                           capture_output=True, text=True, cwd=str(d), env={**ENV, "PYTHONPATH": str(d)}, timeout=600)
        if r.returncode not in (0, 5):
            frames = re.findall(r"^((?:%s|tests)/\S+?\.py):(\d+): " % PKG, r.stdout, re.M)
            msg = "tests: " + next((l for l in reversed(r.stdout.strip().splitlines()) if l.startswith(("E ", "FAILED", "ERROR"))), "failed")[:110]
            out.append((frames[-1][0], int(frames[-1][1]), "test", msg) if frames else ("", 0, "test", msg))
    shutil.rmtree(d, ignore_errors=True)
    return out


def compile_errors(files, name):
    errs, loose = three_judges(files, name)
    if errs or loose:
        return errs, loose
    t = tests_run(files, name)
    return [e for e in t if e[0]], sum(1 for e in t if not e[0])


t1 = time.time()
b = tests_run(head, "base")
in_tests = lambda uid: units[uid].places[0][1].startswith("tests/")
print(f"the tests of the current tree: {'pass' if not b else b[0][3]} ({time.time() - t1:.1f}s); tracked files {len(head)}, of which tests {sum(1 for p in head if p.startswith('tests/'))}", flush=True)
if not b:
    res = []
    todo = [int(x) for x in os.environ["ONLY"].split(",")] if os.environ.get("ONLY") else range(1, len(SUBJ), NTH)
    if os.environ.get("TESTS_ONLY"):   # only the commits that touch the tracked tests
        todo = [i for i in range(1, len(SUBJ)) if any(in_tests(s_[1]) for s_ in steps_of({i}))]
        print("commits that touch the tracked tests:", todo, flush=True)
    for i in todo:
        e0, l0 = compile_errors(program(ALL - {i})[0], f"f{i}")
        r = take_out({i}, f"t{i}")
        mine = steps_of({i})
        res.append((i, not e0 and not l0) + r + (sum(1 for s_ in mine if in_tests(s_[1])),))
        print(f"  commit {i}: {'ok' if r[0] else 'STILL FAILS'}; rule alone passes: {not e0 and not l0}; rounds {r[1]}; steps {r[2]} (in tests {res[-1][-1]}), "
              f"undone {r[3]}, kept by the checks {r[5]} [{SUBJ[i][:40]}] {time.time() - t0:.0f}s", flush=True)
    n = len(res)
    print(f"commits tried {n}: with the recorded rule alone all four checks pass in {sum(1 for r in res if r[1])}; after the loop in {sum(1 for r in res if r[2])} "
          f"({100 * sum(1 for r in res if r[2]) // n}%); rounds at most {max(r[3] for r in res)}; commits where an error was explained by no step: {sum(1 for r in res if r[8])}")
    print(f"steps {sum(r[4] for r in res)} (of which in test files {sum(r[-1] for r in res)}): undone {sum(r[5] for r in res)}, kept by the record {sum(r[6] for r in res)}, "
          f"kept by the checks {sum(r[7] for r in res)}; commits that touch the tracked tests {sum(1 for r in res if r[-1])}; took {time.time() - t0:.0f}s")
shutil.rmtree(tmp, ignore_errors=True)
