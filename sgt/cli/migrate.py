"""`sgt migrate feature-ids [--apply]` (plan U21/D6): re-mint pre-U21 sequential `F<n>` feature
ids to their content-addressed `f-<founding op>` form. Dry-run by default -- prints the full
old->new re-mint map so a human can review the one genuinely destructive step before it writes.
`--apply` performs the atomic re-mint (tree ids + pin references + alias G-Set together);
idempotent, so a second `--apply` is a no-op."""

from __future__ import annotations

from ._common import _emit_json, _fail


def register(subs, parent) -> None:
    p = subs.add_parser("migrate", parents=[parent])
    p.add_argument("what", nargs="?", default="feature-ids")
    p.add_argument("--apply", action="store_true")
    p.set_defaults(func=_cmd_migrate)


def _cmd_migrate(args) -> int:
    if args.what != "feature-ids":
        return _fail(f"unknown migration {args.what!r}; only 'feature-ids' is supported")
    return _migrate(".", args.apply, args.as_json)


def _migrate(repo: str, apply: bool, as_json: bool) -> int:
    from sgt.lens.reconcile import migrate_feature_ids

    report = migrate_feature_ids(repo, dry_run=not apply)
    remap = dict(sorted(report.remap.items()))
    view = {
        "mode": "applied" if report.changed else "dry-run",
        "remap": remap,
        "count": len(remap),
    }
    if as_json:
        return _emit_json(view)

    if not remap:
        print("✓ feature ids already content-addressed -- nothing to migrate")
        return 0
    header = "applied" if report.changed else "dry-run (re-run with --apply to write)"
    print(f"feature-id migration [{header}]: {len(remap)} id(s)")
    for old, new in remap.items():
        print(f"  {old}  ->  {new}")
    return 0
