"""The redesigned `sgt log` renderers, drawn in the one visual language `theme.py` defines.

Nothing in here computes layout. `graph_layout`/`segment_layout` in `graph.py` stay exactly as they
were -- they are one half of a pair whose other half is `computeGraphLayout` in the webview, and a
rule changed in one and not the other drifts silently. What was wrong was never the arithmetic; it
was that the arithmetic was presented with no columns, no ruler, two lines per row, and a legend
underneath explaining an encoding the view could have simply shown.
"""

from __future__ import annotations

from rich.text import Text

from .color import color_for
from .theme import (
    BAR_CELL_MAX, BAR_MIN, BODY, FAINT, FOLD, GHOST, GROUP, GUIDE_BAR, GUIDE_END, GUIDE_MID,
    GUIDE_PAD, HEAD, LEAF, MERGE, MUTE, NAME_FRAC, NAME_MIN, OFFTRUNK, SEL, axis_header,
    car_strip, console, emit, fit,
    FAIL, OK, WARN, agrees, guides, plural, rule, spark_bar, term_height, term_width, to_lines,
    viewport_footer,
)


def _rows_in_order(layout: dict) -> list[tuple[str, dict]]:
    """The header/lane sequence, in the row order the layout fixed.

    That order carries an invariant worth naming: a parent's own lanes come before its sub-groups.
    Ordering a level by first-commit alone once filed four root features *under* a whole subsystem
    subtree, where the indent reads as membership -- so this walk must never re-sort."""
    by_row: dict[int, tuple[str, dict]] = {}
    for h in layout.get("headers", []):
        by_row[h["row"]] = ("header", h)
    for l in layout.get("lanes", []):
        by_row[l["row"]] = ("lane", l)
    return [by_row[r] for r in range(layout.get("row_count", 0)) if r in by_row]


def _guide_prefix(seq: list[tuple[str, dict]]) -> list[str]:
    """Tree guides for every row, computed from the depth sequence.

    A row is the last of its level when no later row reaches that depth again before the sequence
    steps back out above it. Drawing containment with `├─ └─ │` instead of leading spaces means a
    row still says who owns it after you have scrolled its parent off the screen -- which is the
    case that matters, since scrolling past the parent is what a long timeline does."""
    depths = [max(0, d.get("depth", 0)) for _, d in seq]
    out: list[str] = []
    for i, depth in enumerate(depths):
        if depth <= 0:
            out.append("")
            continue
        last = True
        for j in range(i + 1, len(depths)):
            if depths[j] < depth:
                break
            if depths[j] == depth:
                last = False
                break
        # An ancestor level draws a continuing `│` only while it still has rows to come.
        stem = ""
        for lvl in range(1, depth):
            more = False
            for j in range(i + 1, len(depths)):
                if depths[j] < lvl:
                    break
                if depths[j] == lvl:
                    more = True
                    break
            stem += GUIDE_BAR if more else GUIDE_PAD
        out.append(stem + (GUIDE_END if last else GUIDE_MID))
    return out


def _per_commit(cars: list[dict]) -> tuple[dict[int, int], set[int]]:
    """A lane's edits per commit index, and which of those sit past the frontier."""
    per: dict[int, int] = {}
    future: set[int] = set()
    for c in cars:
        bins = c.get("sub_bins") or ()
        if not bins:  # a car with no per-commit detail still occupies its own span
            bins = [(i, 1) for i in range(c["first_index"], c["last_index"] + 1)]
        for ci, cnt in bins:
            per[ci] = per.get(ci, 0) + cnt
            if c.get("is_future"):
                future.add(ci)
    return per, future


