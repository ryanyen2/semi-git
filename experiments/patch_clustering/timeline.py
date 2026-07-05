"""Represent the version history as feature lanes over commits — the deliverable.

Ties the pieces together: cluster HEAD entities into features (Leiden-CPM, fused signals),
map every commit's entity-patches onto those lanes, derive each lane's birth / last-touch /
activity, label the significant lanes with gpt-5.4-mini (fed the commit subjects that touched
them, so intent binds what co-change can't), and render a git-log-style lane grid.

    .venv/bin/python experiments/patch_clustering/timeline.py [gamma]
"""

from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from experiments.patch_clustering.label import Labeler  # noqa: E402
from experiments.patch_clustering.leiden_cluster import (  # noqa: E402
    _leiden, _signals, scope_edges,
)

_OUT = Path(__file__).resolve().parent / "out"

MIN_LANE_SIZE = 4     # a lane must own at least this many HEAD entities to be "significant"
MAX_LANES_SHOWN = 14  # widest the ASCII grid gets; the rest are summarized as a tail


def _dominant_dir(members: list[str]) -> str:
    def prefix(eid: str) -> str:
        parts = eid.split("::", 1)[0].split("/")
        return "/".join(parts[:2]) if len(parts) >= 2 else parts[0]
    return Counter(prefix(m) for m in members).most_common(1)[0][0]


def build(data: dict, repo: Path, gamma: float) -> dict:
    head_entities, hubs, _cut, cochange, structural = _signals(data, repo)
    scope = scope_edges(data, head_entities, hubs)
    # all-three fusion: structural = recall backbone, co-change + scope = cross-cutting alignment
    fused: dict = {}
    for d in (structural, cochange, scope):
        for k, v in d.items():
            fused[k] = fused.get(k, 0.0) + v
    clusters = _leiden(sorted(head_entities), fused, gamma)

    ent2lane: dict[str, str] = {}
    lanes: dict[str, dict] = {}
    for c in clusters:
        if len(c) < MIN_LANE_SIZE:
            continue
        lane_id = f"L{len(lanes)}"
        lanes[lane_id] = {"members": sorted(c), "dir": _dominant_dir(c), "size": len(c)}
        for e in c:
            ent2lane[e] = lane_id

    commits = data["commits"]
    change_sets = {int(k): v for k, v in data["change_sets"].items()}

    # per-commit lane activity + per-lane birth/last/subjects
    commit_lanes: dict[int, set[str]] = {}
    for o in range(len(commits)):
        touched = {ent2lane[e] for e in change_sets.get(o, []) if e in ent2lane}
        commit_lanes[o] = touched
        for lane in touched:
            L = lanes[lane]
            L.setdefault("birth", o)
            L["last"] = o
            L.setdefault("commits", []).append(o)
            L.setdefault("subjects", []).append(commits[o]["subject"])

    # label significant lanes (cheap, cached); intent context = the subjects that touched them
    labeler = Labeler()
    for lane, L in lanes.items():
        if L.get("commits"):
            fl = labeler.label(L["members"], subjects=L.get("subjects"))
            L["label"], L["why"] = fl.label, fl.rationale
        else:
            L["label"], L["why"] = "(untouched at HEAD)", ""
    labeler.save()

    return {"gamma": gamma, "lanes": lanes, "commit_lanes": {k: sorted(v) for k, v in commit_lanes.items()},
            "commits": commits, "cost": labeler.cost_line()}


def _render(result: dict) -> None:
    lanes = result["lanes"]
    commits = result["commits"]
    commit_lanes = {int(k): v for k, v in result["commit_lanes"].items()}

    active = [lid for lid in lanes if lanes[lid].get("commits")]
    active.sort(key=lambda l: -len(lanes[l]["commits"]))
    shown = active[:MAX_LANES_SHOWN]

    print(f"gamma={result['gamma']}   significant lanes: {len(active)} "
          f"(showing top {len(shown)} by activity)\n")
    print("legend:")
    for i, lid in enumerate(shown):
        L = lanes[lid]
        print(f"  {i:2d} {L['label']:28s} [{L['dir']}, {L['size']} entities, "
              f"commits {L['birth']}..{L['last']}]  — {L['why'][:60]}")

    print("\ntimeline (row = commit; column = lane index above; █ = lane touched):")
    header = "        " + "".join(f"{i%10}" for i in range(len(shown)))
    print(header)
    for o in range(len(commits)):
        cells = "".join("█" if lid in commit_lanes.get(o, []) else "·" for lid in shown)
        dead = "†" if not commit_lanes.get(o) else " "
        subj = commits[o]["subject"][:40]
        print(f"  {o:2d} {commits[o]['short']} {cells} {dead} {subj}")

    covered = sum(1 for o in range(len(commits)) if commit_lanes.get(o))
    print(f"\ncoverage: {covered}/{len(commits)} commits touch a significant lane "
          f"(† = only churned code that didn't survive to HEAD, or tiny lanes)")
    print(result["cost"])


if __name__ == "__main__":
    gamma = float(sys.argv[1]) if len(sys.argv) > 1 else 0.02
    data = json.loads((_OUT / "patches.json").read_text(encoding="utf-8"))
    result = build(data, _REPO_ROOT, gamma)
    _render(result)
    (_OUT / "timeline.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"\nwrote {_OUT / 'timeline.json'}")
