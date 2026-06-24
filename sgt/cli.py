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

_VERBS = {"init", "plan", "sync", "checkpoint", "revert", "switch", "reconcile",
          "show", "graph", "status", "blame", "export", "emit",
          "decisions", "compose", "tag", "tui", "mcp", "help"}


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
    # `--json` switches the read verbs to the canonical machine-readable projection (sgt.api),
    # which the VSCode extension and TUI consume. Stripped here so verb parsing is unaffected.
    as_json = "--json" in rest
    rest = [a for a in rest if a != "--json"]

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
        return _graph(repo, as_json)

    if cmd == "status":
        return _status(repo, as_json)

    if cmd == "blame":
        if not rest:
            print("usage: sgt blame [--json] <file>")
            return 2
        return _blame(repo, " ".join(rest), as_json)

    if cmd == "export":
        return _export(repo)

    if cmd == "decisions":
        return _decisions(repo, rest, as_json)

    if cmd == "compose":
        if len(rest) < 2:
            print("usage: sgt compose <feature> <decision-id>")
            return 2
        return _print_report(_orchestrator(repo).compose(rest[0], rest[1]))

    if cmd == "tag":
        if not rest:
            print("usage: sgt tag <name>")
            return 2
        return _print_report(_orchestrator(repo).tag(rest[0]))

    if cmd == "emit":
        # `sgt emit revert <ref>` | `sgt emit switch <ref> on|off` — structured dry-run for a UI.
        if len(rest) < 2 or rest[0] not in ("revert", "switch"):
            print("usage: sgt emit revert|switch <ref> [on|off]")
            return 2
        action = rest[0]
        on = True
        body = rest[1:]
        if action == "switch" and body and body[-1] in ("on", "off"):
            on = body[-1] == "on"
            body = body[:-1]
        payload = _orchestrator(repo).emit_payload(action, " ".join(body), on=on)
        return _emit_json(payload)

    if cmd == "tui":
        return _tui(repo)

    if cmd == "show":
        if not rest:
            print("usage: sgt show <ref>")
            return 2
        return _show(repo, " ".join(rest), as_json)

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
    for nid in getattr(rep, "swept", []):
        print(f"    swept (superseded): {nid}")
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


def _emit_json(payload) -> int:
    import json

    print(json.dumps(payload, indent=2))
    return 1 if isinstance(payload, dict) and "error" in payload else 0


def _graph(repo: str, as_json: bool = False) -> int:
    if as_json:
        from sgt.api import graph_view

        return _emit_json(graph_view(Project.open(repo)))
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


def _status(repo: str, as_json: bool = False) -> int:
    from sgt.effects.model import EffectError

    if as_json:
        from sgt.api import status_view

        return _emit_json(status_view(Project.open(repo)))
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


def _blame(repo: str, file: str, as_json: bool = False) -> int:
    from sgt.api import blame_view

    view = blame_view(Project.open(repo), file)
    if as_json:
        return _emit_json(view)
    if "error" in view:
        print(f"✗ {view['error']}")
        return 1
    print(f"semantic blame — {file}" + ("  (⚠ working tree has drifted)" if view["drift"] else ""))
    for s in view["spans"]:
        nid = s["node_id"]
        label = view["nodes"].get(nid, {}).get("intent", "")[:54] if nid else "(unattributed)"
        rng = f"{s['start']}" if s["start"] == s["end"] else f"{s['start']}-{s['end']}"
        print(f"  L{rng:<9} {nid or '—':<10} {label}")
    return 0


def _export(repo: str) -> int:
    from sgt.api import export_view

    return _emit_json(export_view(Project.open(repo)))


def _decisions(repo: str, rest: list[str], as_json: bool) -> int:
    """The decision DAG. `sgt decisions [--json]` → the graph; `decisions frontier` → the frontier."""
    from sgt.api import decision_graph_view, frontier_view

    if rest and rest[0] == "frontier":
        view = frontier_view(Project.open(repo))
        if as_json:
            return _emit_json(view)
        print(f"{len(view['lanes'])} lanes in force: "
              + ", ".join(f"{f}={view['selection'].get(f, '-')}" for f in view["lanes"]))
        return 0

    if rest and rest[0] == "diff":
        if len(rest) < 3:
            print("usage: sgt decisions diff <ref-a> <ref-b>   (ref = HEAD or a tag name)")
            return 2
        return _emit_json(_orchestrator(repo).diff(rest[1], rest[2]))

    if rest and rest[0] == "blast":
        if len(rest) < 2:
            print("usage: sgt decisions blast <decision-id>")
            return 2
        return _emit_json(_orchestrator(repo).blast_radius(rest[1]))

    if rest and rest[0] == "distill":
        # LLM rationale distillation (Context/Consequence/Alternatives); offline = no-op.
        from sgt.decisions.distill import distill_all

        only = rest[1] if len(rest) > 1 and not rest[1].startswith("-") else None
        n = distill_all(Project.open(repo), only=only, overwrite="--force" in rest)
        print(f"distilled rationale for {n} decision(s)" if n
              else "nothing distilled (no API key, or all decisions already have rationale)")
        return 0

    view = decision_graph_view(Project.open(repo))
    if as_json:
        return _emit_json(view)
    bo = sum(1 for e in view["edges"] if e["type"] == "builds-on")
    lc = sum(1 for e in view["edges"] if e["type"] in ("revises", "fork"))
    print(f"{view['count']} decisions, {len(view['frontier'])} lanes in force, "
          f"{lc} lifecycle + {bo} derived builds-on edges, {len(view['clash'])} clashes")
    return 0


def _tui(repo: str) -> int:
    try:
        from sgt.tui.app import run as run_tui
    except ModuleNotFoundError as ex:
        if ex.name and ex.name.split(".")[0] == "textual":
            print("✗ the TUI needs Textual — install it with:  uv pip install 'semi-git[tui]'")
            return 1
        raise
    run_tui(repo)
    return 0


def _show(repo: str, ref: str, as_json: bool = False) -> int:
    from sgt.agents.resolve import resolve_ref

    if as_json:
        from sgt.api import show_view

        return _emit_json(show_view(Project.open(repo), ref))
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
        "  sgt blame <file>           which feature owns each line of a file (semantic blame)\n"
        "  sgt export                 dump the whole graph as JSON (nodes, edges, effects)\n"
        "  sgt map [--json]           the deterministic code-entity map (whole repo)\n"
        "  sgt timeframe <n> [--json] the map as of checkpoint ordinal n (the scrubber frame)\n"
        "  sgt tui                    open the terminal UI (needs `semi-git[tui]`)\n"
        "  (read verbs take --json for the machine-readable projection)\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
