"""Read/inspection verbs (plan U7/U9/U13, collapsed onto the `log` grid in U14): `sgt log` is the
one inspection surface -- the lane×commit grid, with modes `--tree` (the feature tree, formerly
`sgt map`), `--rail` (episode rail, formerly `sgt episodes`), `--summary` (scalars, formerly `sgt
status`), and `--ops` (the raw op DAG). `diff` compares two ideals; `blame` (now under `advanced`)
attributes a file's symbols; `history` is the commit axis; `fsck` checks op-store integrity;
`preview` is a side-effect-free feature-verb preview. Every verb mines the working tree on contact
before reading (R9)."""

from __future__ import annotations

import argparse as _dep
import sys

from ._common import _add_view_flags, _emit_json, _fail


def register(subs, parent) -> None:
    # U14: `status`/`map`/`graph`/`episodes` are no longer top-level verbs -- they are `sgt log`
    # render modes (--summary/--tree/--rail and the default grid). Their handler bodies
    # (`_status`/`_map`/`_graph`/`_episodes`) stay here: `--summary` calls `_status`, `--tree
    # --rebuild` reuses `_map`'s full-recluster path.
    lp = subs.add_parser("log", parents=[parent])
    lmode = lp.add_mutually_exclusive_group()
    # The map is now the bare `sgt log` default; --map stays as a hidden alias. --tree/--summary
    # back the VS Code MapView/StatusView JSON, --saves the spine save-list -- all kept functional
    # but off the help surface so the daily view is just `sgt log` (map) and `sgt log --rail`.
    lmode.add_argument("--map", action="store_true", dest="map", help=_dep.SUPPRESS)
    lmode.add_argument("--rail", action="store_true",
                       help="the save rail: what happened, newest first, one line per save")
    lmode.add_argument("--tree", action="store_true", help=_dep.SUPPRESS)
    lmode.add_argument("--summary", action="store_true", help=_dep.SUPPRESS)
    lmode.add_argument("--saves", action="store_true", help=_dep.SUPPRESS)
    lmode.add_argument("--ops", action="store_true",
                       help=_dep.SUPPRESS)  # relocated to `sgt advanced ops`; kept as a compat alias
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
    # `sgt status` is the first thing anyone arriving from git types. U14 folded it into
    # `log --summary` for surface economy, which meant the single most predictable command in the
    # tool answered "invalid choice". Surface economy is about what a user must *learn*, not about
    # refusing the word they already know, so the spelling is restored as a thin alias onto the
    # same handler -- one verb's worth of muscle memory, zero new concepts.
    st = subs.add_parser("status", parents=[parent],
                         help="what needs attention (alias of `sgt log --summary`)")
    _add_view_flags(st)
    st.add_argument("--no-color", action="store_true", help="plain text, no ANSI color")
    st.set_defaults(func=_cmd_status)
    # `sgt show <spec> [<path>]` -- the read half of "let me look at that version". The bytes were
    # always computable (`fold_view`) and the workbench playhead already displayed them; a terminal
    # simply had no way to ask.
    # `show` is registered once, in `cli/show.py`: it answers both "what is this" and "as it was at",
    # and one verb owning both keeps the mental model to a single idea (see that module). The
    # historical-read renderer stays here (`_show_at`) beside `fold`, which shares its spec grammar.
    op = subs.add_parser("ops", parents=[parent])
    _add_view_flags(op, paged=True)
    op.set_defaults(func=_cmd_ops)
    hp = subs.add_parser("history", parents=[parent])
    _add_view_flags(hp, paged=True)
    hp.set_defaults(func=_cmd_history)
    sp = subs.add_parser("state", parents=[parent])
    _add_view_flags(sp)
    sp.set_defaults(func=_cmd_state)
    cp = subs.add_parser("compose", parents=[parent])
    _add_view_flags(cp)
    cp.set_defaults(func=_cmd_compose)
    nw = subs.add_parser("now", parents=[parent])
    nw.add_argument("--no-color", action="store_true", help="plain text, no ANSI color")
    nw.set_defaults(func=_cmd_now)
    pf = subs.add_parser("fsck", parents=[parent])
    pf.add_argument("--tree", action="store_true",
                    help="compare code(current_ideal) against the HEAD tree (R2)")
    pf.set_defaults(func=_cmd_fsck)
    rs = subs.add_parser("resync", parents=[parent])
    rs.add_argument("--reseed", action="store_true",
                    help="also discard explicit reverts (a total reset to current git history)")
    rs.set_defaults(func=_cmd_resync)
    p = subs.add_parser("diff", parents=[parent])
    p.add_argument("a")
    p.add_argument("b")
    p.set_defaults(func=_cmd_diff)
    p = subs.add_parser("blame", parents=[parent])
    p.add_argument("file", nargs="?", default=None)
    p.add_argument("--all", action="store_true", dest="all_files",
                    help="every covered file at once, in one fold (the repo-wide provenance map)")
    p.set_defaults(func=_cmd_blame)
    p = subs.add_parser("preview", parents=[parent])
    p.add_argument("--to")
    p.add_argument("rest", nargs="*")
    p.set_defaults(func=_cmd_preview)
    p = subs.add_parser("fold", parents=[parent])
    p.add_argument("--at", required=True,
                    help="`now` (the current ideal — what the tree holds right now), a "
                         "commit-index (int), an explicit op-id set (op:<id>,<id>,...), "
                         "or a ref name")
    p.add_argument("--out", default=None, metavar="DIR",
                    help="also materialize the fold onto DIR as raw bytes -- a sync (files that "
                         "left the fold are removed), never a wipe")
    p.set_defaults(func=_cmd_fold)