def map_lines(
    layout: dict,
    labels: dict,
    *,
    selected: str | None = None,
    frontier: int | None = None,
    color: bool = True,
    bar_width: int | None = None,
    max_rows: int | None = None,
    ghost_by_lane: dict[str, list[str]] | None = None,
    unplaced_ghosts: list[dict] | None = None,
    notes: list[str] | None = None,
    links: dict[str, str] | None = None,
    emphasis: dict | None = None,
    themes: list[dict] | None = None,
) -> list[str]:
    """`sgt log`: one line per lane, on one shared commit axis.

    `emphasis` (`{"ids": set, "label": str, "kind": str}`) is the focus-within-context read
    (`--focus <feature|subsystem|theme>`): the WHOLE map still renders, but only the emphasized
    lanes keep their checkpoint strips -- every other lane compresses to the fold-style density
    spark with a faint name, so the reader keeps their place in the codebase while the group
    stands out. This replaced the scoped views that dropped the map entirely: a focus that hides
    everything else answers "what is in this group" but loses "where does it sit".

    `themes` (`theme_spans` rows, span >= 2 only) draw as rows IN this table, on the same axis:
    one task's work that landed across lanes has no lane of its own, so it gets a ◆ row whose
    density marks sit in the exact columns of its member commits -- the vertical alignment with
    the lane blocks above is the join. A prose footer here before that, and `sgt intent list`
    before THAT, both made the reader map names back onto the picture by hand.

    The row is three columns and only three: WHO (guides + glyph + name), WHEN (the density bar
    under its own `c0 … cN` ruler), and HOW MUCH (a right-aligned edit count). The `@n` checkpoint
    chips that used to occupy a second line per lane are gone from this view -- they were printed at
    a fixed indent that lined up with nothing above them, so they broke the column grid on every
    row and halved how much history fits on a screen. They are the subject of `--focus`, which
    renders them properly, and the footer says so.
    """
    from .graph import _echoes, _min_unique_prefixes
    ghost_by_lane = ghost_by_lane or {}
    width = term_width()
    con = console(color=color, width=width)
    seq = _rows_in_order(layout)
    prefixes = _guide_prefix(seq)

    axis_len = max(1, int(layout.get("commit_count") or 0))
    n_feat = sum(len(l["leaves"]) for l in layout.get("lanes", []))
    n_sub = (sum(1 for h in layout.get("headers", []) if h["depth"] > 0)
             + sum(1 for l in layout.get("lanes", []) if l["is_meta"] and l.get("depth", 0) > 0))

    # ── Column geometry ──────────────────────────────────────────────────────────────────────────
    # The name column asks for what its longest label actually needs, and is granted the smaller of
    # that, a share of the terminal, and whatever survives the bar's floor. Widening the terminal
    # therefore un-truncates names before it stretches an axis that has nothing more to say.
    def lane_name(l: dict) -> str:
        raw = labels.get(l["id"], l["id"])
        n = len(l["leaves"])
        # `(1 feature)` after a fold's name is a count nobody needed: opening it shows one row, and
        # the parenthesis was on more than half the rows in this repo's map saying nothing.
        return f"{raw}  ({n})" if l["is_meta"] and n > 1 else raw

    natural = 0
    for (kind, node), g in zip(seq, prefixes):
        text = node["label"] if kind == "header" else lane_name(node)
        natural = max(natural, len(g) + 2 + len(text))  # +2 for the glyph and its space
    max_edits = max((l.get("op_count") or sum(c.get("op_count", 0) for c in l["cars"])
                     for l in layout.get("lanes", [])), default=0)
    edits_w = max(len("EDITS"), len(f"{max_edits:,}"))
    # The copy-paste handle, in its own column. It was dropped from this view in the first pass of
    # the redesign on the grounds that names address features too -- which is true, and beside the
    # point: a name has to be typed in full and quoted, while `002b` is four characters and every
    # verb resolves a unique prefix. Putting it AFTER the name column is what makes it affordable:
    # the wall of hex the old layout put between the glyph and the name is what made it unreadable,
    # not the hex itself.
    prefix_len = _min_unique_prefixes(list(layout.get("node_by_id") or {}))
    id_w = 0 if not prefix_len else 8

    def latest_of(l: dict) -> str:
        """The newest checkpoint, as `@n Name`.

        The strip names a chapter only when the empty time after it has room, and the NEWEST chapter
        is the one sitting hard against the right edge with no room at all -- so the checkpoint a
        reader most often wants to go back to was the one guaranteed to arrive unnamed. It gets a
        column of its own."""
        if l["is_meta"] or not l["cars"]:
            return ""
        c = l["cars"][-1]
        name = "" if _echoes(c.get("label") or "", lane_name(l)) else (c.get("label") or "")
        return f"@{c['seg_index']} {name}".rstrip()

    latest_want = max((len(latest_of(l)) for l in layout.get("lanes", [])), default=0)
    gutter = 1        # the leading space every line carries
    sel_w = 2         # the `▸ ` selection marker column
    pad = 2           # between every pair of columns
    # An annotation column is reserved UP FRONT when there is anything to put in it -- planned
    # steps, `--links` neighbours -- because appending a note to a row already sized to fill the
    # terminal just cropped it. Zero when nothing is annotated.
    anno_w = min(26, max(0, int(width * 0.22))) if (ghost_by_lane or links) else 0

    # ── The budget, in priority order ────────────────────────────────────────────────────────────
    # The STRIP is what this view is for, so it is served first and the name column takes what is
    # left. Serving the name first (a flat share of the terminal) is what squeezed a 444-commit
    # history into eighteen columns on a 124-column screen: every chapter collapsed to a single `▏`,
    # and the one thing the reader came here to read was the one thing that had no room.
    spine = gutter + sel_w + pad + (id_w + pad if id_w else 0) + pad + edits_w + anno_w
    want_bar = min(max(BAR_MIN, int(width * 0.36)), BAR_CELL_MAX * axis_len)
    latest_w = min(latest_want, 24, max(0, width - spine - NAME_MIN - want_bar - pad))
    if latest_w < 6:
        latest_w = 0
    fixed = spine + ((latest_w + pad) if latest_w else 0)
    name_w = max(NAME_MIN, min(natural, width - fixed - want_bar))
    bar_w = bar_width if bar_width is not None else max(
        BAR_MIN, min(width - fixed - name_w, BAR_CELL_MAX * axis_len))
    name_x = gutter + sel_w

    def _row_w(bw: int) -> int:
        return (name_x + name_w + (pad + id_w if id_w else 0) + pad + bw + pad + edits_w
                + ((pad + latest_w) if latest_w else 0))

    row_w = _row_w(bar_w)
    if row_w > width:  # never wider than the terminal: `emit` would crop the last column off
        bar_w = max(BAR_MIN, bar_w - (row_w - width))
        row_w = _row_w(bar_w)

    # Density survives on FOLDED rows only -- a fold has no `@n` of its own, so it has no blocks to
    # draw -- which means the scale must come from the folded rows and nothing else. Taken over every
    # lane it was set by feature rows that draw no density at all, and since a fold is the SUM of the
    # features inside it, every fold then rendered systematically short against a ceiling nothing on
    # screen reached.
    scale_max = 0
    for l in layout.get("lanes", []):
        if not l["is_meta"]:
            continue
        per, _ = _per_commit(l["cars"])
        if per:
            scale_max = max(scale_max, max(per.values()))
    for t in themes or []:
        if t.get("per_commit"):
            scale_max = max(scale_max, max(t["per_commit"].values()))

    # ── Header ───────────────────────────────────────────────────────────────────────────────────
    title = Text(" ", style=BODY)
    title.append(plural(n_feat, "feature"), style=HEAD)
    title.append("  ·  ", style=MUTE)
    title.append(plural(layout.get("save_count", 0), "save"), style=MUTE)
    if n_sub:
        title.append("  ·  ", style=MUTE)
        title.append(plural(n_sub, "subsystem"), style=MUTE)
    if frontier is not None:
        title.append("  ·  ", style=MUTE)
        title.append(f"frontier c{frontier}", style=FAINT)
    emit(con, title)
    # The focus banner: what is emphasized and how to widen back out. Stated above the rows
    # because a mostly-dim map with no explanation reads as a rendering fault, not a lens.
    if emphasis is not None:
        in_ids = emphasis.get("ids") or set()
        n_mem = sum(len(l["leaves"]) for l in layout.get("lanes", [])
                    if l["id"] in in_ids or any(x in in_ids for x in l["leaves"]))
        banner = Text(" ", style=BODY)
        banner.append("◉ ", style=HEAD)
        banner.append(emphasis.get("label", ""), style=HEAD)
        kind = emphasis.get("kind")
        kind_note = ("feature" if kind == "feature"
                     else f"subsystem, {plural(n_mem, 'feature')}" if kind == "subsystem"
                     else f"one piece of work across {plural(n_mem, 'feature')}")
        banner.append(f"  ·  {kind_note}"
                      "  ·  other lanes dimmed — bare `sgt log` shows everything", style=MUTE)
        emit(con, banner)
    # Work this view cannot draw -- reverted ops with no lane left to hang off -- is disclosed here,
    # above the rows. A map that silently omits them reads as a complete account of the repository,
    # which is the false-green this note exists to prevent.
    for n in (notes or []):
        emit(con, Text(" ", style=MUTE).append(n, style=MUTE))
    if frontier is not None:
        emit(con, Text(" ", style=MUTE).append(
            f"frontier: folded at commit {frontier} — later work is hidden", style=MUTE))
    emit(con, "")

    head = Text(" " * name_x, style=MUTE)
    head.append("FEATURE".ljust(name_w), style=MUTE)
    if id_w:
        head.append(" " * pad)
        head.append("ID".ljust(id_w), style=MUTE)
    head.append(" " * pad)
    head.append_text(axis_header(axis_len, bar_w))
    head.append(" " * pad)
    head.append("EDITS".rjust(edits_w), style=MUTE)
    if latest_w:
        head.append(" " * pad)
        head.append("LATEST".ljust(latest_w), style=MUTE)
    emit(con, head)
    emit(con, rule(row_w - gutter, left=" " * gutter))

    # ── Rows ─────────────────────────────────────────────────────────────────────────────────────
    # The viewport fills the terminal rather than a hardcoded 40, so the same command is one screen
    # on every screen, and never floods a scrollback at 500 features.
    budget = max_rows if max_rows is not None else max(6, term_height() - 12)
    shown = 0
    total_lanes = len(layout.get("lanes", []))
    lanes_drawn = 0
    drew_meta = False   # whether any folded group made it onto the screen (drives the footer)
    for (kind, node), g in zip(seq, prefixes):
        if shown >= budget:
            break
        line = Text(" " * gutter)
        if kind == "header":
            # A subsystem is a fold over features, never a verb target, so it carries no identity
            # hue and no count column -- only its name, its caret, and how many features it holds.
            line.append("  ")
            line.append(g, style=MUTE)
            line.append(f"{GROUP} ", style=MUTE)
            line.append(fit(node["label"], name_w - len(g) - 2), style=HEAD)
            # A group's size is only worth a column when it is more than one -- `· 1 feature` above
            # a single row states what the single row already shows.
            if node["lane_count"] > 1:
                line.append("  ·  ", style=MUTE)
                line.append(plural(node["lane_count"], "feature"), style=MUTE)
            emit(con, line)
            shown += 1
            continue

        l = node
        fid = l["id"]
        hexc = color_for(fid)
        is_sel = fid == selected
        # The TableLens read: under an emphasis, a lane is either IN the group (full detail) or
        # context (present, findable, compressed to its density spark). Membership goes through
        # `leaves` so a folded row lights up when the group's features sit inside it.
        emph_ids = (emphasis or {}).get("ids") or set()
        in_focus = emphasis is None or fid in emph_ids or any(x in emph_ids for x in l["leaves"])
        line.append(f"{SEL} " if is_sel else "  ", style=BODY)
        line.append(g, style=MUTE)
        # A folded row stands for a group of features, so it takes the group's caret rather than a
        # feature's disc: the glyph says which verbs apply before the reader has to find out.
        drew_meta = drew_meta or l["is_meta"]
        glyph = FOLD if l["is_meta"] else LEAF
        line.append(f"{glyph} ", style=FAINT if not in_focus else (MUTE if l["is_meta"] else hexc))
        name = fit(lane_name(l), name_w - len(g) - 2)
        name_style = HEAD if (is_sel or (emphasis is not None and in_focus)) else BODY
        line.append(name.ljust(name_w - len(g) - 2), style=FAINT if not in_focus else name_style)
        if id_w:
            line.append(" " * pad)
            # Bright = the minimal unique prefix, dim = the rest: the eye reads straight off the row
            # how few characters actually select this feature. A folded row is not a verb target and
            # has no handle to offer, so it shows nothing rather than a token that resolves to
            # nothing.
            if l["is_meta"]:
                line.append(" " * id_w)
            else:
                body = fid[2:] if fid.startswith("f-") else fid
                disp = body[:id_w]
                k = max(0, min(prefix_len.get(fid, 5) - (2 if fid.startswith("f-") else 0),
                               len(disp)))
                line.append(disp[:k], style=f"bold {hexc}" if color else BODY)
                line.append(disp[k:].ljust(id_w - k), style=MUTE)
        line.append(" " * pad)
        # The checkpoint strip: every block is one `@n`, the exact unit `revert`/`restore` take.
        # A context lane under an emphasis keeps only the density read -- the same compression a
        # fold already uses -- so the emphasized lanes are the only ones spending chip ink.
        if l["cars"] and not l["is_meta"] and in_focus:
            line.append_text(car_strip(l["cars"], axis_len=axis_len, width=bar_w, hexc=hexc,
                                       color=color,
                                       name_of=lambda c: None if _echoes(c.get("label") or "",
                                                                         lane_name(l))
                                       else c.get("label"),
                                       spill_last=not latest_w))
        else:
            # A folded subsystem has no checkpoints of its own -- its `@n` belong to the features
            # inside it -- so it keeps the density read, which is the honest thing a fold can say.
            per, future = _per_commit(l["cars"])
            line.append_text(spark_bar(per, axis_len=axis_len, width=bar_w, scale_max=scale_max,
                                       hexc=None, faint_at=future, color=color))
        line.append(" " * pad)
        n_edits = l.get("op_count") or sum(c.get("op_count", 0) for c in l["cars"])
        line.append(f"{n_edits:,}".rjust(edits_w), style=MUTE)
        if latest_w:
            line.append(" " * pad)
            line.append(fit(latest_of(l) if in_focus else "", latest_w), style=MUTE)
        room = anno_w
        planned = ghost_by_lane.get(fid) or []
        if planned and room >= 12:
            # One planned step is worth naming; several are worth counting. The old forecast band
            # spent up to 40% of the bar's columns on cards for work that has not happened yet,
            # crowding out the measured history they hang off.
            tail = (f"  {GHOST} {fit(planned[0], room - 4)}" if len(planned) == 1
                    else f"  {GHOST} {len(planned)} planned")
            line.append(tail, style=FAINT)
            room -= len(tail)
        if links and links.get(fid) and room >= 12:
            line.append(f"  ↔ {fit(links[fid], room - 4)}", style=FAINT)
        emit(con, line)
        shown += 1
        lanes_drawn += 1

    # ── Work across several features: rows in the SAME table, on the SAME axis ──────────────────
    # One task's work that landed on several lanes has no lane of its own, so it draws here as a
    # row whose density marks sit in the exact columns of its member commits -- the vertical
    # alignment with the lane blocks above IS the join. This replaced a prose footer that named
    # the same work and left the reader to map the names back onto the picture by hand, which is
    # the `sgt intent list` mistake all over again. No third noun: these are checkpoints that
    # landed across features, named once.
    if themes and emphasis is None:
        emit(con, rule(row_w - gutter, left=" " * gutter))
        thead = Text(" " * gutter + "  ")
        thead.append("↕ ", style=MUTE)
        thead.append("work across several features", style=MUTE)
        emit(con, thead)
        for t in themes[:5]:
            trow = Text(" " * gutter)
            trow.append("  ")
            trow.append("◆ ", style=HEAD)
            # A ◆ row carries no id, so its id column is blank -- give those columns to the name.
            # This name is the one a reader has to be able to TYPE: `sgt log --focus "<name>"`,
            # `sgt revert "<name>"` and `sgt restore "<name>"` all take it, and a ◆ has no id to
            # fall back to the way a lane does. Truncated to the lane name width it came out as
            # "Event Day Handl…", and that prefix does not resolve -- it offers a different,
            # plausible feature instead. Same total prefix width, so the bar still starts in the
            # column the lanes above align to.
            tname_w = name_w - 2 + (pad + id_w if id_w else 0)
            trow.append(fit(t["label"], tname_w).ljust(tname_w), style=BODY)
            trow.append(" " * pad)
            # The cells paint in the hue of the LANE the work landed on at that moment, so the
            # row is read by colour-matching against the lanes above -- a grey strip carried no
            # information at all (nobody can line columns up across ten rows of distance by eye).
            feat_at = t.get("feature_at") or {}
            cells = [None] * bar_w
            for ci, fid in feat_at.items():
                col = min(bar_w - 1, int(ci * bar_w / max(1, axis_len)))
                cells[col] = fid
            for fid in cells:
                if fid is None:
                    trow.append(GUIDE_PAD if not color else "·", style=FAINT)
                else:
                    trow.append("█", style=color_for(fid) if color else BODY)
            trow.append(" " * pad)
            trow.append(f"{t.get('op_count', 0):,}".rjust(edits_w), style=MUTE)
            if latest_w:
                trow.append(" " * pad)
                trow.append(fit(f"across {len(t['feature_ids'])} features", latest_w), style=MUTE)
            emit(con, trow)
        if len(themes) > 5:
            emit(con, Text(" " * gutter + "    ", style=FAINT).append(
                f"… and {len(themes) - 5} more", style=FAINT))

    # ── Footer ───────────────────────────────────────────────────────────────────────────────────
    emit(con, "")
    emit(con, Text(" ", style=MUTE).append_text(
        viewport_footer(lanes_drawn, total_lanes, "row", "--focus <name> to open one")))
    for gh in (unplaced_ghosts or []):
        emit(con, Text(" ", style=FAINT).append(
            f"{GHOST} planned, no lane yet: {fit(gh.get('title', ''), 48)}", style=FAINT))
    # The one thing the view cannot show about itself: that a bar's height is relative, and that the
    # chapters live one command away. Everything else -- that columns are time, that the hue is the
    # feature -- the ruler and the swatches now say on their own.
    # The footer's job is to close the loop the strip opens: every block is an `@n`, and the reader
    # needs to know what typing one of them gets them. Addressed with a REAL handle taken from the
    # rows above, because a `<name>` placeholder is one more thing to work out before you can act.
    example = next((l for l in layout.get("lanes", []) if not l["is_meta"] and l["cars"]), None)
    if example is not None:
        fid_ex = example["id"]
        h = fid_ex[2:10] if fid_ex.startswith("f-") else fid_ex[:8]
        n = example["cars"][-1]["seg_index"]
        clauses = [
            f"each block is one checkpoint — `sgt show {h}@{n}` says what it is "
            "and what reverting it would cost",
            f"`sgt revert {h}@{n}` removes just that checkpoint  ·  "
            f"`sgt log --focus {h}` lists them all",
        ]
    else:
        clauses = ["each block is one checkpoint — `sgt show <id>@<n>` says what it is"]
    # A folded row's ONLY affordance is the verb that opens it, and a map can be entirely folded --
    # in which case the loop above finds no feature to make an example of and this line is the only
    # thing on screen telling the reader how to get anywhere.
    if drew_meta:
        clauses.append('`sgt log --focus "<name>"` opens a folded subsystem')
    if themes and emphasis is None:
        example_t = themes[0]["label"]
        clauses.append(f'a ◆ row is one piece of work across several features, in the same '
                       f'columns as the lanes it touched — `sgt log --focus "{example_t}"` '
                       f'shows it on them; `sgt revert "{example_t}"` removes it everywhere')
    for clause in clauses:
        emit(con, Text(" ", style=FAINT).append(fit(clause, width - 2), style=FAINT))
    return to_lines(con, color=color)


