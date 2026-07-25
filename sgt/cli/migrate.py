"""`sgt migrate <what> [--apply]`: dry-run-by-default, atomic, idempotent store migrations.

- `ops-v3` (plan U10/R15): cross the op store from miner v2 to v3 (U9's rebirth/flip identity) --
  re-key every op and every op-id-bearing artifact under one resumable manifest, recovering the
  ~20% closure the v2 rebirth pseudo-fork dropped.

Prints the change it *would* make so a human reviews the one destructive step before writing;
`--apply` performs it, and a second `--apply` is a no-op."""

from __future__ import annotations

from ._common import _emit_json, _fail

_KNOWN = ("ops-v3",)


def register(subs, parent) -> None:
    p = subs.add_parser("migrate", parents=[parent])
    p.add_argument("what", nargs="?", default="ops-v3")
    p.add_argument("--apply", action="store_true")
    p.set_defaults(func=_cmd_migrate)


def _cmd_migrate(args) -> int:
    if args.what == "ops-v3":
        return _migrate_ops_v3(".", args.apply, args.as_json)
    return _fail(f"unknown migration {args.what!r}; supported: {', '.join(_KNOWN)}")


def _migrate_ops_v3(repo: str, apply: bool, as_json: bool) -> int:
    from sgt.core.migrate import migrate_ops_v3

    report = migrate_ops_v3(repo, dry_run=not apply)
    view = {
        "mode": "applied" if report.changed else "dry-run",
        "total_ops": report.total_ops,
        "rekey_clean": report.rekey_clean,
        "rebirth_remapped": report.rebirth_remapped,
        "orphaned": list(report.orphaned),
        "artifacts": list(report.artifacts),
        "dropped_refs": report.dropped_refs,
        "claims_orphaned": report.claims_orphaned,
    }
    if as_json:
        return _emit_json(view)

    if report.total_ops == 0 and not report.orphaned:
        print("✓ op store already v3 -- nothing to migrate")
        return 0
    header = "applied" if report.changed else "dry-run (re-run with --apply to write)"
    print(f"ops-v3 migration [{header}]: {report.total_ops} pre-v3 op(s)")
    print(f"  {report.rekey_clean} re-key cleanly, {report.rebirth_remapped} rebirth/flip-remapped, "
          f"{len(report.orphaned)} orphaned")
    if report.artifacts:
        print(f"  artifacts {'rewritten' if report.changed else 'to rewrite'}: "
              f"{', '.join(report.artifacts)} ({report.dropped_refs} ref(s) dropped)")
    if report.claims_orphaned:
        print(f"  {report.claims_orphaned} claim(s) orphaned by the re-key (re-publish to re-attach)")
    for oid in report.orphaned:
        print(f"  orphan (no v3 counterpart): {oid}")
    return 0
