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
* ``grid_view``         — the canonical lane×commit cell join (U1): every (feature, commit) cell
  that carries ops, the commit axis, active-plan ghost cells, and per-commit mining-fidelity marks
  — the one join every grid surface (CLI `sgt log`, TUI, VS Code webview) renders, computed once.
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
* ``suggestion_view``   — the U7 clustering/merge suggestion queue: every open `merge`/`split`/
  `conflict` suggestion a clustering-critic or a sync conflict (U6) recorded, for the user to
  accept (via a feature verb) or dismiss. Clustering proposes; the user disposes (R4).
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

    if isinstance(feature_refs, str):  # a discriminated single spec → the universal resolver (U1)
        return resolve_selection(repo, feature_refs)
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


def resolve_selection(repo, spec: str) -> dict:
    """The universal selection resolver's projection (plan U1/KTD1): resolve any `sgt select <spec>`
    form -- exact `file::symbol`, glob, authored-feature ref, clustered-feature ref, explicit id set,
    or an NL phrase -- into the resolved direct/closure op sets, the closure counts, a display label,
    and (on an ambiguous NL phrase) the ranked candidates. Report-only, like `select` (see
    `sgt.lens.select`'s docstring -- the U25 BET-C gate that ruled out silent materialization)."""
    from sgt.lens.select import resolve

    result = resolve(repo, spec)
    return {
        "ok": result.ok, "message": result.message, "label": result.label,
        "direct_ops": sorted(result.direct_ops), "closure": sorted(result.closure),
        "direct_op_count": result.direct_op_count, "closure_op_count": result.closure_op_count,
        "files": list(result.files),
        "candidates": list(result.candidates),
    }


def why_view(repo, op_ref: str, for_feature: str | None = None) -> dict:
    """One op's feature attribution (plan U29): with no `for_feature`, the plurality vote that
    assigned it (every leaf its footprint touched, and how many symbols voted for each); with
    `for_feature`, the exact chain that pulled it into that feature's selection closure."""
    from sgt.lens.select import why

    result = why(repo, op_ref, for_feature)
    # A commit sha is not an op-id (`why` resolves ops/symbols; a sha maps to a whole atom, not one
    # op), so when op resolution fails and no `--for` feature was asked, try resolving the ref as a
    # commit and answer "the aligned words for this commit" -- the contract's third `why` selector
    # (plan §3.4). Op resolution is tried first so every existing `why <op>`/`<symbol>` call is
    # byte-unchanged; only a ref that is *not* an op falls through here.
    if not result.ok and for_feature is None:
        commit = _commit_why(repo, op_ref)
        if commit is not None:
            return commit
        # All three selectors failed. `verbs._resolve`'s message names only the two *it* tried
        # (op-id, symbol), which is right for revert/restore but under-reports here -- `why` also
        # accepts a commit, and a user who pasted a sha deserves to be told that is what was
        # checked, not to be corrected about op-ids they never mentioned.
        return {
            "kind": "op", "ok": False, "op_id": None, "feature_id": None,
            "for_feature": None, "votes": [], "chain": [], "rationale": [],
            "message": (
                f"{op_ref!r} is not an op-id, a live symbol (`path.py::name`), or a commit in "
                f"this repo's history"
            ),
        }
    # Intent-ledger M1: append the recorded "why" -- the rationale reflection derived from the
    # user's own words -- beside the structural attribution. Empty when nothing was captured/derived
    # for this op, which `sgt why` renders as an honest "no recorded reason" rather than a guess.
    rationale = []
    if result.op_id:
        from sgt.intent.rationale import for_op
        rationale = [
            {
                "reason": r["reason"], "actor": r["actor"], "confirmed": r["confirmed"],
                "open": r.get("open", False), "superseded": r.get("superseded", False),
                "evidence": len(r.get("evidence", [])),
            }
            for r in for_op(repo, result.op_id)
        ]
    if result.ok and "::" in op_ref:
        # A symbol question ("why is `add` the way it is?") is about the symbol's whole recorded
        # history, not just the single op the ref resolved to (usually the latest): earlier ops on
        # the same symbol carry rationale too (testbed 2026-07-31: the priority reasoning was
        # invisible from `feature why <sym>` once the due-date op became the resolution target).
        from sgt.intent.rationale import recall
        seen = {r["reason"] for r in rationale}
        for hit in recall(repo, [op_ref])["rationale"]:
            if hit["reason"] not in seen:
                seen.add(hit["reason"])
                rationale.append({
                    "reason": hit["reason"], "actor": hit["actor"], "confirmed": hit["confirmed"],
                    "open": False, "superseded": False, "evidence": hit["evidence"],
                })
    return {
        "kind": "op",
        "ok": result.ok, "message": result.message, "op_id": result.op_id,
        "feature_id": result.feature_id, "for_feature": result.for_feature,
        "votes": list(result.votes), "chain": list(result.chain),
        "rationale": rationale,
    }