def rail_lines(
    rows: list[dict],
    *,
    labels: dict,
    lane_count: int,
    lane_intervals: dict,
    feature_touched: dict,
    n_saves: int,
    n_features: int,
    total_saves: int,
    topology: dict | None = None,
    selected: str | None = None,
    group_label: str | None = None,
    ghosts: tuple[list[dict], list[dict]] = ([], []),
    color: bool = True,
    max_rows: int | None = None,
    width: int | None = None,
    overflow_rows: set | None = None,
    overflow_lane: int | None = None,
    notes: list[str] | None = None,
) -> list[str]:
    """`sgt log` (the default) and `--rail`: one row per save, newest on top.

    The lane gutter is the view's whole reason to exist -- a feature touched by several saves reads
    as one unbroken vertical line -- so it keeps the left edge, where `git log --graph` puts the
    same information. What changed is everything to its right: the subject and the feature column
    now split a measured budget instead of both writing from wherever they happened to start, which
    is what let a long subject run straight through the feature names in both this view and the
    workbench's copy of it.

    The three-line legend is gone. It explained marks the reader could see (a dot, a hue) and marks
    that were not on screen at all, every single run; what survives is one line naming only the
    glyphs this render actually drew.
    """
    placed_ghosts, unplaced_ghosts = ghosts
    width = width or term_width()
    con = console(color=color, width=width)
    mainline = set((topology or {}).get("mainline", ()))
    merges = set((topology or {}).get("merges", ()))

    budget = max_rows if max_rows is not None else max(6, term_height() - 10)
    shown = rows[:budget]

    # ── Column geometry ──────────────────────────────────────────────────────────────────────────
    pos_w = max((len(f"c{r['index']}") for r in shown), default=3)
    if placed_ghosts:
        pos_w = max(pos_w, len("plan"))
    topo_w = 2 if topology is not None else 0
    gutter, sha_w, pad = 1, 7, 2
    fixed = gutter + topo_w + lane_count + pad + pos_w + pad + sha_w + pad + pad
    flex = max(20, width - fixed)
    # The feature column asks for exactly what its widest entry needs and is granted it as long as
    # the subject keeps a floor -- because a HALF name is often unidentifiable ("Semantic Versioning
    # Arch…" and "Semantic Versioning Auth…" are the same cell), while a half subject still reads.
    # Below the point where a name could identify anything at all, the column is dropped rather than
    # truncated to noise: the coloured dot in the gutter already attributes the row.
    want = max((len(((r.get("feature_names") or [""])[0]))
                + (len(f" +{len(r['feature_names']) - 1}") if len(r.get("feature_names") or []) > 1 else 0)
                for r in shown), default=0)
    # The column is dropped when there is no ROOM for it, never because the names happen to be
    # short: `want` is what the widest name asks for, and a repo whose features are called `Wire`
    # and `RGA` asks for six columns -- which is not a reason to stop naming them.
    room = min(36, flex - 30)
    feat_w = 0 if room < 10 else max(0, min(want, room))
    # The subject takes what is left, but no more than its longest entry needs: a table whose rule
    # runs thirty columns past its own content reads as a column that is missing something.
    longest = max((len((r.get("subject") or "").replace("\n", " ")) for r in shown), default=20)
    subj_w = max(20, min(flex - feat_w - (pad if feat_w else 0), longest))
    # Spelled out from the parts rather than as `fixed + …`: `fixed` folds in the pad that separates
    # the subject from the feature column, so reusing it here counted that gap twice and drew a rule
    # a dozen columns past the widest row.
    table_w = (gutter + topo_w + lane_count + pad + pos_w + pad + sha_w + pad + subj_w
               + ((pad + feat_w) if feat_w else 0))

    def rail_cell(lane: int, r_idx: int, is_dom: bool) -> Text:
        """● where this lane's feature touched this save, │ where it is carried across one it did
        not, blank where the lane is idle. Bold ● marks the save's dominant feature."""
        fid = next((f for top, bot, f in lane_intervals.get(lane, ()) if top <= r_idx <= bot), None)
        if fid is None:
            # The shared overflow lane: several features live here at once, so a connector cannot
            # honestly trace one of them. `┆` says "more features, pooled" and stops there; the
            # dominant one still gets its dot below, in its own hue.
            if overflow_lane is not None and lane == overflow_lane and r_idx in (overflow_rows or ()):
                return Text("┆", style=MUTE)
            return Text(" ")
        hexc = color_for(fid)
        if r_idx in feature_touched.get(fid, ()):
            return Text(LEAF, style=f"bold {hexc}" if is_dom else hexc)
        return Text("│", style=f"{hexc} dim")

    def topo_cell(sha: str | None) -> Text:
        if topology is None:
            return Text("")
        s = sha or ""
        if s in merges:
            return Text(f"{MERGE} ", style=HEAD)
        return Text(f"{LEAF} " if s in mainline else f"{OFFTRUNK} ", style=MUTE)

    # ── Header ───────────────────────────────────────────────────────────────────────────────────
    title = Text(" ", style=BODY)
    if group_label:
        title.append(f"focus: {group_label}", style=HEAD)
        title.append("  ·  ", style=MUTE)
    # `n_saves` counts saves with tracked work; `total_saves` counts every commit. Printing both
    # under the bare word "save" -- as a `7 saves` header over a `5 saves` footer -- makes the view
    # look like it lost two, so the difference is stated where it arises.
    if total_saves and total_saves != n_saves:
        title.append(f"{n_saves:,} of {plural(total_saves, 'save')} with tracked work", style=HEAD)
    else:
        title.append(plural(n_saves, "save"), style=HEAD)
    title.append("  ·  ", style=MUTE)
    title.append(plural(n_features, "feature"), style=MUTE)
    title.append("  ·  ", style=MUTE)
    title.append("newest first", style=MUTE)
    emit(con, title)
    # This is the screen a reader lands on, so it carries the same disclosure the map does. Saying
    # it on only the second screen leaves the first one reading as a whole codebase.
    for n in (notes or []):
        emit(con, Text(" ", style=MUTE).append(n, style=MUTE))
    emit(con, "")

    head = Text(" " * (gutter + topo_w + lane_count + pad), style=MUTE)
    head.append("WHEN".ljust(pos_w), style=MUTE)
    head.append(" " * pad)
    head.append("SAVE".ljust(sha_w), style=MUTE)
    head.append(" " * pad)
    head.append("WHAT".ljust(subj_w), style=MUTE)
    if feat_w:
        head.append(" " * pad)
        head.append("FEATURE", style=MUTE)
    emit(con, head)
    emit(con, rule(min(width - gutter, table_w - gutter), left=" " * gutter))

    # ── Ghost rows (planned, no code yet) ────────────────────────────────────────────────────────
    for g in placed_ghosts:
        fid = g["feature_id"]
        hexc = color_for(fid)
        line = Text(" " * gutter)
        line.append(" " * topo_w)
        for L in range(lane_count):
            line.append(GHOST if L == g["lane"] else " ", style=f"{hexc} dim")
        line.append(" " * pad)
        line.append("plan".rjust(pos_w), style=FAINT)
        line.append(" " * pad)
        line.append(" " * sha_w)
        line.append(" " * pad)
        line.append(fit(g.get("title", "") or "planned", subj_w).ljust(subj_w), style=FAINT)
        if feat_w:
            line.append(" " * pad)
            line.append(fit(labels.get(fid, fid), feat_w), style=hexc)
        emit(con, line)

    # ── Rows ─────────────────────────────────────────────────────────────────────────────────────
    for r in shown:
        line = Text(" " * gutter)
        line.append_text(topo_cell(r["sha"]))
        for L in range(lane_count):
            line.append_text(rail_cell(L, r["row"], L == r["lane"]))
        line.append(" " * pad)
        line.append(f"c{r['index']}".rjust(pos_w), style=MUTE)
        line.append(" " * pad)
        sha7 = (r["sha"] or "")[:7]
        line.append(sha7.ljust(sha_w),
                    style=(color_for(r["feature"]) if r["feature"] else MUTE))
        line.append(" " * pad)
        subj = fit((r.get("subject") or "").replace("\n", " "), subj_w)
        line.append(subj.ljust(subj_w), style=HEAD if r["feature"] == selected else BODY)
        if feat_w:
            line.append(" " * pad)
            names = r.get("feature_names") or []
            # One name plus an honest overflow count, never a run of half-names. The old chip
            # column packed as many as its budget allowed and let the last one run under the next
            # column; a reader cannot use `Composition Wor…` and `Ownership Ledge…` side by side
            # anyway, and the gutter already says which feature owns the save.
            if names:
                more = f" +{len(names) - 1}" if len(names) > 1 else ""
                line.append(fit(names[0], feat_w - len(more)),
                            style=color_for(r["feature"]) if r["feature"] else MUTE)
                if more:
                    line.append(more, style=MUTE)
        emit(con, line)

    # ── Footer ───────────────────────────────────────────────────────────────────────────────────
    emit(con, "")
    emit(con, Text(" ", style=MUTE).append_text(
        viewport_footer(len(shown), n_saves, "save", "--limit / --offset for more")))
    for g in unplaced_ghosts:
        emit(con, Text(" ", style=FAINT).append(
            f"{GHOST} planned, no lane yet: {fit(g.get('title', ''), 48)}", style=FAINT))

    # Only the glyphs this render actually drew. A legend that describes absent marks teaches the
    # reader that the header is not about what they are looking at, and they stop reading it.
    seen = {r["sha"] for r in shown}
    bits = []
    if topology is not None and (seen & merges):
        bits.append(f"{MERGE} merge")
    if topology is not None and (seen - mainline - merges):
        bits.append(f"{OFFTRUNK} off-trunk")
    if lane_count > 1:
        bits.append(f"{LEAF} touched  │ carried")
    if bits:
        emit(con, Text(" ", style=FAINT).append("   ".join(bits), style=FAINT))

    # `show` is offered before `revert` deliberately: the next thing a reader wants is usually "what
    # *is* that?" rather than "remove it". Naming the safe reader beside the destructive one is what
    # makes the consequence reachable before the revert is typed.
    example = next((r["feature"] for r in shown if r.get("feature")), None)
    if example is not None:
        handle = example[2:10] if example.startswith("f-") else example[:8]
        label = labels.get(example)
        target = f'"{label}"' if label and '"' not in label and label != example else handle
        picks = [f"sgt show {target}", f"sgt log --focus {target}", "sgt log"]
        # A name only stays easier to act on than a handle while it FITS: a label long enough to
        # push the suggested command past the terminal wraps the very command the line is teaching,
        # and a wrapped command is one a reader mis-copies. The handle survives that.
        if len(" · ".join(picks)) + 2 > width:
            target = handle
            picks = [f"sgt show {target}", f"sgt log --focus {target}", "sgt log"]
        emit(con, "")
        emit(con, Text(" ", style=FAINT).append(fit("   ·   ".join(picks), width - 2), style=FAINT))
    return to_lines(con, color=color)


