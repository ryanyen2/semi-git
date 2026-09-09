#!/usr/bin/env bash
# Stage a throwaway copy of sketchpad-v3 for one take. The take saves, reverts and
# undoes, so it never runs in the demo repo itself; it runs here.
#
#   scripts/demo/sketchpad-v3/stage.sh [<take-dir>]
#
# Prints the take dir on the first line and starts vite on $PORT (default 5501).
# The demo repo's .sgt is not used: the take gets a fresh copy of the golden .sgt,
# so a previous take's saves can never leak into the next one.
set -euo pipefail
SRC=${SRC:-$HOME/repos/sgt-demo/sketchpad-v3}
GOLDEN=${GOLDEN:-$HOME/repos/sgt-demo/.sgt-golden-v2}
TAKE=${1:-$HOME/repos/sgt-demo/.take-sketchpad-v3}
PORT=${PORT:-5501}
[ -d "$SRC/.git" ] || { echo "no demo repo at $SRC" >&2; exit 1; }
[ -d "$GOLDEN/authored" ] || { echo "no golden .sgt at $GOLDEN" >&2; exit 1; }
[ -d "$SRC/node_modules" ] || { echo "$SRC has no node_modules; npm ci there" >&2; exit 1; }
pkill -f "vite --port $PORT" >/dev/null 2>&1 || true
rm -rf "$TAKE"
git clone -q "$SRC" "$TAKE"
cp -r "$GOLDEN" "$TAKE/.sgt"
ln -s "$SRC/node_modules" "$TAKE/node_modules"
# The take is where Claude Code runs on camera, and a clone carries no agent wiring: without this
# the agent has no `ugh_plan_intake`/`ugh_save` to call and answers the request by shelling out,
# which is both off-script and invisible as a tool call. Safe on a staged take -- it rewrites
# `.sgt/local/sync_cache.json` and nothing else, and the map prints identically afterwards.
UGH=${UGH:-$(command -v ugh || true)}
if [ -n "$UGH" ]; then ( cd "$TAKE" && "$UGH" init --agent >/dev/null 2>&1 || true ); fi
( cd "$TAKE" && ./node_modules/.bin/vite --port "$PORT" --strictPort >/dev/null 2>&1 & )
for _ in $(seq 1 40); do curl -sf -o /dev/null "http://localhost:$PORT/" && break; sleep 0.25; done
echo "$TAKE"
echo "app http://localhost:$PORT/   terminal: cd $TAKE"
