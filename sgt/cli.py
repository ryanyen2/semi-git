"""The `sgt` command surface (origin R5, R6) — graph-only.

sgt never authors code. The verbs operate on the *semantic graph* and reconstruct the tree
from it: `plan` (decompose an intent into reviewable PLANNED nodes; bare `sgt "..."` is its
shorthand), `checkpoint`/`sync` (record the agent's on-disk edits), and `revert`/`switch`/
`reconcile` (plug features in and out). Ref arguments resolve fuzzy-to-exact. Read-only verbs
and the graph ops need no OpenAI key; `plan`/`checkpoint` use it for graph-level reasoning only.
"""

from __future__ import annotations

import sys

from sgt.project import Project

_VERBS = {"init", "plan", "sync", "checkpoint", "revert", "switch", "reconcile", "show", "graph", "status", "mcp", "help"}


def _print_report(rep) -> int:
    icon = "✓" if rep.ok else "✗"
    head = f"{icon} [{rep.action}]"
    if rep.node_id:
        head += f" {rep.node_id}"
    print(head + (f" — {rep.message}" if rep.message else ""))
    for d in rep.landed:
        print(f"    landed: {d}")
    for d in getattr(rep, "quarantined", []):
        print(f"    quarantined: {d}")
    for d in rep.held:
        print(f"    held: {d}")
    return 0 if rep.ok else 1


def confirm_sync(clusters) -> bool:
    """Render the reconciliation plan and ask the user to apply it (the sync checkpoint)."""
    print("Proposed reconciliation of out-of-band changes:")
    for cl in clusters:
        where = f"extend {cl.target}" if cl.target else f"new {cl.kind}"
        print(f"  - {where}: {cl.intent}")
        for e in cl.effects:
            print(f"      {e.op.value} {e.target} ({e.file})")
    try:
        return input("Apply this reconciliation? [y/N] ").strip().lower() in ("y", "yes")
    except EOFError:
        return False


def _orchestrator(repo: str, force: bool = False):
    from sgt.orchestrate.loop import Orchestrator

    return Orchestrator(Project.open(repo), repo_path=repo, force=force)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        return _help()

    cmd = argv[0]
    # `sgt "freeform intent"` — first arg is not a known verb -> treat as `plan` (the
    # front door for intents; sgt never authors code, so a bare intent becomes a plan).
    if cmd not in _VERBS:
        cmd, rest = "plan", argv
    else:
        rest = argv[1:]

    repo = "."
    # `--force`/`-f` lets a mutating verb overwrite out-of-band changes intentionally.
    force = any(a in ("--force", "-f") for a in rest)
    rest = [a for a in rest if a not in ("--force", "-f")]

    if cmd == "help":
        return _help()

    if cmd == "init":
        path = rest[0] if rest else "."
        Project.init(path)
        print(f"✓ initialized semi-git in {path} (.sgt/ + git)")
        return 0

    if cmd == "mcp":
        from sgt.mcp import serve

        serve(rest[0] if rest else repo)  # stdio MCP server for coding-agent clients
        return 0

    if cmd == "graph":
        return _graph(repo)

    if cmd == "status":
        return _status(repo)

    if cmd == "show":
        if not rest:
            print("usage: sgt show <ref>")
            return 2
        return _show(repo, " ".join(rest))

    if cmd == "sync":
        assume_yes = any(a in ("--yes", "-y") for a in rest)
        return _sync(repo, assume_yes)

    if cmd == "checkpoint":
        assume_yes = any(a in ("--yes", "-y") for a in rest)
        intent = _opt_value(rest, "--intent")
        fulfills = _opt_value(rest, "--fulfills")
        return _checkpoint(repo, assume_yes, intent, fulfills)

    if cmd == "revert":
        emit = "--emit" in rest
        rest = [a for a in rest if a != "--emit"]
        if not rest:
            print("usage: sgt revert [--emit] <ref>")
            return 2
        return _print_report(_orchestrator(repo, force=force).revert(" ".join(rest), emit=emit))

    if cmd == "switch":
        emit = "--emit" in rest
        rest = [a for a in rest if a != "--emit"]
        if len(rest) < 2 or rest[-1] not in ("on", "off"):
            print("usage: sgt switch [--emit] <ref> on|off")
            return 2
        on = rest[-1] == "on"
        return _print_report(_orchestrator(repo, force=force).switch(" ".join(rest[:-1]), on, emit=emit))

    if cmd == "reconcile":
        ref = " ".join(rest) if rest else None
        return _print_report(_orchestrator(repo).reconcile(ref))

    if cmd == "plan":
        rest = [a for a in rest if a not in ("--yes", "-y")]  # reserved for a future plan checkpoint
        if not rest:
            print('usage: sgt plan [--force] "<intent>"')
            return 2
        return _print_report(_orchestrator(repo, force=force).plan(" ".join(rest)))

    return _help()


def _opt_value(args: list[str], flag: str) -> str | None:
    """Return the value following ``flag`` (e.g. ``--intent "..."``), or None if absent."""
    if flag in args:
        i = args.index(flag)
        if i + 1 < len(args):
            return args[i + 1]
    return None


def _print_sync(rep, verb: str) -> int:
    icon = "✓" if rep.ok else "✗"
    print(f"{icon} [{verb}] — {rep.message}")
    for nid in rep.landed:
        print(f"    new node: {nid}")
    for nid in getattr(rep, "fulfilled", []):
        print(f"    fulfilled: {nid}")
    for nid in rep.extended:
        print(f"    extended: {nid}")
    for nid in rep.quarantined:
        print(f"    quarantined: {nid}")
    for note in rep.notes:
        print(f"    ⚠ {note}")
    return 0 if rep.ok else 1


