#!/usr/bin/env python3
"""Refuse to ship a study repository whose feature graph has lost symbols.

    scripts/check_graph_integrity.py <repo>

The clustering universe is every alive symbol in the ideal's frontier
(`sgt.lens.cluster.alive_nodes`). Every one of those should end up a member of
exactly one leaf feature. Sometimes they do not: a build can produce a feature
whose members are entirely `__residue__`/`__anchor__` bookkeeping sentinels -- a
husk -- while the real symbols it should own are attached nowhere.

A husk is invisible until it matters, and then it is very visible:

  * `sgt show <that feature>` reads "11 edits, 0 symbols in 0 files" and offers
    a revert that reports no affected symbols.
  * `sgt log --tree` omits the feature entirely, so the participant's list of
    features silently disagrees with the count above it.
  * `sgt show shipping.py::shipping_cost` answers "not a known symbol".
  * `sgt find` still ranks all of it correctly, because it matches saves and
    the generated description, so search hides the hole rather than exposing it.

It is intermittent -- the same sixteen-commit repository built twice produced a
clean four-feature graph once and a three-feature graph missing eleven of its
twenty symbols the other time. Intermittent plus silent plus one-build-per-
participant means the failure mode is a single ruined session that nobody can
reconstruct afterwards, which is worth a few seconds at build time to prevent.

Exits non-zero and prints what is missing. It does not try to repair anything:
the repair is to rebuild, and a build script that silently retried would hide
how often this happens, which is itself something we want to know.
"""
import sys
from pathlib import Path


def _plural(n, noun):
    """`3 symbols`, `1 symbol`. Not `1 symbol(s)` -- the same rule `sgt.tui.graph.plural` applies to
    every count sgt prints. Duplicated rather than imported because this script runs against the
    *built* copy's interpreter and must not depend on importing sgt."""
    return "%d %s" % (n, noun) if n == 1 else "%d %ss" % (n, noun)


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: check_graph_integrity.py <repo>", file=sys.stderr)
        return 2
    repo = Path(argv[1])

    try:
        from sgt.core import opindex
        from sgt.core.lens import current_ideal
        from sgt.core.op import is_behavioral, is_bottom
        from sgt.lens import cluster, tree
    except ImportError as e:
        # Run with an interpreter that cannot see sgt. Worth saying plainly:
        # a traceback here reads like the graph is broken, which is the exact
        # confusion this script exists to remove.
        print("cannot import sgt with %s (%s) — run this with the interpreter sgt is"
              " installed into" % (sys.executable, e), file=sys.stderr)
        return 2

    ops = list(opindex.index_ops(repo))
    ideal = current_ideal(repo)
    alive = {
        s for s in cluster.alive_nodes(ideal, ops)
        if "__residue__" not in s and "__anchor__" not in s
    }

    # Check the ideal before the tree. In the one bad build we caught, the tree
    # was faithful to an ideal that had already lost the symbols: fourteen
    # symbols that ops demonstrably touched, and that were never tombstoned,
    # were simply absent from the frontier -- so `alive` was 9 where it should
    # have been 20, and comparing the tree against `alive` alone called that
    # graph complete. A symbol an op touched is either in the frontier or
    # explicitly dead; anything else is the frontier having been computed over a
    # subset of the op log.
    frontier = ideal.frontier(ops)
    by_id = {o.id: o for o in ops}

    # `is_behavioral`, so nested symbols (`waitlist_for._key`) do not count: they are
    # subsumed by the entity that contains them and were never clustering nodes.
    touched = {s for o in ops for s in o.footprint if is_behavioral(s)}
    tombstoned = {
        s for s in touched
        if s in frontier and is_bottom(by_id[frontier[s]].footprint[s][1])
    }

    # A symbol can be absent from the frontier for a legitimate reason: work that was
    # reverted leaves no op in the ideal to carry it, and its file is gone from the
    # tree. Only flag one whose FILE still exists, which is the shape of the real
    # defect -- code sitting in the working tree that the graph cannot see.
    on_disk = {
        s for s in touched - set(frontier)
        if (repo / s.partition("::")[0]).exists()
    }
    vanished = sorted(on_disk)

    nodes = tree.load(repo).get("nodes") or {}
    if not nodes:
        print("no feature tree at all — run `sgt log --refresh`", file=sys.stderr)
        return 1

    # Leaves only. An interior subsystem repeats its descendants' members, so
    # counting it would mask a leaf that owns nothing.
    leaves = {nid: nd for nid, nd in nodes.items() if not nd.get("children")}
    placed: set[str] = set()
    husks: list[str] = []
    for nid, nd in leaves.items():
        real = {
            m for m in nd.get("members", [])
            if "__residue__" not in m and "__anchor__" not in m
        }
        placed |= real
        if nd.get("members") and not real:
            husks.append("%s %r" % (nid[:12], nd.get("label", "")))

    missing = sorted(alive - placed)

    # Two severities, deliberately.
    #
    # A husk, or a symbol the frontier lost, is the graph SAYING something false:
    # a feature that owns nothing while offering a revert, or code in the working
    # tree the graph cannot see at all. Those block the build.
    #
    # A symbol that is in the hierarchy but in no leaf is the graph being
    # INCOMPLETE rather than wrong -- it cannot be reached by feature, which is a
    # coverage gap worth knowing about, but nothing misreports it and `sgt show`
    # still resolves it. Reported loudly, does not block, because refusing to
    # ship over it would stop the study without making anything more truthful.
    #
    # Both study projects sit at 102 of 102 today. They did not: confplan was
    # missing nine, all five top-level symbols of `slots.py` and its tests, which
    # is the module request one asks about. That was one aliased id in
    # `_apply_assign_pins`, not a tolerance to design around -- so if this tier
    # starts reporting whole modules again, treat it as a bug to find rather than
    # a number to accept.
    ok = not husks and not vanished

    print("  %s touched, %d alive, %d tombstoned, %d placed in %s"
          % (_plural(len(touched), "symbol"), len(alive), len(tombstoned),
             len(placed & alive), _plural(len(leaves), "leaf feature")))
    if vanished:
        print("  %s an op touched are in no frontier and were never deleted:"
              % _plural(len(vanished), "symbol"), file=sys.stderr)
        for s in vanished:
            print("    %s" % s, file=sys.stderr)
    if husks:
        print("  %s made only of bookkeeping sentinels:"
              % _plural(len(husks), "feature"), file=sys.stderr)
        for h in husks:
            print("    %s" % h, file=sys.stderr)
    if missing:
        print("  NOTE: %s in the tree are in no leaf feature, so no"
              " feature-scoped verb reaches them (not a blocker):"
              % _plural(len(missing), "alive symbol"), file=sys.stderr)
        for s in missing:
            print("    %s" % s, file=sys.stderr)
    if not ok:
        print("  The graph is degenerate. Rebuild this repo; do not hand it to a"
              " participant.", file=sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
