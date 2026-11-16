"""Read/inspection verbs: `ugh log` is the one inspection surface -- the lane×commit grid, with
modes `--tree` (the feature tree), `--rail` (the save rail), `--summary` (scalars, also `ugh
status`), and `--ops` (the raw op DAG). `check` verifies op-store integrity and, with `--repair`,
re-derives the current ref after a git history rewrite. Every verb mines the working tree on contact
before reading (R9)."""

from __future__ import annotations

import argparse as _dep
import sys

from ._common import _add_view_flags, _emit_json, _fail


def register(subs, parent) -> None:
    lp = subs.add_parser("log", parents=[parent])
    lmode = lp.add_mutually_exclusive_group()
    # The map is now the bare `ugh log` default; --map stays as a hidden alias. --tree/--summary
    # back the VS Code MapView/StatusView JSON, --saves the spine save-list -- all kept functional
    # but off the help surface so the daily view is just `ugh log` (map) and `ugh log --rail`.
    lmode.add_argument("--map", action="store_true", dest="map", help=_dep.SUPPRESS)
    lmode.add_argument("--rail", action="store_true",
                       help="the save rail: what happened, newest first, one line per save")
    lmode.add_argument("--tree", action="store_true", help=_dep.SUPPRESS)
    lmode.add_argument("--summary", action="store_true", help=_dep.SUPPRESS)
    lmode.add_argument("--saves", action="store_true", help=_dep.SUPPRESS)
    lmode.add_argument("--ops", action="store_true", help=_dep.SUPPRESS)
    _add_view_flags(lp, paged=True)  # --full/--limit/--offset (used by --ops)
    lp.add_argument("--at", type=int, default=None, metavar="COMMIT",
                    help="map: fold frontier — only ops up to this commit-index count")
    lp.add_argument("--no-color", action="store_true", help="plain text, no ANSI color")
    lp.add_argument("--refresh", action="store_true",
                    help="re-mine + rebuild the map first (default: fast read of the last-built map)")
    lp.add_argument("--rebuild", action="store_true",
                    help="refresh with a full from-scratch recluster")
    lp.add_argument("--focus", default=None, metavar="FEATURE",
                    help="zoom in: one feature (its checkpoints), or a subsystem/theme name "
                         "(its features as a vertical commit-tree). Implies --map")
    lp.add_argument("--links", action="store_true",
                    help="map: show the co-change ↔ annotation trailing each lane")
    lp.set_defaults(func=_cmd_log)
    # `ugh status` is the first thing anyone arriving from git types. U14 folded it into
    # `log --summary` for surface economy, which meant the single most predictable command in the
    # tool answered "invalid choice". Surface economy is about what a user must *learn*, not about
    # refusing the word they already know, so the spelling is restored as a thin alias onto the
    # same handler -- one verb's worth of muscle memory, zero new concepts.
    st = subs.add_parser("status", parents=[parent],
                         help="what needs attention (alias of `ugh log --summary`)")
    _add_view_flags(st)
    st.add_argument("--no-color", action="store_true", help="plain text, no ANSI color")
    st.set_defaults(func=_cmd_status)
    pc = subs.add_parser("check", parents=[parent])
    pc.add_argument("--tree", action="store_true",
                    help="compare code(current_ideal) against the HEAD tree (R2)")
    pc.add_argument("--repair", action="store_true",
                    help="re-derive this ref's record from current git history, after a reset, "
                         "amend or branch -f dropped commits it names")
    pc.add_argument("--reseed", action="store_true",
                    help="with --repair: also discard explicit reverts (a total reset to current "
                         "git history)")
    pc.set_defaults(func=_cmd_check)


