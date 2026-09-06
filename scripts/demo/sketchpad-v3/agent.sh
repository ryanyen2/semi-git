#!/usr/bin/env bash
# The agent's step of the take, scripted: state the plan, make the edit, typecheck, save.
# On camera the same three moves come from Claude Code over sgt's MCP (sgt_plan_intake,
# the edit, sgt_save) started in the take dir; this is the rehearsal stand-in and what
# check-v3.sh verifies. The request the agent is answering is request.txt.
#
#   scripts/demo/sketchpad-v3/agent.sh <take-dir>
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
take=$(cd "${1:?take dir}" && pwd)
SGT=${SGT:-$(cd "$here/../../.." && pwd)/.venv/bin/sgt}
cd "$take"
"$SGT" plan intake "$(cat "$here/plan.txt")" </dev/null
git apply "$here/agent-seam.patch"
rm -f tsconfig.tsbuildinfo
npx tsc --noEmit
"$SGT" save -m "set the fastened hexagon groups apart by a seam" </dev/null
