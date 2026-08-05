"""The feature-timeline layout + terminal renderer, shared by `sgt graph` (static print) and the
Textual TUI.

This is the terminal counterpart of the VS Code workbench's `computeGraphLayout`/
`computeSegmentLayout` (faithful ports, kept behaviour-parallel on purpose). We lay out the
FEATURES as a tree of lanes:

    row   = one lane per feature, grouped into subsystem swimlanes and ordered by first appearance
            (foundations up top). Vertical position means "which feature / which subsystem".
    lane  = an ordered train of intent-*segment* "cars" (`f-XXXX@n`, `sgt.intent.segment`) -- the
            visual atom, and exactly the `revert`/`edit`/`rewind` unit. Cars sit in per-feature
            sequence order (x = `seg_index`), not a shared commit-time axis -- a short-lived
            feature isn't one lit pixel and a long one isn't a confetti of glyphs. Cross-feature
            wall-clock alignment is `sgt episodes`'s job.
    frontier = a fold point: only ops with commit_index <= frontier count; cars past it stay in
            place, dimmed, rather than disappearing (scrubbing accretes, it doesn't reshuffle).

`graph_layout` builds the plain per-op Gantt (still used as `segment_layout`'s tree/visibility
base, and directly by anything that predates segments); `segment_layout` threads the checkpoint
cars onto it. The terminal render draws the latter -- everything a user needs to answer "what
chapters does this feature have, which one is live, and what can I revert."
"""

from __future__ import annotations

import re
import shutil

from .color import color_for

# ── Layout (pure) ────────────────────────────────────────────────────────────────────────────────


def graph_layout(
    map_view: dict,
    grid_view: dict,
    *,
    collapsed=(),
    frontier: int | None = None,
    top_k: int = 4,
) -> dict:
    collapsed = set(collapsed)
    fr = float("inf") if frontier is None else frontier
    by_id = {n["id"]: n for n in map_view.get("nodes", [])}

    # The op -> (feature, commit) join is `grid_view`'s already-computed cell table (plan U3, the
    # canonical `sgt.api.grid_view`): a cell carries the ops one feature touched in one commit, so a
    # feature's ops are just the cells bearing its id -- no per-op DAG walk here. An op with no
    # feature has no cell and never appears (the same drop this filter used to do inline). `frontier`
    # folds by commit index; cells past it are dropped so scrubbing accretes.
    ops_by_feature: dict[str, list] = {}
    for cell in grid_view.get("cells", []):
        ci = cell["commit_index"]
        if ci > fr:
            continue
        bucket = ops_by_feature.setdefault(cell["feature_id"], [])
        for oid in cell["op_ids"]:
            bucket.append({"id": oid, "commit_index": ci})
    for fid in ops_by_feature:
        ops_by_feature[fid].sort(key=lambda o: o["commit_index"])

    def nearest_subsystem(node_id: str):
        cur = by_id[node_id].get("parent") if node_id in by_id else None
        while cur is not None:
            p = by_id.get(cur)
            if p and p.get("kind") == "subsystem":
                return cur
            cur = p.get("parent") if p else None
        return None

    def leaves_under(node_id: str) -> list[str]:
        out, stack = [], [node_id]
        while stack:
            node = by_id.get(stack.pop())
            if not node:
                continue
            children = node.get("children") or []
            if children:
                stack.extend(children)
            else:
                out.append(node["id"])
        return out

    # Visible lanes: a collapsed subsystem folds to one meta-lane (subtree aggregated); an expanded
    # subsystem contributes its feature descendants as lanes under a header; a feature is a lane.
    visible = []
    seen: set[str] = set()

    def visit(node_id: str):
        node = by_id.get(node_id)
        if not node:
            return
        # The map is a DAG: a feature can be a child of more than one subsystem, so the same node is
        # reachable by multiple paths. Emit it once -- a duplicate lane shares an id, and the id-keyed
        # `lane_by_id` would drop all but the last copy, leaving the earlier one without a row.
        if node_id in seen:
            return
        seen.add(node_id)
        is_sub = node.get("kind") == "subsystem"
        if is_sub and node_id not in collapsed:
            for c in node.get("children") or []:
                visit(c)
            return
        is_meta = is_sub
        leaves = leaves_under(node_id) if is_meta else [node_id]
        visible.append({
            "id": node_id, "is_meta": is_meta, "leaves": leaves,
            "subsystem": node_id if is_meta else nearest_subsystem(node_id),
        })

    for r in sorted(map_view.get("roots") or []):
        visit(r)

    # Aggregate ops -> op count + first/last commit + the sorted commit list. Drop lanes with no ops.
    lanes = []
    for v in visible:
        commits = [op["commit_index"] for leaf in v["leaves"] for op in ops_by_feature.get(leaf, [])]
        if not commits:
            continue
        commits.sort()
        lanes.append({
            **v, "op_count": len(commits), "first_commit": commits[0],
            "last_commit": commits[-1], "commits": commits,
        })
    lane_by_id = {l["id"]: l for l in lanes}

    # Visible co-change edges (hover overlay only): reroute to visible lanes, drop self-loops, merge,
    # top-K per lane.
    id_to_visible = {leaf: l["id"] for l in lanes for leaf in l["leaves"]}
    lane_ids = {l["id"] for l in lanes}

    def resolve_visible(node_id: str):
        if node_id in id_to_visible:
            return id_to_visible[node_id]
        cur = node_id
        while cur is not None and cur not in lane_ids:
            cur = by_id.get(cur, {}).get("parent")
        return cur

    merged: dict[tuple, float] = {}
    for e in map_view.get("edges", []):
        a, b = resolve_visible(e["a"]), resolve_visible(e["b"])
        if a is None or b is None or a == b:
            continue
        key = (a, b) if a < b else (b, a)
        merged[key] = merged.get(key, 0.0) + (e.get("weight") or 0.0)
    all_edges = [{"a": k[0], "b": k[1], "weight": w} for k, w in merged.items()]
    all_edges.sort(key=lambda e: (-e["weight"], e["a"] + e["b"]))
    per_node: dict[str, int] = {}
    edges, overflow = [], {}
    for e in all_edges:
        ca, cb = per_node.get(e["a"], 0), per_node.get(e["b"], 0)
        if ca < top_k and cb < top_k:
            edges.append(e)
            per_node[e["a"]] = ca + 1
            per_node[e["b"]] = cb + 1
        else:
            overflow[e["a"]] = overflow.get(e["a"], 0) + 1
            overflow[e["b"]] = overflow.get(e["b"], 0) + 1

    # Group into swimlanes and order by first appearance. A group is an expanded subsystem (header +
    # feature lanes) or a solo row (a meta-lane, or a feature with no subsystem).
    header_groups: dict[str, dict] = {}
    groups = []
    for l in lanes:
        if l["is_meta"] or l["subsystem"] is None:
            groups.append({"key": l["id"], "is_header": False, "lane_ids": [l["id"]],
                           "first_commit": l["first_commit"]})
            continue
        g = header_groups.get(l["subsystem"])
        if g is None:
            sub = by_id.get(l["subsystem"])
            g = header_groups[l["subsystem"]] = {
                "key": l["subsystem"], "is_header": True,
                "label": (sub or {}).get("label", l["subsystem"]),
                "collapsed_id": l["subsystem"], "lane_ids": [], "first_commit": float("inf"),
            }
            groups.append(g)
        g["lane_ids"].append(l["id"])
        g["first_commit"] = min(g["first_commit"], l["first_commit"])
    groups.sort(key=lambda g: (g["first_commit"], g["key"]))

    row = 0
    headers = []
    for g in groups:
        lane_objs = sorted((lane_by_id[i] for i in g["lane_ids"]),
                           key=lambda l: (l["first_commit"], l["id"]))
        if g["is_header"]:
            headers.append({
                "key": g["key"], "label": g["label"], "collapsed_id": g["collapsed_id"], "row": row,
                "first_commit": g["first_commit"], "last_commit": max(l["last_commit"] for l in lane_objs),
                "op_count": sum(l["op_count"] for l in lane_objs), "lane_count": len(lane_objs),
            })
            row += 1
        for l in lane_objs:
            l["row"] = row
            l["group_key"] = g["key"]
            row += 1

    return {
        "lanes": lanes, "headers": headers, "edges": edges, "overflow": overflow,
        "node_by_id": lane_by_id, "ops_by_feature": ops_by_feature,
        "row_count": max(1, row), "commit_count": len(grid_view.get("commits") or []),
        # The axis length (`commit_count`) and the number a person would call "saves" are different
        # numbers whenever sgt has materialized one of its own edits. The ruler needs the former;
        # the header needs the latter, or the map contradicts `sgt log` on the same repo.
        "save_count": grid_view.get(
            "save_count",
            sum(1 for c in (grid_view.get("commits") or []) if not c.get("bookkeeping")),
        ),
        "bookkeeping_count": grid_view.get("bookkeeping_count", 0),
    }


