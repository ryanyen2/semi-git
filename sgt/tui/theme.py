"""The one visual language every `sgt log` mode is drawn in.

Before this module each renderer in `graph.py` invented its own spacing, its own truncation, and its
own idea of where a column starts, so the six modes of one command did not look like one command.
The rules below are the whole design; a renderer that wants to look different is a renderer that is
wrong.

**Structure is position, identity is hue, everything else is weight.** A row's meaning comes from
which column a thing sits in. The one colour a row may carry is its feature's identity hue (from
`color_for`, shared byte-for-byte with the editor). Status -- reverted, future, selected -- is a
glyph or a dim, never a second hue, because two colour channels in one view means neither reads.

**Four weights, no more.** `HEAD` for the one line that names the screen, `BODY` for the words a
reader is here for, `MUTE` for scaffolding they navigate by (guides, units, counts), `FAINT` for
what is only there to be available (hashes, hints). A fifth weight is a decision nobody can see.

**One line per row.** The old map spent a second line per lane on `@n` chips at a fixed indent that
matched nothing above it, which broke the column grid on every row and halved how much history fits
on screen. Detail belongs in `--focus`, not in a sub-line.

**Nothing is drawn that the viewport cannot hold.** Every list renderer ends in `viewport_footer`,
which states what was shown out of what exists and the flag that shows more. A view that floods the
scrollback at 10,000 commits is a view that has not been designed for the repository it will
actually meet.
"""

from __future__ import annotations

import io
import os
import shutil

from rich.console import Console
from rich.text import Text

# ── Weights ──────────────────────────────────────────────────────────────────────────────────────
# Named, not spelled inline, so "make the scaffolding quieter" is one edit rather than a grep.
HEAD = "bold"
BODY = ""
MUTE = "dim"
FAINT = "dim italic"

# Semantic accents. These are the ONLY non-identity colours; each answers a question hue cannot:
# what is broken, what is pending, what has landed.
WARN = "yellow"
FAIL = "red"
OK = "green"

# ── Glyphs ───────────────────────────────────────────────────────────────────────────────────────
# One glyph per concept, used identically in every mode. `graph.py` had ●/◆/○ meaning topology in
# the rail and ●/◈ meaning lane-kind in the map -- the same disc, two vocabularies, one screen.
LEAF = "●"        # a feature: the thing verbs take
GROUP = "▾"       # an OPEN subsystem: its features are the rows beneath it
FOLD = "▸"        # a FOLDED subsystem: one row standing for the features inside it
MERGE = "◆"       # a merge commit
OFFTRUNK = "○"    # a save off the trunk
GHOST = "◇"       # planned, not built
SEL = "▸"         # the selected row
SPARK = " ▁▂▃▄▅▆▇█"  # index 0 is a space on purpose: "quiet" is absence of ink, not a short bar

# Tree guides, for hierarchy that reads as containment instead of as an accident of leading spaces.
GUIDE_MID, GUIDE_END, GUIDE_BAR, GUIDE_PAD = "├─ ", "└─ ", "│  ", "   "

# ── Geometry ─────────────────────────────────────────────────────────────────────────────────────
NAME_FRAC = 0.42   # the name column never takes more than this share of the terminal...
NAME_MIN = 18      # ...and never less than this, or the words stop being words
BAR_MIN = 20       # below this the shared time axis stops meaning anything, so we stop drawing it
BAR_CELL_MAX = 4   # a single commit never gets a cell wider than this; past it the axis just ends
FALLBACK_COLS = 100  # a pipe or a test has no terminal; pick one width and be reproducible


def term_width(default: int = FALLBACK_COLS) -> int:
    """The terminal's width, or a fixed fallback when there isn't one.

    `shutil.get_terminal_size` answers 80 for a pipe, which is indistinguishable from a real 80-col
    terminal -- so a renderer could not tell "narrow" from "no terminal" and the old code passed
    `fallback=(0, 0)` and then branched on `>= 40` in four separate places, each with its own idea
    of what to do below the threshold. Asking here once, and answering with a width that is always
    usable, deletes that branch from every caller."""
    cols = shutil.get_terminal_size(fallback=(0, 0)).columns
    return cols if cols >= 40 else default


def term_height(default: int = 10 ** 6) -> int:
    """Rows available for a list body, or effectively unbounded when there is no terminal.

    The viewport fills the terminal exactly rather than a hardcoded 40, so the same command is one
    screen on every screen. But a pipe has no height, and answering a guessed 24 there would
    truncate `sgt log > file` and `sgt log | grep` to a screenful of a screen nobody is looking at
    -- the redirect is the case where the user most clearly wants all of it."""
    if not os.isatty(1):
        return default
    rows = shutil.get_terminal_size(fallback=(0, 0)).lines
    return rows if rows >= 10 else 24


