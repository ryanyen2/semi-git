"""Turn the (commit x feature) grid into a typed-operation DAG — the missing 'operation axis'.

The clustering (hierarchy.py) gave us feature lenses. This adds the two things that make it a
DAG of *semantic operations* rather than a binary touched-grid:

  1. TYPED operations. The miner already tagged every entity change added/modified/removed
     (patches.json), but the grid threw that away. Here each (commit, lane) becomes ONE typed
     operation by aggregating its entity changes:
        born      first time a lane appears (adds)     reworked  mostly modifications
        extended  net-new entities added               pruned    mostly removals
        reverted  commit subject says so               touched   fallback
  2. DEPENDENCY edges (directed).
        temporal  op builds on the previous op of the same lane (the lane's own history)
        structural  lane A depends on lane B when A's entities call/import B's (reduced edges)

Deterministic, no LLM. Reads hierarchy.json (lane membership + labels) + patches.json (typed
patches) + the entity graph at HEAD (dependency direction); writes operations.json.

    .venv/bin/python experiments/patch_clustering/operations.py
"""

from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from sgt.entities.graph import build_entity_graph  # noqa: E402
from sgt.store.gitbind import GitBinding  # noqa: E402

_OUT = Path(__file__).resolve().parent / "out"
MAX_DEPENDS = 6  # top-k directed dependencies to keep per lane


def _op_type(order: int, birth: int, a: int, m: int, r: int, subject: str) -> str:
    if "revert" in subject.lower():
        return "reverted"
    if order == birth and a > 0:
        return "born"
    if a > 0 and a >= m and a >= r:
        return "extended"
    if r > 0 and r >= m and r >= a:
        return "pruned"
    if m > 0:
        return "reworked"
    return "touched"


def _final_type(base: str, kinds: set[str]) -> str:
    """Lifecycle events outrank the add/mod/remove base type: a split/merge/death is *what happened*
    to the lane in that commit, regardless of the raw churn counts."""
    if "split" in kinds:
        return "split"
    if "merge" in kinds:
        return "merge"
    if "death" in kinds:
        return "died"
    return base