def _commit_why(repo, ref: str) -> dict | None:
    """Resolve `ref` as a commit sha (full or a unique prefix over the intent atoms) and answer with
    that commit's aligned words -- the `sgt why <sha>` selector. `None` when `ref` matches no commit
    or is an ambiguous prefix, so the caller falls back to the op-scoped `why`'s own error. Reuses
    `intent_view`'s canonical per-atom projection (words, sessions, resume handles) rather than
    re-deriving the joins, and layers the same actor/confirmed/evidence-badged rationale the
    op-scoped path renders, so `sgt why` reads consistently whether asked about an op or a commit."""
    from sgt.intent import group

    atoms = intent_view(repo)["atoms"]
    cand = sorted({
        a["commit_sha"] for a in atoms
        if a["commit_sha"] != group.UNWITNESSED
        and (a["commit_sha"] == ref or a["commit_sha"].startswith(ref))
    })
    if len(cand) != 1:
        return None  # 0 = not a commit; >1 = ambiguous prefix -- a longer ref disambiguates
    sha = cand[0]
    atom = next(a for a in atoms if a["commit_sha"] == sha)

    from sgt.intent.rationale import for_op
    rationale: list[dict] = []
    seen: set[str] = set()
    for op_id in atom["op_ids"]:
        for r in for_op(repo, op_id):
            reason = r.get("reason")
            if r.get("superseded") or not reason or reason in seen:
                continue
            seen.add(reason)
            rationale.append({
                "reason": reason, "actor": r["actor"], "confirmed": r["confirmed"],
                "open": r.get("open", False), "superseded": False,
                "evidence": len(r.get("evidence", [])),
            })
    return {
        "kind": "commit", "ok": True, "message": "", "sha": sha,
        "subject": atom["subject"], "op_count": len(atom["op_ids"]),
        "words": atom["prompt"], "feature_span": atom["feature_span"],
        "claude_session_ids": atom["claude_session_ids"],
        "session_ids": atom["session_ids"], "plan_ids": atom["plan_ids"],
        "rationale": rationale,
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

    # A `<feature>@<n>` or `<feature>:<slug>` checkpoint preview (the intent-segment rewind unit):
    # resolve to its deterministic op-set and preview the same op-set revert `sgt revert` applies,
    # so a UI hover paints the real blast radius rather than an empty (unresolvable) preview.
    if verb == "revert" and ("@" in target or ":" in target):
        from sgt.intent.segment import resolve_checkpoint

        resolved = resolve_checkpoint(repo, target)
        if resolved is not None:
            op_ids, _label = resolved
            return _project_verb_preview(repo, verbs.plan_revert_op_set(repo, target, op_ids))

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


def _coupling_rows(ops, op_leaf, removed_ids, after_ids) -> list[dict]:
    """U4/R3: surface when a removal drops a residue op in a file another feature still has live
    entities in -- the shared whitespace the U32 corruption cuts through. A residue is the byte
    separator between a file's entities, so removing one that a *surviving* entity of a different
    feature sits beside can splice that feature's file (the U32 case); naming it here means a
    revert/restore/checklist shows which feature it reaches into, rather than only leaving the
    corruption visible in the raw byte diff. File-granular (conservative: it flags a shared file,
    not a proven adjacency), so it over-warns rather than silently cutting.

    Pure over the already-loaded `ops` (no images needed -- footprint + `op_leaf` only)."""
    from sgt.core.op import _symbol_kind

    by_id = {op.id: op for op in ops}

    def path_of(op_id):
        op = by_id.get(op_id)
        if op is None:
            return None
        return next((sym.partition("::")[0] for sym in op.footprint), None)

    # files losing a residue op, and the feature(s) that residue belonged to.
    removed_residue_files: dict[str, set] = {}
    for oid in removed_ids:
        op = by_id.get(oid)
        if op is not None and any(_symbol_kind(s) == "residue" for s in op.footprint):
            removed_residue_files.setdefault(path_of(oid), set()).add(op_leaf.get(oid))

    seen, coupling = set(), []
    for oid in after_ids:                       # surviving ops
        p = path_of(oid)
        if p not in removed_residue_files:
            continue
        feat = op_leaf.get(oid)
        if feat is None:
            continue
        for removed_feat in removed_residue_files[p]:
            key = (p, removed_feat, feat)
            if feat != removed_feat and key not in seen:
                seen.add(key)
                coupling.append({"file": p, "removed_feature": removed_feat, "coupled_feature": feat})
    return sorted(coupling, key=lambda c: (c["file"], str(c["removed_feature"]), str(c["coupled_feature"])))


def _frontier_rows(repo, preview) -> list[dict]:
    """The per-dependent revert frontier (plan U3, R4): each op in the revert target's up-set
    classified on ONE axis, plus the target's read-only prerequisites. Three buckets, one
    vocabulary shared with `_affected_rows` and `rewrite.revert_keep_dependents`:

    * ``blast``      -- a *direct* reference-edge dependent (its content names the reverted
      symbol). Keeping it drafts a continuation hollow. ``toggleable``.
    * ``carry``      -- a *transitive* dependent (in the up-set only via a chain through a direct
      one). Keeping it repoints/carries mechanically (U5, free). ``toggleable``.
    * ``foundation`` -- an upstream prerequisite the reverted core is built on (its downset). A
      revert cannot drop it, so it is read-only (``toggleable: false``), never in the kept-set.

    Each row is ``{op_id, bucket, toggleable}``. This is the exact data the TUI checklist (U9) and
    the CLI ``--keep`` consume; blast/carry are derived the same way `revert_keep_dependents`
    splits its up-set, so the projection matches what apply does. ``[]`` for any non-revert verb
    or a refused preview (``edit`` -- plan U4 -- will reuse this same block)."""
    if preview.verb != "revert" or not preview.ok:
        return []
    from sgt.core import lens, order, verbs
    from sgt.core.ideal import Ideal
    from sgt.core.store import Store

    ops = Store(repo).all_ops()
    # `preview.target` is the raw user ref -- a bare op-id, a `file::symbol`, or a unique op-id
    # prefix. Resolve it to the single op-id the up-set was computed from (the SAME resolution
    # `plan_revert` used), so a symbol/prefix revert gets a frontier too, not only a typed op-id.
    # A ref that isn't a single live op (e.g. a whole-feature revert's set) has no single-op
    # frontier -> bail.
    target, _ = verbs.resolve_target(Ideal.from_ops(preview.before_ids, ops), ops, preview.target)
    if target is None:
        return []
    declared = lens._load_declared(repo)
    removed = preview.removed
    direct = {b for a, b in order.reference_edges(ops) if a == target and b in removed}

    rows = [
        {"op_id": oid, "bucket": "blast" if oid in direct else "carry", "toggleable": True}
        for oid in sorted(removed) if oid != target
    ]
    foundation = order.downset_in(target, preview.before_ids, ops, declared) - {target}
    rows += [{"op_id": oid, "bucket": "foundation", "toggleable": False} for oid in sorted(foundation)]
    return rows


_REVERSIBLE_VERBS = frozenset({"revert", "restore", "after", "merge", "split", "rename", "move"})


def _reversible(verb: str) -> bool:
    """Whether a verb's effect can be walked back with `sgt undo` -- the ideal-edit and
    metadata-reorg verbs -- or is a shared/one-way advance (`land`/`propose`/`push`/`sync`,
    default False). Feeds the consequence pane's escape line: the one bit that changes how
    carefully a user reads the rest of the preview."""
    return verb in _REVERSIBLE_VERBS


def _carry_count(projected: dict) -> int:
    """How many frontier dependents repoint mechanically (the `carry` bucket). Surfaced only as a
    quiet number -- carry is deliberately kept out of the act-required fallout (it needs no
    decision), so the pane can footnote "N auto-repoint" without ever listing them."""
    return sum(1 for r in projected.get("frontier", []) if r.get("bucket") == "carry")


def _fallout_rows(projected: dict) -> list[dict]:
    """The act-required subset of a verb preview -- the items a user must decide about. Toggleable
    `blast` dependents (keeping one drafts a continuation hollow; dropping it lets the symbol
    break) plus one `fork` row per forked symbol. Deliberately EXCLUDES `carry` (mechanical) and
    `foundation` (locked prerequisite): the "so what?" fix is to show what needs a decision, not
    everything touched. Pure over the already-projected dict."""
    rows = [
        {"kind": "blast", "op_id": r["op_id"], "toggleable": True}
        for r in projected.get("frontier", [])
        if r.get("bucket") == "blast" and r.get("toggleable")
    ]
    if projected.get("forked"):
        rows += [
            {"kind": "fork", "symbol": s, "toggleable": False}
            for s in projected.get("affected_symbols", [])
        ]
    return rows


def so_what_for(projected: dict, kept: frozenset = frozenset()) -> str:
    """The one-line consequence of a verb preview -- what breaks, what to do next, whether it can
    be undone -- recomputed live as the pane's kept-set changes. Pure over the projection dict plus
    the caller's kept op-ids (no store reads), so the TUI can call it on every toggle. `primary`
    leads with the `file::symbol` the user named when the target is one (not an alphabetically-first
    dependent from the up-set closure); when the target is an op-id/feature ref it falls back to a
    touched symbol, then to the raw target. Carry is never named."""
    verb = projected.get("verb", "")
    target = projected.get("target")
    primary = (
        target if target and "::" in target
        else (projected.get("affected_symbols") or [None])[0] or target or "this"
    )
    undo = ("Undo-able." if projected.get("reversible", _reversible(verb))
            else "Not auto-undoable — review carefully.")

    if verb == "land":
        branch = projected.get("target") or "the branch"
        if not projected.get("clean", True):
            return f"Can't land onto {branch} yet — {projected.get('error') or 'tree not ready'}."
        forks = projected.get("forks") or []
        if forks:
            sym, a, b = forks[0]
            more = f" (+{len(forks) - 1} more)" if len(forks) > 1 else ""
            return (f"Won't advance {branch} — {len(forks)} fork(s) block it{more}. "
                    f"Resolve first: sgt resolve {sym}.")
        if not projected.get("oracle_configured", True):
            return (f"Won't advance {branch} — no oracle configured; land refuses an "
                    f"unverified op-set (LAW-G).")
        n = projected.get("ops_added", 0)
        return f"Advances {branch} by {n} op — runs the oracle (tests) then CAS. {undo}"

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
            return f"Can't resolve {sym} yet — {projected.get('error') or 'no drafted reconciliation'}."
        return (f"Resolves the fork on {sym}: fulfills your merged edit, runs the oracle, then lands "
                f"it (closes the fork). {undo}")

    if not projected.get("ok", True):
        if projected.get("forked"):
            return (f"Won't apply — {verb} of {primary} would fork it. "
                    f"Resolve the fork first (sgt resolve {primary}).")
        return f"Won't apply — {projected.get('message') or 'refused'}."

    blast_ids = {r["op_id"] for r in projected.get("fallout", []) if r["kind"] == "blast"}
    n = len(blast_ids)
    n_kept = len(set(kept) & blast_ids)
    n_break = n - n_kept

    if verb == "revert":
        if n == 0:
            return f"Removes {primary}. Nothing depends on it — clean revert. {undo}"
        kept_clause = f", keeping {n_kept}" if n_kept else ""
        return f"{primary} will break — {n_break} dependent(s) to re-draft{kept_clause}. {undo}"
    if verb == "restore":
        return f"Re-adds {primary} and its prerequisites. Nothing to reconcile. {undo}"
    meta_phrase = {
        "merge": "Merges into", "split": "Splits", "rename": "Relabels to",
        "move": "Moves ops onto", "after": "Reorders",
    }.get(verb)
    if meta_phrase is not None:
        return f"{meta_phrase} {primary} — metadata only, code untouched. {undo}"
    return f"{verb} {primary}. {undo}"


def _project_verb_preview(repo, preview) -> dict:
    """Given an already-computed `sgt.core.verbs.VerbPreview`, the per-file before/after bytes
    plus the rest of `verb_preview_view`'s shape. Factored out so a caller that resolves its own
    preview -- `sgt.cli`'s `revert <feature>` (plan U13), which tries a feature id/label before
    falling back to `verb_preview_view`'s op/symbol dispatch -- gets the identical projection
    without re-deriving the byte diff."""
    from sgt.core.fold import code
    from sgt.core.ideal import Ideal
    from sgt.core.store import Store
    from sgt.lens.tree import load as load_tree

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
    tree = load_tree(repo)
    op_leaf = tree["op_leaf"] if tree else {}
    projected = {
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
        "coupling": _coupling_rows(ops, op_leaf, preview.removed, preview.after_ids),
        "frontier": _frontier_rows(repo, preview),
    }
    # The "so-what" layer (consequence pane): reversibility bit, act-required fallout, the hidden
    # carry count, and the one-line consequence. Pure over `projected`, so every surface that reads
    # this dict (CLI gate, TUI pane, VS Code) inherits them without extra wiring.
    projected["reversible"] = _reversible(preview.verb)
    projected["fallout"] = _fallout_rows(projected)
    projected["carry_count"] = _carry_count(projected)
    projected["so_what"] = so_what_for(projected)
    projected["focus"] = focus_subgraph(preview, repo, so_what=projected["so_what"])
    return projected


def focus_subgraph(preview, repo, *, so_what: str = "") -> dict:
    """The "Focus & Morph" contract for a mutating ideal-edit preview (revert/restore/edit): the
    affected feature subgraph *only*, each node carrying its op-count before and after the edit, so
    a renderer (VS Code webview, TUI pane) can dim the rest of the graph and morph just these nodes
    -- instead of printing "removes 64 ops from X to Y, including …" that nobody can read as a
    consequence. Pure over the preview's before/after op-sets plus the current feature tree; it rolls
    those op-sets up through the same `op_leaf` map `_affected_rows`/`_frontier_rows` use, so a node's
    role matches what the fallout checklist shows.

    Roles are hue-free (the shared invariant: hue = identity, impact = opacity/size/shape/motion):
    * ``target``     -- owns the plurality of touched ops, the acted-on node.
    * ``blast``      -- a feature *losing* ops (it shrinks / collapses in the morph).
    * ``foundation`` -- a feature *gaining* ops, or a live prerequisite the edit is built on (it
      grows / stays lit).
    * ``context``    -- unaffected: summarized by ``context_count``, never listed.

    ``carry`` (a per-op mechanical repoint) adds no new *feature* node -- its ops are already in the
    up-set, so its feature is a ``blast`` node -- and stays a footnote (`_carry_count`), not a role.

    ``so_what`` is passed in (the caller already computed it via `so_what_for`); ``nodes`` is empty
    when no tree has been built or the preview touched no feature (e.g. a whole-file pseudo-symbol),
    so the renderer falls back to the ``so_what`` headline alone."""
    from collections import Counter

    from sgt.lens.tree import load as load_tree

    op_leaf = (load_tree(repo) or {}).get("op_leaf", {})

    def per_feature(op_ids) -> Counter:
        return Counter(op_leaf[o] for o in op_ids if o in op_leaf)

    before, after = per_feature(preview.before_ids), per_feature(preview.after_ids)
    touched = per_feature(preview.removed) + per_feature(preview.added)
    if not touched:
        return {"so_what": so_what, "nodes": [], "edges": [], "context_count": 0}

    # target = the feature owning the most touched ops (the same focus_fid heuristic the CLI uses).
    target_fid = touched.most_common(1)[0][0]

    # Live prerequisites the revert is built on (frontier `foundation`, never removed): name them so
    # the subgraph shows the kept ground the edit stands on, not dim context. Non-revert verbs have
    # no frontier, so this is empty for them.
    prereq_fids = {
        op_leaf[r["op_id"]]
        for r in _frontier_rows(repo, preview)
        if r.get("bucket") == "foundation" and r["op_id"] in op_leaf
    }

    changed = {f for f in (set(before) | set(after)) if before.get(f, 0) != after.get(f, 0)}
    focus_ids = changed | prereq_fids

    mv = map_view(repo)
    node_by_id = {n["id"]: n for n in mv["nodes"]}
    nodes = []
    for fid in sorted(focus_ids):
        ob, oa = before.get(fid, 0), after.get(fid, 0)
        if fid == target_fid:
            role = "target"
        elif oa > ob or (fid in prereq_fids and ob == oa):
            role = "foundation"
        else:
            role = "blast"
        nodes.append({
            "feature_id": fid,
            "label": (node_by_id.get(fid) or {}).get("label", fid[:8]),
            "role": role,
            "ops_before": ob,
            "ops_after": oa,
        })

    edges = [
        {"a": e["a"], "b": e["b"]}
        for e in mv["edges"]
        if e["a"] in focus_ids and e["b"] in focus_ids
    ]
    focus_features = sum(1 for fid in focus_ids if (node_by_id.get(fid) or {}).get("kind") == "feature")
    context_count = max(mv["feature_count"] - focus_features, 0)
    return {"so_what": so_what, "nodes": nodes, "edges": edges, "context_count": context_count}


def _project_feature_preview(repo, verb: str, preview) -> dict:
    """Project a metadata-only feature-reorg preview (`merge`/`rename`/`move`/`split`) into the
    shared consequence shape the pane and `so_what_for` consume. These verbs touch no code, so
    `fallout` and `carry_count` are empty and `reversible` is always True; the projection instead
    carries a short human `summary` (resolved labels, counts, the split groups) the pane renders in
    place of the code rail. Only call on an `ok` preview -- a refusal is handled by the CLI's
    `_fail_preview` before this point."""
    from sgt.lens.tree import load as load_tree

    nodes = (load_tree(repo) or {}).get("nodes", {})

    def label(fid: str) -> str:
        return (nodes.get(fid) or {}).get("label") or fid[:8]

    if verb == "merge":
        primary = label(preview.survivor_id)
        summary = [f"absorb {label(preview.absorbed_id)} → {primary}",
                   f"{preview.op_count} op(s) · {preview.member_count} member(s)"]
    elif verb == "rename":
        primary = preview.new_label
        summary = [f"{preview.old_label!r} → {preview.new_label!r}"]
    elif verb == "move":
        primary = label(preview.target_id)
        summary = [f"{len(preview.op_ids)} op(s) → {primary}"]
    elif verb == "split":
        primary = label(preview.feature_id)
        g0, g1 = preview.groups or ((), ())
        summary = [
            f"{primary} splits in two:",
            f"  keep ({len(g0)}): {', '.join(sorted(g0)[:6])}" + (" …" if len(g0) > 6 else ""),
            f"  new  ({len(g1)}): {', '.join(sorted(g1)[:6])}" + (" …" if len(g1) > 6 else ""),
            f"→ mints {preview.new_id[:8]}",
        ]
    else:  # pragma: no cover -- only the four reorg verbs are projected here
        primary, summary = verb, []

    projected = {
        "ok": True, "verb": verb, "target": primary,
        "affected_symbols": [primary] if primary else [],
        "forked": False, "message": getattr(preview, "message", ""),
        "files": {}, "fallout": [], "carry_count": 0, "reversible": True,
        "summary": summary,
    }
    projected["so_what"] = so_what_for(projected)
    return projected


def _project_land_preview(repo, plan) -> dict:
    """Project a `sgt.core.sync.LandPlan` dry-run into the shared consequence shape the pane and
    `so_what_for` consume. A land advances shared state, so `reversible` is False (the pane's escape
    line reads "review carefully") and there is no toggleable `fallout` -- a fork isn't a
    keep/drop choice, it's a blocker to resolve. The graph rail is precomputed into `summary` (like
    the feature-reorg projection) because a land has no code-diff rail; it draws where your work is
    going and what stops it (`render_collab_preview_lines`)."""
    from sgt.tui.graph import render_collab_preview_lines

    projected = {
        # LAW-G pre-refuses a land with no oracle before it stages anything, so a no-oracle plan is
        # not `ok` even though ingest/resolve found no fork -- the honest bit for the pane.
        "ok": plan.clean and not plan.forks and plan.oracle_configured,
        "verb": "land",
        "target": plan.branch,
        "affected_symbols": [f[0] for f in plan.forks],
        "forked": bool(plan.forks),
        "files": {},
        "message": plan.error or "",
        "fallout": [],
        "carry_count": 0,
        "reversible": False,
        "clean": plan.clean,
        "error": plan.error,
        "ops_added": plan.ops_added,
        "forks": [list(t) for t in plan.forks],
        "pin_contradictions": [
            {"kind": c.kind, "members": list(c.members), "detail": c.detail}
            for c in plan.pin_contradictions
        ],
        "declared_cycles": [list(pair) for pair in plan.declared_cycles],
        "oracle_configured": plan.oracle_configured,
        "advisory": plan.advisory,
    }
    # What `sgt undo` will do *after* this land -- the one thing the "not reversible" line above
    # doesn't answer, and the thing the user actually asks next. The shared advance itself is
    # one-way either way; what differs is whether undo has anything local to act on. Landing the
    # branch you are standing on journals an ordinary ideal_edit, so undo works and writes a
    # forward correction; landing any other branch only moved a ref other people read, so undo
    # refuses. (`sgt/core/sync/land.py:317` vs `:334`.)
    try:
        from sgt.store.gitbind import GitBinding
        gb = GitBinding(repo)
        checked_out = gb.symbolic_ref() == f"refs/heads/{plan.branch}"
    except Exception:  # noqa: BLE001 -- an advisory line must never break a preview
        checked_out = None
    projected["checked_out"] = checked_out
    projected["undo_note"] = (
        "the shared advance is one-way; `sgt undo` afterward writes a local forward correction"
        if checked_out else
        "the shared advance is one-way; `sgt undo` will refuse it (it only moved a ref others read)"
        if checked_out is False else ""
    )
    projected["so_what"] = so_what_for(projected)
    projected["summary"] = render_collab_preview_lines(projected, color=True)
    return projected


def land_preview_view(repo, branch: str | None = None) -> dict:
    """The dry-run consequence of `sgt land [branch]` (plan U19/D4) -- what the CAS *would* advance
    the shared branch by, computed side-effect-free (`sync.plan_land`: `ingest -> resolve`, no
    oracle, no ref move, rolled back to leave no trace). Unlike `land_view` (which projects an
    already-run land), this is a pure read the CLI can call to show a feedforward pane before the
    one-way advance. Carries the shared consequence shape (`so_what`, `reversible` False, the graph
    `summary`) plus the structured fields (`ops_added`, `forks`, `oracle_configured`)."""
    from sgt.core import sync as sync_mod

    plan = sync_mod.plan_land(repo, branch=branch)
    return _project_land_preview(repo, plan)


def _project_sync_preview(repo, plan) -> dict:
    """Project a `sgt.core.sync.SyncPlan` dry-run into the shared consequence shape. Unlike `land`, a
    sync fork does not block -- the fork-free part still merges and the fork surfaces as state -- so
    `forks` here is *what would surface*, and `ok` is False only to flag that attention is needed,
    not that nothing happens. No clean-tree precondition, so `clean` is always True; the graph rail
    is precomputed into `summary` (`render_collab_preview_lines`)."""
    from sgt.tui.graph import render_collab_preview_lines

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
        "declared_cycles": [list(pair) for pair in plan.declared_cycles],
        "base_recovery": plan.base_recovery,
        "theirs_recovery": plan.theirs_recovery,
    }
    projected["so_what"] = so_what_for(projected)
    projected["summary"] = render_collab_preview_lines(projected, color=True)
    return projected


