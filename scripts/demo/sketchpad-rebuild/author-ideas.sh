#!/usr/bin/env bash
# Author the five subtractable ideas of the sketchpad-v2 demo as named features.
#
#   scripts/demo/sketchpad-rebuild/author-ideas.sh [<repo-dir>]
#
# Each idea's feature holds exactly the program-side ops of its constraint type: the
# ops on src/kinds/<K>.ts plus the `import './kinds/<K>'` line in constrain.ts. The
# data side (the constraints stored in the drawing) stays where the miner put it, on
# purpose: reverting the idea removes the program's ability to satisfy the condition
# and leaves the drawing still asking, which is the beat the take films.
#
# Run this AFTER a rebuild and its `sgt log --refresh`. A later mining pass used to
# rewrite the authored features without warning (findings 79 and 80); sgt 0.6.10 keeps
# each one as a lane of exactly its own symbols, and a re-mine now only re-clusters
# and re-labels the machine-named rows. Cut a golden copy of .sgt when this script
# says five passed, so the map you rehearse is the map you record.
#
# Every op reference here is a FULL 64-character id. An 8-character prefix can
# collide with a feature handle (feature ids are minted from a member op's id), and
# `regroup move` with a colliding prefix resolves the feature and moves the wrong
# thing. That collision emptied four authored features once; it is not hypothetical.
set -uo pipefail

repo="$(cd "${1:-$HOME/repos/sgt-demo/sketchpad-v2}" && pwd)"
SGT="${SGT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)/.venv/bin/sgt}"
cd "$repo"

# kind letter | save-subject fragment | the name a person says
IDEAS=$(cat <<'EOF'
C	constraints and the relaxation solver	a corner stays on its circle
M	lines of equal length	lines of equal length
T	fasten them by their corners	fastened at the corners
F	an instance is full size	full size
E	stand a placed group up	stand upright
EOF
)

ops_of_kind() {  # <kind> <subject fragment> -> full op ids, one per line
  local k=$1 subject=$2 sha prev op fp
  sha=$(git log --format="%H %s" | grep -F "$subject" | head -1 | cut -d' ' -f1)
  [ -n "$sha" ] || return 1
  prev=$(git rev-parse "$sha^")
  comm -13 <(git log -1 --format=%B "$prev" | grep -i "^Sgt-Op:" | awk '{print $2}' | sort) \
           <(git log -1 --format=%B "$sha" | grep -i "^Sgt-Op:" | awk '{print $2}' | sort) \
  | while read -r op; do
      fp=$(python3 -c "
import json
print(' '.join(sorted(json.load(open('.sgt/ops/$op')).get('footprint', {}).keys())))
")
      case "$fp" in
        *"kinds/$k.ts"* | *"import__::./kinds/$k"*) echo "$op" ;;
      esac
    done
}

file_body_op() {  # <kind> <the idea's ops> -> the op that carries the file's body
  local k=$1 op fp
  while read -r op; do
    fp=$(python3 -c "
import json
print(' '.join(sorted(json.load(open('.sgt/ops/$op')).get('footprint', {}).keys())))
")
    # The whole-file residue key ends in NUL-delimited "HEAD", which no shell
    # pattern survives; the residue prefix alone names the file body well enough.
    case "$fp" in *"kinds/$k.ts::__residue__"*) echo "$op"; return ;; esac
  done <<< "$2"
}

lane_ops() {  # <full feature id> -> the full op ids the lane holds now
  "$SGT" log --map --json </dev/null 2>/dev/null | python3 -c "
import json, sys
d = json.load(sys.stdin)
ops = set()
for c in d['cells']:
    if c['feature_id'] == '$1':
        ops.update(c['op_ids'])
print('\n'.join(sorted(ops)))
"
}

pass=0; fail=0
ok()  { printf '  \033[32m✓\033[0m %s\n' "$1"; pass=$((pass+1)); }
bad() { printf '  \033[31m✗\033[0m %s\n' "$1"; fail=$((fail+1)); }

while IFS=$'\t' read -r k subject name; do
  [ -n "$k" ] || continue
  want=$(ops_of_kind "$k" "$subject") || { bad "$name: save not found"; continue; }
  want_n=$(echo "$want" | grep -c .)

  existing=$("$SGT" feature select "$name" </dev/null 2>/dev/null | head -1 \
             | sed -n 's/.*: \([0-9]*\) direct.*/\1/p')
  if [ "${existing:-}" = "$want_n" ]; then
    ok "\"$name\" already holds its $want_n op(s)"
    continue
  fi

  # Mint a lane by splitting the feature that owns the kind file's body op.
  body=$(file_body_op "$k" "$want")
  [ -n "$body" ] || { bad "$name: no file-body op"; continue; }
  owner=$("$SGT" show "$body" --json </dev/null 2>/dev/null \
          | python3 -c "
import json, sys
f = json.load(sys.stdin).get('feature')
print(f['id'] if f else '')")
  [ -n "$owner" ] || { bad "$name: file-body op is in no feature"; continue; }
  new=$("$SGT" feature regroup split "$owner" --apply </dev/null 2>&1 \
        | sed -e 's/\x1b\[[0-9;]*m//g' | grep -oE 'f-[a-z0-9]{16,}' | tail -1)
  if [ -z "$new" ] || [ "$new" = "$owner" ]; then
    bad "$name: split minted no lane"
    continue
  fi

  # The split copies a cluster in; evict everything that is not this idea's op.
  # Never with an empty lane id -- an empty match once evicted the whole repo.
  intruders=$(lane_ops "$new" | grep -v -F "$want" || true)
  if [ -n "$intruders" ]; then
    "$SGT" feature regroup move $(echo "$intruders" | tr '\n' ' ') \
      --to "$owner" --json </dev/null >/dev/null 2>&1
  fi
  "$SGT" feature regroup move $(echo "$want" | tr '\n' ' ') --to "$new" --json </dev/null >/dev/null 2>&1
  "$SGT" feature rename "$new" "$name" </dev/null >/dev/null 2>&1

  got=$("$SGT" feature select "$name" </dev/null 2>/dev/null | head -1 \
        | sed -n 's/.*: \([0-9]*\) direct.*/\1/p')
  if [ "${got:-0}" = "$want_n" ]; then
    ok "\"$name\" holds exactly its $want_n op(s)"
  else
    bad "\"$name\" holds ${got:-0} op(s), wanted $want_n"
  fi
done <<< "$IDEAS"

echo
echo "$pass passed, $fail failed"
[ "$fail" -eq 0 ]