def build(repo: Path) -> dict:
    h = json.loads((_OUT / "hierarchy.json").read_text(encoding="utf-8"))
    p = json.loads((_OUT / "patches.json").read_text(encoding="utf-8"))
    lanes = h["lanes"]
    commits = h["commits"]
    subjects = {c["order"]: c["subject"] for c in commits}

    ent2lane: dict[str, str] = {}
    for lid, L in lanes.items():
        for e in L["members"]:
            ent2lane[e] = lid

    # aggregate typed entity changes into per-(commit, lane) counts
    counts: dict[tuple[int, str], Counter] = defaultdict(Counter)
    for pt in p["patches"]:
        lid = ent2lane.get(pt["entity_id"])
        if lid is not None:
            counts[(pt["order"], lid)][pt["change"]] += 1

    # --- lifecycle attribution (split / merge / death from the miner) ---
    # A dead entity is not a HEAD lane member, so we route it to the lane that OWNS its file (by
    # plurality of surviving members). A file that is entirely gone (a removed subsystem) owns
    # nothing, so its deaths stay commit-level only — the honest picture of a mass removal.
    file_owner: dict[str, str] = {}
    owner_votes: dict[str, Counter] = defaultdict(Counter)
    for lid, L in lanes.items():
        for e in L["members"]:
            owner_votes[e.split("::", 1)[0]][lid] += 1
    for f, votes in owner_votes.items():
        file_owner[f] = votes.most_common(1)[0][0]
    ent_file = {pt["entity_id"]: pt["file"] for pt in p["patches"]}

    def _home_lane(eid: str) -> str | None:
        return ent2lane.get(eid) or file_owner.get(ent_file.get(eid, ""))

    lane_events: dict[tuple[int, str], list[dict]] = defaultdict(list)
    death_by_commit: Counter = Counter()
    attributed_deaths = 0
    for e in p.get("lifecycle", []):
        if e["type"] == "death":
            death_by_commit[e["order"]] += 1
            lid = _home_lane(e["entity"])
            if lid:
                lane_events[(e["order"], lid)].append(e)
                attributed_deaths += 1
        else:  # split / merge — route to the lane of the surviving side
            survivor = e["to"] if e["type"] == "merge" else e["to"][0]
            lid = _home_lane(survivor) or _home_lane(e["from"][0] if e["type"] == "merge" else e["from"])
            if lid:
                lane_events[(e["order"], lid)].append(e)

    lane_births = {lid: min(L["commits"]) for lid, L in lanes.items() if L.get("commits")}

    # per-lane typed operation sequence (temporal build-on is the sequence order). A lane's history
    # spans the commits that touched its members UNION the commits that killed/split entities in its
    # territory — so a death shows even in a commit that touched no surviving member.
    op_counts: Counter = Counter()
    for lid, L in lanes.items():
        orders = sorted(set(L.get("commits", [])) | {o for (o, l) in lane_events if l == lid})
        ops = []
        for o in orders:
            c = counts[(o, lid)]
            a, m, r = c["added"], c["modified"], c["removed"]
            evs = lane_events.get((o, lid), [])
            kinds = {ev["type"] for ev in evs}
            t = _final_type(_op_type(o, lane_births.get(lid, o), a, m, r, subjects.get(o, "")), kinds)
            op = {"order": o, "type": t, "added": a, "modified": m, "removed": r}
            deaths = sum(1 for ev in evs if ev["type"] == "death")
            if deaths:
                op["deaths"] = deaths
            for ev in evs:
                if ev["type"] in ("split", "merge"):
                    op["reshape"] = {"type": ev["type"], "from": ev["from"], "to": ev["to"]}
            ops.append(op)
            op_counts[t] += 1
        L["ops"] = ops

    # directed structural dependency at the lane level (reduced calls/imports edges)
    head = GitBinding(repo).head()
    graph = build_entity_graph(GitBinding(repo).tree_at(head))
    dep: dict[str, Counter] = defaultdict(Counter)
    edge_total = 0
    for e in graph.reduced_edges:
        if e.type == "contains":
            continue
        la_, lb_ = ent2lane.get(e.src), ent2lane.get(e.dst)
        if la_ and lb_ and la_ != lb_:
            dep[la_][lb_] += 1
            edge_total += 1
    for lid, L in lanes.items():
        L["depends"] = [{"lane": b, "w": w} for b, w in dep[lid].most_common(MAX_DEPENDS)]

    # subsystem-level dependency (roll lane deps up to their subsystems)
    lane_super = {lid: L["super"] for lid, L in lanes.items()}
    sdep: dict[str, Counter] = defaultdict(Counter)
    for lid, L in lanes.items():
        for d in L["depends"]:
            sa, sb = lane_super[lid], lane_super[d["lane"]]
            if sa != sb:
                sdep[sa][sb] += d["w"]
    for S in h["supers"]:
        S["depends"] = [{"super": b, "w": w} for b, w in sdep[S["id"]].most_common(MAX_DEPENDS)]

    lc_all = p.get("lifecycle", [])
    h["op_types"] = dict(op_counts)
    h["dep_edges"] = edge_total
    h["lifecycle"] = {
        "deaths": sum(1 for e in lc_all if e["type"] == "death"),
        "attributed_deaths": attributed_deaths,
        "splits": sum(1 for e in lc_all if e["type"] == "split"),
        "merges": sum(1 for e in lc_all if e["type"] == "merge"),
        "death_by_commit": {str(o): n for o, n in sorted(death_by_commit.items())},
    }
    return h


def _summary(h: dict) -> None:
    print("typed operations (was 1 undifferentiated 'touched'):")
    order = ["born", "extended", "reworked", "pruned", "split", "merge", "died", "reverted", "touched"]
    total = sum(h["op_types"].values())
    for t in order:
        n = h["op_types"].get(t, 0)
        if n:
            print(f"  {t:9s} {n:4d}  {'#' * round(40 * n / total)}")
    lc = h.get("lifecycle", {})
    print(f"  total operations: {total}   directed dependency edges: {h['dep_edges']}")
    print(f"  lifecycle: {lc.get('deaths', 0)} deaths ({lc.get('attributed_deaths', 0)} attributed to a lane), "
          f"{lc.get('splits', 0)} splits, {lc.get('merges', 0)} merges\n")

    # show a couple of lane op-sequences + their dependencies
    lanes = h["lanes"]
    busy = sorted(lanes, key=lambda l: -len(lanes[l].get("ops", [])))[:3]
    glyph = {"born": "◆", "extended": "+", "reworked": "~", "pruned": "-", "split": "⋔",
             "merge": "⋈", "died": "✝", "reverted": "↺", "touched": "·"}
    for lid in busy:
        L = lanes[lid]
        seq = " ".join(f"{glyph[o['type']]}{o['order']}" for o in L["ops"])
        deps = ", ".join(lanes[d["lane"]]["label"] for d in L["depends"][:4]) or "—"
        print(f"  «{L['label']}»")
        print(f"     history: {seq}")
        print(f"     depends on: {deps}")


if __name__ == "__main__":
    h = build(_REPO_ROOT)
    _summary(h)
    (_OUT / "operations.json").write_text(json.dumps(h, indent=2), encoding="utf-8")
    print(f"\nwrote {_OUT / 'operations.json'}")