def _cmd_log(args) -> int:
    """`ugh log` is the daily inspection surface (KTD9). Bare `ugh log` is the feature map (one
    lane per feature, edit-density over time) -- the spatial overview, and `--map` stays as an
    alias for it. `--rail` is the recurring-feature lane rail (a vertical git-log with a ●/◆/○
    topology column tying each save back to the commit graph), the previous bare default. The rest
    are kept functional but off the help surface: `--tree`/`--summary` back the VS Code
    MapView/StatusView JSON, `--saves` is the lane-less spine save-list, `--ops` the op-log.
    `--focus`/`--links`/`--at` are map refinements of the default. `--json` returns the canonical
    view for the mode: `grid_view` (default, `--map`, `--rail`, `--saves` — all rotations of the
    same cells), the feature tree (`--tree`), or the status scalars (`--summary`)."""
    if args.ops:
        return _log_ops(".", args.as_json, args.full, args.limit, args.offset)
    if args.tree:
        return _log_tree(".", args.as_json, args.refresh, args.rebuild,
                         color=not args.no_color)
    if args.summary:
        return _status(".", args.as_json, full=args.full, color=not args.no_color)
    if args.saves:
        return _log_default(".", as_json=args.as_json, color=not args.no_color,
                            refresh=args.refresh, rebuild=args.rebuild)
    if args.rail:
        return _log_rail(".", as_json=args.as_json, color=not args.no_color,
                         refresh=args.refresh, rebuild=args.rebuild)
    # Bare `ugh log` and the `--map` alias both land here: the feature map is the default view
    # (study pilots read the rail as "commit messages again" and never met the lanes; the map is
    # the representation the tool is about, so it is what the bare verb shows).
    return _log_grid(".", as_json=args.as_json, frontier=args.at, color=not args.no_color,
                     refresh=args.refresh, rebuild=args.rebuild, focus=args.focus,
                     links=args.links)


def _cmd_status(args) -> int:
    """`ugh status` -- the same projection `ugh log --summary` renders, reached by the name a git
    user already has. One handler, so the two spellings can never drift."""
    return _status(".", args.as_json, full=args.full, color=not args.no_color)


def _cmd_check(args) -> int:
    if args.repair:
        return _resync(".", args.reseed, args.as_json)
    if args.tree:
        return _fsck_tree(".", args.as_json)
    return _fsck(".", args.as_json)


def _fsck(repo: str, as_json: bool = False) -> int:
    """Verify the kernel op store's integrity (plan U3): every ``.sgt/ops/<id>`` file's content
    hashes to its own filename. Repair is `--repair`, not this read's job."""
    from ugh.core.store import fsck as run_fsck

    report = run_fsck(repo)
    if as_json:
        return _emit_json(
            {
                "ok": report.ok,
                "checked": report.checked,
                "bad_hash": list(report.bad_hash),
                "corrupt": list(report.corrupt),
                "chain_gaps": list(report.chain_gaps),
                "invalid_ideals": list(report.invalid_ideals),
                "unreachable_witnesses": list(report.unreachable_witnesses),
                "mixed_versions": list(report.mixed_versions),
                "op_index_stale": report.op_index_stale,
            }
        )
    icon = "✓" if report.ok else "✗"
    print(f"{icon} check — {report.checked} op(s) checked")
    for name in report.bad_hash:
        print(f"    bad hash: {name}")
    for name in report.corrupt:
        print(f"    corrupt: {name}")
    for key in report.invalid_ideals:
        print(f"    invalid ideal for {key!r}: names an op the store can't produce -- "
              f"re-mine the ref (`ugh log --refresh`) to rebuild it")
    for key in report.unreachable_witnesses:
        print(f"    unreachable witness for {key!r}: its SHA no longer resolves -- "
              f"`ugh check --repair --reseed` re-seeds it from current history")
    if report.mixed_versions:
        print(f"    mixed miner versions {', '.join(report.mixed_versions)}")
    if report.op_index_stale:
        print("    op index stale (advisory): the next read rebuilds it automatically")
    for gap in report.chain_gaps:
        print(f"    chain gap (advisory): {gap} has no producing op "
              f"(off-ref predecessor -- benign unless unexpected)")
    return 0 if report.ok else 1


def _fsck_tree(repo: str, as_json: bool = False) -> int:
    """`ugh check --tree` (R2): classify every path where `code(current_ideal)` diverges from the
    HEAD tree. Real `drift` is the only failing finding; unmanaged/backstop-kept/unseeded
    are planned divergence and reported for context."""
    from ugh.core.lens import fsck_tree, get

    get(repo)  # mine-on-contact so the comparison reflects current reality (R9)
    result = fsck_tree(repo)
    if as_json:
        return _emit_json(result)
    drift = result["drift"]
    icon = "✗" if drift else "✓"
    print(f"{icon} check --tree — {len(drift)} drifted path(s)")
    for path in drift:
        print(f"    drift: {path} — `ugh log --refresh` to absorb HEAD's bytes, or `ugh save` to "
              f"enforce the ideal (opposite data-loss profiles)")
    for cls, label in (("backstop_kept", "backstop-kept"), ("unmanaged", "unmanaged"),
                       ("unseeded", "unseeded ref")):
        for path in result[cls]:
            print(f"    {label}: {path}")
    return 0 if not drift else 1


