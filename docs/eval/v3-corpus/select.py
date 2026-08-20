"""Freeze the WP-V3 repo selection. Run once; the output is the pre-registered list.

Protocol (plan WP-V3, "Selection protocol (fixed before running, per R3)"):
    GitHub search, language:Python, stars 100-5000, pushed within 12 months, not a fork,
    license permits analysis. Order by the seeded shuffle (seed 20260814) of the first 200
    hits; take the first 30 that clone successfully; log every skip.

Two places the plan is under-specified, decided here and recorded rather than left implicit:

1. *"the first 200 hits" of what order.* GitHub's default is best-match, which is not
   reproducible -- it moves with their relevance model. The first attempt pinned
   `sort=stars, order=desc`, which is reproducible but wrong: it returned 200 repos all
   between 4400 and 5000 stars, i.e. it collapsed the declared `100..5000` population to its
   top 3%. So the 200 hits are drawn as 40 from each of five star bands (100-200, 200-500,
   500-1000, 1000-2500, 2500-5000), each band pinned to `sort=stars, order=desc`. Still
   deterministic, still 200 hits, but now spanning the range the filter says it spans. This is
   a fix to the *sampling*, made before any repo was cloned or measured -- no outcome was
   visible when it was made.
2. *"license permits analysis."* Any OSI license permits reading source. The real exclusion
   is a repo with no license at all (all rights reserved), so the filter here is
   `license is not null`, and each repo's license key is recorded so a stricter reading can
   be re-applied later without re-querying.

Cloning is NOT done here. This script freezes the candidate order; the clone-and-run harness
takes the first 30 that clone and logs every skip, so the shuffled 200 must exist on disk
first or "the first 30 that clone" is not a checkable claim.

Known and deliberately NOT filtered: `language:Python` is dominant-language-by-bytes, so the
candidates include repos that are not really Python codebases (awesome-lists, prompt/skill
collections with three .py files). Excluding them here would be choosing the corpus after
seeing it. They stay in, and the per-repo harness records Python file counts so the write-up
can report "n of 30 were not substantially Python" as a finding about the selection protocol.
"""

from __future__ import annotations

import json
import random
import subprocess
import sys
from pathlib import Path

SEED = 20260814
HITS = 200
BANDS = ["100..200", "200..500", "500..1000", "1000..2500", "2500..5000"]
PER_BAND = HITS // len(BANDS)
PUSHED_SINCE = "2025-08-16"  # 12 months before the run date, 2026-08-16
OUT = Path(__file__).parent / "selection.json"


def query_for(band: str) -> str:
    return f"language:Python stars:{band} pushed:>={PUSHED_SINCE} fork:false"


def search() -> list[dict]:
    """PER_BAND results from each star band, in a pinned order."""
    out = []
    for band in BANDS:
        raw = subprocess.run(
            [
                "gh", "api", "-X", "GET", "search/repositories",
                "-f", f"q={query_for(band)}",
                "-f", "sort=stars",
                "-f", "order=desc",
                "-f", f"per_page={PER_BAND}",
                "-f", "page=1",
            ],
            capture_output=True, text=True, check=True,
        ).stdout
        items = json.loads(raw)["items"]
        for it in items:
            it["_band"] = band
        out.extend(items[:PER_BAND])
    return out


def main() -> int:
    items = search()
    if len(items) < HITS:
        print(f"only {len(items)} hits, expected {HITS} -- the filters or the API changed", file=sys.stderr)

    kept, dropped = [], []
    for it in items:
        rec = {
            "full_name": it["full_name"],
            "clone_url": it["clone_url"],
            "stars": it["stargazers_count"],
            "band": it["_band"],
            "license": (it.get("license") or {}).get("spdx_id"),
            "pushed_at": it["pushed_at"],
            "size_kb": it["size"],
        }
        if rec["license"] in (None, "NOASSERTION"):
            dropped.append({**rec, "why": "no license -- all rights reserved"})
        else:
            kept.append(rec)

    order = list(range(len(kept)))
    random.Random(SEED).shuffle(order)
    shuffled = [kept[i] for i in order]

    OUT.write_text(json.dumps({
        "queries": [query_for(b) for b in BANDS],
        "sort": "stars desc within each band (pinned; see module docstring)",
        "bands": BANDS,
        "per_band": PER_BAND,
        "seed": SEED,
        "hits_requested": HITS,
        "hits_returned": len(items),
        "dropped_no_license": dropped,
        "candidates": shuffled,
    }, indent=2) + "\n")
    print(f"{len(items)} hits, {len(dropped)} dropped for license, {len(shuffled)} candidates -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
