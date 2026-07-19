"""The canonical JSON projection of the operation-ideal kernel — one schema, many clients.

Every machine-readable surface (the CLI's ``--json`` mode, the MCP server, and any future UI)
renders the *same* dicts produced here, so the views can never drift apart (R21; schema changes
are additive-only). Reads are offline (no LLM/network dependency) and pure over an *already-mined*
store -- `sgt.core.lens.get` (mine-on-contact) is the caller's job, kept out of these functions so
they stay side-effect-free. `sgt.core.*` is imported lazily inside each function so `import
sgt.api` never pulls in the kernel's tree-sitter dependency just to define these shapes.

Shapes (stable; additive changes only):

* ``oplog_view``        — the mined operation DAG: every op's id, kind, footprint, provenance,
  structured attribution (D7: session/agent/plan per witnessing sha), intent.
* ``state_view``        — the current ref's ideal: frontier, coverage, entity-granularity fraction,
  and the async oracle's verdict (U9).
* ``ideal_diff_view``   — the semantic diff between two refs' ideals, grouped by symbol.
* ``verb_preview_view`` — a side-effect-free preview of an ideal-edit verb (U8: revert/restore/
  pin/cherry-pick/after): op-ids added/removed, affected symbols, fork refusal, before/after bytes.
* ``rewrite_view``      — the U11 rewrite-verb review surface: pending drafts (hollow ops awaiting
  fulfillment) and the currently staged candidate's oracle verdict, if any.
* ``map_view``          — the U13 feature tree: every node's id/label/kind/parent/children/op_count,
  the roots, the cross-feature structural dependency edges, and the last build's Greene identity
  events (birth/death/merge/split/continuation).
* ``history_view``      — the feature-map webview's commit-index axis: every mined commit in order,
  and every op's derived kind/feature/commit-index, for Gantt-style lifebars.
* ``feature_verb_preview_view`` — a side-effect-free preview of a feature verb (merge/split/move/
  rename/revert), with a uniform ``affected_features`` ripple list for hover-preview UIs.
* ``blame_view``        — per-file symbol spans (`sym -> max-op-in-I -> feature`) for the editor
  gutter: each entity's line range, its feature id, that feature's label, and the plan sessions
  (D7) that touched its tip op.
* ``status_view``       — a kernel-backed summary: file/symbol/feature counts, coverage fraction,
  the oracle's overall status, and working-tree drift from `code(current_ideal)`.
* ``plan_view``         — the U14 plan review surface: every active plan session's steps (with
  matched-step file/line spans) plus the pure checkpoint preview (candidate step<->op groups and
  drift op-ids).
* ``drift_view``        — the U14 "what extra happened" query: every op not predicted by any
  active plan session, with its kind, footprint, and current file/line spans.
* ``sync_view``         — the U15 `sgt sync` result: ops merged in, forks surfaced (with the
  `merge-op` remedy), pin contradictions, declared-edge cycles, and tree identity events.
* ``forks_view``        — the U20 open same-symbol forks recorded in committed `.sgt/forks.json`,
  each with its two tips and the `sgt merge-op` remedy (divergence-as-state, C4).
* ``proposal_view``     — the U24 proposal review object: feature delta, Δ op count, oracle claim,
  provenance summary, and staleness `status` (current / clean-reunion / fork). `render_github`
  projects exactly this shape into a PR body.
* ``tiers_view``        — the U27 three-tier file boundary's effective configuration: `.sgt/
  tiers.json`'s overrides, `.sgtignore`'s patterns, and each covered path's resolved tier + its
  `derived` flag (S4).
* ``selection_view``    — the U29 closure-explanation UX: a feature-tree selection's induced
  closure (direct ops, files, ops pulled in grouped by their own feature with a representative
  requires/chain path each), and the hub symbol when the pull crosses a feature boundary.
* ``why_view``          — the U29 "why is this op here" query: an op's plurality-vote feature
  attribution, or (given a target feature) the exact chain that pulled it into that feature's
  selection closure.
* ``trust_view``        — the U31 trust queue: every op with session/agent attribution or drift
  status that isn't yet covered by a review record, grouped by provenance key (a session/agent
  name, or ``"drift"`` for unattributed drift), so a teammate can act on or ack a whole group at
  once (``sgt revert --session``, ``sgt review-queue ack``).
* ``proposal_review_view`` — the U32 partial-accept surface: everything ``proposal_view`` has,
  plus the U24 ``approvals`` schema and a ``feature_checklist`` naming, per delta feature, which
  *other* delta features it requires — so ``sgt propose land --subset`` (or a future checkbox UI)
  can validate or grey out a choice without recomputing the closure itself.
* ``intent_view``       — the U6 intent-clustering overlay: every commit-keyed `IntentAtom` (rung
  0/1, recomputed on read) and every persisted LLM-named `theme` (rung 2, `sgt intent build`'s
  output), each with its dependency-graph-backed `tier` (coupled/co-changed/thematic) and
  cross-feature `feature_span` -- the "why" axis alongside `map_view`'s structural "what" axis.
* ``compose_view``      — a workbench refresh's whole picture in one call: `map`/`history`/
  `status`/`forks`/`plan`/`drift`/`sessions`/`trust`/`intent`, the current ideal's oracle verdict,
  and the open-proposal list, each delegated to its own view function with no reshaping.
* ``fold_view``         — a side-effect-free fold of an arbitrary frontier (a ref's ideal, every op
  at or before a commit-index position, or an explicit op-id set): `code(I)` plus that exact
  op-set's oracle verdict, without checking anything out. Powers a draggable playhead and fork-tip
  diffs; reports `forked`/`forks` instead of folding when the candidate isn't fork-free.
* ``fork_detail_view``  — per-tip folded images for one open fork's symbol, so a resolution UI can
  diff both tips' full file content without a separate frontier query per tip.
"""

