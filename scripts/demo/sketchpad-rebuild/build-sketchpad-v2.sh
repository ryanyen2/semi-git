#!/usr/bin/env bash
# Rebuild the sketchpad demo repo with a history whose ideas can be subtracted.
#
#   scripts/demo/sketchpad-rebuild/build-sketchpad-v2.sh [<target-dir>]
#
# In the original history the constraint types lived as entries in two const tables,
# and sgt's TypeScript grammar does not treat a top-level const as a symbol -- the
# entries sat in residue, the bytes between symbols, where every extension rewrites
# one blob and only the newest edit reverts clean. So no idea but the last one could
# be taken out by name.
#
# This build replays the same commits with the types restructured: each constraint
# type is one file under src/kinds/, born whole in the save that introduces it and
# never edited again, plus one side-effect import line in constrain.ts owned by the
# same save. Files and import lines are units sgt attributes cleanly, which is what
# makes `sgt revert` able to subtract one type from today's program. The solver
# machinery still evolves on its original schedule, the masters-and-instances save is
# split so the fastening and the size constraint are separate saves, and the balance
# save disappears (its content is born in).
#
# The per-save trees come from the SOURCE repo's commits plus the overrides in
# final/ and the generator, so run this against a source whose tip is the state you
# want to keep.
set -euo pipefail

script_dir="$(cd "$(dirname "$0")" && pwd)"
SRC="${SRC:-$HOME/repos/sgt-demo/sketchpad}"
DST="${1:-$HOME/repos/sgt-demo/sketchpad-v2}"
SGT="${SGT:-$(cd "$script_dir/../../.." && pwd)/.venv/bin/sgt}"

say() { printf '  %s\n' "$*"; }

command -v "$SGT" >/dev/null || { echo "no sgt at $SGT" >&2; exit 1; }
PY_BIN="$(dirname "$SGT")/python"
kind=$("$PY_BIN" -c "import sgt.core.op as o; print(o._symbol_kind('a.ts::__import__::./b'))" 2>/dev/null)
[ "$kind" = "import" ] || { echo "wrong sgt: $SGT does not own import lines" >&2; exit 1; }

[ -d "$SRC/.git" ] || { echo "no source repo at $SRC" >&2; exit 1; }
[ -f "$script_dir/final/constrain.ts" ] || { echo "no final/ payload" >&2; exit 1; }
if [ -e "$DST" ]; then
  [ "${FORCE:-0}" = 1 ] || { echo "$DST exists; FORCE=1 to replace it" >&2; exit 1; }
  rm -rf "$DST"
fi

gen=$(mktemp -d)
python3 "$script_dir/gen_constrain.py" "$script_dir/final/constrain.ts" "$gen"

# <step>:<constrain override>:<kind files present>, in replay order. SPLIT9A/9B both
# come from the masters-and-instances commit; 9A is that tree with the size
# constraint's data taken back out, 9B restores it. The balance save 56b79bf is
# absent on purpose.
PLAN="
e074e6a:-:-
36ca986:-:-
2b50057:-:-
98e6bdf:-:-
3e63bc5:-:-
2ccd4ba:c6.ts:C
c1bf8a0:c6.ts:C
1221489:c8.ts:CM
SPLIT9A:c9a.ts:CMT
SPLIT9B:c9b.ts:CMTF
113ffa5:c9b.ts:CMTF
a5f1667:c9b.ts:CMTF
f6dbb2e:c12.ts:CMTFH
44275d0:c12.ts:CMTFH
885fb11:c14.ts:CMTFHE
13f97d4:c15.ts:CMTFHE
cfbd0fb:c16.ts:CMTFHE
3acfbe7:c17.ts:CMTFHE
d1d7cb9:c17.ts:CMTFHE
"

message_of() {  # the original message minus its op trailers
  git -C "$SRC" log -1 --format=%B "$1" | sed '/^Sgt-Op:/Id' | sed -e :a -e '/^\n*$/{$d;N;ba' -e '}'
}

# SPLIT9A: the original masters-and-instances tree without the size constraint.
drop_f_from_tree() {  # <tree-dir>
  python3 - "$1" <<'PY'
import pathlib, sys
p = pathlib.Path(sys.argv[1]) / "src/drawing.ts"
t = p.read_text()
old_kind = "export type Kind = 'C' | 'M' | 'T' | 'F'"
assert old_kind in t
t = t.replace(old_kind, "export type Kind = 'C' | 'M' | 'T'")
old_push = """    // Appendix A, code 25, letter F: "Instance is full size, i.e. the same size as
    // its master picture." Without it the solver is free to shrink a hexagon to
    // reach its attachments, and six of them come out seven different sizes.
    constraints.push({ id: `f${k + 1}`, kind: 'F', vars: [`i${k + 1}o`, `i${k + 1}h`] })
"""
assert old_push in t
t = t.replace(old_push, "")
p.write_text(t)
PY
}