def focus_lines(
    feature_id: str,
    name: str,
    cars: list[dict],
    *,
    color: bool = True,
    width: int | None = None,
    max_rows: int | None = None,
) -> list[str]:
    """`sgt log --focus <feature>`: one feature's checkpoints, one row each.

    This is where the chapter detail the map used to cram onto a second line per lane now lives, so
    it has to be worth arriving at. Three things were repeated on every row and are now stated once:
    the feature handle (identical on all of them, and the header names it), the `:slug` (derived
    from the label printed next to it, so the row said the same words twice), and the full `sgt
    restore <handle>@<n>` incantation (twelve copies of one command, differing by an index the row
    already shows). What is left is the chapter's index, its name, its size, and its state.
    """
    width = width or term_width()
    con = console(color=color, width=width)
    hexc = color_for(feature_id)
    handle = feature_id[2:10] if feature_id.startswith("f-") else feature_id[:8]
    n_gone = sum(1 for c in cars if c.get("reverted"))

    # ── Header ───────────────────────────────────────────────────────────────────────────────────
    n_gone_pre = sum(1 for c in cars if c.get("reverted"))
    tail_len = len("  ·  ") * (2 + (1 if n_gone_pre else 0)) + len(handle) + 24
    title = Text(" ")
    title.append(f"{LEAF} ", style=hexc)
    title.append(fit(name, max(12, width - tail_len - 4)), style=HEAD)
    title.append("  ·  ", style=MUTE)
    title.append(handle, style=FAINT)
    title.append("  ·  ", style=MUTE)
    title.append(plural(len(cars), "checkpoint"), style=MUTE)
    if n_gone:
        title.append("  ·  ", style=MUTE)
        title.append(f"{n_gone} reverted", style=MUTE)
    emit(con, title)
    emit(con, "")

    if not cars:
        emit(con, Text(" ", style=MUTE).append(
            "no checkpoints yet — `sgt log --refresh` names them", style=MUTE))
        return to_lines(con, color=color)

    # ── Column geometry ──────────────────────────────────────────────────────────────────────────
    idx_w = max(3, max(len(f"@{c['seg_index']}") for c in cars))
    edits_w = max(len("EDITS"), len(f"{max(c.get('op_count', 0) for c in cars):,}"))

    def when_of(c: dict) -> str:
        """The commits this checkpoint covers, so a row here lines up with a block on the map.

        Without it the two views shared no coordinate: the map placed chapters in time and this list
        placed them in `@n` order, and nothing on the page said which block was which row."""
        lo, hi = c.get("first_index"), c.get("last_index")
        if lo is None:
            return ""
        return f"c{lo}" if hi is None or hi == lo else f"c{lo}–c{hi}"

    when_w = max(len("WHEN"), max((len(when_of(c)) for c in cars), default=0))
    # A STATE column is drawn only when something HAS a state. On a feature nothing has been
    # reverted from -- the common case -- it was a header over eight blank cells, which reads as a
    # column that failed to load rather than as one with nothing to say.
    def state_of(c: dict) -> str:
        gone = (c["op_count"] - c["present_op_count"]
                if c.get("present_op_count") is not None else 0)
        if c.get("reverted"):
            return "reverted"
        if gone:
            return f"{gone} of {c['op_count']} reverted"
        if c.get("is_future"):
            return "not yet reached"
        return ""

    state_w = max((len(state_of(c)) for c in cars), default=0)
    state_w = min(state_w, 22)
    gutter, pad = 1, 2
    # Fit to the longest label rather than to the terminal: chapter names come from commit subjects
    # and top out well short of a wide screen, and stretching the column to fill it just parks the
    # count and the state twenty blank columns from the words they describe.
    tail = pad + when_w + pad + edits_w + ((pad + state_w) if state_w else 0)
    name_w = max(len("CHECKPOINT"), min(max(len(c["label"]) for c in cars),
                                        width - gutter - idx_w - pad - tail))

    head = Text(" " * gutter, style=MUTE)
    head.append("".ljust(idx_w), style=MUTE)
    head.append(" " * pad)
    head.append("CHECKPOINT".ljust(name_w), style=MUTE)
    head.append(" " * pad)
    head.append("WHEN".ljust(when_w), style=MUTE)
    head.append(" " * pad)
    head.append("EDITS".rjust(edits_w), style=MUTE)
    if state_w:
        head.append(" " * pad)
        head.append("STATE", style=MUTE)
    emit(con, head)
    emit(con, rule(gutter + idx_w + pad + name_w + tail, left=" " * gutter))

    # ── Rows ─────────────────────────────────────────────────────────────────────────────────────
    budget = max_rows if max_rows is not None else max(6, term_height() - 12)
    shown = cars[:budget]
    example_live = None
    example_gone = None
    for c in shown:
        gone_n = (c["op_count"] - c["present_op_count"]
                  if c.get("present_op_count") is not None else 0)
        line = Text(" " * gutter)
        line.append(f"@{c['seg_index']}".rjust(idx_w), style=hexc)
        line.append(" " * pad)
        line.append(fit(c["label"], name_w).ljust(name_w),
                    style=MUTE if c.get("reverted") else BODY)
        line.append(" " * pad)
        line.append(when_of(c).ljust(when_w), style=MUTE)
        line.append(" " * pad)
        line.append(f"{c.get('op_count', 0):,}".rjust(edits_w), style=MUTE)
        if state_w:
            line.append(" " * pad)
            line.append(state_of(c), style=FAINT if c.get("is_future") else MUTE)
        if c.get("reverted"):
            example_gone = example_gone or c["seg_index"]
        elif not gone_n and not c.get("is_future"):
            example_live = c["seg_index"]  # the NEWEST live one: the usual place to go back to
        emit(con, line)
        # The chapter in the developer's own words, when they were captured. Never a guessed reason:
        # silence here means nothing was recorded, which is itself worth being able to see.
        words = c.get("words") or []
        for w in words[:3]:
            # The words get the whole rest of the line, not the label column's width: they are
            # sentences, and fitting them to a column sized for short commit subjects cut every one
            # of them at the same arbitrary point.
            emit(con, Text(" " * (gutter + idx_w + pad), style=FAINT).append(
                f"“{fit(w, width - gutter - idx_w - pad - 3)}”", style=FAINT))
        if len(words) > 3:
            emit(con, Text(" " * (gutter + idx_w + pad), style=FAINT).append(
                f"… +{len(words) - 3} more", style=FAINT))

    # ── Footer ───────────────────────────────────────────────────────────────────────────────────
    emit(con, "")
    emit(con, Text(" ", style=MUTE).append_text(
        viewport_footer(len(shown), len(cars), "checkpoint", "--limit for more")))
    # `show` before `revert`, deliberately: it names the checkpoint and states what removing it
    # would cost (including work built on top) WITHOUT doing it, which is the thing a reader wants
    # between "which block was that" and "remove it".
    bits = []
    if example_live is not None:
        bits.append(f"sgt show {handle}@{example_live}")
        bits.append(f"sgt revert {handle}@{example_live}")
    bits.append(f'sgt revert "{name}"' if '"' not in name else f"sgt revert {handle}")
    if example_gone is not None:
        bits.append(f"sgt restore {handle}@{example_gone}")
    emit(con, Text(" ", style=FAINT).append(fit("   ·   ".join(bits), width - 2), style=FAINT))
    return to_lines(con, color=color)


