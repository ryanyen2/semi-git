"""Capability clustering: group features into labeled areas with identity that survives change.

Clusters are groupings of *features* (each owns entities), so a cluster's identity must not
ride on any single member node — reverting or reconciling one member would otherwise flip or
vanish the cluster (the AE2 no-flicker / AE5 retire-replace cases). Instead, identity is a
persisted id matched across re-clustering by membership overlap (Jaccard): a group that still
overlaps a prior cluster keeps its id and label; a genuinely new group mints a fresh one.

The grouping itself is deterministic (union-find over an adjacency relation the caller supplies
— feature dependencies + co-file ownership). Labeling has an LLM seam that degrades to a
deterministic label offline, mirroring ``_default_clusterer`` in ``sgt/orchestrate/sync.py``.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

_OVERLAP_REUSE = 0.5  # Jaccard >= this against a prior cluster -> reuse its id + label
_STORE = "clusters.json"


@dataclass(frozen=True)
class Cluster:
    cluster_id: str
    label: str
    members: list[str]  # feature node ids, sorted

    def to_dict(self) -> dict:
        return {"cluster_id": self.cluster_id, "label": self.label, "members": list(self.members)}


def _groups(members: list[str], adjacency: set[frozenset]) -> list[list[str]]:
    """Union-find over members using the adjacency relation; sorted, deterministic."""
    parent = {m: m for m in members}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for pair in adjacency:
        a, b = tuple(pair) if len(pair) == 2 else (next(iter(pair)), next(iter(pair)))
        if a in parent and b in parent:
            parent[find(a)] = find(b)

    groups: dict[str, list[str]] = {}
    for m in members:
        groups.setdefault(find(m), []).append(m)
    return [sorted(g) for g in sorted(groups.values(), key=lambda g: sorted(g)[0])]


def _stable_id(members: list[str]) -> str:
    digest = hashlib.sha1("\x00".join(sorted(members)).encode("utf-8")).hexdigest()
    return f"c-{digest[:8]}"


def _default_label(members: list[str]) -> str:
    """Deterministic offline label — named after the lexically-first member feature."""
    return f"capability:{sorted(members)[0]}" if members else "capability"


def cluster_features(
    members: list[str],
    adjacency: set[frozenset],
    prior: dict | None = None,
    label_fn: Callable[[list[str]], str] | None = None,
) -> list[Cluster]:
    """Group ``members`` and assign overlap-stable ids/labels against ``prior``.

    ``prior`` is a previously-saved store dict (``{"clusters": [...]}``); a new group reuses a
    prior cluster's id/label when their membership Jaccard >= 0.5, else mints a stable id and a
    label via ``label_fn`` (default: deterministic, offline).
    """
    label_fn = label_fn or _default_label
    prior_clusters = (prior or {}).get("clusters", [])
    used: set[str] = set()
    out: list[Cluster] = []
    for group in _groups(members, adjacency):
        gs = set(group)
        best, best_j = None, 0.0
        for pc in prior_clusters:
            if pc["cluster_id"] in used:
                continue
            pm = set(pc["members"])
            union = gs | pm
            j = len(gs & pm) / len(union) if union else 0.0
            if j > best_j:
                best, best_j = pc, j
        if best is not None and best_j >= _OVERLAP_REUSE:
            cid, label = best["cluster_id"], best["label"]
            used.add(cid)
        else:
            cid, label = _stable_id(group), label_fn(group)
        out.append(Cluster(cluster_id=cid, label=label, members=group))
    return out


def refresh_clusters(project, label_fn: Callable[[list[str]], str] | None = None) -> list[Cluster]:
    """Recompute a project's clusters and persist them — the stamping/relabeling entry point.

    Separate from the read projection so reads stay pure: this is where membership-change
    identity stabilization is committed and where the LLM labeling seam (``label_fn``) plugs in,
    degrading to deterministic labels offline. Best-effort callers should guard this — it imports
    the optional ``entities`` extra.
    """
    from sgt.api import entity_graph_view

    clusters = []
    for c in entity_graph_view(project).get("clusters", []):
        label = label_fn(c["members"]) if label_fn else c["label"]
        clusters.append(Cluster(c["cluster_id"], label, c["members"]))
    save_cluster_store(project.sgt_dir, clusters)
    return clusters


def load_cluster_store(sgt_dir: Path) -> dict:
    path = Path(sgt_dir) / _STORE
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_cluster_store(sgt_dir: Path, clusters: list[Cluster]) -> None:
    path = Path(sgt_dir) / _STORE
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"clusters": [c.to_dict() for c in clusters]}
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
