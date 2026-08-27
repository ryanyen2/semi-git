#!/usr/bin/env bash
# Rehearse every beat of the seedbank recording, in a scratch copy, and fail loudly
# if any of them would break on camera.
#
#   scripts/demo/demo-preflight.sh [<repo-dir>]
#
# Run this immediately before recording. It touches nothing in the demo repo: every
# beat runs against a throwaway copy, so a green preflight leaves the repo pristine
# and ready to film. The beats it checks are the ones in
# docs/design/2026-08-27-demo-recording-script.md -- if you change that script,
# change this.
#
# WHY A REHEARSAL AND NOT A TEST SUITE
#
# The unit tests answer "is the kernel correct". This answers a different question:
# "will the exact sequence a presenter types produce the exact thing the script says
# it will". Those come apart. A feature id from a previous build is dead after any
# rebuild, `sgt save`-authored history behaves differently from mined history, and a
# revert of a non-leaf feature leaves a tree that does not compile -- all true with a
# green test suite.
set -uo pipefail

repo="${1:-$HOME/repos/sgt-demo/seedbank-v2}"
# Resolved before any `cd`: the beat-6 checks run from a scratch copy, so a relative path here
# silently resolves to nothing and the check reports a false failure.
here="$(cd "$(dirname "$0")" && pwd)"
PY="$here/../../.venv/bin/python"
[ -x "$PY" ] || PY="python3"
SGT="${SGT:-$here/../../.venv/bin/sgt}"
[ -x "$SGT" ] || SGT="sgt"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

pass=0 fail=0
ok()   { printf "  \033[32m✓\033[0m %s\n" "$1"; pass=$((pass+1)); }
bad()  { printf "  \033[31m✗\033[0m %s\n" "$1"; fail=$((fail+1)); }

[ -d "$repo/.sgt" ] || { echo "no sgt repo at $repo" >&2; exit 1; }
echo "rehearsing $repo"
echo

# --- the repo must be pristine, or beat 1 opens on a half-reverted app -----------
echo "state"
if [ -z "$(git -C "$repo" status --porcelain)" ]; then ok "working tree is clean"
else bad "working tree is DIRTY -- run \`sgt undo\` or rebuild before filming"; fi

# --- beat 2: the history reads in English, not in hashes -------------------------
echo
echo "beat 2 — the history is legible"
tree_out="$(cd "$repo" && "$SGT" log --tree 2>&1)"
if grep -qE "^[0-9]+ features" <<<"$tree_out"; then ok "sgt log --tree lists features"
else bad "sgt log --tree produced no feature count"; fi
if (cd "$repo" && "$SGT" intent list 2>&1) | grep -q "checkpoint(s)"; then ok "sgt intent list names chapters"
else bad "sgt intent list has no checkpoints -- run \`sgt intent build\`"; fi
if grep -qE "af-m[0-9a-f]" <<<"$tree_out"; then
  bad "an UNNAMED auto-feature (af-m...) is in the tree -- it will show on camera"
else ok "no unnamed auto-features in the tree"; fi

# --- beat 6: revert the leaf, by name, and the app must still build ---------------
echo
echo "beat 6 — subtract from the present"
cp -R "$repo" "$work/w" 2>/dev/null
ln -sfn "$repo/node_modules" "$work/w/node_modules" 2>/dev/null
cd "$work/w" || exit 1

base_err=$(npx tsc --noEmit 2>&1 | grep -cE "error TS")
[ "$base_err" = "0" ] && ok "baseline compiles clean" || bad "baseline ALREADY has $base_err errors"

if "$SGT" revert "seed tray" --yes >/dev/null 2>&1; then ok "\`sgt revert \"seed tray\"\` resolves BY NAME and applies"
else bad "revert by name failed -- the label may have changed on rebuild"; fi

after_err=$(npx tsc --noEmit 2>&1 | grep -cE "error TS")
[ "$after_err" = "0" ] && ok "counterfactual compiles clean" || bad "counterfactual has $after_err tsc errors"

# The visible payload: the stars and the header pill must actually be gone.
if grep -rq "TrayButton\|TrayCount" src/ 2>/dev/null; then
  bad "Tray components still referenced after revert"
else ok "every star and the tray pill are gone from the source"; fi

# The import block must not have moved. It compiles either way (ES imports hoist),
# so this is checked by position, not by the compiler.
if head -1 src/Card.tsx | grep -q "^import"; then ok "Card.tsx still begins with its imports"
else bad "Card.tsx no longer begins with imports -- the anchor chain moved them"; fi

# --- beat 4: the overlay's join must resolve, or the rail comes up empty ----------
echo
echo "beat 4 — provenance overlay"
if grep -q "sgtLoc" "$repo/vite.config.ts" 2>/dev/null; then ok "demo stamps data-sgt-loc (sgtLoc plugin wired)"
else bad "vite.config.ts is missing the sgtLoc plugin -- no element can be traced"; fi
blame_json="$(cd "$repo" && "$SGT" advanced blame --all --json 2>/dev/null)"
n_span=$(printf '%s' "$blame_json" | "$PY" -c "
import json,sys
try: b=json.load(sys.stdin)
except Exception: print(0); raise SystemExit
print(sum(1 for v in b.get('files',{}).values() if v.get('spans')))
" 2>/dev/null || echo 0)
if [ "${n_span:-0}" -ge 10 ]; then ok "sgt advanced blame --all covers $n_span files"
else bad "blame covers only ${n_span:-0} files -- the overlay rail will be near-empty"; fi

# --- beat 5: the render panel's primitive -----------------------------------------
# The panel is only as good as `--out`'s sync semantics: it must ADD files going forward, REMOVE
# them going back, and leave alone everything it does not own. If it ever wiped instead of syncing,
# it would take the `node_modules` symlink with it and the dev server would die mid-scrub.
echo
echo "beat 5 — playhead re-folds the running app"
fold_dir="$work/fold"
mkdir -p "$fold_dir"
: > "$fold_dir/.not-mine"
(cd "$repo" && "$SGT" advanced fold --at 13 --out "$fold_dir" >/dev/null 2>&1)
n13=$(ls "$fold_dir/src" 2>/dev/null | wc -l | tr -d " ")
(cd "$repo" && "$SGT" advanced fold --at 4 --out "$fold_dir" >/dev/null 2>&1)
n4=$(ls "$fold_dir/src" 2>/dev/null | wc -l | tr -d " ")
if [ "${n13:-0}" -gt "${n4:-0}" ]; then ok "scrubbing back removes files ($n13 -> $n4 in src/)"
else bad "scrub back did not shrink the tree ($n13 -> $n4) -- --out is not syncing"; fi
if [ -f "$fold_dir/.not-mine" ]; then ok "--out leaves untracked files alone (node_modules survives)"
else bad "--out DELETED an untracked file -- a dev server would not survive a scrub"; fi
if [ -f "$fold_dir/src/Tray.tsx" ]; then bad "Tray.tsx survived a scrub back to frontier 4"
else ok "a file that left the frontier is gone, not stale"; fi

# --- the cycle must close, or a second take opens on a broken app -----------------
echo
echo "reversibility"
if "$SGT" restore "seed tray" --yes >/dev/null 2>&1; then ok "\`sgt restore\` puts it back"
else bad "restore failed"; fi
if diff -rq src "$repo/src" >/dev/null 2>&1; then ok "tree is byte-identical to pristine again"
else bad "restore did NOT round-trip -- reset before the next take"; fi

echo
printf "%d passed, %d failed\n" "$pass" "$fail"
[ "$fail" -eq 0 ] || exit 1
