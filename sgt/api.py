"""The canonical JSON projection of the operation-ideal kernel — one schema, many clients.

Every machine-readable surface (the CLI's ``--json`` mode, the MCP server, and any future UI)
renders the *same* dicts produced here, so the views can never drift apart (R21; schema changes
are additive-only). Reads are offline (no LLM/network dependency) and pure over an *already-mined*
store -- `sgt.core.lens.get` (mine-on-contact) is the caller's job, kept out of these functions so
they stay side-effect-free. `sgt.core.*` is imported lazily inside each function so `import
sgt.api` never pulls in the kernel's tree-sitter dependency just to define these shapes.

Shapes (stable; additive changes only):

* ``oplog_view``        — the mined operation DAG: every op's id, kind, footprint, provenance, intent.
* ``state_view``        — the current ref's ideal: frontier, coverage, entity-granularity fraction,
  and the async oracle's verdict (U9).
* ``ideal_diff_view``   — the semantic diff between two refs' ideals, grouped by symbol.
* ``verb_preview_view`` — a side-effect-free preview of an ideal-edit verb (U8: revert/restore/
  pin/cherry-pick/after): op-ids added/removed, affected symbols, fork refusal, before/after bytes.
* ``rewrite_view``      — the U11 rewrite-verb review surface: pending drafts (hollow ops awaiting
  fulfillment) and the currently staged candidate's oracle verdict, if any.
"""

from __future__ import annotations


def oplog_view(repo) -> dict:
    """The mined operation DAG: every stored op with its id, derived kind, footprint (each
    symbol's before->after version), witnessing-commit provenance, and intent if any.
    Deterministic order -- ops sorted by content-address id, every nested list sorted -- so set
    iteration never leaks into the projection."""
    from sgt.core.store import Store

    ops = sorted(Store(repo).all_ops(), key=lambda op: op.id)
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
                "intent": op.intent,
            }
            for op in ops
        ],
        "count": len(ops),
    }


def state_view(repo) -> dict:
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
    granularity)."""
    from sgt.config import load_oracle_config
    from sgt.core.fold import _symbol_kind
    from sgt.core.lens import ideal_for_ref
    from sgt.core.oracle import verdict_for
    from sgt.core.op import BOTTOM
    from sgt.core.store import Store

    store = Store(repo)
    ops = store.all_ops()
    ideal = ideal_for_ref(repo, "HEAD", store)
    frontier = ideal.frontier(ops)
    by_id = {op.id: op for op in ops}

    covered = ideal.covered_paths(ops)
    entity_paths: set[str] = set()
    for sym, op_id in frontier.items():
        after = by_id[op_id].footprint[sym][1]
        if after != BOTTOM and _symbol_kind(sym) in ("entity", "nested"):
            entity_paths.add(sym.split("::", 1)[0])

    oracle_configured = load_oracle_config(repo) is not None
    return {
        "frontier": {sym: frontier[sym] for sym in sorted(frontier)},
        "covered_paths": sorted(covered),
        "entity_paths": sorted(entity_paths),
        "coverage_fraction": (len(entity_paths) / len(covered)) if covered else 1.0,
        "oracle_configured": oracle_configured,
        "oracle_verdict": verdict_for(repo, ideal) if oracle_configured else None,
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
    from sgt.core.fold import code
    from sgt.core.ideal import Ideal
    from sgt.core.store import Store

    plans = {
        "revert": lambda: verbs.plan_revert(repo, target),
        "restore": lambda: verbs.plan_restore(repo, target),
        "pin": lambda: verbs.plan_pin(repo, target, version),
        "cherry-pick": lambda: verbs.plan_cherry_pick(repo, target, source_ref),
        "after": lambda: verbs.plan_after(repo, target, other),
    }
    if verb not in plans:
        return {"error": f"unknown verb {verb!r}", "verbs": sorted(plans)}
    preview = plans[verb]()

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
