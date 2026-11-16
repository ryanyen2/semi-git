"""How often does a commit leave a Python file that does not parse?"""
import ast, subprocess, sys
repo, path, rev = sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else "HEAD"
git = lambda *a: subprocess.run(["git", "-C", repo, *a], capture_output=True, text=True, errors="replace").stdout
vers = git("log", "--first-parent", "--reverse", "--format=%H", rev, "--", path).split()
bad = files = 0
commits = set()
for k in range(1, len(vers)):
    for row in git("diff-tree", "-r", "--name-status", "--no-renames", vers[k - 1], vers[k], "--", path).splitlines():
        st, p = row.split("\t", 1)
        if st == "D" or not p.endswith(".py"):
            continue
        files += 1
        try:
            ast.parse(git("show", f"{vers[k]}:{p}"))
        except (SyntaxError, ValueError):
            bad += 1
            commits.add(k)
print(f"{repo.split('/')[-1]}/{path}: {len(vers)} commits, {files} changed-file states, {bad} do not parse, in {len(commits)} commits")
