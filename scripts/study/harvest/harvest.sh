#!/usr/bin/env bash
# Run every job in a roles file through its own work session, in order.
#
#   harvest.sh <repo-dir> <roles.json>
#
# The roles file is required. It used to default to `roles.json`, a single-project
# file from before the study had two, and running the command with no second
# argument then built the wrong history in silence.
#
# Each job gets a fresh agent that has never seen the others. It reads whatever
# the previous jobs left behind, does its one thing, and saves. Nothing tells it
# what any of this is for.
set -euo pipefail
REPO="$1"
HERE="$(cd "$(dirname "$0")" && pwd)"
ROLES="${2:-}"
[ -n "$ROLES" ] || {
    echo "usage: harvest.sh <repo-dir> <roles.json>" >&2
    echo "  the two study projects are roles-bikecount.json and roles-footfall.json" >&2
    exit 2
}

count=$(python3 -c "import json,sys;print(len(json.load(open(sys.argv[1]))))" "$ROLES")

for i in $(seq 0 $((count - 1))); do
  name=$(python3 -c "import json,sys;print(json.load(open(sys.argv[1]))[int(sys.argv[2])]['name'])" "$ROLES" "$i")
  prompt=$(python3 -c "import json,sys;print(json.load(open(sys.argv[1]))[int(sys.argv[2])]['prompt'])" "$ROLES" "$i")

  echo
  echo "════════ $((i + 1))/$count  $name ════════"

  # Skip jobs that already landed, so a run interrupted halfway can be restarted
  # without redoing the sessions that finished or, worse, landing a second copy of
  # one under the same name. Attribution outlives the session record, so the ops
  # are the thing to ask.
  if python3 - "$REPO" "$name" <<'PY'
import sys
sys.path.insert(0, "/Users/r4yen/repos/semi-git")
from sgt.core.session import ops_by_session
sys.exit(0 if ops_by_session(sys.argv[1], sys.argv[2]) else 1)
PY
  then
    echo "── already landed, skipping: $name"
    continue
  fi

  if bash "$HERE/run_session.sh" "$REPO" "$name" "$prompt"; then
    echo "── landed: $name"
  else
    echo "── FAILED: $name (left in place, look at it before re-running)"
    exit 1
  fi
done

echo
echo "════════ done ════════"
cd "$REPO" && git log --oneline | head -30
