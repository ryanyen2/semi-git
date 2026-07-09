"""Durable user pins over the feature tree (plan U12/U13, R16/R17): `must_link`/`cannot_link`
pairs and `assign` (member -> feature id) constraints that steer every clustering run, plus
`labels` (feature id -> user-chosen label, U13) that a rename verb writes and `tree.label_tree`
applies as a final override pass. Persisted in the committed `.sgt/pins/pins.json` -- mirrors
`sgt.config.load_identity_constraints`'s shape (frozen dataclass, empty default, plain JSON,
order-independent pairs).

Two members `assign`ed to the same feature id are, by construction, must-linked to each other --
no separate "anchor node" bookkeeping needed (plan D3). Must-link (explicit + assign-derived) is
realized as **graph contraction** before every Leiden call: a union-find group becomes one
synthetic vertex, expanded back afterward (`apply_must_link`/`_expand_members`). Cannot-link is
enforced **after** clustering (`enforce_cannot_link`): a violated pair has its later-sorted member
moved to its next-best *other* leaf by adjacency weight. `labels` is not a clustering constraint
-- the contradiction/must-link machinery below ignores it entirely.

Pin sets can be contradictory (a must-link/cannot-link pair, or a must-link closure spanning two
different `assign` targets); `find_contradictions` reports them structurally and never raises --
the caller (U13's feature verbs) decides whether to refuse or proceed with a warning.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Pins:
    assign: dict[str, str] = field(default_factory=dict)  # member id -> feature id
    must_link: frozenset[tuple[str, str]] = frozenset()  # sorted pairs, order-independent
    cannot_link: frozenset[tuple[str, str]] = frozenset()
    labels: dict[str, str] = field(default_factory=dict)  # feature id -> user-chosen label


def _pins_path(repo_path: str | Path = ".") -> Path:
    return Path(repo_path) / ".sgt" / "pins" / "pins.json"


def load_pins(repo_path: str | Path = ".") -> Pins:
    """Empty (never absent-as-None) if the file doesn't exist -- every caller treats "no pins" the
    same as "empty pins", matching `load_identity_constraints`'s discipline."""
    path = _pins_path(repo_path)
    if not path.is_file():
        return Pins()
    payload = json.loads(path.read_text(encoding="utf-8"))
    return Pins(
        assign=dict(payload.get("assign", {})),
        must_link=frozenset(tuple(sorted(pair)) for pair in payload.get("must_link", [])),
        cannot_link=frozenset(tuple(sorted(pair)) for pair in payload.get("cannot_link", [])),
        labels=dict(payload.get("labels", {})),
    )


