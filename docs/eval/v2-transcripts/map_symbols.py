#!/usr/bin/env python3
"""WP-V2 step 2: turn each request's file edits into the symbols they touched.

    python docs/eval/v2-transcripts/map_symbols.py <edits-<name>.json> --repo <path> --out <dir>

The pairwise metric in step 4 compares "these two symbol-edits came from the same human request"
against "sgt filed them under the same feature". That needs symbols, and a transcript records text
edits. This script bridges the two using sgt's own extractor (`sgt.entities.extract.extract_file`),
deliberately -- if the mapper used a different notion of "symbol" than the system under test, a
disagreement would be unattributable.

How an edit becomes symbols:

* `Write` (and any edit whose `old` is empty) is a whole-file body: extract from the written text.
* `Edit` carries `old`, the exact bytes that were replaced. Those bytes existed in the file at the
  moment of the edit, and if the work was committed they still exist in some blob of that path. So:
  scan the path's blobs newest-first for one containing `old`, extract that blob, and take the
  entities whose byte span overlaps where `old` sat. This locates edits *inside* a function body,
  which is most of them and which a naive "parse the new text" approach misses entirely.
* If no blob contains `old` (the edit was overwritten again before any commit, or the file was
  never committed), fall back to parsing the `new` text as a fragment. Tagged `fragment`, because a
  fragment names only symbols it fully contains -- a body-only edit yields nothing and is counted
  unmatched rather than guessed at.

The declared gate (plan step 2, fixed before running): if the match rate over *eligible* edits is
below 70% for a repo, stop and diagnose rather than reporting a metric computed on a third of the
data. Eligible means "in a file whose language sgt's extractor supports" -- a `.md` or `.tex` edit
has no symbols to find and is reported as a separate share, not as a failure to match.
"""
from __future__ import annotations

import argparse
import json
import subprocess
from collections import Counter, defaultdict
from pathlib import Path

from sgt.entities.extract import _language_for, extract_file

# Newest-first blob budget per path. The blob that still contains an edit's `old` text is nearly
# always the next commit touching that file; a large budget only costs time on paths edited hundreds
# of times. Reported as `blob-budget-exhausted` when it bites, so the cap can never quietly become an
# unmatched edit of unknown cause.
BLOB_BUDGET = 80


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True).stdout


def path_commits(repo: Path) -> dict[str, list[str]]:
    """path -> commit shas that touched it, newest first, across all refs. One git process."""
    out: dict[str, list[str]] = defaultdict(list)
    sha = ""
    for line in git(repo, "log", "--all", "--format=%H", "--name-only").splitlines():
        line = line.strip()
        if not line:
            continue
        if len(line) == 40 and all(c in "0123456789abcdef" for c in line):
            sha = line
        else:
            out[line].append(sha)
    return out