def summary_lines(view: dict, *, color: bool = True, full: bool = False,
                  width: int | None = None) -> list[str]:
    """`sgt log --summary` / `sgt status`: the scalars, then what needs you, then what does not.

    This was a run of bare `print()` calls whose lines each carried a glyph, a count, a sentence,
    and a command, wrapped wherever the terminal chose. Two things were indistinguishable in it: a
    `⚠` that means "you must act" and a `⚠` that means "for your information" (826 untracked files
    and 1 symlink are not problems), and a remedy the reader must fish out of the middle of a
    sentence. Here the sections say which is which and the command sits in its own column.
    """
    width = width or term_width()
    con = console(color=color, width=width)

    head = Text(" ")
    head.append(plural(view.get("files", 0), "file"), style=HEAD)
    head.append("  ·  ", style=MUTE)
    head.append(plural(view.get("symbols", 0), "symbol"), style=HEAD)
    head.append("  ·  ", style=MUTE)
    head.append(plural(view.get("features", 0), "feature"), style=HEAD)
    head.append("  ·  ", style=MUTE)
    head.append(f"{view.get('coverage_fraction', 0) * 100:.0f}% coverage", style=MUTE)
    head.append("  ·  ", style=MUTE)
    ostatus = view.get("oracle", {}).get("status", "unknown")
    head.append(f"oracle {ostatus}",
                style={"pass": OK, "fail": FAIL}.get(ostatus, MUTE))
    emit(con, head)

    cmd_w = 26
    body_w = max(20, width - 5 - cmd_w - 2)

    def item(glyph: str, glyph_style: str, what: str, cmd: str | None, paths=None,
             alt: str | None = None) -> None:
        line = Text(" ")
        line.append(f"{glyph} ", style=glyph_style)
        line.append(fit(what, body_w).ljust(body_w), style=BODY)
        if cmd:
            line.append("  ")
            line.append(fit(cmd, cmd_w), style=FAINT)
        emit(con, line)
        if paths:
            paths = list(paths)
            head_n = len(paths) if full else 4
            # A summary that dumps three hundred paths answers nothing, so the sample is short and
            # says how much it is a sample OF.
            sample = ", ".join(paths[:head_n])
            more = "" if len(paths) <= head_n else f"  +{len(paths) - head_n} more (--full)"
            if full:
                # `--full` exists to answer "which ones, all of them" -- and it did widen the sample
                # to every path, then handed the whole join to `fit`, which clipped it back to one
                # terminal line. So `--full` printed the same truncated line as the default, minus
                # the "+N more" that at least admitted it was a sample: the flag looked answered and
                # was not. Wrap instead of clipping; the default stays a short single line, which is
                # what keeps three hundred paths out of an unasked-for summary.
                import textwrap
                for chunk in textwrap.wrap(sample, max(20, width - 6)) or [""]:
                    emit(con, Text("     ", style=MUTE).append(chunk, style=MUTE))
            else:
                emit(con, Text("     ", style=MUTE).append(fit(sample + more, width - 6), style=MUTE))
        # A state with TWO exits names both. The staged rewrite is the case: `commit` lands the
        # candidate and `unstage` abandons it, and this line is the only place a terminal user
        # learns the state exists at all -- so offering one of the two exits leaves them stuck
        # holding the half of the decision they did not want.
        if alt:
            emit(con, Text("     ", style=FAINT).append(fit(alt, width - 6), style=FAINT))

    # ── What needs you ───────────────────────────────────────────────────────────────────────────
    needs: list = []
    if view.get("sync_status", {}).get("history_rewritten"):
        needs.append(("git history moved backward — the counts above include dropped commits",
                      "sgt advanced resync", None, None))
    elif view.get("drift", {}).get("any"):
        n_drift = len(view["drift"]["paths"])
        needs.append((f"{plural(n_drift, 'file')} {agrees(n_drift, 'differs', 'differ')}"
                      " from the recorded state",
                      "sgt save", view["drift"]["paths"], None))
    if view.get("staged", {}).get("any"):
        n_staged = len(view["staged"]["paths"])
        needs.append((f"{plural(n_staged, 'file')} {agrees(n_staged, 'holds', 'hold')}"
                      " a staged rewrite — edits blocked",
                      "sgt advanced commit", view["staged"]["paths"],
                      "or `sgt advanced unstage` to abandon it"))
    if view.get("backstop_kept"):
        needs.append((f"{plural(len(view['backstop_kept']), 'unreproducible file')} kept on disk",
                      "sgt advanced fsck --tree", view["backstop_kept"], None))
    if needs:
        emit(con, "")
        emit(con, Text(" ", style=WARN).append("NEEDS YOU", style=f"bold {WARN}"))
        for what, cmd, paths, alt in needs:
            item("⚠", WARN, what, cmd, paths, alt=alt)

    # ── What does not ────────────────────────────────────────────────────────────────────────────
    # Untracked files and symlinks used to print with the same `⚠` as work that blocks a verb. They
    # are not problems, nothing can act on them, and 826 of them under a warning glyph teaches a
    # reader that the glyph means nothing.
    fyi: list = []
    if view.get("never_recorded"):
        fyi.append((f"{plural(len(view['never_recorded']), 'file')} sgt does not track, left alone",
                    view["never_recorded"]))
    if view.get("unmanaged"):
        fyi.append((f"{plural(len(view['unmanaged']), 'unmanaged path')} (symlinks, untouched)", None))
    if fyi:
        emit(con, "")
        emit(con, Text(" ", style=MUTE).append("FOR INFORMATION", style=MUTE))
        for what, paths in fyi:
            item("·", MUTE, what, None, paths)

    if not needs and not view.get("forks", {}).get("open"):
        emit(con, "")
        emit(con, Text(" ", style=OK).append("✓ in sync", style=OK))
    return to_lines(con, color=color)


