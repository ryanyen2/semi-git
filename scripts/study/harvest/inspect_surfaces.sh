#!/usr/bin/env bash
# Dump every surface a participant would actually read, into one file.
#
#   inspect_surfaces.sh <repo> [session-name]
#
# The point is to judge legibility, not correctness. Read the output and ask, for
# each block: could someone who has never seen this repo tell what the work was,
# which piece of it to undo, and what undoing it would cost? If a block does not
# answer that, it is the block that needs fixing, not the participant.
set -uo pipefail
REPO="$1"
TARGET="${2:-}"

cd "$REPO"

rule() { printf '\n\n════════════════════ %s ════════════════════\n\n' "$1"; }

rule "git log --oneline   (what the git arm opens with)"
git log --oneline | head -40

rule "git log --stat, most recent piece of work"
git log --stat -1 --format="%s%n%n%b" | head -30

rule "ugh now   (where am I, what next)"
ugh now 2>&1 | head -40

rule "ugh log   (saved work, newest first)"
ugh log 2>&1 | head -50

rule "ugh log --tree   (the features, and their handles)"
ugh log --tree 2>&1 | head -50

rule "ugh log --map   (feature lanes over time)"
ugh log --map 2>&1 | head -60

rule "ugh log --summary   (what needs attention)"
ugh log --summary 2>&1 | head -30

if [ -n "$TARGET" ]; then
  rule "ugh show $TARGET"
  ugh show "$TARGET" 2>&1 | head -40

  rule "ugh revert --session $TARGET   (the preview, nothing applied)"
  ugh revert --session "$TARGET" 2>&1 | head -40
fi

rule "sessions that landed work"
python3 - <<'PY'
import sys
sys.path.insert(0, "/Users/r4yen/repos/semi-git")
from collections import Counter
from ugh.core.store import Store
c = Counter()
for op in Store(".").all_ops():
    for a in op.attribution:
        if a.session:
            c[a.session] += 1
for name, n in c.most_common():
    print(f"  {name}: {n} ops")
PY
