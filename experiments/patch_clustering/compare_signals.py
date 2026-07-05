"""Compare coupling signals offline (free, no LLM) to decide what best defines features.

Headline question from the first run: co-change is too sparse on a young repo — can the
author's conventional-commit *scope* (intent) do better? This scores four signal sets on four
metrics so we pay for LLM labels only on the winner:

  lanes      : # significant lanes (size >= MIN) — want feature-count, not 1 or 200
  coverage   : commits touching a significant lane — want high (history is represented)
  code+test  : share of lanes binding sgt code WITH its tests — cross-cutting feature evidence
  scope-align: mean dominant-scope share within a lane — do lanes match author intent?
  purity     : mean dominant-folder share — 1.0 means we merely recovered folders
"""

from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from experiments.patch_clustering.leiden_cluster import (  # noqa: E402
    _leiden, _signals, commit_scope, scope_edges,
)

_OUT = Path(__file__).resolve().parent / "out"
MIN_LANE = 4
GAMMAS = [0.02, 0.05]


def _fuse(*dicts: dict) -> dict:
    out: dict = defaultdict(float)
    for d in dicts:
        for k, v in d.items():
            out[k] += v
    return dict(out)


def _dir2(eid: str) -> str:
    p = eid.split("::", 1)[0].split("/")
    return "/".join(p[:2]) if len(p) >= 2 else p[0]


def _top(eid: str) -> str:
    return eid.split("/")[0]


def _metrics(nodes, weights, gamma, ent_scopes, n_commits, change_sets, ent2commits):
    clusters = [c for c in _leiden(nodes, weights, gamma) if len(c) >= MIN_LANE]
    if not clusters:
        return None
    # coverage
    ent2lane = {e: i for i, c in enumerate(clusters) for e in c}
    covered = sum(1 for o in range(n_commits)
                  if any(e in ent2lane for e in change_sets.get(o, [])))
    # code+test binding
    bind = sum(1 for c in clusters if {"sgt", "tests"} <= {_top(m) for m in c})
    # scope alignment + folder purity
    scope_shares, purities = [], []
    for c in clusters:
        scopes: Counter = Counter()
        for m in c:
            scopes.update(ent_scopes.get(m, []))
        if scopes:
            scope_shares.append(scopes.most_common(1)[0][1] / sum(scopes.values()))
        dirs = Counter(_dir2(m) for m in c)
        purities.append(dirs.most_common(1)[0][1] / len(c))
    return {
        "lanes": len(clusters),
        "coverage": f"{covered}/{n_commits}",
        "code+test": f"{bind}/{len(clusters)}",
        "scope-align": round(sum(scope_shares) / len(scope_shares), 2) if scope_shares else 0,
        "purity": round(sum(purities) / len(purities), 2),
    }


def main() -> None:
    data = json.loads((_OUT / "patches.json").read_text(encoding="utf-8"))
    head_entities, hubs, _cut, cochange, structural = _signals(data, _REPO_ROOT)
    scope = scope_edges(data, head_entities, hubs)
    nodes = sorted(head_entities)
    change_sets = {int(k): v for k, v in data["change_sets"].items()}
    n_commits = len(data["commits"])

    # per-entity scope memberships (for scope-alignment metric)
    ent_scopes: dict[str, list[str]] = defaultdict(list)
    ent2commits: dict[str, list[int]] = defaultdict(list)
    for o, c in enumerate(data["commits"]):
        s = commit_scope(c["subject"])
        for e in change_sets.get(o, []):
            ent2commits[e].append(o)
            if s:
                ent_scopes[e].append(s)

    signal_sets = {
        "structural": structural,
        "cochange": cochange,
        "scope": scope,
        "struct+scope": _fuse(structural, scope),
        "all-three": _fuse(structural, cochange, scope),
    }
    print(f"HEAD entities {len(nodes)} | struct {len(structural)} co-change {len(cochange)} "
          f"scope {len(scope)} edges | distinct scopes "
          f"{len({commit_scope(c['subject']) for c in data['commits']} - {None})}\n")
    hdr = f"{'signal':14s} {'gamma':6s} {'lanes':6s} {'coverage':9s} {'code+test':10s} {'scope-align':12s} purity"
    print(hdr)
    print("-" * len(hdr))
    for name, w in signal_sets.items():
        for gamma in GAMMAS:
            m = _metrics(nodes, w, gamma, ent_scopes, n_commits, change_sets, ent2commits)
            if m:
                print(f"{name:14s} {gamma:<6} {m['lanes']:<6} {m['coverage']:<9} "
                      f"{m['code+test']:<10} {m['scope-align']:<12} {m['purity']}")


if __name__ == "__main__":
    main()