def _cmd_log(args) -> int:
    """`sgt log` is the daily inspection surface (KTD9). Bare `sgt log` is the feature map (one
    lane per feature, edit-density over time) -- the spatial overview, and `--map` stays as an
    alias for it. `--rail` is the recurring-feature lane rail (a vertical git-log with a ●/◆/○
    topology column tying each save back to the commit graph), the previous bare default. The rest
    are kept functional but off the help surface: `--tree`/`--summary` back the VS Code
    MapView/StatusView JSON, `--saves` is the lane-less spine save-list, `--ops` the op-log.
    `--focus`/`--links`/`--at` are map refinements of the default. `--json` returns the canonical
    view for the mode: `grid_view` (default, `--map`, `--rail`, `--saves` — all rotations of the
    same cells), the feature tree (`--tree`), or the status scalars (`--summary`)."""
    if args.ops:  # compat alias; the listed home is `sgt advanced ops`
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
    # Bare `sgt log` and the `--map` alias both land here: the feature map is the default view
    # (study pilots read the rail as "commit messages again" and never met the lanes; the map is
    # the representation the tool is about, so it is what the bare verb shows).
    return _log_grid(".", as_json=args.as_json, frontier=args.at, color=not args.no_color,
                     refresh=args.refresh, rebuild=args.rebuild, focus=args.focus,
                     links=args.links)


def _cmd_status(args) -> int:
    """`sgt status` -- the same projection `sgt log --summary` renders, reached by the name a git
    user already has. One handler, so the two spellings can never drift."""
    return _status(".", args.as_json, full=args.full, color=not args.no_color)


def _cmd_ops(args) -> int:
    """`sgt advanced ops`: the raw mined op DAG -- kernel plumbing, not a daily view (ops are how
    sgt re-puzzles states internally; the human units are saves, features, symbols, files)."""
    return _log_ops(".", args.as_json, args.full, args.limit, args.offset)


def _cmd_state(args) -> int:
    return _state(".", args.as_json, args.full)


def _cmd_history(args) -> int:
    return _history(".", args.as_json, args.full, args.limit, args.offset)


def _cmd_compose(args) -> int:
    return _compose(".", args.as_json, args.full)


def _cmd_now(args) -> int:
    return _now(".", args.as_json, color=not args.no_color)


def _cmd_fold(args) -> int:
    return _fold(".", args.at, args.as_json, args.out)


def _cmd_fsck(args) -> int:
    if getattr(args, "tree", False):
        return _fsck_tree(".", args.as_json)
    return _fsck(".", args.as_json)


def _cmd_diff(args) -> int:
    return _diff(".", args.a, args.b, args.as_json)


def _cmd_blame(args) -> int:
    if args.all_files:
        return _blame_all(".", args.as_json)
    if args.file is None:
        print("usage: sgt advanced blame [--json] (<file> | --all)")
        return 2
    return _blame(".", args.file, args.as_json)


def _cmd_preview(args) -> int:
    return _preview_verb(".", args.rest, args.to, args.as_json)


def _fsck(repo: str, as_json: bool = False) -> int:
    """Verify the kernel op store's integrity (plan U3): every ``.sgt/ops/<id>`` file's content
    hashes to its own filename. Repair (re-mining) is a follow-up step, not this verb's job.

    Also reports (never reaps -- that's `sgt session gc`'s job, D5's pitfall) any scratch-tree
    session whose owning process has died -- a leaked worktree fsck should surface, not silently
    ignore. This is advisory: a stale session doesn't flip `ok`, since it isn't store corruption."""
    from sgt.core import session as session_mod
    from sgt.core.store import fsck as run_fsck

    report = run_fsck(repo)
    stale = [s.name for s in session_mod.stale_sessions(repo)]
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
                "pending_land": list(report.pending_land),
                "op_index_stale": report.op_index_stale,
                "stale_sessions": stale,
            }
        )
    icon = "✓" if report.ok else "✗"
    print(f"{icon} fsck — {report.checked} op(s) checked")
    for name in report.bad_hash:
        print(f"    bad hash: {name}")
    for name in report.corrupt:
        print(f"    corrupt: {name}")
    for key in report.invalid_ideals:
        print(f"    invalid ideal for {key!r}: names an op the store can't produce -- "
              f"re-mine the ref (`sgt log --refresh`) to rebuild it")
    for key in report.unreachable_witnesses:
        print(f"    unreachable witness for {key!r}: its SHA no longer resolves -- prune the ref "
              f"or re-seed it (`sgt switch` to a live ref)")
    if report.mixed_versions:
        print(f"    mixed miner versions {', '.join(report.mixed_versions)} -- "
              f"run `sgt advanced migrate ops-v3` to unify the store")
    if report.op_index_stale:
        print("    op index stale (advisory): the next read rebuilds it automatically")
    for gap in report.chain_gaps:
        print(f"    chain gap (advisory): {gap} has no producing op "
              f"(off-ref predecessor -- benign unless unexpected)")
    for ref in report.pending_land:
        print(f"    interrupted land on {ref!r} (advisory): a crash left it mid-flight -- "
              f"the next `sgt land` rolls the working tree back to its pre-land snapshot")
    for name in stale:
        print(f"    stale session: {name!r} (owning process died -- `sgt session gc` will reap it)")
    return 0 if report.ok else 1


