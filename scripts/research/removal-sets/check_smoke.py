"""The one rule on the sgt/ history with the strongest judge available here: ruff, then importing
every module, then actually running the tool (init, status, log, now) on a small scratch project.
usage: check_smoke.py <repo> <srcroot> <pkg> <rev> [every-nth-commit]"""
HERE = __import__("os").path.dirname(__import__("os").path.abspath(__file__))   # this folder
ROOT = __import__("os").path.abspath(__import__("os").path.join(HERE, "..", "..", ".."))   # the repository
import os, re, shutil, subprocess, sys, time
S = HERE
_ci = open(S + "/check_import.py").read()
exec(compile(_ci[:_ci.index("t1 = time.time()")], "check_import.py", "exec"))
two_judges = compile_errors
tpl = tmp / "template"
tpl.mkdir()
GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@example.com"]
subprocess.run(["git", "init", "-q", "."], cwd=tpl, check=True)
(tpl / "m.py").write_text("def a():\n    return 1\n\n\ndef b():\n    return a() + 1\n")
subprocess.run(GIT + ["add", "-A"], cwd=tpl, check=True); subprocess.run(GIT + ["commit", "-q", "-m", "first"], cwd=tpl, check=True)
(tpl / "m.py").write_text("def a():\n    return 2\n\n\ndef b():\n    return a() + 1\n\n\ndef c():\n    return b()\n")
subprocess.run(GIT + ["commit", "-q", "-am", "second"], cwd=tpl, check=True)
RUN = "import sys; from %s.cli import main; sys.argv = ['%s'] + sys.argv[1:]; main()" % (PKG, PKG)
ENV = {k: v for k, v in os.environ.items() if k not in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY")}


def smoke_run(files, name):
    d = tmp / (name + "_run")
    for p in extra:
        q = d / "tree" / p.relative_to(base)
        q.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(p, q)
    for p, t in files.items():
        fp = d / "tree" / p
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(t)
    shutil.copytree(tpl, d / "proj")
    out = []
    for cmd in ("init", "status", "log", "now"):
        r = subprocess.run([sys.executable, "-B", "-c", RUN, cmd], capture_output=True, text=True, cwd=str(d / "proj"),
                           env={**ENV, "PYTHONPATH": str(d / "tree" / SRCROOT) if SRCROOT else str(d / "tree")}, timeout=300)
        if r.returncode != 0 or "Traceback (most recent call last)" in r.stderr:
            frames = [(m.group(1), int(m.group(2))) for m in re.finditer(r'File "([^"]+)", line (\d+)', r.stderr) if str(d / "tree") in m.group(1)]
            msg = f"{PKG} {cmd}: " + (r.stderr.strip().splitlines() or r.stdout.strip().splitlines() or ["failed"])[-1][:100]
            out.append((frames[-1][0].split(str(d / "tree") + "/", 1)[1], frames[-1][1], "run", msg) if frames else ("", 0, "run", msg))
            break
    shutil.rmtree(d, ignore_errors=True)
    return out


def compile_errors(files, name):
    errs, loose = two_judges(files, name)
    if errs or loose:
        return errs, loose
    run = smoke_run(files, name)
    return [e for e in run if e[0]], sum(1 for e in run if not e[0])


t1 = time.time()
b = smoke_run(head, "base")
print(f"running the tool from the current tree: {'ok' if not b else b[0][3]} ({time.time() - t1:.1f}s)", flush=True)
if not b:
    res = []
    for i in ([int(x) for x in os.environ["ONLY"].split(",")] if os.environ.get("ONLY") else range(1, len(SUBJ), NTH)):
        e0, l0 = compile_errors(program(ALL - {i})[0], f"f{i}")
        r = take_out({i}, f"t{i}")
        res.append((i, not e0 and not l0) + r)
        if not r[0]:
            print(f"  commit {i} still fails [{SUBJ[i][:50]}]", flush=True)
    n = len(res)
    print(f"commits tried {n}: with the recorded rule alone all three checks pass in {sum(1 for r in res if r[1])}; after the loop in {sum(1 for r in res if r[2])} "
          f"({100 * sum(1 for r in res if r[2]) // n}%); rounds at most {max(r[3] for r in res)}; commits where an error was explained by no step: {sum(1 for r in res if r[8])}")
    print(f"steps {sum(r[4] for r in res)}: undone {sum(r[5] for r in res)}, kept by the record {sum(r[6] for r in res)}, kept by the checks {sum(r[7] for r in res)}; took {time.time() - t0:.0f}s")
shutil.rmtree(tmp, ignore_errors=True)
