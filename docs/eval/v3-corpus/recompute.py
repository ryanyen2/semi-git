"""Recompute WP-V3 reconstruction against an honest denominator, from stored records + clones.

The harness's original rate was `1 - drift / summary["files"]`, and `summary["files"]` is
`len(ideal.covered_paths(index))` (`sgt/api.py:3131`) -- *the number of paths sgt claims*, not the
number of files in the repo. Two errors follow, in opposite directions, and they do not cancel:

  1. Files sgt never recorded are excluded from both numerator and denominator. `fsck --tree`
     classifies a tracked path the ideal cannot regenerate as `backstop_kept` (`lens.py:1430`), and
     the original rate ignored that list entirely -- fullcontrol scored 0.83 with 70 such files.
     A file sgt cannot reproduce is a reconstruction failure whether or not sgt admits to owning it.
  2. `drift` also counts paths sgt composes that do not exist at HEAD (zombies, F51). Those are
     real defects, but they are spurious *extra* files, not failures to reproduce an existing one,
     so charging them against a per-existing-file rate mixes two failure modes into one number.

Honest rate here: of the repo's tracked, non-symlink, in-scope files at HEAD, the fraction whose
bytes `code(current_ideal)` reproduces exactly. In-scope means `resolve_tier != "ignored"`
(`sgt/core/tiers.py:208`) -- dotfiles and gitignored paths are a deliberate boundary, not a loss,
and counting them as failures would be as unfair as the original was flattering. Zombies are
reported separately, as a count of files sgt would invent.

Read-only: `git ls-files` and `lstat` on the clone, plus the `fsck_tree` lists already in run.json.
No mining, no writes, so it is safe to run against clones while the sweep is still going.

    python -u docs/eval/v3-corpus/recompute.py [--corpus docs/eval/v3-corpus] [--work /tmp/v3]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


def tracked_paths(repo: Path) -> list[str]:
    """Tracked regular files, by the only correct reading of `ls-files` (F69).

    Was `ls-files` + `.splitlines()`, which is wrong twice. Plain `ls-files` C-quotes any path with
    non-ASCII bytes, so `resources/\\346\\234\\272...pdf` came back as a quoted literal that sgt's own
    drift list can never match -- an entry that scored as a *success* because it could not be marked a
    failure. And it listed non-blob entries: mode 120000 symlinks (which mine skips by R3, so no op ever
    writes them) and mode 160000 submodule gitlinks (whose path is a directory on disk). All three
    classes inflated the published rates; `-z` plus a blob-mode filter is what sgt's gitbind already does.
    """
    proc = subprocess.run(
        ["git", "-C", str(repo), "ls-files", "-z", "-s"], capture_output=True, text=True, check=True
    )
    keep = []
    for entry in proc.stdout.split("\x00"):          # "<mode> <sha> <stage>\t<path>"
        meta, _, path = entry.partition("\t")
        if path and not path.startswith(".sgt/") and meta.split(" ", 1)[0] in ("100644", "100755"):
            keep.append(path)
    return keep


def through_symlink(repo: Path, path: str) -> bool:
    cur = repo
    for part in Path(path).parts:
        cur = cur / part
        if cur.is_symlink():
            return True
    return False


def recompute(rec: dict, repo: Path) -> dict:
    """The honest rate for one repo, plus the pieces it is built from so it can be audited."""
    from sgt.core import tiers

    ft = rec.get("fsck_tree")
    if not isinstance(ft, dict) or ft.get("drift") is None:
        return {"error": "no fsck_tree payload"}

    cfg = tiers.load_tiers(repo)
    tracked = [p for p in tracked_paths(repo) if not through_symlink(repo, p)]
    scope = {p for p in tracked if tiers.resolve_tier(p, cfg) != "ignored"}

    drift = set(ft["drift"])
    backstop = set(ft.get("backstop_kept") or [])
    # A drifted path that git does not track is one sgt composes and the repo does not have (F51).
    zombie = sorted(drift - set(tracked))
    drift_in_scope = sorted(drift & scope)
    backstop_in_scope = sorted(backstop & scope)
    # backstop outside scope is sgt correctly declining to manage a file, not losing it.
    backstop_out = sorted(backstop - scope)

    failed = set(drift_in_scope) | set(backstop_in_scope)
    other = {k: len(ft.get(k) or []) for k in ("staged", "unseeded", "unmanaged")}
    return {
        "in_scope_files": len(scope),
        "tracked_files": len(tracked),
        "failed": len(failed),
        "honest_rate": round(1 - len(failed) / len(scope), 4) if scope else None,
        "claimed_rate": (rec.get("reconstruction") or {}).get("rate"),
        "drift_in_scope": len(drift_in_scope),
        "backstop_in_scope": len(backstop_in_scope),
        "backstop_out_of_scope": len(backstop_out),
        "zombie": len(zombie),
        "other_classes": other,
        "samples": {
            "backstop_in_scope": backstop_in_scope[:8],
            "zombie": zombie[:8],
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", default="docs/eval/v3-corpus")
    ap.add_argument("--work", default="/tmp/v3")
    ap.add_argument("--write", action="store_true", help="store the result back into each run.json")
    args = ap.parse_args()

    rows = []
    for path in sorted(Path(args.corpus).glob("*/run.json")):
        rec = json.loads(path.read_text())
        if "backfill" not in rec:
            continue
        repo = Path(args.work) / path.parent.name
        if not (repo / ".git").exists():
            rows.append((rec["repo"], {"error": "clone gone"}))
            continue
        res = recompute(rec, repo)
        rows.append((rec["repo"], res))
        if args.write and "error" not in res:
            rec["reconstruction_honest"] = res
            path.write_text(json.dumps(rec, indent=2) + "\n")

    print(f"{'repo':30s} {'scope':>6} {'fail':>5} {'honest':>7} {'claimed':>8} "
          f"{'drift':>6} {'bkstp':>6} {'oos':>5} {'zomb':>5}")
    for name, r in rows:
        if "error" in r:
            print(f"{name:30s} {r['error']}")
            continue
        print(f"{name:30s} {r['in_scope_files']:>6} {r['failed']:>5} "
              f"{str(r['honest_rate']):>7} {str(r['claimed_rate']):>8} "
              f"{r['drift_in_scope']:>6} {r['backstop_in_scope']:>6} "
              f"{r['backstop_out_of_scope']:>5} {r['zombie']:>5}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