def _fsck_tree(repo: str, as_json: bool = False) -> int:
    """`sgt fsck --tree` (R2): classify every path where `code(current_ideal)` diverges from the
    HEAD tree. Real `drift` is the only failing finding; unmanaged/backstop-kept/staged/unseeded
    are planned divergence and reported for context."""
    from sgt.core.lens import fsck_tree, get

    get(repo)  # mine-on-contact so the comparison reflects current reality (R9)
    result = fsck_tree(repo)
    if as_json:
        return _emit_json(result)
    drift = result["drift"]
    icon = "✗" if drift else "✓"
    print(f"{icon} fsck --tree — {len(drift)} drifted path(s)")
    for path in drift:
        print(f"    drift: {path} — `sgt log --refresh` to absorb HEAD's bytes, or `sgt save` to "
              f"enforce the ideal (opposite data-loss profiles)")
    for cls, label in (("backstop_kept", "backstop-kept"), ("unmanaged", "unmanaged"),
                       ("staged", "staged candidate"), ("unseeded", "unseeded ref")):
        for path in result[cls]:
            print(f"    {label}: {path}")
    return 0 if not drift else 1


def _cmd_resync(args) -> int:
    return _resync(".", args.reseed, args.as_json)


def _resync(repo: str, reseed: bool, as_json: bool = False) -> int:
    """`sgt advanced resync [--reseed]`: recover after a git history rewrite (reset --hard / amend /
    branch -f) left the current ref's ideal naming ops from dropped commits. Drops just this ref's
    derived local state and re-mines from what HEAD reaches now; `--reseed` also clears explicit
    reverts. The append-only op store is never touched."""
    from sgt.core.lens import resync

    res = resync(repo, reseed=bool(reseed))
    if as_json:
        return _emit_json({"ok": True, **res})
    if res["key"] is None:
        print("✓ resync — no commits yet, nothing to re-derive")
        return 0
    delta = res["after"] - res["before"]
    change = "unchanged" if delta == 0 else (f"+{delta}" if delta > 0 else str(delta))
    print(f"✓ resync {res['key']} — re-derived from current history: "
          f"{res['before']} → {res['after']} op(s) ({change})")
    return 0


def _log_grid(repo: str, *, as_json: bool = False, frontier: int | None = None, color: bool = True,
              refresh: bool = False, rebuild: bool = False, focus: str | None = None,
              links: bool = False) -> int:
    """`sgt log` (the default grid, KTD9): the lane×commit timeline. `--json` returns the canonical
    `grid_view`; the text render reuses the feature-timeline machinery (`render_graph_lines`) over
    the last-built map. A pure cached read by default (fast, glanceable); `--refresh` re-mines and
    rebuilds features + checkpoints first (see `_map_for_view`); `--rebuild` refreshes with a full
    from-scratch recluster."""
    from sgt import state
    from sgt.api import forks_view, grid_view, rewrite_view, segments_view
    from sgt.tui.graph import _state_banner, render_graph_lines, resolve_focus_group, theme_spans

    mv = _map_for_view(repo, refresh, color and not as_json, rebuild=rebuild)
    gv = grid_view(repo)  # the canonical cell join; the text render and --json now read one shape
    if as_json:
        return _emit_json(gv)
    # The loud fork banner (⋔ + per-symbol remedy) leads, exactly as `now`/`status` lead with it:
    # a fork is divergence to resolve, and bare `sgt log` is the view people actually look at.
    # Printed here once, so neither renderer below reports it a second time.
    states = {"forks": forks_view(repo)["forks"], "rewrites": rewrite_view(repo)}
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
    # Spatial LOD: with no focus the default map folds every LEAF subsystem (one whose children are
    # all features) to a single meta-lane, so its features read as one row instead of many. Interior
    # subsystems stay expanded as nested headers -- the map is a single-rooted tree, so collapsing
    # every subsystem would fold the whole codebase into the root's one lane. Under a focus nothing
    # folds: a fold could hide the very lanes the emphasis is trying to show.
    #
    # The root is never collapsed. On a small repo the root IS a leaf subsystem -- every feature
    # hangs directly off it -- so the rule above folded the entire map to a single lane labelled
    # with the root's theme, under a header still counting four features. That is the first thing a
    # participant sees in the warm-up repo, and "4 feature(s)" above one row is worse than no map.
    if focus is None:
        kind = {n["id"]: n.get("kind") for n in mv.get("nodes", [])}
        collapsed = tuple(
            n["id"] for n in mv.get("nodes", [])
            if n.get("kind") == "subsystem"
            and n.get("parent") is not None
            and not any(kind.get(c) == "subsystem" for c in n.get("children") or [])
        )
    else:
        collapsed = ()
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


def _group_chapter_lines(group: dict, segments: list[dict], typed: str, color: bool) -> list[str]:
    """The chapters inside a focused group, tabled under the map: one line per member checkpoint
    (feature-colored dot, feature, `@n` chip name), then -- for a theme -- the two whole-group
    commands, spelled with the exact name the user just typed. This table is where the map's
    emphasis cashes out: the blocks it highlighted, as the handles `revert`/`restore` take."""
    from sgt.tui.color import color_for
    from sgt.tui.graph import _bold, _dim, _paint

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
        lines.append(" " + _bold(f'sgt revert "{name}"', color=color)
                     + _dim("   removes this work everywhere it landed", color=color))
        lines.append(" " + _bold(f'sgt restore "{name}"', color=color)
                     + _dim("  brings it back", color=color))
    return lines


