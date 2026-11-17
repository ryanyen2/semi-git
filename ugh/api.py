"""The canonical JSON projection of the operation-ideal kernel — one schema, many clients.

Every machine-readable surface (the CLI's ``--json`` mode, the MCP server, and any future UI)
renders the *same* dicts produced here, so the views can never drift apart (R21; schema changes
are additive-only). Reads are offline (no LLM/network dependency) and pure over an *already-mined*
store -- `ugh.core.lens.get` (mine-on-contact) is the caller's job, kept out of these functions so
they stay side-effect-free. `ugh.core.*` is imported lazily inside each function so `import
ugh.api` never pulls in the kernel's tree-sitter dependency just to define these shapes.

Shapes (stable; additive changes only):

* ``oplog_view``        — the mined operation DAG: every op's id, kind, footprint, provenance,
  structured attribution (D7: session/agent/plan per witnessing sha), intent.
* ``state_view``        — the current ref's ideal: frontier, coverage, entity-granularity fraction,
  and the async oracle's verdict (U9).
* ``map_view``          — the U13 feature tree: every node's id/label/kind/parent/children/op_count,
  the roots, the cross-feature structural dependency edges, and the last build's Greene identity
  events (birth/death/merge/split/continuation).
* ``history_view``      — the feature-map webview's commit-index axis: every mined commit in order,
  and every op's derived kind/feature/commit-index, for Gantt-style lifebars.
* ``grid_view``         — the canonical lane×commit cell join (U1): every (feature, commit) cell
  that carries ops, the commit axis, active-plan ghost cells, and per-commit mining-fidelity marks
  — the one join every grid surface (CLI `ugh log`, VS Code webview) reads, computed once.
* ``status_view``       — a kernel-backed summary: file/symbol/feature counts, coverage fraction,
  the oracle's overall status, and working-tree drift from `code(current_ideal)`.
* ``plan_view``         — the U14 plan review surface: every active plan session's steps (with
  matched-step file/line spans) plus the pure checkpoint preview (candidate step<->op groups and
  drift op-ids).
* ``drift_view``        — the U14 "what extra happened" query: every op not predicted by any
  active plan session, with its kind, footprint, and current file/line spans.
* ``sync_view``         — the U15 `ugh sync` result: ops merged in, forks surfaced (with the
  `ugh resolve` remedy), pin contradictions, and tree identity events.
* ``forks_view``        — the U20 open same-symbol forks recorded in committed `.sgt/forks.json`,
  each with its two tips and the `ugh resolve` remedy (divergence-as-state, C4).
* ``intent_view``       — the U6 intent-clustering overlay: every commit-keyed `IntentAtom` (rung
  0/1, recomputed on read) and every persisted LLM-named `theme` (rung 2), each with its dependency-graph-backed `tier` (coupled/co-changed/thematic) and
  cross-feature `feature_span` -- the "why" axis alongside `map_view`'s structural "what" axis.
* ``fold_view``         — a side-effect-free fold of an arbitrary frontier (a ref's ideal, every op
  at or before a commit-index position, or an explicit op-id set): `code(I)` plus that exact
  op-set's oracle verdict, without checking anything out. Powers a draggable playhead and fork-tip
  diffs; reports `forked`/`forks` instead of folding when the candidate isn't fork-free.
* ``fork_detail_view``  — per-tip folded images for one open fork's symbol, so a resolution UI can
  diff both tips' full file content without a separate frontier query per tip.
"""

from __future__ import annotations


def plural(n: int, noun: str) -> str:
    """`3 symbols`, `1 symbol`, for the regular nouns ugh counts."""
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"


def oplog_view(repo, *, full: bool = False, limit: int = 100, offset: int = 0) -> dict:
    """The mined operation DAG: every stored op with its id, derived kind, footprint (each
    symbol's before->after version), witnessing-commit provenance, and intent if any.
    Deterministic order -- ops sorted by content-address id, every nested list sorted -- so set
    iteration never leaks into the projection.

    Compact by default (R21's context-economy contract): `{count, kinds, truncated, ops}`, each
    op reduced to `{id, kind, symbols, intent}` and sliced by `offset`/`limit` -- an agent's
    default read never pays for every op's before/after versions, provenance, and attribution.
    `full=True` restores today's per-op payload, unpaged. Neither mode touches `op.images` --
    this view never needed it -- so both source from the footprint-only `opindex` sidecar."""
    from ugh.core import opindex

    ops = sorted(opindex.index_ops(repo), key=lambda op: op.id)
    if full:
        return {
            "ops": [
                {
                    "id": op.id,
                    "kind": op.kind,
                    "footprint": [
                        {"symbol": sym, "before": before, "after": after}
                        for sym, (before, after) in sorted(op.footprint.items())
                    ],
                    "provenance": sorted(op.provenance),
                    "attribution": _attribution_entries(op),
                    "intent": op.intent,
                }
                for op in ops
            ],
            "count": len(ops),
        }

    kinds: dict[str, int] = {}
    for op in ops:
        kinds[op.kind] = kinds.get(op.kind, 0) + 1
    window = ops[offset:offset + limit]
    return {
        "count": len(ops),
        "kinds": kinds,
        # Whether ops remain *beyond this window* -- not just whether the window is smaller
        # than the total (that's also true, harmlessly, on a full last page once offset > 0).
        "truncated": offset + len(window) < len(ops),
        "ops": [
            {"id": op.id, "kind": op.kind, "symbols": sorted(op.footprint), "intent": op.intent}
            for op in window
        ],
    }


def _attribution_entries(op) -> list[dict]:
    """An op's structured provenance (D7) as a stable, sorted-by-sha list of `{sha, session?,
    agent?, plan?}` dicts, omitting None fields -- additive to `provenance` (the bare sha list),
    never replacing it. `[]` for an op with no attribution."""
    return [
        {"sha": a.sha, **{f: getattr(a, f) for f in ("session", "agent", "plan") if getattr(a, f) is not None}}
        for a in sorted(op.attribution, key=lambda a: a.sha)
    ]


def state_view(repo, *, full: bool = False) -> dict:
    """The current ref's ideal: its per-chain frontier (symbol -> tip op id), the paths
    `code(I)` covers, R7's entity-granularity coverage fraction, and the async oracle's verdict
    (U9) -- `oracle_verdict` is `None` until `ugh oracle run` has recorded one for this exact
    ideal, or always `None` when `oracle_configured` is False (no `.sgt/oracle.json`).

    Coverage-fraction definition (R7): of the paths present in the materialized tree, the
    fraction carried at *entity* granularity -- a path with at least one live top-level or nested
    entity symbol at the frontier (a parseable def/class/method ugh can revert or cherry-pick on
    its own) -- versus paths represented only by a whole-file pseudo-symbol or by module-level
    residue / layout facts (coarse, file-granularity coverage). `entity_paths` is that numerator
    as an explicit list, so `covered_paths` minus `entity_paths` is exactly the whole-file-only
    remainder. A ref with nothing covered reports 1.0 (vacuously: nothing is stuck at whole-file
    granularity).

    Compact by default: drops the per-chain `frontier` map and the `entity_paths` list (one
    entry per symbol/path -- unbounded on a large ideal) in favor of their counts.
    `covered_paths`/`coverage_fraction`/`oracle_*` stay in both modes -- `status_view` reads
    them, never `frontier`/`entity_paths`, so it can keep calling this at its default. `full=True`
    restores `frontier` and `entity_paths`."""
    from ugh.config import load_oracle_config
    from ugh.core import opindex
    from ugh.core.fold import _symbol_kind
    from ugh.core.lens import ideal_for_ref
    from ugh.core.oracle import verdict_for
    from ugh.core.op import is_bottom

    ops = opindex.index_ops(repo)
    ideal = ideal_for_ref(repo, "HEAD")
    frontier = ideal.frontier(ops)
    by_id = {op.id: op for op in ops}

    covered = ideal.covered_paths(ops)
    entity_paths: set[str] = set()
    for sym, op_id in frontier.items():
        after = by_id[op_id].footprint[sym][1]
        if not is_bottom(after) and _symbol_kind(sym) in ("entity", "nested"):
            entity_paths.add(sym.split("::", 1)[0])

    from ugh.core import tiers

    oracle_configured = load_oracle_config(repo) is not None
    base = {
        "covered_paths": sorted(covered),
        "coverage_fraction": (len(entity_paths) / len(covered)) if covered else 1.0,
        "derived_paths": sorted(p for p in covered if tiers.is_derived(p)),  # S4/U27
        "oracle_configured": oracle_configured,
        "oracle_verdict": verdict_for(repo, ideal) if oracle_configured else None,
    }
    if full:
        return {
            "frontier": {sym: frontier[sym] for sym in sorted(frontier)},
            "entity_paths": sorted(entity_paths),
            **base,
        }
    return {
        "frontier_count": len(frontier),
        "entity_path_count": len(entity_paths),
        **base,
    }


_REVERSIBLE_VERBS = frozenset({"revert", "restore"})


def _reversible(verb: str) -> bool:
    """Whether a verb's effect can be walked back with `ugh undo` -- the ideal-edit verbs -- or is
    a shared/one-way advance (`sync`, default False)."""
    return verb in _REVERSIBLE_VERBS


def so_what_for(projected: dict) -> str:
    """The one-line consequence of a sync or resolve preview -- what happens, whether it can be
    undone. Pure over the projection dict."""
    verb = projected.get("verb", "")
    target = projected.get("target")
    # `__anchor__`/`__residue__` are the miner's own bookkeeping symbols, and they sort ahead of
    # the real one in the same file. Naming one here put "b.py::__anchor__::user will break" in
    # front of a user, which reads as an internal leak rather than a consequence, so the fallback
    # skips them and only uses one if a preview genuinely touched nothing else.
    touched = projected.get("affected_symbols") or []
    real = [s for s in touched if "::__" not in s]
    primary = (
        target if target and "::" in target
        else (real or touched or [None])[0] or target or "this"
    )
    undo = ("Undo-able." if projected.get("reversible", _reversible(verb))
            else "Not auto-undoable — review carefully.")

    if verb == "sync":
        src = f"{projected.get('remote') or 'the remote'}/{projected.get('target') or 'the branch'}"
        if projected.get("up_to_date"):
            return f"Already up to date with {src} — nothing to fold in."
        n = projected.get("ops_added", 0)
        forks = projected.get("forks") or []
        if forks:
            return (f"Folds in {n} op(s) from {src}; {len(forks)} fork(s) surface for you to "
                    f"resolve — no work is lost, they wait at the common ancestor. {undo}")
        return f"Folds in {n} op(s) from {src} — footprint-disjoint, no forks. {undo}"

    if verb == "resolve":
        sym = projected.get("target") or "the symbol"
        if not projected.get("clean", True):
            return f"Can't resolve {sym} yet — {projected.get('error') or 'no open fork'}."
        return (f"Resolves the fork on {sym}: takes your merged edit, runs the oracle, then lands "
                f"it (closes the fork). {undo}")

    if not projected.get("ok", True):
        if projected.get("forked"):
            return (f"Won't apply — {verb} of {primary} would fork it. "
                    f"Resolve the fork first (ugh resolve {primary}).")
        return f"Won't apply — {projected.get('message') or 'refused'}."

    return f"{verb} {primary}. {undo}"


def _side_entity_spans(path: str, source: bytes) -> list[dict]:
    """One side of a verb projection, as `{symbol, kind, start_line, end_line}` in document order.

    The same tree-sitter extraction `_entity_line_spans` runs against the current ideal, pointed at
    an arbitrary folded text instead. A client showing "what did this chapter change" has a
    before/after pair and wants the answer per entity, not per file -- and it cannot find where a
    function begins by looking at the text, which is the whole reason this repo parses in the first
    place. Both sides are spanned because the two texts have different line numbers: a caller maps
    a line on either side to the entity that owns it. Never raises (`extract_file` answers `[]` for
    an unsupported or unparseable file), so a projection stays complete even where it has no
    entities to name.
    """
    from ugh.entities.extract import extract_file

    return [
        {"symbol": e.id, "kind": e.kind, "start_line": e.start_line, "end_line": e.end_line}
        for e in sorted(extract_file(path, source), key=lambda e: (e.start_line, e.id))
    ]


def _project_sync_preview(repo, plan) -> dict:
    """Project a `ugh.core.sync.SyncPlan` dry-run into the shared consequence shape. Unlike `land`, a
    sync fork does not block -- the fork-free part still merges and the fork surfaces as state -- so
    `forks` here is *what would surface*, and `ok` is False only to flag that attention is needed,
    not that nothing happens. No clean-tree precondition, so `clean` is always True."""
    projected = {
        "ok": not plan.forks,
        "verb": "sync",
        "target": plan.branch,
        "remote": plan.remote,
        "affected_symbols": [f[0] for f in plan.forks],
        "forked": bool(plan.forks),
        "files": {},
        "message": "",
        "fallout": [],
        "carry_count": 0,
        "reversible": False,
        "clean": True,
        "up_to_date": plan.up_to_date,
        "ops_added": plan.ops_added,
        "forks": [list(t) for t in plan.forks],
        "pin_contradictions": [
            {"kind": c.kind, "members": list(c.members), "detail": c.detail}
            for c in plan.pin_contradictions
        ],
        "base_recovery": plan.base_recovery,
        "theirs_recovery": plan.theirs_recovery,
    }
    projected["so_what"] = so_what_for(projected)
    return projected


def sync_preview_view(repo, remote: str | None = None, branch: str | None = None) -> dict:
    """The dry-run consequence of `ugh sync [remote] [branch]` -- what folding the teammate's branch
    in *would* bring (incoming op count, any fork that would surface, recovery modes), computed
    side-effect-free (`sync.plan_sync`: `fetch -> ingest -> resolve`, no `materialize`, rolled back
    to leave no trace). A pure read the CLI shows as a feedforward pane before the merge lands."""
    from ugh.core import sync as sync_mod

    plan = sync_mod.plan_sync(repo, remote=remote, branch=branch)
    return _project_sync_preview(repo, plan)


