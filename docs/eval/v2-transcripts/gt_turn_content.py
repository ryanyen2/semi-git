#!/usr/bin/env python3
"""How much of WP-V2's ground truth is carried by turns that say nothing?

    python docs/eval/v2-transcripts/gt_turn_content.py symbols-<name>.json [...]

Coding eico's error sample (`codes-eico.json`) turned up the reason its numbers are the worst of the
four: the ground-truth clusters holding almost all of its mass are the turns `"resume"`,
`"move on until all Us done"`, `"start U3"`, `"ok sure"` and `"(a)"`. The transcript record attributes to
`"resume"` everything an autonomous agent then did over a long run, and the pre-registered metric treats
that footprint as one intent. So this measures the instrument, not sgt.

The rule is mechanical and fixed here rather than tuned per repo:

* a turn is **contentless** if its text, whitespace-collapsed, is <= 24 characters, or every one of its
  alphabetic words is in `CONTINUATION` below;
* the reported share is over ground-truth **positive pairs**, not turns, because one huge contentless
  turn distorts the metric far more than several small ones, and pairs are what the F1 is computed from.

Turns are counted only if they have >= 1 matched symbol (the ones that reach the metric at all).
"""
from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from itertools import combinations
from pathlib import Path

CONTINUATION = {
    "resume", "resu", "e", "continue", "go", "on", "ok", "okay", "sure", "yep", "yes", "yeah",
    "next", "move", "until", "all", "us", "done", "donw", "lets", "let", "do", "it", "dot",
    "start", "again", "stopped", "stucked", "stuck", "kept", "stopping", "a", "so", "now",
}


def contentless(text: str) -> bool:
    flat = " ".join((text or "").split())
    if len(flat) <= 24:
        return True
    words = [w.lower() for w in re.findall(r"[A-Za-z]+", flat)]
    return bool(words) and all(w in CONTINUATION for w in words)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("symbols", type=Path, nargs="+")
    args = ap.parse_args()

    print(f"{'repo':16s} {'turns':>6s} {'contentless':>12s} {'their pair share':>17s} "
          f"{'biggest turn':>13s}  biggest turn's text")
    for path in args.symbols:
        data = json.loads(path.read_text())
        requests = [r for r in data["requests"] if r["symbols"]]
        pairs_of: dict[str, set[tuple[str, str]]] = {}
        for req in requests:
            pairs_of[req["request_id"]] = set(combinations(sorted(set(req["symbols"])), 2))
        all_pairs: set[tuple[str, str]] = set()
        for p in pairs_of.values():
            all_pairs |= p
        dead = {r["request_id"] for r in requests if contentless(r.get("text") or "")}
        dead_pairs: set[tuple[str, str]] = set()
        for rid in dead:
            dead_pairs |= pairs_of[rid]
        biggest = max(requests, key=lambda r: len(pairs_of[r["request_id"]]))
        share = len(dead_pairs) / len(all_pairs) if all_pairs else 0.0
        big_share = len(pairs_of[biggest["request_id"]]) / len(all_pairs) if all_pairs else 0.0
        text = " ".join((biggest.get("text") or "").split())[:46]
        print(f"{path.stem.replace('symbols-',''):16s} {len(requests):6d} "
              f"{len(dead):5d} ({len(dead)/len(requests):3.0%}) "
              f"{share:16.1%} {big_share:12.1%}  {text!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