def _resync(repo: str, reseed: bool, as_json: bool = False) -> int:
    """`ugh check --repair [--reseed]`: recover after a git history rewrite (reset --hard / amend /
    branch -f) left the current ref's ideal naming ops from dropped commits. Drops just this ref's
    derived local state and re-mines from what HEAD reaches now; `--reseed` also clears explicit
    reverts. The append-only op store is never touched."""
    from ugh.core.lens import resync

    res = resync(repo, reseed=bool(reseed))
    if as_json:
        return _emit_json({"ok": True, **res})
    if res["key"] is None:
        print("✓ resync — no commits yet, nothing to re-derive")
        return 0
    delta = res["after"] - res["before"]
    change = "unchanged" if delta == 0 else (f"+{delta}" if delta > 0 else str(delta))
    # A partial re-derivation is not a successful one. `resync` mines unbounded now, so this
    # should not fire -- and if it ever does, the record is short and the next verb may refuse
    # over files nobody touched, which is unreadable unless this line said so first.
    if not res.get("complete", True):
        print(f"⚠ resync {res['key']} — the walk did not reach the end of history; the record is "
              f"incomplete ({res['after']} op(s)). Re-run it.")
        return 1
    print(f"✓ resync {res['key']} — re-derived from current history: "
          f"{res['before']} → {res['after']} op(s) ({change})")
    return 0


def _log_grid(repo: str, *, as_json: bool = False, frontier: int | None = None, color: bool = True,
              refresh: bool = False, rebuild: bool = False, focus: str | None = None,
              links: bool = False) -> int:
    """`ugh log` (the default grid, KTD9): the lane×commit timeline. `--json` returns the canonical
    `grid_view`; the text render reuses the feature-timeline machinery (`render_graph_lines`) over
    the last-built map. A pure cached read by default (fast, glanceable); `--refresh` re-mines and
    rebuilds features + checkpoints first (see `_map_for_view`); `--rebuild` refreshes with a full
    from-scratch recluster."""
    from ugh import state
    from ugh.api import forks_view, grid_view, segments_view
    from ugh.tui.graph import _state_banner, render_graph_lines, resolve_focus_group, theme_spans

    mv = _map_for_view(repo, refresh, color and not as_json, rebuild=rebuild)
    gv = grid_view(repo)  # the canonical cell join; the text render and --json now read one shape
    if as_json:
        return _emit_json(gv)
    # The loud fork banner (⋔ + per-symbol remedy) leads, exactly as `now`/`status` lead with it:
    # a fork is divergence to resolve, and bare `ugh log` is the view people actually look at.
    # Printed here once, so neither renderer below reports it a second time.
    states = {"forks": forks_view(repo)["forks"]}
    for line in _state_banner(states, color=color):
        print(line)
    themes_raw = state.load_json(repo, "intent_themes", default={}) or {}
    # `--focus <subsystem|theme>` is a category zoom, rendered TableLens-style: the whole map
    # stays on screen, the group's lanes keep full detail, every other lane compresses to its
    # density spark -- then the group's own chapters (the revertable units) are tabled below.
    # A single-feature `--focus` names no group, so it falls through to the map's own emphasized
    # single-lane path inside `render_graph_lines`.
    group = None
    if focus is not None:
        group = resolve_focus_group(focus, mv, gv, themes_raw)
    # Spatial LOD: with no focus the map folds LEAF subsystems (ones whose children are all
    # features) to a single meta-lane each, but only while the map is over its row budget, and it
    # folds the largest first. Under a focus nothing folds, since a fold could hide the very lanes
    # the emphasis is trying to show. The root is never folded: on a small repo the root IS a leaf
    # subsystem, and folding it puts the whole codebase on one lane under a header still counting
    # its features.
    collapsed = _default_collapsed(mv) if focus is None else ()
    segs = segments_view(repo)
    for line in render_graph_lines(
        mv, gv, segs, frontier=frontier, color=color,
        focus=None if group is not None else focus, group=group, show_links=links,
        collapsed=collapsed,
        themes=theme_spans(themes_raw, mv, gv) if focus is None else None,
    ):
        print(line)
    if group is not None:
        for line in _group_chapter_lines(group, segs, focus, color=color):
            print(line)
    return 0


