"""The one rule on the sgt/ history with a stronger judge: ruff, and then actually importing every
module of the package (a failure is located by the innermost traceback frame inside the package)."""
HERE = __import__("os").path.dirname(__import__("os").path.abspath(__file__))   # this folder
ROOT = __import__("os").path.abspath(__import__("os").path.join(HERE, "..", "..", ".."))   # the repository
import os, re, shutil, subprocess, sys, time
from pathlib import Path
S = HERE
_src = open(S + "/check_ruff.py").read()
exec(compile(_src[:_src.index("res = []")], "check_ruff.py", "exec"))
ruff_errors = compile_errors
base = tmp / "basetree"
base.mkdir()
subprocess.run(f"git -C {REPO} archive {REV} {SRCROOT}{PKG} | tar -x -C {base}", shell=True, check=True)
extra = [p for p in base.rglob("*") if p.is_file() and p.suffix != ".py"]
IMPORT_ALL = ("import importlib, pkgutil, sys; sys.path.insert(0, '.'); import %s as P; "
              "[importlib.import_module(m.name) for m in pkgutil.walk_packages(P.__path__, P.__name__ + '.')]" % PKG)


def import_all(files, name):
    d = tmp / (name + "_imp")
    for p in extra:
        q = d / p.relative_to(base)
        q.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(p, q)
    for p, t in files.items():
        fp = d / p
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(t)
    r = subprocess.run([os.environ.get("IMPORT_PY", sys.executable), "-B", "-c", IMPORT_ALL], capture_output=True, text=True,
                       cwd=str(d / SRCROOT) if SRCROOT else str(d), timeout=300,
                       env={k: v for k, v in os.environ.items() if k not in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "PYTHONPATH")})
    out = []
    if r.returncode != 0:
        frames = [(m.group(1), int(m.group(2))) for m in re.finditer(r'File "([^"]+)", line (\d+)', r.stderr) if str(d) in m.group(1)]
        msg = (r.stderr.strip().splitlines() or ["failed"])[-1][:120]
        if frames:
            f, row = frames[-1]
            out.append((f.split(str(d) + "/", 1)[1], row, "import", msg))
        else:
            out.append(("", 0, "import", msg))
    shutil.rmtree(d, ignore_errors=True)
    return out


def compile_errors(files, name):
    errs, loose = ruff_errors(files, name)
    if errs:
        return errs, loose
    imp = import_all(files, name)
    return [e for e in imp if e[0]], sum(1 for e in imp if not e[0])


t1 = time.time()
b = import_all(head, "base")
print(f"importing every module of the current tree: {'ok' if not b else b[0][3]} ({time.time() - t1:.1f}s)", flush=True)
if not b:
    res = []
    for i in ([int(x) for x in os.environ["ONLY"].split(",")] if os.environ.get("ONLY") else range(1, len(SUBJ), NTH)):
        files0 = program(ALL - {i})[0]
        e0, l0 = compile_errors(files0, f"f{i}")
        r = take_out({i}, f"t{i}")
        res.append((i, not e0 and not l0) + r)
        if not r[0]:
            print(f"  commit {i} still fails [{SUBJ[i][:50]}]", flush=True)
    n = len(res)
    print(f"commits tried {n}: with the recorded rule alone both checks pass in {sum(1 for r in res if r[1])}; after the loop in {sum(1 for r in res if r[2])} "
          f"({100 * sum(1 for r in res if r[2]) // n}%); rounds at most {max(r[3] for r in res)}; commits where an error was explained by no step: {sum(1 for r in res if r[8])}")
    print(f"steps {sum(r[4] for r in res)}: undone {sum(r[5] for r in res)}, kept by the record {sum(r[6] for r in res)}, kept by the checks {sum(r[7] for r in res)}; took {time.time() - t0:.0f}s")
shutil.rmtree(tmp, ignore_errors=True)
