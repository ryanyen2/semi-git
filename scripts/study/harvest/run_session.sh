#!/usr/bin/env bash
# Run one harvested work session.
#
# Starts a scratch worktree, lets the agent do one job in it, then lands the
# result back onto main. Landing is what stamps the session name onto every op
# the session produced, which is what makes `sgt revert --session` exact later.
#
#   run_session.sh <repo> <session-name> <prompt>
set -euo pipefail
REPO="$1"; NAME="$2"; PROMPT="$3"
HERE="$(cd "$(dirname "$0")" && pwd)"

cd "$REPO"
if ! sgt session status 2>/dev/null | grep -q "^  $NAME:"; then
  sgt session start "$NAME" --base main
fi
WORK="$REPO/.sgt/local/sessions/$NAME"

cd "$WORK"
claude -p "$PROMPT" \
  --append-system-prompt "$(cat "$HERE/persona.txt")" \
  --model sonnet 2>&1 | tail -30

cd "$REPO"
sgt session land "$NAME"

# `sgt session land` advances the branch but leaves the main working tree and
# index at the pre-land state, so the files on disk are behind the commit that
# was just made. Everything is committed at this point, so resetting to HEAD is
# safe, and it puts the working tree back in step with what sgt recorded.
git reset --hard -q HEAD
sgt log --summary 2>&1 | grep -E "in sync|differ from the recorded state" || true