MAP_ROW_BUDGET = 24  # feature lanes plus headers a reader takes in without scrolling


def _default_collapsed(map_view: dict) -> tuple[str, ...]:
    """The leaf subsystems the map folds when nothing is focused, largest first.

    Folding every leaf subsystem by tree shape hid work the reader was looking for: on the
    Sketchpad demo the five hand-named constraint features, one of them named a minute earlier,
    sat behind a single row reading `Geometric Constraints (5)`, and `--full` does not open it
    (finding 81). The fold earns its place on a repository with more features than a screen, so
    it is spent by row budget instead: a map that already fits stays open, and a map that does
    not folds its biggest leaf subsystems until it does.

    A fold trades one subsystem's feature rows for the one meta-lane its header becomes, so the
    largest subsystem buys the most room per fold. Husk features (no `own_symbols`) are not
    counted, because `graph_layout` drops them from the render as well."""
    nodes = {n["id"]: n for n in map_view.get("nodes", [])}
    kind = {nid: n.get("kind") for nid, n in nodes.items()}

    def is_row(nid: str) -> bool:  # the husk filter `graph_layout` applies to leaves
        own = nodes[nid].get("own_symbols")
        return own is None or len(own) > 0

    rows = sum(1 for nid, n in nodes.items()
               if (kind[nid] == "subsystem") or (kind[nid] == "feature" and is_row(nid)))
    leaf_subs = [
        nid for nid, n in nodes.items()
        if kind[nid] == "subsystem" and n.get("parent") is not None
        and not any(kind.get(c) == "subsystem" for c in n.get("children") or [])
    ]
    saved = {nid: sum(1 for c in nodes[nid].get("children") or []
                      if kind.get(c) == "feature" and is_row(c))
             for nid in leaf_subs}
    collapsed: list[str] = []
    for nid in sorted(leaf_subs, key=lambda i: (-saved[i], i)):
        if rows <= MAP_ROW_BUDGET:
            break
        collapsed.append(nid)
        rows -= saved[nid]  # the header row stays, as the meta-lane
    return tuple(collapsed)


def _group_chapter_lines(group: dict, segments: list[dict], typed: str, color: bool) -> list[str]:
    """The chapters inside a focused group, tabled under the map: one line per member checkpoint
    (feature-colored dot, feature, `@n` chip name), then -- for a theme -- the two whole-group
    commands, spelled with the exact name the user just typed. This table is where the map's
    emphasis cashes out: the blocks it highlighted, as the handles `revert`/`restore` take."""
    from ugh.tui.color import color_for
    from ugh.tui.graph import _bold, _dim, _paint

    ids = set(group.get("feature_ids") or ())
    idxs = group.get("commit_indices")
    rows = []
    for s in segments:
        if s.get("feature_id") not in ids:
            continue
        if idxs and not any(s.get("first_index", -1) <= i <= s.get("last_index", -1) for i in idxs):
            continue  # a theme emphasizes only the chapters its commits sit in
        rows.append(s)
    rows.sort(key=lambda s: (s.get("first_index", 0), s.get("feature_label", "")))
    if not rows:
        return []
    lines = ["", _dim(" in this group:", color=color)]
    flab_w = min(30, max(len(s.get("feature_label", "")) for s in rows))
    for s in rows[:20]:
        fid = s["feature_id"]
        dot = _paint(color_for(fid), "●", color=color)
        flab = s.get("feature_label", "")[:flab_w].ljust(flab_w)
        handle = f"{fid[2:10] if fid.startswith('f-') else fid[:8]}@{s['seg_index']}"
        lines.append(f"   {dot} {flab}  @{s['seg_index']} {s.get('intent', '')}  "
                     + _dim(f"({handle})", color=color))
    if len(rows) > 20:
        lines.append(_dim(f"   … and {len(rows) - 20} more", color=color))
    if group.get("kind") == "theme":
        name = typed if typed else group.get("label", "")
        lines.append("")
        lines.append(" " + _bold(f'ugh revert "{name}"', color=color)
                     + _dim("   removes this work everywhere it landed", color=color))
        lines.append(" " + _bold(f'ugh restore "{name}"', color=color)
                     + _dim("  brings it back", color=color))
    return lines


