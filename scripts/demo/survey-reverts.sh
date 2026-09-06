#!/usr/bin/env bash
# Which reverts are filmable? Try each one on a throwaway clone and report three things:
# does it still compile, does the picture move, and how big is it.
#
#   scripts/demo/survey-reverts.sh [<repo-dir>] [<out-dir>]
#
# A revert is filmable when it compiles clean AND changes pixels. Neither is
# guessable from the diff. A feature can remove a whole file and leave the running
# app identical (nothing on the default sheet used it), and a three-line revert can
# redraw the entire scope (it took a constraint out of the solver). The only way to
# know is to run it and photograph it, which is what this does.
#
# Reverts run on clones, so nothing here touches the demo repo and nothing here mines.
# An authored feature also survives a mine since 0.6.10 (findings 79, 80). A mine
# still re-clusters the machine-named rows, so the clones keep the survey comparable.
set -uo pipefail

repo="$(cd "${1:-$HOME/repos/sgt-demo/sketchpad}" && pwd)"
out="${2:-/tmp/sketchpad-revert-survey}"
SGT="${SGT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/.venv/bin/sgt}"
CHROME="${CHROME:-/Applications/Google Chrome.app/Contents/MacOS/Google Chrome}"
PORT="${PORT:-5399}"

[ -x "$CHROME" ] || { echo "no headless Chrome at $CHROME; set CHROME=<path>" >&2; exit 1; }
[ -d "$repo/node_modules" ] || { echo "$repo has no node_modules" >&2; exit 1; }

# Same guard as check-revert.sh: the wrong sgt previews correctly and then refuses to
# apply, naming files it never touches (finding 84).
PY_BIN="$(dirname "$SGT")/python"
if [ -x "$PY_BIN" ]; then
  kind=$("$PY_BIN" -c "import sgt.core.op as o; print(o._symbol_kind('a.ts::__import__::./b'))" 2>/dev/null)
  [ "$kind" = "import" ] || { echo "wrong sgt: $SGT does not own import lines" >&2; exit 1; }
fi

rm -rf "$out"; mkdir -p "$out"

shoot() {  # <dir> <png>
  ( cd "$1" && ./node_modules/.bin/vite --port "$PORT" --strictPort >/dev/null 2>&1 & )
  for _ in $(seq 1 40); do curl -sf -o /dev/null "http://[::1]:$PORT/" && break; sleep 0.25; done
  "$CHROME" --headless=new --disable-gpu --hide-scrollbars --force-device-scale-factor=1 \
      --window-size=1280,1000 --virtual-time-budget=6000 \
      --screenshot="$2" "http://localhost:$PORT/" >/dev/null 2>&1
  pkill -f "vite --port $PORT" >/dev/null 2>&1 || true
  sleep 0.6
}

# The picture as it stands, to compare every counterfactual against.
base="$out/_base"
git clone -q "$repo" "$base" && cp -r "$repo/.sgt" "$base/.sgt" && ln -s "$repo/node_modules" "$base/node_modules"
shoot "$base" "$out/base.png"
[ -s "$out/base.png" ] || { echo "could not photograph the app as it stands" >&2; exit 1; }

targets_file="$out/targets.txt"
if [ $# -ge 3 ]; then
  printf '%s\n' "${@:3}" > "$targets_file"
else
  # Every feature by name, then every checkpoint of every feature. The checkpoints are
  # read off `log --focus`'s own rows: the map's JSON carries features and cells but not
  # segments, and a survey that invented segment indices would report on refs that do
  # not resolve.
  : > "$targets_file"
  ids=$( cd "$repo" && "$SGT" log --map --json 2>/dev/null | python3 -c "
import json, sys
for fid, f in json.load(sys.stdin)['features'].items():
    print(fid[2:10], f['label'], sep='\t')
" )
  printf '%s\n' "$ids" | cut -f2 >> "$targets_file"
  while IFS=$'\t' read -r fid _; do
    [ -n "$fid" ] || continue
    ( cd "$repo" && "$SGT" log --focus "$fid" 2>/dev/null ) \
      | sed -e 's/\x1b\[[0-9;]*m//g' \
      | sed -n "s/^ *@\([0-9][0-9]*\) .*/$fid@\1/p" >> "$targets_file"
  done <<< "$ids"
fi

printf '%-34s %-7s %-7s %-9s %s\n' target edits tsc picture verdict
n=0
while IFS= read -r target; do
  [ -n "$target" ] || continue
  n=$((n + 1))
  work="$out/t$n"
  git clone -q "$repo" "$work" 2>/dev/null || continue
  cp -r "$repo/.sgt" "$work/.sgt"
  ln -s "$repo/node_modules" "$work/node_modules"
  res=$( cd "$work" && "$SGT" revert "$target" --yes 2>&1 | sed -e 's/\x1b\[[0-9;]*m//g' )
  echo "$res" > "$out/t$n.log"
  echo "$target" > "$out/t$n.target"
  if ! echo "$res" | grep -q 'revert applied'; then
    printf '%-34s %-7s %-7s %-9s %s\n' "${target:0:33}" "-" "-" "-" "did not apply"
    continue
  fi
  edits=$(echo "$res" | sed -n 's/.*removes \([0-9]*\) edits.*/\1/p' | head -1)
  errs=$( cd "$work" && rm -f tsconfig.tsbuildinfo && npx tsc --noEmit 2>&1 | grep -c 'error TS' )
  if [ "$errs" != "0" ]; then
    printf '%-34s %-7s %-7s %-9s %s\n' "${target:0:33}" "${edits:-?}" "$errs err" "-" "BREAKS BUILD"
    continue
  fi
  shoot "$work" "$out/t$n.png"
  if [ ! -s "$out/t$n.png" ]; then
    pic="no render"; verdict="BLANK"
  elif cmp -s "$out/base.png" "$out/t$n.png"; then
    pic="identical"; verdict="invisible"
  else
    pic="MOVED"; verdict="FILMABLE"
  fi
  printf '%-34s %-7s %-7s %-9s %s\n' "${target:0:33}" "${edits:-?}" "0 err" "$pic" "$verdict"
done < "$targets_file"

echo
echo "shots and logs in $out"
