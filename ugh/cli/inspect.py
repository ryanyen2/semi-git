"""Read/inspection verbs: `ugh log` lists the saves with their names, `ugh status` prints what needs
attention, and `check` verifies op-store integrity and, with `--repair`, re-derives the current ref
after a git history rewrite. Every verb mines the working tree on contact before reading (R9)."""

from __future__ import annotations

from ._common import _add_view_flags, _emit_json, fork_lines


def register(subs, parent) -> None:
    lp = subs.add_parser("log", parents=[parent])
    lp.add_argument("-n", "--limit", type=int, default=None, help="only the newest N saves")
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
    """`ugh log`: the saves, newest first, one line each with the names on its steps."""
    from ugh.api import log_view
    from ugh.core.lens import get

    get(".")
    view = log_view(".", limit=args.limit)
    if args.as_json:
        return _emit_json(view)
    for line in log_lines(view) or ["(no saves yet — `ugh save` records your edits)"]:
        print(line)
    return 0


def log_lines(view: dict) -> list[str]:
    """One line per save: short sha, subject, `label ×n` per name, `n unnamed`, `n out`."""
    from collections import Counter

    lines = []
    for save in view["saves"]:
        steps = save["steps"]
        counts = Counter(st["name"] for st in steps if st["name"])
        parts = [f"{label} ×{n}" for label, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]
        unnamed = sum(1 for st in steps if not st["name"])
        out = sum(1 for st in steps if st["out"])
        parts += [f"{unnamed} unnamed"] * bool(unnamed) + [f"{out} out"] * bool(out)
        lines.append("  ".join([f"{save['sha'][:7]} {save['subject']}", *parts]))
    return lines


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
              f"`ugh check --repair` re-derives it")
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
        print(f"    drift: {path} — `ugh check --repair` to absorb HEAD's bytes, or `ugh save` to "
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


def _status(repo: str, as_json: bool = False, full: bool = False) -> int:
    """`ugh status`: file/symbol counts, coverage, oracle status, then what needs you: forks, drift,
    unreproducible and untracked files, and the plan. Path lists show four (`--full` for all)."""
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
          f"{view['coverage_fraction'] * 100:.0f}% coverage · oracle {view['oracle']['status']}")
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
    if view["plan"] is not None:
        from .plan import plan_lines

        lines += plan_lines(view["plan"])
    for line in lines or ["✓ in sync"]:
        print(line)
    return 0