def _log_rail(repo: str, *, as_json: bool = False, color: bool = True, refresh: bool = False,
              rebuild: bool = False) -> int:
    """`ugh log --rail`: the episode rail / vertical git-log --
    "where am I + what I did, in order." A compact state block (from `now_view`) sits atop the rail;
    the rail carries a ●/◆/○ topology column tying each save back to the commit graph. `--json`
    returns `grid_view` (the rail is a time-major rotation of the same cells); the text render
    reuses `render_rail_lines`."""
    from ugh.api import grid_view
    from ugh.store.gitbind import GitBinding
    from ugh.tui.graph import render_rail_lines

    mv = _map_for_view(repo, refresh, color and not as_json, rebuild=rebuild)
    gv = grid_view(repo)
    if as_json:
        return _emit_json(gv)
    topology = GitBinding(repo).graph_topology()
    for line in _state_block_lines(repo, color=color):
        print(line)
    for line in render_rail_lines(mv, gv, color=color, topology=topology):
        print(line)
    return 0


def _log_default(repo: str, *, as_json: bool = False, color: bool = True, refresh: bool = False,
                 rebuild: bool = False) -> int:
    """Bare `ugh log` (Phase 4 default): a compact state block (from `now_view` -- what's in flight,
    what needs you, the single next action) atop a lane-less per-save list ("where am I + what I
    did"), the legible replacement for the recurring-feature lane wall (now `ugh log --rail`).
    `--json` still returns the canonical `grid_view` (R21/C11), byte-identical to the other modes'."""
    from ugh.api import grid_view
    from ugh.store.gitbind import GitBinding
    from ugh.tui.graph import render_save_list_lines

    mv = _map_for_view(repo, refresh, color and not as_json, rebuild=rebuild)
    gv = grid_view(repo)
    if as_json:
        return _emit_json(gv)
    topology = GitBinding(repo).graph_topology()
    for line in _state_block_lines(repo, color=color):
        print(line)
    for line in render_save_list_lines(mv, gv, topology=topology, color=color):
        print(line)
    return 0


def _state_block_lines(repo: str, *, color: bool = False) -> list[str]:
    """The compact state header bare `ugh log` prints above its save list: in-flight + needs-you +
    the one recommended next action, read from `api.now_view` (which mines the working tree on
    contact so in-flight is live, R9). Recently-done is omitted -- the save list below already is
    that. The leading space aligns it under the list rows. Open forks get the loud red `_state_banner`
    (⋔ + per-symbol remedy) atop the block instead of a muted count buried in `needs you` -- a fork
    is divergence you must resolve, not a passing note (still non-blocking; save/switch never refuse)."""
    from ugh.api import now_view

    lines = state_lines(now_view(repo), color=color, indent=" ")
    lines.append("")
    return lines


def state_lines(view: dict, *, color: bool = False, indent: str = "") -> list[str]:
    """The state-of-actions block `ugh log --rail`/`--saves` print above the save list."""
    from ugh.tui.graph import _state_banner

    inflight, needs = view["in_flight"], view["needs_you"]
    action, working = view["next_action"], view.get("working_on")
    lines: list[str] = _state_banner({"forks": needs["forks"]}, color=color)

    if working:
        lines.append(f"{indent}working on  {working['title']}{_ago(working.get('ts'), color=color)}")
    if inflight["total_op_count"]:
        total, fresh = inflight["total_op_count"], inflight["new_work_count"]
        n_feat = len(inflight["affected"])
        where = f" in {n_feat} feature{'s' if n_feat != 1 else ''}" if n_feat else ""
        what = f"{total} new edit(s)" if fresh == total else f"{total} edit(s)"
        if fresh and fresh != total:
            what += f", {fresh} new"
        lines.append(f"{indent}unsaved     {what}{where}")
    for p in view.get("in_progress", []):
        lines.append(f"{indent}            step {p['matched_count'] + 1} of {p['step_count']}")
    activity = (view.get("context") or {}).get("activity") or []
    if activity:
        last = activity[0]
        where = f" {last['file']}" if last.get("file") else ""
        more = f" (+{len(activity) - 1} more)" if len(activity) > 1 else ""
        lines.append(f"{indent}agent       {last['tool']}{where}{more}")
    parts = _needs_you_parts(needs)
    if parts:
        lines.append(f"{indent}needs you   " + " · ".join(parts))
    cmd = f"   ({action['command']})" if action["command"] else ""
    lines.append(f"{indent}→ next      {action['label']}{cmd}")
    return lines


