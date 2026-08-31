#!/usr/bin/env bash
# Serve the closing shot of the sketchpad take: today's app and four counterfactuals,
# each one today's program minus one named idea, side by side in five browser tabs.
#
#   scripts/demo/idea-grid.sh [<repo-dir>]
#
# Each variant is a fresh clone with the shared store copied in and one
# `sgt revert "<name>"` applied. Nothing touches the demo repo. The servers stay up
# until this script is interrupted, and every window shows an identical console over
# a different drawing, which is the point of the shot.
set -euo pipefail

repo="$(cd "${1:-$HOME/repos/sgt-demo/sketchpad-v2}" && pwd)"
SGT="${SGT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/.venv/bin/sgt}"
work=/tmp/sketchpad-grid

PY_BIN="$(dirname "$SGT")/python"
kind=$("$PY_BIN" -c "import sgt.core.op as o; print(o._symbol_kind('a.ts::__import__::./b'))" 2>/dev/null)
[ "$kind" = "import" ] || { echo "wrong sgt: $SGT does not own import lines" >&2; exit 1; }

# name<TAB>port. "today" is an unreverted clone on the first port.
# All four variants show on the lattice sheet the grid opens on. The two sheet-one
# ideas (equal length, corner on circle) are walked through live in the take instead;
# a lattice window cannot show them, because a master's internal shape never relaxes.
VARIANTS=$(cat <<'EOF'
today	5501
fastened at the corners	5502
full size	5503
stand upright	5504
EOF
)

rm -rf "$work"; mkdir -p "$work"
pids=()
n=0
while IFS=$'\t' read -r name port; do
  [ -n "$name" ] || continue
  n=$((n + 1))
  dir="$work/v$n"
  git clone -q "$repo" "$dir"
  cp -r "$repo/.sgt" "$dir/.sgt"
  ln -s "$repo/node_modules" "$dir/node_modules"
  if [ "$name" != today ]; then
    out=$( cd "$dir" && "$SGT" revert "$name" --yes 2>&1 | sed -e 's/\x1b\[[0-9;]*m//g' | tail -1 )
    case "$out" in
      *"revert applied"*) ;;
      *) echo "revert of \"$name\" failed: $out" >&2; exit 1 ;;
    esac
  fi
  ( cd "$dir" && exec ./node_modules/.bin/vite --port "$port" --strictPort >/dev/null 2>&1 ) &
  pids+=($!)
  printf '  http://localhost:%s/   %s\n' "$port" "$name"
done <<< "$VARIANTS"

trap 'kill "${pids[@]}" 2>/dev/null' EXIT
echo
echo "four servers up. Ctrl-C stops them all."
wait
