"""Async tiered build/test verdicts attached to the current ideal (plan U9/U22, R13). `sgt oracle
run [--tier NAME]` executes configured tiers in declared order, stopping at the first failure;
`sgt oracle override --status pass|fail --reason "..." [--by NAME]` records a human verdict that
supersedes them; `sgt oracle publish [--by NAME]` publishes the recorded verdict as a committed
claim that travels via sync (D8). Materialization itself never calls this -- a verdict is "pending"
until one of these verbs is run explicitly."""

from __future__ import annotations

from ._common import _emit_json


def register(subs, parent) -> None:
    p = subs.add_parser("oracle", parents=[parent])
    p.add_argument("sub", nargs="?")
    p.add_argument("--tier")
    p.add_argument("--status")
    p.add_argument("--reason")
    p.add_argument("--by")
    p.set_defaults(func=_cmd_oracle)


def _cmd_oracle(args) -> int:
    return _oracle(".", args.sub, args.tier, args.status, args.reason, args.by, args.as_json)


def _oracle(repo: str, sub: str | None, tier: str | None, status: str | None,
            reason: str | None, by: str | None, as_json: bool = False) -> int:
    from sgt.core import oracle
    from sgt.core.lens import get

    usage = ('usage: sgt oracle run [--json] [--tier NAME] | '
             'sgt oracle override --status pass|fail --reason "..." [--by NAME] | '
             'sgt oracle publish [--json] [--by NAME]')
    if sub not in ("run", "override", "publish"):
        print(usage)
        return 2

    get(repo)  # mine-on-contact so the verdict is keyed to the current ideal

    if sub == "run":
        try:
            result = oracle.run(repo, tier=tier)
        except ValueError as e:
            print(f"✗ {e}")
            return 2
        if not result["configured"]:
            print("⚠ no oracle configured (.sgt/oracle.json not found) — proceeding without a verdict")
            return 0
        if as_json:
            return _emit_json(result)
        for name, tr in result["tiers"].items():
            icon = "✓" if tr["status"] == "pass" else "✗"
            print(f"{icon} [{name}] exit {tr['exit_code']}")
        return 0

    if sub == "publish":
        try:
            claim = oracle.publish(repo, by=by)
        except ValueError as e:
            print(f"✗ {e}")
            return 2
        if as_json:
            return _emit_json(claim)
        print(f"✓ published claim for {claim['ideal_key']} ({claim['status']})")
        return 0

    if status not in ("pass", "fail") or reason is None:
        print(usage)
        return 2
    record = oracle.override(repo, status, reason, by)
    if as_json:
        return _emit_json(record)
    print(f"✓ override recorded: {status} ({reason})")
    return 0
