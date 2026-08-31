#!/usr/bin/env bash
# Which "revert to here" lands? For every feature and every checkpoint in it, rewind that ONE
# feature to that checkpoint -- peeling its later chapters off the tip, newest first, which is what
# the workbench's `⇤ Revert to here` does -- then compile it and photograph it.
#
#   scripts/demo/survey-backto.sh [<repo-dir>] [<out-dir>]
#
# This is the measurement the demo turns on, and it is different from `survey-reverts.sh`. That one
# takes a single chapter out on its own, which for anything but the tip means digging out from under
# work that depends on it: on this repo every such revert breaks the build, and correctly so.
# Rewinding peels from the top down, so the tree stays consistent the whole way -- which is why
# "put this feature back to how it was, and leave the rest of today alone" is a thing you can film
# and "delete this one old chapter" is not.
#
# Reverts run on clones. Nothing here touches the demo repo, and nothing here mines.
set -uo pipefail

repo="$(cd "${1:-$HOME/repos/sgt-demo/sketchpad}" && pwd)"
out="${2:-/tmp/sketchpad-backto-survey}"
SGT="${SGT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/.venv/bin/sgt}"
CHROME="${CHROME:-/Applications/Google Chrome.app/Contents/MacOS/Google Chrome}"
PORT="${PORT:-5397}"

[ -x "$CHROME" ] || { echo "no headless Chrome at $CHROME; set CHROME=<path>" >&2; exit 1; }
[ -d "$repo/node_modules" ] || { echo "$repo has no node_modules" >&2; exit 1; }

PY_BIN="$(dirname "$SGT")/python"
if [ -x "$PY_BIN" ]; then
  kind=$("$PY_BIN" -c "import sgt.core.op as o; print(o._symbol_kind('a.ts::__import__::./b'))" 2>/dev/null)
  [ "$kind" = "import" ] || { echo "wrong sgt: $SGT does not own import lines" >&2; exit 1; }
fi

rm -rf "$out"; mkdir -p "$out"

shoot() {
  ( cd "$1" && ./node_modules/.bin/vite --port "$PORT" --strictPort >/dev/null 2>&1 & )
  for _ in $(seq 1 40); do curl -sf -o /dev/null "http://[::1]:$PORT/" && break; sleep 0.25; done
  "$CHROME" --headless=new --disable-gpu --hide-scrollbars --force-device-scale-factor=1 \
      --window-size=1280,1000 --virtual-time-budget=6000 \
      --screenshot="$2" "http://localhost:$PORT/" >/dev/null 2>&1
  pkill -f "vite --port $PORT" >/dev/null 2>&1 || true
  sleep 0.6
}

base="$out/_base"
git clone -q "$repo" "$base" && cp -r "$repo/.sgt" "$base/.sgt" && ln -s "$repo/node_modules" "$base/node_modules"
shoot "$base" "$out/base.png"
[ -s "$out/base.png" ] || { echo "could not photograph the app as it stands" >&2; exit 1; }

ids=$( cd "$repo" && "$SGT" log --map --json 2>/dev/null | python3 -c "
import json, sys
for fid, f in json.load(sys.stdin)['features'].items():
    print(fid[2:10], f['label'], sep='\t')
" )

printf '%-46s %-6s %-8s %-9s %s\n' 'rewind this feature to this chapter' chaps tsc picture verdict
n=0
while IFS=$'\t' read -r fid label; do
  [ -n "$fid" ] || continue
  # The chapters, in order, with the name each one carries on the timeline. Read into an array the
  # long way round: macOS ships bash 3.2, which has no `mapfile`.
  chapters=()
  while IFS= read -r line; do chapters+=("$line"); done < <(
    cd "$repo" && "$SGT" log --focus "$fid" 2>/dev/null | sed -e 's/\x1b\[[0-9;]*m//g' \
      | sed -n 's/^ *@\([0-9][0-9]*\) \{1,\}\(.\{1,\}\)$/\1	\2/p' \
      | awk -F'\t' '{n=$2; sub(/  +[^ ].*$/,"",n); print $1"\t"n}' )
  [ "${#chapters[@]}" -gt 0 ] || continue
  # Every checkpoint except the last: rewinding to the last chapter removes nothing.
  for (( k = 0; k < ${#chapters[@]} - 1; k++ )); do
    idx="${chapters[$k]%%$'\t'*}"; name="${chapters[$k]#*$'\t'}"
    n=$((n + 1))
    work="$out/t$n"
    git clone -q "$repo" "$work" 2>/dev/null || continue
    cp -r "$repo/.sgt" "$work/.sgt"
    ln -s "$repo/node_modules" "$work/node_modules"
    # Peel the later chapters newest-first, which is what `⇤ Revert to here` applies.
    okall=yes; peeled=0
    for (( j = ${#chapters[@]} - 1; j > k; j-- )); do
      later="${chapters[$j]%%$'\t'*}"
      res=$( cd "$work" && "$SGT" revert "$fid@$later" --yes 2>&1 | sed -e 's/\x1b\[[0-9;]*m//g' )
      echo "--- $fid@$later" >> "$out/t$n.log"; echo "$res" >> "$out/t$n.log"
      echo "$res" | grep -q 'revert applied' && peeled=$((peeled + 1)) || okall=no
    done
    row="$(printf '%.42s' "$label → $name")"
    if [ "$okall" = no ]; then
      printf '%-46s %-6s %-8s %-9s %s\n' "$row" "$peeled" "-" "-" "a revert refused"
      continue
    fi
    errs=$( cd "$work" && rm -f tsconfig.tsbuildinfo && npx tsc --noEmit 2>&1 | grep -c 'error TS' )
    if [ "$errs" != "0" ]; then
      printf '%-46s %-6s %-8s %-9s %s\n' "$row" "$peeled" "$errs err" "-" "BREAKS BUILD"
      continue
    fi
    shoot "$work" "$out/t$n.png"
    echo "$fid@$idx  $label → $name" > "$out/t$n.target"
    if [ ! -s "$out/t$n.png" ]; then
      printf '%-46s %-6s %-8s %-9s %s\n' "$row" "$peeled" "0 err" "no render" "BLANK"
    elif cmp -s "$out/base.png" "$out/t$n.png"; then
      printf '%-46s %-6s %-8s %-9s %s\n' "$row" "$peeled" "0 err" "identical" "invisible"
    else
      printf '%-46s %-6s %-8s %-9s %s\n' "$row" "$peeled" "0 err" "MOVED" "FILMABLE  ($fid@$idx)"
    fi
  done
done <<< "$ids"

echo
echo "shots and logs in $out"
