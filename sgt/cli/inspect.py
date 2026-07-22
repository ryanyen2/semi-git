"""Read/inspection verbs (plan U7/U9/U13): `log`/`state`/`diff` over the op DAG, the current
ideal, and ideal-vs-ideal diffs; `map`/`blame`/`status` over the feature tree; `history` for the
commit axis; `fsck` for op-store integrity; `preview` for side-effect-free feature-verb previews.
Every verb mines the working tree on contact before reading (R9)."""

from __future__ import annotations

from ._common import _add_view_flags, _emit_json, _fail


def register(subs, parent) -> None:
    subs.add_parser("status", parents=[parent]).set_defaults(func=_cmd_status)
    mp = subs.add_parser("map", parents=[parent])
    mp.add_argument("--rebuild", action="store_true",
                     help="force a full from-scratch recluster instead of splicing unchanged subtrees")
    mp.set_defaults(func=_cmd_map)
    gp = subs.add_parser("graph", parents=[parent])
    gp.add_argument("--at", type=int, default=None, metavar="COMMIT",
                    help="fold frontier: only ops up to this commit-index count (features accrete)")
    gp.add_argument("--no-color", action="store_true", help="plain text, no ANSI color")
    gp.add_argument("--refresh", action="store_true",
                    help="re-mine + rebuild the map first (default: fast read of the last-built map)")
    gp.add_argument("--focus", default=None, metavar="FEATURE",
                    help="one feature, full width, one detail line per checkpoint car")
    gp.add_argument("--links", action="store_true",
                    help="show the co-change ↔ annotation trailing each lane (off by default)")
    gp.set_defaults(func=_cmd_graph)
    ep = subs.add_parser("episodes", parents=[parent])
    ep.add_argument("--no-color", action="store_true", help="plain text, no ANSI color")
    ep.add_argument("--refresh", action="store_true",
                    help="re-mine + rebuild the map first (default: fast read of the last-built map)")
    ep.set_defaults(func=_cmd_episodes)
    lp = subs.add_parser("log", parents=[parent])
    lmode = lp.add_mutually_exclusive_group()
    lmode.add_argument("--ops", action="store_true",
                       help="the raw mined op DAG (the pre-grid `sgt log`)")
    lmode.add_argument("--tree", action="store_true",
                       help="the feature tree, no time axis (what `sgt map` shows)")
    lmode.add_argument("--rail", action="store_true",
                       help="the episode rail / vertical git-log (what `sgt episodes` shows)")
    lmode.add_argument("--summary", action="store_true",
                       help="file/symbol/feature/coverage/oracle/drift scalars (what `sgt status` shows)")
    _add_view_flags(lp, paged=True)  # --full/--limit/--offset (used by --ops)
    lp.add_argument("--at", type=int, default=None, metavar="COMMIT",
                    help="grid: fold frontier — only ops up to this commit-index count")
    lp.add_argument("--no-color", action="store_true", help="grid/rail: plain text, no ANSI color")
    lp.add_argument("--refresh", action="store_true",
                    help="re-mine + rebuild the map first (default: fast read of the last-built map)")
    lp.add_argument("--focus", default=None, metavar="FEATURE",
                    help="grid: one feature, full width, one detail line per checkpoint car")
    lp.add_argument("--links", action="store_true",
                    help="grid: show the co-change ↔ annotation trailing each lane")
    lp.set_defaults(func=_cmd_log)
    hp = subs.add_parser("history", parents=[parent])
    _add_view_flags(hp, paged=True)
    hp.set_defaults(func=_cmd_history)
    sp = subs.add_parser("state", parents=[parent])
    _add_view_flags(sp)
    sp.set_defaults(func=_cmd_state)
    cp = subs.add_parser("compose", parents=[parent])
    _add_view_flags(cp)
    cp.set_defaults(func=_cmd_compose)
    pf = subs.add_parser("fsck", parents=[parent])
    pf.add_argument("--tree", action="store_true",
                    help="compare code(current_ideal) against the HEAD tree (R2)")
    pf.set_defaults(func=_cmd_fsck)
    subs.add_parser("reindex", parents=[parent]).set_defaults(func=_cmd_reindex)
    p = subs.add_parser("diff", parents=[parent])
    p.add_argument("a")
    p.add_argument("b")
    p.set_defaults(func=_cmd_diff)
    p = subs.add_parser("blame", parents=[parent])
    p.add_argument("file")
    p.set_defaults(func=_cmd_blame)
    p = subs.add_parser("preview", parents=[parent])
    p.add_argument("--to")
    p.add_argument("rest", nargs="*")
    p.set_defaults(func=_cmd_preview)
    p = subs.add_parser("fold", parents=[parent])
    p.add_argument("--at", required=True,
                    help="a commit-index (int), an explicit op-id set (op:<id>,<id>,...), "
                         "or a ref name")
    p.set_defaults(func=_cmd_fold)