def segment_layout(
    map_view: dict,
    grid_view: dict,
    segments: list[dict],
    *,
    collapsed=(),
    frontier: int | None = None,
) -> dict:
    """The chunk-car timeline layout: the visual atom is the intent *segment* (a `<feature>@<n>`
    checkpoint), not the raw op. Reuses `graph_layout` verbatim for the gutter -- tree walk,
    visibility, collapse-to-meta, ordering by first appearance, lanes-with-no-ops dropped -- then
    threads each visible lane's leaf feature(s) segments onto it as an ordered train of `cars`
    (a collapsed subsystem's meta-lane naturally gets the union of its features' cars, sorted
    together -- the "aggregate car strip" the redesign calls for).

    `segments` is the flat list `sgt.api.segments_view`/`_segments_out` already returns (one dict
    per checkpoint: `feature_id`, `seg_index`, `checkpoint`, `intent` (label), `op_ids`, `op_count`,
    `first_index`, `last_index`, `tier`, `source`) -- passed in, never re-derived here, so this
    function stays pure and repo-free like `graph_layout`.

    A car whose `first_index` is past `frontier` is kept and flagged `is_future` rather than
    dropped: the renderer dims it, but a lane's car *count* and positions stay stable while
    scrubbing -- only cell density (via `graph_layout`'s own op filtering) accretes."""
    fr = float("inf") if frontier is None else frontier
    commit_index_of = {oid: cell["commit_index"]
                       for cell in grid_view.get("cells", []) for oid in cell["op_ids"]}

    by_feature: dict[str, list[dict]] = {}
    for seg in segments:
        by_feature.setdefault(seg["feature_id"], []).append(seg)

    base = graph_layout(map_view, grid_view, collapsed=collapsed, frontier=frontier)

    lanes = []
    for l in base["lanes"]:
        cars = []
        for leaf in l["leaves"]:
            for seg in by_feature.get(leaf, []):
                bins: dict[int, int] = {}
                for oid in seg["op_ids"]:
                    ci = commit_index_of.get(oid)
                    if ci is not None:
                        bins[ci] = bins.get(ci, 0) + 1
                cars.append({
                    "feature_id": leaf,
                    "seg_index": seg["seg_index"],
                    "checkpoint": seg["checkpoint"],
                    "label": seg["intent"],
                    "op_count": seg["op_count"],
                    "tier": seg["tier"],
                    "source": seg["source"],
                    "first_index": seg["first_index"],
                    "last_index": seg["last_index"],
                    "sub_bins": sorted(bins.items()),
                    "is_future": seg["first_index"] > fr,
                    "words": seg.get("words", []),  # captured words for this chapter (zoom render)
                })
        cars.sort(key=lambda c: (c["first_index"], c["feature_id"], c["seg_index"]))
        lanes.append({**l, "cars": cars})

    return {**base, "lanes": lanes, "node_by_id": {l["id"]: l for l in lanes}}


# ── Episodic projection (pure) ───────────────────────────────────────────────────────────────────


def episodes(map_view: dict, grid_view: dict) -> dict:
    """Roll the flat op stream up into EPISODES -- one per commit that carried ops -- and group
    episodes by their dominant feature into collapsible episode-groups (the "co-commit cluster" a
    developer rewinds as a unit; Stage C).

    Sessions are empty on mined history (only sgt's own land/checkpoint stamp them), so the episode
    axis is projected from provenance: an op's ``commit_index`` identifies its earliest provenance
    commit, so ops sharing a ``commit_index`` were advanced in the same commit = one episode --
    exactly the co-commit signal Stage B clusters on. Real sgt sessions supersede this going
    forward; the shape is identical. Pure over the canonical ``grid_view`` cell table (plan U3) --
    a cell already carries the ops one feature touched in one commit, so an episode is the cells
    sharing one ``commit_index`` re-rolled across features; an op with no feature has no cell, so
    (unlike the raw op stream) an all-unattributed commit forms no episode. The VS Code counterpart
    is ``rollupEpisodes`` in workbench.js."""
    labels = {n["id"]: n.get("label", n["id"]) for n in map_view.get("nodes", [])}
    subject_of = {c["index"]: c.get("subject", "") for c in grid_view.get("commits", [])}
    sha_of = {c["index"]: c.get("sha") for c in grid_view.get("commits", [])}

    by_index: dict[int, dict] = {}
    for cell in grid_view.get("cells", []):
        idx = cell["commit_index"]
        ep = by_index.get(idx)
        if ep is None:
            ep = by_index[idx] = {
                "index": idx, "sha": sha_of.get(idx), "subject": subject_of.get(idx, ""),
                "op_ids": [], "features": {}, "kinds": {},
            }
        ep["op_ids"].extend(cell["op_ids"])
        ep["features"][cell["feature_id"]] = ep["features"].get(cell["feature_id"], 0) + cell["op_count"]
        for kind, n in cell["kinds"].items():
            ep["kinds"][kind] = ep["kinds"].get(kind, 0) + n

    episodes_out = []
    for idx in sorted(by_index):
        ep = by_index[idx]
        ep["op_count"] = len(ep["op_ids"])
        # Dominant feature: most ops in this commit; ties broken by larger id for determinism.
        ep["dominant_feature"] = (
            max(ep["features"], key=lambda f: (ep["features"][f], f)) if ep["features"] else None
        )
        episodes_out.append(ep)

    # Episode-groups: episodes sharing a dominant feature (the collapsible "thing I was doing"),
    # ordered by first appearance; unattributed episodes (no feature) fall under a None group.
    groups: dict = {}
    for ep in episodes_out:
        key = ep["dominant_feature"]
        g = groups.get(key)
        if g is None:
            g = groups[key] = {
                "feature_id": key, "label": labels.get(key, key) if key else "(unattributed)",
                "episode_indices": [], "op_count": 0, "kinds": {},
                "first_index": ep["index"], "last_index": ep["index"],
            }
        g["episode_indices"].append(ep["index"])
        g["op_count"] += ep["op_count"]
        g["last_index"] = ep["index"]  # episodes are index-sorted, so the latest append is the last
        for k, n in ep["kinds"].items():
            g["kinds"][k] = g["kinds"].get(k, 0) + n
    groups_out = sorted(groups.values(), key=lambda g: (g["first_index"], str(g["feature_id"])))

    return {"episodes": episodes_out, "groups": groups_out}


def episode_rail_layout(ep_view: dict) -> dict:
    """Lay episodes out as a vertical git-log rail: the newest episode on top (row 0). A RECURRING
    feature -- one touched by two or more saves -- gets its own dedicated lane so its saves read as
    a single unbroken vertical line, traceable end to end (the connection the flat op stream loses,
    and the bug behind "Conflict Resolution should be connected"). A ONE-OFF feature (a single save)
    shares a packed lane pool with the other one-offs via greedy interval-graph coloring, so
    incidental single-save work never widens the rail -- that pooling is the compaction, applied
    only where a feature isn't worth its own column.

    A feature's span covers every save it *touched*, not only the ones it dominated -- so a recurring
    feature stays connected straight through a save that some other feature happened to dominate. As
    before, only features that dominate at least one save get a lane at all; a purely incidental brush
    stays a chip, off the rail.

    Input is `episodes()`'s output; each rail row carries what a rewind decision needs (subject,
    op_count, sha, per-feature op counts, dominant feature). The VS Code counterpart is
    `episodeRailLayout` in workbench.js (dominant-only; the recurring-lane refinement is terminal-side
    for now)."""
    episodes = ep_view.get("episodes", [])
    ordered = sorted(episodes, key=lambda e: -e["index"])  # newest (largest commit_index) on top
    row_of = {e["index"]: r for r, e in enumerate(ordered)}

    # Rail features: those that dominate >=1 save. `touched` records every save each such feature
    # brushed (drives the `feature_touched` output + the cosmetic `recurring` set), but a feature's
    # LANE SPAN is the range of saves it DOMINATED, `[min, max]` -- matching workbench.js's
    # `episodeRailLayout`. Spanning only the dominated range still bridges interior saves the feature
    # didn't dominate (the "stays connected through a save it didn't dominate" property: those rows
    # fall inside `[min, max]`); it just doesn't stretch the lane out to incidental brushes before the
    # feature's first / after its last dominated save. Touched-based spans stretched to every brush,
    # which on a long history overlap so heavily that lane pooling can't compact the rail at all.
    rail_feats = {e["dominant_feature"] for e in episodes if e["dominant_feature"] is not None}
    touched: dict = {}  # fid -> set of rows the feature touched (for feature_touched / recurring)
    dominated: dict = {}  # fid -> set of rows the feature DOMINATED (drives the lane span)
    for e in episodes:
        r = row_of[e["index"]]
        for fid in e.get("features", {}):
            if fid in rail_feats:
                touched.setdefault(fid, set()).add(r)
        if e["dominant_feature"] is not None:
            dominated.setdefault(e["dominant_feature"], set()).add(r)
    span = {fid: [min(rs), max(rs)] for fid, rs in dominated.items()}
    recurring = {fid for fid, rs in touched.items() if len(rs) >= 2}

    # Pool ALL features into a single shared lane pool via greedy interval-graph coloring: a lane is
    # reused as soon as its current occupant's span ends, so the rail width collapses to the interval
    # chromatic number (the max number of features live at any one row) instead of one lane per
    # feature. Interval coloring guarantees no two features on a lane ever overlap, so a pooled lane's
    # `│` connectors never collide even though several features share it over disjoint spans.
    lane_of: dict = {}
    lane_bot: list = []  # pool lane -> bottom row of the feature currently occupying it
    for fid in sorted(span, key=lambda f: (span[f][0], str(f))):
        top, bot = span[fid]
        lane = next((L for L in range(len(lane_bot)) if lane_bot[L] < top), None)
        if lane is None:
            lane = len(lane_bot)
            lane_bot.append(bot)
        else:
            lane_bot[lane] = bot
        lane_of[fid] = lane

    # A pooled lane can hold several one-off features over disjoint row-spans, so a lane maps to a
    # LIST of (top, bot, fid) intervals -- not one feature. A cell resolves which feature occupies the
    # lane at a given row by interval membership; collapsing to one fid per lane drops the dots of
    # every pooled feature but the last (a save with no node on its own dominant lane).
    lane_intervals: dict = {}
    for fid, L in lane_of.items():
        lane_intervals.setdefault(L, []).append((span[fid][0], span[fid][1], fid))
    rows = [
        {"index": e["index"], "row": row_of[e["index"]], "feature": e["dominant_feature"],
         "lane": lane_of.get(e["dominant_feature"], 0), "subject": e["subject"],
         "op_count": e["op_count"], "sha": e["sha"], "features": e["features"]}
        for e in ordered
    ]
    return {"rows": rows, "lane_of": lane_of, "lane_intervals": lane_intervals,
            "feature_touched": {f: sorted(rs) for f, rs in touched.items()},
            "feature_span": span, "recurring": sorted(str(f) for f in recurring),
            "lane_count": max(1, len(lane_bot)), "row_count": len(ordered)}