def resolve_apply_preview_view(repo, symbol: str) -> dict:
    """The dry-run consequence of `ugh resolve <symbol> --apply`: take the edited tree's version,
    run the oracle, commit it, closing the fork. `clean=False` with a reason when there is no open
    fork for `symbol`. A pure read."""
    from ugh.config import load_oracle_config

    fork = next((f for f in forks_view(repo)["forks"] if f["symbol"] == symbol), None)
    error = None if fork is not None else f"no open fork for {symbol!r} — `ugh status` lists the open forks"
    clean = error is None

    projected = {
        "ok": clean,
        "verb": "resolve",
        "target": symbol,
        "affected_symbols": [symbol],
        "forked": False,
        "files": {},
        "message": error or "",
        "fallout": [],
        "carry_count": 0,
        "reversible": False,
        "clean": clean,
        "error": error,
        "oracle_configured": load_oracle_config(repo) is not None,
        "tips": list(fork["tips"]) if fork else [],
    }
    projected["so_what"] = so_what_for(projected)
    return projected


def map_view(repo) -> dict:
    """The feature tree (plan U13): a pure read of the last `ugh map`-built `.sgt/tree/tree.json`
    -- building/labeling/saving it is `ugh.lens.map.build_map`'s job, kept out of this read-only,
    dependency-light projection. Empty (`{"nodes": [], ...}`) if no tree has been built yet, so a
    UI can render "run `ugh map`" rather than erroring.

    Every node -- leaf (a `label_tree`/Greene-matched feature, `f-<op>` id) or internal (a
    structural subsystem grouping, build-local `N<n>` id) -- is emitted uniformly with
    `kind: "feature" | "subsystem"` distinguishing them; `op_count` is the number of ops
    `op_leaf` assigns to a feature, rolled up through subsystems. `edges` rolls the fused
    structural/co-change/scope coupling graph (`ugh.lens.tree.fused_graph`, the same signal
    `plan_split` reads) up to leaf-feature pairs -- the cross-feature dependency edges a
    visualization draws between features. Fully sorted for a stable projection."""
    from ugh.core import opindex
    from ugh.core.lens import current_ideal, sync_status
    from ugh.lens.tree import feature_edges, fused_graph
    from ugh.lens.tree import load as load_tree

    result = load_tree(repo)
    if result is None:
        return {
            "nodes": [],
            "roots": [],
            "identity_events": [],
            "feature_count": 0,
            "edges": [],
            "sync_status": sync_status(repo),
        }

    nodes = result["nodes"]
    op_leaf = result["op_leaf"]
    leaf_op_count: dict[str, int] = {}
    for leaf in op_leaf.values():
        leaf_op_count[leaf] = leaf_op_count.get(leaf, 0) + 1

    # The tree is a DAG: an authored feature (U6) can be spliced under more than one subsystem, so a
    # shared leaf is listed in several parents' `children` yet carries exactly one canonical `parent`.
    # Every projection below -- the emitted `children`, `op_count`, and the session rollup -- walks
    # only the *canonical* children (those whose `parent` points back here), so a shared feature is
    # rendered once, under its one parent. That keeps the feature tree and the workbench timeline
    # consistent; a raw double-listing otherwise double-counts the shared ops in every
    # ancestor's rollup and draws the feature under whichever parent a surface happens to visit first.
    def canonical_children(nid: str) -> list[str]:
        return [c for c in nodes[nid]["children"] if nodes[c]["parent"] == nid]

    def op_count(nid: str) -> int:
        children = canonical_children(nid)
        if not children:
            return leaf_op_count.get(nid, 0)
        return sum(op_count(c) for c in children)

    # Never touches op.images (only footprint/attribution below, and fused_graph's clustering
    # signals) -- the opindex sidecar, not Store.all_ops()'s images decode, is correct here.
    ops = opindex.index_ops(repo)
    by_id = {op.id: op for op in ops}
    leaf_sessions: dict[str, set[str]] = {}
    for op_id, leaf in op_leaf.items():
        op = by_id.get(op_id)
        if op is None:
            continue
        sessions = {a.session for a in op.attribution if a.session}
        if sessions:
            leaf_sessions.setdefault(leaf, set()).update(sessions)

    def node_sessions(nid: str) -> list[str]:
        """Every session (plan U30/D5) whose attributed ops sit under this node -- additive
        provenance rollup (plan U31, S7), same children-recursion shape as `op_count`."""
        children = canonical_children(nid)
        if not children:
            return sorted(leaf_sessions.get(nid, ()))
        merged: set[str] = set()
        for c in children:
            merged.update(node_sessions(c))
        return sorted(merged)

    # Authored features (U6/R3, KTD4) override the clustered proposal: where a user has authored a
    # feature over a leaf's symbols, that leaf shows the authored label + `af-` id, not the cluster's.
    # Guarded on presence so a repo with no authored features projects byte-identically to before.
    from ugh.lens.authored import load_authored
    from ugh.lens.label import fallback_label
    from ugh.lens.tree import _authored_leaf_claims
    authored_claims = _authored_leaf_claims(nodes, load_authored(repo))

    # `members` is what the clustering assigned to a node, which is not the same question as "what
    # does this feature's own work touch" -- and `ugh show` answers the second one (`_show_footprint`:
    # the feature's ops' footprints, minus the residue/anchor sentinels). The two disagree hard on
    # husks: `Section Waitlist` carries 5 members but its single op touches only sentinels, so the
    # tree printed `· 5 symbol(s)` next to a `ugh show` reading `0 symbols in 0 files` whose revert
    # removes nothing. A pilot participant picked that row to "remove the waitlist" off the map.
    # Computed the same way here so every surface reports one number per feature.
    # Kind-classified, not substring-matched: `__import__::` symbols are bookkeeping exactly like
    # residue/anchors (an import statement is not code a person recognizes as "the feature"), but
    # the import kind postdates this filter, so a lane holding ONLY a page file's import crumbs
    # counted as owning real symbols -- it survived the husk filter and the labeler named it after
    # the page, which put a phantom twin of the real page lane on the map ("Hourly Footfall" next
    # to the lane actually holding hourly.py::render).
    from ugh.core.op import _symbol_kind as _sym_kind
    own_symbols: dict[str, set[str]] = {}
    files_touched: dict[str, set[str]] = {}
    for op_id, leaf in op_leaf.items():
        op = by_id.get(op_id)
        if op is None:
            continue
        own_symbols.setdefault(leaf, set()).update(_named_symbols(op))
        files_touched.setdefault(leaf, set()).update(s.partition("::")[0] for s in op.footprint)
    # A file the parser finds no named entity in (the Sketchpad demo's `src/kinds/T.ts` is one
    # `register(...)` call) is mined as a HEAD residue plus its imports, so a feature made of such
    # files owned nothing by the rule above and every surface hid it as a husk -- while `ugh revert`
    # on it removed eleven edits. Such a feature owns those files at file level. Only files with no
    # entity in ANY leaf qualify, so a real husk (sentinel-only ops on a file whose entities live in
    # another lane) still reads as owning nothing.
    files_with_entities = {
        s.partition("::")[0] for syms in own_symbols.values() for s in syms
    } | {
        m.partition("::")[0] for nd in nodes.values() if not nd["children"]
        for m in nd.get("members", ()) if _sym_kind(m) in ("entity", "nested", "whole_file")
    }
    for leaf, files in files_touched.items():
        if not own_symbols.get(leaf):
            own_symbols[leaf] = {f for f in files if f not in files_with_entities}

    def _emit(nid: str, nd: dict) -> dict:
        # A node id is a content hash (`f-`/`af-`), never a name -- it must never reach a surface as
        # a label. A properly built tree labels every node (`tree.label_tree`), but a tree persisted
        # without that pass (or a node minted after it) can arrive here label-less or with the id
        # copied in; falling back to `nid` then printed the raw hash on the graph/grid ("unreadable").
        # Derive the same deterministic, offline name the labeler's own fallback uses instead.
        label = nd.get("label") or ""
        if not label or label == nid:
            label = fallback_label(nd.get("members", [])).label
        row = {
            "id": nid,
            "label": label,
            "kind": "feature" if not canonical_children(nid) else "subsystem",
            "parent": nd["parent"],
            "children": sorted(canonical_children(nid)),
            "size": nd["size"],
            "members": list(nd.get("members", [])),
            "own_symbols": sorted(own_symbols.get(nid, ())),
            "op_count": op_count(nid),
            "dir": nd.get("dir", ""),
            "why": nd.get("why", ""),
            "split_reason": nd.get("split_reason"),
            "sessions": node_sessions(nid),
        }
        claim = authored_claims.get(nid)
        if claim is not None:
            row["authored_id"] = claim.id
            if claim.label:  # empty = save-time cascade lane, unnamed; keep the clustered label
                row["label"] = claim.label
        return row

    emitted = [_emit(nid, nd) for nid, nd in sorted(nodes.items())]
    ideal = current_ideal(repo)
    _, fused = fused_graph(repo, ops, ideal)

    return {
        "nodes": emitted,
        "roots": sorted(result["roots"]),
        "identity_events": sorted(result.get("identity_events", []), key=lambda e: (e["event"], e["feature_id"])),
        "feature_count": sum(1 for nid in nodes if not canonical_children(nid)),
        "edges": feature_edges(nodes, fused),
        "sync_status": sync_status(repo),
    }


def history_view(repo, *, full: bool = False, limit: int = 200, offset: int = 0) -> dict:
    """The feature-map webview's commit-index axis: every mined commit in chronological order
    (`ugh.store.gitbind.GitBinding.history`, oldest-first), and every stored op's derived kind,
    feature (`op_leaf`, if a tree has been built), and `commit_index` -- the position in that
    chronological list of the *earliest* of the op's provenance commits that actually appears
    there. An op none of whose provenance commits are in `history()` (e.g. mined from a detached
    or since-rewritten commit) is omitted rather than assigned a misleading index.

    Compact by default: drops the full per-op `ops` list (unbounded on a large history) for
    `{commit_count, op_count, kinds, features, latest_commits}` -- `latest_commits` is the
    `offset`/`limit` window of the *most recent* commits (reverse-chronological), the summary an
    agent actually wants ("what happened recently"). `full=True` restores today's unpaged
    `{commits, ops}` -- required by `fold_view`'s internal use of the `ops`/`commit_index` axis."""
    from ugh.core import opindex
    from ugh.lens.tree import load as load_tree
    from ugh.store.gitbind import GitBinding

    gb = GitBinding(repo)
    # `history_meta` carries the committer time and the bookkeeping mark in the format of the walk
    # `history()` already does, so knowing which commits are ugh's own mechanics -- and when the
    # last save happened -- costs no extra git calls. Every commit keeps its index and its place on
    # the time axis (the grid, the fold frontier, and every op's `commit_index` are unchanged); only
    # human-facing lists drop them, instead of telling a developer that `ugh restore f-08ccdb12...`
    # is something they did.
    meta = gb.history_meta()
    rows = [(sha, parent, subject) for sha, parent, subject, _ts, _bk in meta]
    commit_index = {sha: i for i, (sha, _parent, _subject) in enumerate(rows)}
    commits = [
        {"sha": sha, "subject": subject, "index": i, "ts": ts, "bookkeeping": bk}
        for i, (sha, _parent, subject, ts, bk) in enumerate(meta)
    ]

    tree_result = load_tree(repo)
    op_leaf = tree_result["op_leaf"] if tree_result else {}

    # The shared time-axis rule (`opindex.earliest_commit_sha`): an op's earliest in-history
    # provenance, falling back to the earliest committed `Sgt-Op:` trailer for a *pending* op (just-
    # saved work a `record_ideal` witness-advance left provenance-less). `feature_runs`/`group.atoms`
    # read the same helper so all three time-aware projections agree on when an op happened.
    ops = list(opindex.index_ops(repo))
    from ugh.lens.tree import filed_op_leaf

    op_leaf = filed_op_leaf(repo, op_leaf, ops)  # saves and reverts since the last map build
    sha_of = opindex.earliest_commit_sha(gb, rows, ops)
    ops_out = []
    for op in sorted(ops, key=lambda op: op.id):
        sha = sha_of.get(op.id)
        if sha is None:
            continue  # embodied by no commit in this history -- omit, as before
        ops_out.append({
            "id": op.id, "kind": op.kind, "feature_id": op_leaf.get(op.id),
            "commit_index": commit_index[sha],
        })
    ops_out.sort(key=lambda o: (o["commit_index"], o["id"]))

    if full:
        return {"commits": commits, "ops": ops_out}

    kinds: dict[str, int] = {}
    features: dict[str, int] = {}
    for o in ops_out:
        kinds[o["kind"]] = kinds.get(o["kind"], 0) + 1
        if o["feature_id"] is not None:
            features[o["feature_id"]] = features.get(o["feature_id"], 0) + 1
    # "What did I do" is a question about the developer's work, so two filters apply in sequence and
    # they are independent improvements to the same answer.
    #
    # First, ugh's own materializations drop out of the rows and are reported as a count beside them.
    # `commit_count` stays the honest total (it is the time axis's length, which every index refers
    # to); `save_count` is the number a person would give if asked how much they had done.
    #
    # Then each surviving row gets a `headline` beside its raw `subject`: what the work *was*,
    # falling back to its dominant feature's label when the subject is a bare stamp like `wip`. Only
    # computed for the returned window (one label lookup per row), so this stays a compact-path cost.
    latest_first = list(reversed(commits))
    real_first = [c for c in latest_first if not c["bookkeeping"]]
    window = real_first[offset:offset + limit]
    if window:
        labels = _grid_labels(repo)
        dominant = _dominant_feature_by_commit(ops_out)
        window = [{**c, "feature_id": dominant.get(c["index"]),
                   "headline": headline_for(c["subject"], dominant.get(c["index"]), labels)}
                  for c in window]
    return {
        "commit_count": len(commits),
        "save_count": len(real_first),
        "bookkeeping_count": len(commits) - len(real_first),
        "op_count": len(ops_out),
        "kinds": kinds,
        "features": features,
        "latest_commits": window,
    }