def _cmd_log(args) -> int:
    """`sgt log` is the daily grid surface (KTD9): bare `sgt log` renders the lane×commit grid
    (`grid_view`), with mode flags for its sibling projections. The old op-DAG dump lives on under
    `--ops`. `--json` returns the view matching the mode: grid (default/`--rail`), the feature tree
    (`--tree`), the status scalars (`--summary`), or the op DAG (`--ops`)."""
    if args.ops:
        return _log_ops(".", args.as_json, args.full, args.limit, args.offset)
    if args.tree:
        return _log_tree(".", args.as_json, args.refresh)
    if args.summary:
        return _status(".", args.as_json)
    if args.rail:
        return _log_rail(".", as_json=args.as_json, color=not args.no_color, refresh=args.refresh)
    return _log_grid(".", as_json=args.as_json, frontier=args.at, color=not args.no_color,
                     refresh=args.refresh, focus=args.focus, links=args.links)


def _cmd_state(args) -> int:
    return _state(".", args.as_json, args.full)


def _cmd_status(args) -> int:
    return _status(".", args.as_json)


def _cmd_map(args) -> int:
    return _map(".", args.as_json, args.rebuild)


def _cmd_graph(args) -> int:
    return _graph(".", frontier=args.at, color=not args.no_color, refresh=args.refresh,
                  focus=args.focus, links=args.links)


def _cmd_episodes(args) -> int:
    return _episodes(".", color=not args.no_color, refresh=args.refresh)


def _cmd_history(args) -> int:
    return _history(".", args.as_json, args.full, args.limit, args.offset)


def _cmd_compose(args) -> int:
    return _compose(".", args.as_json, args.full)


def _cmd_fold(args) -> int:
    return _fold(".", args.at, args.as_json)


def _cmd_fsck(args) -> int:
    if getattr(args, "tree", False):
        return _fsck_tree(".", args.as_json)
    return _fsck(".", args.as_json)


def _cmd_reindex(args) -> int:
    return _reindex(".", args.as_json)


def _cmd_diff(args) -> int:
    return _diff(".", args.a, args.b, args.as_json)


def _cmd_blame(args) -> int:
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
              f"re-mine the ref (`sgt get`) to rebuild it")
    for key in report.unreachable_witnesses:
        print(f"    unreachable witness for {key!r}: its SHA no longer resolves -- prune the ref "
              f"or re-seed it (`sgt get` on a live ref)")
    if report.mixed_versions:
        print(f"    mixed miner versions {', '.join(report.mixed_versions)} -- "
              f"run `sgt migrate ops-v3` to unify the store")
    if report.op_index_stale:
        print("    op index stale (advisory): the next read rebuilds it -- "
              "`sgt reindex` forces it now")
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
        print(f"    drift: {path} — `sgt get` to absorb HEAD's bytes, or `sgt save` to enforce "
              f"the ideal (opposite data-loss profiles)")
    for cls, label in (("backstop_kept", "backstop-kept"), ("unmanaged", "unmanaged"),
                       ("staged", "staged candidate"), ("unseeded", "unseeded ref")):
        for path in result[cls]:
            print(f"    {label}: {path}")
    return 0 if not drift else 1