def _log_rail(repo: str, *, as_json: bool = False, color: bool = True, refresh: bool = False,
              rebuild: bool = False) -> int:
    """`sgt log --rail`: the episode rail / vertical git-log --
    "where am I + what I did, in order." A compact state block (from `now_view`) sits atop the rail;
    the rail carries a ●/◆/○ topology column tying each save back to the commit graph. `--json`
    returns `grid_view` (the rail is a time-major rotation of the same cells); the text render
    reuses `render_rail_lines`."""
    from sgt.api import grid_view, rewrite_view
    from sgt.store.gitbind import GitBinding
    from sgt.tui.graph import render_rail_lines

    mv = _map_for_view(repo, refresh, color and not as_json, rebuild=rebuild)
    gv = grid_view(repo)
    if as_json:
        return _emit_json(gv)
    # The state block above already carries the loud fork banner (⋔ + per-symbol remedy); the rail's
    # own footer only adds the pending merge-op drafts, so forks aren't reported twice.
    states = {"forks": [], "rewrites": rewrite_view(repo)}
    topology = GitBinding(repo).graph_topology()
    for line in _state_block_lines(repo, color=color):
        print(line)
    for line in render_rail_lines(mv, gv, color=color, states=states, topology=topology):
        print(line)
    return 0


def _log_default(repo: str, *, as_json: bool = False, color: bool = True, refresh: bool = False,
                 rebuild: bool = False) -> int:
    """Bare `sgt log` (Phase 4 default): a compact state block (from `now_view` -- what's in flight,
    what needs you, the single next action) atop a lane-less per-save list ("where am I + what I
    did"), the legible replacement for the recurring-feature lane wall (now `sgt log --rail`).
    `--json` still returns the canonical `grid_view` (R21/C11), byte-identical to the other modes'."""
    from sgt.api import grid_view
    from sgt.store.gitbind import GitBinding
    from sgt.tui.graph import render_save_list_lines

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
    """The compact state header bare `sgt log` prints above its save list: in-flight + needs-you +
    the one recommended next action, read from `api.now_view` (which mines the working tree on
    contact so in-flight is live, R9). Recently-done is omitted -- the save list below already is
    that. The leading space aligns it under the list rows. Open forks get the loud red `_state_banner`
    (⋔ + per-symbol remedy) atop the block instead of a muted count buried in `needs you` -- a fork
    is divergence you must resolve, not a passing note (still non-blocking; save/switch never refuse)."""
    from sgt.api import now_view

    lines = state_lines(now_view(repo), color=color, indent=" ")
    lines.append("")
    return lines


def state_lines(view: dict, *, color: bool = False, indent: str = "") -> list[str]:
    """The state-of-actions block, rendered once for every surface that shows it.

    `sgt now` and bare `sgt log`'s header are the same answer to the same question, and they were
    two copies that had already drifted apart in wording. One renderer means a developer reads the
    identical sentence wherever they happen to be standing, and a change to how sgt phrases its
    state cannot land in one place and not the other."""
    from sgt.tui.graph import _state_banner

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
    """`sgt log --tree` (the feature tree, no time axis — what `sgt map` shows): a read of the
    last-built tree (`--refresh` rebuilds it first). `--json` returns `map_view`. `--rebuild` is the
    former `sgt map --rebuild`: a full from-scratch recluster (delegates to `_map`)."""
    from sgt.api import map_view

    if rebuild:
        return _map(repo, as_json, rebuild=True, color=color)
    _map_for_view(repo, refresh, not as_json)
    view = map_view(repo)
    if as_json:
        return _emit_json(view)
    # The same `├─ └─ │` guides the map draws, so the two views of one hierarchy read as one view.
    from sgt.tui.views import tree_lines

    for line in tree_lines(view, color=color):
        print(line)
    return 0


def _log_ops(repo: str, as_json: bool = False, full: bool = False,
             limit: int | None = None, offset: int = 0) -> int:
    """`sgt log --ops` (the raw mined op DAG, plan U7). Mine-on-contact first, then project via
    `sgt.api.oplog_view`. Compact by default (`--full` for today's per-op before/after/provenance/
    attribution payload); `limit`/`offset` unset forward nothing, so the view's own default window
    applies (keeping `sgt log --ops --json`'s default byte-identical to `oplog_view(repo)`, R21)."""
    from sgt.api import oplog_view
    from sgt.core.lens import get

    get(repo)  # sync foreign commits into the store before inspecting it
    kwargs = {"offset": offset} if not full else {}
    if limit is not None and not full:
        kwargs["limit"] = limit
    view = oplog_view(repo, full=full, **kwargs)
    if as_json:
        return _emit_json(view)
    if not view["ops"]:
        print("(no ops — nothing mined yet; commit some work then run `sgt advanced ops`)")
        return 0
    note = "" if full else (" (truncated)" if view["truncated"] else "")
    print(f"{view['count']} op(s){note}:")
    for op in view["ops"]:
        syms = ", ".join(f["symbol"] for f in op["footprint"]) if full else ", ".join(op["symbols"])
        print(f"  {op['id'][:12]} [{op['kind']}]: {syms}")
    return 0


def _state(repo: str, as_json: bool = False, full: bool = False) -> int:
    """The current ref's ideal (plan U7): frontier, coverage, entity-granularity fraction.
    Compact by default (`--full` restores the per-symbol `frontier` map and `entity_paths`
    list)."""
    from sgt.api import state_view
    from sgt.core.lens import get
    from sgt.tui.graph import plural

    get(repo)  # mine-on-contact so the ideal reflects current reality
    view = state_view(repo, full=full)
    if as_json:
        return _emit_json(view)
    pct = view["coverage_fraction"] * 100
    frontier_n = len(view["frontier"]) if full else view["frontier_count"]
    entity_n = len(view["entity_paths"]) if full else view["entity_path_count"]
    print(f"{plural(frontier_n, 'symbol')} at the frontier; "
          f"{plural(len(view['covered_paths']), 'path')} covered, "
          f"{entity_n} at entity granularity ({pct:.0f}%)")
    derived = set(view["derived_paths"])
    entity_paths = set(view["entity_paths"]) if full else set()
    for path in view["covered_paths"]:
        tag = " [derived]" if path in derived else ""
        if full:
            mark = "entity" if path in entity_paths else "whole-file"
            print(f"  {path}  ({mark}){tag}")
        else:
            print(f"  {path}{tag}")
    if view["oracle_configured"]:
        from sgt.core.oracle import overall_status

        print(f"  oracle: {overall_status(view['oracle_verdict'])}")
    return 0


