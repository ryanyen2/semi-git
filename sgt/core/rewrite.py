"""Rewrite verbs: the explicit escape hatch (ADR S6; plan U11, R14/R17).

Where the ideal algebra can't express an edit exactly -- a chain fork, a two-concern op, a
backport onto a diverged chain, a dependency someone wants gone without breaking its callers --
the verb computes the *exact* part (which chain position, which symbols, which dependents) and
drafts one hollow op per symbol needing new bytes, off-chain (R18's substrate: `Op.off_chain`,
`Store.add_hollow`). An agent or human supplies the images (`fulfill(..., from_tree=True)` reads
them straight out of the working tree, entity by entity); `stage` then validates the resulting
ideal, folds it, and writes it to the working tree *without committing* -- live and testable, but
reversible. `land` is the only writer that commits, and it refuses unless the oracle's verdict for
that exact candidate ideal is "pass" (or an attributed override supersedes it) -- R14's landing
gate, distinct from R13's async, non-blocking *materialization* gate that ordinary verbs use.

Two verbs never touch a hollow op at all: `identity_split`/`identity_join` correct the tiered
matcher itself (`sgt.core.identity`), not a chain -- they write `.sgt/identity_constraints.json`
(committed, team-shared) and a subsequent `mine()` call consults it before its hash/fuzzy tiers
run (`sgt.config.load_identity_constraints`).

**Design correction versus the original sketch (recorded here, not silently changed):** the
sketch for `merge-op` had the drafted op's `requires` name the *other* fork tip's own produced
version, on the theory that `order.py`'s existing reference-edge machinery would then place both
tips below the merge op "for free". It does not: `requires`-grounding (`order._grounded`) demands
the referenced version's producer be a member of the *same* ideal, and that producer is exactly
the other fork tip -- which still shares `(symbol, before_version)` with the first tip, so
`is_fork_free` correctly rejects the union as a genuine fork regardless of which tip is nominally
the "chain parent". Reference edges cannot express "resolved by a later merge" without weakening
fork detection itself (which U8's cherry-pick refusal, AE2, depends on). `merge_op` here instead
drafts a plain chain-extension of the *ideal's own* tip; the other tip's identity is recorded only
in the drafted op's advisory `intent`, for the agent/human authoring the merge to read both diffs
and reconcile them by hand -- exactly the "explicit rewrite" R14 calls for.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

from sgt import state
from sgt.config import IdentityConstraints, load_identity_constraints, save_identity_constraints
from sgt.core import lens, oracle, order
from sgt.core.fold import code
from sgt.core.ideal import Ideal
from sgt.core.mine import _content_version, _entity_bytes, _positional_version
from sgt.core.op import Op, make_op
from sgt.core.store import Store, locked_section
from sgt.core.verbs import resolve_target
from sgt.entities.extract import extract_file

_PENDING = "…pending…"  # hollow after_version placeholder -- never a real content hash


class RewriteError(Exception):
    """A rewrite verb's draft was refused, a hollow couldn't be fulfilled, fulfilling one would
    leave an invalid ideal, or `land` was called without a passing oracle verdict (R14)."""


@dataclass(frozen=True)
class RewriteDraft:
    """A drafted rewrite: `ok=False` means the verb refused (bad target, nothing to resolve) and
    `hollow_ids`/`draft_id` are empty. Otherwise its hollow op(s) are already written to
    `.sgt/local/hollow/`, and the draft itself is registered under `draft_id` in
    `.sgt/local/drafts.json` so a later, separate `sgt fulfill <draft_id>` process can find it.
    `meta["removed_ids"]`, if present, are op-ids `stage` subtracts from the current ideal before
    adding the fulfilled ops -- how `revert --keep-dependents` and `split-op` express "this
    fulfillment replaces something", without a hollow op of its own for the removal."""

    ok: bool
    verb: str
    target: str
    hollow_ids: tuple[str, ...] = ()
    message: str = ""
    meta: dict = field(default_factory=dict)
    draft_id: str = ""


def _refuse(verb: str, target: str, message: str) -> RewriteDraft:
    return RewriteDraft(ok=False, verb=verb, target=target, message=message)


# -- local-state persistence (mirrors lens.py's small-JSON-table convention) ----------------------

def _load_drafts(repo: Path) -> dict:
    return state.load_json(repo, "drafts", default={})


def _save_drafts(repo: Path, table: dict) -> None:
    state.save_json(repo, "drafts", table)


