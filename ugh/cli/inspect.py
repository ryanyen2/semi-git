"""Read/inspection verbs: `ugh log` lists features with their edits per recent save, `--ops` the
raw op DAG; `ugh status` prints what needs attention. `check` verifies op-store integrity and, with
`--repair`, re-derives the current ref after a git history rewrite. Every verb mines the working
tree on contact before reading (R9)."""

from __future__ import annotations

import argparse as _dep
import sys

from ._common import _add_view_flags, _emit_json, _fail, fork_lines


def register(subs, parent) -> None:
    lp = subs.add_parser("log", parents=[parent])
    lmode = lp.add_mutually_exclusive_group()
    # `--tree --json` is the feature tree (`map_view`) the editor reads; `--ops` the raw op list.
    lmode.add_argument("--tree", action="store_true", help=_dep.SUPPRESS)
    lmode.add_argument("--ops", action="store_true", help=_dep.SUPPRESS)
    _add_view_flags(lp, paged=True)  # --full/--limit/--offset (used by --ops)
    lp.add_argument("--refresh", action="store_true",
                    help="re-mine + rebuild the map first (default: fast read of the last-built map)")
    lp.add_argument("--rebuild", action="store_true",
                    help="refresh with a full from-scratch recluster")
    lp.set_defaults(func=_cmd_log)
    st = subs.add_parser("status", parents=[parent], help="what needs attention")
    _add_view_flags(st)
    st.set_defaults(func=_cmd_status)
    pc = subs.add_parser("check", parents=[parent])
    pc.add_argument("--tree", action="store_true",
                    help="compare code(current_ideal) against the HEAD tree (R2)")
    pc.add_argument("--repair", action="store_true",
                    help="re-derive this ref's record from current git history")
    pc.set_defaults(func=_cmd_check)


def _cmd_log(args) -> int:
    """`ugh log`: one row per feature with its edits per recent save. `--json` returns
    `grid_view`, `--tree --json` the feature tree (`map_view`), `--ops` the op list."""
    if args.ops:
        return _log_ops(".", args.as_json, args.full, args.limit, args.offset)
    if args.tree and args.as_json:
        return _log_tree(".", args.refresh, args.rebuild)
    _map_for_view(".", args.refresh, not args.as_json, rebuild=args.rebuild)
    if args.as_json:
        from ugh.api import grid_view

        return _emit_json(grid_view("."))
    for line in _log_lines("."):
        print(line)
    return 0


def _cmd_status(args) -> int:
    return _status(".", args.as_json, full=args.full)


def _cmd_check(args) -> int:
    if args.repair:
        return _resync(".", args.as_json)
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
              f"`ugh check --repair` re-seeds it from current history")
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


def _resync(repo: str, as_json: bool = False) -> int:
    """`ugh check --repair`: drop this ref's derived local state and re-mine from what HEAD reaches
    now. The append-only op store is never touched."""
    from ugh.core.lens import resync

    res = resync(repo)
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


SAVE_COLUMNS = 8


def _log_lines(repo: str) -> list[str]:
    """A fixed-column list: one row per feature (handle, label, edits per save), the newest saves
    to the right under their short shas, then one row for edits no feature owns. An edit is an
    op that touches a named symbol; residue, anchor and import ops are not counted."""
    from collections import Counter

    from ugh.api import _grid_labels, _named_symbols, history_view
    from ugh.core.opindex import index_ops

    hv = history_view(repo, full=True)
    edits = {op.id for op in index_ops(repo) if _named_symbols(op)}
    counts: dict = {}
    for op in hv["ops"]:
        if op["id"] in edits:
            counts.setdefault(op["feature_id"], Counter())[op["commit_index"]] += 1
    carrying = {i for c in counts.values() for i in c}
    saves = [c for c in hv["commits"] if not c["bookkeeping"] and c["index"] in carrying]
    saves = saves[-SAVE_COLUMNS:]
    if not saves:
        return ["(no saves yet — `ugh save` records your edits)"]
    labels = _grid_labels(repo)

    def row(handle: str, label: str, per: Counter) -> str:
        label = label if len(label) <= 32 else label[:31] + "…"
        cells = "".join(f"{per.get(c['index'], 0) or '.':>8}" for c in saves)
        return f"{handle:<8}  {label:<32}{cells}"

    lines = [f"{'id':<8}  {'feature':<32}" + "".join(f"{c['sha'][:7]:>8}" for c in saves)]
    features = sorted((fid for fid in counts if fid is not None),
                      key=lambda fid: (min(counts[fid]), labels.get(fid, fid)))
    for fid in features:
        handle = (fid[2:] if fid.startswith("f-") else fid)[:8]
        lines.append(row(handle, labels.get(fid, fid), counts[fid]))
    if None in counts:
        lines.append(row("", "(no feature)", counts[None]))
    return lines