def _diff(repo: str, ref_a: str, ref_b: str, as_json: bool = False) -> int:
    """Ideal-vs-ideal semantic diff (plan U7): the symmetric difference grouped by symbol."""
    from sgt.api import ideal_diff_view
    from sgt.core.lens import get

    get(repo)  # sync the current ref; ref_a/ref_b use whatever the store already holds
    view = ideal_diff_view(repo, ref_a, ref_b)
    if as_json:
        return _emit_json(view)
    print(f"{view['count']} differing op(s) between {ref_a} (a) and {ref_b} (b):")
    for sym, sides in view["by_symbol"].items():
        print(f"  {sym}")
        for oid in sides["only_in_a"]:
            print(f"    a: {oid[:12]}")
        for oid in sides["only_in_b"]:
            print(f"    b: {oid[:12]}")
    return 0


def _print_map_tree(view: dict) -> None:
    """Indented `label (handle) · N symbol(s)` tree, DFS from `roots` via each node's `children`.
    The handle is the same minimal-unique prefix every other surface prints (typeable back into
    `revert`/`restore`/`--focus`); internal cluster nodes show no handle -- they aren't operable
    targets. A feature leaf with no live members is a clustering-algorithm artifact (an empty split
    child), not a real feature -- same "drop what has nothing to show" filter `graph_layout` applies
    to lanes with no ops (`sgt/tui/graph.py`) -- so it and any subsystem left with no other visible
    descendant are skipped here. Display-only: the underlying tree/clustering is untouched."""
    from sgt.tui.graph import _min_unique_prefixes, plural

    by_id = {n["id"]: n for n in view["nodes"]}
    leaves = [n["id"] for n in view["nodes"] if not n["children"]]
    prefix_len = _min_unique_prefixes(leaves)

    def live(n: dict) -> list:
        """What this feature's own ops touch -- the same set `sgt show` counts. `members` is the
        clustering's assignment, which prints `Section Waitlist · 5 symbol(s)` for a feature
        `sgt show` reports as `0 symbols in 0 files` and whose revert removes nothing: two
        surfaces disagreeing about one id, with the wrong one on the map a reader picks a revert
        target from."""
        return n.get("own_symbols", n["members"])

    def handle(nid: str) -> str:
        body = nid[2:] if nid.startswith("f-") else nid
        k = max(3, prefix_len.get(nid, 8) - (2 if nid.startswith("f-") else 0))
        return body[:max(k, 8)]

    def is_visible(nid: str) -> bool:
        n = by_id[nid]
        if not n["children"]:
            return bool(live(n))
        return any(is_visible(c) for c in n["children"])

    def visit(nid: str, depth: int) -> None:
        n = by_id[nid]
        if n["children"]:
            print(f"{'  ' * depth}{n['label']}")
        else:
            print(f"{'  ' * depth}{n['label']} ({handle(nid)}) · {plural(len(live(n)), 'symbol')}")
        for child in n["children"]:
            if is_visible(child):
                visit(child, depth + 1)

    for root in view["roots"]:
        if is_visible(root):
            visit(root, 0)
    shown = sum(1 for n in view["nodes"] if not n["children"] and live(n))
    print(plural(shown, "feature"))


def _map(repo: str, as_json: bool = False, rebuild: bool = False, *,
         color: bool = True) -> int:
    """`sgt log --tree` (formerly `sgt map`, plan U13/U14): (re)build the feature tree from the live
    op store -- clustering, Greene identity, pins, labeling -- then print the kernel-backed
    projection (`api.map_view`). `--rebuild` forces a full from-scratch recluster, bypassing
    dirty-subtree splicing (the former `sgt map --rebuild`, now `sgt log --tree --rebuild`)."""
    from sgt.api import map_view
    from sgt.core.lens import get
    from sgt.lens.map import build_map

    get(repo)  # mine-on-contact so the map reflects current reality (R9)
    build_map(repo, rebuild=rebuild)
    view = map_view(repo)
    if as_json:
        return _emit_json(view)
    # The same `├─ └─ │` guides the map draws, so the two views of one hierarchy read as one view.
    from sgt.tui.views import tree_lines

    for line in tree_lines(view, color=color):
        print(line)
    return 0


