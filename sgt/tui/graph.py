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

    def visit(node_id: str):
        node = by_id.get(node_id)
        if not node:
            return
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
    """Lay episodes out as a vertical git-log rail: the newest episode on top (row 0), each FEATURE
    a lane (column) so its episodes read as a straight vertical line, and lanes reused by features
    whose row-spans don't overlap (greedy interval-graph coloring) -- so the column count stays
    small no matter how many features exist. That lane reuse is the compaction the user asked for:
    shared column space is a common subexpression the layout eliminates rather than opening a fresh
    column per feature.

    Input is `episodes()`'s output; each rail row carries what a rewind decision needs (subject,
    op_count, sha, dominant feature). The VS Code counterpart is `episodeRailLayout` in
    workbench.js, kept behaviour-parallel."""
    episodes = ep_view.get("episodes", [])
    ordered = sorted(episodes, key=lambda e: -e["index"])  # newest (largest commit_index) on top
    row_of = {e["index"]: r for r, e in enumerate(ordered)}

    # Each feature's inclusive row-span over its episodes.
    span: dict = {}
    for e in episodes:
        fid = e["dominant_feature"]
        r = row_of[e["index"]]
        s = span.get(fid)
        if s is None:
            span[fid] = [r, r]
        else:
            s[0], s[1] = min(s[0], r), max(s[1], r)

    # Greedy interval coloring: features top-first; a lane is reusable once its last occupant ends
    # above (in a smaller row than) this feature's top. Lowest free lane wins (minimal columns).
    lane_of: dict = {}
    lane_bot: list = []  # lane -> bottom row of the feature currently occupying it
    for fid in sorted(span, key=lambda f: (span[f][0], str(f))):
        top, bot = span[fid]
        lane = next((L for L in range(len(lane_bot)) if lane_bot[L] < top), None)
        if lane is None:
            lane = len(lane_bot)
            lane_bot.append(bot)
        else:
            lane_bot[lane] = bot
        lane_of[fid] = lane

    rows = [
        {"index": e["index"], "row": row_of[e["index"]], "feature": e["dominant_feature"],
         "lane": lane_of.get(e["dominant_feature"], 0), "subject": e["subject"],
         "op_count": e["op_count"], "sha": e["sha"]}
        for e in ordered
    ]
    return {"rows": rows, "lane_of": lane_of, "lane_count": max(1, len(lane_bot)),
            "row_count": len(ordered)}


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

_TIER_BRACKETS = {"co-changed": ("[", "]"), "coupled": ("[", "]"), "thematic": ("(", ")")}


def _resolve_focus(focus: str, layout: dict, labels: dict) -> str | None:
    """Resolve `--focus`'s argument against a lane id: a unique id-prefix (the short handle the
    render prints, e.g. `f-0575f655` for a longer real id), else a unique case-insensitive label
    match. Mirrors `sgt.intent.segment.resolve_checkpoint`'s handle resolution, so the id you copy
    off the graph and the id you pass back in agree."""
    prefix_hits = [nid for nid in layout["node_by_id"] if nid.startswith(focus)]
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