def sync_preview_view(repo, remote: str | None = None, branch: str | None = None) -> dict:
    """The dry-run consequence of `sgt sync [remote] [branch]` -- what folding the teammate's branch
    in *would* bring (incoming op count, any fork that would surface, recovery modes), computed
    side-effect-free (`sync.plan_sync`: `fetch -> ingest -> resolve`, no `materialize`, rolled back
    to leave no trace). A pure read the CLI shows as a feedforward pane before the merge lands."""
    from sgt.core import sync as sync_mod

    plan = sync_mod.plan_sync(repo, remote=remote, branch=branch)
    return _project_sync_preview(repo, plan)


def proposal_land_preview_view(repo, proposal_id: str, accept_ids=None) -> dict:
    """The dry-run consequence of `sgt propose land <id> [--subset ...]` -- reuses the shared land
    projection over `propose.plan_land` (a stale-forked proposal surfaces as a fork blocker; an
    up-to-date proposal delegates to `sync.plan_land`). The pane reads exactly as a `land` because
    it *is* one, scoped to the proposal's Δ."""
    from sgt.core import propose

    plan = propose.plan_land(repo, proposal_id, accept_ids=accept_ids)
    return _project_land_preview(repo, plan)


def resolve_apply_preview_view(repo, symbol: str) -> dict:
    """The dry-run consequence of `sgt resolve <symbol> --apply` -- the three-step remedy it will run
    (fulfill the drafted reconciliation from your edited tree, run the oracle, land it, closing the
    fork). Reports `clean=False` with a reason when there's no open fork or no drafted reconciliation
    yet (so the pane refuses before the apply path would). A pure read; the draft lookup mirrors the
    `--apply` path's own."""
    from sgt.config import load_oracle_config
    from sgt.core import rewrite
    from sgt.core.store import Store
    from sgt.tui.graph import render_collab_preview_lines

    fork = next((f for f in forks_view(repo)["forks"] if f["symbol"] == symbol), None)
    store = Store(repo)
    has_draft = any(
        (h := store.get_hollow(hid)) is not None and symbol in h.footprint
        for rec in rewrite.pending_drafts(repo).values()
        for hid in rec["hollow_ids"]
    )
    if fork is None:
        error = f"no open fork for {symbol!r} — run `sgt advanced forks` to list the open forks"
    elif not has_draft:
        error = (f"no drafted reconciliation — run `sgt resolve {symbol}` first, then edit the "
                 f"file to merge both versions")
    else:
        error = None
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
    projected["summary"] = render_collab_preview_lines(projected, color=True)
    return projected


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

    Every node -- leaf (a `label_tree`/Greene-matched feature, `f-<op>` id) or internal (a
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

    # The tree is a DAG: an authored feature (U6) can be spliced under more than one subsystem, so a
    # shared leaf is listed in several parents' `children` yet carries exactly one canonical `parent`.
    # Every projection below -- the emitted `children`, `op_count`, and the session rollup -- walks
    # only the *canonical* children (those whose `parent` points back here), so a shared feature is
    # rendered once, under its one parent. That keeps the feature tree, the workbench timeline, and
    # the TUI consistent; a raw double-listing otherwise double-counts the shared ops in every
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
    from sgt.lens.authored import load_authored
    from sgt.lens.label import fallback_label
    from sgt.lens.tree import _authored_leaf_claims
    authored_claims = _authored_leaf_claims(nodes, load_authored(repo))

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

    from sgt.store.gitbind import is_bookkeeping_message

    gb = GitBinding(repo)
    rows = gb.history()
    commit_index = {sha: i for i, (sha, _parent, _subject) in enumerate(rows)}
    # Which commits are sgt's own mechanics rather than the developer's work. Every commit keeps its
    # index and its place on the time axis -- the grid, the fold frontier, and every op's
    # `commit_index` are unchanged -- but a human-facing list can now drop them instead of telling a
    # developer that `sgt restore f-08ccdb12...` is something they did.
    marked = gb.bookkeeping_shas()
    commits = [
        {"sha": sha, "subject": subject, "index": i,
         "bookkeeping": sha in marked or is_bookkeeping_message(subject)}
        for i, (sha, _parent, subject) in enumerate(rows)
    ]

    tree_result = load_tree(repo)
    op_leaf = tree_result["op_leaf"] if tree_result else {}

    # The shared time-axis rule (`opindex.earliest_commit_sha`): an op's earliest in-history
    # provenance, falling back to the earliest committed `Sgt-Op:` trailer for a *pending* op (just-
    # saved work a `record_ideal` witness-advance left provenance-less). `feature_runs`/`group.atoms`
    # read the same helper so all three time-aware projections agree on when an op happened.
    ops = list(opindex.index_ops(repo))
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
    # "What did I do" is a question about the developer's work, so the window is taken over real
    # saves and sgt's own materializations are reported as a count beside it rather than as rows.
    # `commit_count` stays the honest total (it is the time axis's length, which every index refers
    # to); `save_count` is the number a person would give if asked how much they had done.
    latest_first = list(reversed(commits))
    real_first = [c for c in latest_first if not c["bookkeeping"]]
    return {
        "commit_count": len(commits),
        "save_count": len(real_first),
        "bookkeeping_count": len(commits) - len(real_first),
        "op_count": len(ops_out),
        "kinds": kinds,
        "features": features,
        "latest_commits": real_first[offset:offset + limit],
    }


def _grid_labels(repo) -> dict:
    """Feature-id -> label, the cheap way: the last-built tree's own labels plus the authored-
    feature overrides (U6/KTD4), *without* `map_view`'s `fused_graph` recompute (the expensive
    step `grid_view` must not pay to stay a fast daily surface). Mirrors `map_view`'s label
    resolution exactly, so the grid names a lane the same thing `sgt map` does."""
    from sgt.lens.authored import load_authored
    from sgt.lens.tree import _authored_leaf_claims
    from sgt.lens.tree import load as load_tree

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
    from sgt import state
    from sgt.core.lens import current_ref_key

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
    from sgt.loop.plan import active_sessions

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
    every surface -- the CLI `sgt log`, the TUI, the VS Code webview -- renders, so the (op ->
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
        # `commit_count` is the time axis's length -- every op's `commit_index` refers to it, so it
        # must keep counting sgt's own materialization commits. `save_count` is what a person means
        # by "how many saves": the same axis minus sgt's plumbing. Keeping both is what stops the
        # map's header from disagreeing with `sgt log`'s save list on the same repo.
        "commit_count": len(hv["commits"]),
        "save_count": sum(1 for c in hv["commits"] if not c.get("bookkeeping")),
        "bookkeeping_count": sum(1 for c in hv["commits"] if c.get("bookkeeping")),
        "op_count": sum(len(c["op_ids"]) for c in cells.values()),
        "feature_count": len(features),
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
            affected = [preview.feature_id, preview.new_id]  # the content-addressed id apply mints (KTD4)
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
    from pathlib import Path

    from sgt.core.lens import current_ideal
    from sgt.core.store import Store
    from sgt.lens.tree import load as load_tree

    line_spans, error = _entity_line_spans(repo, file)
    if error:
        # "Not covered by the ideal" is the normal answer for a working-tree file sgt has no op
        # for -- a doc (JOURNAL.md), an untracked config -- not a failure. Reporting it under
        # `error` flips the CLI `--json` exit code to 1, which the editor's per-file blame reads as
        # a hard failure and re-appends "Command failed" every time such a tab is focused. A file
        # that merely lacks coverage reports `covered: False`; only a genuinely absent path errors.
        result = {"file": file, "spans": [], "features": {}, "covered": False}
        if not (Path(repo) / file).exists():
            result["error"] = error
        return result

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
    return {"file": file, "spans": spans, "features": features, "covered": True}


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
    import time

    from sgt.core import opindex
    from sgt.loop import plan as plan_mod
    from sgt.loop.match import compute_checkpoint, session_coverage
    from sgt.loop.plan import STALLED_SECONDS

    by_id = {op.id: op for op in opindex.index_ops(repo)}
    checkpoint = compute_checkpoint(repo)
    # `full` alone carries per-step file-coverage ("did this step's work land, just under a
    # different name?") -- the "why isn't this matched" surface. Kept off the compact path (which
    # `now_view` reads on every refresh) so that hot path stays a pure count read.
    coverage = session_coverage(repo) if full else {}
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


def save_preview_view(repo) -> dict:
    """The in-situ "what would a save land" query: which existing features would gain ops if you
    ran `sgt save` right now. Answers the workbench's ghost-checkpoint render -- a dashed car on
    each affected lane at the frontier -- so the user sees the consequence of saving before saving.

    Feature-granular by design (the workflow-legibility redesign keeps op/symbol reconciliation as
    drill-down, never the default surface): the delta is `get(repo).op_ids - current_ideal(repo).op_ids`
    (the same equality `porcelain._save` uses for "nothing_new"), attributed to the *current* tree via
    the pure `tree.assign_ops_to_leaves` re-attributor -- not the persisted `op_leaf`, which only
    covers committed ops. Ops that touch no owned symbol are the "new work" bucket, reported as a
    count only (v1 does not predict which new lane they'd mint).

    Shape: `{"affected": [{"feature_id", "op_count", "op_ids"}], "new_work_count": int,
    "total_op_count": int}`. Clean tree -> `affected: []`, all counts 0 (no ghosts render).

    NOT fully side-effect-free: `get(repo)` mines the working tree and persists the mined ops +
    sync cache + witness into `.sgt/` (like `sgt save`/`status` already do). It creates no git
    commit and does not advance the recorded ideal, so it is safe as a preview -- but it is a
    mine-on-contact, not a pure read."""
    from sgt.core import opindex
    from sgt.core.lens import current_ideal, get
    from sgt.lens.tree import assign_ops_to_leaves
    from sgt.lens.tree import load as load_tree

    delta = get(repo).op_ids - current_ideal(repo).op_ids
    if not delta:
        return {"affected": [], "new_work_count": 0, "total_op_count": 0}

    by_id = {op.id: op for op in opindex.index_ops(repo)}
    uncommitted = [by_id[oid] for oid in delta if oid in by_id]

    tree_result = load_tree(repo)
    nodes = tree_result["nodes"] if tree_result else {}
    op_leaf = assign_ops_to_leaves(nodes, uncommitted) if nodes else {}

    by_feature: dict[str, list[str]] = {}
    for op in uncommitted:
        leaf = op_leaf.get(op.id)
        if leaf is not None:
            by_feature.setdefault(leaf, []).append(op.id)

    affected = [
        {"feature_id": fid, "op_count": len(ids), "op_ids": sorted(ids)}
        for fid, ids in sorted(by_feature.items())
    ]
    attributed = sum(len(ids) for ids in by_feature.values())
    return {
        "affected": affected,
        "new_work_count": len(uncommitted) - attributed,
        "total_op_count": len(delta),
    }


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


def suggestion_view(repo) -> dict:
    """The U7 clustering/merge suggestion queue: every open suggestion (`merge`/`split`/`conflict`)
    a clustering-critic or a sync conflict (U6) recorded, for `sgt advanced suggestions`. A pure
    read; accepting a suggestion is the existing `sgt feature merge`/`split`/`move`, and dismissing
    it is `sgt advanced suggestions dismiss <id>` -- this view only renders what's queued, the same
    "report, don't invent mutation semantics" boundary `trust_view` follows. Empty
    (`{"count": 0, "suggestions": []}`) when there are none."""
    from sgt.core import suggest

    records = suggest.all_records(repo)
    return {
        "count": len(records),
        "suggestions": [
            {"id": r.id, "kind": r.kind, "features": list(r.features),
             "op_ids": list(r.op_ids), "rationale": r.rationale}
            for r in records
        ],
    }


def _open_fork_records(repo) -> list:
    """The `.sgt/forks.json` records the read/surfacing path should show: filtered to *resolvable*
    forks (`order.resolvable_forks`) -- real symbols whose two tips genuinely diverge. Synthetic
    `__anchor__`/`__residue__` collisions and same-after pseudo-forks (a revert onto identical bytes,
    or an add/delete/re-add rebirth) are neutralized by `fork_free` and carry no `sgt resolve` remedy,
    so they never surface. This also cleans records persisted *before* the write-side filter landed
    (`_record_parked_forks` is union-only and never removes), and drops any record whose tip is no
    longer in the store (`Store.get` -> None). Decodes only the two tip ops per record -- no full
    `all_ops()` scan."""
    from sgt import state
    from sgt.core.order import resolvable_forks
    from sgt.core.store import Store

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
    return [r for r in valid if (r["symbol"], r["tips"][0], r["tips"][1]) in keep]


def forks_view(repo) -> dict:
    """The open same-symbol forks a prior sync recorded in committed `.sgt/forks.json` (plan U20,
    C4) -- for `sgt forks`. Each fork carries its symbol, its two tips, and the `sgt merge-op`
    remedy that closes it, plus the cheap-to-derive `file` it lives in (`symbol.split("::", 1)[0]`).
    There's no single "current" line span to add beyond that: both tips are, by construction,
    excluded from every verb-visible ideal, so a resolution UI that needs each tip's own content
    calls `fork_detail_view` instead. Filtered to resolvable forks via `_open_fork_records` (light
    per-tip store reads); empty (`{"open": 0, "forks": []}`) when there are none."""
    records = _open_fork_records(repo)
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
    separate frontier query per tip. `{"error": ...}` when the symbol has no open (resolvable) fork."""
    from sgt.core.fold import code
    from sgt.core.ideal import Ideal
    from sgt.core.lens import _load_declared
    from sgt.core.order import downset
    from sgt.core.store import Store

    records = _open_fork_records(repo)
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
    from sgt.intent.turns import turns_for

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
    # membership in `plan_sessions` filters out genuine `sgt session` names.
    keys = sorted(set(atom.plan_ids) | set(atom.session_ids))
    if keys:
        from sgt import state as _state
        plan_sessions = _state.load_json(repo, "plan_sessions", default={})
        for p in keys:
            chat = (plan_sessions.get(p) or {}).get("claude_session_id")
            hits = turns_for(repo, chat, key_kind="chat") if chat else []
            if hits:
                return hits[0]["text"]
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

    # Intent-ledger M1 provenance joins, resolved once: which chat session a plan came from
    # (`claude --resume` affordance, plan U12) and the live recorded reason for each op. Both are
    # local-tier reads; absent stores just mean empty enrichment, never an error.
    plan_sessions = state.load_json(repo, "plan_sessions", default={})
    from sgt.intent.rationale import load_rationale
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
        tier = group.tier(atom.op_ids, commit_shas, all_ops, declared, op_leaf)
        # Plural on purpose: one commit's ops can come from several plans/chats (a save landing
        # work from two agent conversations, an op re-witnessed across merges) -- nothing in the
        # ledger is 1:1, so the projection must not collapse it either. Plan ids AND session ids:
        # `confirm_match` stamps the plan-session id into the attribution's `session` field, so
        # the planned path's key arrives as a session id (plan_sessions membership filters the
        # genuine `sgt session` names out).
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

    segments_out = _segments_out(repo, op_leaf, tree_result)
    return {"themes": themes_out, "atoms": atoms_out, "segments": segments_out}


def _commit_words_join(repo):
    """A cheap `sha -> captured words` lookup for the per-chapter zoom, built once (the two stores
    loaded a single time, not reloaded per commit as a naive `label_prompt_for` loop would). Same
    two rung-0 sources `sgt.intent.theme_segment.label_prompt_for` reads: the committed prompt
    sidecar first, then the highest-seq sha-keyed `save -m` turn. Chat-keyed words are deliberately
    excluded -- they arrive with the P2 alignment rung, not this pure-projection stage."""
    from sgt.intent.prompts import load_prompts
    from sgt.intent.turns import load_turns

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


def _segments_out(repo, op_leaf, tree_result) -> list[dict]:
    """The feature-scoped intent segments (the "checkpoints" a user rewinds to): every feature's
    ops cut into contiguous, labeled chapters (`sgt.intent.segment`), each addressable as
    `<feature_id>@<seg_index>`. Deterministic rungs 0/1 on read; if `sgt intent build` has
    persisted LLM labels/boundaries (`.sgt/intent/segments.json`), those override per feature
    -- same read-vs-build split as themes and the feature tree. Each segment carries a `tier`
    (`group.tier`, KTD3) and a `novelty` weight, so a client can dim trivial chapters. Flat,
    sorted by `(feature_id, seg_index)`, for a stable projection."""
    from sgt import state
    from sgt.intent import group
    from sgt.intent import segment as seg_mod

    nodes = tree_result["nodes"] if tree_result else {}
    persisted = state.load_json(repo, "intent_segments", default={})
    label_pins = state.load_json(repo, "intent_segment_pins", default={})
    runs_by_feature = seg_mod.feature_runs(repo, op_leaf)
    words_for = _commit_words_join(repo)

    out: list[dict] = []
    for feature_id in sorted(runs_by_feature):
        segs = seg_mod.overlay_persisted(runs_by_feature[feature_id], persisted.get(feature_id))
        segs = seg_mod.apply_label_pins(segs, label_pins.get(feature_id))
        feature_label = nodes.get(feature_id, {}).get("label", feature_id)
        for s in segs:
            commit_shas = frozenset(s.commit_shas)
            # Per-chapter captured words (intent-ledger P1 zoom): the user's own words for each
            # commit this chapter covers, so the TUI/editor zoom answers "in my own words" -- the
            # data `intent_view` already holds per atom, made addressable per chapter. Deduped,
            # chapter order preserved.
            words: list[str] = []
            for sha in s.commit_shas:
                w = words_for(sha)
                if w and w not in words:
                    words.append(w)
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
                "commit_shas": list(s.commit_shas),
                "words": words,
                "first_index": s.first_index,
                "last_index": s.last_index,
                "novelty": round(s.novelty, 3),
                "tier": tier,
                "source": s.source,
            })
    return out


def segments_view(repo) -> list[dict]:
    """Just the feature-scoped intent segments (`_segments_out`), without the atoms/themes
    machinery `intent_view` also builds (`Store(repo).all_ops()`, `group.atoms`, the declared-ops
    load) -- the cheap read the graph/timeline layouts use so drawing the chunk-car atom doesn't
    pay for the cross-feature theme projection it deliberately no longer draws."""
    from sgt.lens.tree import load as load_tree

    tree_result = load_tree(repo)
    op_leaf = tree_result["op_leaf"] if tree_result else {}
    return _segments_out(repo, op_leaf, tree_result)


def _next_action(in_flight: dict, needs_you: dict, working: dict | None = None) -> dict:
    """The single "do this next" recommendation as a STRUCTURED action (not a rendered string, so
    each surface phrases it in its own idiom), from a fixed priority ladder: an open fork blocks
    everything (its two tips are excluded from every ideal), a stalled plan is a resumable thread,
    then dirty work to save, then guesses to review, else clean. Shape:
    `{kind, command, target, label}` -- `command` is a copy-pasteable shell line (or `None` when
    there's nothing to run, e.g. `clean`, or a fork with no recorded remedy)."""
    forks = needs_you["forks"]
    if forks:
        f = forks[0]
        # Derive the high-level verb from the symbol rather than echoing the stored remedy: a
        # forks.json committed before the remedy switch still names the old low-level `merge-op`,
        # and this is the most prominent "what next" surface (`sgt now`).
        sym = f.get("symbol")
        command = f"sgt resolve {sym}" if sym else f.get("remedy")
        return {"kind": "resolve_fork", "command": command, "target": sym,
                "label": f"resolve fork on {sym}"}
    stalled = needs_you["stalled_plans"]
    if stalled:
        s = stalled[0]
        csid = s.get("claude_session_id")
        # There is no `sgt plan resume` verb; a stalled plan is a Claude Code thread the developer
        # left mid-build, so the resume path is Claude's own `--resume` (plan_view carries the id).
        return {"kind": "resume_plan", "command": f"claude --resume {csid}" if csid else None,
                "target": s["session_id"],
                "label": f"resume stalled plan ({s['pending_count']} step(s) left)"}
    if in_flight["total_op_count"] > 0:
        # A save is offered by what it *records*, not by the store's unit of accounting. The
        # developer's own words are already on the surface one line above; if a message can be
        # suggested from them, the command is copy-pasteable as-is rather than a `-m` to fill in.
        n = in_flight["total_op_count"]
        title = (working or {}).get("full_title") if isinstance(working, dict) else None
        # Only offer a filled-in message when it can be pasted verbatim: a quote in the sentence
        # would break the shell line, and a suggestion the developer has to repair is worse than
        # none. Long asks stay as the sentence they were -- a save message may run long.
        usable = title and '"' not in title and "\\" not in title and "\n" not in title
        command = f'sgt save -m "{title}"' if usable else "sgt save"
        return {"kind": "save", "command": command, "target": None,
                "label": f"save your {n} unsaved edit(s)"}
    reviews = needs_you["reviews"]
    if reviews:
        return {"kind": "review", "command": "sgt intent review", "target": None,
                "label": f"review {len(reviews)} pending alignment(s)"}
    return {"kind": "clean", "command": None, "target": None, "label": "nothing pending"}


def now_view(repo, *, include_preview: bool = True, recent_limit: int = 5) -> dict:
    """The "state of actions" surface (`sgt now`, the extension's Now tree): one THIN, FAST assembler
    over child views answering the four questions a developer orienting mid-session has -- what's
    *in flight*, what *needs me*, what was *recently done*, and what to do *next* -- plus a little
    *context* (latest human turns + the live agent-action feed). On the hot path (every `sgt log`,
    every extension refresh), so it deliberately avoids `intent_view` (which decodes op images, ~85%
    of the store); rationale/feature_span stay a lazy drill-down.

    `include_preview` gates the one mine-on-contact step (`save_preview_view` calls `get(repo)`); the
    seam ships defaulted-on so a cheaper feed-only tick can be split off later without reshaping
    callers. Everything else is a pure read of already-mined state."""
    from sgt.intent import activity as activity_mod
    from sgt.intent import turns as turns_mod
    from sgt.intent.review import pending_reviews

    in_flight = (save_preview_view(repo) if include_preview
                 else {"affected": [], "new_work_count": 0, "total_op_count": 0})

    forks = forks_view(repo)
    reviews = pending_reviews(repo)
    sessions = plan_view(repo)["sessions"]
    stalled = [s for s in sessions if s["derived_status"] == "stalled"]
    # A plan that is actively being built appeared nowhere: only *stalled* plans reached
    # `needs_you`, so a working agent was invisible until it had been quiet for an hour, and the
    # question "what is happening right now" had no answer on the surface built to answer it.
    # This is deliberately not in `needs_you` -- an agent making progress needs nothing from the
    # developer, it just needs to be visible.
    in_progress = [
        {"session_id": s["session_id"], "claude_session_id": s.get("claude_session_id"),
         "matched_count": s.get("matched_count", 0), "step_count": s.get("step_count", 0),
         "pending_count": s["pending_count"],
         "current_title": (s["remaining_titles"] or [None])[0]}
        for s in sessions if s["derived_status"] == "building"
    ]
    needs_you = {
        "forks": forks["forks"],
        "reviews": [{"id": r["id"], "subject": r["subject"], "reason": r["reason"]} for r in reviews],
        "stalled_plans": [
            {"session_id": s["session_id"], "claude_session_id": s.get("claude_session_id"),
             "pending_count": s["pending_count"], "remaining_titles": s["remaining_titles"]}
            for s in stalled
        ],
    }

    recently_done = history_view(repo, limit=recent_limit)["latest_commits"]

    # What the developer is working on, in their own words. The prompt hook has always recorded
    # every ask verbatim, so this needs no declaration step -- which matters because the common way
    # to work is Claude Code's plan mode or a planning plugin, neither of which calls
    # `sgt plan intake`. Without this the surface built to answer "what am I working on" answered
    # with op counts. A prompt older than the last save has already been answered by it, so the last
    # save's committer time is the cutoff.
    from sgt.intent.working import working_on
    from sgt.store.gitbind import GitBinding as _GB

    try:
        last_save_ts = _GB(repo).head_time()
    except Exception:  # noqa: BLE001 -- an advisory line must never break the orient surface
        last_save_ts = None
    working = working_on(repo, active_plans=in_progress, last_save_ts=last_save_ts,
                         has_unsaved=in_flight["total_op_count"] > 0)

    turns_all = sorted(turns_mod.load_turns(repo).values(), key=lambda t: t["ts"], reverse=True)
    context = {
        "turns": [{"text": t["text"], "actor": t["actor"], "ts": t["ts"]} for t in turns_all[:recent_limit]],
        "activity": activity_mod.recent_activity(repo, limit=recent_limit),
    }

    return {
        "working_on": working,
        "in_flight": in_flight,
        "in_progress": in_progress,
        "needs_you": needs_you,
        "recently_done": recently_done,
        "context": context,
        "next_action": _next_action(in_flight, needs_you, working),
    }


def compose_view(repo, *, full: bool = False) -> dict:
    """One aggregate for a workbench refresh: `map`/`history`/`status`/`forks`/`plan`/`drift`/
    `sessions`/`trust`/`intent`/`save_preview`, the current ideal's oracle verdict, and a lightweight
    open-proposal list, each delegated to its own view function with no reshaping. Collapses what
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
        "save_preview": save_preview_view(repo),
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
    from sgt.core.lens import current_ideal, ops_with_frontier_images, sync_status
    from sgt.core.oracle import overall_status
    from sgt.core.op import is_bottom
    from sgt.lens.tree import load as load_tree

    st = state_view(repo)
    ideal = current_ideal(repo)
    # Frontier-selective read: this view folds only `ideal`, so it needs images for exactly the
    # frontier producers -- not `Store.all_ops()`'s every-op images decode (the dominant cost of
    # `sgt log --summary` on a large store).
    ops = ops_with_frontier_images(repo, ideal)
    by_id = {op.id: op for op in ops}
    symbol_count = sum(
        1 for sym, op_id in ideal.frontier(ops).items() if not is_bottom(by_id[op_id].footprint[sym][1])
    )

    tree_result = load_tree(repo)
    # Count feature (leaf) nodes by *canonical* children, matching `map_view`: a node whose listed
    # children are all borrowed (spliced from another subsystem, their `parent` points elsewhere)
    # owns no features of its own and is itself the feature, so both views report the same count.
    _tnodes = tree_result["nodes"] if tree_result else {}
    feature_count = sum(
        1 for nid, nd in _tnodes.items() if not any(_tnodes[c]["parent"] == nid for c in nd["children"])
    )

    from sgt.core.lens import materialization_skips

    materialized = code(ideal, ops)
    drift = _drift_paths(repo, materialized)
    # `None`, not `ops`: the skips read folds the *maximal* ideal when candidates exist, whose
    # frontier reaches ops this list carries imageless -- let it load the full store itself
    # (rare: only when tracked paths would be deleted).
    skips = materialization_skips(repo, materialized, None)
    open_forks = _open_fork_records(repo)

    return {
        # Count files from the *same* `current_ideal` the symbol count comes from, not
        # `state_view`'s HEAD ideal -- on an init-only repo (no sgt commit advancing HEAD) the two
        # diverge, which read as the nonsensical "0 file(s), N symbol(s)".
        "files": len(ideal.covered_paths(ops)),
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