# ── Terminal render ────────────────────────────────────────────────────────────────────────────


def _rgb(hex_str: str) -> tuple[int, int, int]:
    h = hex_str.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _fg(hex_str: str, s: str) -> str:
    r, g, b = _rgb(hex_str)
    return f"\x1b[38;2;{r};{g};{b}m{s}\x1b[0m"


def _shade(hex_str: str, intensity: float, s: str) -> str:
    """`s` painted in the feature hue, brightness scaled by `intensity` in (0,1] -- so a dense time
    column reads as a bright block and a sparse one as a dim block (density without extra glyphs)."""
    r, g, b = _rgb(hex_str)
    k = 0.4 + 0.6 * max(0.0, min(1.0, intensity))
    return f"\x1b[38;2;{int(r * k)};{int(g * k)};{int(b * k)}m{s}\x1b[0m"


_DIM = "\x1b[2m"
_BOLD = "\x1b[1m"
_RESET = "\x1b[0m"
_RED = "\x1b[31m"
_AMBER = "\x1b[33m"

_TIER_BRACKETS = {"co-changed": ("[", "]"), "coupled": ("[", "]"), "thematic": ("(", ")")}

# Shared "state" glyphs across BOTH graphs (the map and the rail), so a fork/merge/plan reads the
# same everywhere: a plan step with no code yet, a fork (divergent edits to one symbol), and a
# pending merge-op draft that would close one. Isolated here so a terminal that renders one poorly
# is a one-line swap.
_GHOST = "◇"
_FORK = "⋔"
_MERGE = "⋈"


def _sgr(code: str, s: str, *, color: bool) -> str:
    return f"{code}{s}{_RESET}" if color else s


def _state_banner(states: dict | None, *, color: bool) -> list[str]:
    """Footer lines shared by both graphs: open forks (⋔ — divergent edits to one symbol) and
    pending merge-op drafts (⋈) that would close one. `states` is `{"forks": [...], "rewrites":
    {...}}` as the CLI builds from `forks_view`/`rewrite_view`; None/empty renders nothing, so the
    default (stateless) call sites and their golden snapshots stay byte-identical.

    Forks/drafts are symbol-scoped and their tips are excluded from every verb-visible ideal
    (`order.reduce_to_ideal`), so there is no reliable lane cell to paint -- the banner is the
    honest surface (it carries the symbol + the exact remedy), not a fabricated lane position."""
    if not states:
        return []
    out: list[str] = []
    forks = states.get("forks") or []
    if forks:
        out.append(_sgr(_RED, f" {_FORK} {len(forks)} open fork(s) — divergent edits to one symbol:",
                        color=color))
        for f in forks:
            remedy = f"sgt resolve {f.get('symbol', '?')}"
            out.append(_dim(f"     {f.get('symbol', '?')}  →  {remedy}", color=color))
    drafts = [d for d in (states.get("rewrites") or {}).get("drafts", []) if d.get("verb") == "merge-op"]
    if drafts:
        out.append(_sgr(_AMBER, f" {_MERGE} {len(drafts)} pending merge-op draft(s):", color=color))
        for d in drafts:
            did = (d.get("draft_id") or "")[:12]
            out.append(_dim(f"     {d.get('target', '?')}  →  sgt advanced repair {did}", color=color))
    return out


def _leaf_features_under(node_id: str, by_id: dict) -> set[str]:
    """Every feature-leaf id reachable under `node_id` (a subsystem node) via `children`. A leaf is
    a node with no children; the same tree walk `graph_layout.leaves_under` does, module-level so
    the group resolver can reuse it without building a layout."""
    out, stack = set(), [node_id]
    while stack:
        node = by_id.get(stack.pop())
        if not node:
            continue
        children = node.get("children") or []
        if children:
            stack.extend(children)
        elif node.get("members"):  # a childless node with no members is a clustering artifact
            out.add(node["id"])
    return out


def resolve_focus_group(ref: str, map_view: dict, grid_view: dict, themes: dict | None = None):
    """Resolve `--focus`'s argument to a GROUP of features -- a subsystem (its feature leaves) or a
    theme (the features its commits touched) -- for the vertical category view. Returns
    `{"label", "kind", "feature_ids"}` or None when `ref` names no group, in which case the caller
    falls through to the single-lane `render_graph_lines(focus=...)` path.

    A subsystem is matched by unique id-prefix or exact (case-insensitive) label against the
    `kind=="subsystem"` nodes; a theme by exact label against `themes` (the committed
    `.sgt/intent/themes.json`, `{theme_id: {label, atom_shas, ...}}`). The theme→feature join goes
    through `grid_view`: a theme's `atom_shas` -> commit indices -> the features whose cells sit on
    those commits. `themes` defaults empty so a repo with no built themes still resolves subsystems."""
    by_id = {n["id"]: n for n in map_view.get("nodes", [])}
    want = (ref or "").strip().lower()

    subs = [n for n in map_view.get("nodes", []) if n.get("kind") == "subsystem"]
    hits = [n for n in subs if ref and n["id"].startswith(ref)]
    if len(hits) != 1:
        hits = [n for n in subs if n.get("label", "").strip().lower() == want]
    if len(hits) == 1:
        feats = _leaf_features_under(hits[0]["id"], by_id)
        if feats:
            return {"label": hits[0].get("label", hits[0]["id"]), "kind": "subsystem",
                    "feature_ids": feats}

    themes = themes or {}
    thits = [t for t in themes.values() if t.get("label", "").strip().lower() == want]
    if len(thits) == 1:
        shas = set(thits[0].get("atom_shas", []))
        idx_of = {c["sha"]: c["index"] for c in grid_view.get("commits", [])}
        want_idx = {idx_of[s] for s in shas if s in idx_of}
        feats = {c["feature_id"] for c in grid_view.get("cells", []) if c["commit_index"] in want_idx}
        if feats:
            return {"label": thits[0]["label"], "kind": "theme", "feature_ids": feats}
    return None


def _resolve_focus(focus: str, layout: dict, labels: dict) -> str | None:
    """Resolve `--focus`'s argument against a lane id: a unique id-prefix (the short handle the
    render prints, e.g. `f-0575f655` for a longer real id), else a unique case-insensitive label
    match. Mirrors `sgt.intent.segment.resolve_checkpoint`'s handle resolution, so the id you copy
    off the graph and the id you pass back in agree."""
    prefix_hits = [nid for nid in layout["node_by_id"]
                   if focus and (nid.startswith(focus) or nid.startswith("f-" + focus))]
    if len(prefix_hits) == 1:
        return prefix_hits[0]
    want = focus.strip().lower()
    label_hits = [nid for nid in layout["node_by_id"] if labels.get(nid, "").strip().lower() == want]
    return label_hits[0] if len(label_hits) == 1 else None


