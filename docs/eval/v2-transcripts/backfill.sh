#!/bin/zsh
# Drive sgt's backward genesis-backfill to the root commit, for every repo named on the command line.
#
# A no-horizon `sgt init` bootstraps the witness to HEAD and then walks history backward one
# 10-second-deadline-bounded chunk per `get()` call (sgt/core/lens.py:24-29, :709). One
# `sgt log --refresh` = one chunk, so a 261-commit repo needs ~24 of them. Until
# `.sgt/local/backfill.json` reports `reached_genesis` for the checked-out ref, only the newest slice
# of history has been mined at all -- and nothing in sgt says so (F28), which is why this exists.
#
# Keyed on the *checked-out* ref, not `main`: three of the four V2 repos sit on a feature branch, and
# a `main`-only check silently never terminates on them.
set -u
for repo in "$@"; do
  cd "$repo" || continue
  ref=$(git symbolic-ref -q HEAD)
  n=0
  while : ; do
    flag=$(python -c "
import json,sys
print(json.load(open('.sgt/local/backfill.json'))['data'].get('$ref',{}).get('reached_genesis',False))
" 2>/dev/null)
    [[ "$flag" == "True" ]] && break
    n=$((n+1))
    if [[ $n -gt 80 ]]; then echo "$repo ($ref): GAVE UP after 80 chunks, still not at genesis"; break; fi
    sgt log --refresh --json >/dev/null 2>&1
  done
  echo "$repo ($ref): reached_genesis=$flag after $n chunks"
  sgt log --rebuild --json >/dev/null 2>&1
  python - <<'PY'
import json, pathlib
t = json.loads(pathlib.Path(".sgt/tree/tree.json").read_text())["data"]
leaves = [n for n in t["nodes"].values() if not n.get("children")]
def bare(s):
    p, _, rest = s.partition("::")
    for tag in ("__residue__::", "__anchor__::"):
        if rest.startswith(tag):
            return f"{p}::{rest[len(tag):]}"
    return s
members = {bare(m) for n in leaves for m in (n.get("members") or [])}
print(f"  ops {len(list(pathlib.Path('.sgt/ops').iterdir()))}  leaves {len(leaves)}  "
      f"member symbols {len(members)}")
PY
done