def _log_tree(repo: str, refresh: bool, rebuild: bool) -> int:
    """`ugh log --tree --json`: the feature tree (`map_view`). `--rebuild` reclusters it from
    scratch first; `--refresh` re-mines and rebuilds it."""
    if rebuild:
        from ugh.api import map_view
        from ugh.core.lens import get
        from ugh.lens.map import build_map

        get(repo)  # mine-on-contact so the map reflects current reality (R9)
        build_map(repo, rebuild=True)
        return _emit_json(map_view(repo))
    return _emit_json(_map_for_view(repo, refresh, False))


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


def _map_for_view(repo: str, refresh: bool, human: bool, rebuild: bool = False) -> dict:
    """The shared read/refresh path behind `ugh log`. The daily command is a
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
            print(line, file=sys.stderr)
        return mv

    from ugh.core.lens import get
    from ugh.intent.theme import build_themes
    from ugh.intent.theme_segment import build_segments
    from ugh.lens.map import build_map

    if human:
        print(" refreshing: mining edits + naming features and checkpoints…", file=sys.stderr)
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
    from ugh.api import plural
    from ugh.store.gitbind import GitBinding

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


def _status(repo: str, as_json: bool = False, full: bool = False) -> int:
    """`ugh status`: file/symbol/feature counts, coverage, oracle status, then what needs you
    (forks, drift, unreproducible files) and what does not. Path lists show four (`--full` for
    all of them)."""
    from ugh.api import plural, status_view
    from ugh.core.lens import get

    get(repo)
    view = status_view(repo)
    if as_json:
        return _emit_json(view)

    def paths(items) -> str:
        shown = items if full else items[:4]
        return "      " + ", ".join(shown) + ("" if len(items) == len(shown)
                                              else f"  +{len(items) - len(shown)} more (--full)")

    print(f"{plural(view['files'], 'file')} · {plural(view['symbols'], 'symbol')} · "
          f"{plural(view['features'], 'feature')} · {view['coverage_fraction'] * 100:.0f}% coverage"
          f" · oracle {view['oracle']['status']}")
    lines = fork_lines(view["forks"]["records"])
    if view["drift"]["any"]:
        n = len(view["drift"]["paths"])
        lines += [f"⚠ {plural(n, 'file')} {'differs' if n == 1 else 'differ'} from the recorded "
                  "state — `ugh save`", paths(view["drift"]["paths"])]
    if view["backstop_kept"]:
        lines += [f"⚠ {plural(len(view['backstop_kept']), 'unreproducible file')} kept on disk — "
                  "`ugh check --tree`", paths(view["backstop_kept"])]
    if view["never_recorded"]:
        lines += [f"· {plural(len(view['never_recorded']), 'file')} ugh does not track, left alone",
                  paths(view["never_recorded"])]
    if view["unmanaged"]:
        lines.append(f"· {plural(len(view['unmanaged']), 'unmanaged path')} (symlinks, untouched)")
    for line in lines or ["✓ in sync"]:
        print(line)
    _print_residual(repo, full)
    return 0


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
    `predicted_fp`). Folded into `ugh status` as "what needs attention" so unfinished
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
