"""§6.2's refusal figures for the five real clones, recomputed from the sweep artifacts.

The paragraph in `sec:eval-precondition` quotes 137 revert/restore attempts, 82 refused, per-repository
rates, and a 21/49/12 attribution split. Those came out of an ad-hoc read of `/tmp`; this recomputes
them from `final-sweep/`, so the paragraph is checkable from the repository.

It also recovers what the ledger had written off. Instrument error #21(b) keeps only the last 150
characters of each message, and the sentence naming the guard is at the front, so 46 of 49 path-list
refusals could not be attributed to a guard. But the two guards that print a bare sorted path list
differ in what they are *able* to name: `_dirty_conflicts` (`lens.py:1345`) flags a path only when
`on_disk != committed`, which for a path absent from both disk and HEAD is `None != None` -- false. So
a list naming a path that does not exist at HEAD can only be the outside-delta guard's.

HEAD comes from `head-trees.json` (sha + `ls-files` per input repository, archived beside this script
so the attribution is checkable without the 6 GB of clones); `--clones` re-reads them live instead.
Either way it is the clone's HEAD, not the sweep copy's, whose sha the run artifacts never recorded --
instrument error #29. Harness-created `v4_mod_*.py` are excluded by name. Truncation keeps the *tail*
of a sorted list, so the distinct-path count is a floor and is biased toward alphabetically-late paths.

    python -u docs/eval/v4-robustness/refusals.py [--runs docs/eval/v4-robustness/final-sweep] [--clones /tmp/v3]
"""

from __future__ import annotations

import argparse
import collections
import json
import re
import subprocess
from pathlib import Path

# The op population §6.2's claim is about: the verbs that materialize a tree.
# Exactly as the artifacts spell them -- `revert --keep-dependents` carries its flag in the op name,
# and dropping it silently loses 21 of the 137 attempts (and moves every per-repository rate).
OPS = {"restore", "revert", "revert --keep-dependents", "revert_restore_probe", "revert_undo_probe"}
GUARD = "roll back files outside this edit's scope"
CLOSURE = ("without the edit(s) it was built on", "would leave", "fork")
HARNESS_FILE = re.compile(r"v4_mod_\d+\.py$")


def heads(archive: Path, clones: Path | None) -> dict[str, set[str]]:
    """Tracked paths per input repository, from the archive unless --clones asks for a live read."""
    if clones is None:
        rows = json.loads(archive.read_text())
        return {name: set(row["files"]) for name, row in rows.items()}
    out = {}
    for clone in sorted(clones.iterdir()):
        if not (clone / ".git").exists():
            continue
        proc = subprocess.run(["git", "-C", str(clone), "ls-files", "-z"],
                              capture_output=True, text=True, check=True)
        out[clone.name] = {p for p in proc.stdout.split("\x00") if p}
    return out


def listed_paths(out: str) -> list[str]:
    """Complete path tokens in a possibly head-truncated Python-repr list. The first token of a
    truncated list is a fragment and is dropped; the last is kept only if its closing quote survived."""
    if "', '" not in out and "['" not in out:
        return []
    body = out[out.index("[") + 1:] if "[" in out else out
    parts = body.split("', '")
    toks = []
    for i, part in enumerate(parts):
        if i == 0 and "[" not in out:
            continue  # leading fragment
        if i == 0:
            part = part.lstrip("'")
        if i == len(parts) - 1:
            closed = re.match(r"([^']*)'", part)
            if not closed:
                continue
            part = closed.group(1)
        toks.append(part)
    return [t for t in toks if t and not t.startswith(" ") and not HARNESS_FILE.match(t)]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", default="docs/eval/v4-robustness/final-sweep")
    ap.add_argument("--clones", help="read HEAD live from these clones instead of head-trees.json")
    args = ap.parse_args()
    runs = Path(args.runs)
    head = heads(Path(__file__).resolve().parent / "head-trees.json",
                 Path(args.clones) if args.clones else None)
    real = set(json.loads((runs / "sweep-plan.json").read_text()).get("repos", ()))
    real = {Path(r).name for r in real}

    kinds: collections.Counter = collections.Counter()
    per_repo: dict[str, list[int]] = {}
    split: collections.Counter = collections.Counter()
    distinct: dict[str, set[str]] = {}
    for f in sorted(runs.glob("run-*.json")):
        rec = json.loads(f.read_text())
        name = rec["label"]
        if name not in real:
            continue
        at_head = head[name]
        seen, refused = 0, 0
        for entry in rec["log"]:
            if entry["op"] not in OPS:
                continue
            seen += 1
            if entry.get("rc", 0) == 0:
                continue
            refused += 1
            kinds[entry["op"]] += 1
            out = entry.get("out") or ""
            paths = listed_paths(out)
            if paths:
                split["path_list"] += 1
                distinct.setdefault(name, set()).update(paths)
                absent = [p for p in paths if p not in at_head]
                if GUARD in out:
                    split["named_guard"] += 1
                elif absent:
                    split["attributed_by_absent_path"] += 1
                if absent:
                    split["names_a_surplus_path"] += 1
            elif any(c in out for c in CLOSURE):
                split["closure_or_fork"] += 1
            else:
                split["unattributed"] += 1
        per_repo[name] = [seen, refused]

    total = sum(v[0] for v in per_repo.values())
    ref = sum(v[1] for v in per_repo.values())
    print(f"attempts {total}  refused {ref}  ({ref / total:.0%})")
    for name, (seen, refused) in sorted(per_repo.items()):
        print(f"  {name:38s} {refused:3d} of {seen:3d}  {refused / seen if seen else 0:.0%}")
    print(f"\nby op kind: {dict(sorted(kinds.items()))}")
    print(f"attribution: {dict(sorted(split.items()))}")
    for name, paths in sorted(distinct.items()):
        absent = sorted(p for p in paths if p not in head[name])
        print(f"  {name:38s} distinct named {len(paths):3d}  absent from HEAD {len(absent):3d}")
        for p in absent:
            print(f"        surplus: {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