def _reindex(repo: str, as_json: bool = False) -> int:
    """`sgt reindex [--json]`: force-rebuild the `sgt.core.opindex` footprint-only sidecar. Every
    read view self-heals this automatically, so this is a maintenance verb (rebalancing latency
    into a deliberate moment) rather than a repeat step in the daily loop."""
    from sgt.core import opindex

    opindex.rebuild(repo)
    count = len(opindex.index_ops(repo))
    if as_json:
        return _emit_json({"ok": True, "op_count": count})
    print(f"✓ reindex — {count} op(s) indexed")
    return 0


def _log_grid(repo: str, *, as_json: bool = False, frontier: int | None = None, color: bool = True,
              refresh: bool = False, focus: str | None = None, links: bool = False) -> int:
    """`sgt log` (the default grid, KTD9): the lane×commit timeline. `--json` returns the canonical
    `grid_view`; the text render reuses the feature-timeline machinery (`render_graph_lines`) over
    the last-built map. A pure cached read by default (fast, glanceable); `--refresh` re-mines and
    rebuilds features + checkpoints first (see `_map_for_view`)."""
    from sgt.api import grid_view, history_view, segments_view
    from sgt.tui.graph import render_graph_lines

    mv = _map_for_view(repo, refresh, "log", color and not as_json)
    if as_json:
        return _emit_json(grid_view(repo))
    hv = history_view(repo, full=True, limit=1_000_000)
    for line in render_graph_lines(
        mv, hv, segments_view(repo), frontier=frontier, color=color, focus=focus, show_links=links,
    ):
        print(line)
    return 0


def _log_rail(repo: str, *, as_json: bool = False, color: bool = True, refresh: bool = False) -> int:
    """`sgt log --rail` (the episode rail / vertical git-log): "what I did, in order." `--json`
    returns `grid_view` (the rail is a time-major rotation of the same cells); the text render
    reuses `render_rail_lines`."""
    from sgt.api import grid_view, history_view
    from sgt.tui.graph import render_rail_lines

    mv = _map_for_view(repo, refresh, "log", color and not as_json)
    if as_json:
        return _emit_json(grid_view(repo))
    for line in render_rail_lines(mv, history_view(repo, full=True, limit=1_000_000), color=color):
        print(line)
    return 0