def console(*, color: bool, width: int | None = None) -> Console:
    """A recording console every renderer draws into.

    `record=True` + `export_text(styles=color)` is what lets the redesign use rich's layout engine
    while every renderer keeps its `-> list[str]` contract, so the CLI's paging, `--json`, and the
    existing golden tests all keep working untouched. `force_terminal` is what makes rich emit ANSI
    into a pipe at all -- without it `sgt log | less -R` would come out plain."""
    return Console(
        # A recording console still WRITES unless it is given somewhere else to write. Left on the
        # real stdout it printed every view twice: once as it drew, once as the CLI printed the
        # lines it exported.
        file=io.StringIO(),
        width=width or term_width(),
        record=True,
        force_terminal=color,
        no_color=not color,
        highlight=False,       # rich guessing that `c341` is a number and colouring it is noise
        soft_wrap=False,       # a row that wraps has broken the column grid; truncate instead
        legacy_windows=False,
    )


def emit(con: Console, text) -> None:
    """Print one row, cropping rather than wrapping.

    This is the structural guarantee behind "a row that wraps has broken the column grid": a row
    that outgrows the console must lose its tail, not fold onto a second line where its columns line
    up with nothing. `soft_wrap=False` alone does not do this -- rich still word-wraps -- so every
    renderer prints through here."""
    con.print(text, no_wrap=True, overflow="ellipsis", crop=True)


def to_lines(con: Console, *, color: bool) -> list[str]:
    """Drain a console to the `list[str]` every `render_*_lines` returns.

    Trailing whitespace is stripped per line because rich pads every row out to the console width;
    left in, a `sgt log` piped to a file is a third spaces, and a golden test diff is unreadable."""
    text = con.export_text(styles=color)
    return [ln.rstrip() for ln in text.split("\n")]


def fit(s: str, width: int) -> str:
    """Cut `s` to `width` columns, ending in `…` when it had to cut.

    A one-character budget yields `…` rather than an empty string: a name that is present but did
    not fit must still leave a mark, or the row reads as having no name at all."""
    s = str(s)
    if width <= 0:
        return ""
    if len(s) <= width:
        return s
    return s[: width - 1] + "…" if width > 1 else "…"


def guides(depth: int, *, last: bool = False) -> str:
    """The `├─ / └─ / │` prefix for a row at `depth`, for a caller that already knows whether the
    row is the last of its level (`tree_lines`, walking a real tree). The map's rows arrive as a
    flat depth sequence instead, so it derives `last` itself in `views._guide_prefix` and calls the
    same glyphs from there.

    Hierarchy drawn as indentation alone is ambiguous the moment a row is taller than the screen --
    you scroll past the parent and the indent stops meaning anything. Guides carry the containment
    with the row."""
    if depth <= 0:
        return ""
    return GUIDE_BAR * (depth - 1) + (GUIDE_END if last else GUIDE_MID)


