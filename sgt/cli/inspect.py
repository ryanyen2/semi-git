"""Read/inspection verbs (plan U7/U9/U13): `log`/`state`/`diff` over the op DAG, the current
ideal, and ideal-vs-ideal diffs; `map`/`blame`/`status` over the feature tree; `history` for the
commit axis; `fsck` for op-store integrity; `preview` for side-effect-free feature-verb previews.
Every verb mines the working tree on contact before reading (R9)."""

from __future__ import annotations

from ._common import _emit_json, _fail


def register(subs, parent) -> None:
    for verb, fn in (
        ("log", _cmd_log), ("state", _cmd_state), ("status", _cmd_status),
        ("map", _cmd_map), ("history", _cmd_history), ("compose", _cmd_compose),
    ):
        subs.add_parser(verb, parents=[parent]).set_defaults(func=fn)
    pf = subs.add_parser("fsck", parents=[parent])
    pf.add_argument("--tree", action="store_true",
                    help="compare code(current_ideal) against the HEAD tree (R2)")
    pf.set_defaults(func=_cmd_fsck)
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
    return _log(".", args.as_json)


def _cmd_state(args) -> int:
    return _state(".", args.as_json)


def _cmd_status(args) -> int:
    return _status(".", args.as_json)


def _cmd_map(args) -> int:
    return _map(".", args.as_json)


def _cmd_history(args) -> int:
    return _history(".", args.as_json)


def _cmd_compose(args) -> int:
    return _compose(".", args.as_json)


def _cmd_fold(args) -> int:
    return _fold(".", args.at, args.as_json)


def _cmd_fsck(args) -> int:
    if getattr(args, "tree", False):
        return _fsck_tree(".", args.as_json)
    return _fsck(".", args.as_json)


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


def _log(repo: str, as_json: bool = False) -> int:
    """The kernel op DAG (plan U7). Mine-on-contact first, then project via `sgt.api.oplog_view`."""
    from sgt.api import oplog_view
    from sgt.core.lens import get

    get(repo)  # sync foreign commits into the store before inspecting it
    view = oplog_view(repo)
    if as_json:
        return _emit_json(view)
    if not view["ops"]:
        print("(no ops — nothing mined yet; commit some work then run `sgt log`)")
        return 0
    print(f"{view['count']} op(s):")
    for op in view["ops"]:
        syms = ", ".join(f["symbol"] for f in op["footprint"])
        print(f"  {op['id'][:12]} [{op['kind']}]: {syms}")
    return 0


def _state(repo: str, as_json: bool = False) -> int:
    """The current ref's ideal (plan U7): frontier, coverage, entity-granularity fraction."""
    from sgt.api import state_view
    from sgt.core.lens import get

    get(repo)  # mine-on-contact so the ideal reflects current reality
    view = state_view(repo)
    if as_json:
        return _emit_json(view)
    pct = view["coverage_fraction"] * 100
    print(f"{len(view['frontier'])} symbol(s) at the frontier; "
          f"{len(view['covered_paths'])} path(s) covered, "
          f"{len(view['entity_paths'])} at entity granularity ({pct:.0f}%)")
    derived = set(view["derived_paths"])
    for path in view["covered_paths"]:
        mark = "entity" if path in set(view["entity_paths"]) else "whole-file"
        tag = " [derived]" if path in derived else ""
        print(f"  {path}  ({mark}){tag}")
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
    """Indented `label (id) · N op(s)` tree, DFS from `roots` via each node's `children`."""
    by_id = {n["id"]: n for n in view["nodes"]}

    def visit(nid: str, depth: int) -> None:
        n = by_id[nid]
        print(f"{'  ' * depth}{n['label']} ({n['id']}) · {n['op_count']} op(s)")
        for child in n["children"]:
            visit(child, depth + 1)

    for root in view["roots"]:
        visit(root, 0)
    print(f"{view['feature_count']} feature(s)")


def _map(repo: str, as_json: bool = False) -> int:
    """`sgt map` (plan U13): (re)build the feature tree from the live op store -- clustering,
    Greene identity, pins, labeling -- then print the kernel-backed projection (`api.map_view`)."""
    from sgt.api import map_view
    from sgt.core.lens import get
    from sgt.lens.map import build_map

    get(repo)  # mine-on-contact so the map reflects current reality (R9)
    build_map(repo)
    view = map_view(repo)
    if as_json:
        return _emit_json(view)
    _print_map_tree(view)
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


def _history(repo: str, as_json: bool = False) -> int:
    """`sgt history [--json]`: every mined commit in chronological order plus every op's derived
    kind/feature/commit-index -- the feature-map webview's Gantt commit-index axis
    (`api.history_view`)."""
    from sgt.api import history_view
    from sgt.core.lens import get

    get(repo)  # mine-on-contact so history reflects current reality (R9)
    view = history_view(repo)
    if as_json:
        return _emit_json(view)
    print(f"{len(view['commits'])} commit(s), {len(view['ops'])} op(s) placed on the axis:")
    for c in view["commits"]:
        print(f"  [{c['index']}] {c['sha'][:12]}  {c['subject']}")
    return 0


def _compose(repo: str, as_json: bool = False) -> int:
    """`sgt compose [--json]`: one aggregate read (`api.compose_view`) bundling map/history/status/
    forks/plan/drift/sessions/trust + the oracle verdict + open proposals -- the composition
    workbench's single-call refresh, replacing ~9 separate `sgt <verb> --json` invocations."""
    from sgt.api import compose_view
    from sgt.core.lens import get

    get(repo)  # mine-on-contact so the bundle reflects current reality (R9)
    view = compose_view(repo)
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
