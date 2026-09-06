#!/usr/bin/env bash
# Preflight for the sketchpad-v3 take (the four-step walkthrough): stage a throwaway
# clone, run every command the take runs, and check what each one has to be true for.
# Run it immediately before recording; every line should be a tick.
#
#   scripts/demo/sketchpad-v3/check-v3.sh [<out-dir>]
set -uo pipefail
here=$(cd "$(dirname "$0")" && pwd)
out=${1:-/tmp/sketchpad-v3-check}
SGT=${SGT:-$(cd "$here/../../.." && pwd)/.venv/bin/sgt}
PORT=${PORT:-5511}
DRIVE=$here/../drive-page.mjs
export DRIVE_PORT=${DRIVE_PORT:-9455}
export DRIVE_PROFILE=$out/chrome-profile
rm -rf "$out"; mkdir -p "$out"
pass=0; fail=0
ok()  { printf '  \033[32m✓\033[0m %s\n' "$1"; pass=$((pass+1)); }
bad() { printf '  \033[31m✗\033[0m %s\n' "$1"; fail=$((fail+1)); }
strip() { sed -e 's/\x1b\[[0-9;]*m//g'; }
shot() {  # <png> -> prints the console line under the scope
  cat > "$out/plan.json" <<EOP
[ { "wait": 1500 }, { "shot": "$1" }, { "eval": "document.body.innerText.slice(-60)", "as": "footer" } ]
EOP
  node "$DRIVE" "http://localhost:$PORT/" "$out/plan.json" 2>&1 | grep 'eval footer' | sed 's/^eval footer //'
}

take=$(PORT=$PORT bash "$here/stage.sh" "$out/take" | head -1)
[ -d "$take/.sgt" ] && ok "staged a throwaway take at $take" || { bad "could not stage the take"; exit 1; }
cd "$take"

# step 1 and 2: the history reads, and the fastening is findable and costed
res=$("$SGT" show "fastened at the corners" </dev/null 2>&1 | strip)
echo "$res" | grep -q '11 edits' && ok 'fastened at the corners: 11 edits before the agent' || bad "fastened at the corners: expected 11 edits: $(echo "$res" | sed -n 2p)"
res=$("$SGT" find "hexagon groups held together at their corners" </dev/null 2>&1 | strip)
echo "$res" | grep -o '[0-9a-f]\{7\}' | head -5 | grep -q c69fc3e && ok 'find puts c69fc3e in the top five' || bad 'find: c69fc3e not in the top five'
footer=$(shot "$out/1-lattice.png")
echo "$footer" | grep -q 'ONE PASS' && ok "the lattice photographs, one pass" || bad "base shot: $footer"

# step 3: the agent plans, edits, saves; sgt files the work into the feature the plan named
res=$(bash "$here/agent.sh" "$take" 2>&1 | strip); echo "$res" > "$out/agent.txt"
echo "$res" | grep -q 'intake: session' && ok 'plan intake recorded' || bad 'plan intake failed'
echo "$res" | grep -q 'fastened at the corners' && echo "$res" | grep -q 'plan step .* fulfilled' \
  && ok 'save filed into "fastened at the corners" and fulfilled the plan step' || bad "save echo: $(echo "$res" | tail -6)"
res=$("$SGT" show "fastened at the corners" </dev/null 2>&1 | strip)
echo "$res" | grep -q '13 edits' && ok 'fastened at the corners: 13 edits after the agent' || bad "expected 13 edits: $(echo "$res" | sed -n 2p)"
cp -r src "$out/src-after-save"
footer=$(shot "$out/2-seam.png")
cmp -s "$out/1-lattice.png" "$out/2-seam.png" && bad 'the seam did not move the picture' || ok 'the seam moves the picture'
echo "$footer" | grep -q 'ONE PASS' && ok 'still one pass with the seam' || bad "seam shot: $footer"

# step 4: preview, apply, see, undo
res=$("$SGT" revert "fastened at the corners" </dev/null 2>&1 | strip); echo "$res" > "$out/preview.txt"
echo "$res" | grep -q 'removes 13 edits' && echo "$res" | grep -q 'not applied' && ok 'preview: removes 13 edits, nothing applied' || bad "preview: $(echo "$res" | tail -3)"
diff -rq src "$out/src-after-save" >/dev/null && ok 'preview left the tree untouched' || bad 'preview changed the tree'
res=$("$SGT" revert "fastened at the corners" --yes </dev/null 2>&1 | strip)
echo "$res" | grep -q 'revert applied' && ok 'revert applied' || bad "revert: $(echo "$res" | tail -2)"
rm -f tsconfig.tsbuildinfo
errs=$(npx tsc --noEmit 2>&1 | grep -c 'error TS')
[ "$errs" = "0" ] && ok 'compiles without the fastening' || bad "$errs tsc error(s) without the fastening"
footer=$(shot "$out/3-unfastened.png")
cmp -s "$out/2-seam.png" "$out/3-unfastened.png" && bad 'removing the fastening did not move the picture' || ok 'removing the fastening moves the picture'
echo "$footer" | grep -q 'RELAXATION' && ok "solver falls back to relaxation: $footer" || bad "unfastened shot: $footer"
res=$("$SGT" undo </dev/null 2>&1 | strip)
echo "$res" | grep -q 'restored' && ok 'undo restored the record' || bad "undo: $res"
diff -r src "$out/src-after-save" >/dev/null && ok 'undo restores src byte for byte (seam present, fastening back)' || bad 'undo did not restore src exactly'

pkill -f "vite --port $PORT" >/dev/null 2>&1 || true
echo
echo "$pass passed, $fail failed  ·  shots and logs in $out"
[ "$fail" -eq 0 ]
