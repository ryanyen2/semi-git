#!/bin/sh
# F42 -- reverting a file's last entity leaves the path behind as a blank tracked file.
# Minimized from `no_empty_phantom` (38/69 of the WP-V4 non-fatal failures, via both revert and undo).
# Run from anywhere; writes only to $WORK. Changes nothing in the sgt repo.
#
# Expected: m.py is left tracked at 0 bytes, and the ideal's only remaining m.py op is the
# end-of-file gap sentinel `m.py::__residue__::\x00HEAD\x00`, which no removal path ever names
# (`subtract.layout_ops_of` mints layout symbols for born *entity* names only). `residue` is
# content-bearing (`op.py:105`), so `fold.code` keeps the path covered and folds it to b"".
#
# The second half shows why the obvious fix -- "a path needs a live entity to materialize" -- is
# wrong: a comment-only file and an empty file have the *same* ideal shape as the phantom.
set -e
WORK=${WORK:-/tmp/f42-repro}
rm -rf "$WORK" && mkdir -p "$WORK" && cd "$WORK"

git init -q . && git config user.email a@b.c && git config user.name t
printf 'def keep():\n    return 0\n' > k.py
printf 'def only():\n    return 1\n' > m.py
printf '# just a comment file\n# no entities at all\n' > c.py
printf '' > empty.py
git add -A && git commit -qm init
python -m sgt.cli init >/dev/null 2>&1

echo "--- symbols, by kind ---"
python -c "
from sgt.core.store import Store
from sgt.core.op import _symbol_kind
for op in Store('.').all_ops():
    for s in op.footprint:
        print(op.id[:12], _symbol_kind(s), repr(s))
" | sort -k3

target=$(python -c "
from sgt.core.store import Store
print(next(o.id for o in Store('.').all_ops() if 'm.py::only' in o.footprint))
")
echo "--- revert m.py::only ($(echo "$target" | cut -c1-12)) ---"
python -m sgt.cli revert "$target" --yes

echo "--- tracked files (blank = the phantom) ---"
python - <<'PY'
import subprocess, pathlib
for p in subprocess.run(["git", "ls-files"], capture_output=True, text=True).stdout.split():
    b = pathlib.Path(p).read_bytes()
    print(f"  {p:12} bytes={len(b):<4} {'BLANK' if not b.strip() else 'ok'}")
PY

echo "--- ideal after the revert ---"
python -c "
import json, pathlib
from sgt.core.store import Store
by = {o.id: sorted(o.footprint) for o in Store('.').all_ops()}
d = json.loads(pathlib.Path('.sgt/local/ideal.json').read_text())['data']
for ref, ids in d.items():
    for i in sorted(ids):
        print(' ', i[:12], by[i])
"