# Bare stamps a real history is full of -- they say nothing about what the save did.
_LOW_SIGNAL_SUBJECTS = {"done", "ok", "wip", "fix", "sss", "update", "stuff", "misc"}


def headline_for(subject: str, feature: str | None, labels: dict) -> str:
    """What to call a commit in a history listing: its own subject when that carries signal, else the
    label of the feature it mostly touched. A subject is low-signal when it is empty, <=3 characters,
    or a bare stamp like `done`/`wip`/`sss`. Falls back to the raw subject when there is no feature
    label to borrow -- better a weak name than none.

    Lives here, in the projection layer, because it is a statement about *what the work was* and
    every surface listing history needs the same answer."""
    subj = (subject or "").strip()
    if len(subj) >= 4 and subj.lower() not in _LOW_SIGNAL_SUBJECTS:
        return subject
    if feature is not None:
        return labels.get(feature) or subject
    return subject


def _dominant_feature_by_commit(ops_out: list[dict]) -> dict[int, str]:
    """commit-index -> the feature id most of that commit's ops belong to. Ties break on the smallest
    feature id, matching `tree.assign_ops_to_leaves`, so the headline a commit gets is stable."""
    from collections import Counter

    tally: dict[int, Counter] = {}
    for o in ops_out:
        if o["feature_id"] is not None:
            tally.setdefault(o["commit_index"], Counter())[o["feature_id"]] += 1
    out = {}
    for idx, counts in tally.items():
        top = max(counts.values())
        out[idx] = min(fid for fid, n in counts.items() if n == top)
    return out


def _grid_labels(repo) -> dict:
    """Feature-id -> label, the cheap way: the last-built tree's own labels plus the authored-
    feature overrides (U6/KTD4), *without* `map_view`'s `fused_graph` recompute (the expensive
    step `grid_view` must not pay to stay a fast daily surface). Mirrors `map_view`'s label
    resolution exactly, so the grid names a lane the same thing `ugh map` does."""
    from ugh.lens.authored import load_authored
    from ugh.lens.tree import _authored_leaf_claims
    from ugh.lens.tree import load as load_tree

    tree = load_tree(repo)
    nodes = tree["nodes"] if tree else {}
    labels = {nid: nd.get("label", nid) for nid, nd in nodes.items()}
    for nid, claim in _authored_leaf_claims(nodes, load_authored(repo)).items():
        if claim.label:  # empty = save-time cascade lane, unnamed; keep the clustered label
            labels[nid] = claim.label
    return labels


def _grid_partial_shas(repo) -> set[str]:
    """The witnessing commits whose mined ops `order.reduce_to_ideal` dropped from the current
    ref's ideal, from the fidelity side table (U2 writes it). Empty until that producer runs, so
    every cell reads "full" until a real reduction has been recorded -- forward-compatible, not a
    stub: the field is real, only the producer arrives in U2."""
    from ugh import state
    from ugh.core.lens import current_ref_key

    key = current_ref_key(repo)
    if key is None:
        return set()
    entry = state.load_json(repo, "fidelity", default={}).get(key)
    return set(entry.get("shas", ())) if isinstance(entry, dict) else set()


def _grid_ghosts(repo, known: set) -> list[dict]:
    """Active plan sessions' still-pending predictions -- one ghost per (step -> predicted_feature)
    -- for the dim tip cells a UI draws for intent that has no code yet (the "planned feature"
    ghost, Stage C). Off-chain plan hollows never enter the ideal, so this is the *only* place a
    prediction reaches the grid. `known_feature` flags whether the predicted lane still exists, so
    a renderer can place a ghost at its lane's tip or in an unplaced-predictions gutter."""
    from ugh.loop.plan import active_sessions

    ghosts = []
    for sid, rec in sorted(active_sessions(repo).items()):
        for i, step in enumerate(rec.get("steps", [])):
            if step.get("status") != "pending":
                continue
            fid = step.get("predicted_feature")
            if fid is None:
                continue
            ghosts.append({
                "feature_id": fid, "session_id": sid, "step_index": i,
                "title": step.get("title", ""), "known_feature": fid in known,
            })
    return ghosts


def grid_view(repo) -> dict:
    """The canonical lane×commit cell join (plan U1): the single source of truth for the grid
    every surface -- the CLI `ugh log`, the VS Code webview -- reads, so the (op ->
    cell) join is computed *once* here and never re-derived per surface (R5). A complete
    projection, not paged: a grid surface needs every cell to draw, so there is no compact/full
    split -- it is `map_view`-shaped, not `oplog_view`-shaped. Pure/offline over an already-mined
    store, like every view here.

    Composes `history_view`'s commit axis + per-op (feature, commit-index) with the feature tree's
    labels, active plan sessions' pending predictions (ghost cells), and the mining-fidelity side
    table (commits whose ops `reduce_to_ideal` dropped -- R6). Shape:

    * ``commits``  -- the time axis, `{sha, subject, index}`, oldest-first.
    * ``cells``    -- one per (feature, commit) carrying ops: `{feature_id, commit_index, op_ids,
      op_count, kinds, fidelity}` (`fidelity` = "partial" if that commit had ops dropped, else
      "full"), sorted by `(feature_id, commit_index)`. An op with no feature (`op_leaf` miss, e.g.
      new work before a map rebuild) has no lane and is omitted -- the same drop `graph_layout`/
      `episodes` already apply.
    * ``features`` -- the lane roster with labels: `{feature_id: {label, op_count}}`.
    * ``ghosts``   -- pending plan predictions, `{feature_id, session_id, step_index, title,
      known_feature}`.
    * ``partial_commits`` -- the sorted commit indices carrying any dropped-op cell.
    """
    hv = history_view(repo, full=True)
    labels = _grid_labels(repo)

    partial_shas = _grid_partial_shas(repo)
    partial_indices = {c["index"] for c in hv["commits"] if c["sha"] in partial_shas}

    cells: dict[tuple, dict] = {}
    features: dict[str, int] = {}
    for op in hv["ops"]:
        fid = op["feature_id"]
        if fid is None:
            continue
        features[fid] = features.get(fid, 0) + 1
        cell = cells.setdefault((fid, op["commit_index"]), {"op_ids": [], "kinds": {}})
        cell["op_ids"].append(op["id"])
        cell["kinds"][op["kind"]] = cell["kinds"].get(op["kind"], 0) + 1

    cells_out = [
        {
            "feature_id": fid, "commit_index": ci, "op_ids": sorted(c["op_ids"]),
            "op_count": len(c["op_ids"]), "kinds": c["kinds"],
            "fidelity": "partial" if ci in partial_indices else "full",
        }
        for (fid, ci), c in sorted(cells.items())
    ]
    return {
        "commits": hv["commits"],
        "cells": cells_out,
        "features": {
            fid: {"label": labels.get(fid, fid), "op_count": n}
            for fid, n in sorted(features.items())
        },
        "ghosts": _grid_ghosts(repo, set(features)),
        "partial_commits": sorted(partial_indices),
        "reverted_unaccounted": _reverted_unaccounted(
            repo, {oid for c in cells.values() for oid in c["op_ids"]}),
        # `commit_count` is the time axis's length -- every op's `commit_index` refers to it, so it
        # must keep counting ugh's own materialization commits. `save_count` is what a person means
        # by "how many saves": the same axis minus ugh's plumbing. Keeping both is what stops the
        # map's header from disagreeing with `ugh log`'s save list on the same repo.
        "commit_count": len(hv["commits"]),
        "save_count": sum(1 for c in hv["commits"] if not c.get("bookkeeping")),
        "bookkeeping_count": sum(1 for c in hv["commits"] if c.get("bookkeeping")),
        "op_count": sum(len(c["op_ids"]) for c in cells.values()),
        "feature_count": len(features),
    }


def _reverted_unaccounted(repo, shown: set[str]) -> dict:
    """Code a revert took away that no lane and no chapter draws: the steps the planner's `out`
    table lists, outside `shown`, whose symbols are gone from the code."""
    from ugh.core import opindex
    from ugh.core.lens import current_ideal
    from ugh.core.op import is_bottom
    from ugh.core.planner import load_out

    out = load_out(repo)
    if not out:
        return {"op_count": 0, "symbols": []}
    ideal = current_ideal(repo)
    absent = (set(out) & ideal.op_ids) - shown
    if not absent:
        return {"op_count": 0, "symbols": []}
    ops = opindex.index_ops(repo)
    by_id = {op.id: op for op in ops}
    alive = {s for s, oid in ideal.frontier(ops).items() if not is_bottom(by_id[oid].footprint[s][1])}
    gone = {s for oid in absent for s in by_id[oid].footprint} - alive
    named = sorted(s for s in gone if "__residue__" not in s and "__anchor__" not in s)
    carriers = [oid for oid in absent if set(by_id[oid].footprint) & gone]
    return {"op_count": len(carriers), "symbols": named}


def _entity_line_spans(repo, file: str) -> tuple[list[tuple[str, int, int]], str | None]:
    """Materialize the current ideal and extract `file`'s live entities as `(symbol, start_line,
    end_line)` triples, in document order -- the span lookup `blame_view`, `plan_view`, and
    `drift_view` all need. Returns `(spans, error)`: `error` is set (and `spans` is `[]`) when the
    current ideal doesn't cover `file`."""
    from ugh.core.fold import code
    from ugh.core.lens import current_ideal
    from ugh.core.store import Store
    from ugh.entities.extract import extract_file

    ops = Store(repo).all_ops()
    materialized = code(current_ideal(repo), ops)
    source = materialized.get(file)
    if source is None:
        return [], f"{file!r} is not covered by the current ideal"
    spans = [
        (e.id, e.start_line, e.end_line)
        for e in sorted(extract_file(file, source), key=lambda e: (e.start_line, e.id))
    ]
    return spans, None


def _spans_for_symbols(repo, symbols) -> dict[str, list[dict]]:
    """`{file: [{"symbol", "start_line", "end_line"}, ...]}` for exactly `symbols`, grouped by
    file -- the per-op/per-step span projection `plan_view` and `drift_view` share. A file no
    longer covered by the current ideal, or a symbol no longer live in it, is silently omitted
    (the op/step just gets fewer spans, never an error)."""
    by_file: dict[str, set[str]] = {}
    for sym in symbols:
        by_file.setdefault(sym.split("::", 1)[0], set()).add(sym)
    out: dict[str, list[dict]] = {}
    for file, wanted in sorted(by_file.items()):
        line_spans, error = _entity_line_spans(repo, file)
        if error:
            continue
        matched = [
            {"symbol": sym, "start_line": start, "end_line": end}
            for sym, start, end in line_spans if sym in wanted
        ]
        if matched:
            out[file] = matched
    return out


def plan_view(repo, *, full: bool = False) -> dict:
    """The plan-session review surface (plan U14): every active session's steps (a matched
    step's current file/line spans, via `_spans_for_symbols`) plus `ugh.loop.match.
    compute_checkpoint`'s pure preview -- candidate step<->op groups and drift op-ids, each group
    carrying its own per-op file/line spans. Never mutates anything (`compute_checkpoint` is
    pure); `ugh checkpoint --confirm-...` (`ugh.loop.match.confirm_match`) is the only writer.

    Compact by default: each session reports `step_count`/`matched_count` instead of the full
    `steps` list, and each checkpoint match drops its `files` (the `_spans_for_symbols`->`code()`
    fold) while keeping `hollow_ids`/`op_ids` -- small, activity-bounded lists `ugh checkpoint`'s
    default preview and `--confirm-...` workflow still need. `full=True` restores per-step detail
    (with spans) and per-match `files`; the MCP `tool_checkpoint` always requests it."""
    import time

    from ugh.core import opindex
    from ugh.loop import plan as plan_mod
    from ugh.loop.match import compute_checkpoint, session_coverage
    from ugh.loop.plan import STALLED_SECONDS

    by_id = {op.id: op for op in opindex.index_ops(repo)}
    checkpoint = compute_checkpoint(repo)
    # `full` alone carries per-step file-coverage ("did this step's work land, just under a
    # different name?") -- the "why isn't this matched" surface. Kept off the compact path so it
    # stays a pure count read.
    coverage = session_coverage(repo) if full else {}
    # Read-time lanes for unplaced steps (`plan_mod.step_feature`); the map is only read for the
    # full payload, the one that carries steps.
    member_leaf = {}
    if full:
        from ugh.lens.tree import leaf_member_index
        from ugh.lens.tree import load as load_tree

        tree_result = load_tree(repo)
        member_leaf = leaf_member_index(tree_result["nodes"]) if tree_result else {}
    now = time.time()
    # A session with a live candidate has uncommitted work flowing toward it right now, so it is
    # actively *building* regardless of how long ago its last step was confirmed -- age alone must
    # not flag an agent mid-edit (or a fresh intake) as stalled.
    sessions_with_candidates = {g.session_id for g in checkpoint.matches}

    def _files_for_ops(op_ids) -> list[dict]:
        symbols = {sym for op_id in op_ids if op_id in by_id for sym in by_id[op_id].footprint}
        return [{"path": f, "spans": s} for f, s in sorted(_spans_for_symbols(repo, symbols).items())]

    sessions = []
    for session_id, rec in sorted(plan_mod.active_sessions(repo).items()):
        steps = rec["steps"]
        pending = [s for s in steps if s["status"] == "pending"]
        # Derived (never stored, never a writer): an active plan is `stalled` iff it has unbuilt
        # steps, no work in flight toward it, and has gone quiet past STALLED_SECONDS -- interrupted,
        # resumable. Otherwise `building`. A fully-matched active session (edge case; normally it
        # has already flipped to `completed`) reads `complete`, never stalled.
        if not pending:
            derived_status = "complete"
        elif session_id in sessions_with_candidates or now - rec["last_activity_ts"] <= STALLED_SECONDS:
            derived_status = "building"
        else:
            derived_status = "stalled"
        base = {
            "session_id": session_id, "plan_text": rec["plan_text"], "status": rec["status"],
            "created_ts": rec["created_ts"], "last_activity_ts": rec["last_activity_ts"],
            "claude_session_id": rec.get("claude_session_id"),
            "derived_status": derived_status,
            "pending_count": len(pending),
            "remaining_titles": [s["title"] for s in pending],
        }
        if full:
            cov_by_hollow = {c["hollow_id"]: c for c in coverage.get(session_id, {}).get("pending", [])}
            base["steps"] = [
                {**step,
                 "predicted_feature": plan_mod.step_feature(member_leaf, step),
                 "files": _files_for_ops(step["matched_op_ids"]) if step["status"] == "matched" else [],
                 # A pending step also carries whether its predicted file already saw edits (covered)
                 # and why -- so a surface can explain a stall ("built in server.py, not the predicted
                 # connection.py") without re-deriving it. Absent on matched steps (already resolved).
                 **({"covered": cov_by_hollow[step["hollow_id"]]["covered"],
                     "coverage_reason": cov_by_hollow[step["hollow_id"]]["reason"]}
                    if step["hollow_id"] in cov_by_hollow else {})}
                for step in steps
            ]
        else:
            base["step_count"] = len(steps)
            base["matched_count"] = sum(1 for s in steps if s["status"] == "matched")
        sessions.append(base)

    matches = [
        {
            "session_id": group.session_id, "hollow_ids": list(group.hollow_ids),
            "op_ids": list(group.op_ids),
            **({"files": _files_for_ops(group.op_ids)} if full else {}),
        }
        for group in checkpoint.matches
    ]

    return {
        "sessions": sessions,
        "checkpoint": {"matches": matches, "drift_op_ids": list(checkpoint.drift_op_ids)},
    }


