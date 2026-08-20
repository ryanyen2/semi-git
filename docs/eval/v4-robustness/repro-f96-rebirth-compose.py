#!/usr/bin/env python3
"""F96 reproduction: a delete-then-re-add-elsewhere history does not compose back to its own HEAD.

Run it: `PYTHONPATH=<repo> python3 docs/eval/v4-robustness/repro-f96-rebirth-compose.py`
Exits 1 while the defect stands, 0 when it is fixed. It lives here rather than in `tests/` because
it is evaluation evidence, not a change to the system under test (R1).

What it shows, in order:

1. `fold.code(current_ideal, all_ops)` reverses the two top-level defs and drops the blank line
   between them -- immediately after `sgt init`, with no user edit and no verb run.
2. `sgt advanced fsck` is satisfied (op-level integrity cannot see a composition defect);
   `fsck --tree` catches it; `sgt status` blames the disk for it.
3. A plain `revert` of the residue-deletion op reports `0 symbol(s) changed` and changes a byte,
   because it mints a `__anchor__` layout-repair op and materializes.
4. Neither restore rung recovers the byte (nothing was refused; nothing was removed), and `sgt undo`
   succeeds, restores the exact prior op-set, and still leaves the file wrong -- the prior op-set
   never composed to HEAD either.

Mechanism (F93): `mine._apply_rebirth_chaining` skips `__anchor__` symbols, so the re-added `only`
mints a second `(anchor, before=None)` birth instead of chaining onto the deletion. Two chain heads
for one anchor; `order.fork_free` keeps one, and the one it keeps carries the *original* position.
A fix has to teach `_present_symbols_at` about anchors as well as drop the filter, and it bumps
MINER_VERSION -- which invalidates V3, §6.3/§7.1 and both study fixtures. Hence: reported, not fixed.

Scope, measured: 18 of the 19 `tests/laws/corpus.py` shapes compose back to HEAD byte-for-byte. Only
a rebirth fails. No corpus shape contains a rebirth.
"""
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2]))
sys.path.insert(0, str(HERE))

import harness as H  # noqa: E402
from sgt.core import fold  # noqa: E402
from sgt.core.lens import current_ideal  # noqa: E402
from sgt.core.store import Store  # noqa: E402
from tests.laws import corpus  # noqa: E402


def build(root: Path, cycles: int = 2) -> Path:
    """`only` is written, edited twice, deleted, then re-added *after* `other` -- twice."""
    repo = root / "reborn"
    corpus._init(repo)
    corpus._write(repo, "keep.py", "def keep():\n    return 1\n")
    corpus._write(repo, "mod.py", "def only():\n    return 2\n")
    corpus._commit(repo, "two modules", 1)
    n = 2
    for _c in range(cycles):
        for _e in range(2):
            n += 1
            body = "".join(f"    x{i} = {i}\n" for i in range(n))
            corpus._write(repo, "mod.py", f"def only():\n{body}    return {n}\n")
            corpus._commit(repo, f"edit {n}", n)
        n += 1
        corpus._write(repo, "mod.py", "def other():\n    return 0\n")
        corpus._commit(repo, f"drop only {n}", n)
        n += 1
        body = "".join(f"    y{i} = {i}\n" for i in range(n))
        corpus._write(repo, "mod.py",
                      f"def other():\n    return 0\n\ndef only():\n{body}    return {n}\n")
        corpus._commit(repo, f"re-add only {n}", n)
    return repo


def main() -> int:
    root = Path("/tmp/repro-f96")
    shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True)
    repo = build(root)
    rc, out, err = H.sgt(repo, "init")
    if rc != 0:
        print(f"sgt init failed: {(err or out)[-400:]}")
        return 2

    head = H.tracked_bytes(repo)
    composed = fold.code(current_ideal(repo), Store(repo).all_ops())
    print("1. compose(recorded history) vs HEAD, on an untouched repo")
    print(f"   HEAD    : {head.get('mod.py')!r}")
    print(f"   composed: {composed.get('mod.py')!r}")
    faithful = head.get("mod.py") == composed.get("mod.py")
    print(f"   faithful: {faithful}")

    print("2. what each read says about it")
    for args in (["advanced", "fsck"], ["advanced", "fsck", "--tree"], ["status"]):
        rc, out, err = H.sgt(repo, *args)
        first = ((err or "") + (out or "")).strip().splitlines()
        print(f"   $ sgt {' '.join(args)} -> rc={rc}: {' / '.join(l.strip() for l in first[:3])}"[:220])

    ops = {o.id: o for o in Store(repo).all_ops()}
    target = next((i for i in sorted(current_ideal(repo).op_ids)
                   if any("__residue__" in s for s in ops[i].footprint)), None)
    if target is None:
        print("   (no residue op live -- the shape changed; steps 3-4 skipped)")
        return 0 if faithful else 1

    print(f"3. plain revert of the residue op {target[:12]}")
    ids_before = H.ideal_ids(repo)
    rc, out, err = H.sgt(repo, "revert", target, "--yes")
    said = ((err or "") + (out or "")).strip().splitlines()
    print(f"   rc={rc}: {' / '.join(l.strip() for l in said if l.strip())}"[:300])
    now = H.ideal_ids(repo)
    print(f"   removed {len(ids_before - now)} op(s), minted {len(now - ids_before)} op(s)")
    print(f"   bytes changed: {sorted(p for p in head if head[p] != H.tracked_bytes(repo).get(p))}")

    print("4. recovery")
    for _pass in range(6):
        at = H.ideal_ids(repo)
        for op in sorted(ids_before):
            H.sgt(repo, "restore", op, "--yes")
        if H.ideal_ids(repo) == at:
            break
    for _pass in range(4):
        at = H.ideal_ids(repo)
        for sym in sorted({s for i in sorted(ids_before - H.ideal_ids(repo)) if i in ops
                           for s in ops[i].footprint}) or sorted(ops[target].footprint):
            H.sgt(repo, "restore", sym, "--yes")
        if H.ideal_ids(repo) == at:
            break
    still = sorted(p for p in head if head[p] != H.tracked_bytes(repo).get(p))
    print(f"   after both restore rungs, bytes still wrong in: {still or 'nothing'}")
    rc, out, err = H.sgt(repo, "undo")
    print(f"   $ sgt undo -> rc={rc}: {((err or '') + (out or '')).strip()[:120]}")
    after = H.ideal_ids(repo)
    print(f"   op-set vs before the revert: {len(after - ids_before)} extra, {len(ids_before - after)} missing")
    still = sorted(p for p in head if head[p] != H.tracked_bytes(repo).get(p))
    print(f"   bytes still wrong in: {still or 'nothing'}")

    print(f"\nF96 {'is fixed' if faithful else 'stands'}: the recorded history "
          f"{'composes' if faithful else 'does not compose'} back to its own HEAD.")
    return 0 if faithful else 1


if __name__ == "__main__":
    raise SystemExit(main())