def _time_ruler(prefix_w: int, bar_w: int, commit_count: int) -> str:
    """A commit-index ruler aligned under the car strip: start/mid/end ticks (`c0 … cN`) at the same
    columns `render_cars` maps commits onto, so a car's horizontal position reads as *when*. Blank
    when there's no width or only one commit (nothing to place along)."""
    if bar_w <= 0 or commit_count <= 1:
        return ""
    max_ci = commit_count - 1
    buf = [" "] * bar_w
    for frac in (0.0, 0.5, 1.0):
        tick = f"c{int(round(frac * max_ci))}"
        pos = int(round(frac * (bar_w - 1)))
        if frac == 1.0:
            pos = bar_w - len(tick)          # end-anchored
        elif frac == 0.5:
            pos -= len(tick) // 2            # centered
        pos = max(0, min(bar_w - len(tick), pos))
        for k, ch in enumerate(tick):
            buf[pos + k] = ch
    return " " * prefix_w + "".join(buf)


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
    bar_width: int = 42,
    label_width: int = 24,
    show_links: bool = False,
) -> list[str]:
    """Render the feature timeline as terminal lines (ANSI truecolor when `color`). The visual atom
    is the intent *segment*, not the raw op: each lane draws its checkpoints as bracketed "cars"
    positioned on the SHARED commit-time axis -- a car sits at the column of its first commit and
    spans to its last, over a dim lifetime track -- so reading left-to-right shows *when* each
    feature was worked on and when it went quiet, against the same time span every lane shares. The
    texture inside a car ∝ its own commit density, the bracket shape ∝ `tier` (`[ ]` co-changed/
    coupled, `( )` thematic, a weaker claim), and the lane's fattest chapter (its "big event") gets
    bold brackets. The digit is the checkpoint's `@n` -- the handle you pass to `sgt revert`.

    `segments` is the flat `sgt.api.segments_view` list; a lane with no matching segments (nothing
    built yet for it) falls back to a plain dim lifetime track. `focus=<feature_id>` renders just
    that one lane, full width, one detail line per car (checkpoint, label, op count, tier, source).
    `show_links` re-enables the co-change `↔` annotation trailing each row -- off by default, since
    themes/co-change are now an overlay, not the primary read."""
    segments = segments or []
    fr = float("inf") if frontier is None else frontier
    layout = segment_layout(map_view, grid_view, segments, collapsed=collapsed, frontier=frontier)
    labels = {n["id"]: n.get("label", n["id"]) for n in map_view.get("nodes", [])}

    def paint(hex_str: str, s: str) -> str:
        return _fg(hex_str, s) if color else s

    def dim(s: str) -> str:
        return f"{_DIM}{s}{_RESET}" if color else s

    def bold(s: str) -> str:
        return f"{_BOLD}{s}{_RESET}" if color else s

    def render_car(car: dict, width: int, hexc: str, is_big: bool = False) -> str:
        lo, hi = _TIER_BRACKETS.get(car["tier"], ("[", "]"))
        inner_w = max(0, width - 2)
        body = ""
        if inner_w >= 1:
            digit = str(car["seg_index"] % 10)
            body += dim(digit) if car["is_future"] else (bold(paint(hexc, digit)) if color else digit)
        if inner_w >= 2:
            buckets = _bucket_density(car["sub_bins"], inner_w - 1)
            local_max = max(buckets) if buckets else 0
            for n in buckets:
                ch = "█" if n > 0 else "·"
                if car["is_future"] or n == 0:
                    body += dim(ch)
                else:
                    body += _shade(hexc, (n / max(1, local_max)) ** 0.5, ch) if color else ch

        def bracket(b: str) -> str:
            if car["is_future"]:
                return dim(b)
            painted = paint(hexc, b)
            return bold(painted) if (is_big and color) else painted  # big event = bold brackets

        return f"{bracket(lo)}{body}{bracket(hi)}"

    def render_cars(cars: list[dict], hexc: str, width: int) -> str:
        """Draw a lane's cars positioned on the SHARED commit-time axis: each car sits at the column
        of its `first_index`, spanning to its `last_index`, over a dim lifetime track -- so a lane
        reads *when* the feature was worked on and when it went quiet, against the same time span
        every other lane uses. A single left-to-right pass floors each car's width and enforces a
        one-column gap, so short chapters stay legible and a burst in a narrow band nudges right
        rather than stacking. Mirrors the VS Code `renderCars` (columns here, pixels there)."""
        if not cars or width <= 0:
            return dim("─" * max(0, width))
        max_ci = max(1, layout["commit_count"] - 1)
        max_ops = max((c["op_count"] for c in cars), default=1)

        def col_of(ci: int) -> int:
            return int(round(max(0, min(max_ci, ci)) / max_ci * (width - 1)))

        placed: list[tuple[int, int, dict]] = []
        cursor = 0
        for c in cars:
            start = max(col_of(c["first_index"]), cursor)
            w = max(3, col_of(c["last_index"]) - start + 1)  # floor: bracket-digit-bracket
            if start + w > width:
                start = max(cursor, width - w)
            if start + w > width:
                w = max(2, width - start)
            if w < 2 or start >= width:
                break  # out of room -- the remaining (rightmost, latest) cars don't fit
            placed.append((start, w, c))
            cursor = start + w + 1  # a one-column gap between adjacent cars

        out, col = "", 0
        for start, w, c in placed:
            if start > col:
                out += dim("─" * (start - col))
            is_big = len(cars) > 1 and c["op_count"] == max_ops
            out += render_car(c, w, hexc, is_big=is_big)
            col = start + w
        if col < width:
            out += dim("─" * (width - col))
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
    total_ops = sum(l["op_count"] for l in layout["lanes"])
    n_sub = len(layout["headers"])
    sub_note = f"  ·  {n_sub} subsystem(s)" if n_sub else ""
    lines.append(bold(f" {len(layout['lanes'])} feature(s)  ·  {layout['commit_count']} commit(s)  ·  "
                       f"{total_ops} op(s){sub_note}"))
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
        handle = focus[:10]  # the copy-paste token -- same prefix the gutter itself prints
        hexc = color_for(focus)
        raw = labels.get(focus, focus)
        lines.append(f" {paint(hexc, '●')} {bold(raw)}  {dim(focus)}  ·  {lane['op_count']} op(s)")
        lines.append("")
        if not lane["cars"]:
            lines.append(dim("   no checkpoints yet -- run `sgt intent build` (or `sgt graph --refresh`)"))
        for car in lane["cars"]:
            head = render_car(car, 6, hexc)
            future = dim(" (not yet reached)") if car["is_future"] else ""
            lines.append(f"   {head}  {handle}@{car['seg_index']}  {car['label']}  "
                         f"({car['op_count']} op, {car['tier']}, {car['source']}){future}")
        lines.append("")
        lines.append(dim(f" operate:  sgt revert {handle}  (whole feature)   ·   "
                         f"sgt revert {handle}@<n>  (one car)   ·   sgt intent show {handle}"))
        return lines

    lanes_by_row = {l["row"]: l for l in layout["lanes"]}
    headers_by_row = {h["row"]: h for h in layout["headers"]}
    # A time ruler aligned under the car strip -- the prefix width matches the row layout below
    # (leading space + marker + glyph + space + 10-char handle + space + label + space).
    ruler = _time_ruler(label_width + 16, bar_width, layout["commit_count"])
    if ruler:
        lines.append(dim(ruler))
    for row in range(layout["row_count"]):
        if row in headers_by_row:
            hd = headers_by_row[row]
            label = ("▾ " + hd["label"])[:label_width + 2].ljust(label_width + 2)
            meta = f"{hd['lane_count']} feat · {hd['op_count']} op"
            lines.append(dim(f" {label} {meta}"))
        elif row in lanes_by_row:
            l = lanes_by_row[row]
            fid = l["id"]
            hexc = color_for(fid)
            is_sel = fid == selected
            glyph = "◈" if l["is_meta"] else "●"  # ◈ / ●
            marker = "▸" if is_sel else " "
            raw = labels.get(fid, fid)
            if l["is_meta"]:
                raw = f"{raw} ({len(l['leaves'])})"
            label = raw[:label_width].ljust(label_width)
            handle = dim(fid[:10].ljust(10))  # the copy-paste token for revert
            bar = render_cars(l["cars"], hexc, bar_width)
            count = str(l["op_count"]).rjust(5)
            n_ckpt = len(l["cars"])
            ckpt = dim(f" ✦{n_ckpt}") if n_ckpt else ""  # rewind points on this lane
            row_s = (f" {marker}{paint(hexc, glyph)} {handle} "
                     f"{bold(label) if is_sel else label} {bar} {dim(count)}{ckpt}")
            row_s += links_note(fid)
            lines.append(row_s)
            if is_sel and l["cars"]:
                chapters = "   ".join(f"{c['seg_index']} {c['label']}" for c in l["cars"])
                lines.append(dim(f"      {chapters}"))
    lines.append("")

    # Legend + next-step hints: the view explains its own encoding and what to do from here.
    lines.append(dim(" [0···] = a checkpoint car at its commit-time (digit = its @n, brightness = op"
                     " density); [ ] co-changed · ( ) thematic · bold = the lane's big event · dim = past frontier"))
    lines.append(dim(" daily:  sgt graph  (fast, cached)   ·   sgt graph --refresh  (after edits: re-name"
                     " features + checkpoints)   ·   sgt graph --focus <f-XXXX>  (one feature, full detail)"))
    lines.append(dim(" operate:  sgt revert <f-XXXX>  (whole feature)   ·   sgt revert <f-XXXX>@<n>"
                     "  (one checkpoint/car)   ·   sgt intent show <f-XXXX>  (its chapters)"))
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
) -> list[str]:
    """Render the episode rail as a vertical git-log (Stage C): newest episode on top, each feature
    a lane column (its episodes a straight vertical line), lanes reused across non-overlapping
    spans. Each row is one commit-episode -- the "what I did, in order" rewind unit -- with its
    subject and the dominant feature it advanced. Capped at `max_rows` (newest first); a footer
    notes how many older episodes were folded (the lazy nod for a long history)."""
    ep = episodes(map_view, grid_view)
    layout = episode_rail_layout(ep)
    labels = {n["id"]: n.get("label", n["id"]) for n in map_view.get("nodes", [])}
    rows = layout["rows"]
    lane_count = layout["lane_count"]

    # Reconstruct each feature's (top, bot, lane) span, then the per-lane occupants, so a lane a
    # feature passes through (between two of its episodes) draws a vertical connector, not a gap.
    span: dict = {}
    for r in rows:
        fid, rr, L = r["feature"], r["row"], r["lane"]
        if fid not in span:
            span[fid] = [rr, rr, L]
        else:
            span[fid][0], span[fid][1] = min(span[fid][0], rr), max(span[fid][1], rr)
    lane_spans: dict = {}
    for fid, (top, bot, L) in span.items():
        lane_spans.setdefault(L, []).append((top, bot, fid))

    def occupant(lane: int, r: int) -> tuple[bool, str | None]:
        # (found, fid) -- fid may itself be None (the unattributed-episode lane), so a plain
        # None return can't distinguish "the None feature is here" from "nothing is here".
        for top, bot, fid in lane_spans.get(lane, []):
            if top <= r <= bot:
                return True, fid
        return False, None

    def paint(hex_str: str, s: str) -> str:
        return _fg(hex_str, s) if color else s

    def dim(s: str) -> str:
        return f"{_DIM}{s}{_RESET}" if color else s

    def bold(s: str) -> str:
        return f"{_BOLD}{s}{_RESET}" if color else s

    lines: list[str] = []
    n_ep = len(rows)
    n_feat = len(span)
    lines.append(bold(f" {n_ep} episode(s)  ·  {n_feat} feature(s)  ·  {lane_count} lane(s)   (newest on top)"))
    lines.append(dim(" each row = one commit-episode; each column = a feature; ● the episode's feature"))
    lines.append("")

    shown = rows[:max_rows]
    for r in shown:
        this_lane, this_fid = r["lane"], r["feature"]
        cells = []
        for L in range(lane_count):
            found, occ_fid = occupant(L, r["row"])
            if L == this_lane:
                cells.append(paint(color_for(this_fid or ""), "●"))
            elif found:
                cells.append(paint(color_for(occ_fid or ""), "│"))
            else:
                cells.append(" ")
        rail = " ".join(cells)
        sha = dim((r["sha"] or "")[:7].ljust(7))
        subj = (r["subject"] or "").replace("\n", " ")[:label_width].ljust(label_width)
        flabel = labels.get(this_fid, this_fid or "(unattributed)")
        is_sel = this_fid == selected
        tail = dim(f"{flabel} · {r['op_count']} op")
        subj_s = bold(subj) if is_sel else subj
        lines.append(f" {rail}  {sha}  {subj_s}  {tail}")

    if n_ep > len(shown):
        lines.append("")
        lines.append(dim(f" … {n_ep - len(shown)} older episode(s) folded (newest {len(shown)} shown)"))
    lines.append("")
    lines.append(dim(" operate:  sgt revert <f-XXXX>@<n>  (rewind one checkpoint)   ·   sgt graph  (the timeline)"))
    return lines