def spark_bar(
    per_commit: dict[int, int],
    *,
    axis_len: int,
    width: int,
    scale_max: int,
    hexc: str | None = None,
    faint_at: set[int] | None = None,
    color: bool = True,
) -> Text:
    """One lane's edit density on the SHARED commit axis.

    Column `c` covers commit indices `[c*axis/width, (c+1)*axis/width)`, identical in every row, so
    a vertical slice down the page is one moment in time. Height is scaled against `scale_max` --
    the busiest column anywhere in the view, not in this lane -- so a quiet lane's single edit does
    not draw as tall as a busy lane's twenty.

    A commit fills its WHOLE cell. Marking only the column its index maps to drew confetti whenever
    the terminal was wider than the history was long, and got worse the wider the terminal got,
    which is the tell that the encoding rather than the size was wrong.
    """
    if width <= 0:
        return Text("")
    buckets = [0] * width
    faint = [False] * width
    faint_at = faint_at or set()
    for ci, cnt in per_commit.items():
        lo = min(width - 1, max(0, ci * width // max(1, axis_len)))
        hi = min(width, max(lo + 1, (ci + 1) * width // max(1, axis_len)))
        for col in range(lo, hi):
            buckets[col] += cnt
            if ci in faint_at:
                faint[col] = True
    gmax = max(1, scale_max)
    out = Text()
    for col, n in enumerate(buckets):
        if n <= 0:
            out.append(" ")
            continue
        # Square-root, not linear. Edit counts per commit are heavy-tailed -- one refactor commit
        # touching 80 symbols sets `gmax`, and under a linear map every other row in the repository
        # then renders as the same floor glyph. The bar stops distinguishing "quiet" from "steady"
        # exactly where a reader most needs it to. sqrt spends the glyph range on the crowded low
        # end while still ranking the tall column highest.
        frac = min(1.0, n / gmax) ** 0.5
        ch = SPARK[max(1, min(len(SPARK) - 1, round(frac * (len(SPARK) - 1))))]
        if faint[col]:
            out.append(ch, style=MUTE)
        elif color and hexc:
            # Intensity rides on the glyph height; the hue stays constant so the row keeps ONE
            # identity. Shading the hue too would make a busy lane and a quiet lane look like two
            # different features.
            out.append(ch, style=hexc)
        else:
            out.append(ch)
    return out


def axis_header(axis_len: int, width: int, *, left: str = "", style: str = MUTE) -> Text:
    """The `c0 ─────── c441` ruler that sits directly above the bars.

    The old map printed tick labels nowhere and a legend far below explaining that columns meant
    time. A ruler in the column it rules needs no legend."""
    lo, hi = f"c0 ", f" c{max(0, axis_len - 1)}"
    room = max(0, width - len(lo) - len(hi))
    line = Text(left, style=style)
    if room <= 0:  # too narrow for a labelled ruler; a bare rule still marks the column
        line.append("─" * max(0, width), style=style)
        return line
    line.append(lo, style=style)
    line.append("─" * room, style=style)
    line.append(hi, style=style)
    return line


def rule(width: int, *, left: str = "") -> Text:
    """The single horizontal rule under a header row. One rule, not a box: a full border around a
    table draws four lines to separate two things, and the eye only ever needed one."""
    return Text(left + "─" * max(0, width), style=MUTE)


def viewport_footer(shown: int, total: int, noun: str, more_flag: str) -> Text:
    """What was shown out of what exists, and how to see the rest. `noun` is SINGULAR -- the count
    decides the ending, so a one-row repository does not read `1 checkpoints`.

    Every list mode ends in this line. A view that silently truncates is a view that lies about the
    size of the repository, and at 10,000 commits that is the difference between a tool you trust
    and one you double-check with git."""
    if shown >= total:
        return Text(plural(total, noun), style=MUTE)
    t = Text(style=MUTE)
    t.append(f"{shown:,} of {plural(total, noun)}")
    t.append("  ·  ")
    t.append(more_flag, style=FAINT)
    return t


def agrees(n: int, singular: str, plural_form: str) -> str:
    """The verb that agrees with a count: `1 file differs`, `343 files differ`.

    `plural` inflects the noun; nothing inflected the verb, so every sentence built from a count
    read correctly at one end of the range and wrong at the other. A summary is mostly counted
    sentences, so this is most of its sentences."""
    return singular if n == 1 else plural_form


def plural(n: int, noun: str) -> str:
    """`3 symbols`, `1 symbol`. Not `1 symbol(s)` -- `(s)` is a placeholder for a decision nobody
    made, and it was on the first line a new reader met."""
    return f"{n:,} {noun}" if n == 1 else f"{n:,} {noun}s"


def car_strip(
    cars: list[dict],
    *,
    axis_len: int,
    width: int,
    hexc: str,
    color: bool = True,
    name_of=None,
    spill_last: bool = True,
) -> Text:
    """A feature's CHECKPOINTS as blocks on the shared commit axis — the terminal's Gantt strip.

    The density bar this replaces drew a feature's life as one unbroken run of `▁▂▃▄▅▆▇█`. It reads
    beautifully and answers nothing you can act on: `the console` was a sixty-column smear meaning
    "worked on, throughout", when the question a reader brings to this view is *which point can I go
    back to, and what was it*. Every block here is one `@n` — the exact unit `sgt revert` and `sgt
    restore` take — positioned at the commits it actually covers, so its left edge is when that
    chapter started and its width is how long it ran.

    Each block carries its own `@n` as soon as it is wide enough to hold it, and its name too when
    there is room, so the reader gets the handle they would type without opening anything. A block
    too narrow for either is still a block; the seam beside it is what says a boundary is there.

    The block is drawn as a filled background in the feature's hue with the label written ON it —
    a Gantt bar, not a label floating in space. Writing the label into a bar of `▄` glyphs instead
    made a labelled block vanish: the text replaced the very run of ink that was the block. Without
    colour there is no background to fill, so the fallback is a `▏` seam plus `▄` body, which keeps
    the extent visible in a pipe, a capture, or `--no-color`.
    """
    if width <= 0 or not cars:
        return Text(" " * max(0, width))
    cells: list[dict | None] = [None] * width
    axis = max(1, axis_len)
    for car in cars:
        lo = min(width - 1, max(0, car["first_index"] * width // axis))
        hi = min(width, max(lo + 1, (car["last_index"] + 1) * width // axis))
        # Adjacent chapters need a visible seam or they read as one long block, which is exactly the
        # misreading this strip exists to fix. The later chapter yields the column, so a chapter's
        # LEFT edge -- when it began, the thing a reader lines up against the axis -- stays true.
        while lo < hi - 1 and cells[lo] is not None:
            lo += 1
        for col in range(lo, hi):
            if cells[col] is None:
                cells[col] = car
    out = Text()
    col = 0
    while col < width:
        car = cells[col]
        if car is None:
            out.append(" ")
            col += 1
            continue
        run = col
        while run < width and cells[run] is car:
            run += 1
        span = run - col
        # One column of the block is given back as a seam whenever another block starts right after
        # it; without it two chapters that meet read as one, which is the whole misreading here.
        seam = 1 if run < width and cells[run] is not None else 0
        body = max(1, span - seam)
        # A chapter may write its NAME into the empty time that follows it -- never into anyone
        # else's. Blocks are only a few columns wide on any real axis, so a name almost never fits
        # inside one, and `@6` alone does not answer "what was that". The newest chapter usually has
        # the most empty space to its right, which is why this names the recent ones first without
        # having to single them out. (The workbench spells the same rule `gcar-tag-inrow`.)
        free = 0
        while run + free < width and cells[run + free] is None:
            free += 1
        label = _car_label(car, body, name_of)
        spill = ""
        # The newest chapter is skipped when a caller names it in a column of its own; spelling it
        # both inline and there spends the strip's scarcest columns saying a thing already said.
        last_car = car is cars[-1]
        if free > 1 and (spill_last or not last_car) and (not label or label == f"@{car['seg_index']}"):
            name = (name_of(car) if name_of else car.get("label")) or ""
            if name and len(name) + 1 <= free - 1:
                spill = " " + name
        out.append_text(_car_block(car, label, body, hexc, color=color))
        if seam:
            out.append(" ")
        if spill:
            # The hue, not a dim grey: the name has to read as belonging to the block beside it.
            # Dimmed, it detached into anonymous light text floating between two coloured bars, and
            # a reader had to work out which of the two it named.
            out.append(spill, style=(MUTE if not color else hexc))
            for k in range(len(spill)):
                cells[run + seam + k] = car  # claim the columns so nothing else writes over them
            col = run + len(spill)
            continue
        col = run
    return out


def _car_block(car: dict, label: str, span: int, hexc: str, *, color: bool) -> Text:
    """One chapter, drawn to `span` columns: a filled bar carrying its own label."""
    faded = bool(car.get("is_future") or car.get("reverted"))
    if not color:
        # No background to fill, so the body is a run of `▄` and the label displaces only the run
        # it needs -- padded with the block glyph, never with spaces, or a labelled block becomes a
        # label floating in space and the chapter's extent disappears.
        #
        # There is no leading `▏` edge marker: the seam column reserved above is already a blank
        # between adjacent blocks, so the glyph separated nothing and cost the one column a short
        # chapter has. On a 444-commit axis every chapter is a single column, and spending it on an
        # edge marker drew a row of bare `▏` with no blocks between them.
        text = label.ljust(span, "▄")[:span]
        return Text(text, style=MUTE if faded else BODY)
    if faded:
        # Retired or not-yet-reached: an outline, never a filled bar. Status is a shape here, not a
        # second hue -- one hue per row is what lets the column be scanned down the page.
        return Text((label or "").ljust(span)[:span], style=f"{hexc} dim")
    return Text((label or "").ljust(span)[:span], style=f"black on {hexc}")


def _car_label(car: dict, span: int, name_of) -> str:
    """`@n`, or `@n Name` when the block is wide enough to hold the whole name.

    A half-name is worse than none here: `@6 Maste…` and `@6 Master…` are the same block to a reader,
    and the `@n` alone is already the token they would type."""
    tag = f"@{car['seg_index']}"
    if span < len(tag):
        return ""
    name = (name_of(car) if name_of else car.get("label")) or ""
    if name and span >= len(tag) + 1 + len(name):
        return f"{tag} {name}"
    return tag


def _car_style(car: dict, hexc: str, shade: float, *, color: bool) -> str:
    """Identity hue, dimmed for a chapter that is retired or not yet reached. Status is never a
    second colour -- one hue per row is what lets a column be scanned down the page."""
    if car.get("is_future") or car.get("reverted"):
        return MUTE
    if not color:
        return BODY
    return hexc if shade >= 0.55 else f"{hexc} dim"
