"""`sgt intent` (plan U6/U7/U8): the intent-clustering overlay's CLI surface.

`sgt intent list [--json]` and `sgt intent show <theme-id|commit-sha> [--json]` render
`sgt.api.intent_view` -- every commit-keyed atom (rung 0/1, recomputed on read) and every
persisted, LLM-named theme (rung 2), each with its dependency-graph-backed tier and cross-feature
span. `sgt intent build [--json]` is the one command that runs the LLM theme pass and writes
`.sgt/intent/themes.json` (`sgt.intent.theme.build_themes`) -- kept out of the read verbs, exactly
as `sgt map` (the write) is distinct from `map_view` (the read).

`sgt intent revert <theme-id|commit-sha> [--subset <sha>...] [--emit] [--json]` (KTD6): resolves
the target to its deterministic atom union (`sgt.intent.group.resolve_group` -- never from the
LLM's own output) and runs the *exact same* `verbs.plan_revert_op_set` -> `_emit_verb_result` path
as every other revert -- same up-set removal, same fork refusal, same oracle gate. The LLM only
ever decided the theme's *default* membership; a wrong boundary is a mis-default visible in the
preview and adjustable with `--subset`, never a silent destructive edit.
"""

from __future__ import annotations

from ._common import _emit_json, _fail_json

_USAGE = ("usage: sgt intent list [--json] | sgt intent show <theme-id|commit-sha> [--json] | "
          "sgt intent build [--json] | "
          "sgt intent revert <theme-id|commit-sha> [--subset <sha>...] [--emit] [--json]")


def register(subs, parent) -> None:
    p = subs.add_parser("intent", parents=[parent])
    p.add_argument("sub", nargs="?")
    p.add_argument("target", nargs="?")
    p.add_argument("--subset", nargs="*")
    p.add_argument("--emit", action="store_true")
    p.set_defaults(func=_cmd_intent)


def _cmd_intent(args) -> int:
    return _intent(".", args.sub, args.target, args.subset, args.emit, args.as_json)


def _intent(
    repo: str, sub: str | None, target: str | None, subset: list[str] | None, emit: bool,
    as_json: bool,
) -> int:
    from sgt.core.lens import get

    if sub not in ("list", "show", "build", "revert"):
        print(_USAGE)
        return 2
    get(repo)  # mine-on-contact so the overlay reflects current reality (R9)
    if sub == "list":
        return _list(repo, as_json)
    if sub == "build":
        return _build(repo, as_json)
    if target is None:
        print(_USAGE)
        return 2
    if sub == "revert":
        return _revert(repo, target, subset, emit, as_json)
    return _show(repo, target, as_json)


def _tier_badge(tier: str) -> str:
    return {"coupled": "●", "co-changed": "◐", "thematic": "○"}.get(tier, "?")


def _list(repo: str, as_json: bool) -> int:
    from sgt.api import intent_view

    view = intent_view(repo)
    if as_json:
        return _emit_json(view)
    if not view["themes"]:
        print("(no themes built yet -- run `sgt intent build`)")
    for t in view["themes"]:
        span = ", ".join(t["feature_span"]) or "(no feature)"
        print(f"  {_tier_badge(t['tier'])} {t['label']}  [{t['theme_id']}]  "
              f"{len(t['op_ids'])} op(s) across {span}  ({t['tier']}, {t['source']})")
        if t["stale_shas"]:
            names = ", ".join(sha[:8] for sha in t["stale_shas"])
            print(f"    ⚠ stale: {len(t['stale_shas'])} member commit(s) no longer resolve ({names})")
    print(f"{len(view['themes'])} theme(s), {len(view['atoms'])} atom(s)")
    return 0


