#!/usr/bin/env bash
# Rehearse the Sketchpad demo's revert beat against a throwaway clone.
#
#   scripts/demo/check-revert.sh [<repo-dir>] [<feature label>]
#
# Run this immediately before recording. It is the check that silently stops being
# true: the authored feature the beat reverts gets rewritten by any mining pass
# (`sgt save`, `sgt log --refresh`, `sgt log --rebuild`), nothing warns you, the name
# still resolves, and the revert quietly grows from seventeen edits to two hundred.
# Findings 79 and 80.
#
# The clone is made and reverted first, before anything else runs in it. Interleaving
# other commands here reproducibly tripped sgt's `put()` guard, which then refused
# the revert naming four files it never touches. Everything measurable is measured
# after.
set -uo pipefail

repo="$(cd "${1:-$HOME/repos/sgt-demo/sketchpad}" && pwd)"
label="${2:-show the solving order}"
# Default to the build this demo needs, not whatever `sgt` is on PATH. Getting this
# wrong is not a loud failure: the revert preview is computed from the store and
# comes out right, and then the apply refuses with a `put()` rollback warning naming
# files the revert never touches. That cost an hour. The guard below is the same
# check RUNBOOK section 1 asks you to run by hand.
SGT="${SGT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/.venv/bin/sgt}"
PY_BIN="$(dirname "$SGT")/python"
if [ -x "$PY_BIN" ]; then
  kind=$("$PY_BIN" -c "import sgt.core.op as o; print(o._symbol_kind('a.ts::__import__::./b'))" 2>/dev/null)
  if [ "$kind" != "import" ]; then
    echo "wrong sgt: $SGT does not own import lines (_symbol_kind said '${kind:-nothing}')." >&2
    echo "check out feat/live-render-timeline in the sgt source tree, or set SGT to one that does." >&2
    exit 1
  fi
fi
work="$(cd /tmp && pwd -P)/sketchpad-revert-check"

rm -rf "$work"
git clone -q "$repo" "$work" || { echo "could not clone $repo" >&2; exit 1; }
cp -r "$repo/.sgt" "$work/.sgt"
ln -s "$repo/node_modules" "$work/node_modules"
cd "$work" || exit 1
out=$("$SGT" revert "$label" --yes 2>&1 | sed -e 's/\x1b\[[0-9;]*m//g')
counterfactual=$(rm -f tsconfig.tsbuildinfo; npx tsc --noEmit 2>&1 | grep -c 'error TS')
gone_file=$([ -f src/Freedoms.tsx ] && echo no || echo yes)
gone_switch=$(grep -q FREEDOMS src/Toggles.tsx 2>/dev/null && echo no || echo yes)
"$SGT" undo >/dev/null 2>&1
restored=$(rm -f tsconfig.tsbuildinfo; npx tsc --noEmit 2>&1 | grep -c 'error TS')
exact=$(diff -rq src "$repo/src" >/dev/null 2>&1 && echo yes || echo no)

dirty=$(git -C "$repo" status --porcelain)
direct=$(cd "$repo" && "$SGT" feature select "$label" 2>/dev/null | head -1 | sed -n 's/.*: \([0-9]*\) direct.*/\1/p')
removed=$(echo "$out" | sed -n 's/.*removes \([0-9]*\) edits.*/\1/p' | head -1)

pass=0; fail=0
ok()  { printf '  \033[32m✓\033[0m %s\n' "$1"; pass=$((pass+1)); }
bad() { printf '  \033[31m✗\033[0m %s\n' "$1"; fail=$((fail+1)); }

echo "rehearsing \"$label\" in $repo"
[ -z "$dirty" ] && ok "working tree is clean" || bad "working tree is DIRTY, so \`sgt undo\` has nothing exact to return to"

if [ "${direct:-0}" -gt 0 ] && [ "${direct:-0}" -le 20 ]; then
  ok "\"$label\" is still one save's work ($direct direct ops)"
else
  bad "\"$label\" resolves to ${direct:-no} direct op(s), expected about 17; see RUNBOOK section 6"
fi

echo "$out" | grep -q 'revert applied' \
  && ok "\`sgt revert \"$label\"\` resolves BY NAME and applies" \
  || { bad "revert did not apply"; echo "$out" | tail -3; }

if [ "${removed:-0}" -gt 0 ] && [ "${removed:-0}" -le 40 ]; then
  ok "it removes $removed edits, which is one save's worth"
else
  bad "it removes ${removed:-?} edits, too many for the beat; see RUNBOOK section 6"
fi

[ "$counterfactual" = "0" ] && ok "the counterfactual compiles clean" || bad "the counterfactual has $counterfactual tsc error(s)"
[ "$gone_file" = "yes" ]    && ok "src/Freedoms.tsx is gone" || bad "src/Freedoms.tsx survived the revert"
[ "$gone_switch" = "yes" ]  && ok "the FREEDOMS switch is gone from the bank" || bad "the FREEDOMS switch survived in Toggles.tsx"
[ "$restored" = "0" ]       && ok "the restored tree compiles clean" || bad "the restored tree has $restored tsc error(s)"
[ "$exact" = "yes" ]        && ok "\`sgt undo\` restores src byte for byte" || bad "\`sgt undo\` did not restore src exactly"

echo
echo "$pass passed, $fail failed"
[ "$fail" -eq 0 ]