def drift_view(repo, *, full: bool = False) -> dict:
    """The "what extra happened" query (plan U14): every drift op -- one not predicted by any
    active plan session -- with its kind, footprint, and current file/line spans, decoupled from
    full session detail (`plan_view`).

    Compact by default: `{count, op_ids, kinds}` -- no per-op file/line spans, which also skips
    `_spans_for_symbols`'s `code()` fold entirely. `full=True` restores today's `{entries: [...]}`
    with each entry's footprint and spans."""
    from ugh.core import opindex
    from ugh.loop.match import compute_checkpoint

    by_id = {op.id: op for op in opindex.index_ops(repo)}
    checkpoint = compute_checkpoint(repo)
    op_ids = sorted(oid for oid in checkpoint.drift_op_ids if oid in by_id)

    if not full:
        kinds: dict[str, int] = {}
        for oid in op_ids:
            kinds[by_id[oid].kind] = kinds.get(by_id[oid].kind, 0) + 1
        return {"count": len(op_ids), "op_ids": op_ids, "kinds": kinds}

    entries = []
    for op_id in op_ids:
        op = by_id[op_id]
        footprint = sorted(op.footprint)
        files = [{"path": f, "spans": s} for f, s in sorted(_spans_for_symbols(repo, footprint).items())]
        entries.append({"op_id": op.id, "kind": op.kind, "footprint": footprint, "files": files})
    return {"entries": entries}


def save_preview_view(repo) -> dict:
    """The in-situ "what would a save land" query: which existing features would gain ops if you
    ran `ugh save` right now. Answers the workbench's ghost-checkpoint render -- a dashed car on
    each affected lane at the frontier -- so the user sees the consequence of saving before saving.

    Symbol-granular spread, deliberately NOT the tree's own per-op plurality vote: the study's
    stage-1 working tree mines as ONE op whose footprint spans every feature the assistant
    touched, and the plurality vote filed all of it under the single biggest lane -- so the ghost
    said "one feature gains work" while the quiz (and the truth) was "this change touched three
    parts of the dashboard". A lane is affected here iff the pending work touches a symbol it
    owns (residue/anchor symbols follow their anchor entity's lane, exactly the vote
    `tree._member_leaf_for` casts), so one multi-feature op ghosts every lane it really touches.
    The ledger still files each op under one home lane at save time (an op is atomic); this
    preview answers "what does this change touch", not "where will the record file it".

    Shape: `{"affected": [{"feature_id", "op_count", "op_ids", "symbols"}], "new_work_count": int,
    "total_op_count": int}` -- `symbols` are the owned symbols touched (residues shown as their
    anchor entity), rows sorted most-touched first. An op counting in several lanes appears in
    each row's `op_ids`; `new_work_count` counts ops touching NO owned symbol. Clean tree ->
    `affected: []`, all counts 0 (no ghosts render).

    NOT fully side-effect-free: `get(repo)` mines the working tree and persists the mined ops +
    sync cache + witness into `.sgt/` (like `ugh save`/`status` already do). It creates no git
    commit and does not advance the recorded ideal, so it is safe as a preview -- but it is a
    mine-on-contact, not a pure read."""
    from ugh.core import opindex
    from ugh.core.lens import current_ideal, get
    from ugh.lens.tree import _anchor_entity_of, _member_leaf_for, leaf_member_index
    from ugh.lens.tree import load as load_tree

    delta = get(repo).op_ids - current_ideal(repo).op_ids
    if not delta:
        return {"affected": [], "new_work_count": 0, "total_op_count": 0}

    by_id = {op.id: op for op in opindex.index_ops(repo)}
    uncommitted = [by_id[oid] for oid in delta if oid in by_id]

    tree_result = load_tree(repo)
    nodes = tree_result["nodes"] if tree_result else {}
    member_leaf = leaf_member_index(nodes) if nodes else {}

    by_feature: dict[str, dict] = {}
    unowned = 0
    for op in uncommitted:
        hit = False
        for sym in op.footprint:
            leaf = _member_leaf_for(sym, member_leaf)
            if leaf is None:
                continue
            hit = True
            row = by_feature.setdefault(leaf, {"op_ids": set(), "symbols": set()})
            row["op_ids"].add(op.id)
            row["symbols"].add(_anchor_entity_of(sym) or sym)
        if not hit:
            unowned += 1

    affected = [
        {"feature_id": fid, "op_count": len(row["op_ids"]),
         "op_ids": sorted(row["op_ids"]), "symbols": sorted(row["symbols"])}
        for fid, row in by_feature.items()
    ]
    affected.sort(key=lambda r: (-len(r["symbols"]), -r["op_count"], r["feature_id"]))
    return {
        "affected": affected,
        "new_work_count": unowned,
        "total_op_count": len(delta),
    }


def sync_view(report) -> dict:
    """Project an already-run `ugh.core.sync.SyncReport` (plan U15/U20) -- `sync` performs a real
    git fetch/merge/commit, so unlike this module's other views it isn't a pure read the CLI can
    call on demand; the CLI runs `sync.sync(...)` itself and hands the result here for projection.
    `open_fork_count` (additive, U20/C4) is the divergence-as-state loudness signal: nonzero means
    the fork-free part merged but that many same-symbol forks are recorded and await resolution."""
    return {
        "remote": report.remote,
        "branch": report.branch,
        "merged": report.merged,
        "message": report.message,
        "fetched_sha": report.fetched_sha,
        "merge_sha": report.merge_sha,
        "ops_added": report.ops_added,
        "forks": [list(triple) for triple in report.forks],
        "open_fork_count": len(report.forks),
        "base_recovery": report.base_recovery,  # U7/R12: how the merge-base ideal was recovered
        "theirs_recovery": report.theirs_recovery,  # ...and theirs' tip; "none" = a refused claim
        "pin_contradictions": [
            {"kind": c.kind, "members": list(c.members), "detail": c.detail}
            for c in report.pin_contradictions
        ],
        "identity_events": list(report.identity_events),
    }


def _open_fork_records(repo) -> list:
    """The `.sgt/forks.json` records the read/surfacing path should show: filtered to *resolvable*
    forks (`order.resolvable_forks`) -- real symbols whose two tips genuinely diverge. Synthetic
    `__anchor__`/`__residue__` collisions and same-after pseudo-forks (a revert onto identical bytes,
    or an add/delete/re-add rebirth) are neutralized by `fork_free` and carry no `ugh resolve` remedy,
    so they never surface. This also cleans records persisted *before* the write-side filter landed
    (`_record_parked_forks` is union-only and never removes), and drops any record whose tip is no
    longer in the store (`Store.get` -> None). Decodes only the two tip ops per record -- no full
    `all_ops()` scan.

    Stamps each surviving record with `cross_version`: True when its two tips were mined under
    *different* MINER_VERSIONs (F82). A version bump re-mines history but nothing evicts the previous
    generation, so the same commit can sit in the store twice; the two generations disagree about a
    symbol's after-state and collide on its `before_version`. `fork_free` drops both tips and their
    up-sets exactly as for a real divergence -- all 612 records on ugh's own store, costing 91% of the
    ideal -- so these must stay visible. But nobody edited anything and no hand-merge closes them:
    the only remedy is `ugh advanced migrate ops-v3`. Stamped here rather than in one caller because
    two surfaces read this list (`forks_view`, `status_view`) and each phrased it
    itself -- the flag has to arrive with the record or a surface silently keeps the old wording."""
    from ugh import state
    from ugh.core.order import resolvable_forks
    from ugh.core.store import Store

    records = state.load_json(repo, "forks", default=[])
    valid = [r for r in records if len(r.get("tips", ())) == 2]
    if not valid:
        return []
    store = Store(repo)
    by_id = {tip: store.get(tip) for r in valid for tip in r["tips"]}
    keep = {
        (s, a, b)
        for s, a, b in resolvable_forks(
            [(r["symbol"], r["tips"][0], r["tips"][1]) for r in valid], by_id
        )
    }
    out = []
    for r in valid:
        if (r["symbol"], r["tips"][0], r["tips"][1]) not in keep:
            continue
        versions = {op.miner_version for op in (by_id[t] for t in r["tips"]) if op is not None}
        out.append({**r, "cross_version": len(versions) > 1})
    return out


def forks_view(repo) -> dict:
    """The open same-symbol forks a prior sync recorded in committed `.sgt/forks.json` (plan U20,
    C4) -- for `ugh forks`. Each fork carries its symbol, its two tips, and the `ugh merge-op`
    remedy that closes it, plus the cheap-to-derive `file` it lives in (`symbol.split("::", 1)[0]`).
    There's no single "current" line span to add beyond that: both tips are, by construction,
    excluded from every verb-visible ideal, so a resolution UI that needs each tip's own content
    calls `fork_detail_view` instead. Filtered to resolvable forks via `_open_fork_records` (light
    per-tip store reads, each carrying `cross_version`); empty (`{"open": 0, "forks": []}`) when there
    are none."""
    records = _open_fork_records(repo)
    return {
        "open": len(records),
        "forks": [{**r, "file": r["symbol"].split("::", 1)[0]} for r in records],
    }


def fork_detail_view(repo, symbol: str) -> dict:
    """Per-tip folded images for one open same-symbol fork (plan U20, C4) -- `ugh resolve`'s side-by-side pick.
    Each tip is folded on its own downward closure (`order.downset`, over the whole op universe --
    correct here because a fork's two tips are siblings, never each other's predecessor, so a tip's
    own downset structurally excludes the other tip and anything reachable only through it) via
    `Ideal.from_ops` + `code`, so a resolution UI can diff both tips' full file content without a
    separate frontier query per tip. `{"error": ...}` when the symbol has no open (resolvable) fork."""
    from ugh.core.fold import code
    from ugh.core.ideal import Ideal
    from ugh.core.order import downset
    from ugh.core.store import Store

    records = _open_fork_records(repo)
    record = next((r for r in records if r["symbol"] == symbol), None)
    if record is None:
        return {"error": f"no open fork for {symbol!r}", "symbol": symbol}

    ops = Store(repo).all_ops()
    tips = []
    for tip in record["tips"]:
        ideal = Ideal.from_ops(downset(tip, ops), ops)
        materialized = code(ideal, ops)
        tips.append({
            "op_id": tip,
            "files": {path: content.decode("utf-8", "replace") for path, content in materialized.items()},
        })
    return {"symbol": symbol, "tips": tips, "remedy": record["remedy"]}


def _fold_ideal(repo, *, ref, at_commit_index, op_ids, current=False):
    """Resolve one of `fold_view`'s frontier args to `(ideal, ops)`, or refuse with
    `(None, None, {...})`. Shared by `fold_view` (which decodes to text) and `fold_out_view`
    (which writes raw bytes) so the two can never drift about what a frontier spec means.

    `current=True` is the *present* -- `lens.current_ideal`, the ideal the working tree actually
    holds. It is deliberately not the same frontier as `ref="HEAD"`, and the difference is not a
    nicety: `ideal_for_ref` selects ops whose provenance intersects the ref's commit ancestry
    (documented, and what `sync`/ref-to-ref `diff` need), but an ideal edit applied locally mints
    forward-subtraction ops in `apply` with **empty provenance**, so no ref can ever select them,
    while the ops they compensate for still carry provenance inside HEAD and stay selected. After
    `ugh revert <f> --yes` on bikecount that made `--at HEAD` report the pre-revert ideal exactly
    -- 111 ops against the current ideal's 113, disagreeing with the working tree on 7 of 16 files
    -- because a revert is local ideal state, not commit history. Anything asking "what does the
    tree look like right now" wants this, not a ref."""
    from ugh.core.ideal import Ideal
    from ugh.core.lens import current_ideal, ideal_for_ref
    from ugh.core.store import Store

    given = [x for x in (ref, at_commit_index, op_ids, current or None) if x is not None]
    if len(given) != 1:
        return None, None, {
            "error": "fold requires exactly one of ref, at_commit_index, op_ids, current"
        }

    store = Store(repo)
    ops = store.all_ops()

    if current:
        return current_ideal(repo), ops, None
    if ref is not None:
        return ideal_for_ref(repo, ref, store), ops, None
    if at_commit_index is not None:
        hist = history_view(repo, full=True)  # needs the unpaged per-op commit_index axis
        frontier_ids = frozenset(o["id"] for o in hist["ops"] if o["commit_index"] <= at_commit_index)
    else:
        frontier_ids = frozenset(op_ids)
    try:
        return Ideal.from_ops(frontier_ids, ops), ops, None
    except ValueError as e:
        return None, None, {"forked": True, "message": str(e)}