def _log_tree(repo: str, as_json: bool = False, refresh: bool = False, rebuild: bool = False,
              color: bool = True) -> int:
    """`ugh log --tree` (the feature tree, no time axis — what `ugh map` shows): a read of the
    last-built tree (`--refresh` rebuilds it first). `--json` returns `map_view`. `--rebuild` is the
    former `ugh map --rebuild`: a full from-scratch recluster (delegates to `_map`)."""
    from ugh.api import map_view

    if rebuild:
        return _map(repo, as_json, rebuild=True, color=color)
    _map_for_view(repo, refresh, not as_json)
    view = map_view(repo)
    if as_json:
        return _emit_json(view)
    # The same `├─ └─ │` guides the map draws, so the two views of one hierarchy read as one view.
    from ugh.tui.views import tree_lines

    for line in tree_lines(view, color=color):
        print(line)
    return 0


def _log_ops(repo: str, as_json: bool = False, full: bool = False,
             limit: int | None = None, offset: int = 0) -> int:
    """`ugh log --ops` (the raw mined op DAG, plan U7). Mine-on-contact first, then project via
    `ugh.api.oplog_view`. Compact by default (`--full` for today's per-op before/after/provenance/
    attribution payload); `limit`/`offset` unset forward nothing, so the view's own default window
    applies (keeping `ugh log --ops --json`'s default byte-identical to `oplog_view(repo)`, R21)."""
    from ugh.api import oplog_view
    from ugh.core.lens import get

    get(repo)  # sync foreign commits into the store before inspecting it
    kwargs = {"offset": offset} if not full else {}
    if limit is not None and not full:
        kwargs["limit"] = limit
    view = oplog_view(repo, full=full, **kwargs)
    if as_json:
        return _emit_json(view)
    if not view["ops"]:
        print("(no ops — nothing mined yet; commit some work then run `ugh log --ops`)")
        return 0
    note = "" if full else (" (truncated)" if view["truncated"] else "")
    print(f"{view['count']} op(s){note}:")
    for op in view["ops"]:
        syms = ", ".join(f["symbol"] for f in op["footprint"]) if full else ", ".join(op["symbols"])
        print(f"  {op['id'][:12]} [{op['kind']}]: {syms}")
    return 0


def _map(repo: str, as_json: bool = False, rebuild: bool = False, *,
         color: bool = True) -> int:
    """`ugh log --tree` (formerly `ugh map`, plan U13/U14): (re)build the feature tree from the live
    op store -- clustering, Greene identity, pins, labeling -- then print the kernel-backed
    projection (`api.map_view`). `--rebuild` forces a full from-scratch recluster, bypassing
    dirty-subtree splicing (the former `ugh map --rebuild`, now `ugh log --tree --rebuild`)."""
    from ugh.api import map_view
    from ugh.core.lens import get
    from ugh.lens.map import build_map

    get(repo)  # mine-on-contact so the map reflects current reality (R9)
    build_map(repo, rebuild=rebuild)
    view = map_view(repo)
    if as_json:
        return _emit_json(view)
    # The same `├─ └─ │` guides the map draws, so the two views of one hierarchy read as one view.
    from ugh.tui.views import tree_lines

    for line in tree_lines(view, color=color):
        print(line)
    return 0


