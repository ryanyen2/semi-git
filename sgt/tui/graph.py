"""The feature-timeline (Gantt) layout + terminal renderer, shared by `sgt graph` (static print)
and the Textual TUI.

This is the terminal counterpart of the VS Code workbench's `computeGraphLayout` (a faithful port,
kept behaviour-parallel on purpose). A commit-index is not a snapshot node -- it's a *set of ops*
spread across features -- so we don't draw a commit spine or a node cloud. We lay out the FEATURES
as a Gantt:

    row   = one lane per feature, grouped into subsystem swimlanes and ordered by first appearance
            (foundations up top). Vertical position means "which feature / which subsystem".
    x     = real commit-time, a shared axis. A lane's bar spans [first_commit, last_commit] and its
            ops are binned along it as a density heatstrip, so a 2000-op commit is one dark column,
            never a wall of glyphs.
    frontier = a fold point: only ops with commit_index <= frontier count, so scrubbing accretes.

The terminal render is the honest projection of that model: a fixed-width time strip per lane,
brightness = local op density, under a labeled axis -- everything a user needs to answer "what is
here, when did it live, how big, and what can I edit."
"""

from __future__ import annotations

from .color import color_for

# ── Layout (pure) ────────────────────────────────────────────────────────────────────────────────


def graph_layout(
    map_view: dict,
    history_view: dict,
    *,
    collapsed=(),
    frontier: int | None = None,
    top_k: int = 4,
) -> dict:
    collapsed = set(collapsed)
    fr = float("inf") if frontier is None else frontier
    by_id = {n["id"]: n for n in map_view.get("nodes", [])}

    ops_by_feature: dict[str, list] = {}
    for op in history_view.get("ops", []):
        fid = op.get("feature_id")
        if fid is None or op["commit_index"] > fr:
            continue
        ops_by_feature.setdefault(fid, []).append(op)
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
        "row_count": max(1, row), "commit_count": len(history_view.get("commits") or []),
    }


# ── Episodic projection (pure) ───────────────────────────────────────────────────────────────────


def episodes(map_view: dict, history_view: dict) -> dict:
    """Roll the flat op stream up into EPISODES -- one per commit that carried ops -- and group
    episodes by their dominant feature into collapsible episode-groups (the "co-commit cluster" a
    developer rewinds as a unit; Stage C).

    Sessions are empty on mined history (only sgt's own land/checkpoint stamp them), so the episode
    axis is projected from provenance: an op's ``commit_index`` identifies its earliest provenance
    commit, so ops sharing a ``commit_index`` were advanced in the same commit = one episode --
    exactly the co-commit signal Stage B clusters on. Real sgt sessions supersede this going
    forward; the shape is identical. Pure over the same ``map_view``/``history_view(full=True)``
    both surfaces already fetch. The VS Code counterpart is ``rollupEpisodes`` in workbench.js."""
    labels = {n["id"]: n.get("label", n["id"]) for n in map_view.get("nodes", [])}
    subject_of = {c["index"]: c.get("subject", "") for c in history_view.get("commits", [])}
    sha_of = {c["index"]: c.get("sha") for c in history_view.get("commits", [])}

    by_index: dict[int, dict] = {}
    for op in history_view.get("ops", []):
        idx = op["commit_index"]
        ep = by_index.get(idx)
        if ep is None:
            ep = by_index[idx] = {
                "index": idx, "sha": sha_of.get(idx), "subject": subject_of.get(idx, ""),
                "op_ids": [], "features": {}, "kinds": {},
            }
        ep["op_ids"].append(op["id"])
        fid = op.get("feature_id")
        if fid is not None:
            ep["features"][fid] = ep["features"].get(fid, 0) + 1
        kind = op.get("kind")
        if kind:
            ep["kinds"][kind] = ep["kinds"].get(kind, 0) + 1

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