def _map_for_view(repo: str, refresh: bool, color: bool, rebuild: bool = False) -> dict:
    """The shared read/refresh path behind the `sgt log` grid/rail modes. The daily command is a
    pure read of the *last-built* map (~sub-second) -- NOT a re-mine + re-cluster (which costs ~30s
    and would make a glanceable command unusable). `--refresh` is the *one* command that reflects
    brand-new edits: it re-mines and rebuilds BOTH layers the graph shows -- the feature tree
    (`build_map`) AND the intent checkpoints (`build_segments`/`build_themes`) -- so a user never
    has to run `intent build` then `sgt log --tree --rebuild` then `sgt log` as three separate
    steps. `rebuild=True` (the former `sgt map --rebuild`) forces a full from-scratch recluster and
    implies a refresh. Both label passes have a deterministic offline fallback, so a refresh works
    with no LLM key (just terser names). Returns the `map_view` dict."""
    from sgt.api import map_view

    refresh = refresh or rebuild
    mv = None if refresh else map_view(repo)
    if not (refresh or not (mv and mv.get("nodes"))):
        from sgt.core.lens import cached_map_is_current

        # Only say the view is behind when it actually is, and say what is missing. This line used
        # to print on every read, which made it carry no information: the developer either learns
        # to ignore it (so a genuinely stale view goes unnoticed) or re-runs with `--refresh`
        # reflexively, paying a rebuild to be told nothing changed.
        #
        # `cached_map_is_current` is the authority on *whether* -- it mirrors `_sync`'s own no-op
        # gate, so it agrees with what a re-mine would actually do, and is conservative on any
        # ambiguity. `_map_staleness` only says *what* is missing, which is the part worth reading.
        if not cached_map_is_current(repo):
            line = f" ({_map_staleness(repo) or 'new edits'} not shown yet — `sgt log --refresh`)"
            # stderr, not stdout: a person still reads it, but `--json` stays parseable. It used to
            # print above the `{`, so `sgt log --json | jq` failed on any repo with unmined edits --
            # which is every freshly cloned or copied one, i.e. an agent's first contact.
            print(f"\x1b[2m{line}\x1b[0m" if color else line, file=sys.stderr)
        return mv

    from sgt.core.lens import get
    from sgt.intent.theme import build_themes
    from sgt.intent.theme_segment import build_segments
    from sgt.lens.map import build_map

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
    from sgt.core.lens import current_ideal
    from sgt.lens.tree import load as load_tree
    from sgt.store.gitbind import GitBinding
    from sgt.tui.graph import plural

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


def _blame(repo: str, file: str, as_json: bool = False) -> int:
    """`sgt blame <file>` (plan U13): per-symbol feature attribution (`sym -> max-op-in-I ->
    feature`) for the file's live entities."""
    from sgt.api import blame_view
    from sgt.core.lens import get

    get(repo)
    view = blame_view(repo, file)
    if as_json:
        return _emit_json(view)
    if view.get("error"):
        print(f"✗ {view['error']}")
        return 1
    for span in view["spans"]:
        print(f"  {span['start_line']:>5}-{span['end_line']:<5}  {span['symbol']}"
              f"  [{span['label']} ({span['feature_id']})]")
    return 0


def _blame_all(repo: str, as_json: bool = False) -> int:
    """`sgt advanced blame --all`: the same attribution for every covered file, off one fold
    (`api.blame_all_view`) rather than one fold -- and one subprocess -- per file."""
    from sgt.api import blame_all_view
    from sgt.core.lens import get

    get(repo)
    view = blame_all_view(repo)
    if as_json:
        return _emit_json(view)
    for file in sorted(view["files"]):
        spans = view["files"][file]["spans"]
        print(f"{file}  ({len(spans)} span(s))")
        for span in spans:
            print(f"  {span['start_line']:>5}-{span['end_line']:<5}  {span['symbol']}"
                  f"  [{span['label']} ({span['feature_id']})]")
    return 0


def _status(repo: str, as_json: bool = False, full: bool = False, *, color: bool = False) -> int:
    """`sgt log --summary` (formerly `sgt status`, plan U13/U14): file/symbol/feature counts,
    coverage, oracle status, drift. Path lists are capped at 5 (`--full` for all of them) --
    a summary that dumps three hundred paths answers nothing. Open forks lead with the loud red
    `_state_banner` (⋔ + per-symbol remedy), not a muted `⚠` count -- divergence to resolve."""
    from sgt.api import status_view
    from sgt.core.lens import get
    from sgt.tui.graph import _state_banner, plural

    get(repo)
    view = status_view(repo)
    if as_json:
        return _emit_json(view)

    # The block is `views.summary_lines`: a scalar header, then WHAT NEEDS YOU, then FOR
    # INFORMATION. The split is the point -- 826 untracked files and one symlink used to print under
    # the same `⚠` as a staged rewrite that blocks every materializing verb.
    from sgt.tui.views import summary_lines

    for line in _state_banner({"forks": view["forks"]["records"]}, color=color):
        print(line)
    for line in summary_lines(view, color=color, full=full):
        print(line)
    _print_residual(repo, full)
    return 0