def _bucket_density(sub_bins: list, width: int) -> list[int]:
    """Fold a car's `[(commit_index, count), ...]` into `width` sequential buckets -- the within-
    car density texture drawn between its brackets. A single-commit car (the common case) has
    nothing to spread across time, so it fills solid at that one count -- a flat opacity, not one
    bright pixel bleeding into a tail of dead space."""
    if width <= 0:
        return []
    n = len(sub_bins)
    if n == 0:
        return [0] * width
    if n == 1:
        return [sub_bins[0][1]] * width
    buckets = [0] * width
    for i, (_ci, cnt) in enumerate(sub_bins):
        buckets[min(width - 1, i * width // n)] += cnt
    return buckets


_SPARK = "▁▂▃▄▅▆▇█"

# Temporal LOD: the commit-time axis is power-warped so recent commits get more column width than
# old ones (γ>1 pushes low fractions toward 0, so history near the newest end spreads out and the
# old tail compresses). The bar and the ruler share `_col_of` so their positions can never drift.
_LOD_GAMMA = 2.0


def _col_of(ci: int, max_ci: int, width: int) -> int:
    """Screen column for commit index `ci` on a `width`-wide strip, power-warped by `_LOD_GAMMA`:
    0 (oldest) … max_ci (newest) maps to 0 … width-1, with recent commits given more room."""
    if max_ci <= 0 or width <= 0:
        return 0
    t = max(0, min(max_ci, ci)) / max_ci  # 0 oldest … 1 newest
    return int(round((t ** _LOD_GAMMA) * (width - 1)))


def _time_ruler(prefix_w: int, bar_w: int, commit_count: int) -> str:
    """A commit-index ruler aligned under the car strip: start/mid/end ticks at evenly-spaced screen
    columns, but each label reads the *true* commit index under the power warp (`ci = round(frac**(1/γ)
    * max_ci)`) -- so the mid tick honestly reads ~70% through history, communicating the compression
    of the old tail. Blank when there's no width or only one commit (nothing to place along)."""
    if bar_w <= 0 or commit_count <= 1:
        return ""
    max_ci = commit_count - 1
    buf = [" "] * bar_w
    for frac in (0.0, 0.5, 1.0):
        ci = int(round((frac ** (1 / _LOD_GAMMA)) * max_ci))  # invert the warp so the label is honest
        tick = f"c{ci}"
        pos = int(round(frac * (bar_w - 1)))
        if frac == 1.0:
            pos = bar_w - len(tick)          # end-anchored
        elif frac == 0.5:
            pos -= len(tick) // 2            # centered
        pos = max(0, min(bar_w - len(tick), pos))
        for k, ch in enumerate(tick):
            buf[pos + k] = ch
    return " " * prefix_w + "".join(buf)


def _ellipsize(s: str, width: int) -> str:
    """Truncate a checkpoint label to `width` columns, cutting on a word boundary where one is near
    the limit (so `add foo, qux, config, binary` becomes `add foo, qux, config…`, not `…config, bi`)
    and appending `…`. Short-enough labels pass through untouched."""
    if len(s) <= width:
        return s
    cut = s[:width].rstrip()
    space = cut.rfind(" ")
    if space >= width - 8:  # a word boundary close enough to the limit -- cut there, not mid-word
        cut = cut[:space].rstrip(" ,;:")
    return cut + "…"


def _paint(hex_str: str, s: str, *, color: bool) -> str:
    return _fg(hex_str, s) if color else s


def _dim(s: str, *, color: bool) -> str:
    return f"{_DIM}{s}{_RESET}" if color else s


def _bold(s: str, *, color: bool) -> str:
    return f"{_BOLD}{s}{_RESET}" if color else s


def _chips(r: dict, labels: dict, *, color: bool, chip_width: int = 22, budget: int = 60) -> str:
    """A save's feature attribution: each touched feature's label in its own hue, main feature first
    then densest-first. Each label is ellipsized to `chip_width` and the run is capped at `budget`
    visible columns, features past it collapsing into a dim `+N` -- without this cap a save touching
    many features overruns the terminal and wraps (the Phase-4 wrapping fix). Shared by the lane rail
    and the lane-less save list so both bound identically."""
    feats = r.get("features") or {}
    order_ = sorted(feats, key=lambda f: (f != r["feature"], -feats[f], f))
    parts: list[str] = []
    used = 0
    for f in order_:
        label = _ellipsize(labels.get(f, f or "(unattributed)"), chip_width)
        w = len(label) + (3 if parts else 0)  # 3 = visible width of the " · " separator
        if parts and used + w > budget:
            break
        parts.append(_paint(color_for(f or ""), label, color=color))
        used += w
    extra = len(order_) - len(parts)
    if extra > 0:
        parts.append(_dim(f"+{extra}", color=color))
    return _dim(" · ", color=color).join(parts) if parts else _dim("(unattributed)", color=color)


# Bare stamps a launch-eve history is full of -- they say nothing about what the save did.
_LOW_SIGNAL_SUBJECTS = {"done", "ok", "wip", "fix", "sss", "update", "stuff", "misc"}


def _row_headline(subject: str, feature: str | None, labels: dict) -> str:
    """The headline for a save row: its commit subject when that subject carries signal, else the
    save's dominant-feature label (already in `labels` -- the same feature+intent data the `--map`
    view leads with). A subject is low-signal when it's empty, <=3 chars, or a bare stamp like
    `done`/`wip`/`sss`. Falls through to the raw subject when there's no feature label to borrow."""
    subj = (subject or "").strip()
    if len(subj) >= 4 and subj.lower() not in _LOW_SIGNAL_SUBJECTS:
        return subject
    if feature is not None:
        return labels.get(feature) or subject
    return subject


def render_save_list_lines(
    map_view: dict,
    grid_view: dict,
    *,
    selected: str | None = None,
    color: bool = True,
    label_width: int = 48,
    max_rows: int = 40,
) -> list[str]:
    """The default `sgt log` (Phase 4): a lane-less "what I did, in order" -- one row per save,
    newest on top, `cN  sha  subject  features`. Drops `render_rail_lines`' recurring-feature lane
    column (the `" ".join(cell(...))` art that overruns the terminal past ~20 lanes and wraps),
    keeping the same episode rows and width-bounded feature chips. The lane rail stays available
    under `sgt log --rail` for readers who want the recurring-feature threads."""
    ep = episodes(map_view, grid_view)
    layout = episode_rail_layout(ep)
    labels = {n["id"]: n.get("label", n["id"]) for n in map_view.get("nodes", [])}
    rows = layout["rows"]

    n_ep = len(rows)
    n_feat = len({r["feature"] for r in rows if r["feature"] is not None})
    lines = [_bold(f" {n_ep} save(s)  ·  {n_feat} feature(s)   (newest on top)", color=color), ""]
    shown = rows[:max_rows]
    pos_w = max((len(f"c{r['index']}") for r in shown), default=2)
    for r in shown:
        pos = _dim(f"c{r['index']}".rjust(pos_w), color=color)
        sha = _dim((r["sha"] or "")[:7], color=color)
        head = _row_headline(r["subject"], r["feature"], labels)
        subj = _ellipsize((head or "").replace("\n", " "), label_width).ljust(label_width)
        subj_s = _bold(subj, color=color) if r["feature"] == selected else subj
        lines.append(f" {pos} {sha}  {subj_s}  {_chips(r, labels, color=color)}")
    if n_ep > len(shown):
        lines.append("")
        lines.append(_dim(f" {n_ep - len(shown)} older save(s) folded (newest {len(shown)} shown)",
                          color=color))
    return lines


def _render_car(car: dict, width: int, hexc: str, *, color: bool, is_big: bool = False) -> str:
    """One checkpoint "car": tier brackets around a `@n` digit and a density-shaded body. Module
    level so both the timeline rail (`render_graph_lines`) and the feedforward preview
    (`render_verb_preview_lines`) draw the same glyph. A future car (past the frontier) renders dim."""
    lo, hi = _TIER_BRACKETS.get(car["tier"], ("[", "]"))
    inner_w = max(0, width - 2)
    body = ""
    if inner_w >= 1:
        digit = str(car["seg_index"] % 10)
        body += _dim(digit, color=color) if car["is_future"] else (
            _bold(_paint(hexc, digit, color=color), color=color) if color else digit)
    if inner_w >= 2:
        buckets = _bucket_density(car["sub_bins"], inner_w - 1)
        local_max = max(buckets) if buckets else 0
        for n in buckets:
            ch = "█" if n > 0 else "·"
            if car["is_future"] or n == 0:
                body += _dim(ch, color=color)
            else:
                body += _shade(hexc, (n / max(1, local_max)) ** 0.5, ch) if color else ch

    def bracket(b: str) -> str:
        if car["is_future"]:
            return _dim(b, color=color)
        painted = _paint(hexc, b, color=color)
        return _bold(painted, color=color) if (is_big and color) else painted  # big event = bold

    return f"{bracket(lo)}{body}{bracket(hi)}"


_BAR_MAX = 12  # cap+scale wide op-sets so a bar stays one legible glyph run


def _magnitude_bar(before: int, after: int, present: int, hexc: str, *, color: bool) -> str:
    """The static stand-in for the webview's collapsing lane: a bar `max(before, after)` cells wide
    where the ops *present in this frame* are solid in the feature hue and the rest are dim `░`
    ghosts. Because the length is fixed by the union of both states, flipping `present` (before↔after
    via the pane's `b` key) fills or empties the bar *in place* -- the eye sees the shrink/grow the
    terminal can't animate. Counts above `_BAR_MAX` scale down proportionally (min one cell for any
    nonzero side) so a 64-op revert is still one glyph run, not a wrapped wall."""
    total = max(before, after, 1)
    scale = min(1.0, _BAR_MAX / total)

    def cells(n: int) -> int:
        return 0 if n <= 0 else max(1, round(n * scale))

    width = cells(total)
    solid = min(width, cells(present))
    ghost = max(0, width - solid)
    solid_s = (_shade(hexc, 1.0, "█" * solid) if color else "█" * solid) if solid else ""
    return solid_s + _dim("░" * ghost, color=color)


def _min_unique_prefixes(ids, *, floor: int = 5, cap: int = 10) -> dict:
    """jj-style: for each id, the shortest prefix length (clamped to `[floor, cap]`) that no *other*
    id in the set shares -- the minimal handle a user must type to point at it unambiguously. Feature
    ids are `f-<hex>`, so the leading `f-` is common; brightening `fid[:k]` and dimming the rest tells
    the eye exactly how few characters actually select this feature."""
    ids = list(ids)
    out: dict = {}
    for fid in ids:
        k = floor
        while k < cap and any(other != fid and other[:k] == fid[:k] for other in ids):
            k += 1
        out[fid] = min(k, len(fid))
    return out


def render_graph_lines(
    map_view: dict,
    grid_view: dict,
    segments: list[dict] | None = None,
    *,
    selected: str | None = None,
    focus: str | None = None,
    frontier: int | None = None,
    collapsed=(),
    color: bool = True,
    bar_width: int | None = None,
    show_links: bool = False,
    states: dict | None = None,
    max_rows: int = 40,
) -> list[str]:
    """Render the feature map as terminal lines (ANSI truecolor when `color`): one row per lane,
    each drawing its checkpoints' `▁▂▃▄▅▆▇█` edit-density *positioned on the SHARED commit-time axis*
    (a `c0 … cN` ruler runs above the lanes) -- so glyph height reads *how busy* a stretch was and
    its horizontal position reads *when* it happened, with dim `·` for quiet spans. Each lane trails
    its checkpoint chips as `@n slug` (the `@n` is the `sgt revert` handle). This folds the former
    `--timeline` rail (relative-time position + numbers) into the map (density + named checkpoints).

    `segments` is the flat `sgt.api.segments_view` list; a lane with no matching segments (nothing
    built yet for it) falls back to a plain dim track. `focus=<feature_id>` renders just that one
    lane, full width, one detail line per car (checkpoint, slug, label, op count, tier). `show_links`
    re-enables the co-change `↔` annotation trailing each row -- off by default, since themes/co-change
    are now an overlay, not the primary read."""
    from sgt.intent.segment import checkpoint_slug

    segments = segments or []
    fr = float("inf") if frontier is None else frontier
    layout = segment_layout(map_view, grid_view, segments, collapsed=collapsed, frontier=frontier)
    labels = {n["id"]: n.get("label", n["id"]) for n in map_view.get("nodes", [])}

    # Plan ghosts (pending steps with no code yet): a ◇ chip at its predicted lane's tip, or -- when
    # the predicted feature has no visible lane -- an "unplaced" pseudo-row after the lanes. Read
    # straight off `grid_view.ghosts` (the only place a prediction reaches the grid), so no new input.
    feature_to_lane = {leaf: l["id"] for l in layout["lanes"] for leaf in l["leaves"]}
    ghost_by_lane: dict[str, list[str]] = {}
    unplaced_ghosts: list[dict] = []
    for g in grid_view.get("ghosts", []):
        lane_id = feature_to_lane.get(g["feature_id"])
        if lane_id is not None:
            ghost_by_lane.setdefault(lane_id, []).append(g.get("title", ""))
        else:
            unplaced_ghosts.append(g)

    def paint(hex_str: str, s: str) -> str:
        return _paint(hex_str, s, color=color)

    def dim(s: str) -> str:
        return _dim(s, color=color)

    def bold(s: str) -> str:
        return _bold(s, color=color)

    # jj-style handles: the minimal-unique-prefix of each feature id, brightened so the eye sees
    # exactly how few characters actually select it (the rest dimmed). The token stays copy-pasteable.
    prefix_len = _min_unique_prefixes(list(layout["node_by_id"]))

    def brighten_prefix(fid: str, hexc: str, *, full: bool = False) -> str:
        # Drop the shared `f-` tag -- the eye doesn't need it and the user asked it gone; the copy
        # token is the bare hex. Brighten its minimal-unique prefix (offset by the 2 `f-` chars the
        # prefix length counted). `full` shows the whole body (focus header), else the 8-char handle.
        has_tag = fid.startswith("f-")
        body = fid[2:] if has_tag else fid
        disp = body if full else body[:8]
        k = max(0, min(prefix_len.get(fid, 5) - (2 if has_tag else 0), len(disp)))
        head = bold(paint(hexc, disp[:k])) if color else disp[:k]
        return head + dim(disp[k:])

    def render_car(car: dict, width: int, hexc: str, is_big: bool = False) -> str:
        return _render_car(car, width, hexc, color=color, is_big=is_big)

    def time_bar(cars: list[dict], hexc: str, width: int) -> str:
        """A lane's `▁▂▃▄▅▆▇█` edit-density positioned on the SHARED commit-time axis: each checkpoint's
        region sits at the column of its commits (`first_index`→`last_index`, one commit-column wide at
        minimum via gap-fill), so left-to-right reads *when* the work happened; glyph height scales to
        the lane's busiest commit, so taller reads *busier*. Quiet spans between checkpoints are dim `·`.
        Future cars (past the frontier) render dim. This is the former `--timeline` placement carrying
        the map's density texture -- the two views merged. Empty (dim `·`) when the lane has no ops yet."""
        if not cars or width <= 0:
            return dim("·" * max(0, width))
        max_ci = max(1, layout["commit_count"] - 1)
        counts = [cnt for c in cars for _ci, cnt in c.get("sub_bins", [])]
        gmax = max(counts) if counts else 0

        def col_of(ci: int) -> int:
            return _col_of(ci, max_ci, width)

        placed: list[tuple[int, int, dict]] = []
        cursor = 0
        for i, c in enumerate(cars):
            start = max(col_of(c["first_index"]), cursor)
            # Gap-fill tiling: fill through the END of this car's last commit column, so a single-commit
            # checkpoint is a full column, not one pixel bleeding into dead space. Under the power warp
            # a commit's column span varies with position (old commits are narrower), so measure it
            # locally as the distance to the next commit's column rather than a fixed step. The last car
            # fills to the strip edge, and no car runs into the next; distant checkpoints still leave a
            # dim `·` track between them, which is what reads as "went quiet".
            li = c["last_index"]
            col_span = max(1, _col_of(li + 1, max_ci, width) - col_of(li))
            right_end = width if li >= max_ci else col_of(li) + col_span
            if i + 1 < len(cars):
                right_end = min(right_end, col_of(cars[i + 1]["first_index"]) - 1)
            w = max(1, right_end - start)
            if start + w > width:
                start = max(cursor, width - w)
            if start + w > width:
                w = max(1, width - start)
            if w < 1 or start >= width:
                break  # out of room -- the remaining (rightmost, latest) cars don't fit
            placed.append((start, w, c))
            cursor = start + w + 1  # a one-column gap between adjacent cars

        out, col = "", 0
        for start, w, c in placed:
            if start > col:
                out += dim("·" * (start - col))
            if gmax <= 0:
                out += dim("·" * w)
            else:
                for n in _bucket_density(c.get("sub_bins", []), w):
                    if n <= 0:
                        out += dim("·")
                        continue
                    frac = n / gmax
                    ch = _SPARK[min(len(_SPARK) - 1, int(frac * (len(_SPARK) - 1) + 0.5))]
                    out += dim(ch) if c.get("is_future") else (
                        _shade(hexc, frac ** 0.5, ch) if color else ch)
            col = start + w
        if col < width:
            out += dim("·" * (width - col))
        return out

    # Nearest co-change neighbours (strongest first), for the optional per-lane annotation.
    nbrs: dict[str, list] = {}
    for e in sorted(layout["edges"], key=lambda e: -e["weight"]):
        nbrs.setdefault(e["a"], []).append(e["b"])
        nbrs.setdefault(e["b"], []).append(e["a"])

    def links_note(fid: str) -> str:
        if not show_links:
            return ""
        link_ids = nbrs.get(fid, [])[:2]
        links = ", ".join(labels.get(x, x) for x in link_ids)
        extra = len(nbrs.get(fid, [])) - len(link_ids)
        return ("  " + dim("↔ " + links + (f" +{extra}" if extra > 0 else ""))) if links else ""

    lines: list[str] = []
    n_sub = len(layout["headers"])
    sub_note = f"  ·  {n_sub} subsystem(s)" if n_sub else ""
    bk = layout.get("bookkeeping_count", 0)
    bk_note = dim(f"  (+{bk} bookkeeping)") if bk else ""
    lines.append(bold(f" {len(layout['lanes'])} feature(s)  ·  {layout.get('save_count', layout['commit_count'])} save(s)"
                      f"{sub_note}") + bk_note)
    if frontier is not None:
        lines.append(dim(f"   frontier: folded at commit {frontier} (later features hidden)"))
    lines.append("")

    if focus is not None:
        fid_match = focus if focus in layout["node_by_id"] else _resolve_focus(focus, layout, labels)
        lane = layout["node_by_id"].get(fid_match) if fid_match else None
        if lane is None:
            lines.append(dim(f" {focus!r} has no lane yet (no ops, an unknown feature id, or an "
                             "ambiguous prefix/label)"))
            return lines
        focus = fid_match
        handle = focus[2:10] if focus.startswith("f-") else focus[:8]  # copy token: the bare hex the gutter prints
        hexc = color_for(focus)
        raw = labels.get(focus, focus)
        n_ckpt = len(lane["cars"])
        lines.append(f" {paint(hexc, '●')} {bold(raw)}  {brighten_prefix(focus, hexc, full=True)}"
                     f"  ·  {n_ckpt} checkpoint(s)")
        lines.append("")
        if not lane["cars"]:
            lines.append(dim("   no checkpoints yet -- run `sgt log --refresh` to name them"))
        example_slug = None
        for car in lane["cars"]:
            head = render_car(car, 6, hexc)
            future = dim(" (not yet reached)") if car["is_future"] else ""
            slug = checkpoint_slug(car["label"])
            if example_slug is None and not car["is_future"]:
                example_slug = slug
            lines.append(f"   {head}  {handle}@{car['seg_index']}  {dim(':' + slug)}  {car['label']}"
                         f"{future}")
            # The chapter in the user's own words (intent-ledger P1 zoom): the words captured for the
            # commits this chapter covers, so "the history answers in my own words" is literally on
            # screen. Up to three, ellipsized; `sgt feature why <sha>` shows the full text + the
            # `claude --resume` handle. Silent when nothing was captured (never a guessed reason).
            words = car.get("words", [])
            for w in words[:3]:
                lines.append("       " + dim(f"“{_ellipsize(w, 66)}”"))
            if len(words) > 3:
                lines.append("       " + dim(f"… +{len(words) - 3} more"))
        lines.append("")
        slug_hint = example_slug or "<slug>"
        lines.append(dim(f" operate:  sgt revert {handle}:{slug_hint}  (one checkpoint, by name)   ·   "
                         f"sgt revert {handle}  (whole feature)   ·   sgt revert {handle}@<n>  (by index)"))
        return lines

    lanes_by_row = {l["row"]: l for l in layout["lanes"]}
    headers_by_row = {h["row"]: h for h in layout["headers"]}

    # Title column: pad every lane label to the widest label so the density bars line up, but cap the
    # column at 32 cols and ellipsize a longer label -- one very long feature name mustn't shove the
    # bar off-screen.
    def lane_label(l: dict) -> str:
        raw = labels.get(l["id"], l["id"])
        return f"{raw} ({len(l['leaves'])})" if l["is_meta"] else raw

    title_w = min(32, max((len(lane_label(l)) for l in layout["lanes"]), default=10))
    # Columns before the density bar: indent(3) + marker(1) + glyph(1) + space + handle(8) + space +
    # title + space. The ruler and the header meta both align to this so the c-ticks sit over the bar.
    bar_prefix = 3 + 1 + 1 + 1 + 8 + 1 + title_w + 1

    # Terminal-fit bar width: fill the space left after the fixed prefix so rows don't hard-wrap on a
    # narrow terminal (which would break lane alignment). Fall back to the proven 38 when the size is
    # unavailable or absurdly small; an explicit caller/test override is honoured untouched.
    if bar_width is None:
        cols = shutil.get_terminal_size(fallback=(0, 0)).columns
        bar_width = 38 if cols < 40 else max(12, cols - bar_prefix)

    ruler = _time_ruler(bar_prefix, bar_width, layout["commit_count"])
    if ruler:
        lines.append(dim(ruler))
    lanes_shown = 0
    total_lanes = len(layout["lanes"])
    for row in range(layout["row_count"]):
        if lanes_shown >= max_rows:
            break  # a huge history dumps thousands of lanes -- cap, like the save list does
        if row in headers_by_row:
            hd = headers_by_row[row]
            if lines and lines[-1] != "":
                lines.append("")  # breathing room between subsystems
            label = ("▾ " + hd["label"]).ljust(bar_prefix - 1)
            meta = f"{hd['lane_count']} feature(s)"
            lines.append(dim(f" {label} {meta}"))
        elif row in lanes_by_row:
            l = lanes_by_row[row]
            fid = l["id"]
            hexc = color_for(fid)
            is_sel = fid == selected
            glyph = "◈" if l["is_meta"] else "●"  # ◈ / ●
            marker = "▸" if is_sel else " "
            raw = lane_label(l)
            handle = brighten_prefix(fid, hexc)  # copy-paste token; bright = the minimal unique prefix
            label = _ellipsize(raw, title_w - 1).ljust(title_w)  # cap + ellipsize a long label
            bar = time_bar(l["cars"], hexc, bar_width)
            # Checkpoints spelled out as `@n label`: the `@n` is the `sgt revert` handle, the label
            # its human name. Only the last 3 (most recent) are named -- the rest fold into a `+N
            # earlier` head -- and each label is ellipsized so a busy lane can't run off the edge; the
            # density bar still shows every checkpoint's position, so nothing is lost, just unlabelled.
            cars = l["cars"]
            recent = cars[-3:]
            chips = [f"@{c['seg_index']} {_ellipsize(c['label'], 24)}" for c in recent]
            hidden = len(cars) - len(recent)
            if hidden > 0:
                chips.insert(0, f"+{hidden} earlier")
            trailer = ("  " + dim("  ·  ".join(chips))) if chips else ""
            planned = ghost_by_lane.get(fid)
            if planned:
                trailer += "  " + dim("  ".join(f"{_GHOST} planned: {_ellipsize(t, 24)}"
                                                for t in planned))
            row_s = (f"   {marker}{paint(hexc, glyph)} {handle} "
                     f"{bold(label) if is_sel else label} {bar}{trailer}")
            row_s += links_note(fid)
            lines.append(row_s)
            lanes_shown += 1
    if total_lanes > lanes_shown:
        lines.append("")
        lines.append(dim(f" …{total_lanes - lanes_shown} more feature(s) "
                         f"({lanes_shown} of {total_lanes} shown)"))
    for g in unplaced_ghosts:
        lines.append(" " + dim(f"{_GHOST} planned (no lane yet): {_ellipsize(g.get('title', ''), 40)}"))
    lines.append("")

    # Legend: the view explains its own encoding.
    lines.append(dim(" ▁▂▃▄▅▆▇█ = edit density (taller = busier), positioned by save-time (c0…cN above)"
                     "   ·   dim · = a quiet span   ·   trailing @n chips = the checkpoints (rewind by @n)"))
    lines.extend(_state_banner(states, color=color))
    return lines


def render_verb_preview_lines(
    map_view: dict,
    grid_view: dict,
    segments: list[dict] | None,
    preview_view: dict,
    *,
    focus_fid: str | None,
    color: bool = True,
    frame: str = "after",
) -> list[str]:
    """The **feedforward** graph for a revert/restore: instead of applying blind, draw *where it
    lands*. The target feature's checkpoints are shown vertically (like `--focus`) with the affected
    slice marked -- `▸`/`✗` a checkpoint the edit removes, `◐` one partly touched, else `kept`; a
    restore marks the re-added slice instead. Below it, the OTHER features the edit reaches, each
    carrying its op-count `before → after` and its role (`blast` loses ops -> must re-draft;
    `foundation` a locked prerequisite it stands on), then a `· N unchanged` floor for the dim
    context -- the "Focus & Morph" grammar the webview also renders, sourced from the same
    `focus_subgraph` projection so both surfaces agree.

    `frame` toggles the before/after view of the morph (the terminal's stand-in for the animated
    webview): `"after"` (default) ghosts the removed checkpoints (`✗`) and shows each lane's
    post-edit count; `"before"` shows them still present so the user can compare. Pure over the
    `_project_verb_preview` dict + the same map/grid/segments the log reads, so it's testable and
    shares `_render_car`/`color_for` with the overview. The `[y/N]` prompt and any capped diff stay
    in the CLI caller."""
    segments = segments or []
    verb = preview_view.get("verb", "revert")
    target = preview_view.get("target", "")
    removed = set(preview_view.get("removed", []))
    added = set(preview_view.get("added", []))
    touched = removed if verb == "revert" else added
    fnodes = (preview_view.get("focus") or {}).get("nodes", [])
    focus_by_fid = {n["feature_id"]: n for n in fnodes}
    context_count = (preview_view.get("focus") or {}).get("context_count", 0)
    files = preview_view.get("files", {}) or {}

    layout = segment_layout(map_view, grid_view, segments)
    labels = {n["id"]: n.get("label", n["id"]) for n in map_view.get("nodes", [])}
    prefix_len = _min_unique_prefixes(list(layout["node_by_id"]))

    def bp(fid: str, hexc: str) -> str:
        # Bare-hex handle (no `f-`), minimal-unique prefix brightened -- matches the overview's handles.
        has_tag = fid.startswith("f-")
        disp = (fid[2:] if has_tag else fid)[:8]
        k = max(0, min(prefix_len.get(fid, 5) - (2 if has_tag else 0), len(disp)))
        head = _bold(_paint(hexc, disp[:k], color=color), color=color) if color else disp[:k]
        return head + _dim(disp[k:], color=color)

    # Per-checkpoint status for the target feature: is the whole segment in the edit's op-set, some
    # of it, or none? (segments carry their `op_ids`; the car dict drops them, so join on segments.)
    # Keep the op total + the touched count so the magnitude bar can split a *partial* checkpoint
    # into its kept vs. leaving cells, not just flag it.
    seg_status: dict = {}
    for seg in segments:
        if seg.get("feature_id") != focus_fid:
            continue
        ops = set(seg.get("op_ids", []))
        hit = ops & touched
        status = "gone" if (ops and hit == ops) else ("partial" if hit else "kept")
        seg_status[seg["seg_index"]] = (status, len(ops), len(hit))

    lines: list[str] = []
    action = "rewind" if verb == "revert" else "restore"
    if focus_fid:
        hexc = color_for(focus_fid)
        flabel = labels.get(focus_fid, focus_fid)
        tgt = focus_by_fid.get(focus_fid)
        delta = ""
        if tgt:
            ob, oa = tgt["ops_before"], tgt["ops_after"]
            present = ob if frame == "before" else oa
            bar = _magnitude_bar(ob, oa, present, hexc, color=color)
            delta = f"  {bar}  " + _dim(f"{ob}→{oa} edits", color=color)
        lines.append(f" {_paint(hexc, '▸', color=color)} {_bold(action, color=color)}  "
                     f"{_bold(flabel, color=color)}  {bp(focus_fid, hexc)}{delta}")
        # The typeable form of what is about to run -- a long content-hash target collapses to
        # the same 8-char handle every other surface prints.
        short = target[2:10] if (target.startswith("f-") and len(target) > 20) else (
            target[:8] if re.fullmatch(r"[0-9a-f]{40,}", target) else target)
        lines.append(_dim(f"      sgt {verb} {short}", color=color))
        lines.append("")

        lane = layout["node_by_id"].get(focus_fid)
        if lane is None or not lane.get("cars"):
            lines.append(_dim("   (no checkpoints cached -- run `sgt log --refresh` to name them)", color=color))
        else:
            gone_word = "removed" if verb == "revert" else "restored"
            stem = gone_word.rstrip("d").rstrip("e")  # remov / restor
            first_gone = True
            for car in lane["cars"]:
                idx = car["seg_index"]
                st, total, leaving = seg_status.get(idx, ("kept", car["op_count"], 0))
                head = _render_car(car, 6, hexc, color=color)
                raw = f"@{idx} {car['label']}"
                tail = _ellipsize(raw, 42).ljust(42)
                # The checkpoint's own two-state span, so its bar fills/empties on the `b` toggle in
                # lockstep with the header: revert peels `leaving` ops off `total`, restore adds them.
                kept = max(0, total - leaving)
                seg_before, seg_after = (total, kept) if verb == "revert" else (kept, total)
                present = seg_before if frame == "before" else seg_after
                bar = _magnitude_bar(seg_before, seg_after, present, hexc, color=color)
                if st == "kept":
                    lines.append(f"     {head}  {_dim(tail, color=color)}  {bar}  {_dim('· kept', color=color)}")
                    continue
                if st == "partial":
                    mark = _dim("◐", color=color)
                    note = f"· {leaving}/{total} edits {gone_word}"
                else:  # fully touched: ▸ leads the slice; ✗ ghosts only a *removed* car (revert/after)
                    if first_gone:
                        mark = _paint(hexc, "▸", color=color)
                    elif verb == "revert" and frame != "before":
                        mark = _dim("✗", color=color)
                    else:
                        mark = " "
                    first_gone = False
                    note = (f"· will be {gone_word}" if frame == "before"
                            else f"· {gone_word}")
                lines.append(f"   {mark} {head}  {_dim(tail, color=color)}  {bar}  {_dim(note, color=color)}")
        lines.append("")
    else:
        # No feature context (a single-op target on an un-mapped tree): skip the checkpoint rail and
        # let the affected-features section + summary carry the feedforward. No "?" placeholder lane.
        lines.append(f" {_paint('#888888', '▸', color=color)} {_bold(action, color=color)}  "
                     f"{_dim(target, color=color)}")
        lines.append("")

    # The focus subgraph beyond the target: each reached feature with its op-count `before → after`
    # and role -- the "morph" numbers a terminal shows in place of the webview's animation. Blast
    # loses ops (must re-draft); foundation is the kept prerequisite the edit stands on.
    others = [n for n in fnodes if n["feature_id"] != focus_fid]
    if others:
        lines.append(_dim(" also affected", color=color))
        for n in others[:8]:
            afid = n["feature_id"]
            ahex = color_for(afid)
            albl = labels.get(afid, n.get("label", afid))
            ob, oa = n["ops_before"], n["ops_after"]
            present = ob if frame == "before" else oa
            bar = _magnitude_bar(ob, oa, present, ahex, color=color)
            if n["role"] == "foundation":
                glyph = _paint(ahex, "◈", color=color)
                badge = "prerequisite, kept" if ob == oa else f"gains {oa - ob} edits"
            else:  # blast (target is drawn as the rail above, never here)
                glyph = _paint(ahex, "●", color=color)
                badge = f"loses {ob - oa} edits, re-draft"
            note = _dim(badge, color=color)
            lines.append(f"   {glyph} {_ellipsize(albl, 28).ljust(28)}  {bar}  {note}")
        if len(others) > 8:
            lines.append(_dim(f"   +{len(others) - 8} more feature(s)", color=color))
        lines.append("")
    if context_count:
        lines.append(_dim(f" · {context_count} other feature(s) unchanged", color=color))

    n_op = len(removed) if verb == "revert" else len(added)
    verbword = "removes" if verb == "revert" else "restores"
    frame_hint = "" if frame == "after" else "  · showing before"
    syms = [s for s in preview_view.get("affected_symbols", []) if "::__" not in s]
    sym_note = f" across {len(syms)} symbol(s)" if syms else ""
    shown_files = sorted(files)[:4]
    file_note = ", ".join(shown_files) + (f" +{len(files) - 4} more" if len(files) > 4 else "")
    lines.append(_dim(f" {verbword} {n_op} edit(s){sym_note} · "
                      f"{len(files)} file(s): {file_note}{frame_hint}"
                      if files else
                      f" {verbword} {n_op} edit(s){sym_note} · no file changes{frame_hint}",
                      color=color))
    return lines


def _render_sync_preview_lines(preview_view: dict, *, color: bool = True) -> list[str]:
    """The `sync` feedforward: what folding a teammate's branch in would bring. A sync never blocks
    -- the fork-free part always merges -- so a fork is drawn as *surfacing* (work waits at the
    common ancestor, not lost), and a degraded base/lost-provenance tip is surfaced loudly (R12)."""
    src = f"{preview_view.get('remote', '')}/{preview_view.get('target', '')}"
    ops_added = preview_view.get("ops_added", 0)
    forks = preview_view.get("forks", []) or []
    contradictions = preview_view.get("pin_contradictions", []) or []
    cycles = preview_view.get("declared_cycles", []) or []
    tagline = _dim("fold in a teammate's work", color=color)

    lines: list[str] = [
        f" {_paint('#5fafff', '▸', color=color)} {_bold('sync', color=color)}  "
        f"{_bold(src, color=color)}  {tagline}",
        "",
        f"   {_paint('#5fafff', '↓', color=color)} brings in "
        f"{_bold(str(ops_added), color=color)} op(s) from {src}",
        "",
    ]

    if forks:
        lines.append(_paint("#ffaf00", f"   ⚠ {len(forks)} fork(s) surface -- the fork-free work "
                                       f"still merges; resolve these when ready (nothing is lost):",
                            color=color))
        for sym, a, b in forks[:8]:
            remedy = _dim(f"sgt resolve {sym}", color=color)
            lines.append(f"       {_paint('#ffaf00', sym, color=color)}   {remedy}")
        if len(forks) > 8:
            lines.append(_dim(f"       +{len(forks) - 8} more fork(s)", color=color))
        lines.append("")

    for c in contradictions:
        lines.append(_paint("#ffaf00", f"   ⚠ pin contradiction: {c.get('detail', '')}", color=color))
    for pair in cycles:
        lines.append(_paint("#ffaf00", f"   ⚠ declared-edge cycle: {pair}", color=color))
    if contradictions or cycles:
        lines.append("")

    # R12 loudness: a degraded base or a lost-provenance tip fell back to weaker semantics. Never
    # silent -- name the recovery path that was refused, exactly as the post-merge report does.
    if preview_view.get("base_recovery") == "none":
        lines.append(_paint("#ff5f5f", "   ⚠ base recovery: none -- no witnessed merge-base; union "
                                       "semantics (cannot delete work one side removed)", color=color))
    if preview_view.get("theirs_recovery") == "none":
        lines.append(_paint("#ff5f5f", "   ⚠ theirs' tip has sgt ops but no witnessed trailers -- "
                                       "re-mine on their side, then sync again", color=color))

    lines.append("")
    tail = (f" folds in {ops_added} op · {len(forks)} fork(s) surface to resolve · not auto-undoable"
            if forks else f" folds in {ops_added} op · no forks · not auto-undoable")
    lines.append(_dim(tail, color=color))
    return lines


def _render_resolve_preview_lines(preview_view: dict, *, color: bool = True) -> list[str]:
    """The `resolve <symbol>` feedforward: the three-step remedy `--apply` runs to close a fork --
    fulfill the drafted reconciliation from the edited tree, run the oracle, land it. An unclean plan
    (no open fork, or no drafted reconciliation yet) renders its `error` alone."""
    sym = preview_view.get("target", "")

    if not preview_view.get("clean", True):
        return [f" {_paint('#cc5500', '✗', color=color)} {_bold('resolve', color=color)}  "
                f"{_dim(sym, color=color)}",
                "",
                _dim(f"   {preview_view.get('error', 'not resolvable')}", color=color)]

    tips = preview_view.get("tips", []) or []
    lines: list[str] = [
        f" {_paint('#5fafff', '▸', color=color)} {_bold('resolve', color=color)}  "
        f"{_bold(sym, color=color)}  {_dim('reconcile a same-symbol fork', color=color)}",
        "",
    ]
    if len(tips) == 2:
        lines.append(f"   {_dim('tips', color=color)} "
                     f"{_paint('#ffaf00', tips[0][:8], color=color)} "
                     f"{_dim('↔', color=color)} {_paint('#ffaf00', tips[1][:8], color=color)}")
        lines.append("")
    lines.append(f"   {_paint('#5fafff', '1', color=color)} fulfill your merged edit from the tree")
    lines.append(f"   {_paint('#5fafff', '2', color=color)} run the oracle on the reconciled candidate")
    lines.append(f"   {_paint('#5fafff', '3', color=color)} land it -- closes the fork on {sym}")
    lines.append("")
    if not preview_view.get("oracle_configured", True):
        lines.append(_paint("#ffaf00", "   oracle: none configured -- the reconciliation lands "
                                       "unverified", color=color))
    else:
        lines.append(_dim("   oracle: green required -- runs the tests on confirm before landing",
                          color=color))
    lines.append("")
    lines.append(_dim(f" fulfill + oracle + land · closes the fork on {sym} · not auto-undoable",
                      color=color))
    return lines


def render_collab_preview_lines(preview_view: dict, *, color: bool = True) -> list[str]:
    """The **feedforward** graph for a collaboration verb -- `land`, `sync`, `propose land`, or
    `resolve` -- drawn before the one-way step runs. Unlike a revert's checkpoint rail, the
    consequence here is *where your work is going and what stops it*: for `land`/`propose land` the
    op count it would advance the shared branch by, any fork that BLOCKS it, and the LAW-G oracle
    gate; for `sync` the op count it folds *in* and any fork that SURFACES (a sync never blocks --
    the fork-free part still merges); for `resolve` the three-step remedy it runs. Pure over the
    projection dict; the `[y/N]` prompt stays in the CLI caller. An unclean plan (`clean` False)
    renders its `error` alone."""
    verb = preview_view.get("verb", "land")
    if verb == "sync":
        return _render_sync_preview_lines(preview_view, color=color)
    if verb == "resolve":
        return _render_resolve_preview_lines(preview_view, color=color)

    branch = preview_view.get("target", "")
    forks = preview_view.get("forks", []) or []
    contradictions = preview_view.get("pin_contradictions", []) or []
    cycles = preview_view.get("declared_cycles", []) or []
    advisory = preview_view.get("advisory")

    if not preview_view.get("clean", True):
        return [f" {_paint('#cc5500', '✗', color=color)} {_bold('land', color=color)}  "
                f"{_dim(branch, color=color)}",
                "",
                _dim(f"   {preview_view.get('error', 'not landable')}", color=color)]

    ops_added = preview_view.get("ops_added", 0)
    lines: list[str] = [
        f" {_paint('#5fafff', '▸', color=color)} {_bold(verb, color=color)}  "
        f"{_bold(branch, color=color)}  {_dim('advance the shared branch', color=color)}",
        "",
    ]
    # Only claim "adds N op" when there's something to advance -- when a fork blocks and nothing
    # else would land, "adds 0 op" is noise the fork line already explains.
    if ops_added:
        lines.append(f"   {_paint('#5fafff', '↑', color=color)} your work adds "
                     f"{_bold(str(ops_added), color=color)} op(s) to {branch}")
        lines.append("")

    if forks:
        lines.append(_paint("#ffaf00", f"   ⚠ {len(forks)} fork(s) block the land -- "
                                       f"reconcile before it can advance:", color=color))
        for sym, a, b in forks[:8]:
            remedy = _dim(f"sgt resolve {sym}", color=color)
            lines.append(f"       {_paint('#ffaf00', sym, color=color)}   {remedy}")
        if len(forks) > 8:
            lines.append(_dim(f"       +{len(forks) - 8} more fork(s)", color=color))
        lines.append("")

    for c in contradictions:
        lines.append(_paint("#ffaf00", f"   ⚠ pin contradiction: {c.get('detail', '')}", color=color))
    for pair in cycles:
        lines.append(_paint("#ffaf00", f"   ⚠ declared-edge cycle: {pair}", color=color))
    if contradictions or cycles:
        lines.append("")

    # The LAW-G gate: name what the confirm will (or won't) do. A fork short-circuits before the
    # oracle; no oracle configured refuses outright; otherwise the oracle runs the tests on confirm.
    if not preview_view.get("oracle_configured", True):
        lines.append(_paint("#ff5f5f", "   oracle: none configured -- land refuses to advance an "
                                       "unverified op-set (LAW-G)", color=color))
    elif forks:
        lines.append(_dim("   oracle: not reached -- the fork blocks the land first", color=color))
    else:
        lines.append(_dim("   oracle: green required -- runs the tests on confirm, then CAS onto "
                          "the tip", color=color))

    if advisory:
        lines.append("")
        lines.append(_paint("#ffaf00", f"   ⚠ {advisory}", color=color))

    lines.append("")
    if forks:
        tail = f" won't advance -- {len(forks)} fork(s) to resolve first"
    elif not preview_view.get("oracle_configured", True):
        tail = " won't advance -- no oracle to verify against (LAW-G)"
    else:
        tail = f" advances {branch} by {ops_added} op · runs tests on confirm · not auto-undoable"
    lines.append(_dim(f" {tail.strip()}", color=color))
    return lines


# ── Episode rail render (vertical git-log) ───────────────────────────────────────────────────────


def render_rail_lines(
    map_view: dict,
    grid_view: dict,
    *,
    selected: str | None = None,
    color: bool = True,
    label_width: int = 44,
    max_rows: int = 40,
    only_features: set | None = None,
    group_label: str | None = None,
    states: dict | None = None,
) -> list[str]:
    """Render the episode rail as a vertical git-log (Stage C): newest episode on top. Recurring
    features (touched by >=2 saves) each get a dedicated lane so their saves read as one unbroken
    vertical line (● at each touched save, │ carried across the saves between); one-off features
    pack into a shared pool lane. Each row is one commit-episode -- the "what I did, in order" rewind
    unit -- with its commit position (cN), subject, and the feature(s) it touched (bold ● marks the
    save's dominant one). Capped at `max_rows` (newest first); a footer notes how many older episodes
    were folded (the lazy nod for a long history)."""
    ep = episodes(map_view, grid_view)
    # Category focus (`--focus <subsystem|theme>`): keep only saves that touched a feature in the
    # group, restrict each kept save's feature set to the group, and re-pick its dominant feature --
    # so the rail's lanes are purely the group's features, laid out by `episode_rail_layout` as usual.
    if only_features is not None:
        only = set(only_features)
        filtered = []
        for e in ep["episodes"]:
            feats = {f: n for f, n in (e.get("features") or {}).items() if f in only}
            if not feats:
                continue
            dom = max(feats, key=lambda f: (feats[f], f))
            filtered.append({**e, "features": feats, "dominant_feature": dom})
        ep = {"episodes": filtered, "groups": []}
    layout = episode_rail_layout(ep)
    labels = {n["id"]: n.get("label", n["id"]) for n in map_view.get("nodes", [])}
    rows = layout["rows"]
    lane_count = layout["lane_count"]
    lane_intervals = layout["lane_intervals"]
    feature_touched = {f: set(rs) for f, rs in layout["feature_touched"].items()}
    n_recurring = len(layout["recurring"])

    def paint(hex_str: str, s: str) -> str:
        return _fg(hex_str, s) if color else s

    def dim(s: str) -> str:
        return f"{_DIM}{s}{_RESET}" if color else s

    def bold(s: str) -> str:
        return f"{_BOLD}{s}{_RESET}" if color else s

    def cell(lane: int, r_idx: int, is_dom: bool) -> str:
        """One rail cell. ● where the lane's feature touched this save (bold = the save's dominant
        feature, its main work); │ where the feature is carried across a save it didn't touch (the
        connector that keeps a recurring feature one traceable line); blank where the lane is idle."""
        fid = next((f for top, bot, f in lane_intervals.get(lane, ()) if top <= r_idx <= bot), None)
        if fid is None:
            return " "
        hexc = color_for(fid or "")
        if r_idx in feature_touched.get(fid, ()):  # noqa: SIM118 -- membership, not iteration
            dot = paint(hexc, "●")
            return bold(dot) if is_dom else dot
        return paint(hexc, "│")

    lines: list[str] = []
    n_ep = len(rows)
    n_feat = len({r["feature"] for r in rows if r["feature"] is not None})
    recur_note = f"  ·  {n_recurring} recurring" if n_recurring else ""
    if group_label:
        lines.append(bold(f" focus: {group_label}  ·  {n_feat} feature(s)  ·  {n_ep} save(s)"
                          f"   (newest on top)"))
    else:
        lines.append(bold(f" {n_ep} save(s)  ·  {n_feat} feature(s){recur_note}   (newest on top)"))
    lines.append(dim(" each row = one save (cN = its commit position); ● = feature touched here "
                     "(bold ● = the save's main one), │ = a feature carried across"))
    lines.append("")

    shown = rows[:max_rows]
    # Plan ghosts (pending steps, no code yet): a ◇ row above the newest save on its predicted
    # lane; a prediction whose feature has no rail lane (it dominates no save) drops to an
    # "unplaced" gutter after the rail. Read straight off `grid_view.ghosts` -- no new input.
    all_ghosts = grid_view.get("ghosts", [])
    if only_features is not None:  # a group focus shows only that group's plan steps, not every plan's
        all_ghosts = [g for g in all_ghosts if g["feature_id"] in only_features]
    placed_ghosts = [g for g in all_ghosts if g["feature_id"] in layout["lane_of"]]
    unplaced_ghosts = [g for g in all_ghosts if g["feature_id"] not in layout["lane_of"]]
    pos_w = max((len(f"c{r['index']}") for r in shown), default=2)
    if placed_ghosts:
        pos_w = max(pos_w, len("plan"))
    for g in placed_ghosts:
        lane = layout["lane_of"][g["feature_id"]]
        fid = g["feature_id"]
        hexc = color_for(fid or "")
        rail = " ".join(dim(paint(hexc, _GHOST)) if L == lane else " " for L in range(lane_count))
        pos = dim("plan".rjust(pos_w))
        subj = _ellipsize(g.get("title", "") or "planned", label_width).ljust(label_width)
        lines.append(f" {rail}  {pos} {' ' * 7}  {dim(subj)}  {_paint(hexc, labels.get(fid, fid), color=color)}")
    for r in shown:
        rail = " ".join(cell(L, r["row"], L == r["lane"]) for L in range(lane_count))
        pos = dim(f"c{r['index']}".rjust(pos_w))
        sha = dim((r["sha"] or "")[:7])
        head = _row_headline(r["subject"], r["feature"], labels)
        subj = _ellipsize((head or "").replace("\n", " "), label_width).ljust(label_width)
        subj_s = bold(subj) if r["feature"] == selected else subj
        lines.append(f" {rail}  {pos} {sha}  {subj_s}  {_chips(r, labels, color=color)}")

    if n_ep > len(shown):
        lines.append("")
        lines.append(dim(f" {n_ep - len(shown)} older save(s) folded (newest {len(shown)} shown)"))
    for g in unplaced_ghosts:
        lines.append(" " + dim(f"{_GHOST} planned (no lane yet): {_ellipsize(g.get('title', ''), label_width)}"))
    lines.append("")
    example = next((r["feature"] for r in shown if r["feature"]), None)
    handle = (example[2:10] if example and example.startswith("f-") else (example or "<feature>")[:8])
    lines.append(dim(f" next:  sgt log --map  (the feature map)   ·   sgt log --focus {handle}  "
                     f"(one feature's checkpoints)   ·   sgt revert {handle}  (remove that feature)"))
    lines.extend(_state_banner(states, color=color))
    return lines