def _show(repo: str, target: str, as_json: bool) -> int:
    from sgt.api import intent_view

    view = intent_view(repo)
    theme = next((t for t in view["themes"] if t["theme_id"] == target), None)
    matching_atoms = [a for a in view["atoms"] if a["commit_sha"].startswith(target)]

    if theme is not None:
        result = {"kind": "theme", **theme, "atoms": [
            a for a in view["atoms"] if a["commit_sha"] in theme["atom_shas"]
        ]}
    elif matching_atoms:
        result = {"kind": "atom", **matching_atoms[0]}
    else:
        return _fail_json(f"no theme or commit {target!r} found in the intent overlay", as_json)

    if as_json:
        return _emit_json(result)
    if result["kind"] == "theme":
        print(f"{result['label']}  [{result['theme_id']}]  ({result['tier']}, {result['source']})")
        print(f"  {result['rationale']}")
        print(f"  feature span: {', '.join(result['feature_span']) or '(none)'}")
        if result["stale_shas"]:
            names = ", ".join(sha[:8] for sha in result["stale_shas"])
            print(f"  ⚠ stale: {len(result['stale_shas'])} member commit(s) no longer resolve ({names})")
        for a in result["atoms"]:
            print(f"    {a['commit_sha'][:12]}  {a['subject']}  ({len(a['op_ids'])} op(s))")
    else:
        print(f"{result['commit_sha'][:12]}  {result['subject']}  ({result['tier']})")
        print(f"  feature span: {', '.join(result['feature_span']) or '(none)'}")
        print(f"  {len(result['op_ids'])} op(s)")
        if result["prompt"]:
            print(f"  prompt: {result['prompt']}")
    return 0


def _build(repo: str, as_json: bool) -> int:
    from sgt.intent.theme import build_themes

    themes = build_themes(repo)
    if as_json:
        return _emit_json({"themes": themes, "count": len(themes)})
    print(f"✓ intent build: {len(themes)} theme(s)")
    return 0


def _revert(repo: str, target: str, subset: list[str] | None, emit: bool, as_json: bool) -> int:
    from sgt import state
    from sgt.core import verbs
    from sgt.core.lens import _load_declared
    from sgt.core.store import Store
    from sgt.intent import group
    from sgt.lens.tree import load as load_tree

    all_ops = Store(repo).all_ops()
    declared = _load_declared(repo)
    all_atoms = group.atoms(repo)
    themes = state.load_json(repo, "intent_themes", default={})

    theme_entry = themes.get(target)
    if theme_entry is not None:
        current_shas = {a.commit_sha for a in all_atoms}
        stale = sorted(frozenset(theme_entry["atom_shas"]) - current_shas)
        if stale:
            names = ", ".join(sha[:8] for sha in stale)
            return _fail_json(
                f"run `sgt intent build` to reconcile -- {len(stale)} member commit(s) no longer "
                f"resolve: {names}", as_json,
            )

    resolved = group.resolve_group(target, themes, all_atoms)
    if resolved is None:
        return _fail_json(f"no theme or commit {target!r} found in the intent overlay", as_json)
    kind, member_atoms = resolved

    requires = group.group_requires(member_atoms, all_ops, declared)
    chosen, err = group.apply_subset(member_atoms, requires, subset)
    if err is not None:
        return _fail_json(err, as_json)

    op_ids = frozenset().union(*(a.op_ids for a in chosen)) if chosen else frozenset()
    tree_result = load_tree(repo)
    op_leaf = tree_result["op_leaf"] if tree_result else {}
    commit_shas = frozenset(a.commit_sha for a in chosen if a.commit_sha != group.UNWITNESSED)
    tier = group.tier(op_ids, commit_shas, all_ops, declared, op_leaf)

    if not as_json:
        if len(chosen) > 1:
            print(f"reverting {len(chosen)} atom(s) as one group:")
            for atom in chosen:
                print(f"    {atom.commit_sha[:12]}  {atom.subject}  ({len(atom.op_ids)} op(s))")
        print(f"  tier: {tier} {_tier_badge(tier)}")

    preview = verbs.plan_revert_op_set(repo, target, op_ids)

    from .ideal_edit import _emit_verb_result

    return _emit_verb_result(repo, preview, emit, as_json, extra={"tier": tier})