def _needs_you_parts(needs: dict) -> list[str]:
    """The "needs you" summary items, shared by `sgt now` and the save-list header block. One
    implementation because there are two renderers of the same data: when the paused-git-operation
    item was added to only one of them, the other silently kept omitting it."""
    parts: list[str] = []
    if needs.get("history_rewritten"):
        parts.append("git history moved backward — run `sgt advanced resync`")
    if needs.get("paused_operation"):
        # First, and phrased as a blocker rather than a note: while a git merge/cherry-pick/revert is
        # paused the tree holds conflict markers and `sgt save` refuses outright (F26), so every
        # other item here describes work the user cannot actually do yet.
        parts.append(f"a paused git {needs['paused_operation']} is blocking sgt")
    if needs["stalled_plans"]:
        parts.append(f"{len(needs['stalled_plans'])} stalled plan(s)")
    if needs["reviews"]:
        parts.append(f"{len(needs['reviews'])} review(s)")
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
    `predicted_fp`). Folded into `sgt log --summary` as "what needs attention" so unfinished
    intentions resurface here rather than needing a separate queue to groom. Only plan-derived opens
    surface: a chat utterance that failed to align is an alignment miss, not stated-but-never-landed,
    so it stays out of this list (it is the P2 review band, not a to-do). Nothing prints when the
    residual is empty."""
    import time

    from sgt.intent.rationale import open_intents

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


def _history(repo: str, as_json: bool = False, full: bool = False,
             limit: int | None = None, offset: int = 0) -> int:
    """`sgt history [--json]`: every mined commit in chronological order plus every op's derived
    kind/feature/commit-index -- the feature-map webview's Gantt commit-index axis
    (`api.history_view`). Compact by default (`--full` for today's unpaged `{commits, ops}`;
    `limit`/`offset` unset forward nothing, keeping the default byte-identical to
    `history_view(repo)`, R21)."""
    from sgt.api import history_view
    from sgt.core.lens import get

    get(repo)  # mine-on-contact so history reflects current reality (R9)
    kwargs = {"offset": offset} if not full else {}
    if limit is not None and not full:
        kwargs["limit"] = limit
    view = history_view(repo, full=full, **kwargs)
    if as_json:
        return _emit_json(view)
    if full:
        print(f"{len(view['commits'])} commit(s), {len(view['ops'])} op(s) placed on the axis:")
        for c in view["commits"]:
            print(f"  [{c['index']}] {c['sha'][:12]}  {c['subject']}")
    else:
        print(f"{view['commit_count']} commit(s), {view['op_count']} op(s) placed on the axis:")
        for c in view["latest_commits"]:
            print(f"  [{c['index']}] {c['sha'][:12]}  {c['subject']}")
    return 0


def _compose(repo: str, as_json: bool = False, full: bool = False) -> int:
    """`sgt compose [--json]`: one aggregate read (`api.compose_view`) bundling map/history/status/
    forks/plan/drift/sessions/trust + the oracle verdict + open proposals -- the composition
    workbench's single-call refresh, replacing ~9 separate `sgt <verb> --json` invocations.
    `--full` threads into every child view that accepts it; the text rendering below only reads
    fields no child's `full` flag touches, so it needs no branching."""
    from sgt.api import compose_view
    from sgt.core.lens import get
    from sgt.tui.graph import plural

    get(repo)  # mine-on-contact so the bundle reflects current reality (R9)
    view = compose_view(repo, full=full)
    if as_json:
        return _emit_json(view)
    status, oracle = view["status"], view["oracle_verdict"]
    print(f"{plural(status['files'], 'file')}, {plural(status['symbols'], 'symbol')}, "
          f"{plural(status['features'], 'feature')}")
    print(f"  oracle: {status['oracle']['status']}")
    if view["forks"]["open"]:
        print(f"  ⚠ {view['forks']['open']} open fork(s)")
    if status["drift"]["any"]:
        print(f"  ⚠ drift: {', '.join(status['drift']['paths'])}")
    if status["staged"]["any"]:
        # These paths used to be counted as drift, which was the wrong word for them and also the only
        # word: a staged candidate blocks `save`/`switch`/every materializing verb, so a summary that
        # omits it leaves the next refusal unexplained. Named with its two exits for the same reason
        # the refusal names them -- this line is where a reader learns the state exists.
        staged = view["rewrite"]["staged"]
        verb = f" ({staged['verb']})" if staged else ""
        print(f"  ⧗ staged rewrite{verb}: {', '.join(status['staged']['paths'])}")
        print("    `sgt advanced commit` to land it, `sgt advanced unstage` to abandon it")
    if view["sessions"]["sessions"]:
        print(f"  {len(view['sessions']['sessions'])} active session(s)")
    if view["proposals"]:
        print(f"  {len(view['proposals'])} open proposal(s)")
    return 0


def _ago(ts: float | None, *, color: bool = False) -> str:
    """A dim, relative "when" -- ` (4m ago)` -- or nothing when there is no timestamp. Relative,
    because the only thing a developer reads this for is whether it is still the thing they were
    just doing, and a clock time makes them do that subtraction themselves. The wording is
    `_fmt_age`'s, so the two places this file states an age cannot drift into two vocabularies."""
    if not ts:
        return ""
    import time as _t

    from sgt.tui.graph import _dim

    return _dim(f"  ({_fmt_age(max(0.0, _t.time() - ts))})", color=color)


def _now(repo: str, as_json: bool = False, *, color: bool = False) -> int:
    """`sgt now [--json]`: the state-of-actions surface (`api.now_view`) -- what's in flight, what
    needs you, what was recently done, and the single recommended next action. The daily "where am
    I, what next" orient. Mine-on-contact first so the in-flight preview reflects the working tree
    (R9); `--json` returns the canonical view. Open forks lead with the loud red `_state_banner`
    (⋔ + per-symbol remedy) -- a fork is divergence you must resolve, not a muted count (non-blocking)."""
    from sgt.api import now_view
    from sgt.core.lens import get

    get(repo)
    view = now_view(repo)
    if as_json:
        return _emit_json(view)

    # `sgt now` and bare `sgt log`'s header answer the same question, so they share one renderer
    # (`state_lines`) rather than two copies that drift apart in wording. Only the recently-done
    # list is `now`'s own -- `log` prints the save list right below, which is already that.
    lines = state_lines(view, color=color)
    tail = lines.pop()  # the "→ next" line, printed last so recently-done sits above it
    for line in lines:
        print(line)
    if view["recently_done"]:
        print("recently done")
        for c in view["recently_done"]:
            # `headline`, not `subject`: a bare `wip`/`sss` commit message says nothing about what
            # the work was, and this is the surface a developer reads first.
            print(f"    {c['sha'][:8]}  {c.get('headline') or c['subject']}")
    print(tail)
    return 0


def _parse_at(spec: str) -> dict:
    """Parse `sgt fold --at <spec>` into one of `fold_view`'s keyword-only frontier args: `now` is
    the current ideal (the present); an all-digit spec is a commit-index position on
    `history_view`'s axis; an `op:<id>,<id>,...` spec is an explicit op-id set; anything else is a
    ref name.

    `now` is spelled the way `sgt now` already spells the same idea -- "where am I", the present
    state -- so the grammar gains a word the tool has, not a new one. It is not a synonym for
    `HEAD`: a ref folds the ops whose provenance sits in that ref's commit ancestry, which cannot
    see a locally-applied revert (its compensating ops are minted with empty provenance), so after
    `sgt revert <f> --yes` only `now` agrees with the working tree. Anything asking "what does the
    tree look like right now" -- the workbench panel, the render overlay -- wants `now`.

    A branch literally named `now` is shadowed by this, the same way one named `5` is already
    shadowed by the commit-index rung; spell it `refs/heads/now` to reach it."""
    if spec == "now":
        return {"current": True}
    if spec.isdigit():
        return {"at_commit_index": int(spec)}
    if spec.startswith("op:"):
        return {"op_ids": spec[3:].split(",")}
    return {"ref": spec}