def _map_for_view(repo: str, refresh: bool, color: bool, rebuild: bool = False) -> dict:
    """The shared read/refresh path behind the `ugh log` grid/rail modes. The daily command is a
    pure read of the *last-built* map (~sub-second) -- NOT a re-mine + re-cluster (which costs ~30s
    and would make a glanceable command unusable). `--refresh` is the *one* command that reflects
    brand-new edits: it re-mines and rebuilds BOTH layers the graph shows -- the feature tree
    (`build_map`) AND the intent checkpoints (`build_segments`/`build_themes`). `rebuild=True`
    forces a full from-scratch recluster and implies a refresh. Both label passes have a deterministic offline fallback, so a refresh works
    with no LLM key (just terser names). Returns the `map_view` dict."""
    from ugh.api import map_view

    refresh = refresh or rebuild
    mv = None if refresh else map_view(repo)
    if not (refresh or not (mv and mv.get("nodes"))):
        from ugh.core.lens import cached_map_is_current

        # Only say the view is behind when it actually is, and say what is missing. This line used
        # to print on every read, which made it carry no information: the developer either learns
        # to ignore it (so a genuinely stale view goes unnoticed) or re-runs with `--refresh`
        # reflexively, paying a rebuild to be told nothing changed.
        #
        # `cached_map_is_current` is the authority on *whether* -- it mirrors `_sync`'s own no-op
        # gate, so it agrees with what a re-mine would actually do, and is conservative on any
        # ambiguity. `_map_staleness` only says *what* is missing, which is the part worth reading.
        if not cached_map_is_current(repo):
            line = f" ({_map_staleness(repo) or 'new edits'} not shown yet — `ugh log --refresh`)"
            # stderr, not stdout: a person still reads it, but `--json` stays parseable. It used to
            # print above the `{`, so `ugh log --json | jq` failed on any repo with unmined edits --
            # which is every freshly cloned or copied one, i.e. an agent's first contact.
            print(f"\x1b[2m{line}\x1b[0m" if color else line, file=sys.stderr)
        return mv

    from ugh.core.lens import get
    from ugh.intent.theme import build_themes
    from ugh.intent.theme_segment import build_segments
    from ugh.lens.map import build_map

    if color:
        print("\x1b[2m refreshing: mining edits + naming features and checkpoints…\x1b[0m",
              file=sys.stderr)
    get(repo)  # mine-on-contact (R9)
    build_map(repo, rebuild=rebuild)  # feature tree + labels (the "what exists" layer)
    build_segments(repo)     # per-feature checkpoints (the "what I did, in chapters" layer)
    build_themes(repo)       # cross-feature rollup (kept for the "one PR spanned N features" view)
    return map_view(repo)


def _map_staleness(repo: str) -> str | None:
    """What the last-built map does not yet reflect, phrased for a person -- the *description* only.
    Whether it is stale at all is `lens.cached_map_is_current`'s call, which mirrors `_sync`'s own
    no-op gate and so agrees with what a re-mine would really do; this just names the two ways it
    falls behind. `None` means "nothing nameable", and the caller falls back to a plain wording
    rather than suppressing a warning the gate asked for. Both checks are cheap reads, so this
    stays affordable on a surface the developer hits constantly."""
    from ugh.core.lens import current_ideal
    from ugh.lens.tree import load as load_tree
    from ugh.store.gitbind import GitBinding
    from ugh.tui.graph import plural

    parts: list[str] = []
    try:
        tree = load_tree(repo)
        known = set((tree or {}).get("op_leaf") or {})
        unmapped = len(current_ideal(repo).op_ids - known)
        if unmapped:
            parts.append(plural(unmapped, "saved edit"))
    except Exception:  # noqa: BLE001 -- an advisory line must never break a read
        pass
    try:
        if GitBinding(repo).has_dirty_source():
            parts.append("unsaved edits")
    except Exception:  # noqa: BLE001
        pass
    return " and ".join(parts) if parts else None


def _status(repo: str, as_json: bool = False, full: bool = False, *, color: bool = False) -> int:
    """`ugh log --summary` (formerly `ugh status`, plan U13/U14): file/symbol/feature counts,
    coverage, oracle status, drift. Path lists are capped at 5 (`--full` for all of them) --
    a summary that dumps three hundred paths answers nothing. Open forks lead with the loud red
    `_state_banner` (⋔ + per-symbol remedy), not a muted `⚠` count -- divergence to resolve."""
    from ugh.api import status_view
    from ugh.core.lens import get
    from ugh.tui.graph import _state_banner, plural

    get(repo)
    view = status_view(repo)
    if as_json:
        return _emit_json(view)

    # The block is `views.summary_lines`: a scalar header, then WHAT NEEDS YOU, then FOR
    # INFORMATION.
    from ugh.tui.views import summary_lines

    for line in _state_banner({"forks": view["forks"]["records"]}, color=color):
        print(line)
    for line in summary_lines(view, color=color, full=full):
        print(line)
    _print_residual(repo, full)
    return 0


