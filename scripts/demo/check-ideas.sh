#!/usr/bin/env bash
# The sketchpad-v2 preflight: rehearse all five subtractable ideas on throwaway
# clones, immediately before recording.
#
#   scripts/demo/check-ideas.sh [<repo-dir>] [<out-dir>]
#
# For each idea it reverts BY NAME, typechecks, photographs both sheets, compares
# them against the repo as it stands, undoes, and byte-compares the restored tree.
# Five ideas, six checks each. This is the check that silently stops being true:
# any mining pass (`sgt save`, `sgt log --refresh`, `sgt log --rebuild`) rewrites
# authored features with no warning, the names keep resolving, and the reverts
# quietly grow. Findings 79 and 80.
#
# Expected picture movement per idea: "a corner stays on its circle" and "lines of
# equal length" move sheet 1 only (the lattice's hexagons are rigid master
# expansions); the other three move sheet 2.
set -uo pipefail

repo="$(cd "${1:-$HOME/repos/sgt-demo/sketchpad-v2}" && pwd)"
out="${2:-/tmp/sketchpad-check-ideas}"
SGT="${SGT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/.venv/bin/sgt}"
CHROME="${CHROME:-/Applications/Google Chrome.app/Contents/MacOS/Google Chrome}"
PORT="${PORT:-5384}"

[ -x "$CHROME" ] || { echo "no headless Chrome at $CHROME" >&2; exit 1; }
[ -d "$repo/node_modules" ] || { echo "$repo has no node_modules; npm ci there" >&2; exit 1; }
PY_BIN="$(dirname "$SGT")/python"
kind=$("$PY_BIN" -c "import sgt.core.op as o; print(o._symbol_kind('a.ts::__import__::./b'))" 2>/dev/null)
[ "$kind" = "import" ] || { echo "wrong sgt: $SGT does not own import lines" >&2; exit 1; }

rm -rf "$out"; mkdir -p "$out"
pass=0; fail=0
ok()  { printf '  \033[32m✓\033[0m %s\n' "$1"; pass=$((pass+1)); }
bad() { printf '  \033[31m✗\033[0m %s\n' "$1"; fail=$((fail+1)); }

dirty=$(git -C "$repo" status --porcelain)
[ -z "$dirty" ] && ok "working tree is clean" || bad "working tree is DIRTY"

shoot_both() {  # <dir> <prefix>
  ( cd "$1" && ./node_modules/.bin/vite --port "$PORT" --strictPort >/dev/null 2>&1 & )
  for _ in $(seq 1 40); do curl -sf -o /dev/null "http://[::1]:$PORT/" && break; sleep 0.25; done
  cat > "$out/plan.json" <<EOF
[
  { "wait": 1300 },
  { "shot": "$2-sheet2.png" },
  { "eval": "[...document.querySelectorAll('.tog')].find(b=>b.textContent.includes('SHEET 2')).click(); 'sheet1'", "as": "a" },
  { "wait": 1100 },
  { "shot": "$2-sheet1.png" }
]
EOF
  node "$(dirname "${BASH_SOURCE[0]}")/drive-page.mjs" "http://localhost:$PORT/" "$out/plan.json" \
    > "$2.drive" 2>&1
  pkill -f "vite --port $PORT" >/dev/null 2>&1 || true
  sleep 0.5
}

base="$out/_base"
git clone -q "$repo" "$base" && cp -r "$repo/.sgt" "$base/.sgt" && ln -s "$repo/node_modules" "$base/node_modules"
shoot_both "$base" "$out/base"
[ -s "$out/base-sheet2.png" ] && ok "the app as it stands photographs" \
  || { bad "could not photograph the app as it stands"; echo; echo "$pass passed, $fail failed"; exit 1; }

IDEAS=$(cat <<'EOF'
a corner stays on its circle	sheet1
lines of equal length	sheet1
fastened at the corners	sheet2
full size	sheet2
stand upright	sheet2
EOF
)

while IFS=$'\t' read -r name expect; do
  [ -n "$name" ] || continue
  slug=$(echo "$name" | tr ' ' '-')
  work="$out/$slug"
  git clone -q "$repo" "$work" && cp -r "$repo/.sgt" "$work/.sgt" && ln -s "$repo/node_modules" "$work/node_modules"
  res=$( cd "$work" && "$SGT" revert "$name" --yes </dev/null 2>&1 | sed -e 's/\x1b\[[0-9;]*m//g' )
  echo "$res" > "$out/$slug.revert"
  if ! echo "$res" | grep -qE 'revert applied|removed [0-9]+ edit'; then
    bad "\"$name\": revert did not apply"; continue
  fi
  errs=$( cd "$work" && rm -f tsconfig.tsbuildinfo && npx tsc --noEmit 2>&1 | grep -c 'error TS' )
  [ "$errs" = "0" ] && ok "\"$name\" reverts by name and compiles" \
    || { bad "\"$name\": $errs tsc error(s) after revert"; continue; }
  shoot_both "$work" "$out/$slug"
  moved=""
  cmp -s "$out/base-sheet1.png" "$out/$slug-sheet1.png" || moved="sheet1 $moved"
  cmp -s "$out/base-sheet2.png" "$out/$slug-sheet2.png" || moved="sheet2 $moved"
  case "$moved" in
    *"$expect"*) ok "\"$name\" moves the picture ($moved)" ;;
    *) bad "\"$name\" expected $expect to move; moved: ${moved:-nothing}" ;;
  esac
  ( cd "$work" && "$SGT" undo </dev/null >/dev/null 2>&1 )
  if diff -rq "$work/src" "$repo/src" >/dev/null 2>&1; then
    ok "\"$name\": undo restores src byte for byte"
  else
    bad "\"$name\": undo did not restore src exactly"
  fi
done <<< "$IDEAS"

echo
echo "$pass passed, $fail failed  ·  shots in $out"
[ "$fail" -eq 0 ]