def tree_lines(view: dict, *, color: bool = True, width: int | None = None,
               max_rows: int | None = None) -> list[str]:
    """`sgt log --tree`: the feature tree, no time axis.

    The same `├─ └─ │` guides the map uses, so the two views of one hierarchy read as one view. The
    old renderer indented with bare spaces and appended `(hash) · N symbols` inline, which put the
    one number worth comparing across rows at a different column on every row.

    Husks are dropped, the same rule `graph_layout` applies to the lanes: a feature whose own ops
    touch no real symbol answers `sgt show` with "0 symbols in 0 files" and offers a revert that
    removes nothing, so it is not a row anyone can act on. It came back when this renderer replaced
    the old one, which had the filter -- and the study bundles shipped with it: footfall's tree
    listed ten features where its own map drew nine, and the extra one ("Daily CSV Export") was a
    lane whose symbols live in "Footfall Summary Pages". A subsystem left with no visible descendant
    goes with them. Display-only: the tree and the clustering underneath are untouched.
    """
    width = width or term_width()
    con = console(color=color, width=width)
    by_id = {n["id"]: n for n in view.get("nodes", [])}
    roots = view.get("roots") or [n["id"] for n in view.get("nodes", []) if not n.get("parent")]

    def own(n: dict) -> int:
        """What this feature's own ops touch -- the number `sgt show` reports. `members` is the
        clustering's assignment, which is a different question and disagrees on exactly the rows
        this filter is about."""
        return len(n.get("own_symbols", n.get("members") or ()) or ())

    def visible(node_id: str) -> bool:
        n = by_id.get(node_id)
        if n is None:
            return False
        kids = [k for k in (n.get("children") or []) if k in by_id]
        return any(visible(k) for k in kids) if kids else bool(own(n))

    rows: list[tuple[int, dict, bool]] = []

    def walk(node_id: str, depth: int, last: bool) -> None:
        n = by_id.get(node_id)
        if n is None:
            return
        rows.append((depth, n, last))
        kids = [k for k in (n.get("children") or []) if k in by_id and visible(k)]
        for i, k in enumerate(kids):
            walk(k, depth + 1, i == len(kids) - 1)

    shown_roots = [r for r in roots if visible(r)]
    for i, r in enumerate(shown_roots):
        walk(r, 0, i == len(shown_roots) - 1)

    n_feat = sum(1 for _, n, _ in rows if n.get("kind") != "subsystem")
    def own_n(n: dict) -> int:
        """`own_symbols` is the LIST of symbols a feature owns, not a count."""
        return len(n.get("own_symbols") or ())

    sym_w = max(len("SYMBOLS"), len(f"{max((own_n(n) for _, n, _ in rows), default=0):,}"))
    emit(con, Text(" ").append(plural(n_feat, "feature"), style=HEAD)
         .append("  ·  ", style=MUTE)
         .append(plural(sum(1 for _, n, _ in rows if n.get("kind") == "subsystem"), "subsystem"),
                 style=MUTE))
    emit(con, "")
    # Fit to content: feature names top out well short of a wide terminal, and stretching the column
    # to fill it parks the symbol count ninety blank columns from the name it belongs to.
    natural = max((len(guides(depth, last=last)) + 2 + len(n.get("label") or n["id"])
                   for depth, n, last in rows), default=20)
    name_w = max(20, min(natural, width - 2 - sym_w - 2))
    emit(con, Text(" ", style=MUTE).append("FEATURE".ljust(name_w), style=MUTE)
         .append("  ").append("SYMBOLS".rjust(sym_w), style=MUTE))
    emit(con, rule(1 + name_w + 2 + sym_w, left=" "))

    budget = max_rows if max_rows is not None else max(6, term_height() - 8)
    for depth, n, last in rows[:budget]:
        g = guides(depth, last=last)
        line = Text(" ")
        line.append(g, style=MUTE)
        is_group = n.get("kind") == "subsystem"
        line.append(f"{GROUP if is_group else LEAF} ",
                    style=MUTE if is_group else color_for(n["id"]))
        avail = max(4, name_w - len(g) - 2)
        line.append(fit(n.get("label") or n["id"], avail).ljust(avail),
                    style=HEAD if is_group else BODY)
        line.append("  ")
        # A subsystem owns no symbols of its own; printing 0 there invites the reader to compare it
        # with a feature's count, which is not the same quantity.
        line.append(("" if is_group else f"{own_n(n):,}").rjust(sym_w), style=MUTE)
        emit(con, line)
    emit(con, "")
    emit(con, Text(" ", style=MUTE).append_text(
        viewport_footer(min(len(rows), budget), len(rows), "row", "--limit for more")))
    return to_lines(con, color=color)