def _needs_you_parts(needs: dict) -> list[str]:
    """The "needs you" summary items of the save-list header block."""
    parts: list[str] = []
    if needs.get("history_rewritten"):
        parts.append("git history moved backward — run `ugh check --repair`")
    if needs.get("paused_operation"):
        # First, and phrased as a blocker rather than a note: while a git merge/cherry-pick/revert is
        # paused the tree holds conflict markers and `ugh save` refuses outright (F26), so every
        # other item here describes work the user cannot actually do yet.
        parts.append(f"a paused git {needs['paused_operation']} is blocking ugh")
    if needs["stalled_plans"]:
        parts.append(f"{len(needs['stalled_plans'])} stalled plan(s)")
    return parts


def _fmt_age(seconds: float) -> str:
    """A coarse human age for the residual list -- days / hours / minutes, never a raw timestamp."""
    if seconds >= 86400:
        return f"{int(seconds // 86400)}d ago"
    if seconds >= 3600:
        return f"{int(seconds // 3600)}h ago"
    if seconds >= 60:
        return f"{int(seconds // 60)}m ago"
    return "just now"


def _print_residual(repo: str, full: bool) -> None:
    """The residual (intent-ledger P1): things you *stated* but that never landed -- plan steps
    whose sessions closed with the step still pending (`open_intents`, each carrying a
    `predicted_fp`). Folded into `ugh log --summary` as "what needs attention" so unfinished
    intentions resurface here rather than needing a separate queue to groom. Only plan-derived opens
    surface: a chat utterance that failed to align is an alignment miss, not stated-but-never-landed,
    so it stays out of this list (it is the P2 review band, not a to-do). Nothing prints when the
    residual is empty."""
    import time

    from ugh.intent.rationale import open_intents

    opens = open_intents(repo)
    if not opens:
        return
    now = time.time()
    head = opens if full else opens[:5]
    print(f"  ⚠ {len(opens)} stated but never landed (what needs attention):")
    for r in head:
        age = _fmt_age(max(0.0, now - r.get("ts", now)))
        print(f"      · {r['reason'] or '(unknown)'}  ({age})")
    if not full and len(opens) > 5:
        print(f"      (+{len(opens) - 5} more — --full lists them)")


def _ago(ts: float | None, *, color: bool = False) -> str:
    """A dim, relative "when" -- ` (4m ago)` -- or nothing when there is no timestamp. Relative,
    because the only thing a developer reads this for is whether it is still the thing they were
    just doing, and a clock time makes them do that subtraction themselves. The wording is
    `_fmt_age`'s, so the two places this file states an age cannot drift into two vocabularies."""
    if not ts:
        return ""
    import time as _t

    from ugh.tui.graph import _dim

    return _dim(f"  ({_fmt_age(max(0.0, _t.time() - ts))})", color=color)


def _show_at(repo: str, at: str, path: str | None, as_json: bool = False) -> int:
    """`ugh show <path> --at <spec>`: print a file's content as it was at a past point, or, with no
    path, list the files that existed there.

    The bytes were always computable -- `fold_view` reconstructs `code(I)` at any frontier, and the
    workbench's playhead has been reading exactly this. There was simply no way to ask for it from a
    terminal: `ugh fold --at` prints file *names* and never their contents, so "let me look at that
    version" had no answer short of checking a branch out. `git show <rev>:<path>` is the shape
    people already know, so this is that, over ugh's own frontier grammar. Resolution lives in
    `api.show_view`, shared with the MCP tool; this function only renders. Read-only: nothing is
    checked out, and the working tree is untouched."""
    from ugh.api import show_at_view
    from ugh.core.lens import get

    get(repo)  # mine-on-contact so the fold reflects current reality (R9)
    view = show_at_view(repo, at, path)
    if "error" in view:
        return _fail(view["error"])
    if as_json:
        return _emit_json(view)
    if "content" not in view:
        print(f"{len(view['files'])} file(s) at {at}:")
        for p in view["files"]:
            print(f"  {p}")
        print(f"  (add a path to print one: `ugh show <path> --at {at}`)")
        return 0
    print(view["content"], end="" if view["content"].endswith("\n") else "\n")
    return 0
