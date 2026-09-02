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


def plural(n: int, noun: str) -> str:
    """`3 symbols`, `1 symbol`. Not `1 symbol(s)`.

    `(s)` is a placeholder for a decision nobody made, and it is on the first line of sgt a new
    reader meets: `sgt log --tree` prints `· 1 symbol(s)` under every feature. It costs one branch to
    write what a person would write. Only the regular nouns sgt counts pass through here (symbol,
    feature, file, save, edit, op), so appending "s" is the whole rule."""
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"

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

    # Aggregate ops -> op count + first/last commit + the sorted commit list. Drop lanes with no ops,
    # and lanes whose feature holds no symbols of its own: a cluster whose ops touch only the
    # residue/anchor sentinels is not a feature anyone can act on -- it draws a full lane, answers
    # `sgt show` with "0 symbols in 0 files", and reverting it removes nothing. A pilot participant
    # read one of those rows ("Section Waitlist") as the waitlist and reverted it, which silently
    # did nothing while the live waitlist sat in a feature this filter had crowded off the map.
    # Same "drop what has nothing to show" rule `sgt log --tree` applies (`views.tree_lines`),
    # on the same set, so the two views of one hierarchy never disagree about how many there are.
    lanes = []
    for v in visible:
        # Husks leave the leaf SET, not just the listing. `(N)` on a folded row, a header's
        # `N feature(s)` and the view's headline total are all leaf counts, so a husk counted inside a
        # fold promises rows that opening the fold does not deliver -- on the pilot fixture that put
        # the map's total four features above `sgt log --tree`'s for one and the same repo, which is
        # the arithmetic behind "I can't match this view against the others".
        leaves = [leaf for leaf in v["leaves"]
                  if by_id.get(leaf, {}).get("own_symbols", ("?",))]
        if not leaves:
            continue
        v = {**v, "leaves": leaves}
        commits = [op["commit_index"] for leaf in leaves for op in ops_by_feature.get(leaf, [])]
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

    # Emit rows in TREE order so nesting is visible: walk the map from its roots; at each level
    # siblings are ordered by first appearance (min descendant first_commit); an expanded subsystem is
    # a header row with its descendants rendered one level deeper; a collapsed subsystem is a single
    # meta-lane; a feature is a lane. `depth` (root = 0, +1 per subsystem level) drives the render
    # indent, so a sub-subsystem steps in visually under its parent instead of flattening onto the
    # same level.
    #
    # This used to build a FLAT list of groups sorted globally by first_commit, which lost the
    # parent->child relation entirely: a collapsed subsystem became its own top-level row wherever
    # its first commit happened to fall, so a child subsystem was routinely printed *above* the
    # header of the parent that contains it. On the pilot's repo that read as three top-level groups
    # plus a fourth group that also contained one of them, and it is the whole reason `sgt log --map`
    # could not be matched against the workbench or the sidebar, both of which show the real tree.
    # `computeGraphLayout` in `editor/vscode/media/workbench.js` was fixed to walk the tree; this is
    # the same walk, so the two surfaces order and nest identically (`test_nested_subsystems_*`).
    lane_set = {l["id"] for l in lanes}

    def children_of(node_id: str) -> list[str]:
        return (by_id.get(node_id) or {}).get("children") or []

    # A node earns a row iff it's a lane, or an expanded subsystem with >=1 descendant lane.
    present_cache: dict[str, bool] = {}

    def is_present(node_id: str) -> bool:
        if node_id in present_cache:
            return present_cache[node_id]
        present = node_id in lane_set
        if not present:
            present = any(is_present(c) for c in children_of(node_id))
        present_cache[node_id] = present
        return present

    # Earliest first_commit anywhere under a node (a lane returns its own) -- the per-level sort key.
    first_cache: dict[str, float] = {}

    def subtree_first(node_id: str) -> float:
        if node_id in first_cache:
            return first_cache[node_id]
        lane = lane_by_id.get(node_id)
        best = float(lane["first_commit"]) if lane else float("inf")
        if lane is None:
            for c in children_of(node_id):
                best = min(best, subtree_first(c))
        first_cache[node_id] = best
        return best

    def rollup(node_id: str) -> tuple[int, int, int]:
        """``(op_count, feature_count, last_commit)`` over every descendant feature lane. The middle
        number counts FEATURES, not rows: a collapsed subsystem contributes its leaves. `lane_count`
        used to be `len(lane_objs)`, so a header sitting above a collapsed child reported the child
        as one feature and disagreed with every other surface on the size of the same group."""
        op_count = lane_count = 0
        last = -1

        def walk(nid: str) -> None:
            nonlocal op_count, lane_count, last
            lane = lane_by_id.get(nid)
            if lane:
                op_count += lane["op_count"]
                last = max(last, lane["last_commit"])
                lane_count += len(lane["leaves"]) if lane["is_meta"] else 1
            else:
                for c in children_of(nid):
                    walk(c)

        walk(node_id)
        return op_count, lane_count, last

    def sort_key(node_id: str) -> tuple[float, str]:
        return (subtree_first(node_id), node_id)

    def ordered_children(node_id: str | None) -> list[str]:
        """One parent's rows, in reading order: its own feature lanes first, then its sub-groups --
        each half in first-appearance order. (`node_id=None` asks for the roots.)

        Ordering the whole level by first-commit alone interleaved the two kinds, and a subsystem is
        not one row but a block: a feature that started after a subsystem did was emitted *below*
        that subsystem's entire subtree, where the indent then read as membership. On seedbank-v3
        that put four of the root's own features (`seed catalog`, `sort the grid`, `show what is on
        the shelf`, `seed tray`) under the `Plant Discovery` header, which announces 6 features
        above 9 rows -- so the one number the reader can check against the rows disagrees with them.
        Grouping is what lets the indent mean containment; within a group, time still orders.

        The halves split by NODE KIND, not by "is it a lane": a COLLAPSED subsystem is a lane
        (one meta row) while an expanded one is a header, so splitting on lane-ness moved the
        whole block from the leaves half to the groups half whenever its fold state changed --
        in the workbench, expanding a subsystem visibly teleported it below every feature and
        the reader lost the row they had just clicked. Kind is fold-invariant, so a subsystem
        occupies the same slot folded and open."""
        kids = (map_view.get("roots") or []) if node_id is None else children_of(node_id)
        present = [c for c in kids if is_present(c)]
        is_sub = lambda c: (by_id.get(c) or {}).get("kind") == "subsystem"  # noqa: E731
        leaves = sorted((c for c in present if not is_sub(c)), key=sort_key)
        groups = sorted((c for c in present if is_sub(c)), key=sort_key)
        return leaves + groups

    row = 0
    headers = []
    emitted: set[str] = set()

    def emit(node_id: str, depth: int) -> None:
        nonlocal row
        # The map is a DAG, so the same node can be reached down two paths. `visit` above gives it one
        # lane; give it one row too, or the second visit overwrites `row` and leaves a blank line where
        # the first one was.
        if node_id in emitted:
            return
        emitted.add(node_id)
        lane = lane_by_id.get(node_id)
        if lane is not None:  # a feature leaf or a collapsed-subsystem meta-lane -- no recursion
            lane["row"] = row
            lane["depth"] = depth
            lane["group_key"] = (by_id.get(node_id) or {}).get("parent")
            row += 1
            return
        node = by_id.get(node_id)
        if not node or node.get("kind") != "subsystem" or not is_present(node_id):
            return
        op_count, lane_count, last_commit = rollup(node_id)
        headers.append({
            "key": node_id, "label": node.get("label", node_id), "collapsed_id": node_id,
            "row": row, "depth": depth, "first_commit": subtree_first(node_id),
            "last_commit": last_commit, "op_count": op_count, "lane_count": lane_count,
        })
        row += 1
        for c in ordered_children(node_id):
            emit(c, depth + 1)

    for r in ordered_children(None):
        emit(r, 0)

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
        # Work a revert took out of the ideal that no lane and no chapter holds -- clustering keeps
        # only alive symbols, so a reverted symbol's ops lose their leaf and with it their row. The
        # layout has no way to derive this (the ops are precisely the ones absent from every cell it
        # is given), so `sgt.api.grid_view` computes it and the header reports it. A payload without
        # the key is no claim, not a report of zero.
        "reverted_unaccounted": grid_view.get("reverted_unaccounted") or {},
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
                    # Whether this chapter's ops are still in HEAD's ideal (`present_op_count`, from
                    # `sgt.api._segments_out`). A revert leaves the chapter in the store and takes it
                    # out of the ideal, so a renderer that reads only the store cannot tell a rewound
                    # chapter from a live one. `None` -- an unreadable ideal, or a client that predates
                    # the field -- is no claim, and must not read as removed.
                    "present_op_count": seg.get("present_op_count"),
                    "reverted": seg.get("present_op_count") == 0,
                    "tier": seg["tier"],
                    "source": seg["source"],
                    "first_index": seg["first_index"],
                    "last_index": seg["last_index"],
                    "sub_bins": sorted(bins.items()),
                    "is_future": seg["first_index"] > fr,
                    "asks": seg.get("asks", []),  # captured asks for this chapter (zoom render)
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