from __future__ import annotations


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
    from sgt.core import opindex

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
    (U9) -- `oracle_verdict` is `None` until `sgt oracle run` has recorded one for this exact
    ideal, or always `None` when `oracle_configured` is False (no `.sgt/oracle.json`).

    Coverage-fraction definition (R7): of the paths present in the materialized tree, the
    fraction carried at *entity* granularity -- a path with at least one live top-level or nested
    entity symbol at the frontier (a parseable def/class/method sgt can revert or cherry-pick on
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
    from sgt.config import load_oracle_config
    from sgt.core import opindex
    from sgt.core.fold import _symbol_kind
    from sgt.core.lens import ideal_for_ref
    from sgt.core.oracle import verdict_for
    from sgt.core.op import is_bottom

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

    from sgt.core import tiers

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


def tiers_view(repo) -> dict:
    """The three-tier file boundary's effective configuration (U27/D4): `.sgt/tiers.json`'s
    explicit overrides, `.sgtignore`'s patterns, and -- for every path this ref's ideal
    currently covers -- the tier it resolves to plus whether it's flagged `derived` (S4). Reads
    the *working tree*'s config (a reporting/mutation surface); mining itself always reads via
    `sgt.core.tiers.load_tiers_at` against the mined commit's own tree (LAW-0), never this."""
    from sgt.core import tiers
    from sgt.core.lens import ideal_for_ref
    from sgt.core.store import Store

    cfg = tiers.load_tiers(repo)
    store = Store(repo)
    ops = store.all_ops()
    ideal = ideal_for_ref(repo, "HEAD", store)
    covered = ideal.covered_paths(ops)
    return {
        "overrides": {t: list(cfg.overrides.get(t, ())) for t in ("entity", "opaque", "ignored")},
        "sgtignore": list(cfg.sgtignore),
        "paths": {
            path: {"tier": tiers.resolve_tier(path, cfg), "derived": tiers.is_derived(path)}
            for path in sorted(covered)
        },
    }


def selection_view(repo, feature_refs) -> dict:
    """The closure induced by selecting `feature_refs` (plan U29): direct op count, files, the
    closure's total op count, ops additionally pulled in grouped by their own feature (each with
    a representative requires/chain path), and the hub symbol when the pull crosses a feature
    boundary. `select()` reports; it never materializes anything (see `sgt.lens.select`'s
    module docstring for why -- the U25 BET-C gate that ruled out silent branch materialization)."""
    from sgt.lens.select import select

    result = select(repo, feature_refs)
    if not result.ok:
        return {"ok": False, "message": result.message}
    return {
        "ok": True, "feature_ids": list(result.feature_ids), "files": list(result.files),
        "direct_op_count": result.direct_op_count, "closure_op_count": result.closure_op_count,
        "pulled": [
            {"feature_id": g.feature_id, "op_count": g.op_count, "chain": list(g.chain)}
            for g in result.pulled
        ],
        "hub": result.hub,
        "message": result.message,
    }


def why_view(repo, op_ref: str, for_feature: str | None = None) -> dict:
    """One op's feature attribution (plan U29): with no `for_feature`, the plurality vote that
    assigned it (every leaf its footprint touched, and how many symbols voted for each); with
    `for_feature`, the exact chain that pulled it into that feature's selection closure."""
    from sgt.lens.select import why

    result = why(repo, op_ref, for_feature)
    return {
        "ok": result.ok, "message": result.message, "op_id": result.op_id,
        "feature_id": result.feature_id, "for_feature": result.for_feature,
        "votes": list(result.votes), "chain": list(result.chain),
    }


def ideal_diff_view(repo, ref_a: str, ref_b: str) -> dict:
    """The semantic diff between two refs' ideals: the symmetric difference of their op sets
    (`Ideal.diff`), grouped by symbol and labeled by side (`only_in_a` / `only_in_b`). A pure
    read over the store -- both refs must already have been mined (via `get()` on each) for the
    diff to be complete; this projects what the store holds onto each ref's own commit ancestry
    without checking anything out."""
    from sgt.core.lens import ideal_for_ref
    from sgt.core.store import Store

    store = Store(repo)
    ideal_a = ideal_for_ref(repo, ref_a, store)
    ideal_b = ideal_for_ref(repo, ref_b, store)
    by_id = {op.id: op for op in store.all_ops()}

    sym_diff = ideal_a.diff(ideal_b)
    only_a = sym_diff & ideal_a.op_ids
    only_b = sym_diff & ideal_b.op_ids

    by_symbol: dict[str, dict[str, list[str]]] = {}
    for op_id in only_a:
        for sym in by_id[op_id].footprint:
            by_symbol.setdefault(sym, {}).setdefault("only_in_a", []).append(op_id)
    for op_id in only_b:
        for sym in by_id[op_id].footprint:
            by_symbol.setdefault(sym, {}).setdefault("only_in_b", []).append(op_id)

    grouped = {
        sym: {
            "only_in_a": sorted(sides.get("only_in_a", [])),
            "only_in_b": sorted(sides.get("only_in_b", [])),
        }
        for sym, sides in sorted(by_symbol.items())
    }
    return {"ref_a": ref_a, "ref_b": ref_b, "by_symbol": grouped, "count": len(sym_diff)}


def verb_preview_view(
    repo, verb: str, target: str, *, version: str | None = None,
    source_ref: str | None = None, other: str | None = None,
) -> dict:
    """A side-effect-free preview of an ideal-edit verb (U8: revert/restore/pin/cherry-pick/after)
    -- the op-ids it would add/remove, the symbols whose frontier tip moves, whether it would fork
    (and so refuse), and the per-file before/after bytes of the fold. Pure: it runs the verb's
    `plan_*` (no mining, no writes) and materializes both ideals in memory via `fold.code`, so a
    UI can render `--emit` without a CLI flip. Output is fully sorted for a stable projection.

    `version` is required for `pin` (target is the symbol); `source_ref` for `cherry-pick`;
    `other` for `after` (target and other are the two ops of the `a <= b` edge)."""
    from sgt.core import verbs

    plans = {
        "revert": lambda: verbs.plan_revert(repo, target),
        "restore": lambda: verbs.plan_restore(repo, target),
        "pin": lambda: verbs.plan_pin(repo, target, version),
        "cherry-pick": lambda: verbs.plan_cherry_pick(repo, target, source_ref),
        "after": lambda: verbs.plan_after(repo, target, other),
    }
    if verb not in plans:
        return {"error": f"unknown verb {verb!r}", "verbs": sorted(plans)}
    return _project_verb_preview(repo, plans[verb]())


def _affected_rows(repo, removed_ids, added_ids) -> list[dict]:
    """`affected` rows for a verb preview's removed/added op ids: each touched feature, a
    direction (`blast` for a feature losing ops, `foundation` for one gaining ops), and how many
    of its ops are touched. Lets a hover-preview UI render off-screen affected-feature pills/a
    minimap without re-deriving the DAG itself; `[]` when the tree hasn't been built or nothing
    touched a feature (e.g. a whole-file pseudo-symbol)."""
    from collections import Counter

    from sgt.lens.tree import load as load_tree

    tree_result = load_tree(repo)
    op_leaf = tree_result["op_leaf"] if tree_result else {}

    def tally(op_ids) -> Counter:
        return Counter(op_leaf[op_id] for op_id in op_ids if op_id in op_leaf)

    blast, foundation = tally(removed_ids), tally(added_ids)
    return (
        [{"feature_id": f, "direction": "blast", "op_count": c} for f, c in sorted(blast.items())]
        + [{"feature_id": f, "direction": "foundation", "op_count": c} for f, c in sorted(foundation.items())]
    )


def _project_verb_preview(repo, preview) -> dict:
    """Given an already-computed `sgt.core.verbs.VerbPreview`, the per-file before/after bytes
    plus the rest of `verb_preview_view`'s shape. Factored out so a caller that resolves its own
    preview -- `sgt.cli`'s `revert <feature>` (plan U13), which tries a feature id/label before
    falling back to `verb_preview_view`'s op/symbol dispatch -- gets the identical projection
    without re-deriving the byte diff."""
    from sgt.core.fold import code
    from sgt.core.ideal import Ideal
    from sgt.core.store import Store

    ops = Store(repo).all_ops()
    before = code(Ideal.from_ops(preview.before_ids, ops), ops)
    after = code(Ideal.from_ops(preview.after_ids, ops), ops)
    files = {
        path: {
            "before": before.get(path, b"").decode("utf-8", "replace"),
            "after": after.get(path, b"").decode("utf-8", "replace"),
        }
        for path in sorted(set(before) | set(after))
        if before.get(path) != after.get(path)
    }
    return {
        "ok": preview.ok,
        "verb": preview.verb,
        "target": preview.target,
        "removed": sorted(preview.removed),
        "added": sorted(preview.added),
        "affected_symbols": list(preview.affected_symbols),
        "forked": preview.forked,
        "files": files,
        "message": preview.message,
        "affected": _affected_rows(repo, preview.removed, preview.added),
    }


def rewrite_view(repo) -> dict:
    """U11's review surface: every registered-but-unfulfilled rewrite draft (`merge-op`/
    `split-op`/`transplant`/`revert --keep-dependents`) with its hollow ops' symbol/kind/intent,
    plus the currently staged candidate ideal (if `sgt fulfill` has run) with its oracle verdict --
    the thing `sgt land` is gated on (R14). `None` for ``staged`` means nothing is staged."""
    from sgt.core import oracle, rewrite
    from sgt.core.ideal import Ideal
    from sgt.core.store import Store

    store = Store(repo)
    ops = store.all_ops()

    drafts = []
    for draft_id, rec in sorted(rewrite.pending_drafts(repo).items()):
        hollows = [store.get_hollow(hid) for hid in rec["hollow_ids"]]
        drafts.append({
            "draft_id": draft_id,
            "verb": rec["verb"],
            "target": rec["target"],
            "message": rec.get("message", ""),
            "hollow_ops": [
                {"id": h.id, "symbol": next(iter(h.footprint)), "kind": h.kind, "intent": h.intent}
                for h in hollows if h is not None
            ],
        })

    staged_record = rewrite.staged_candidate(repo)
    staged = None
    if staged_record is not None:
        candidate = Ideal.from_ops(frozenset(staged_record["op_ids"]), ops)
        verdict = oracle.verdict_for(repo, candidate)
        staged = {
            "verb": staged_record["verb"],
            "target": staged_record["target"],
            "op_count": len(candidate.op_ids),
            "oracle_verdict": verdict,
            "oracle_status": oracle.overall_status(verdict),
        }

    return {"drafts": drafts, "staged": staged}


def map_view(repo) -> dict:
    """The feature tree (plan U13): a pure read of the last `sgt map`-built `.sgt/tree/tree.json`
    -- building/labeling/saving it is `sgt.lens.map.build_map`'s job, kept out of this read-only,
    dependency-light projection. Empty (`{"nodes": [], ...}`) if no tree has been built yet, so a
    UI can render "run `sgt map`" rather than erroring.

    Every node -- leaf (a `label_tree`/Greene-matched feature, `F<n>` id) or internal (a
    structural subsystem grouping, build-local `N<n>` id) -- is emitted uniformly with
    `kind: "feature" | "subsystem"` distinguishing them; `op_count` is the number of ops
    `op_leaf` assigns to a feature, rolled up through subsystems. `edges` rolls the fused
    structural/co-change/scope coupling graph (`sgt.lens.tree.fused_graph`, the same signal
    `plan_split` reads) up to leaf-feature pairs -- the cross-feature dependency edges a
    visualization draws between features. Fully sorted for a stable projection."""
    from sgt.core import opindex
    from sgt.core.lens import current_ideal, sync_status
    from sgt.lens.tree import feature_edges, fused_graph
    from sgt.lens.tree import load as load_tree

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

    def op_count(nid: str) -> int:
        children = nodes[nid]["children"]
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
        children = nodes[nid]["children"]
        if not children:
            return sorted(leaf_sessions.get(nid, ()))
        merged: set[str] = set()
        for c in children:
            merged.update(node_sessions(c))
        return sorted(merged)

    emitted = [
        {
            "id": nid,
            "label": nd.get("label", nid),
            "kind": "feature" if not nd["children"] else "subsystem",
            "parent": nd["parent"],
            "children": sorted(nd["children"]),
            "size": nd["size"],
            "op_count": op_count(nid),
            "dir": nd.get("dir", ""),
            "why": nd.get("why", ""),
            "split_reason": nd.get("split_reason"),
            "sessions": node_sessions(nid),
        }
        for nid, nd in sorted(nodes.items())
    ]
    ideal = current_ideal(repo)
    _, fused = fused_graph(repo, ops, ideal)

    return {
        "nodes": emitted,
        "roots": sorted(result["roots"]),
        "identity_events": sorted(result.get("identity_events", []), key=lambda e: (e["event"], e["feature_id"])),
        "feature_count": sum(1 for nd in nodes.values() if not nd["children"]),
        "edges": feature_edges(nodes, fused),
        "sync_status": sync_status(repo),
    }


def history_view(repo, *, full: bool = False, limit: int = 200, offset: int = 0) -> dict:
    """The feature-map webview's commit-index axis: every mined commit in chronological order
    (`sgt.store.gitbind.GitBinding.history`, oldest-first), and every stored op's derived kind,
    feature (`op_leaf`, if a tree has been built), and `commit_index` -- the position in that
    chronological list of the *earliest* of the op's provenance commits that actually appears
    there. An op none of whose provenance commits are in `history()` (e.g. mined from a detached
    or since-rewritten commit) is omitted rather than assigned a misleading index.

    Compact by default: drops the full per-op `ops` list (unbounded on a large history) for
    `{commit_count, op_count, kinds, features, latest_commits}` -- `latest_commits` is the
    `offset`/`limit` window of the *most recent* commits (reverse-chronological), the summary an
    agent actually wants ("what happened recently"). `full=True` restores today's unpaged
    `{commits, ops}` -- required by `fold_view`'s internal use of the `ops`/`commit_index` axis."""
    from sgt.core import opindex
    from sgt.lens.tree import load as load_tree
    from sgt.store.gitbind import GitBinding

    rows = GitBinding(repo).history()
    commit_index = {sha: i for i, (sha, _parent, _subject) in enumerate(rows)}
    commits = [{"sha": sha, "subject": subject, "index": i} for i, (sha, _parent, subject) in enumerate(rows)]

    tree_result = load_tree(repo)
    op_leaf = tree_result["op_leaf"] if tree_result else {}

    ops_out = []
    for op in sorted(opindex.index_ops(repo), key=lambda op: op.id):
        idx = min((commit_index[sha] for sha in op.provenance if sha in commit_index), default=None)
        if idx is None:
            continue
        ops_out.append({"id": op.id, "kind": op.kind, "feature_id": op_leaf.get(op.id), "commit_index": idx})
    ops_out.sort(key=lambda o: (o["commit_index"], o["id"]))

    if full:
        return {"commits": commits, "ops": ops_out}

    kinds: dict[str, int] = {}
    features: dict[str, int] = {}
    for o in ops_out:
        kinds[o["kind"]] = kinds.get(o["kind"], 0) + 1
        if o["feature_id"] is not None:
            features[o["feature_id"]] = features.get(o["feature_id"], 0) + 1
    latest_first = list(reversed(commits))
    return {
        "commit_count": len(commits),
        "op_count": len(ops_out),
        "kinds": kinds,
        "features": features,
        "latest_commits": latest_first[offset:offset + limit],
    }


def feature_verb_preview_view(repo, verb: str, *args: str) -> dict:
    """A side-effect-free preview of a feature verb (U13's merge/split/move/rename) or a
    feature-grouped revert (U8's kernel edit, resolved via `sgt.lens.verbs.plan_revert_feature`),
    each verb's own `plan_*` fields plus a uniform ``affected_features`` list -- the real ripple
    of that verb, so a hover-preview UI never needs per-verb-shaped logic: merge/rename affect the
    named feature(s) directly; split's second id is the fresh id the *next* `sgt map` would mint
    for the new group (previewed, not committed); move's are the op-losing source leaf(es) plus
    the target; revert's is every feature whose ops sit in the real upset closure being removed --
    the genuine cross-feature blast radius, not a guessed dependency edge."""
    from collections import Counter

    from sgt.lens import tree as tree_mod
    from sgt.lens import verbs as lens_verbs

    if verb == "merge":
        if len(args) != 2:
            return {"error": "merge requires <survivor> <absorbed>"}
        preview = lens_verbs.plan_merge(repo, args[0], args[1])
        return {
            "ok": preview.ok, "verb": "merge", "message": preview.message,
            "survivor_id": preview.survivor_id, "absorbed_id": preview.absorbed_id,
            "op_count": preview.op_count, "member_count": preview.member_count,
            "affected_features": [preview.survivor_id, preview.absorbed_id] if preview.ok else [],
            "affected": [],  # metadata-only: no op moves, so no blast/foundation direction applies
        }

    if verb == "rename":
        if len(args) != 2:
            return {"error": "rename requires <feature> <new-label>"}
        preview = lens_verbs.plan_rename(repo, args[0], args[1])
        return {
            "ok": preview.ok, "verb": "rename", "message": preview.message,
            "feature_id": preview.feature_id, "old_label": preview.old_label, "new_label": preview.new_label,
            "affected_features": [preview.feature_id] if preview.ok else [],
            "affected": [],  # metadata-only
        }

    if verb == "split":
        if len(args) != 1:
            return {"error": "split requires <feature>"}
        preview = lens_verbs.plan_split(repo, args[0])
        affected: list[str] = []
        if preview.ok:
            tree_result = tree_mod.load(repo)
            new_id = next(tree_mod._fresh_id_gen(set(tree_result["nodes"])))
            affected = [preview.feature_id, new_id]
        return {
            "ok": preview.ok, "verb": "split", "message": preview.message,
            "feature_id": preview.feature_id,
            "groups": [list(g) for g in preview.groups] if preview.groups else None,
            "reason": preview.reason,
            "affected_features": affected,
            "affected": [],  # metadata-only
        }

    if verb == "move":
        if len(args) < 2:
            return {"error": "move requires <op>... <target-feature>"}
        *op_refs, target_id = args
        preview = lens_verbs.plan_move(repo, list(op_refs), target_id)
        affected = []
        affected_rows: list[dict] = []
        if preview.ok:
            tree_result = tree_mod.load(repo)
            op_leaf = tree_result["op_leaf"] if tree_result else {}
            sources = {op_leaf[op] for op in preview.op_ids if op in op_leaf} - {preview.target_id}
            affected = sorted(sources | {preview.target_id})
            blast = Counter(
                leaf for op in preview.op_ids
                if (leaf := op_leaf.get(op)) is not None and leaf != preview.target_id
            )
            # every moved op lands in the target regardless of source, so it's one foundation row
            affected_rows = (
                [{"feature_id": f, "direction": "blast", "op_count": c} for f, c in sorted(blast.items())]
                + [{"feature_id": preview.target_id, "direction": "foundation", "op_count": len(preview.op_ids)}]
            )
        return {
            "ok": preview.ok, "verb": "move", "message": preview.message,
            "op_ids": list(preview.op_ids), "target_id": preview.target_id,
            "affected_features": affected,
            "affected": affected_rows,
        }

    if verb in ("revert", "restore"):
        if len(args) != 1:
            return {"error": f"{verb} requires <feature>"}
        plan_fn = lens_verbs.plan_revert_feature if verb == "revert" else lens_verbs.plan_restore_feature
        preview = plan_fn(repo, args[0])
        affected = []
        if preview.ok:
            tree_result = tree_mod.load(repo)
            op_leaf = tree_result["op_leaf"] if tree_result else {}
            touched = preview.removed if verb == "revert" else preview.added
            affected = sorted({op_leaf[op] for op in touched if op in op_leaf})
        return {
            "ok": preview.ok, "verb": verb, "message": preview.message,
            "target": preview.target, "removed": sorted(preview.removed), "added": sorted(preview.added),
            "affected_features": affected,
            "affected": _affected_rows(repo, preview.removed, preview.added),
        }

    return {"error": f"unknown verb {verb!r}", "verbs": ["merge", "split", "move", "rename", "revert", "restore"]}


def _entity_line_spans(repo, file: str) -> tuple[list[tuple[str, int, int]], str | None]:
    """Materialize the current ideal and extract `file`'s live entities as `(symbol, start_line,
    end_line)` triples, in document order -- the span lookup `blame_view`, `plan_view`, and
    `drift_view` all need. Returns `(spans, error)`: `error` is set (and `spans` is `[]`) when the
    current ideal doesn't cover `file`."""
    from sgt.core.fold import code
    from sgt.core.lens import current_ideal
    from sgt.core.store import Store
    from sgt.entities.extract import extract_file

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


def blame_view(repo, file: str) -> dict:
    """Per-symbol feature attribution for one file (plan U13): for each of `file`'s live entities
    (`_entity_line_spans`), resolve `sym -> max-op-in-I -> feature` via the frontier and the
    feature tree's `op_leaf`. Returns `{"file", "spans", "features", "error"?}`; an entity whose
    tip op has no feature assignment yet (tree stale, or `sgt map` never run) is omitted from
    `spans` rather than guessed at."""
    from sgt.core.lens import current_ideal
    from sgt.core.store import Store
    from sgt.lens.tree import load as load_tree

    line_spans, error = _entity_line_spans(repo, file)
    if error:
        return {"file": file, "spans": [], "features": {}, "error": error}

    tree_result = load_tree(repo)
    op_leaf = tree_result["op_leaf"] if tree_result else {}
    nodes = tree_result["nodes"] if tree_result else {}
    ops = Store(repo).all_ops()
    by_id = {op.id: op for op in ops}
    frontier = current_ideal(repo).frontier(ops)

    spans = []
    features: dict[str, dict] = {}
    for sym, start_line, end_line in line_spans:
        tip = frontier.get(sym)
        feature_id = op_leaf.get(tip) if tip else None
        if feature_id is None:
            continue
        label = nodes.get(feature_id, {}).get("label", feature_id)
        tip_op = by_id.get(tip)
        sessions = sorted({a.session for a in tip_op.attribution if a.session is not None}) if tip_op else []
        spans.append({
            "symbol": sym, "start_line": start_line, "end_line": end_line,
            "feature_id": feature_id, "label": label, "sessions": sessions,
        })
        features[feature_id] = {"label": label}
    return {"file": file, "spans": spans, "features": features}


def plan_view(repo, *, full: bool = False) -> dict:
    """The plan-session review surface (plan U14): every active session's steps (a matched
    step's current file/line spans, via `_spans_for_symbols`) plus `sgt.loop.match.
    compute_checkpoint`'s pure preview -- candidate step<->op groups and drift op-ids, each group
    carrying its own per-op file/line spans. Never mutates anything (`compute_checkpoint` is
    pure); `sgt checkpoint --confirm-...` (`sgt.loop.match.confirm_match`) is the only writer.

    Compact by default: each session reports `step_count`/`matched_count` instead of the full
    `steps` list, and each checkpoint match drops its `files` (the `_spans_for_symbols`->`code()`
    fold) while keeping `hollow_ids`/`op_ids` -- small, activity-bounded lists `sgt checkpoint`'s
    default preview and `--confirm-...` workflow still need. `full=True` restores per-step detail
    (with spans) and per-match `files`; the MCP `tool_checkpoint` always requests it."""
    from sgt.core import opindex
    from sgt.loop import plan as plan_mod
    from sgt.loop.match import compute_checkpoint

    by_id = {op.id: op for op in opindex.index_ops(repo)}
    checkpoint = compute_checkpoint(repo)

    def _files_for_ops(op_ids) -> list[dict]:
        symbols = {sym for op_id in op_ids if op_id in by_id for sym in by_id[op_id].footprint}
        return [{"path": f, "spans": s} for f, s in sorted(_spans_for_symbols(repo, symbols).items())]

    sessions = []
    for session_id, rec in sorted(plan_mod.active_sessions(repo).items()):
        steps = rec["steps"]
        base = {
            "session_id": session_id, "plan_text": rec["plan_text"], "status": rec["status"],
            "created_ts": rec["created_ts"], "last_activity_ts": rec["last_activity_ts"],
        }
        if full:
            base["steps"] = [
                {**step, "files": _files_for_ops(step["matched_op_ids"]) if step["status"] == "matched" else []}
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
    from sgt.core import opindex
    from sgt.loop.match import compute_checkpoint

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


def trust_view(repo, *, full: bool = False) -> dict:
    """The U31 trust queue: every op carrying session/agent attribution (D7) or drift status
    (`drift_view`) that isn't yet covered by a review record (`sgt.core.review`), grouped by
    provenance key -- a session or agent name, or ``"drift"`` for an unattributed drift op. Acting
    on a group (`sgt revert --session`) or acking it (`sgt review-queue ack`) is the existing verb
    surface; this view only renders what's queued, per the plan's "report, don't invent mutation
    semantics" boundary.

    Compact by default: each group reports `op_count` instead of the full `op_ids`/`ops` detail
    (footprint + attribution per op). `sgt review-queue list` -- which hands the listed op ids to
    a follow-up `ack` -- always requests `full=True`, the same "verb whose output feeds another
    verb's input needs the full shape" rule `tool_checkpoint` (Part D) and `intent/resolve.py`
    (Part E) follow."""
    from sgt.core import opindex, review
    from sgt.loop.match import compute_checkpoint

    ops = opindex.index_ops(repo)
    drift_ids = compute_checkpoint(repo).drift_op_ids
    reviewed = review.reviewed_op_ids(repo)

    groups: dict[str, list[dict]] = {}
    for op in ops:
        if op.id in reviewed:
            continue
        keys = sorted({a.session for a in op.attribution if a.session} |
                      {a.agent for a in op.attribution if a.agent})
        is_drift = op.id in drift_ids
        if not keys and not is_drift:
            continue
        for key in keys or ["drift"]:
            groups.setdefault(key, []).append({
                "op_id": op.id,
                "kind": op.kind,
                "footprint": sorted(op.footprint),
                "attribution": _attribution_entries(op),
                "drift": is_drift,
            })

    if full:
        group_list = [
            {"provenance": key, "op_ids": [e["op_id"] for e in entries], "ops": entries}
            for key, entries in sorted(groups.items())
        ]
    else:
        group_list = [
            {"provenance": key, "op_count": len(entries)}
            for key, entries in sorted(groups.items())
        ]
    total_ops = len({e["op_id"] for entries in groups.values() for e in entries})
    return {"groups": group_list, "total_ops": total_ops}


def sync_view(report) -> dict:
    """Project an already-run `sgt.core.sync.SyncReport` (plan U15/U20) -- `sync` performs a real
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
        "declared_cycles": [list(pair) for pair in report.declared_cycles],
        "identity_events": list(report.identity_events),
    }


def land_view(report) -> dict:
    """Project an already-run `sgt.core.sync.LandReport` (plan U23, C9) -- like `sync_view`, `land`
    performs real git plumbing (a branch-record CAS), so the CLI runs `sync.land(...)` itself and
    hands the result here. `landed` is the CAS outcome; `blocked_reason` is set (and `landed` False)
    when the land was refused -- a red/absent oracle (LAW-G), an open fork, or persistent contention.
    Additive-only (R21)."""
    return {
        "branch": report.branch,
        "landed": report.landed,
        "land_sha": report.land_sha,
        "blocked_reason": report.blocked_reason,
        "ops_added": report.ops_added,
        "attempts": report.attempts,
        "forks": [list(triple) for triple in report.forks],
        "open_fork_count": len(report.forks),
        "pin_contradictions": [
            {"kind": c.kind, "members": list(c.members), "detail": c.detail}
            for c in report.pin_contradictions
        ],
        "declared_cycles": [list(pair) for pair in report.declared_cycles],
        "identity_events": list(report.identity_events),
        "advisory": report.advisory,
    }


def sessions_view(repo) -> dict:
    """Every active scratch-tree session (plan U30, D5): name, branches, new-op count since its
    base, and whether its owning pid is still alive. `overlaps` is the early-fork warning's data
    -- pairs of sessions whose new ops touch a shared symbol -- that `sgt session status`/
    `--watch` renders; it is a *report*, never a lock (S6)."""
    from sgt.core import session as session_mod

    sessions = session_mod.list_sessions(repo)
    return {
        "sessions": [
            {
                "name": s.name, "branch": s.branch, "target_branch": s.target_branch,
                "scratch": s.scratch, "new_op_count": len(session_mod.new_op_ids(s)),
                "owner_pid": s.owner_pid, "alive": session_mod.is_alive(s.owner_pid),
            }
            for s in sessions
        ],
        "overlaps": list(session_mod.overlaps(repo)),
    }


def forks_view(repo) -> dict:
    """The open same-symbol forks a prior sync recorded in committed `.sgt/forks.json` (plan U20,
    C4) -- for `sgt forks`. Each fork carries its symbol, its two tips, and the `sgt merge-op`
    remedy that closes it, plus the cheap-to-derive `file` it lives in (`symbol.split("::", 1)[0]`).
    There's no single "current" line span to add beyond that: both tips are, by construction,
    excluded from every verb-visible ideal, so a resolution UI that needs each tip's own content
    calls `fork_detail_view` instead. A pure read of shared state; empty (`{"open": 0, "forks":
    []}`) when there are none."""
    from sgt import state

    records = state.load_json(repo, "forks", default=[])
    return {
        "open": len(records),
        "forks": [{**r, "file": r["symbol"].split("::", 1)[0]} for r in records],
    }


def fork_detail_view(repo, symbol: str) -> dict:
    """Per-tip folded images for one open same-symbol fork (plan U20, C4) -- `sgt forks <symbol>`.
    Each tip is folded on its own downward closure (`order.downset`, over the whole op universe --
    correct here because a fork's two tips are siblings, never each other's predecessor, so a tip's
    own downset structurally excludes the other tip and anything reachable only through it) via
    `Ideal.from_ops` + `code`, so a resolution UI can diff both tips' full file content without a
    separate frontier query per tip. `{"error": ...}` when the symbol has no open fork."""
    from sgt import state
    from sgt.core.fold import code
    from sgt.core.ideal import Ideal
    from sgt.core.lens import _load_declared
    from sgt.core.order import downset
    from sgt.core.store import Store

    records = state.load_json(repo, "forks", default=[])
    record = next((r for r in records if r["symbol"] == symbol), None)
    if record is None:
        return {"error": f"no open fork for {symbol!r}", "symbol": symbol}

    ops = Store(repo).all_ops()
    declared = _load_declared(repo)
    tips = []
    for tip in record["tips"]:
        ideal = Ideal.from_ops(downset(tip, ops, declared), ops, declared)
        materialized = code(ideal, ops)
        tips.append({
            "op_id": tip,
            "files": {path: content.decode("utf-8", "replace") for path, content in materialized.items()},
        })
    return {"symbol": symbol, "tips": tips, "remedy": record["remedy"]}


def fold_view(repo, *, ref=None, at_commit_index=None, op_ids=None) -> dict:
    """A side-effect-free fold of an arbitrary frontier -- a ref's ideal, every op at or before a
    commit-index position on `history_view`'s axis, or an explicit op-id set -- without checking
    anything out. Powers the composition workbench's draggable playhead and fork-tip diffs. Exactly
    one of `ref`/`at_commit_index`/`op_ids` must be given. Returns `code(I)` (UTF-8 text, replacing
    undecodable bytes -- this is a preview, not a byte-exact export) plus that exact op-set's
    oracle verdict (`verdict_for`). A candidate that isn't a valid ideal (forked, or not downward-
    closed) is never raised through the API: it's reported as `{"forked": True, "message": ...}`,
    the same conversion `sgt.core.verbs._validated` already does for verb previews."""
    from sgt.core.fold import code
    from sgt.core.ideal import Ideal
    from sgt.core.lens import _load_declared, ideal_for_ref
    from sgt.core.oracle import verdict_for
    from sgt.core.store import Store

    given = [x for x in (ref, at_commit_index, op_ids) if x is not None]
    if len(given) != 1:
        return {"error": "fold requires exactly one of ref, at_commit_index, op_ids"}

    store = Store(repo)
    ops = store.all_ops()
    declared = _load_declared(repo)

    if ref is not None:
        ideal = ideal_for_ref(repo, ref, store)
    else:
        if at_commit_index is not None:
            hist = history_view(repo, full=True)  # needs the unpaged per-op commit_index axis
            frontier_ids = frozenset(o["id"] for o in hist["ops"] if o["commit_index"] <= at_commit_index)
        else:
            frontier_ids = frozenset(op_ids)
        try:
            ideal = Ideal.from_ops(frontier_ids, ops, declared)
        except ValueError as e:
            return {"forked": True, "message": str(e)}

    materialized = code(ideal, ops)
    return {
        "op_count": len(ideal.op_ids),
        "files": {path: content.decode("utf-8", "replace") for path, content in materialized.items()},
        "oracle_verdict": verdict_for(repo, ideal),
    }


def _atom_prompt(repo, atom) -> str | None:
    """The best available recorded prompt for one atom (plan U3/U6): try its own commit sha
    first (`sgt session start --task` keys land here indirectly only via provenance, but a direct
    per-commit key is checked too for forward-compat), then any plan-id, then any session-name --
    the same three key kinds `Attribution` carries. `None` when nothing was ever recorded; the
    commit subject (already present on every atom) is the fallback human label, never this."""
    from sgt.intent.prompts import prompt_for

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
    return None


def intent_view(repo) -> dict:
    """The intent overlay's one canonical projection (plan U6, KTD3/KTD7): every commit-keyed
    `IntentAtom` (rung 0/1, recomputed on read -- cheap and pure, like `map_view`'s coupling
    edges) and every persisted, LLM-named `theme` (rung 2, read from `.sgt/intent/themes.json`
    if `sgt intent build` has run; `[]` otherwise -- a UI renders "run `sgt intent build`", never
    an error). Each carries its dependency-graph-backed `tier` (`coupled` | `co-changed` |
    `thematic`, KTD3) and `feature_span` -- the "across features" claim is computed here, never
    asserted by a client. A theme also carries `stale_shas` (plan U5/KTD4): persisted member
    shas that no longer resolve against the current atom partition (e.g. a rebase/amend) are
    reported here rather than silently dropped, so a reader sees the theme is diminished before
    acting on it -- this view never refuses to render, only `sgt intent revert` does. Fully
    sorted for a stable projection, like every other view in this module."""
    from sgt import state
    from sgt.core import lens as _lens
    from sgt.core.store import Store
    from sgt.intent import group
    from sgt.lens.tree import load as load_tree

    all_ops = Store(repo).all_ops()
    declared = _lens._load_declared(repo)
    tree_result = load_tree(repo)
    op_leaf = tree_result["op_leaf"] if tree_result else {}

    atoms = group.atoms(repo)
    atoms_by_sha = {a.commit_sha: a for a in atoms}

    atoms_out = []
    for atom in atoms:
        span = group.feature_span(atom.op_ids, op_leaf)
        commit_shas = frozenset() if atom.commit_sha == group.UNWITNESSED else frozenset({atom.commit_sha})
        tier = group.tier(atom.op_ids, commit_shas, all_ops, declared, op_leaf)
        atoms_out.append({
            "commit_sha": atom.commit_sha,
            "subject": atom.subject,
            "op_ids": sorted(atom.op_ids),
            "feature_span": sorted(span),
            "tier": tier,
            "prompt": _atom_prompt(repo, atom),
        })
    atoms_out.sort(key=lambda a: (a["commit_sha"] == group.UNWITNESSED, a["commit_sha"]))

    themes_persisted = state.load_json(repo, "intent_themes", default={})
    themes_out = []
    for theme_id, entry in sorted(themes_persisted.items()):
        member_shas = frozenset(entry["atom_shas"])
        stale_shas = member_shas - frozenset(atoms_by_sha)
        op_ids = frozenset().union(*(atoms_by_sha[sha].op_ids for sha in member_shas if sha in atoms_by_sha))
        span = group.feature_span(op_ids, op_leaf)
        tier = group.tier(op_ids, member_shas, all_ops, declared, op_leaf)
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

    return {"themes": themes_out, "atoms": atoms_out}


def compose_view(repo, *, full: bool = False) -> dict:
    """One aggregate for a workbench refresh: `map`/`history`/`status`/`forks`/`plan`/`drift`/
    `sessions`/`trust`/`intent`, the current ideal's oracle verdict, and a lightweight open-
    proposal list, each delegated to its own view function with no reshaping. Collapses what
    would otherwise be ~9 separate `sgt <verb> --json` shell-outs (each a fresh process) into one
    call -- the single biggest responsiveness win for a UI that refreshes on every `.sgt/` change.

    `full` threads into every child that accepts it (`history`/`plan`/`drift`/`trust`); `map`/
    `status`/`forks`/`sessions`/`intent` take no `full` param and are always their own single
    shape. The default (`full=False`) delegates each child at *its own* default, so
    `compose_view(repo) == {"map": map_view(repo), "history": history_view(repo), ...}` stays an
    exact identity -- R21's byte-equality guardrail, unchanged by this compaction."""
    from sgt.core import propose
    from sgt.core.lens import current_ideal
    from sgt.core.oracle import verdict_for

    proposals = [
        {
            "id": p.id, "title": p.title, "base_ref": p.base_ref, "created_ts": p.created_ts,
            "delta_op_count": len(p.delta_ids), "feature_delta": sorted(p.feature_delta),
        }
        for p in propose.all_proposals(repo)
    ]
    return {
        "map": map_view(repo),
        "history": history_view(repo, full=full),
        "status": status_view(repo),
        "forks": forks_view(repo),
        "plan": plan_view(repo, full=full),
        "drift": drift_view(repo, full=full),
        "sessions": sessions_view(repo),
        "trust": trust_view(repo, full=full),
        "intent": intent_view(repo),
        "oracle_verdict": verdict_for(repo, current_ideal(repo)),
        "proposals": proposals,
    }


def proposal_view(repo, proposal_id: str) -> dict:
    """The proposal review surface (plan U24, C10): a base+Δ review object projected for a client --
    the feature delta (each touched feature's id/label/op-count), Δ's op count, the oracle claim for
    base∪Δ, a provenance summary (the structured attribution + witnessing shas across Δ), and the
    full staleness `status` (current / clean-reunion / fork, with the `merge-op` remedy). Pure and
    deterministic; `{"error": ...}` for an unknown id. `render_github` is a pure projection of exactly
    this shape."""
    from sgt.core import propose
    from sgt.core.store import Store
    from sgt.lens.tree import load as load_tree

    p = propose.load(repo, proposal_id)
    if p is None:
        return {"error": f"no proposal {proposal_id!r}", "id": proposal_id}

    st = propose.status(repo, proposal_id)
    by_id = {op.id: op for op in Store(repo).all_ops()}
    delta_ids = list(p.delta_ids)

    tree_result = load_tree(repo)
    nodes = tree_result["nodes"] if tree_result else {}
    op_leaf = tree_result["op_leaf"] if tree_result else {}
    leaf_op_count: dict[str, int] = {}
    for op_id in delta_ids:
        leaf = op_leaf.get(op_id)
        if leaf is not None:
            leaf_op_count[leaf] = leaf_op_count.get(leaf, 0) + 1
    feature_delta = [
        {"feature_id": fid, "label": nodes.get(fid, {}).get("label", fid), "op_count": leaf_op_count.get(fid, 0)}
        for fid in sorted(p.feature_delta)
    ]

    # Provenance across Δ: the structured attribution (D7) keyed by witnessing sha, with every bare
    # provenance sha folded in even when it carries no session/agent/plan -- a sorted, stable list.
    prov: dict[str, dict] = {}
    for op_id in delta_ids:
        op = by_id.get(op_id)
        if op is None:
            continue
        for a in sorted(op.attribution, key=lambda a: a.sha):
            entry = prov.setdefault(a.sha, {"sha": a.sha})
            for f in ("session", "agent", "plan"):
                if getattr(a, f) is not None:
                    entry[f] = getattr(a, f)
        for sha in op.provenance:
            prov.setdefault(sha, {"sha": sha})
    provenance = [prov[s] for s in sorted(prov)]

    return {
        "id": p.id,
        "base_ref": p.base_ref,
        "title": p.title,
        "description": p.description,
        "feature_delta": feature_delta,
        "delta_op_count": len(delta_ids),
        "claim": st["claim"],
        "provenance": provenance,
        "status": st,
    }


def proposal_review_view(repo, proposal_id: str) -> dict:
    """`proposal_view` plus what a partial-accept UI needs (plan U32, S8) without computing
    anything itself: the U24 ``approvals`` schema, and a ``feature_checklist`` -- each delta
    feature's entry from `proposal_view`'s ``feature_delta``, plus ``op_ids`` (this feature's own Δ
    op ids, so a caller can build an `accept_ids` set without recomputing feature attribution) and
    ``requires``: the *other* delta features whose ops sit in this feature's closure over base∪Δ
    (chain/reference/declared edges, `order.downset_in`, restricted to Δ -- the same closure
    primitive `sgt select`/`sgt why` (U29) trace, just scoped to a proposal's op-set instead of the
    current ideal). Un-checking a feature while a still-checked feature ``requires`` it would make
    the accepted subset fail `propose.land`'s downward-closure check; this field lets a caller
    (`sgt propose land --subset`, or a future checkbox rail) refuse/grey-out that choice with a
    name, not just a raw op-id failure. `{"error": ...}` for an unknown id, same as `proposal_view`.
    """
    from sgt.core import lens, order, propose
    from sgt.core.store import Store
    from sgt.lens.tree import load as load_tree

    view = proposal_view(repo, proposal_id)
    if "error" in view:
        return view
    p = propose.load(repo, proposal_id)
    assert p is not None  # proposal_view already confirmed it loads

    ops = Store(repo).all_ops()
    declared = lens._load_declared(repo)
    delta_ids = frozenset(p.delta_ids)
    union_ids = frozenset(p.base_ideal_ids) | delta_ids

    tree_result = load_tree(repo)
    op_leaf = tree_result["op_leaf"] if tree_result else {}
    feature_ops: dict[str, set[str]] = {}
    for op_id in delta_ids:
        leaf = op_leaf.get(op_id)
        if leaf is not None:
            feature_ops.setdefault(leaf, set()).add(op_id)

    checklist = []
    for f in view["feature_delta"]:
        fid = f["feature_id"]
        closure: set[str] = set()
        for op_id in feature_ops.get(fid, ()):
            closure |= order.downset_in(op_id, union_ids, ops, declared)
        requires = sorted({
            op_leaf[oid] for oid in (closure & delta_ids)
            if op_leaf.get(oid) not in (None, fid)
        })
        checklist.append({**f, "op_ids": sorted(feature_ops.get(fid, ())), "requires": requires})

    return {**view, "approvals": list(p.approvals), "feature_checklist": checklist}


def _drift_paths(repo, materialized: dict[str, bytes]) -> list[str]:
    from pathlib import Path

    repo_path = Path(repo)
    drift = []
    for path, expected in materialized.items():
        full = repo_path / path
        actual = full.read_bytes() if full.is_file() else None
        if actual != expected:
            drift.append(path)
    return sorted(drift)


def status_view(repo) -> dict:
    """A kernel-backed summary (plan U13): file/symbol/feature counts, R7's coverage fraction
    (reusing `state_view`'s definition), the oracle's overall status, and working-tree drift --
    paths whose on-disk bytes no longer match `code(current_ideal)` (e.g. an edit made outside
    `sgt`, or a verb applied without re-writing the working tree)."""
    from sgt import state as state_mod
    from sgt.core.fold import code
    from sgt.core.lens import current_ideal, sync_status
    from sgt.core.oracle import overall_status
    from sgt.core.op import is_bottom
    from sgt.core.store import Store
    from sgt.lens.tree import load as load_tree

    st = state_view(repo)
    ops = Store(repo).all_ops()
    ideal = current_ideal(repo)
    by_id = {op.id: op for op in ops}
    symbol_count = sum(
        1 for sym, op_id in ideal.frontier(ops).items() if not is_bottom(by_id[op_id].footprint[sym][1])
    )

    tree_result = load_tree(repo)
    feature_count = sum(1 for nd in tree_result["nodes"].values() if not nd["children"]) if tree_result else 0

    from sgt.core.lens import materialization_skips

    materialized = code(ideal, ops)
    drift = _drift_paths(repo, materialized)
    skips = materialization_skips(repo, materialized, ops)
    open_forks = state_mod.load_json(repo, "forks", default=[])

    return {
        "files": len(st["covered_paths"]),
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
        "forks": {"open": len(open_forks), "records": open_forks},
        "sync_status": sync_status(repo),
    }