def fold_view(repo, *, ref=None, at_commit_index=None, op_ids=None, current=False) -> dict:
    """A side-effect-free fold of an arbitrary frontier -- a ref's ideal, every op at or before a
    commit-index position on `history_view`'s axis, or an explicit op-id set -- without checking
    anything out. Powers the composition workbench's draggable playhead and fork-tip diffs. Exactly
    one of `ref`/`at_commit_index`/`op_ids` must be given. Returns `code(I)` (UTF-8 text, replacing
    undecodable bytes -- this is a preview, not a byte-exact export; `fold_out_view` is the
    byte-exact one) plus that exact op-set's oracle verdict (`verdict_for`). A candidate that isn't
    a valid ideal (forked, or not downward-closed) is never raised through the API: it's reported
    as `{"forked": True, "message": ...}`."""
    from ugh.core.fold import code
    from ugh.core.oracle import verdict_for

    ideal, ops, refusal = _fold_ideal(
        repo, ref=ref, at_commit_index=at_commit_index, op_ids=op_ids, current=current
    )
    if refusal is not None:
        return refusal

    materialized = code(ideal, ops)
    return {
        "op_count": len(ideal.op_ids),
        "files": {path: content.decode("utf-8", "replace") for path, content in materialized.items()},
        "oracle_verdict": verdict_for(repo, ideal),
    }


FOLD_MANIFEST = ".sgt-fold-manifest.json"


