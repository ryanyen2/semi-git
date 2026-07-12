"""`sgt tiers` (plan U27, D4): the three-tier file boundary's CLI surface -- a read view of the
current effective configuration, and the one mutating command that adds an override pattern.

Only `set` mutates (to `entity`/`opaque`/`ignored`); there is no `unset` -- add it if a real
scenario needs it (CLAUDE.md's minimal-surface rule). Setting `ignored` refuses if the pattern
matches any path the current ideal covers (D4's second safety guard): silently dropping a live
path out of mining is a data-loss shape, so the refusal names `sgt revert` as the remedy --
drop the path's ops from the ideal first, then ignore it.
"""

from __future__ import annotations

from ._common import _emit_json, _fail

_TIERS = ("entity", "opaque", "ignored")


def register(subs, parent) -> None:
    p = subs.add_parser("tiers", parents=[parent])
    tsub = p.add_subparsers(dest="tiers_verb")
    s = tsub.add_parser("set", parents=[parent])
    s.add_argument("pattern")
    s.add_argument("tier", choices=_TIERS)
    s.set_defaults(func=_cmd_tiers_set)
    p.set_defaults(func=_cmd_tiers)


def _cmd_tiers(args) -> int:
    if getattr(args, "tiers_verb", None) == "set":
        return _cmd_tiers_set(args)
    return _tiers(".", args.as_json)


def _cmd_tiers_set(args) -> int:
    return _tiers_set(".", args.pattern, args.tier, args.as_json)


def _tiers(repo: str, as_json: bool) -> int:
    from sgt.api import tiers_view
    from sgt.core.lens import get

    get(repo)  # mine-on-contact so the covered-path breakdown reflects current reality (R9)
    view = tiers_view(repo)
    if as_json:
        return _emit_json(view)
    for tier in _TIERS:
        patterns = view["overrides"][tier]
        if patterns:
            print(f"  {tier}: {', '.join(patterns)}")
    if view["sgtignore"]:
        print(f"  .sgtignore: {', '.join(view['sgtignore'])}")
    by_tier: dict[str, list[str]] = {t: [] for t in _TIERS}
    for path, info in view["paths"].items():
        by_tier[info["tier"]].append(path)
    derived = sorted(p for p, info in view["paths"].items() if info["derived"])
    print(f"{len(view['paths'])} covered path(s): "
          f"{len(by_tier['entity'])} entity, {len(by_tier['opaque'])} opaque, "
          f"{len(by_tier['ignored'])} ignored")
    if derived:
        print(f"  {len(derived)} derived: {', '.join(derived)}")
    return 0


def _tiers_set(repo: str, pattern: str, tier: str, as_json: bool) -> int:
    from sgt import state
    from sgt.core import tiers
    from sgt.core.lens import get

    ideal = get(repo)  # mine-on-contact (R9)
    if tier == "ignored":
        from sgt.core.store import Store

        covered = ideal.covered_paths(Store(repo).all_ops())
        hit = sorted(p for p in covered if tiers._match_one(p, pattern))
        if hit:
            shown = ", ".join(hit[:3]) + (", ..." if len(hit) > 3 else "")
            msg = (
                f"'{pattern}' matches {len(hit)} currently-covered path(s) ({shown}) -- "
                "ignoring would silently stop tracking live content; "
                "`sgt revert` those paths' ops first"
            )
            return _emit_json({"ok": False, "error": msg}) if as_json else _fail(msg)

    cfg = tiers.load_tiers(repo)
    overrides = {t: list(cfg.overrides.get(t, ())) for t in _TIERS}
    if pattern not in overrides[tier]:
        overrides[tier].append(pattern)
    state.save_json(repo, "tiers", overrides)

    if as_json:
        return _emit_json({"ok": True, "pattern": pattern, "tier": tier})
    print(f"✓ tiers set {pattern!r} -> {tier}")
    return 0