def _sync(repo: str, assume_yes: bool) -> int:
    from sgt.orchestrate.sync import run_sync

    proj = Project.open(repo)
    confirm = (lambda clusters: True) if assume_yes else confirm_sync
    return _print_sync(run_sync(proj, repo_path=repo, confirm=confirm), "sync")


def _checkpoint(repo: str, assume_yes: bool, intent: str | None, fulfills: str | None) -> int:
    """Distill on-disk edits into the graph; optionally fulfill a PLANNED node (--fulfills)."""
    from sgt.agents.distill import fallback_cluster
    from sgt.agents.resolve import resolve_ref
    from sgt.orchestrate.sync import run_sync

    proj = Project.open(repo)
    fulfills_id = None
    if fulfills:
        r = resolve_ref(proj.graph, fulfills)
        if r.node_id is None:
            print(f"✗ [checkpoint] — could not resolve --fulfills {fulfills!r} ({r.kind})")
            return 1
        fulfills_id = r.node_id

    # A declared --intent labels the nodes directly via the deterministic clusterer (no LLM);
    # without one, fall back to the default LLM/deterministic clustering. --fulfills skips
    # clustering entirely inside run_sync.
    clusterer = None
    if intent and not fulfills_id:
        def clusterer(effects, project):  # noqa: E306
            clusters = fallback_cluster(effects, project)
            for c in clusters:
                c.intent = intent
            return clusters

    confirm = (lambda clusters: True) if assume_yes else confirm_sync
    rep = run_sync(proj, repo_path=repo, clusterer=clusterer, confirm=confirm,
                   fulfills=fulfills_id, intent=intent)
    return _print_sync(rep, "checkpoint")


def _graph(repo: str) -> int:
    proj = Project.open(repo)
    nodes = proj.graph.nodes()
    if not nodes:
        print("(empty graph — run `sgt plan \"...\"` to plan a feature)")
        return 0
    print("Semantic graph:")
    for n in nodes:
        status = "" if n.status.value == "active" else f" [{n.status.value}]"
        deps = proj.graph.successors(n.id)
        dep_str = f"  → depends on {', '.join(deps)}" if deps else ""
        print(f"  {n.id} [{n.kind.value}]{status}: {n.intent[:70]}{dep_str}")
        w = proj.witnesses.get(n.id)
        if w:
            print(f"      ⚠ quarantined — {w.get('reason', '?')}; held: {', '.join(w.get('held', []))}")
    return 0


def _status(repo: str) -> int:
    from sgt.effects.model import EffectError

    proj = Project.open(repo)
    try:
        cb = proj.materialize()
    except EffectError as ex:
        print(f"nodes: {len(proj.graph.nodes())}  (cannot materialize: {ex})")
        return 1
    print(f"nodes: {len(proj.graph.nodes())}  files: {len(cb)}  "
          f"effects: {sum(len(b) for b in proj.bundles.values())}")
    for path in sorted(cb):
        print(f"  {path} ({len(cb[path].splitlines())} lines)")
    return 0


def _show(repo: str, ref: str) -> int:
    from sgt.agents.resolve import resolve_ref

    proj = Project.open(repo)
    r = resolve_ref(proj.graph, ref)
    if r.node_id is None:
        print(f"could not resolve {ref!r} ({r.kind}"
              + (f": {', '.join(r.matches)}" if r.matches else "") + ")")
        return 1
    n = proj.graph.get(r.node_id)
    print(f"{n.id} [{n.kind.value}] {n.status.value}")
    print(f"  intent: {n.intent}")
    for prior in n.provenance:
        print(f"  provenance: {prior}")
    print(f"  depends on: {', '.join(proj.graph.successors(n.id)) or '(none)'}")
    print(f"  dependents: {', '.join(proj.graph.predecessors(n.id)) or '(none)'}")
    print(f"  commits: {', '.join(c[:8] for c in n.commit_ids) or '(none)'}")
    w = proj.witnesses.get(n.id)
    if w:
        print(f"  ⚠ quarantined — reason: {w.get('reason', '?')}")
        print(f"    held: {', '.join(w.get('held', [])) or '(none)'}")
    print("  effects:")
    for e in proj.bundles.get(n.id, []):
        print(f"    - {e.op.value} {e.target} ({e.file})")
    return 0


def _help() -> int:
    print(
        "sgt — semantic feature-level version control\n\n"
        "  sgt init [path]            initialize .sgt + git\n"
        '  sgt plan "<intent>"        decompose an intent into reviewable PLANNED nodes (no code)\n'
        '  sgt "<intent>"             shorthand for `sgt plan`\n'
        "  sgt sync [--yes]           distill out-of-band edits back into the graph\n"
        '  sgt checkpoint [--intent "..."] [--fulfills <ref>]\n'
        "                             record your edits as a node; --fulfills lands them on a PLANNED node\n"
        "  sgt revert [--emit] <ref>  remove a feature (by closure); --emit previews, writes nothing\n"
        "  sgt switch [--emit] <ref> on|off   suspend / restore a feature (--emit previews)\n"
        "  sgt reconcile [<ref>]      re-gate pending quarantine(s); resolve any that now commute\n"
        "  sgt mcp [path]             run the MCP stdio server for coding-agent clients\n"
        "  (mutating verbs take --force to overwrite out-of-band changes)\n"
        "  sgt show <ref>             inspect a node\n"
        "  sgt graph                  print the semantic DAG\n"
        "  sgt status                 summarize state\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