def _register(repo: Path, draft: RewriteDraft) -> RewriteDraft:
    """Persist a successful draft under a stable id (content hash of its hollow ids + meta) so a
    later, separate CLI invocation of `fulfill` can look it up by that id alone."""
    payload = json.dumps({"hollow_ids": sorted(draft.hollow_ids), "meta": draft.meta}, sort_keys=True)
    draft_id = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]
    draft = RewriteDraft(
        ok=draft.ok, verb=draft.verb, target=draft.target, hollow_ids=draft.hollow_ids,
        message=draft.message, meta=draft.meta, draft_id=draft_id,
    )
    table = _load_drafts(repo)
    table[draft_id] = {
        "verb": draft.verb, "target": draft.target, "hollow_ids": list(draft.hollow_ids),
        "meta": draft.meta, "message": draft.message,
    }
    _save_drafts(repo, table)
    return draft


def pending_drafts(repo: str | Path) -> dict:
    """Every registered, not-yet-fulfilled draft -- the review surface's read side."""
    return _load_drafts(Path(repo))


def _save_staged(repo: Path, ideal: Ideal, verb: str, target: str) -> None:
    state.save_json(repo, "staged", {"op_ids": sorted(ideal.op_ids), "verb": verb, "target": target})


def _load_staged(repo: Path) -> dict | None:
    return state.load_json(repo, "staged")


def _clear_staged(repo: Path) -> None:
    path = state.path(repo, "staged")
    if path.is_file():
        path.unlink()


def staged_candidate(repo: str | Path) -> dict | None:
    """The currently staged draft (op-ids + verb/target), or `None` if nothing is staged --
    the review surface's other read side."""
    return _load_staged(Path(repo))


# -- resolving targets across refs, not just the current ideal ------------------------------------

def _resolve_op(repo: Path, ops: list[Op], ref: str) -> tuple[str | None, str]:
    """Resolve `ref` against the *whole store*, not one ideal -- rewrite verbs routinely name a
    tip from another ref (e.g. `merge-op`'s second parent). `<ref>:<file::symbol>` resolves that
    ref's own frontier tip for the symbol; otherwise `ref` is an exact op-id or an unambiguous
    prefix."""
    if ":" in ref:
        branch, _, sym = ref.partition(":")
        if "::" in sym:
            tip = lens.ideal_for_ref(repo, branch).frontier(ops).get(sym)
            if tip is None:
                return None, f"symbol {sym!r} is not live on {branch!r}"
            return tip, ""
    ids = {op.id for op in ops}
    if ref in ids:
        return ref, ""
    matches = sorted(oid for oid in ids if oid.startswith(ref))
    if len(matches) == 1:
        return matches[0], ""
    if matches:
        return None, f"ambiguous op-id prefix {ref!r}: {matches[:5]}"
    return None, f"{ref!r} is not a known op id (use <ref>:<file::symbol> to resolve on another branch)"


def _entity_bytes_from_tree(repo: Path, sym: str) -> bytes:
    """The exact bytes of symbol `sym` as it stands in the working tree right now -- the
    `fulfill(..., from_tree=True)` path, so an agent/human can just edit the file and re-run
    `sgt fulfill` rather than pipe bytes through the CLI."""
    path, _, _name = sym.partition("::")
    full = repo / path
    if not full.is_file():
        raise RewriteError(f"{path!r} not found in the working tree -- author {sym} there first")
    source = full.read_bytes()
    ent = next((e for e in extract_file(path, source) if e.id == sym), None)
    if ent is None:
        raise RewriteError(f"{sym!r} not found in the working tree's current {path} -- author it there first")
    return _entity_bytes(source, ent)


# -- draft builders (compute the exact part; write hollow op(s) off-chain) -------------------------

