#!/usr/bin/env bash
# Build the starting point the harvested work sessions grow from.
#
#   bootstrap.sh <repo-dir> <prepared-counts.csv> <seed-dir>
#
# A seed dir holds the first commit's tree plus a `.seed-commit-message` file.
# `seed-bikecount` and `seed-footfall` are the two study projects.
#
# Order matters twice here, and both orderings were learned the hard way.
#
# `sgt init` has to run before the first commit, because a session worktree is a
# checkout of the base commit: anything sgt keeps in `.sgt/` that is missing from
# that commit shows up as an untracked file in the worktree, and `sgt session land`
# refuses to land out of a dirty tree.
#
# The seed then has to be *mined* before any session starts. `sgt session start`
# records the base op-ids with `lens.ideal_for_ref` and does not mine first, so a
# base commit nobody has read comes back with an empty op set and the first session
# is credited with every symbol in the repository. Reverting one afternoon's work
# then offers to demolish the codebase (finding 43).
set -euo pipefail
REPO="$1"; DATA="$2"; SEED="$3"
HERE="$(cd "$(dirname "$0")" && pwd)"
SEED_DIR="$HERE/$(basename "$SEED")"
AUTHOR=(-c user.name="Dana Whitfield" -c user.email="dana@example.org")

rm -rf "$REPO"
mkdir -p "$REPO/data"
cp -R "$SEED_DIR"/. "$REPO"/
rm -f "$REPO/.seed-commit-message"
cp "$DATA" "$REPO/data/counts.csv"

cat > "$REPO/.gitignore" <<'EOF'
__pycache__/
*.pyc

# editor and agent tooling, everyone sets these up differently
.claude/
.mcp.json
.vscode/
EOF

cd "$REPO"
git init -q
sgt init >/dev/null
cat > .sgt/oracle.json <<'EOF'
{
  "tiers": [
    {"name": "smoke", "command": "python3 check.py"}
  ]
}
EOF

python3 check.py

git add -A
git "${AUTHOR[@]}" commit -q -F "$SEED_DIR/.seed-commit-message"

sgt log --summary >/dev/null
python3 - <<'PY'
import sys
sys.path.insert(0, "/Users/r4yen/repos/semi-git")
from sgt.core.store import Store
n = len(Store(".").all_ops())
print(f"seed mined: {n} ops")
if n == 0:
    sys.exit("seed was not mined; the first session would claim the whole repo")
PY

git log --oneline
echo
echo "base commit: $(git rev-parse --short HEAD)"