def _log_tree(repo: str, as_json: bool = False, refresh: bool = False) -> int:
    """`sgt log --tree` (the feature tree, no time axis — what `sgt map` shows): a read of the
    last-built tree (`--refresh` rebuilds it first). `--json` returns `map_view`."""
    from sgt.api import map_view

    _map_for_view(repo, refresh, "log", not as_json)
    view = map_view(repo)
    if as_json:
        return _emit_json(view)
    _print_map_tree(view)
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
        print("(no ops — nothing mined yet; commit some work then run `sgt log --ops`)")
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

    get(repo)  # mine-on-contact so the ideal reflects current reality
    view = state_view(repo, full=full)
    if as_json:
        return _emit_json(view)
    pct = view["coverage_fraction"] * 100
    frontier_n = len(view["frontier"]) if full else view["frontier_count"]
    entity_n = len(view["entity_paths"]) if full else view["entity_path_count"]
    print(f"{frontier_n} symbol(s) at the frontier; "
          f"{len(view['covered_paths'])} path(s) covered, "
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
    """Indented `label (id) · N op(s)` tree, DFS from `roots` via each node's `children`. A feature
    leaf with no live members is a clustering-algorithm artifact (an empty split child), not a real
    feature -- same "drop what has nothing to show" filter `graph_layout` applies to lanes with no
    ops (`sgt/tui/graph.py`) -- so it and any subsystem left with no other visible descendant are
    skipped here. Display-only: the underlying tree/clustering is untouched."""
    by_id = {n["id"]: n for n in view["nodes"]}

    def is_visible(nid: str) -> bool:
        n = by_id[nid]
        if not n["children"]:
            return bool(n["members"])
        return any(is_visible(c) for c in n["children"])

    def visit(nid: str, depth: int) -> None:
        n = by_id[nid]
        print(f"{'  ' * depth}{n['label']} ({n['id']}) · {n['op_count']} op(s)")
        for child in n["children"]:
            if is_visible(child):
                visit(child, depth + 1)

    for root in view["roots"]:
        if is_visible(root):
            visit(root, 0)
    shown = sum(1 for n in view["nodes"] if not n["children"] and n["members"])
    print(f"{shown} feature(s)")


def _map(repo: str, as_json: bool = False, rebuild: bool = False) -> int:
    """`sgt map` (plan U13): (re)build the feature tree from the live op store -- clustering,
    Greene identity, pins, labeling -- then print the kernel-backed projection (`api.map_view`).
    `--rebuild` forces a full from-scratch recluster, bypassing dirty-subtree splicing."""
    from sgt.api import map_view
    from sgt.core.lens import get
    from sgt.lens.map import build_map

    get(repo)  # mine-on-contact so the map reflects current reality (R9)
    build_map(repo, rebuild=rebuild)
    view = map_view(repo)
    if as_json:
        return _emit_json(view)
    _print_map_tree(view)
    return 0


def _map_for_view(repo: str, refresh: bool, verb: str, color: bool) -> dict:
    """The shared read/refresh path behind `sgt graph` and `sgt episodes`. The daily command is a
    pure read of the *last-built* map (~sub-second) -- NOT a re-mine + re-cluster (which costs ~30s
    and would make a glanceable command unusable). `--refresh` is the *one* command that reflects
    brand-new edits: it re-mines and rebuilds BOTH layers the graph shows -- the feature tree
    (`build_map`) AND the intent checkpoints (`build_segments`/`build_themes`) -- so a user never
    has to run `intent build` then `map --rebuild` then `graph` as three separate steps. Both label
    passes have a deterministic offline fallback, so a refresh works with no LLM key (just terser
    names). Returns the `map_view` dict."""
    from sgt.api import map_view

    mv = None if refresh else map_view(repo)
    if not (refresh or not (mv and mv.get("nodes"))):
        if color:
            print(f"\x1b[2m (cached — run `sgt {verb} --refresh` to reflect new edits)\x1b[0m")
        return mv

    from sgt.core.lens import get
    from sgt.intent.theme import build_themes
    from sgt.intent.theme_segment import build_segments
    from sgt.lens.map import build_map

    if color:
        print("\x1b[2m refreshing: mining edits + naming features and checkpoints…\x1b[0m")
    get(repo)  # mine-on-contact (R9)
    build_map(repo)          # feature tree + labels (the "what exists" layer)
    build_segments(repo)     # per-feature checkpoints (the "what I did, in chapters" layer)
    build_themes(repo)       # cross-feature rollup (kept for the "one PR spanned N features" view)
    return map_view(repo)


def _graph(repo: str, *, frontier: int | None = None, color: bool = True, refresh: bool = False,
           focus: str | None = None, links: bool = False) -> int:
    """`sgt graph` (the terminal feature timeline): one identity-colored lane per feature, grouped
    into subsystem swimlanes and ordered by first appearance; each lane leads with its short
    `f-XXXX` handle (the copy-paste target for `sgt revert <handle>[@n]`) and draws its intent
    checkpoints as an ordered train of bracketed "cars" -- the atom is the chapter you'd actually
    revert to, not a raw op. `focus` narrows to one feature, full width, one detail line per car;
    `links` re-enables the co-change `↔` annotation (off by default).

    A pure read of the last-built map by default; `--refresh` re-mines and rebuilds both layers
    (features + checkpoints) in one step. See `_map_for_view`."""
    from sgt.api import history_view, segments_view
    from sgt.tui.graph import render_graph_lines

    mv = _map_for_view(repo, refresh, "graph", color)
    hv = history_view(repo, full=True, limit=1_000_000)
    for line in render_graph_lines(
        mv, hv, segments_view(repo), frontier=frontier, color=color, focus=focus, show_links=links,
    ):
        print(line)
    return 0


def _episodes(repo: str, *, color: bool = True, refresh: bool = False) -> int:
    """`sgt episodes` (the terminal episode rail / vertical git-log): the newest commit-episode on
    top, each feature a lane column (its episodes a straight vertical line), lanes reused across
    non-overlapping spans. Where `sgt graph` answers "what is the codebase made of, over time,"
    this answers "what did I do, in order" -- the rewind lens. A pure read of the last-built map
    (like `sgt graph`); `--refresh` re-mines + rebuilds both layers first (see `_map_for_view`)."""
    from sgt.api import history_view
    from sgt.tui.graph import render_rail_lines

    mv = _map_for_view(repo, refresh, "episodes", color)
    for line in render_rail_lines(mv, history_view(repo, full=True, limit=1_000_000), color=color):
        print(line)
    return 0


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


def _status(repo: str, as_json: bool = False) -> int:
    """`sgt status` (plan U13): file/symbol/feature counts, coverage, oracle status, drift."""
    from sgt.api import status_view
    from sgt.core.lens import get

    get(repo)
    view = status_view(repo)
    if as_json:
        return _emit_json(view)
    print(f"{view['files']} file(s), {view['symbols']} symbol(s), {view['features']} feature(s), "
          f"{view['coverage_fraction'] * 100:.0f}% entity coverage")
    print(f"  oracle: {view['oracle']['status']}")
    if view["forks"]["open"]:
        print(f"  ⚠ {view['forks']['open']} OPEN FORK(S) — run `sgt forks` for the merge-op remedies")
    if view["drift"]["any"]:
        print(f"  ⚠ drift: {', '.join(view['drift']['paths'])}")
    if view.get("backstop_kept"):
        print(f"  ⚠ kept {len(view['backstop_kept'])} unreproducible file(s): "
              f"{', '.join(view['backstop_kept'])} — left on disk (not deleted); repair the chain "
              f"(`sgt fsck --tree`) to materialize them")
    if view.get("unmanaged"):
        print(f"  ⚠ {len(view['unmanaged'])} unmanaged path(s) (symlinks, untouched): "
              f"{', '.join(view['unmanaged'])}")
    if not view["drift"]["any"] and not view["forks"]["open"]:
        print("  ✓ in sync")
    return 0


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

    get(repo)  # mine-on-contact so the bundle reflects current reality (R9)
    view = compose_view(repo, full=full)
    if as_json:
        return _emit_json(view)
    status, oracle = view["status"], view["oracle_verdict"]
    print(f"{status['files']} file(s), {status['symbols']} symbol(s), {status['features']} feature(s)")
    print(f"  oracle: {status['oracle']['status']}")
    if view["forks"]["open"]:
        print(f"  ⚠ {view['forks']['open']} open fork(s)")
    if status["drift"]["any"]:
        print(f"  ⚠ drift: {', '.join(status['drift']['paths'])}")
    if view["sessions"]["sessions"]:
        print(f"  {len(view['sessions']['sessions'])} active session(s)")
    if view["proposals"]:
        print(f"  {len(view['proposals'])} open proposal(s)")
    return 0


def _parse_at(spec: str) -> dict:
    """Parse `sgt fold --at <spec>` into one of `fold_view`'s three keyword-only frontier args: an
    all-digit spec is a commit-index position on `history_view`'s axis; an `op:<id>,<id>,...` spec
    is an explicit op-id set; anything else is a ref name."""
    if spec.isdigit():
        return {"at_commit_index": int(spec)}
    if spec.startswith("op:"):
        return {"op_ids": spec[3:].split(",")}
    return {"ref": spec}


def _fold(repo: str, at: str, as_json: bool = False) -> int:
    """`sgt fold --at <spec> [--json]`: a side-effect-free fold of an arbitrary frontier
    (`api.fold_view`) -- the composition workbench's draggable-playhead primitive. Never checks
    anything out."""
    from sgt.api import fold_view
    from sgt.core.lens import get

    get(repo)  # mine-on-contact so the fold reflects current reality (R9)
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