def _read_fold_manifest(out) -> set[str]:
    """The relative paths the previous `fold_out_view` run wrote into `out`. Missing, unreadable,
    or malformed reads as "no previous run", which makes the next run write-only."""
    import json

    try:
        data = json.loads((out / FOLD_MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    return {p for p in data.get("paths", []) if isinstance(p, str)}


def _prune_fold_leftovers(out, stale: set[str]) -> list[str]:
    """Delete exactly the manifest-listed paths this fold no longer contains, then remove the
    directories that deletion emptied. Two containment guards, because the manifest is a file on
    disk that something else could have edited: never follow a symlink, and never touch anything
    that doesn't resolve inside `out`. A directory that still holds anything at all survives."""
    deleted: list[str] = []
    for rel in sorted(stale):
        full = out / rel
        try:
            full.resolve().relative_to(out.resolve())
        except (OSError, ValueError):
            continue  # escapes the overlay -- not ours to delete
        if full.is_symlink() or not full.is_file():
            continue
        full.unlink()
        deleted.append(rel)
    # Deepest first, so a directory emptied by removing its own subdirectory is itself collected.
    for rel in sorted(deleted, key=lambda p: p.count("/"), reverse=True):
        parent = (out / rel).parent
        while parent != out and parent.is_dir():
            try:
                parent.rmdir()  # only ever succeeds on a genuinely empty directory
            except OSError:
                break
            parent = parent.parent
    return deleted


def _sync_fold_dir(out_dir, materialized: dict[str, bytes], op_count: int) -> dict:
    """Write `materialized`'s raw bytes into `out_dir`, remove the manifest-listed paths this fold
    no longer contains, and re-write the manifest. The overlay-sync half of `fold_out_view`, split
    out so `verb_result_out_view` renders a preview's own counterfactual through exactly the same
    write/prune/inventory rules rather than a second copy of them."""
    import json
    from pathlib import Path

    out = Path(out_dir)
    previous = _read_fold_manifest(out)

    out.mkdir(parents=True, exist_ok=True)
    for path, data in sorted(materialized.items()):
        full = out / path
        full.parent.mkdir(parents=True, exist_ok=True)
        # Skip the write when the bytes already match. A dev server watching this directory sees
        # mtimes, not contents: Vite restarts the whole server when `vite.config.ts` is touched
        # and clears its TypeScript cache when `tsconfig.json` is, so rewriting every file turns
        # each scrub step into a restart instead of a hot replace of the few files that differ.
        try:
            if full.read_bytes() == data:
                continue
        except OSError:
            pass  # absent, unreadable, or a directory -- let the write decide
        full.write_bytes(data)

    deleted = _prune_fold_leftovers(out, previous - set(materialized))
    (out / FOLD_MANIFEST).write_text(
        json.dumps({"paths": sorted(materialized)}, indent=2) + "\n", encoding="utf-8"
    )
    return {
        "ok": True,
        "path": str(out),
        "written": len(materialized),
        "deleted": len(deleted),
        "op_count": op_count,
    }


def verb_result_out_view(repo, plan, out_dir) -> dict:
    """Write the tree a revert/restore plan describes onto `out_dir`, through `_sync_fold_dir`'s
    overlay rules: the current fold with the plan's files changed (a planned edit writes nothing
    else)."""
    from ugh.core.fold import code
    from ugh.core.ideal import Ideal
    from ugh.core.lens import ops_with_frontier_images

    ideal = Ideal(op_ids=frozenset(plan.before_ids))
    tree = code(ideal, ops_with_frontier_images(repo, ideal))
    for path, (_before, after) in plan.files.items():
        if after is None:
            tree.pop(path, None)
        else:
            tree[path] = after
    return _sync_fold_dir(out_dir, tree, len(plan.after_ids))


def show_at_view(repo, at: str, path: str | None = None) -> dict:
    """A file as it was at a past frontier, or the list of files that existed there.

    The shared resolution behind `ugh show <path> --at <spec>` and the MCP `ugh_show`'s `at` mode. It
    lives here because both surfaces need the same answer and rebuilding it per surface is how they
    drift: the MCP copy matched only a path suffix while the CLI matched an exact repo-relative path
    *or* a suffix, and the two reported a miss differently. Read-only -- `fold_view` reconstructs
    `code(I)` without checking anything out.

    Named `show_at_view` rather than `show_view` because `ugh show` answers two questions and both
    needed a projection: this one is the *time* reading ("as it was at"), and `show_view` below is the
    *identity* reading ("what is this"). Two functions called `show_view` briefly coexisted here after
    a merge, one silently shadowing the other."""
    view = fold_view(repo, **_parse_show_spec(at))
    if view.get("forked"):
        return {"error": view["message"]}
    if "error" in view:
        return {"error": view["error"]}
    files = view["files"]
    if not path:
        return {"at": at, "op_count": view["op_count"], "files": sorted(files)}
    if path not in files:
        # Suffix-matching beats an exact-path demand: the reader knows the file by its name far
        # more reliably than by its full repo-relative path.
        matches = [p for p in sorted(files) if p == path or p.endswith("/" + path)]
        if not matches:
            return {"error": f"{path!r} does not exist at {at} ({plural(len(files), 'file')} do; "
                             f"run `ugh show {at}` to list them)"}
        if len(matches) > 1:
            return {"error": f"{path!r} is ambiguous at {at}: {', '.join(matches)}"}
        path = matches[0]
    return {"at": at, "path": path, "content": files[path]}


def _parse_show_spec(spec: str) -> dict:
    """`ugh show`'s frontier grammar: `now` is the current ideal (the present), an all-digit spec is
    a commit-index position, `op:<id>,...` an explicit op-id set, anything else a ref name."""
    if spec == "now":
        return {"current": True}
    if spec.isdigit():
        return {"at_commit_index": int(spec)}
    if spec.startswith("op:"):
        return {"op_ids": spec[3:].split(",")}
    return {"ref": spec}


def _atom_prompt(repo, atom) -> str | None:
    """The best available recorded prompt for one atom (plan U3/U6): try its own commit sha
    first (`ugh session start --task` keys land here indirectly only via provenance, but a direct
    per-commit key is checked too for forward-compat), then any plan-id, then any session-name --
    the same three key kinds `Attribution` carries. `None` when nothing was ever recorded; the
    commit subject (already present on every atom) is the fallback human label, never this."""
    from ugh.intent.prompts import prompt_for
    from ugh.intent.turns import turns_for

    direct = prompt_for(repo, atom.commit_sha)
    if direct is not None:
        return direct
    for plan_id in sorted(atom.plan_ids):
        found = prompt_for(repo, plan_id)
        if found is not None:
            return found
    for session_id in sorted(atom.session_ids):
        found = prompt_for(repo, session_id)
        if found is not None:
            return found
    # Same three keys against the local turn store (intent-ledger M1): words harvested from the
    # workflow (`save -m`, hook turns) that never entered the committed sidecar still reach every
    # surface this join feeds -- the labeler, `intent show`, the editor.
    for key in (atom.commit_sha, *sorted(atom.plan_ids), *sorted(atom.session_ids)):
        hits = turns_for(repo, key)
        if hits:
            return hits[0]["text"]
    # Chat-keyed hook turns: reachable via the plan's stored claude_session_id (plan U12) --
    # the join that lets a verbatim `UserPromptSubmit` capture answer for the commit it produced.
    # Plan ids AND session ids: `confirm_match` stamps the plan-session id into the attribution's
    # `session` field (match.py `_stamp`), so the planned path's key arrives here as a session id;
    # membership in `plan_sessions` filters out genuine `ugh session` names.
    keys = sorted(set(atom.plan_ids) | set(atom.session_ids))
    if keys:
        from ugh import state as _state
        plan_sessions = _state.load_json(repo, "plan_sessions", default={})
        for p in keys:
            chat = (plan_sessions.get(p) or {}).get("claude_session_id")
            hits = turns_for(repo, chat, key_kind="chat") if chat else []
            if hits:
                return hits[0]["text"]
    # The capture manifest (weave P2): the window this save closed is the last place words can
    # live -- a chat-keyed prompt carried over MCP with no plan session to join through. First
    # turn of the window wins, matching the write-once sidecar's first-string-per-key spirit.
    from ugh.intent.manifest import load_manifests
    m = load_manifests(repo).get(atom.commit_sha)
    if m:
        for t in m["turns"]:
            if t["key_kind"] == "chat":
                return t["text"]
    return None


def intent_view(repo) -> dict:
    """The intent overlay's one canonical projection (plan U6, KTD3/KTD7): every commit-keyed
    `IntentAtom` (rung 0/1, recomputed on read -- cheap and pure, like `map_view`'s coupling
    edges) and every persisted, LLM-named `theme` (rung 2, read from `.sgt/intent/themes.json`
    if a `ugh log --refresh` has built them; `[]` otherwise, never an error). Each carries its dependency-graph-backed `tier` (`coupled` | `co-changed` |
    `thematic`, KTD3) and `feature_span` -- the "across features" claim is computed here, never
    asserted by a client. A theme also carries `stale_shas` (plan U5/KTD4): persisted member
    shas that no longer resolve against the current atom partition (e.g. a rebase/amend) are
    reported here rather than silently dropped, so a reader sees the theme is diminished before
    acting on it -- this view never refuses to render, only `ugh intent revert` does. Fully
    sorted for a stable projection, like every other view in this module."""
    from ugh import state
    from ugh.core.store import Store
    from ugh.intent import group
    from ugh.lens.tree import load as load_tree

    all_ops = Store(repo).all_ops()
    tree_result = load_tree(repo)
    op_leaf = tree_result["op_leaf"] if tree_result else {}

    atoms = group.atoms(repo)
    atoms_by_sha = {a.commit_sha: a for a in atoms}

    # Intent-ledger M1 provenance joins, resolved once: which chat session a plan came from
    # (`claude --resume` affordance, plan U12) and the live recorded reason for each op. Both are
    # local-tier reads; absent stores just mean empty enrichment, never an error.
    plan_sessions = state.load_json(repo, "plan_sessions", default={})
    from ugh.intent.rationale import load_rationale
    _recs = list(load_rationale(repo).values())
    _dead = {rel["target"] for r in _recs for rel in r.get("relations", [])
             if rel.get("type") == "supersedes"}
    reasons_by_op: dict[str, set[str]] = {}
    for r in sorted(_recs, key=lambda r: r["ts"]):
        if r["id"] in _dead or not r.get("reason"):
            continue
        for s in r.get("subject", []):
            reasons_by_op.setdefault(s["op"], set()).add(r["reason"])

    atoms_out = []
    for atom in atoms:
        span = group.feature_span(atom.op_ids, op_leaf)
        commit_shas = frozenset() if atom.commit_sha == group.UNWITNESSED else frozenset({atom.commit_sha})
        tier = group.tier(atom.op_ids, commit_shas, all_ops, op_leaf)
        # Plural on purpose: one commit's ops can come from several plans/chats (a save landing
        # work from two agent conversations, an op re-witnessed across merges) -- nothing in the
        # ledger is 1:1, so the projection must not collapse it either. Plan ids AND session ids:
        # `confirm_match` stamps the plan-session id into the attribution's `session` field, so
        # the planned path's key arrives as a session id (plan_sessions membership filters the
        # genuine `ugh session` names out).
        claude_sids = sorted({
            plan_sessions[p]["claude_session_id"]
            for p in (set(atom.plan_ids) | set(atom.session_ids))
            if p in plan_sessions and plan_sessions[p].get("claude_session_id")
        })
        rationale = sorted(set().union(*(reasons_by_op.get(o, set()) for o in atom.op_ids)))
        atoms_out.append({
            "commit_sha": atom.commit_sha,
            "subject": atom.subject,
            "op_ids": sorted(atom.op_ids),
            "feature_span": sorted(span),
            "tier": tier,
            "prompt": _atom_prompt(repo, atom),
            "session_ids": sorted(atom.session_ids),
            "plan_ids": sorted(atom.plan_ids),
            "claude_session_ids": claude_sids,
            "rationale": rationale,
        })
    atoms_out.sort(key=lambda a: (a["commit_sha"] == group.UNWITNESSED, a["commit_sha"]))

    themes_persisted = state.load_json(repo, "intent_themes", default={})
    themes_out = []
    for theme_id, entry in sorted(themes_persisted.items()):
        member_shas = frozenset(entry["atom_shas"])
        stale_shas = member_shas - frozenset(atoms_by_sha)
        op_ids = frozenset().union(*(atoms_by_sha[sha].op_ids for sha in member_shas if sha in atoms_by_sha))
        span = group.feature_span(op_ids, op_leaf)
        tier = group.tier(op_ids, member_shas, all_ops, op_leaf)
        themes_out.append({
            "theme_id": theme_id,
            "label": entry["label"],
            "rationale": entry["rationale"],
            "source": entry["source"],
            "atom_shas": sorted(member_shas),
            "stale_shas": sorted(stale_shas),
            "op_ids": sorted(op_ids),
            "feature_span": sorted(span),
            "tier": tier,
        })

    segments_out = _segments_out(repo, op_leaf, tree_result)
    return {"themes": themes_out, "atoms": atoms_out, "segments": segments_out}


def _commit_words_join(repo):
    """A cheap `sha -> captured words` lookup for the per-chapter zoom, built once (the two stores
    loaded a single time, not reloaded per commit as a naive `label_prompt_for` loop would). Same
    two rung-0 sources `ugh.intent.theme_segment.label_prompt_for` reads: the committed prompt
    sidecar first, then the highest-seq sha-keyed `save -m` turn. Chat-keyed words are deliberately
    excluded -- they arrive with the P2 alignment rung, not this pure-projection stage."""
    from ugh.intent.prompts import load_prompts
    from ugh.intent.turns import load_turns

    prompts = load_prompts(repo)
    sha_turn: dict[str, tuple[int, str]] = {}
    for t in load_turns(repo).values():
        if t.get("key_kind") == "sha":
            cur = sha_turn.get(t["key"])
            if cur is None or t["seq"] >= cur[0]:  # last save -m under this sha wins (hits[-1])
                sha_turn[t["key"]] = (t["seq"], t["text"])

    def words_for(sha: str) -> str | None:
        recorded = prompts.get(sha)
        if recorded:
            return recorded
        hit = sha_turn.get(sha)
        return hit[1] if hit else None

    return words_for


# ── per-checkpoint symbols and liveness (2026-10-18 workbench redesign, K1/K4) ───────────────────
_NAMED_KINDS = ("entity", "nested", "whole_file")


def _named_symbols(op) -> list[str]:
    """The symbols in `op`'s footprint a reader recognises: a function/class/method, a nested
    definition, or a whole file. Residue, anchors and imports are how the miner represents the
    bytes *around* those, and most ops touch nothing else (83% on this repository, 85% on the
    footfall study repo). One rule, used by `map_view`'s `own_symbols`, `_show_footprint` and the
    checkpoint `symbols` below, so the map, `ugh show` and the workbench chips cannot disagree
    about what a piece of work touched -- the husk-filter lesson, applied before a third copy."""
    from ugh.core.op import _symbol_kind

    return [s for s in op.footprint if _symbol_kind(s) in _NAMED_KINDS]


def _files_with_entities(ops) -> set[str]:
    """Files some op names an entity in. A file outside this set has no parseable definition
    anywhere in the store, so work on it is owned at file level (`map_view`'s fallback rule)."""
    return {s.partition("::")[0] for op in ops for s in _named_symbols(op)}


SEGMENT_SYMBOL_CAP = 24


def _segment_symbols(seg, by_id, op_commit, files_with_entities, head_op_ids, out, tips) -> dict:
    """A checkpoint's named symbols with a per-symbol kind and the saves that touched each, the two
    counts a surface prints, and whether its work is still in the code.

    `kind` is read off the symbol's own chain inside the checkpoint, oldest op first: born here is
    `added`, ending at bottom is `removed`, a move op is `moved`, anything else `changed`. Not the
    op's kind, which is per op and can mean "some symbols added, some modified". A symbol born and
    removed inside one checkpoint is dropped -- there is nothing of it to show.

    `change_count` is the sum over symbols of the saves that touched each, so a surface saying
    "6 changes · 4 functions · 3 saves" is true by construction; op counts, which are mostly
    layout bookkeeping, never reach a reader. Each symbol carries `live`, and `state` rolls them
    up: `live` when every named symbol's contribution is still in the code, `reverted` when none
    is, `partial` otherwise (a surface then says which functions are gone), and `None` when the
    ideal is unreadable (no claim, like `present_op_count`). A symbol's contribution is live when
    one of the checkpoint's ops on it is in the ideal and not out (the planner's `out` table), and
    the symbol's live tip is not someone else's removal. A layout-only
    checkpoint (no named symbol, 9 of 27 on footfall) is live iff any of its ops is. The
    file-level fallback is `map_view`'s: a file nobody names an entity in is owned whole."""
    from ugh.core.op import is_bottom

    ordered = sorted((by_id[o] for o in seg.op_ids if o in by_id),
                     key=lambda op: (op_commit.get(op.id, -1), op.id))
    per: dict[str, dict] = {}
    touched_files: set[str] = set()
    taken_out_by: set[str] = set()  # the ops that took this chapter's steps out
    for op in ordered:
        touched_files.update(s.partition("::")[0] for s in op.footprint)
        ci = op_commit.get(op.id)
        for sym in _named_symbols(op):
            before, after = op.footprint[sym]
            e = per.get(sym)
            if e is None:
                e = per[sym] = {"first_before": before, "last_after": after, "saves": set(),
                                "moved": False, "live": False, "own_tip": False}
            e["last_after"] = after
            if ci is not None:
                e["saves"].add(ci)
            if op.kind == "move":
                e["moved"] = True
            if op.id in out:
                taken_out_by.update(t[:12] for t in out[op.id])
            elif op.id in head_op_ids:
                e["live"] = True
            if tips.get(sym) == op.id:
                e["own_tip"] = True
    if not per:
        live_any = any(o in head_op_ids for o in seg.op_ids)
        saves = {op_commit[o] for o in seg.op_ids if o in op_commit}
        for f in sorted(f for f in touched_files if f not in files_with_entities):
            per[f] = {"first_before": "", "last_after": "", "saves": set(saves), "moved": False,
                      "live": live_any, "own_tip": False}
    out = []
    for sym in sorted(per):
        e = per[sym]
        born, gone = e["first_before"] is None, is_bottom(e["last_after"])
        if born and gone:
            continue
        kind = "added" if born else "removed" if gone else "moved" if e["moved"] else "changed"
        tip = tips.get(sym)
        tip_gone = (tip is not None and tip in by_id
                    and is_bottom(by_id[tip].footprint.get(sym, (None, ""))[1]))
        out.append({"symbol": sym, "file": sym.partition("::")[0], "kind": kind,
                    "saves": sorted(e["saves"]),
                    "live": e["live"] and (not tip_gone or e["own_tip"])})
    if not head_op_ids:
        state = None
    elif out:
        n_live = sum(1 for e in out if e["live"])
        state = "live" if n_live == len(out) else "reverted" if n_live == 0 else "partial"
    else:
        state = "live" if any(o in head_op_ids for o in seg.op_ids) else "reverted"
    return {
        "symbols": out[:SEGMENT_SYMBOL_CAP],
        "symbol_count": len(out),
        "change_count": sum(max(1, len(e["saves"])) for e in out),
        "symbols_truncated": max(0, len(out) - SEGMENT_SYMBOL_CAP),
        "state": state,
        "taken_out_by": sorted(taken_out_by),
    }


def _segments_out(repo, op_leaf, tree_result) -> list[dict]:
    """The feature-scoped intent segments (the "checkpoints" a user rewinds to): every feature's
    ops cut into contiguous, labeled chapters (`ugh.intent.segment`), each addressable as
    `<feature_id>@<seg_index>`. Deterministic rungs 0/1 on read; if `ugh intent build` has
    persisted LLM labels/boundaries (`.sgt/intent/segments.json`), those override per feature
    -- same read-vs-build split as themes and the feature tree. Each segment carries a `tier`
    (`group.tier`, KTD3) and a `novelty` weight, so a client can dim trivial chapters. Flat,
    sorted by `(feature_id, seg_index)`, for a stable projection."""
    from ugh import state
    from ugh.core.lens import current_ideal
    from ugh.intent import group
    from ugh.intent import segment as seg_mod

    nodes = tree_result["nodes"] if tree_result else {}
    persisted = state.load_json(repo, "intent_segments", default={})
    label_pins = state.load_json(repo, "intent_segment_pins", default={})
    runs_by_feature = seg_mod.feature_runs(repo, op_leaf)
    words_for = _commit_words_join(repo)
    # The capture evidence (weave P3): loaded once for every feature's cut, so the boundaries and
    # words labels here match what `resolve_checkpoint` derives -- both go through `segments_for`.
    from ugh.intent import stint as stint_mod
    from ugh.intent.stint import stint_words
    words_by_sha = stint_words(repo)
    # Which of a chapter's ops are still in HEAD's ideal. A revert removes ops from the ideal and
    # leaves them in the store -- that asymmetry is what makes `ugh restore` possible -- so the
    # chapter list must keep a reverted chapter and *say* it is gone. `current_ideal` (not
    # `ideal_for_ref`) is the read that reflects an explicit ideal edit; a provenance scan alone
    # cannot represent "still in git history, excluded from the ideal". An empty ideal means the
    # ref is unborn or unmined, which is no claim about any chapter, so `present_op_count` is
    # `None` there and a renderer must not read it as "removed".
    ideal = current_ideal(repo)
    head_op_ids = ideal.op_ids
    # The ops behind every checkpoint, footprint-only (`opindex` is memoised and already loaded by
    # `map_view`/`history_view` in the same compose), for the per-checkpoint symbols and liveness.
    from ugh.core import opindex
    idx_ops = opindex.index_ops(repo)
    by_id = {op.id: op for op in idx_ops}
    files_with_entities = _files_with_entities(idx_ops)
    op_commit = {oid: run.commit_index
                 for runs in runs_by_feature.values() for run in runs for oid in run.op_ids}
    from ugh.core.planner import load_out
    out_table = load_out(repo)
    tips = ideal.frontier(idx_ops) if head_op_ids else {}

    out: list[dict] = []
    for feature_id in sorted(runs_by_feature):
        segs = seg_mod.segments_for(repo, runs_by_feature[feature_id], persisted.get(feature_id),
                                    words_by_sha)
        segs = seg_mod.apply_label_pins(segs, label_pins.get(feature_id))
        feature_label = nodes.get(feature_id, {}).get("label", feature_id)
        for s in segs:
            commit_shas = frozenset(s.commit_shas)
            # Per-chapter captured asks (weave P3 + the P4 render): the grounded chat prompts
            # whose stints claimed this chapter's ops, each as an excerpt with the provenance to
            # say whose words they were (`stint.ask_record`). The stint join is what makes a
            # chat-keyed word per-commit-safe -- the exclusion `_commit_words_join` maintains no
            # longer applies to these.
            #
            # Excerpts, not prompts: this list feeds the timeline tooltip and the checkpoint panel,
            # which draw every chapter of every feature. Full prompts here were both unreadable (a
            # 900-character paragraph in a native tooltip) and the bulk of the payload, for words
            # nobody reads until they open one -- which is what `ugh show --asked` is for.
            asks: list[dict] = []
            seen_words: set[str] = set()
            for sha in s.commit_shas:
                for e in words_by_sha.get(sha, ()):
                    if not (s.op_ids & frozenset(e["op_ids"])) or e["text"] in seen_words:
                        continue
                    seen_words.add(e["text"])
                    asks.append(stint_mod.ask_record(
                        e["text"], channel=e.get("channel", "hook"),
                        actor=e.get("actor", "human"), ts=e["ts"], session=e["session"],
                        claimed=len(s.op_ids & frozenset(e["op_ids"])), full=False))
            # Nothing grounded claims this chapter: fall back to whatever prompt its commits
            # recorded (`_atom_prompt`'s ladder). Marked `recorded` rather than as a chat capture,
            # because that ladder reaches sidecars and save messages too and the channel is not
            # knowable from here -- naming a source we cannot vouch for would be the one thing
            # this attribute must never do.
            if not asks:
                for sha in s.commit_shas:
                    w = words_for(sha)
                    if w and w not in seen_words:
                        seen_words.add(w)
                        asks.append(stint_mod.ask_record(w, channel="recorded", full=False))
            # A segment's ops all belong to ONE feature by construction (feature-scoped cut), so
            # `feature_span` is always a single feature -- `group.tier` would degenerate to its
            # single-feature branch anyway. Compute that branch directly (no `components_in` walk):
            # a one-commit chapter is `co-changed` (one moment), a multi-commit one `thematic`
            # (spread over time). This keeps `intent_view` a cheap, poll-safe read.
            tier = group.CO_CHANGED if len(commit_shas) <= 1 else group.THEMATIC
            out.append({
                "feature_id": feature_id,
                "feature_label": feature_label,
                "seg_index": s.seg_index,
                "checkpoint": f"{feature_id}@{s.seg_index}",
                "intent": s.label,
                "rationale": s.rationale,
                "op_ids": sorted(s.op_ids),
                "op_count": s.op_count,
                "present_op_count": (len(s.op_ids & head_op_ids) if head_op_ids else None),
                "commit_shas": list(s.commit_shas),
                "asks": asks,
                "first_index": s.first_index,
                "last_index": s.last_index,
                "novelty": round(s.novelty, 3),
                "tier": tier,
                "source": s.source,
                **_segment_symbols(s, by_id, op_commit, files_with_entities, head_op_ids,
                                   out_table, tips),
            })
    _segment_dependents(repo, out)
    return out


DEPENDENT_SYMBOL_CAP = 8


def _segment_dependents(repo, segs) -> None:
    """What stands on each checkpoint: `dependents`, the other checkpoints owning the steps that
    hold its revert (with up to 8 held symbols each), and `clean`, whether its revert would go
    whole. From the planner, one plan per standing checkpoint; `None` when nothing of it is live."""
    from ugh.core import planner

    seg_of = {oid: i for i, seg in enumerate(segs) for oid in seg["op_ids"]}
    standing = [i for i, seg in enumerate(segs) if seg.get("state") in ("live", "partial")]
    plans = planner.plan_many(repo, [frozenset(segs[i]["op_ids"]) for i in standing])
    for seg in segs:
        seg["dependents"], seg["clean"] = [], None
    for i, p in zip(standing, plans):
        shared: dict[int, set[str]] = {}
        for hd in p.held:
            for h in hd["holders"]:
                if h in seg_of and seg_of[h] != i:
                    shared.setdefault(seg_of[h], set()).add(hd["step"].symbol)
        deps = sorted(shared, key=lambda d: (segs[d]["first_index"], segs[d]["checkpoint"]))
        segs[i]["dependents"] = [{"ref": segs[d]["checkpoint"],
                                  "symbols": sorted(shared[d])[:DEPENDENT_SYMBOL_CAP]}
                                 for d in deps]
        segs[i]["clean"] = bool(p.steps) and not p.held


def segments_view(repo) -> list[dict]:
    """Just the feature-scoped intent segments (`_segments_out`), without the atoms/themes
    machinery `intent_view` also builds (`Store(repo).all_ops()`, `group.atoms`) -- the cheap read the graph/timeline layouts use so drawing the chunk-car atom doesn't
    pay for the cross-feature theme projection it deliberately no longer draws."""
    from ugh.lens.tree import load as load_tree

    tree_result = load_tree(repo)
    op_leaf = tree_result["op_leaf"] if tree_result else {}
    return _segments_out(repo, op_leaf, tree_result)


def show_view(repo, target: str, *, symbol_limit: int = 12, save_limit: int = 5,
              include_ops: bool = False, include_asked: bool = False) -> dict:
    """`ugh show <sel>` -- "what is this thing?" for any id ugh ever printed.

    Every other view is organized around a *question* the user already knows how to ask (history,
    attribution, forks). This one is organized around the opposite situation: the user is holding an
    opaque token -- a `f-` handle off a graph node, a bare hex off `--ops`, a `f-x:slug` checkpoint,
    a symbol off a blame line, a commit sha off `ugh log`'s id column -- and does not yet know which
    question applies, let alone which verb takes it. Before this, answering "what is `f-00573aa`?"
    required knowing its *type* first in order to pick between `why`, `intent show`, and
    `advanced state`, which is backwards.

    Four things, in the order a user needs them:

    1. **identity** -- what kind of thing it is, its canonical copy token, its label
    2. **extent** -- how many edits, which symbols/files, which saves produced it, over what span
    3. **consequence** -- what reverting it would take: the steps that would go and the steps held
       because other work stands on them, from the same planner the real revert uses -- not an
       estimate, and nothing is written.
    4. **next** -- runnable commands for this *kind* of thing.

    Deliberately deterministic and offline: no LLM rung, no mining. `show` is what a cautious user
    runs *before* a mutating verb, possibly several times, so it must be instant and must never
    change what it is describing. A token the deterministic ladder can't claim comes back
    `ok: False` with the places to look, rather than an LLM's guess about what was probably meant.

    It also deliberately does not re-derive *why* -- attribution and rationale belong to `ugh why`,
    which `next` points at. Two views answering "why" would drift apart."""
    import re

    from ugh.select import resolve as select_resolve

    found = select_resolve.identify(repo, target)
    if found is None:
        # A ◆ row is a thing `ugh revert`/`ugh restore` both act on by name -- and it was the one noun `show` could not answer for. Asked about the
        # piece of work a task actually names ("Event Day Handling"), the verb whose whole job is
        # "what is this and what would come with it" said it was not a known anything. That gap is
        # felt exactly where it costs most: a ◆ carries no id in the log the way a lane does, so its
        # label is the only handle a reader has, and this is the verb that turns a handle into an
        # answer. Matched on the label the way the acting verbs match it, and on the theme id, which
        # this view is now also the place to *find*.
        theme = _theme_by_label(repo, target)
        if theme is not None:
            from types import SimpleNamespace

            op_ids = frozenset(theme["op_ids"])
            symbols, files = _show_footprint(repo, op_ids)
            provenance = _show_provenance(repo, op_ids, save_limit)
            # Real consequences, from the same planner every other kind uses.
            shim = SimpleNamespace(target=theme["label"], op_ids=op_ids, kind="theme",
                                   label=theme["label"], feature_id=None)
            consequences = _show_consequences(repo, shim, symbol_limit)
            quoted = f'"{theme["label"]}"'
            return {
                "ok": True,
                "kind": "work across features",
                "target": target,
                "id": theme["theme_id"],
                "handle": theme["theme_id"],
                "label": theme["label"],
                "rationale": theme["rationale"],
                "across_features": len(theme["feature_span"]),
                "feature": None,
                "op_count": len(op_ids),
                **({"ops": sorted(op_ids)} if include_ops else {}),
                "symbols": symbols[:symbol_limit],
                "symbol_count": len(symbols),
                "files": files,
                "saves": provenance["saves"],
                "save_count": provenance["save_count"],
                "span": provenance["span"],
                "asked": _show_asked(repo, op_ids, provenance["witnessing"], include_asked),
                "consequences": consequences,
                "next": [
                    # Which of the two acting verbs to offer depends on where the work currently
                    # stands, and `removes == 0` is what says so: reverting it would take nothing
                    # out, so it is not in the project now. Offering `revert` there points at the
                    # verb that has already run -- and the participant reading this card in the
                    # stage that asks them to put the work BACK would be handed the verb that took
                    # it out, under a consequence line ("reverting this changes nothing") they have
                    # to reason backwards from.
                    ({"cmd": f"ugh restore {quoted}",
                      "why": "it is not in the project now; this puts it back"}
                     if consequences["out_op_count"] and not consequences["undone"] else
                     {"cmd": f"ugh revert {quoted}",
                      "why": "preview taking it out; add --yes to apply"}),
                ],
            }
        # A fourth such situation, and the only one where the token named something real: the
        # feature part of a `<feature>@<n>` resolved and the chapter index did not. "not a known
        # feature, checkpoint, op, or symbol" denies the feature the user can see in the tree.
        from ugh.intent.segment import checkpoint_miss

        miss = checkpoint_miss(repo, target)
        if miss is not None:
            feat_part, label, seg_labels = miss
            n = len(seg_labels)
            return {
                "ok": False, "target": target, "kind": None,
                "message": (f"{label!r} has {n} checkpoint{'' if n == 1 else 's'}, "
                            f"so {target!r} names none of them"),
                "next": [{"cmd": f"ugh show {feat_part}@{i}", "why": lbl}
                         for i, lbl in enumerate(seg_labels[:8])]
                        or [{"cmd": f"ugh show {feat_part}", "why": "the feature as a whole"}],
            }
        # A commit-shaped token that got *here* has already been through the save rung, so the one
        # thing left to say is why that rung didn't claim it -- three different situations that all
        # produce the same refusal, and the user can only act on the difference. The flat "not a
        # known feature, checkpoint, op, or symbol" is what dead-ended this verb six times out of
        # ten in the pilot, and it named none of them. Reads only, like the general miss below.
        if re.fullmatch(r"[0-9a-f]{4,40}", target.strip()):
            return {
                "ok": False, "target": target, "kind": None,
                "message": (
                    f"{target!r} looks like a commit, but no save here matches it: either it is "
                    "not a commit in this repo, or it is a prefix matching more than one (type a "
                    "few more characters), or ugh recorded no edits for it."
                ),
                "next": [
                    {"cmd": "ugh log", "why": "the saves, each with the id this view accepts"},
                    {"cmd": f"git show {target}", "why": "if what you actually wanted was the commit itself"},
                ],
            }
        return {
            "ok": False, "target": target, "kind": None,
            # `show` matches ids and exact labels; it never reaches the NL resolver (pinned by
            # `test_show_never_calls_the_nl_resolver`), so a phrase misses here even when it would
            # resolve elsewhere. Saying that is the difference between "you have no such feature" and
            # "this verb does not look things up that way" -- and the miss branch used to close that
            # gap by offering `ugh revert <the same phrase>`, i.e. answering "what is this?" with a
            # verb that resolves by meaning and then acts on the guess, with no `--dry-run` to make
            # taking the suggestion safe. A read that failed must only offer reads.
            "message": f"{target!r} is not a known feature, checkpoint, op, or symbol "
                       f"(ids and exact labels only — `show` does not resolve a phrase)",
            "next": [
                {"cmd": "ugh log", "why": "every feature with its handle, and its edits per save"},
            ],
        }

    # A symbol selection's `op_ids` is deliberately a single op -- its frontier tip -- because that is
    # the op a `revert` of the symbol takes, and `resolve` guarantees "an id `show` accepts is exactly
    # an id `revert` accepts". As an *extent*, the tip alone is the wrong answer: `ugh show
    # coursecraft/cli.py::cmd_search` reported `1 edit` and named the commit that last *rewrote*
    # cmd_search (`extract Repository class for persistence`) where `git log -S"def cmd_search"` names
    # the one that introduced it (`add course search`). "When did this land, and what else happened to
    # it" is the question `show <symbol>` exists to answer, so extent and provenance run over the
    # symbol's whole live history while consequence and the revert offer stay on the tip.
    is_symbol = found.kind == "symbol"
    extent = _symbol_history(repo, found.target) if is_symbol else found.op_ids
    ops = sorted(extent)
    symbols, files = _show_footprint(repo, extent, only=found.target if is_symbol else None)
    consequences = _show_consequences(repo, found, symbol_limit)
    provenance = _show_provenance(repo, extent, save_limit)
    return {
        "ok": True,
        "kind": found.kind,
        "target": target,
        # `id` is canonical and unambiguous (for machines and for copy-out); `handle` is the short
        # form the graph gutter already prints, which is what the suggested commands use -- a 64-char
        # id in a `next:` line wraps the terminal and makes the whole block unreadable. Both resolve
        # identically: `resolve_feature` accepts the bare-hex prefix. For a save the pair is the
        # same shape -- full sha as the canonical id, the 7-char one `ugh log` printed as the handle.
        "id": (found.feature_id if found.kind == "feature"
               else found.sha if found.kind == "save"
               else (found.label or target)),
        "handle": _show_handle(found),
        "label": found.label,
        "feature": _show_feature_ref(repo, found),
        "op_count": len(ops),
        # Op ids are omitted unless asked for, and then complete. No renderer prints them, and they
        # are 64 chars each: on this repo a large feature made the payload 1,862 tokens, most of it
        # ids nobody read. `op_count` carries the fact. The rejected middle option was a silent
        # slice, which is the worse failure -- a caller reading five of forty ids has no way to know
        # the rest exist, where an absent field is unmistakably absent. `include_ops=True` when you
        # genuinely need the set (feeding it to another verb).
        **({"ops": ops} if include_ops else {}),
        "symbols": symbols[:symbol_limit],
        "symbol_count": len(symbols),
        "files": files,
        "saves": provenance["saves"],
        "save_count": provenance["save_count"],
        "span": provenance["span"],
        # What was asked for, as an attribute of the thing being shown. A symbol's extent is its
        # whole live history (above), so its asks are every prompt that ever wrote it -- which is
        # the answer to "why does this function look like this", asked of the function.
        "asked": _show_asked(repo, extent, provenance["witnessing"], include_asked),
        "consequences": consequences,
        "next": _show_next(found, consequences),
    }


def _theme_by_label(repo, target: str) -> dict | None:
    """The ◆ row whose label (or theme id) is `target`, or None.

    Label matching is deliberately the same shape the acting verbs use -- case-insensitive and blind
    to punctuation -- so the two projects' spellings of the same work ("Event-Day Handling" and
    "Event Day Handling") both land, and a name copied out of a stage card resolves whichever way it
    was written. `show` still refuses a *phrase*: this is an exact-label rung, not the NL resolver
    (`test_show_never_calls_the_nl_resolver` pins that), and a name that merely mentions the work
    squashes to something different and misses."""
    from ugh import state

    squash = lambda t: "".join(c for c in t.casefold() if c.isalnum())
    want = squash(target)
    if not want:
        return None
    # Cheap gate first. This runs on every `show` miss -- a typo, a stale id -- and `intent_view`
    # folds atoms, ops and the tree to build its answer. A repo with no ◆ rows at all (most of them,
    # and every one before `ugh intent build` has run) pays one small JSON read instead.
    persisted = state.load_json(repo, "intent_themes", default={})
    if not persisted:
        return None
    if not any(squash(e.get("label", "")) == want or tid == target.strip()
               for tid, e in persisted.items()):
        return None
    for theme in intent_view(repo)["themes"]:
        if squash(theme["label"]) == want or theme["theme_id"] == target.strip():
            return theme
    return None


def _show_handle(found) -> str:
    """The short token to put in a suggested command: for a feature, the 8-char bare hex the graph
    gutter prints (`f-00573aa…` -> `00573aaf`), so `show` hands back the same string the user saw
    there; for an op, the same 8-char truncation `--ops` shows; for a save, the 7-char sha `ugh log`
    prints in its id column; otherwise the token as typed."""
    if found.kind == "feature" and found.feature_id:
        fid = found.feature_id
        return fid[2:10] if fid.startswith("f-") else fid[:8]
    if found.kind == "op":
        return next(iter(sorted(found.op_ids)))[:8]
    if found.kind == "save" and found.sha:
        return found.sha[:7]
    return found.target


def _show_footprint(repo, op_ids, *, only: str | None = None) -> tuple[list[str], list[str]]:
    """(symbols, files) the selection's ops touch. Bookkeeping sentinels (`__residue__`/`__anchor__`)
    are dropped: they are how the miner represents the parts of a file *around* the symbols, so
    counting them would inflate "what this touched" with entries no user recognizes.

    `only` narrows the answer to one symbol, for a symbol selection. An op's footprint is
    many-symbols-to-one (one edit can rewrite several symbols across several files), so unfiltered
    this reported `17 symbols in 3 files` for a one-symbol query -- the co-edited symbols of the ops
    that happened to carry it, presented as the symbol's own extent. They are a real and interesting
    fact, but they are not what was asked, and no other field in the view is co-edited work, so
    showing them here read as the answer."""
    from ugh.core import opindex

    wanted = frozenset(op_ids)
    symbols: set[str] = set()
    touched: set[str] = set()
    files_with_entities: set[str] = set()
    for op in opindex.index_ops(repo):
        named = _named_symbols(op)
        files_with_entities.update(s.partition("::")[0] for s in named)
        if op.id in wanted:
            # kind-classified like map_view's own_symbols: import crumbs are bookkeeping too
            symbols.update(named)
            touched.update(s.partition("::")[0] for s in op.footprint)
    if not symbols:
        # the same file-level rule as `compose`'s own_symbols: a file no parser names an entity in
        # is owned whole, so a feature made of such files does not read as `0 symbols in 0 files`
        symbols = {f for f in touched if f not in files_with_entities}
    if only is not None:
        symbols &= {only}
    files = sorted({s.partition("::")[0] for s in symbols})
    return sorted(symbols), files


def _symbol_history(repo, symbol: str) -> frozenset[str]:
    """Every *live* op that wrote `symbol` -- the symbol's history as it currently stands.

    Restricted to the ideal's op set on purpose: an op a user has already reverted is not part of the
    answer to "what happened to this", and listing the commit behind it as a save would offer a
    provenance the code no longer has. Read-only -- `lens.current_ideal`, never `get`, because `show`
    must not mine (a read a user repeats before a mutating verb must not change what it describes)."""
    from ugh.core import lens, opindex

    live = lens.current_ideal(repo).op_ids
    return frozenset(op.id for op in opindex.index_ops(repo)
                     if op.id in live and symbol in op.footprint)


def _show_feature_ref(repo, found) -> dict | None:
    """The feature this selection lives in -- omitted when the selection *is* that feature, since
    echoing it twice reads as two different things."""
    if found.feature_id is None or found.kind == "feature":
        return None
    from ugh.lens.tree import load as load_tree

    nodes = (load_tree(repo) or {}).get("nodes", {})
    node = nodes.get(found.feature_id) or {}
    fid = found.feature_id
    return {"id": fid, "handle": fid[2:10] if fid.startswith("f-") else fid[:8],
            "label": node.get("label", fid)}


def _show_provenance(repo, op_ids, limit: int) -> tuple[list[dict], dict]:
    """(saves, span) for a selection's ops, from one pass over the commit graph.

    `saves` are the commits that produced these ops, deduped and ordered **oldest-first** so a
    feature's saves read as its story. When
    there are more than `limit`, the *most recent* are kept (the tail), because "what happened lately"
    is what a user is usually reconstructing; keeping the head would hide current work behind history.

    `span` is the `{first, last}` committer timestamp across them, i.e. "when did I do this"; both
    ends are `None` for ops with no committed witness yet (saved but not landed).

    Computed together because they need the same `history()` + `earliest_commit_sha()` work, and
    `show` is a read a user runs repeatedly."""
    from ugh.core import opindex
    from ugh.store.gitbind import GitBinding

    gb = GitBinding(repo)
    rows = gb.history()  # oldest-first (see `history_view`, which reverses it for its own window)
    ops = [op for op in opindex.index_ops(repo) if op.id in frozenset(op_ids)]
    sha_of = opindex.earliest_commit_sha(gb, rows, ops)
    subject = {sha: subj for sha, _parent, subj in rows}

    witnessing = set(sha_of.values())
    chronological = [sha for sha, _p, _s in rows if sha in witnessing]
    kept = chronological[-limit:] if limit and len(chronological) > limit else chronological

    times = gb.commit_times()
    stamps = sorted(times[sha] for sha in witnessing if sha in times)
    return {
        "saves": [{"sha": sha[:7], "subject": subject.get(sha, "")} for sha in kept],
        # Full shas, chronological and uncapped -- the key for the capture stores, which are keyed
        # by whole sha and have no view of a display limit. Free here: the pass is already done.
        "witnessing": chronological,
        # The true total, so a renderer can say how many it isn't showing. A silent truncation reads
        # as "this is all of it", which is the same class of quiet mislead as a silent no-op.
        "save_count": len(chronological),
        "span": {"first": stamps[0] if stamps else None, "last": stamps[-1] if stamps else None},
    }


def _show_asked(repo, op_ids, shas, include_all: bool = False) -> dict:
    """The `asked` attribute: what the developer asked for, for whatever `show` is describing.

    `ugh show` deliberately does not re-derive *why* -- that is `ugh why`'s job, and two views
    deriving the same answer drift apart. This is not that. It is one attribute of the thing on
    screen, in the same class as `symbols` and `saves`: the words that produced it, as an excerpt,
    with enough provenance to say whose words they were and where they were typed. The recorded
    reasoning stays behind `ugh why`, which `next` still points at.

    `top` is the ask grounding most of the selection (`dominant_ask`) and carries its verbatim text
    so a card can expand in place; `count` is how many asks there were in total, so a renderer can
    say what it is not showing rather than implying the one line is all of it. `include_all` adds
    the rest -- `ugh show --asked`, the read for someone who wants the conversation.

    Never raises: an unmanifested selection (all pre-weave history) is `count: 0`, which every
    renderer treats as "say nothing" rather than "say nothing was asked"."""
    from ugh.intent.stint import asks_for_ops, dominant_ask

    try:
        asks = asks_for_ops(repo, op_ids, shas)
    except Exception:  # noqa: BLE001 -- an attribute must never take down the verb carrying it
        return {"top": None, "count": 0}
    out = {"top": dominant_ask(asks), "count": len(asks)}
    if include_all:
        out["asks"] = asks
    return out


def _show_consequences(repo, found, symbol_limit: int = 12) -> dict:
    """What reverting this selection would take, from the planner (pure, writes nothing): how many
    of its steps would go, how many are held because other work stands on them, how many are out
    already, and the size of its removal set."""
    from ugh.core import planner

    p = planner.plan(repo, frozenset(found.op_ids), target=found.target)
    out = planner.load_out(repo)
    held = sorted({hd["step"].symbol for hd in p.held})
    return {
        "ok": p.ok,
        "message": p.message,
        "live_op_count": len(p.ops),
        "out_op_count": sum(1 for o in found.op_ids if o in out),
        "undone": len(p.undone),
        "held": len(p.held),
        "held_symbols": held[:symbol_limit],
        "removal_set_steps": (p.removal_set or {}).get("steps", 0),
    }


def revert_cost(consequences: dict) -> str:
    """The magnitude of a revert, for `ugh show`'s consequence line and its `next:` footer."""
    undone, held = consequences["undone"], consequences["held"]
    if not undone and not held:
        return "changes nothing"
    return f"undoes {undone} step{'s' if undone != 1 else ''}" + (f", {held} held" if held else "")


def _show_next(found, consequences: dict) -> list[dict]:
    """Runnable next steps for this kind of selection. Every `cmd` here is a real, currently-spelled
    verb at its current path -- the P0-A rule: a suggestion that silently no-ops is worse than no
    suggestion, so nothing entity-level and nothing re-homed-without-its-prefix goes in this list."""
    token = _show_handle(found)
    steps: list[dict] = []

    if found.kind == "feature":
        steps.append({"cmd": f'ugh feature rename {token} "<name>"',
                      "why": "if the generated label doesn't match what this is"})
    elif found.kind == "save":
        # `git show` is where the line-by-line diff lives, and saying so is better than implying
        # ugh has its own copy.
        steps.append({"cmd": f"git show {token}",
                      "why": "the line-by-line diff — ugh does not duplicate it"})

    # The revert offer comes last and states its cost, so the consequence is read before the verb is
    # copied. Omitted when nothing here is in the code; a restore is offered when it is out.
    if consequences.get("out_op_count") and not consequences.get("undone"):
        steps.append({"cmd": f"ugh restore {token}", "why": "it is out; this brings it back"})
    elif consequences.get("live_op_count"):
        steps.append({"cmd": f"ugh revert {token}", "why": revert_cost(consequences)})
    return steps


def _drift_paths_by_hash(repo, path_hashes: dict[str, str]) -> list[str]:
    """`_drift_paths` against a `{path: sha256-hex}` manifest instead of materialized bytes --
    same answer (hash equality stands in for byte equality), no op images loaded."""
    from hashlib import sha256
    from pathlib import Path

    repo_path = Path(repo)
    drift = []
    for path, expected in path_hashes.items():
        full = repo_path / path
        actual = sha256(full.read_bytes()).hexdigest() if full.is_file() else None
        if actual != expected:
            drift.append(path)
    return sorted(drift)


def status_view(repo) -> dict:
    """A kernel-backed summary (plan U13): file/symbol/feature counts, R7's coverage fraction
    (reusing `state_view`'s definition), the oracle's overall status, and working-tree drift --
    paths whose on-disk bytes no longer match `code(current_ideal)` (e.g. an edit made outside
    `ugh`, or a verb applied without re-writing the working tree)."""
    from hashlib import sha256 as _sha256

    from ugh import state as state_mod
    from ugh.core import opindex
    from ugh.core.fold import code
    from ugh.core.lens import _ids_digest, current_ideal, ops_with_frontier_images, sync_status
    from ugh.core.op import MINER_VERSION, is_bottom
    from ugh.core.oracle import overall_status
    from ugh.lens.tree import load as load_tree

    st = state_view(repo)
    ideal = current_ideal(repo)
    # Materialization manifest (`.sgt/local/mat_manifest.json`): `{path: sha256}` of
    # `code(current_ideal)`, keyed by the ideal's id-set digest + miner version. Drift detection
    # and the skips read only need byte-EQUALITY per path and path MEMBERSHIP -- both answered
    # by the manifest -- so a repeat `status` over an unchanged ideal skips loading the frontier
    # producers' images entirely (~3.5k op-file reads here). Any ideal movement changes the
    # digest; op content is fixed by the content-addressed ids, so the digest fixes the fold.
    ideal_digest = f"{MINER_VERSION}:{_ids_digest(ideal.op_ids)}"
    manifest = state_mod.load_json(repo, "mat_manifest", default=None)
    if isinstance(manifest, dict) and manifest.get("digest") == ideal_digest:
        path_hashes = manifest["paths"]
    else:
        # Frontier-selective read: this view folds only `ideal`, so it needs images for exactly
        # the frontier producers -- not `Store.all_ops()`'s every-op images decode.
        ops = ops_with_frontier_images(repo, ideal)
        materialized = code(ideal, ops)
        path_hashes = {p: _sha256(b).hexdigest() for p, b in materialized.items()}
        state_mod.save_json(repo, "mat_manifest", {"digest": ideal_digest, "paths": path_hashes})
    index = opindex.index_ops(repo)
    by_id = {op.id: op for op in index}
    symbol_count = sum(
        1 for sym, op_id in ideal.frontier(index).items() if not is_bottom(by_id[op_id].footprint[sym][1])
    )

    tree_result = load_tree(repo)
    # Count feature (leaf) nodes by *canonical* children, matching `map_view`: a node whose listed
    # children are all borrowed (spliced from another subsystem, their `parent` points elsewhere)
    # owns no features of its own and is itself the feature, so both views report the same count.
    _tnodes = tree_result["nodes"] if tree_result else {}
    feature_count = sum(
        1 for nid, nd in _tnodes.items() if not any(_tnodes[c]["parent"] == nid for c in nd["children"])
    )

    from ugh.core.lens import materialization_skips

    drift = _drift_paths_by_hash(repo, path_hashes)
    # `path_hashes` stands in for the materialized dict: the skips read consults it for path
    # MEMBERSHIP only (which tracked paths the ideal doesn't cover). `None` as `all_ops`: the
    # backstop read folds the *maximal* ideal -- let it load (path-restricted) images itself
    # (rare: only when tracked paths would be deleted).
    skips = materialization_skips(repo, path_hashes, None)
    open_forks = _open_fork_records(repo)

    return {
        # Count files from the *same* `current_ideal` the symbol count comes from, not
        # `state_view`'s HEAD ideal -- on an init-only repo (no ugh commit advancing HEAD) the two
        # diverge, which read as the nonsensical "0 file(s), N symbol(s)".
        "files": len(ideal.covered_paths(index)),
        "symbols": symbol_count,
        "features": feature_count,
        "coverage_fraction": st["coverage_fraction"],
        "oracle": {
            "configured": st["oracle_configured"],
            "status": overall_status(st["oracle_verdict"]) if st["oracle_configured"] else "unconfigured",
        },
        "drift": {"any": bool(drift), "paths": drift},
        # R3/R4: paths a materializing verb refuses to touch -- symlinks (unmanaged) and files the
        # current ideal dropped whose live bytes no valid ideal can regenerate (backstop-kept).
        "unmanaged": skips["unmanaged"],
        "backstop_kept": skips["backstop_kept"],
        # Files ugh holds no op for at all: kept, but not damage and not repairable.
        "never_recorded": skips.get("never_recorded", []),
        "forks": {"open": len(open_forks), "records": open_forks},
        "sync_status": sync_status(repo),
    }