def merge_op(repo: str | Path, tip_a: str, tip_b: str, intent: str | None = None) -> RewriteDraft:
    """Drafts one hollow per forked symbol shared by `tip_a` and `tip_b` (a chain-parent
    extension of `tip_a`, the ideal's own tip; see the module docstring for why `tip_b`'s content
    rides in `intent` rather than a structural `requires` edge). Resolves the AE2-style fork
    U8's cherry-pick refuses on."""
    repo = Path(repo)
    store = Store(repo)
    ops = store.all_ops()
    by_id = {op.id: op for op in ops}
    a_id, ea = _resolve_op(repo, ops, tip_a)
    b_id, eb = _resolve_op(repo, ops, tip_b)
    label = f"{tip_a}+{tip_b}"
    if ea or eb:
        return _refuse("merge-op", label, ea or eb)
    a, b = by_id[a_id], by_id[b_id]
    forked = sorted(
        sym for sym in set(a.footprint) & set(b.footprint)
        if a.footprint[sym][0] == b.footprint[sym][0] and a.footprint[sym][1] != b.footprint[sym][1]
    )
    if not forked:
        return _refuse("merge-op", label, "tip_a and tip_b share no forked symbol -- nothing to merge")

    hollows = []
    for sym in forked:
        before = a.footprint[sym][1]
        h = make_op(
            {sym: (before, _PENDING)}, {}, kind="merge", off_chain=True,
            intent=intent or f"merge-op: reconcile {a_id[:12]} and {b_id[:12]} on {sym}",
        )
        store.add_hollow(h)
        hollows.append(h)
    draft = RewriteDraft(
        ok=True, verb="merge-op", target=f"{a_id[:12]}+{b_id[:12]}",
        hollow_ids=tuple(h.id for h in hollows),
        message=f"drafted {len(hollows)} hollow(s) for {', '.join(forked)}",
    )
    return _register(repo, draft)


def split_op(repo: str | Path, op_id: str, intent: str | None = None) -> RewriteDraft:
    """Drafts one hollow for a new intermediate cut of a single-symbol op: `before` = the
    original op's own `before_version`, `after` = TBD (the agent's intermediate bytes). The tail
    back to the original op's own after-image is minted automatically inside `stage` once the
    intermediate is fulfilled -- no agent authorship needed for that half, since it's the
    original's own bytes verbatim."""
    repo = Path(repo)
    store = Store(repo)
    ops = store.all_ops()
    by_id = {op.id: op for op in ops}
    ideal = lens.current_ideal(repo)
    target_id, err = resolve_target(ideal, ops, op_id)
    if err:
        return _refuse("split-op", op_id, err)
    original = by_id[target_id]
    if len(original.footprint) != 1:
        return _refuse(
            "split-op", target_id,
            "split-op targets a single-symbol op; use merge-op/transplant for multi-symbol edits",
        )
    (sym, (before, _after)), = original.footprint.items()
    if original.images.get(sym) is None:
        return _refuse("split-op", target_id, f"{sym} has no after-image to split (a removal can't be split)")

    hollow = make_op(
        {sym: (before, _PENDING)}, {}, kind="split", off_chain=True,
        intent=intent or f"split-op: intermediate cut of {target_id[:12]} ({sym})",
    )
    store.add_hollow(hollow)
    draft = RewriteDraft(
        ok=True, verb="split-op", target=target_id, hollow_ids=(hollow.id,),
        meta={"original_op_id": target_id, "symbol": sym, "removed_ids": [target_id]},
        message=f"drafted an intermediate hollow for {sym}; fulfilling it mints the "
                f"{target_id[:12]}-tail automatically",
    )
    return _register(repo, draft)


def transplant(repo: str | Path, op_ids: list[str], onto_ref: str, intent: str | None = None) -> RewriteDraft:
    """Drafts one hollow per symbol touched by `op_ids`, with `onto_ref`'s own chain tip as
    `before_version` (AE3) -- no structural link back to the source ops; they're inspiration for
    the agent's rewrite, named only in `intent`."""
    repo = Path(repo)
    store = Store(repo)
    ops = store.all_ops()
    by_id = {op.id: op for op in ops}
    dest_frontier = lens.ideal_for_ref(repo, onto_ref).frontier(ops)

    resolved: list[Op] = []
    for ref in op_ids:
        oid, err = _resolve_op(repo, ops, ref)
        if err:
            return _refuse("transplant", ",".join(op_ids), f"{ref}: {err}")
        resolved.append(by_id[oid])

    symbols = sorted({sym for op in resolved for sym in op.footprint})
    if not symbols:
        return _refuse("transplant", ",".join(op_ids), "no symbols to transplant")

    label = ",".join(o.id[:12] for o in resolved)
    hollows = []
    for sym in symbols:
        dest_tip = dest_frontier.get(sym)
        before = by_id[dest_tip].footprint[sym][1] if dest_tip else None
        h = make_op(
            {sym: (before, _PENDING)}, {}, kind="transplant", off_chain=True,
            intent=intent or f"transplant of {label} onto {onto_ref}",
        )
        store.add_hollow(h)
        hollows.append(h)
    draft = RewriteDraft(
        ok=True, verb="transplant", target=f"{label} onto {onto_ref}",
        hollow_ids=tuple(h.id for h in hollows),
        message=f"drafted {len(hollows)} hollow(s) with {onto_ref}'s chain tip as before_version",
    )
    return _register(repo, draft)