def render_graph_lines(
    map_view: dict,
    history_view: dict,
    *,
    selected: str | None = None,
    frontier: int | None = None,
    collapsed=(),
    color: bool = True,
    bar_width: int = 42,
    label_width: int = 24,
    checkpoints: dict[str, list] | None = None,
) -> list[str]:
    """Render the timeline as terminal lines (ANSI truecolor when `color`). Each feature is a lane:
    its short `f-XXXX` handle (the copy-paste token for `sgt revert <handle>[@n]`), identity glyph,
    label, and a fixed-width time strip whose lit span is [first,last] with per-column brightness =
    op density -- the terminal projection of the same Gantt the VS Code graph draws, under one
    shared, labeled commit axis.

    `checkpoints={fid: [(seg_index, first_commit_index), ...]}` overlays each feature's rewind
    points *on its own lane*: the digit `n` is drawn at the commit-time column where checkpoint
    `<fid>@n` begins, so the density blocks (WHEN the feature was active) and the checkpoints (the
    chapters you can rewind) finally read on the same axis -- `✦N` in the margin is just their
    count. Absent (e.g. the TUI) -> no markers, no count."""
    checkpoints = checkpoints or {}
    fr = float("inf") if frontier is None else frontier
    layout = graph_layout(map_view, history_view, collapsed=collapsed, frontier=frontier)
    labels = {n["id"]: n.get("label", n["id"]) for n in map_view.get("nodes", [])}

    def paint(hex_str: str, s: str) -> str:
        return _fg(hex_str, s) if color else s

    def dim(s: str) -> str:
        return f"{_DIM}{s}{_RESET}" if color else s

    def bold(s: str) -> str:
        return f"{_BOLD}{s}{_RESET}" if color else s

    commit_count = layout["commit_count"]
    max_commit = max(1, commit_count - 1)

    def col_of(ci: int) -> int:
        return max(0, min(bar_width - 1, int(ci / max_commit * bar_width)))

    # Global max per-column op count, for brightness normalization across lanes.
    lane_cols: dict[str, list] = {}
    gmax = 1
    for l in layout["lanes"]:
        cols = [0] * bar_width
        for ci in l["commits"]:
            cols[col_of(ci)] += 1
        lane_cols[l["id"]] = cols
        gmax = max(gmax, *cols) if cols else gmax

    def strip(cols: list[int], first: int, last: int) -> str:
        """A bar_width time strip: shaded block where ops land (brightness=density), a dim track
        across the feature's [first,last] lifetime, blank outside it."""
        lo, hi = col_of(first), col_of(last)
        out = []
        for c in range(bar_width):
            n = cols[c]
            if n > 0:
                out.append((n, "█"))  # █
            elif lo <= c <= hi:
                out.append((0, "─"))  # ─ lifetime track
            else:
                out.append((-1, " "))
        return out  # list of (count, char); colored by caller with the lane hue

    # Nearest co-change neighbours (strongest first), for the per-lane "connects to" annotation.
    nbrs: dict[str, list] = {}
    for e in sorted(layout["edges"], key=lambda e: -e["weight"]):
        nbrs.setdefault(e["a"], []).append(e["b"])
        nbrs.setdefault(e["b"], []).append(e["a"])

    lines: list[str] = []
    total_ops = sum(l["op_count"] for l in layout["lanes"])
    n_sub = len(layout["headers"])
    sub_note = f"  ·  {n_sub} subsystem(s)" if n_sub else ""
    lines.append(bold(f" {len(layout['lanes'])} feature(s)  ·  {commit_count} commit(s)  ·  {total_ops} op(s){sub_note}"))
    if frontier is not None:
        lines.append(dim(f"   frontier: folded at commit {frontier} (later features hidden)"))
    lines.append("")

    # Shared time axis: c0 at the strip's left edge, cMax at its right. The gutter width matches the
    # lane prefix (" " + marker + glyph + " " + handle + " " + label + " ") so the axis aligns over
    # the bars.
    handle_width = 10
    gutter = " " * (label_width + handle_width + 6)
    ends = f"c0c{max_commit}"
    axis = "c0" + "─" * max(1, bar_width - len(ends)) + f"c{max_commit}"
    lines.append(dim(f"{gutter}{axis}  time →"))

    lanes_by_row = {l["row"]: l for l in layout["lanes"]}
    headers_by_row = {h["row"]: h for h in layout["headers"]}
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
            handle = dim(fid[:handle_width].ljust(handle_width))  # the copy-paste token for revert
            cells = strip(lane_cols[fid], l["first_commit"], l["last_commit"])
            # Checkpoint markers: the digit `n` at the column where `<fid>@n` begins. First-in wins
            # a column so a marker is never silently swallowed by a later, denser one.
            ck = [m for m in checkpoints.get(fid, []) if m[1] <= fr]
            marks: dict[int, int] = {}
            for seg_index, first_ci in sorted(ck, key=lambda m: m[0]):
                marks.setdefault(col_of(first_ci), seg_index)
            bar = ""
            for c, (n, ch) in enumerate(cells):
                if c in marks:  # a rewind point takes the column over the density cell
                    d = str(marks[c])
                    bar += (bold(paint(hexc, d)) if color else d)
                elif n > 0:
                    bar += _shade(hexc, (n / gmax) ** 0.5, ch) if color else ch
                elif n == 0:
                    bar += dim(ch)
                else:
                    bar += ch
            count = str(l["op_count"]).rjust(5)
            n_ckpt = len(ck)
            ckpt = dim(f" ✦{n_ckpt}") if n_ckpt else ""  # rewind points on this lane (see the digits)
            link_ids = nbrs.get(fid, [])[:2]
            links = ", ".join(labels.get(x, x) for x in link_ids)
            extra = len(nbrs.get(fid, [])) - len(link_ids)
            row_s = (f" {marker}{paint(hexc, glyph)} {handle} "
                     f"{bold(label) if is_sel else label} {bar} {dim(count)}{ckpt}")
            if links:
                row_s += "  " + dim("↔ " + links + (f" +{extra}" if extra > 0 else ""))
            lines.append(row_s)
    lines.append("")

    # Legend + next-step hints: the view explains its own encoding and what to do from here.
    lines.append(dim(" f-XXXX = the handle you type   bar = op density over time   digits 0·1·2 = where"
                     " checkpoint @n begins   ✦N = N rewind points   ↔ co-change   (structural) = glue"))
    lines.append(dim(" daily:  sgt graph  (fast, cached)   ·   sgt graph --refresh  (after edits: re-name"
                     " features + checkpoints)"))
    lines.append(dim(" operate:  sgt revert <f-XXXX>  (whole feature)   ·   sgt revert <f-XXXX>@<n>"
                     "  (one checkpoint)   ·   sgt intent show <f-XXXX>  (its chapters)"))
    return lines


# ── Episode rail render (vertical git-log) ───────────────────────────────────────────────────────


def render_rail_lines(
    map_view: dict,
    history_view: dict,
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
    ep = episodes(map_view, history_view)
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