def episode_rail_layout(ep_view: dict, *, max_lanes: int | None = None) -> dict:
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

    `max_lanes` bounds the rail's width. Without it the pool grows to the interval chromatic number
    -- the most features live at any one row -- which is fine at 15 and is 60 columns of `│` on a
    repository large enough to need this view. Past the cap, features share one OVERFLOW lane drawn
    as a neutral `┆`: their dots still land (only one feature dominates a save, so a dot is never
    ambiguous), but the carried connector stops claiming to trace a single feature, because on a
    shared lane it no longer does. Unbounded stays the default so every existing caller is
    unchanged.

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
    def color_lanes(order, cap):
        """Greedy interval coloring over `order`, spilling past `cap` into a shared overflow lane."""
        lane_of: dict = {}
        lane_bot: list = []  # pool lane -> bottom row of the feature currently occupying it
        overflow: set = set()
        for fid in order:
            top, bot = span[fid]
            lane = next((L for L in range(len(lane_bot)) if lane_bot[L] < top), None)
            if lane is None:
                if cap is not None and len(lane_bot) >= cap:
                    overflow.add(fid)
                    lane_of[fid] = cap - 1
                    continue
                lane = len(lane_bot)
                lane_bot.append(bot)
            else:
                lane_bot[lane] = bot
            lane_of[fid] = lane
        return lane_of, lane_bot, overflow

    # First-row order draws the staircase a reader expects -- the newest feature on the left, each
    # older one stepping right -- so it is what runs whenever the rail fits. Only when the pool would
    # overrun the cap is it re-colored longest-span-first, which spends the scarce lanes on the long
    # unbroken lines that are the whole point of the view and spills the rest into one shared lane.
    # Sorting that way unconditionally would rearrange a gutter that had no need to change.
    by_first = sorted(span, key=lambda f: (span[f][0], str(f)))
    cap = None if max_lanes is None else max(1, max_lanes)
    lane_of, lane_bot, overflow = color_lanes(by_first, None)
    if cap is not None and len(lane_bot) > cap:
        by_span = sorted(span, key=lambda f: (-(span[f][1] - span[f][0]), span[f][0], str(f)))
        lane_of, lane_bot, overflow = color_lanes(by_span, cap)

    # A pooled lane can hold several one-off features over disjoint row-spans, so a lane maps to a
    # LIST of (top, bot, fid) intervals -- not one feature. A cell resolves which feature occupies the
    # lane at a given row by interval membership; collapsing to one fid per lane drops the dots of
    # every pooled feature but the last (a save with no node on its own dominant lane).
    lane_intervals: dict = {}
    for fid, L in lane_of.items():
        if fid in overflow:
            continue  # a shared lane has no single occupant, so it has no interval to resolve
        lane_intervals.setdefault(L, []).append((span[fid][0], span[fid][1], fid))
    overflow_rows = {r for fid in overflow for r in range(span[fid][0], span[fid][1] + 1)}
    rows = [
        {"index": e["index"], "row": row_of[e["index"]], "feature": e["dominant_feature"],
         "lane": lane_of.get(e["dominant_feature"], 0), "subject": e["subject"],
         "op_count": e["op_count"], "sha": e["sha"], "features": e["features"]}
        for e in ordered
    ]
    return {"rows": rows, "lane_of": lane_of, "lane_intervals": lane_intervals,
            "feature_touched": {f: sorted(rs) for f, rs in touched.items()},
            "feature_span": span, "recurring": sorted(str(f) for f in recurring),
            "overflow": sorted(str(f) for f in overflow), "overflow_rows": sorted(overflow_rows),
            "overflow_lane": (cap - 1) if overflow else None,
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
    all_forks = states.get("forks") or []
    # F82: a fork whose tips were mined under two MINER_VERSIONs is the same commit sitting in the
    # store twice, not two people editing one symbol. It costs the ideal just as much, so it stays
    # visible -- but `sgt resolve <symbol>` cannot close it and offering one per symbol is busywork
    # against a wrong diagnosis (612 hand-merges on sgt's own repo). One line, the real remedy.
    forks = [f for f in all_forks if not f.get("cross_version")]
    stale = [f for f in all_forks if f.get("cross_version")]
    if forks:
        out.append(_sgr(_RED, f" {_FORK} {plural(len(forks), 'open fork')} — divergent edits to one symbol:",
                        color=color))
        for f in forks:
            remedy = f"sgt resolve {f.get('symbol', '?')}"
            out.append(_dim(f"     {f.get('symbol', '?')}  →  {remedy}", color=color))
    if stale:
        out.append(_sgr(_AMBER, f" {_FORK} {plural(len(stale), 'fork')} are two mining generations of the "
                                f"same commit, not edits — the store mixes miner versions:",
                        color=color))
        out.append(_dim("     →  sgt advanced migrate ops-v3   (unifies the store; "
                        "`sgt advanced fsck` shows the versions present)", color=color))
    drafts = [d for d in (states.get("rewrites") or {}).get("drafts", []) if d.get("verb") == "merge-op"]
    if drafts:
        out.append(_sgr(_AMBER, f" {_MERGE} {plural(len(drafts), 'pending merge-op draft')}:", color=color))
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


def _names_a_leaf_feature(ref: str, by_id: dict) -> bool:
    """Whether `ref` names one leaf feature: its exact id, a unique id-prefix (bare or `f-`-prefixed,
    the two spellings the render prints), or an exact case-insensitive label. The same three rungs as
    `sgt.intent.segment.resolve_feature_spec`, inlined because this module stays import-free of the
    rest of sgt; `_resolve_focus` below mirrors the same ladder against a built layout. Ambiguity
    (two features, one name) is not a feature match -- the group reading gets its usual turn rather
    than this function guessing which of the two was meant."""
    leaves = {nid for nid, nd in by_id.items() if not nd.get("children")}
    if not ref:
        return False
    if ref in leaves:
        return True
    hits = [nid for nid in leaves if nid.startswith(ref) or nid.startswith("f-" + ref)]
    if len(hits) == 1:
        return True
    want = ref.strip().lower()
    return len([nid for nid in leaves if str(by_id[nid].get("label", "")).strip().lower() == want]) == 1


def resolve_focus_group(ref: str, map_view: dict, grid_view: dict, themes: dict | None = None):
    """Resolve `--focus`'s argument to a GROUP of features -- a subsystem (its feature leaves) or a
    theme (the features its commits touched) -- for the vertical category view. Returns
    `{"label", "kind", "feature_ids"}` or None when `ref` names no group, in which case the caller
    falls through to the single-lane `render_graph_lines(focus=...)` path.

    A subsystem is matched by unique id-prefix or exact (case-insensitive) label against the
    `kind=="subsystem"` nodes; a theme by exact label against `themes` (the committed
    `.sgt/intent/themes.json`, `{theme_id: {label, atom_shas, ...}}`). The theme→feature join goes
    through `grid_view`: a theme's `atom_shas` -> commit indices -> the features whose cells sit on
    those commits. `themes` defaults empty so a repo with no built themes still resolves subsystems.

    A name that names a LEAF FEATURE resolves to no group, whatever else it also names. A theme is
    minted per save and carries the save message as its label, and a feature that still carries its
    own save-message label therefore collides by construction -- two thirds of the pilot fixture's
    features shared a name with a theme, and a promoted lone feature shares one with its subsystem.
    The group used to win those, so `--focus "<feature>"` answered with a rail for a different set
    of features and the feature's checkpoint detail -- the only screen that prints the `@n` handles
    the map's chips say to rewind by -- could not be reached by name at all. `--focus`'s metavar is
    FEATURE and two footers advertise it as "its checkpoints"; a group keeps its own id."""
    by_id = {n["id"]: n for n in map_view.get("nodes", [])}
    want = (ref or "").strip().lower()

    if _names_a_leaf_feature(ref, by_id):
        return None

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
            return {"label": thits[0]["label"], "kind": "theme", "feature_ids": feats,
                    "commit_indices": want_idx}
    return None


def theme_spans(themes: dict, map_view: dict, grid_view: dict) -> list[dict]:
    """The cross-feature themes joined to the lanes they span, for the map's own rows: each
    multi-feature theme's label, the LABELS (not hashes) of the features its commits touched,
    and its per-commit edit density on the SHARED axis -- so the map can draw the spanning work
    as rows whose marks line up column-for-column with the lane blocks above, instead of a prose
    footer the reader has to join by hand. The join is the same commit->cell walk
    `resolve_focus_group` does, so what these rows name and what `--focus` opens can never
    disagree. Single-feature themes are dropped -- their lane already tells that story."""
    labels = {n["id"]: n.get("label", n["id"]) for n in map_view.get("nodes", [])}
    idx_of = {c["sha"]: c["index"] for c in grid_view.get("commits", [])}
    cells_by_idx: dict[int, list[dict]] = {}
    for c in grid_view.get("cells", []):
        cells_by_idx.setdefault(c["commit_index"], []).append(c)
    out = []
    for t in (themes or {}).values():
        label = (t or {}).get("label", "").strip()
        # "(unwitnessed)" is the catch-all rollup for commits outside every theme -- it spans most
        # of the repo by construction, so as a ◆ row it would read as the repo's biggest work item.
        if not label or label == "(unwitnessed)":
            continue
        feats: set[str] = set()
        per: dict[int, int] = {}
        feat_at: dict[int, str] = {}  # dominant member lane per commit -- the row paints in ITS hue
        best_at: dict[int, int] = {}
        for sha in t.get("atom_shas", []):
            idx = idx_of.get(sha)
            if idx is None:
                continue
            for cell in cells_by_idx.get(idx, []):
                feats.add(cell["feature_id"])
                n = cell.get("op_count") or len(cell.get("op_ids") or ())
                per[idx] = per.get(idx, 0) + n
                if n >= best_at.get(idx, 0):
                    best_at[idx] = n
                    feat_at[idx] = cell["feature_id"]
        if len(feats) < 2:
            continue
        out.append({"label": label, "feature_ids": feats,
                    "feature_labels": sorted(labels.get(f, f) for f in feats),
                    "per_commit": per, "feature_at": feat_at, "op_count": sum(per.values())})
    out.sort(key=lambda t: (-len(t["feature_ids"]), t["label"]))
    return out


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

# The --map strip draws every feature on ONE shared commit axis: column `c` is the same span of
# commits in every row, so a vertical slice reads as "what was happening then" and two lanes that
# look adjacent really were. It used to pack each lane's own checkpoints left to right, which reads
# a single feature's life nicely but makes the map as a whole unreadable -- every row was a private
# clock, and nothing could be compared downward. A column a lane never touched is left blank, so
# quiet stretches show as real gaps rather than being squeezed away.

# Checkpoint chips are a LIST, packed at their natural width right under the label they belong to.
# They used to sit in fixed 30-column cells, which spread three of them evenly across the same
# x-range the density bar occupies -- so `@5 Seed Tray` landed under a commit column and read as
# "this checkpoint happened *there*", in a view whose legend promises that a column is a time. The
# chips carry no time; only the bar above them does. Packed tight and separated by `·` they read as
# what they are, and the axis keeps its meaning.

# How the row's width is split between the feature's NAME and its density bar. The title column used
# to be a flat 32 columns, so on a 170-column terminal every label was ellipsized while ~90 columns
# of bar sat empty -- the budget was backwards. Now the title takes what its longest label actually
# needs, bounded by a share of the terminal and by a floor under the bar, and the bar takes the rest.

_WORD_RE = re.compile(r"[a-z0-9]+")


def _echoes(chip: str, lane: str) -> bool:
    """True when a checkpoint's name only repeats words from the lane it sits under -- `@0 Chips
    Filters` printed below `make the chips filters: pick traits in the header…`.

    Those chips were most of the ink on the map and none of the information: every other line said,
    in title case, what the line above it had just said. Only the echo is dropped; the `@n` handle
    stays, because it is the token `sgt revert` takes and the map is where a reader finds it.
    Compared on the first few words, since the two strings are ellipsized independently and a tail
    cut at a different place would otherwise read as new content."""
    lane_words = set(_WORD_RE.findall(lane.lower()))
    if not lane_words:
        return False
    chip_words = _WORD_RE.findall(chip.lower())[:6]
    return bool(chip_words) and all(w in lane_words for w in chip_words)


# The FORECAST BAND, the terminal twin of the webview's band right of the `now` rule: anticipated work
# (a pending plan step) drawn on the lane's own row, past a `┊` rule, in the same left→right reading
# order as history. It replaces a `◇ planned: …` chip that used to sit on the checkpoint line below --
# which put "what is coming" in a different place, and a different grammar, from every other unit of
# work in the view. `_NOW_RULE` is the boundary: left of it happened, right of it has not.
def _ellipsize(s: str, width: int) -> str:
    """Truncate a checkpoint label to `width` columns, cutting on a word boundary where one is near
    the limit (so `add foo, qux, config, binary` becomes `add foo, qux, config…`, not `…config, bi`)
    and appending `…`. Short-enough labels pass through untouched.

    `width` is a hard bound, ellipsis included -- the `…` costs a column, so the text is cut one short
    of the limit. It used to return `width + 1`, which is invisible in a padded column and fatal in a
    fitted one: every layout that budgets a row by summing its columns was over by one per ellipsized
    field, and a row one column past the terminal wraps just as badly as one twenty past it."""
    if len(s) <= width:
        return s
    cut = s[:max(0, width - 1)].rstrip()
    space = cut.rfind(" ")
    if space >= width - 9:  # a word boundary close enough to the limit -- cut there, not mid-word
        cut = cut[:space].rstrip(" ,;:")
    return cut + "…"


def _paint(hex_str: str, s: str, *, color: bool) -> str:
    return _fg(hex_str, s) if color else s


def _dim(s: str, *, color: bool) -> str:
    return f"{_DIM}{s}{_RESET}" if color else s


def _bold(s: str, *, color: bool) -> str:
    return f"{_BOLD}{s}{_RESET}" if color else s




def _wrap_parts(parts: list[str], width: int, *, sep: str = "; ", prefix: str = " ") -> list[str]:
    """Pack caption parts into lines that fit `width`, breaking only between parts.

    The two prose lines under the rail header measured 181 and 190 columns on a real repo in an
    80-column terminal, so both wrapped -- and a legend that wraps is where a reader decides this
    header is not worth reading, which costs the header its whole job. Breaking on the separators the
    caption already has keeps each clause and each suggested command intact on one line; nothing is
    truncated, because a shell command cut in half is worse than a wrapped one. Continuation lines
    indent to `prefix` so the block reads as one caption rather than as more rows."""
    lines: list[str] = []
    cur: str | None = None
    cont = " " * len(prefix)
    for part in parts:
        if cur is None:
            cur = prefix + part
        elif len(cur) + len(sep) + len(part) <= width:
            cur += sep + part
        else:
            lines.append(cur)
            cur = cont + part
    return lines + ([cur] if cur is not None else [])


def _reverted_gap_note(gap: dict | None, width: int = 10 ** 6) -> list[str]:
    """The two lines that disclose reverted work no lane on the screen can draw, in one place because
    both lane views need them: the default rail (`sgt log`) and the feature map (`sgt log --map`).

    Every other absence these screens show is drawable -- a hollow car, a `N of M reverted` note --
    because the op still belongs to a chapter. These ops belong to none: `build_map` clusters *alive*
    symbols, so a reverted symbol is no member of any leaf, its ops lose their `op_leaf` entry and
    with it their cell and their chapter. Without this note the screen draws the codebase whole while
    the code is off disk, and a partial restore (the case `sgt restore`'s own gap warning names)
    reads as a completed one. An absent key is no claim, not a report of zero.

    The symbols are shown whole and wrapped to `width`, never clipped: `restore` takes a name the
    reader has to read back off this screen exactly, so a half-name here is a command they cannot type.
    A qualified name runs ~36 columns, so the flat `syms[:4]` this started as measured 110 and wrapped
    wherever the terminal chose. When they do not fit beside the count they get their own indented
    lines; a long list is capped and counted, because this is a disclosure that something is missing,
    not the full inventory of it."""
    gap = gap or {}
    if not gap.get("op_count"):
        return []
    syms = gap.get("symbols") or []
    head = f" ⚠ {plural(gap['op_count'], 'reverted edit')} sit in no lane below"
    # "they are still recorded" is gone from this line: the two commands say it, and with it the line
    # measured 110 columns and wrapped -- so the remedy for the warning was the part that broke.
    how = _wrap_parts(["`sgt undo` reverses the whole revert",
                       "`sgt restore <symbol>` brings one back"], width, sep="; ", prefix="   ")
    if not syms:
        return [head, *how]
    flat = ", ".join(syms)
    if len(head) + 2 + len(flat) <= width:
        return [f"{head}: {flat}", *how]
    keep, left = syms[:6], max(0, len(syms) - 6)
    parts = keep + ([f"+{left} more"] if left else [])
    return [head + ":", *_wrap_parts(parts, width, sep=", ", prefix="   "), *how]




def _row_headline(subject: str, feature: str | None, labels: dict) -> str:
    """The headline for a save row. Delegates to `sgt.api.headline_for`, which is the single
    definition of this rule: every surface that lists history needs it (this rail, the save list,
    `sgt now`'s recently-done, the extension's Now tree), and a second copy would let them disagree
    about what the same commit is called."""
    from sgt.api import headline_for

    return headline_for(subject, feature, labels)


# Git-topology glyphs, shared by the save-list spine (`--saves`) and the default rail's topology
# column: a save on the first-parent trunk (●), a merge where a side branch folded back in (◆), a
# save that landed on a side branch (○ — off the trunk), and the trunk connector (│).
_SPINE_NODE = "●"
_SPINE_MERGE = "◆"
_SPINE_TRUNK = "│"




def render_save_list_lines(
    map_view: dict,
    grid_view: dict,
    *,
    topology: dict | None = None,
    selected: str | None = None,
    color: bool = True,
    label_width: int = 48,
    max_rows: int = 40,
    width: int | None = None,
) -> list[str]:
    """The default `sgt log` (Phase 4): a lane-less "what I did, in order" -- one row per save,
    newest on top, `cN  sha  subject  features`. Drops `render_rail_lines`' recurring-feature lane
    column (the `" ".join(cell(...))` art that overruns the terminal past ~20 lanes and wraps),
    keeping the same episode rows and width-bounded feature chips. The lane rail stays available
    under `sgt log --rail` for readers who want the recurring-feature threads.

    When `topology` (from `GitBinding.graph_topology`) is given, a narrow git-log-style spine is
    drawn to the left of each row (`_spine_prefixes`); when it is None the rendering is unchanged
    (so callers and golden snapshots without topology stay byte-identical).

    `width` is the column budget a row must fit in, defaulting to the terminal's, and it is split the
    same way the lane rail splits its own: subject first, chips with what is left, and no chips column
    at all when no feature name would survive in it. Measured on a real repo at 80 columns, 39 of 44
    rows overran here -- the same defect the rail had, on the screen next to it, because each renderer
    bounded its chips against a constant instead of against the terminal."""
    # The save list is the rail without its lane gutter -- same rows, same columns, same rules --
    # so it is the same renderer with `lane_count=0` rather than a second one that drifts. It used
    # to be a near-copy: its own header, its own legend, its own chip budget, and the same
    # width-overrun bug fixed twice, once on each screen.
    from .views import rail_lines

    ep = episodes(map_view, grid_view)
    layout = episode_rail_layout(ep)
    labels = {n["id"]: n.get("label", n["id"]) for n in map_view.get("nodes", [])}
    rows = layout["rows"]
    view_rows = [{**r, "subject": _row_headline(r["subject"], r["feature"], labels),
                  "feature_names": [labels.get(f, f) for f in
                                    sorted((r.get("features") or {}),
                                           key=lambda f: (-(r["features"][f]), f))]}
                 for r in rows]
    return rail_lines(
        view_rows, labels=labels, lane_count=0, lane_intervals={}, feature_touched={},
        n_saves=len(rows), n_features=len({r["feature"] for r in rows if r["feature"] is not None}),
        total_saves=grid_view.get("save_count", len(rows)), topology=topology, selected=selected,
        color=color, max_rows=None if max_rows == 40 else max_rows, width=width,
        notes=_reverted_gap_note(grid_view.get("reverted_unaccounted"),
                                 width or shutil.get_terminal_size(fallback=(10 ** 6, 0)).columns),
    )


def _render_car(car: dict, width: int, hexc: str, *, color: bool, is_big: bool = False) -> str:
    """One checkpoint "car": tier brackets around a `@n` digit and a density-shaded body. Module
    level so both the timeline rail (`render_graph_lines`) and the feedforward preview
    (`render_verb_preview_lines`) draw the same glyph. A future car (past the frontier) renders dim; a
    car whose ops a revert took out of the ideal (`reverted`) renders hollow in `░`, the same glyph
    the preview spends on removal, so the two screens describe one state in one vocabulary."""
    lo, hi = _TIER_BRACKETS.get(car["tier"], ("[", "]"))
    faint = car["is_future"] or car.get("reverted", False)
    inner_w = max(0, width - 2)
    body = ""
    if inner_w >= 1:
        digit = str(car["seg_index"] % 10)
        body += _dim(digit, color=color) if faint else (
            _bold(_paint(hexc, digit, color=color), color=color) if color else digit)
    if inner_w >= 2:
        buckets = _bucket_density(car["sub_bins"], inner_w - 1)
        local_max = max(buckets) if buckets else 0
        for n in buckets:
            ch = ("░" if car.get("reverted", False) else "█") if n > 0 else "·"
            if faint or n == 0:
                body += _dim(ch, color=color)
            else:
                body += _shade(hexc, (n / max(1, local_max)) ** 0.5, ch) if color else ch

    def bracket(b: str) -> str:
        if faint:
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
    group: dict | None = None,
    themes: list[dict] | None = None,
    frontier: int | None = None,
    collapsed=(),
    color: bool = True,
    bar_width: int | None = None,
    show_links: bool = False,
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

    # One shared commit axis for every lane. Column `c` covers commit indices
    # `[c*axis_len/width, (c+1)*axis_len/width)`, so the same column means the same commits in every
    # row and a vertical slice reads as "what was happening then". The old renderer packed each
    # lane's own checkpoints back to back, which made every row a private clock -- two blocks
    # sitting in the same column had nothing to do with each other, and the map could not be read
    # downward at all.
    axis_len = max(1, int(layout.get("commit_count") or 0))
    # Density is scaled against the busiest column *anywhere*, not per lane, so height is comparable
    # across rows too. Per-lane scaling made a quiet lane's one edit as tall as a busy lane's twenty.
    _global_max = 0
    for _l in layout["lanes"]:
        _per_col: dict[int, int] = {}
        for _c in _l["cars"]:
            for _ci, _cnt in _c.get("sub_bins") or ():
                _per_col[_ci] = _per_col.get(_ci, 0) + _cnt
        if _per_col:
            _global_max = max(_global_max, max(_per_col.values()))

    def time_bar(cars: list[dict], hexc: str, width: int) -> str:
        """This lane's edit density on the shared commit axis, as `▁▂▃▄▅▆▇█` blocks. Taller = busier,
        scaled against the busiest commit in the whole map. A commit the lane never touched is a
        space, so the gaps are real gaps rather than squeezed-away ones. Future cars (past the
        frontier) render dim. Padded to `width` so the columns after the bar stay aligned.

        A commit fills its WHOLE cell -- every column in `[ci*width/axis, (ci+1)*width/axis)` -- so
        the bar reads as a bar. It used to mark only the one column a commit's index mapped to,
        which is fine when there are more commits than columns and confetti when there aren't: 14
        commits across 123 columns drew 14 lonely glyphs with eight blanks between each pair, and a
        density profile you have to reassemble in your head isn't one. Widening the terminal made it
        worse, which is the tell that the encoding, not the size, was wrong.

        One pass over this lane's bins plus one over the columns, so the whole map costs O(total
        bins + lanes*width)."""
        if width <= 0:
            return ""
        if not cars:
            return " " * width
        per_commit: dict[int, int] = {}
        future_at: set[int] = set()
        for c in cars:
            is_future = bool(c.get("is_future"))
            bins = c.get("sub_bins") or ()
            if not bins:  # a car with no per-commit detail still occupies its own span
                bins = [(i, 1) for i in range(c["first_index"], c["last_index"] + 1)]
            for ci, cnt in bins:
                per_commit[ci] = per_commit.get(ci, 0) + cnt
                if is_future:
                    future_at.add(ci)
        buckets = [0] * width
        future = [False] * width
        for ci, cnt in per_commit.items():
            lo = min(width - 1, max(0, ci * width // axis_len))
            hi = min(width, max(lo + 1, (ci + 1) * width // axis_len))
            for col in range(lo, hi):
                buckets[col] += cnt
                if ci in future_at:
                    future[col] = True
        gmax = _global_max or max(buckets) or 1
        out = []
        for col, n in enumerate(buckets):
            if n <= 0:
                out.append(" ")
                continue
            frac = min(1.0, n / gmax)
            ch = _SPARK[min(len(_SPARK) - 1, int(frac * (len(_SPARK) - 1) + 0.5))]
            out.append(dim(ch) if future[col] else (_shade(hexc, frac ** 0.5, ch) if color else ch))
        return "".join(out)

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
    # Count subsystems the way the feature count below counts features: stable under collapse. A
    # folded subsystem leaves `headers` and becomes a meta-LANE, so `len(headers)` alone dropped one
    # subsystem per folded row -- the default map (which folds every leaf subsystem) said `1
    # subsystem(s)` where `--focus`, which folds nothing, said `4` for the same repo at the same
    # moment, and a reader moving between the two has no way to tell which one is lying.
    # Depth 0 is the root, which holds every feature in the repository, so it is not one of the
    # groupings this number is telling the reader about. Counted, a repo with no subsystems at all
    # said `1 subsystem` above a single header row named after the repo -- a level of structure the
    # reader then goes looking for and does not find.
    n_sub = (sum(1 for h in layout["headers"] if h["depth"] > 0)
             + sum(1 for l in layout["lanes"] if l["is_meta"] and l.get("depth", 0) > 0))
    sub_note = f"  ·  {plural(n_sub, 'subsystem')}" if n_sub else ""
    bk = layout["bookkeeping_count"]
    bk_note = dim(f"  (+{bk} bookkeeping)") if bk else ""
    # Count FEATURES, not rows. This used to report `len(lanes)`, which folds a collapsed subsystem to
    # a single meta-lane -- so the headline size of the repo dropped every time the reader folded a
    # row, and disagreed with every other surface. Leaves are stable under collapse.
    n_feat = sum(len(l["leaves"]) for l in layout["lanes"])
    lines.append(bold(f" {plural(n_feat, 'feature')}  ·  {plural(layout['save_count'], 'save')}"
                      f"{sub_note}") + bk_note)
    _cols = shutil.get_terminal_size(fallback=(0, 0)).columns
    lines.extend(dim(s) for s in _reverted_gap_note(layout.get("reverted_unaccounted"),
                                                    _cols if _cols >= 40 else 10 ** 6))
    if frontier is not None:
        lines.append(dim(f"   frontier: folded at commit {frontier} (later features hidden)"))
    lines.append("")

    emphasis = None
    focus_detail: list[str] = []
    if group is not None:
        # A group focus (`--focus <subsystem|theme>`, resolved by the caller): TableLens over the
        # whole map -- the group's lanes keep full detail, everything else compresses in place.
        emphasis = {"ids": set(group.get("feature_ids") or ()),
                    "label": group.get("label", ""), "kind": group.get("kind", "group")}
    elif focus is not None:
        fid_match = focus if focus in layout["node_by_id"] else _resolve_focus(focus, layout, labels)
        lane = layout["node_by_id"].get(fid_match) if fid_match else None
        if lane is None:
            lines.append(dim(f" {focus!r} has no lane yet (no ops, an unknown feature id, or an "
                             "ambiguous prefix/label)"))
            return lines
        focus = fid_match
        raw = labels.get(focus, focus)
        # The map stays on screen (emphasized lane bright, the rest compressed to density) and the
        # chapter table renders BELOW it -- `views.focus_lines`, the one screen that prints the
        # `@n` rewind handles. The old behaviour swapped the map out for the table entirely, which
        # answered "what is in this feature" while losing "where does it sit among the others" --
        # the whole point of a focus+context read.
        emphasis = {"ids": {focus}, "label": raw, "kind": "feature"}
        from .views import focus_lines
        focus_detail = [""] + focus_lines(focus, raw, lane["cars"], color=color)

    # The map is drawn by the redesigned renderer in `views.py`: one line per lane, three
    # columns, a `c0 … cN` ruler over the bars. Layout stays this module's job; presentation does not.
    from .views import map_lines
    _cols = shutil.get_terminal_size(fallback=(0, 0)).columns
    link_note = {}
    if show_links:
        for l in layout["lanes"]:
            picked = nbrs.get(l["id"], [])[:2]
            if picked:
                extra = len(nbrs.get(l["id"], [])) - len(picked)
                link_note[l["id"]] = (", ".join(labels.get(x, x) for x in picked)
                                      + (f" +{extra}" if extra > 0 else ""))
    return map_lines(
        layout, labels, selected=selected, frontier=frontier, color=color,
        bar_width=bar_width, max_rows=None if max_rows == 40 else max_rows,
        ghost_by_lane=ghost_by_lane, unplaced_ghosts=unplaced_ghosts,
        notes=_reverted_gap_note(layout.get("reverted_unaccounted"),
                                 _cols if _cols >= 40 else 10 ** 6),
        links=link_note, emphasis=emphasis, themes=themes,
    ) + focus_detail



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
    # A revert that rewrites a symbol in place removes no op from it, so a checkpoint whose whole
    # effect is being subtracted has an empty intersection with `touched` and used to render as
    # `kept` -- the one word it must never say about the chapter the user just named. Ops whose
    # symbols are being subtracted count as touched for display, since that is exactly what the
    # edit does to them.
    # The ops the user actually named. A revert that rewrites a symbol in place removes no op, so
    # `touched` is empty for it and the chapter being reverted used to render as `kept` -- the one
    # word it must never say about the thing the user just asked to undo.
    touched = touched | set(preview_view.get("target_ops") or ())

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
        # Any `@<chapter>` suffix is split off first and put back after. Shortening the whole
        # string collapsed `f-0a413ceb…@Exclude Event Days` to `0a413ceb`, so the command the
        # preview told you to re-run was a revert of the entire feature rather than the one chapter
        # you asked about -- on this history, sixty-seven edits instead of one.
        head, sep, chapter = target.partition("@")
        short = head[2:10] if (head.startswith("f-") and len(head) > 20) else (
            head[:8] if re.fullmatch(r"[0-9a-f]{40,}", head) else head)
        short = f"{short}{sep}{chapter}" if sep else short
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
            # Worded from the sign, not from the role. `restore` is `revert`'s inverse, so a
            # feature the removal took ops off is the same feature the restore puts them back on
            # -- and the role stays `blast` either way. Wording the badge from the role alone
            # printed "loses -6 edits, re-draft" on every restore preview: a double negative on
            # the one line that says what the operation is about to do.
            delta = oa - ob
            if n["role"] == "foundation":
                glyph = _paint(ahex, "◈", color=color)
                badge = ("prerequisite, kept" if delta == 0
                         else f"gains {plural(delta, 'edit')}" if delta > 0
                         else f"loses {plural(-delta, 'edit')}")
            else:  # blast (target is drawn as the rail above, never here)
                glyph = _paint(ahex, "●", color=color)
                badge = ("unchanged" if delta == 0
                         else f"gains {plural(delta, 'edit')} back" if delta > 0
                         else f"loses {plural(-delta, 'edit')}, re-draft")
            note = _dim(badge, color=color)
            lines.append(f"   {glyph} {_ellipsize(albl, 28).ljust(28)}  {bar}  {note}")
        if len(others) > 8:
            lines.append(_dim(f"   +{plural(len(others) - 8, 'more feature')}", color=color))
        lines.append("")
    if context_count:
        lines.append(_dim(f" · {plural(context_count, 'other feature')} unchanged", color=color))

    n_op = len(removed) if verb == "revert" else len(added)
    verbword = "removes" if verb == "revert" else "restores"
    frame_hint = "" if frame == "after" else "  · showing before"
    syms = [s for s in preview_view.get("affected_symbols", []) if "::__" not in s]
    sym_note = f" across {plural(len(syms), 'symbol')}" if syms else ""
    # A revert whose edit is shared with later work is spliced out of the live code rather than
    # removed as an op, so the op count is 0 while the file changes (`sgt.core.subtract`). Leading
    # with "removes 0 edit(s)" made the feedforward read as a no-op right before it applied.
    magnitude = (f"changes {plural(len(syms), 'symbol')}"
                 if verb == "revert" and not n_op and syms
                 else f"{verbword} {plural(n_op, 'edit')}{sym_note}")
    shown_files = sorted(files)[:4]
    file_note = ", ".join(shown_files) + (f" +{len(files) - 4} more" if len(files) > 4 else "")
    lines.append(_dim(f" {magnitude} · {plural(len(files), 'file')}: {file_note}{frame_hint}"
                      if files else
                      f" {magnitude} · no file changes{frame_hint}",
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
        f"{_bold(str(ops_added), color=color)} {'op' if ops_added == 1 else 'ops'} from {src}",
        "",
    ]

    if forks:
        lines.append(_paint("#ffaf00", f"   ⚠ {plural(len(forks), 'fork')} surface -- the fork-free work "
                                       f"still merges; resolve these when ready (nothing is lost):",
                            color=color))
        for sym, a, b in forks[:8]:
            remedy = _dim(f"sgt resolve {sym}", color=color)
            lines.append(f"       {_paint('#ffaf00', sym, color=color)}   {remedy}")
        if len(forks) > 8:
            lines.append(_dim(f"       +{plural(len(forks) - 8, 'more fork')}", color=color))
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
    tail = (f" folds in {ops_added} op · {plural(len(forks), 'fork')} surface to resolve · not auto-undoable"
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
                     f"{_bold(str(ops_added), color=color)} {'op' if ops_added == 1 else 'ops'} to {branch}")
        lines.append("")

    if forks:
        lines.append(_paint("#ffaf00", f"   ⚠ {plural(len(forks), 'fork')} block the land -- "
                                       f"reconcile before it can advance:", color=color))
        for sym, a, b in forks[:8]:
            remedy = _dim(f"sgt resolve {sym}", color=color)
            lines.append(f"       {_paint('#ffaf00', sym, color=color)}   {remedy}")
        if len(forks) > 8:
            lines.append(_dim(f"       +{plural(len(forks) - 8, 'more fork')}", color=color))
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
        tail = f" won't advance -- {plural(len(forks), 'fork')} to resolve first"
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
    topology: dict | None = None,
    width: int | None = None,
) -> list[str]:
    """Render the episode rail as a vertical git-log (Stage C): newest episode on top. Recurring
    features (touched by >=2 saves) each get a dedicated lane so their saves read as one unbroken
    vertical line (● at each touched save, │ carried across the saves between); one-off features
    pack into a shared pool lane. Each row is one commit-episode -- the "what I did, in order" rewind
    unit -- with its commit position (cN), subject, and the feature(s) it touched (bold ● marks the
    save's dominant one). Capped at `max_rows` (newest first); a footer notes how many older episodes
    were folded (the lazy nod for a long history).

    `width` is the column budget a row must fit in, defaulting to the terminal's. Rows that overrun
    wrap, and a wrapped row folds the lane gutter onto a second line -- which destroys the single
    thing this view exists to show, a recurring feature reading as one unbroken vertical line.
    Measured on a real repo at 80 columns, every row came out 109-189 wide. `--map` has fitted its bar
    to the terminal all along (`render_graph_lines`) for exactly this reason, so this was the only
    screen that did not, and it is the one a reader lands on."""
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
    # The gutter is capped at a share of the terminal: it is scaffolding for reading the subject
    # column, and scaffolding that crowds out what it scaffolds has stopped being useful. Without a
    # terminal (a pipe, a test) there is nothing to divide, so the rail stays uncapped and every
    # existing golden renders byte-identically.
    _rail_cols = width if width is not None else shutil.get_terminal_size(fallback=(0, 0)).columns
    layout = episode_rail_layout(ep, max_lanes=(max(4, int(_rail_cols * 0.18))
                                                if _rail_cols >= 40 else None))
    labels = {n["id"]: n.get("label", n["id"]) for n in map_view.get("nodes", [])}
    rows = layout["rows"]
    lane_count = layout["lane_count"]
    lane_intervals = layout["lane_intervals"]
    feature_touched = {f: set(rs) for f, rs in layout["feature_touched"].items()}

    # Terminal fit. The fixed part of a row is everything but the subject and the chips: a leading
    # space, the topology column, one gutter column per lane, and the ` pos sha  ` run. What's left is
    # split subject-first -- the subject is how a reader tells one save from another, while the chips
    # repeat an attribution the gutter's bold dot already carries. Below 40 columns (or when the size
    # is unavailable, as under a captured stdout) the proven defaults stand, so every existing caller,
    # test and golden renders byte-identically and only a real narrow terminal is re-laid-out.
    pos_w_fit = max(4, len(f"c{max((r['index'] for r in rows[:max_rows]), default=0)}"))
    term_cols = width if width is not None else shutil.get_terminal_size(fallback=(0, 0)).columns
    chip_budget = 60
    if term_cols >= 40:
        fixed = 1 + (2 if topology is not None else 0) + lane_count + 1 + pos_w_fit + 1 + 7 + 2
        avail = max(24, term_cols - fixed)
        label_width = max(12, min(label_width, avail))
        chip_budget = min(60, avail - label_width - 2)  # 2 = the gap before the chips column
        # One law for the column: it is drawn only while it can identify a feature. Under 12 columns a
        # name is a guess, and this view can afford to drop it -- the lane gutter's coloured ● already
        # says which feature the save belongs to, which is why the subject wins the columns here and
        # the lane-less save list (below) instead takes columns back from its subject.
        if chip_budget < 12:
            chip_budget = 0

    n_feat_hdr = len({r["feature"] for r in rows if r["feature"] is not None})
    # Presentation moves to `views.rail_lines`; the episode/lane computation above stays here.
    from .views import rail_lines
    all_ghosts = grid_view.get("ghosts", [])
    if only_features is not None:  # a group focus shows only that group's steps, not every plan's
        all_ghosts = [g for g in all_ghosts if g["feature_id"] in only_features]
    placed = [{**g, "lane": layout["lane_of"][g["feature_id"]]}
              for g in all_ghosts if g["feature_id"] in layout["lane_of"]]
    unplaced = [g for g in all_ghosts if g["feature_id"] not in layout["lane_of"]]
    # The feature column takes NAMES, resolved here where `labels` lives, so the renderer never has
    # to know how a feature id maps to a word.
    view_rows = [{**r, "subject": _row_headline(r["subject"], r["feature"], labels),
                  "feature_names": [labels.get(f, f) for f in
                                    sorted((r.get("features") or {}),
                                           key=lambda f: (-(r["features"][f]), f))]}
                 for r in rows]
    out = rail_lines(
        view_rows, labels=labels, lane_count=lane_count, lane_intervals=lane_intervals,
        feature_touched=feature_touched, n_saves=len(rows), n_features=n_feat_hdr,
        total_saves=grid_view.get("save_count", len(rows)), topology=topology, selected=selected,
        group_label=group_label, ghosts=(placed, unplaced), color=color,
        max_rows=None if max_rows == 40 else max_rows, width=width,
        overflow_rows=set(layout.get("overflow_rows") or ()),
        overflow_lane=layout.get("overflow_lane"),
        notes=_reverted_gap_note(grid_view.get("reverted_unaccounted"),
                                 term_cols if term_cols >= 40 else 10 ** 6),
    )
    out.extend(_state_banner(states, color=color))
    return out