def revert_keep_dependents(repo: str | Path, target: str, intent: str | None = None) -> RewriteDraft:
    """Removes `target`'s full up-set (chain + reference + declared), but drafts one continuation
    hollow per *direct* reference-edge dependent so each dependent's own symbol stays present in
    the ideal, only its content needing a rewrite that no longer depends on the removed symbol.
    (v1 scope: only one-hop reference dependents get a continuation; anything further downstream
    is dropped exactly like a plain revert -- see FINDINGS.md.)"""
    repo = Path(repo)
    store = Store(repo)
    ops = store.all_ops()
    by_id = {op.id: op for op in ops}
    ideal = lens.current_ideal(repo)
    declared = lens._load_declared(repo)

    op_id, err = resolve_target(ideal, ops, target)
    if err:
        return _refuse("revert-keep-dependents", target, err)

    full_removed = order.upset_in(op_id, ideal.op_ids, ops, declared)
    direct_dependents = sorted(
        b for a, b in order.reference_edges(ops) if a == op_id and b in full_removed
    )
    removed_symbol = next(iter(by_id[op_id].footprint))

    hollows = []
    for dep_id in direct_dependents:
        dep = by_id[dep_id]
        for sym, (before, _after) in dep.footprint.items():
            h = make_op(
                {sym: (before, _PENDING)}, {}, kind="rework", off_chain=True,
                intent=intent or f"rewrite {sym} to not depend on removed {removed_symbol}",
            )
            store.add_hollow(h)
            hollows.append(h)

    draft = RewriteDraft(
        ok=True, verb="revert-keep-dependents", target=op_id,
        hollow_ids=tuple(h.id for h in hollows),
        meta={"removed_ids": sorted(full_removed)},
        message=f"removes {len(full_removed)} op(s); drafted {len(hollows)} continuation "
                f"hollow(s) for {len(direct_dependents)} direct dependent(s)",
    )
    return _register(repo, draft)


# -- identity corrections (no hollow op; corrects the matcher, not a chain) -----------------------

def identity_split(repo: str | Path, a: str, b: str) -> dict:
    """Corrects a wrong weld: the matcher linked surface ids `a` and `b` as a rename/move when
    they're unrelated. Records a permanent `never_link` constraint; a subsequent `mine()` treats
    them as delete + add again."""
    repo = Path(repo)
    constraints = load_identity_constraints(repo)
    pair = tuple(sorted((a, b)))
    updated = IdentityConstraints(
        never_link=constraints.never_link | {pair},
        force_link=constraints.force_link - {pair},
    )
    save_identity_constraints(repo, updated)
    return {"never_link": sorted(updated.never_link), "force_link": sorted(updated.force_link)}


def identity_join(repo: str | Path, a: str, b: str) -> dict:
    """Corrects a missed weld: `a` and `b` are the same identity (a rename/move) but the
    hash/fuzzy tiers didn't find it. Records a permanent `force_link` constraint."""
    repo = Path(repo)
    constraints = load_identity_constraints(repo)
    pair = tuple(sorted((a, b)))
    updated = IdentityConstraints(
        never_link=constraints.never_link - {pair},
        force_link=constraints.force_link | {pair},
    )
    save_identity_constraints(repo, updated)
    return {"never_link": sorted(updated.never_link), "force_link": sorted(updated.force_link)}


# -- stage / fulfill / land (the only writers of real bytes and commits) --------------------------