class Blobs:
    """Lazy `sha:path` -> bytes, one `git cat-file --batch` process for the whole run."""

    def __init__(self, repo: Path) -> None:
        self.proc = subprocess.Popen(["git", "cat-file", "--batch"], cwd=repo,
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        self.cache: dict[str, bytes | None] = {}

    def get(self, spec: str) -> bytes | None:
        if spec in self.cache:
            return self.cache[spec]
        assert self.proc.stdin and self.proc.stdout
        self.proc.stdin.write(spec.encode() + b"\n")
        self.proc.stdin.flush()
        header = self.proc.stdout.readline().decode(errors="replace").split()
        if len(header) < 3:                     # "<spec> missing"
            self.cache[spec] = None
            return None
        size = int(header[2])
        data = self.proc.stdout.read(size)
        self.proc.stdout.read(1)                # trailing newline
        self.cache[spec] = data
        return data

    def close(self) -> None:
        if self.proc.stdin:
            self.proc.stdin.close()
        self.proc.wait()


def entities_covering(rel: str, blob: bytes, start: int, end: int) -> list[str]:
    return sorted({e.name for e in extract_file(rel, blob)
                   if e.start_byte < end and start < e.end_byte})


def entities_in(rel: str, text: str) -> list[str]:
    return sorted({e.name for e in extract_file(rel, text)})


def resolve(rel: str, old: str | None, new: str | None, shas: list[str], blobs: Blobs,
            state: dict[tuple[str, str], bytes], key: tuple[str, str],
            stats: Counter) -> tuple[list[str], str]:
    """(symbol names, how) for one edit. `how` is empty when nothing resolved.

    `state` is the replayed file content per (session, path). The first version of this script had no
    such state and searched only committed blobs, which failed on the commonest thing an agent does:
    edit a function, then edit it again. Only the final state of that function was ever committed, so
    the earlier edit's `old` text existed in no blob and 24 of CodeNav's 52 unmatched edits were
    iterations rather than misses. Replaying gives every edit in a chain the file as it stood when
    that edit ran, which is the only state in which its `old` is findable.
    """
    if not old:                                  # Write, or an insertion at an empty anchor
        if new:
            state[key] = new.encode("utf-8", "replace")
        got = entities_in(rel, new or "")
        return (got, "written-body" if got else "")
    needle = old.encode("utf-8", "replace")

    content = state.get(key)
    how = "replayed"
    if content is None or needle not in content:
        how = "located-in-blob"
        content = None
        for sha in shas[:BLOB_BUDGET]:
            blob = blobs.get(f"{sha}:{rel}")
            if blob is not None and needle in blob:
                content = blob
                break

    if content is not None:
        at = content.find(needle)
        got = entities_covering(rel, content, at, at + len(needle))
        state[key] = content.replace(needle, (new or "").encode("utf-8", "replace"), 1)
        if got:
            return (got, how)
        stats["unmatched: located, but outside every entity span"] += 1
        return ([], "")

    got = entities_in(rel, new or "")
    if got:
        return (got, "fragment")
    if len(shas) > BLOB_BUDGET:
        stats["unmatched: blob budget exhausted"] += 1
    elif not shas:
        stats["unmatched: path never committed"] += 1
    else:
        stats["unmatched: old text in no blob and not in the replayed state"] += 1
    return ([], "")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("edits", type=Path)
    ap.add_argument("--repo", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    repo = args.repo.expanduser().resolve()
    out = args.out.expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)
    data = json.loads(args.edits.read_text())

    commits = path_commits(repo)
    blobs = Blobs(repo)
    stats: Counter = Counter()
    how: Counter = Counter()
    requests = []
    state: dict[tuple[str, str], bytes] = {}      # (session, path) -> content as replayed so far

    for req in data["requests"]:
        symbols: set[str] = set()
        details = []
        eligible_here = 0
        for ed in req["edits"]:
            units = ed["edits"] or [{"old_string": ed["old"], "new_string": ed["new"]}]
            raw = Path(ed["path"])
            try:
                rel = str(raw.resolve().relative_to(repo))
            except ValueError:
                stats["ineligible: outside the repo"] += len(units)
                continue
            if _language_for(rel) is None:
                stats["ineligible: no extractor for this language"] += len(units)
                continue
            for unit in units:
                stats["eligible"] += 1
                eligible_here += 1
                got, hw = resolve(rel, unit.get("old_string"), unit.get("new_string"),
                                  commits.get(rel, []), blobs,
                                  state, (req["session"] or "", rel), stats)
                if got:
                    stats["matched"] += 1
                    how[hw] += 1
                    ids = [f"{rel}::{n}" for n in got]
                    symbols.update(ids)
                    details.append({"path": rel, "how": hw, "symbols": ids})
                else:
                    stats["unmatched"] += 1
                    details.append({"path": rel, "how": "", "symbols": []})
        stats["requests with >=1 eligible edit"] += 1 if eligible_here else 0
        requests.append({"request_id": req["request_id"], "session": req["session"],
                         "ts": req["ts"], "text": " ".join(req["text"].split())[:300],
                         "n_edits": len(req["edits"]), "n_eligible": eligible_here,
                         "symbols": sorted(symbols), "edit_detail": details})
    blobs.close()

    eligible, matched = stats["eligible"], stats["matched"]
    rate = matched / eligible if eligible else 0.0
    report = {"edits_file": str(args.edits), "repo": str(repo),
              "match_rate": round(rate, 4), "gate": 0.70,
              "gate_passed": rate >= 0.70, "stats": dict(sorted(stats.items())),
              "how_matched": dict(how.most_common()),
              "n_requests_with_symbols": sum(1 for r in requests if r["symbols"]),
              "requests": requests}
    name = args.edits.stem.replace("edits-", "")
    (out / f"symbols-{name}.json").write_text(json.dumps(report, indent=1))

    print(f"{name}: {matched}/{eligible} eligible edits mapped to symbols = {rate:.1%}"
          f"  {'PASS' if rate >= 0.70 else 'BELOW THE 70% GATE — stop and diagnose'}")
    for k, v in sorted(stats.items()):
        print(f"    {k}: {v}")
    print(f"    how: {dict(how.most_common())}")
    print(f"    requests with >=1 symbol: {report['n_requests_with_symbols']} of {len(requests)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