def save_pins(repo_path: str | Path, pins: Pins) -> None:
    """Write `.sgt/pins/pins.json` -- committed, team-shared, current-state (not an append log):
    re-pinning a member simply overwrites its entry, so "latest-wins" is just "whatever the file
    currently says." """
    path = _pins_path(repo_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "assign": dict(sorted(pins.assign.items())),
        "must_link": sorted([list(pair) for pair in pins.must_link]),
        "cannot_link": sorted([list(pair) for pair in pins.cannot_link]),
        "labels": dict(sorted(pins.labels.items())),
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


class _UnionFind:
    def __init__(self) -> None:
        self._parent: dict[str, str] = {}

    def find(self, x: str) -> str:
        self._parent.setdefault(x, x)
        root = x
        while self._parent[root] != root:
            root = self._parent[root]
        while self._parent[x] != root:  # path compression
            self._parent[x], x = root, self._parent[x]
        return root

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self._parent[ra] = rb


@dataclass(frozen=True)
class Contradiction:
    kind: str  # "must_and_cannot_link" | "assign_conflict_in_must_link_group" | "cannot_link_within_must_link_group"
    members: tuple[str, ...]
    detail: str


def _must_link_groups(pins: Pins) -> dict[str, set[str]]:
    """Union-find closure of explicit `must_link` pairs plus assign-derived must-link (members
    sharing the same `assign` target are implicitly must-linked to each other)."""
    uf = _UnionFind()
    all_members: set[str] = set()
    for a, b in pins.must_link:
        uf.union(a, b)
        all_members |= {a, b}

    by_feature: dict[str, list[str]] = defaultdict(list)
    for member, feature_id in pins.assign.items():
        by_feature[feature_id].append(member)
        all_members.add(member)
    for members in by_feature.values():
        for m in members[1:]:
            uf.union(members[0], m)

    groups: dict[str, set[str]] = defaultdict(set)
    for m in all_members:
        groups[uf.find(m)].add(m)
    return groups


def find_contradictions(pins: Pins) -> list[Contradiction]:
    """Every contradictory pin fact, never raising. Ordinary re-pinning (overwriting a member's
    `assign` target) is not a contradiction -- only genuine simultaneous conflicts are:
    (a) the same pair in both `must_link` and `cannot_link`; (b) a must-link closure group whose
    members carry two different explicit `assign` targets; (c) a `cannot_link` pair that
    transitively collapses into the same must-link group via other constraints."""
    out: list[Contradiction] = []

    both = pins.must_link & pins.cannot_link
    for pair in sorted(both):
        out.append(Contradiction(
            "must_and_cannot_link", pair, f"{pair[0]} and {pair[1]} are both must-link and cannot-link",
        ))

    groups = _must_link_groups(pins)

    for members in groups.values():
        targets = {pins.assign[m] for m in members if m in pins.assign}
        if len(targets) > 1:
            out.append(Contradiction(
                "assign_conflict_in_must_link_group", tuple(sorted(members)),
                f"members {sorted(members)} are must-linked but assigned to different features: {sorted(targets)}",
            ))

    member_group = {m: root for root, members in groups.items() for m in members}
    for a, b in sorted(pins.cannot_link):
        if (a, b) in both:
            continue  # already reported as (a)
        if a in member_group and b in member_group and member_group[a] == member_group[b]:
            out.append(Contradiction(
                "cannot_link_within_must_link_group", (a, b),
                f"{a} and {b} are cannot-link but transitively must-linked via other constraints",
            ))

    return out


def apply_must_link(
    nodes: list[str], fused: dict[frozenset, float], pins: Pins,
) -> tuple[list[str], dict[frozenset, float], dict[str, frozenset[str]]]:
    """Contract every must-link group present in `nodes` into one synthetic vertex before
    clustering. Returns ``(contracted_nodes, contracted_edges, expansion)`` where `expansion` maps
    each synthetic id back to its real members (`_expand_members` reverses this on a built tree)."""
    groups = _must_link_groups(pins)
    node_set = set(nodes)

    contraction: dict[str, str] = {}
    expansion: dict[str, frozenset[str]] = {}
    for members in groups.values():
        present = sorted(m for m in members if m in node_set)
        if len(present) < 2:
            continue
        synthetic = f"\x00pin::{present[0]}"
        expansion[synthetic] = frozenset(present)
        for m in present:
            contraction[m] = synthetic

    new_nodes = sorted({contraction.get(n, n) for n in nodes})
    new_edges: dict[frozenset, float] = defaultdict(float)
    for pair, w in fused.items():
        a, b = tuple(pair)
        if a not in node_set or b not in node_set:
            continue
        ca, cb = contraction.get(a, a), contraction.get(b, b)
        if ca == cb:
            continue  # inner edge of a contracted group -- dropped, not double counted
        new_edges[frozenset((ca, cb))] += w

    return new_nodes, dict(new_edges), expansion


def _expand_members(node: dict, expansion: dict[str, frozenset[str]]) -> None:
    """Replace any synthetic pin-group id in `node`'s (and every descendant's) `members` with its
    real members, in place -- the inverse of `apply_must_link`'s contraction."""
    expanded: list[str] = []
    for m in node["members"]:
        expanded.extend(expansion.get(m, (m,)))
    node["members"] = sorted(expanded)
    node["size"] = len(node["members"])
    for child in node.get("children", []):
        _expand_members(child, expansion)


def enforce_cannot_link(
    nodes: dict[str, dict], pins: Pins, adj: dict[str, list[tuple[str, float]]],
) -> list[str]:
    """Move the later-sorted member of any violated `cannot_link` pair to its next-best *other*
    leaf by adjacency weight, mutating leaf `members`/`size` in place. Ancestor `members` rollups
    are not re-synced -- no consumer reads them for correctness yet (U13's `map_view` will need
    to re-derive rollups from leaves regardless, once it exists). Returns a human-readable log of
    every move made."""
    member_leaf = {m: nid for nid, nd in nodes.items() if not nd["children"] for m in nd["members"]}
    moves: list[str] = []

    for a, b in sorted(pins.cannot_link):
        leaf_a, leaf_b = member_leaf.get(a), member_leaf.get(b)
        if leaf_a is None or leaf_b is None or leaf_a != leaf_b:
            continue
        mover, partner, stay_leaf = (b, a, leaf_a) if b > a else (a, b, leaf_a)
        other_leaves = [nid for nid, nd in nodes.items() if not nd["children"] and nid != stay_leaf]
        if not other_leaves:
            continue
        best_leaf = max(
            other_leaves,
            key=lambda nid: sum(
                w for (other, w) in adj.get(mover, ()) if other in nodes[nid]["members"]
            ),
        )
        nodes[stay_leaf]["members"].remove(mover)
        nodes[stay_leaf]["size"] = len(nodes[stay_leaf]["members"])
        nodes[best_leaf]["members"] = sorted(nodes[best_leaf]["members"] + [mover])
        nodes[best_leaf]["size"] = len(nodes[best_leaf]["members"])
        member_leaf[mover] = best_leaf
        moves.append(f"{mover}: {stay_leaf} -> {best_leaf} (cannot-link with {partner})")

    return moves