def _fold(repo: str, at: str, as_json: bool = False, out: str | None = None) -> int:
    """`sgt fold --at <spec> [--out <dir>] [--json]`: a fold of an arbitrary frontier
    (`api.fold_view`) -- the composition workbench's draggable-playhead primitive. Never checks
    anything out.

    Bare, it is side-effect-free and prints the file list. `--out <dir>` additionally materializes
    the fold onto `dir` as raw bytes (`api.fold_out_view`) -- the render overlay's sync step, which
    also removes the files that left the fold. Same `--at` grammar either way."""
    from sgt.api import fold_view
    from sgt.core.lens import get

    get(repo)  # mine-on-contact so the fold reflects current reality (R9)
    if out is not None:
        return _fold_out(repo, at, out, as_json)
    view = fold_view(repo, **_parse_at(at))
    if as_json:
        return _emit_json(view)
    if view.get("forked"):
        print(f"✗ fold --at {at}: {view['message']}")
        return 1
    if "error" in view:
        return _fail(view["error"])
    from sgt.core.oracle import overall_status

    print(f"code(I) at {at}: {view['op_count']} op(s), {len(view['files'])} file(s)")
    for path in sorted(view["files"]):
        print(f"  {path}")
    print(f"  oracle verdict: {overall_status(view['oracle_verdict'])}")
    return 0


def _fold_out(repo: str, at: str, out: str, as_json: bool) -> int:
    """The `--out <dir>` half of `_fold`: materialize rather than list. Rendered separately from
    the listing branch because it reports what it *did* (wrote/removed), not what exists."""
    from sgt.api import fold_out_view

    view = fold_out_view(repo, out, **_parse_at(at))
    if as_json:
        return _emit_json(view)
    if view.get("forked"):
        print(f"✗ fold --at {at}: {view['message']}")
        return 1
    if "error" in view:
        return _fail(view["error"])
    print(f"code(I) at {at} → {view['path']}: {view['op_count']} op(s), "
          f"{view['written']} file(s) written, {view['deleted']} removed")
    return 0


def _show_at(repo: str, at: str, path: str | None, as_json: bool = False) -> int:
    """`sgt show <path> --at <spec>`: print a file's content as it was at a past point, or, with no
    path, list the files that existed there.

    The bytes were always computable -- `fold_view` reconstructs `code(I)` at any frontier, and the
    workbench's playhead has been reading exactly this. There was simply no way to ask for it from a
    terminal: `sgt fold --at` prints file *names* and never their contents, so "let me look at that
    version" had no answer short of checking a branch out. `git show <rev>:<path>` is the shape
    people already know, so this is that, over sgt's own frontier grammar. Resolution lives in
    `api.show_view`, shared with the MCP tool; this function only renders. Read-only: nothing is
    checked out, and the working tree is untouched."""
    from sgt.api import show_at_view
    from sgt.core.lens import get

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
        print(f"  (add a path to print one: `sgt show <path> --at {at}`)")
        return 0
    print(view["content"], end="" if view["content"].endswith("\n") else "\n")
    return 0


def _preview_verb(repo: str, rest: list[str], to: str | None, as_json: bool = False) -> int:
    """`sgt preview <verb> <args...> [--json]`: a side-effect-free preview of a feature verb or a
    feature-grouped revert/restore (`api.feature_verb_preview_view`) -- the feature-map webview's
    hover-preview primitive. Purely additive: `merge`/`rename`/`move`/`revert`/`restore` themselves
    are untouched and remain the only commands that actually apply one. `split` has no entry here --
    bare `sgt split <feature>` (no `--apply`) already *is* that preview, so a second path to the
    same read would be a duplicate command, not an additive one."""
    usage = ("usage: sgt preview merge <survivor> <absorbed> | "
             'sgt preview rename <feature> "<new label>" | '
             "sgt preview move <op>... --to <feature> | sgt preview revert <feature> | "
             "sgt preview restore <feature> | (for split, use: sgt split <feature>)")
    if not rest or rest[0] not in ("merge", "rename", "move", "revert", "restore"):
        print(usage)
        return 2

    from sgt.api import feature_verb_preview_view
    from sgt.core.lens import get

    get(repo)  # mine-on-contact so the preview reflects current reality (R9)
    verb, opts = rest[0], rest[1:]

    if verb == "merge":
        if len(opts) != 2:
            print(usage)
            return 2
        args: tuple[str, ...] = (opts[0], opts[1])
    elif verb in ("revert", "restore"):
        if len(opts) != 1:
            print(usage)
            return 2
        args = (opts[0],)
    elif verb == "rename":
        if len(opts) < 2:
            print(usage)
            return 2
        args = (opts[0], " ".join(opts[1:]))
    else:  # move
        if not opts or not to:
            print(usage)
            return 2
        args = (*opts, to)

    view = feature_verb_preview_view(repo, verb, *args)
    if as_json:
        return _emit_json(view)
    if "error" in view:
        return _fail(view["error"])
    if not view["ok"]:
        return _fail(view["message"])
    print(f"✓ preview {verb}: affects {', '.join(view['affected_features'])}")
    return 0
