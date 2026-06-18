"""The `sgt` command surface (origin R5, R6).

Two tiers: a freeform front door (`sgt do "..."`, or just `sgt "..."`) routed through
the classifier, and explicit graph verbs (`revert`, `modify`, `switch`, `show`,
`graph`) whose ref argument is resolved fuzzy-to-exact. Read-only verbs work without
the OpenAI key; `do`/`modify` need it.
"""

from __future__ import annotations

import sys

from sgt.project import Project

_VERBS = {"init", "do", "revert", "modify", "switch", "show", "graph", "status", "help"}


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


def confirm_plan(graph) -> bool:
    """Render a fan-out plan and ask the user to proceed (the checkpoint, R29)."""
    print("Proposed fan-out plan:")
    for i, layer in enumerate(graph.layers(), 1):
        print(f"  layer {i} (parallel):")
        for t in layer:
            needs = f"  [needs: {', '.join(t.needs)}]" if t.needs else ""
            print(f"    - {t.key}: {t.intent}{needs}")
    try:
        return input("Proceed with fan-out? [y/N] ").strip().lower() in ("y", "yes")
    except EOFError:
        return False


def _orchestrator(repo: str, assume_yes: bool = False):
    from sgt.adapter.openai_agent import OpenAICodingAgent
    from sgt.orchestrate.loop import Orchestrator

    proj = Project.open(repo)
    agent = OpenAICodingAgent(repo_path=repo)
    confirm = (lambda graph: True) if assume_yes else confirm_plan
    return Orchestrator(proj, agent, repo_path=repo, confirm=confirm)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        return _help()

    cmd = argv[0]
    # `sgt "freeform prompt"` — first arg is not a known verb -> treat as `do`.
    if cmd not in _VERBS:
        cmd, rest = "do", argv
    else:
        rest = argv[1:]

    repo = "."

    if cmd == "help":
        return _help()

    if cmd == "init":
        path = rest[0] if rest else "."
        Project.init(path)
        print(f"✓ initialized semi-git in {path} (.sgt/ + git)")
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

    if cmd == "revert":
        if not rest:
            print("usage: sgt revert <ref>")
            return 2
        return _print_report(_orchestrator(repo).revert(" ".join(rest)))

    if cmd == "switch":
        if len(rest) < 2 or rest[-1] not in ("on", "off"):
            print("usage: sgt switch <ref> on|off")
            return 2
        on = rest[-1] == "on"
        return _print_report(_orchestrator(repo).switch(" ".join(rest[:-1]), on))

    if cmd == "modify":
        if len(rest) < 2:
            print('usage: sgt modify <ref> "<change>"')
            return 2
        return _print_report(_orchestrator(repo).modify(rest[0], " ".join(rest[1:])))

    if cmd == "do":
        assume_yes = any(a in ("--yes", "-y") for a in rest)
        rest = [a for a in rest if a not in ("--yes", "-y")]
        if not rest:
            print('usage: sgt do [--yes] "<prompt>"')
            return 2
        return _print_report(_orchestrator(repo, assume_yes).ingest(" ".join(rest)))

    return _help()


def _graph(repo: str) -> int:
    proj = Project.open(repo)
    nodes = proj.graph.nodes()
    if not nodes:
        print("(empty graph — run `sgt do \"...\"` to add a feature)")
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
    proj = Project.open(repo)
    cb = proj.materialize()
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
        '  sgt do [--yes] "<prompt>"  add/change code by intent (--yes skips the fan-out checkpoint)\n'
        '  sgt "<prompt>"             shorthand for `sgt do`\n'
        '  sgt modify <ref> "<chg>"   iterate on an existing feature\n'
        "  sgt revert <ref>           remove a feature (by dependency closure)\n"
        "  sgt switch <ref> on|off    suspend / restore a feature\n"
        "  sgt show <ref>             inspect a node\n"
        "  sgt graph                  print the semantic DAG\n"
        "  sgt status                 summarize state\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