resolve_sha() {
  case "$1" in
    SPLIT9A | SPLIT9B) echo 707c755 ;;
    *) echo "$1" ;;
  esac
}

step_message() {
  case "$1" in
    SPLIT9B) printf '%s' 'an instance is full size: the same size as its master picture, held there by a constraint rather than by luck' ;;
    *) message_of "$(resolve_sha "$1")" ;;
  esac
}

# Stage a step's intended tree into <dir>: original commit tree, the SPLIT9A data
# edit, the generated constrain.ts, and the kind files that exist by then.
stage_tree() {  # <step> <override> <kinds> <dir>
  local step=$1 override=$2 kinds=$3 dir=$4
  git -C "$SRC" archive "$(resolve_sha "$step")" | tar -x -C "$dir"
  [ "$step" = SPLIT9A ] && drop_f_from_tree "$dir"
  if [ "$override" != "-" ]; then
    cp "$gen/$override" "$dir/src/constrain.ts"
    mkdir -p "$dir/src/kinds"
    cp "$script_dir/final/kinds/registry.ts" "$dir/src/kinds/"
    local k
    for k in $(echo "$kinds" | grep -o .); do
      cp "$script_dir/final/kinds/$k.ts" "$dir/src/kinds/"
    done
  fi
}

mkdir -p "$DST"; DST="$(cd "$DST" && pwd)"

# Root: the scaffold, a real install, a plain commit, then sgt binds to the repo.
tmp=$(mktemp -d); stage_tree c8bbedd - - "$tmp"
rsync -a --delete --exclude .git --exclude .sgt --exclude node_modules "$tmp/" "$DST/"; rm -rf "$tmp"
cd "$DST"
# A private npm cache: this machine's shared one throws EACCES on rename when another
# user's install has written to it, and that failure looks like a network problem.
say "npm ci..."
npm ci --cache "${NPM_CACHE:-/tmp/npm-cache-sketchpad-v2}" > /tmp/v2-npm.log 2>&1 \
  || { echo "npm ci failed; tail of /tmp/v2-npm.log:" >&2; tail -5 /tmp/v2-npm.log >&2; exit 1; }
git init -q
git add -A
git commit -q -m "$(message_of c8bbedd)"
say "root $(git rev-parse --short HEAD)  $(git log -1 --format=%s)"
"$SGT" init >/dev/null
say "sgt init"

n=1
for entry in $PLAN; do
  step="${entry%%:*}"; rest="${entry#*:}"; override="${rest%%:*}"; kinds="${rest##*:}"
  tmp=$(mktemp -d); stage_tree "$step" "$override" "$kinds" "$tmp"
  rsync -a --delete --exclude .git --exclude .sgt --exclude node_modules "$tmp/" "$DST/"
  git add -A
  "$SGT" save -m "$(step_message "$step")" >/dev/null 2>&1 \
    || { echo "sgt save failed at $step" >&2; exit 1; }
  # Finding 77: a replayed save once lost content while reporting success. Compare
  # the working tree against the intended one after every save.
  if ! diff -rq --exclude .git --exclude .sgt --exclude node_modules "$tmp" "$DST" >/dev/null; then
    echo "TREE MISMATCH after save of $step" >&2
    diff -rq --exclude .git --exclude .sgt --exclude node_modules "$tmp" "$DST" | head >&2
    exit 1
  fi
  rm -rf "$tmp"
  say "save $n  $(git -C "$DST" log -1 --format=%h)  $(git -C "$DST" log -1 --format=%s | cut -c1-58)"
  n=$((n + 1))
done

# The tip must be the source tree wearing the restructured solver.
tmp=$(mktemp -d); stage_tree d1d7cb9 c17.ts CMTFHE "$tmp"
if diff -rq --exclude .git --exclude .sgt --exclude node_modules "$tmp" "$DST" >/dev/null; then
  say "tip tree is the source tree with the kinds/ solver"
else
  echo "TIP DIFFERS FROM INTENDED" >&2
  diff -rq --exclude .git --exclude .sgt --exclude node_modules "$tmp" "$DST" | head >&2
  exit 1
fi
rm -rf "$tmp"
rm -f "$DST/tsconfig.tsbuildinfo"
errs=$(cd "$DST" && npx tsc --noEmit 2>&1 | grep -c 'error TS' || true)
say "tsc: $errs errors"
[ "$errs" = "0" ]
say "done: $DST"