def stage(
    repo: str | Path, draft: RewriteDraft, images: dict[str, bytes] | None = None, *,
    from_tree: bool = False,
) -> Ideal:
    """Fulfills every hollow in `draft` (agent-supplied `images`, keyed by hollow id, or
    `from_tree=True` to read each hollow's symbol straight out of the working tree), builds the
    real `Op`(s) and `store.add()`s them, computes the candidate ideal (current ideal, minus
    `draft.meta["removed_ids"]`, plus the fulfilled ops), validates it, and folds + writes it to
    the working tree -- **no commit**. Consumed hollow files are deleted (fulfilled, not pending
    anymore); `land` is the only later step that commits."""
    if not draft.ok:
        raise RewriteError(draft.message or f"{draft.verb} draft refused")
    repo = Path(repo)
    lens.get(repo)  # absorb any pre-existing dirty tree / foreign commit first (R9)
    store = Store(repo)
    ideal = lens.current_ideal(repo)
    candidate_ids = set(ideal.op_ids) - set(draft.meta.get("removed_ids", ()))

    fulfilled: dict[str, Op] = {}
    for hollow_id in draft.hollow_ids:
        hollow = store.get_hollow(hollow_id)
        if hollow is None:
            raise RewriteError(f"hollow {hollow_id[:12]} not found -- already fulfilled or never drafted")
        sym = next(iter(hollow.footprint))
        before, _pending = hollow.footprint[sym]
        image = _entity_bytes_from_tree(repo, sym) if from_tree else (images or {})[hollow_id]
        after = _positional_version(sym, _content_version(image))
        op = make_op(
            {sym: (before, after)}, {sym: image}, requires=hollow.requires, kind=hollow.kind,
            intent=hollow.intent,
        )
        store.add(op)
        fulfilled[sym] = op
        candidate_ids.add(op.id)
        (store.hollow_dir / hollow_id).unlink(missing_ok=True)

    if draft.verb == "split-op":
        sym = draft.meta["symbol"]
        original = store.get(draft.meta["original_op_id"])
        intermediate = fulfilled[sym]
        tail = make_op(
            {sym: (intermediate.footprint[sym][1], original.footprint[sym][1])},
            {sym: original.images[sym]}, requires=original.requires, kind="split",
            intent=f"split-op tail: continues {original.id[:12]}",
        )
        store.add(tail)
        candidate_ids.add(tail.id)

    ops = store.all_ops()
    try:
        candidate = Ideal.from_ops(candidate_ids, ops)
    except ValueError as e:
        raise RewriteError(f"fulfilling {draft.verb} would leave an invalid ideal, refused: {e}") from e

    # The staged bytes (working tree) and the staged record must move together (R5): a crash
    # between them would leave a dirty tree with no record, or a record for bytes never written.
    # Ops were added above, before this section, so `Store.add`'s lock never nests here.
    materialized = code(candidate, ops)
    with locked_section(repo):
        lens._write_working_tree(repo, materialized, ops)
        _save_staged(repo, candidate, draft.verb, draft.target)
    return candidate


def fulfill(
    repo: str | Path, draft_id: str, *, images: dict[str, bytes] | None = None, from_tree: bool = False,
) -> Ideal:
    """The CLI-facing entry point: look up a draft registered by `merge_op`/`split_op`/
    `transplant`/`revert_keep_dependents` by its `draft_id` (so a separate `sgt fulfill` process
    doesn't need the draft object itself), then `stage` it. Removes the draft record on success --
    its hollows are consumed either way."""
    repo = Path(repo)
    table = _load_drafts(repo)
    record = table.get(draft_id)
    if record is None:
        raise RewriteError(f"no draft {draft_id!r} -- see merge-op/split-op/transplant/revert --keep-dependents")
    draft = RewriteDraft(
        ok=True, verb=record["verb"], target=record["target"], hollow_ids=tuple(record["hollow_ids"]),
        meta=record.get("meta", {}), draft_id=draft_id,
    )
    candidate = stage(repo, draft, images, from_tree=from_tree)
    del table[draft_id]
    _save_drafts(repo, table)
    return candidate


def land(
    repo: str | Path, *, message: str | None = None, override: tuple[str, str, str | None] | None = None,
) -> str:
    """Commits the last-`stage`d candidate (R14's landing gate): refused unless the oracle's
    verdict for that exact ideal is "pass", or `override` (status, reason, by) is supplied and
    itself resolves to "pass". On success, mirrors U8's `apply()` tail (`lens.put` then
    `lens.record_ideal`) so the edit survives the next `get()` instead of being re-mined away."""
    repo = Path(repo)
    record = _load_staged(repo)
    if record is None:
        raise RewriteError("nothing staged -- run `sgt fulfill` first")
    ops = Store(repo).all_ops()
    candidate = Ideal.from_ops(frozenset(record["op_ids"]), ops)

    status = oracle.overall_status(oracle.verdict_for(repo, candidate))
    if status != "pass":
        if override is None:
            raise RewriteError(
                f"cannot land: oracle verdict is {status!r} -- run `sgt oracle run`, or land with an override"
            )
        status_, reason, by = override
        rec = oracle.override(repo, status_, reason, by, ideal=candidate)
        status = rec["override"]["status"]
        if status != "pass":
            raise RewriteError(f"override recorded as {status!r}, still refusing to land")

    sha = lens.put(repo, candidate, message=message or f"sgt {record['verb']} {record['target']}")
    lens.record_ideal(repo, candidate, sha)
    _clear_staged(repo)
    return sha
