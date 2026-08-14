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
            resolves=frozenset({a_id, b_id}),
        )
        store.add_hollow(h)
        hollows.append(h)
    # Every hollow chain-extends `tip_a`, so grounding the fulfilled merge needs `tip_a` and its
    # whole downset in the candidate ideal (U6). A sync-recorded fork parks the symbol at the common
    # ancestor -- neither tip is in the current ideal -- so `stage` must *pull `tip_a`'s downset in*,
    # else the fold refuses as ungrounded. Recorded here so `stage` unions it before folding, the
    # way `revert --keep-dependents` records `removed_ids`.
    declared = lens._load_declared(repo)
    required = sorted(order.downset(a_id, ops, declared))
    draft = RewriteDraft(
        ok=True, verb="merge-op", target=f"{a_id[:12]}+{b_id[:12]}",
        hollow_ids=tuple(h.id for h in hollows), meta={"required_ids": required},
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


def revert_keep_dependents(
    repo: str | Path, target: str, intent: str | None = None,
    keep: frozenset[str] | set[str] | None = None,
) -> RewriteDraft:
    """Removes `target`'s full up-set (chain + reference + declared), keeping a caller-chosen
    *frontier* of dependents (plan U3, R4). The up-set splits on one axis into two toggleable
    buckets and one read-only one: **blast** = a *direct* reference-edge dependent whose content
    names the removed symbol, so keeping it drafts a real continuation hollow an agent/human must
    rewrite (plan U5's repair loop); **carry** = a *transitive* dependent, in the up-set only
    because it structurally required a direct dependent's *pre-removal* version -- its own bytes
    never named the removed symbol, so keeping it carries it forward mechanically (same footprint,
    same image, `requires` cleared -- `build_candidate`'s `carry_forward` step, mirroring
    `split-op`'s automatic tail -- no hollow, no agent, no LLM). This is deliberately *not* "fix
    each downstream dependent one hop at a time": the transitive tail is pure bookkeeping, resolved
    once, for free, regardless of how deep the chain runs.

    `keep` is the set of toggleable (blast/carry) dependent op-ids to preserve, as chosen from the
    `--preview` frontier (`sgt.api._frontier_rows`). `keep=None` keeps them **all** -- the original
    all-or-nothing behavior, preserved exactly. A dependent *not* in `keep` is removed with the
    up-set (a dropped blast drafts no hollow; a dropped carry is not carried). An empty `keep`
    degenerates to a plain full-up-set removal (equivalently `verbs.plan_revert`)."""
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
    direct_syms = {sym for dep_id in direct_dependents for sym in by_id[dep_id].footprint}

    # The transitive tail: symbols still alive at the pre-removal frontier whose tip op fell into
    # `full_removed` only via a *chain* through a direct dependent, never via their own reference
    # edge to `target`. `sym -> tip op-id`, so the kept-set (op-ids) can select them.
    target_syms = set(by_id[op_id].footprint)
    frontier = ideal.frontier(ops)
    carry_tips = {
        sym: tip_id for sym, tip_id in frontier.items()
        if tip_id in full_removed and sym not in target_syms and sym not in direct_syms
        and by_id[tip_id].images.get(sym) is not None
    }

    # The frontier's toggleable dependents are the blast (direct) + carry (transitive-tip) op-ids;
    # `keep=None` keeps them all (today's behavior), an explicit set keeps only the named ids.
    kept = set(direct_dependents) | set(carry_tips.values()) if keep is None else set(keep)

    hollows = []
    kept_blast = [dep_id for dep_id in direct_dependents if dep_id in kept]
    for dep_id in kept_blast:
        dep = by_id[dep_id]
        for sym, (before, _after) in dep.footprint.items():
            h = make_op(
                {sym: (before, _PENDING)}, {}, kind="rework", off_chain=True,
                intent=intent or f"rewrite {sym} to not depend on removed {removed_symbol}",
            )
            store.add_hollow(h)
            hollows.append(h)

    carry_forward = sorted(sym for sym, tip_id in carry_tips.items() if tip_id in kept)

    draft = RewriteDraft(
        ok=True, verb="revert-keep-dependents", target=op_id,
        hollow_ids=tuple(h.id for h in hollows),
        meta={"removed_ids": sorted(full_removed), "carry_forward": carry_forward},
        message=f"removes {len(full_removed)} op(s); drafted {len(hollows)} continuation "
                f"hollow(s) for {len(kept_blast)} kept direct dependent(s); carries "
                f"{len(carry_forward)} transitively affected symbol(s) forward unchanged",
    )
    return _register(repo, draft)


def _blast_dependents(op_id, sym, version, ideal, ops, by_id, declared) -> list[str]:
    """The target op's *blast* dependents: direct reference-edge dependents whose `requires` names
    `(sym, version)` -- the version the target op itself presents. A transitive dependent never
    names the target's own version, so it is untouched (no repoint, no removal). Shared by `edit_op`
    (the happy-path repoint) and `edit_repair_op` (the red-oracle rework), which classify the same
    set off the same reference edges."""
    upset = order.upset_in(op_id, ideal.op_ids, ops, declared)
    return sorted(
        b for a, b in order.reference_edges(ops)
        if a == op_id and b in upset and (sym, version) in by_id[b].requires
    )


def edit_op(repo: str | Path, target: str, intent: str | None = None) -> RewriteDraft:
    """`sgt edit <target>` (plan U4, R5/KTD5): change a symbol in place. Drafts one chain-extension
    hollow whose `before` is the target's *current* after_version -- the tip-after shape `merge_op`
    uses (`sgt/core/rewrite.py`, `before = a.footprint[sym][1]`), NOT `split_op`'s original-before
    intermediate cut -- and `after = _PENDING`. The user edits the file and `fulfill(..., from_tree=
    True)` reads the new bytes.

    Because versions are content hashes, the edit stales every dependent's `requires` edge that
    named the target's *old* version. Those are exactly the target's direct reference-edge (blast)
    dependents; they are recorded in `meta` and repointed mechanically (`_mint_repoints`, no LLM) in
    `build_candidate` once the new version is known. `removed_ids` subtracts the pre-edit dependents
    so their repointed replacements take their place. A red oracle can't be pinned to one dependent
    (KTD5 bounded-safety), so `edit_repair_op` then drafts continuation hollows for *all* of them."""
    repo = Path(repo)
    store = Store(repo)
    ops = store.all_ops()
    by_id = {op.id: op for op in ops}
    ideal = lens.current_ideal(repo)
    declared = lens._load_declared(repo)

    op_id, err = resolve_target(ideal, ops, target)
    if err:
        return _refuse("edit", target, err)
    original = by_id[op_id]
    if len(original.footprint) != 1:
        return _refuse("edit", op_id, "edit targets a single-symbol op; regroup the feature first")
    (sym, (_before, after_version)), = original.footprint.items()
    if original.images.get(sym) is None:
        return _refuse("edit", op_id, f"{sym} has no after-image to edit (a removal can't be edited)")

    hollow = make_op(
        {sym: (after_version, _PENDING)}, {}, kind="edit", off_chain=True,
        intent=intent or f"edit: change {sym} in place (chain-extends {op_id[:12]})",
    )
    store.add_hollow(hollow)

    # Blast = direct reference-edge dependents naming the target's current version; a transitive
    # dependent never names the target's own version, so it's untouched (no repoint, no removal).
    blast = _blast_dependents(op_id, sym, after_version, ideal, ops, by_id, declared)
    draft = RewriteDraft(
        ok=True, verb="edit", target=op_id, hollow_ids=(hollow.id,),
        meta={
            "edit_symbol": sym, "old_version": after_version,
            "removed_ids": blast, "repoint_deps": blast,
        },
        message=f"drafted an edit hollow for {sym}; fulfilling it repoints "
                f"{len(blast)} dependent(s) mechanically",
    )
    return _register(repo, draft)


def edit_repair_op(repo: str | Path, target: str, intent: str | None = None) -> RewriteDraft:
    """The red-oracle companion to `edit_op` (plan U4/KTD5, bounded-safety caveat). Once a staged
    edit's whole-suite oracle verdict is red, it can't be attributed to a specific dependent, so
    this drafts a continuation hollow for *every* blast (direct reference-edge) dependent of the
    target -- not "only the broken ones" -- reusing `revert_keep_dependents`'s per-dependent hollow
    shape, for `--repair` (the LLM loop) to fill and re-gate. The edit op itself is already in the
    store (the happy-path `fulfill` added it); it is carried as a `required_id`, while the stale
    pre-edit dependents are `removed`, replaced by the reworked hollows."""
    repo = Path(repo)
    store = Store(repo)
    ops = store.all_ops()
    by_id = {op.id: op for op in ops}
    ideal = lens.current_ideal(repo)
    declared = lens._load_declared(repo)

    op_id, err = resolve_target(ideal, ops, target)
    if err:
        return _refuse("edit-repair", target, err)
    original = by_id[op_id]
    (sym, (_before, old_version)), = original.footprint.items()

    # The edit op is the `kind="edit"` chain-extension the happy-path `fulfill` already added to the
    # store (its `before` is the target's own version); absent it there is nothing to repair.
    edits = [o for o in ops if o.kind == "edit" and o.footprint.get(sym, (None, None))[0] == old_version]
    if not edits:
        return _refuse("edit-repair", op_id, f"no staged edit of {sym} to repair -- run `sgt edit` + fulfill first")
    edit = edits[-1]

    blast = _blast_dependents(op_id, sym, old_version, ideal, ops, by_id, declared)
    hollows = []
    for dep_id in blast:
        for dsym, (before, _after) in by_id[dep_id].footprint.items():
            h = make_op(
                {dsym: (before, _PENDING)}, {}, kind="rework", off_chain=True,
                intent=intent or f"rework {dsym} for the edited {sym}",
            )
            store.add_hollow(h)
            hollows.append(h)
    draft = RewriteDraft(
        ok=True, verb="edit-repair", target=op_id, hollow_ids=tuple(h.id for h in hollows),
        meta={"required_ids": [edit.id], "removed_ids": blast},
        message=f"drafted {len(hollows)} continuation hollow(s) for {sym}'s blast dependents",
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

def _mint_repoints(store: Store, entries: list[dict]) -> dict[str, Op]:
    """The mechanical, LLM-free `requires`-repoint mint (R6 / U5), shared by the two paths that
    stale a dependent's edge to an advanced target: U5's revert-and-replace (`draft.meta["repoint"]`
    carries the entries, since the new version is known at draft time) and U4's `edit` (build_candidate
    computes the entries once the edit hollow is fulfilled). Each entry names a dependent op-id plus
    the `(symbol, old_version) -> (symbol, new_version)` edge to remap; the dependent's footprint and
    image are copied verbatim -- only that one edge changes, so the produced version is unchanged.
    Pure and content-addressed like the split-op tail / carry-forward mints: no hollow, no backend,
    no LLM. A dependent whose `requires` never named the old version has no edge to the target and is
    skipped (no op minted), keyed `repoint:<dep-id>`."""
    by_id = {op.id: op for op in store.all_ops()}
    minted: dict[str, Op] = {}
    for entry in entries:
        dep = by_id[entry["op_id"]]
        old_edge = (entry["symbol"], entry["old_version"])
        if old_edge not in dep.requires:
            continue
        new_requires = frozenset(dep.requires - {old_edge}) | {(entry["symbol"], entry["new_version"])}
        minted[f"repoint:{dep.id}"] = make_op(
            dict(dep.footprint), dict(dep.images), requires=new_requires, kind="repoint",
            intent=f"repoint {entry['symbol']} onto {entry['new_version'][:12]} "
                   f"(requires-only, mechanical, no LLM)",
            resolves=dep.resolves,
        )
    return minted


def build_candidate(
    repo: str | Path, draft: RewriteDraft, images: dict[str, bytes] | None = None, *,
    from_tree: bool = False,
) -> tuple[Ideal, dict[str, Op]]:
    """The pure half of `stage` (plan: semantic repair loop U3), factored out so Tier-0
    verification (`sgt.repair.verify`) can build and validate the exact same candidate a real
    `stage` would, without any side effect: no `store.add`, no hollow-file deletion, no working-
    tree write. `make_op` is a pure, content-addressed constructor, so the ops built here are
    byte-identical to what `stage` itself would build and add.

    Fulfills every hollow in `draft` (agent-supplied `images`, keyed by hollow id, or
    `from_tree=True` to read each hollow's symbol straight out of the working tree), computes the
    candidate ideal (current ideal, minus `draft.meta["removed_ids"]`, plus
    `draft.meta["required_ids"]`, plus the fulfilled ops), and validates it. Returns the fulfilled
    ops keyed by hollow id (plus, for `split-op`, a `"<hollow_id>:tail"` entry for the
    automatically-minted tail op) -- everything the caller needs to `store.add` and persist."""
    repo = Path(repo)
    store = Store(repo)
    ideal = lens.current_ideal(repo)
    # `removed_ids` subtracts (revert/split remedies); `required_ids` adds a fork tip's downset the
    # merge remedy chain-extends but the parked ideal excludes (U6) -- both before the fulfilled ops.
    candidate_ids = (set(ideal.op_ids) - set(draft.meta.get("removed_ids", ()))
                     | set(draft.meta.get("required_ids", ())))

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
            intent=hollow.intent, resolves=hollow.resolves,
        )
        fulfilled[hollow_id] = op
        candidate_ids.add(op.id)

    if draft.verb == "split-op":
        hollow_id, = draft.hollow_ids
        sym = draft.meta["symbol"]
        original = store.get(draft.meta["original_op_id"])
        intermediate = fulfilled[hollow_id]
        tail = make_op(
            {sym: (intermediate.footprint[sym][1], original.footprint[sym][1])},
            {sym: original.images[sym]}, requires=original.requires, kind="split",
            intent=f"split-op tail: continues {original.id[:12]}",
        )
        fulfilled[f"{hollow_id}:tail"] = tail
        candidate_ids.add(tail.id)

    if draft.verb == "revert-keep-dependents" and draft.meta.get("carry_forward"):
        # U7: symbols dragged into the up-set only through a chain to a direct dependent, never by
        # their own reference edge to the removed target -- their bytes don't need a rewrite, only
        # a fresh, requires-free producer for the exact (symbol, version) pair the removed tip
        # produced. Mirrors the split-op tail above: minted here, not drafted as a hollow, since
        # the content is already fully known (unchanged).
        pre_removal_ops = store.all_ops()
        by_pre_id = {op.id: op for op in pre_removal_ops}
        frontier = ideal.frontier(pre_removal_ops)
        for sym in draft.meta["carry_forward"]:
            tip = by_pre_id[frontier[sym]]
            before, after = tip.footprint[sym]
            carry = make_op(
                {sym: (before, after)}, {sym: tip.images[sym]}, kind="rework",
                intent=f"carry {sym} forward unchanged (transitively affected, no direct reference)",
            )
            fulfilled[f"carry:{sym}"] = carry
            candidate_ids.add(carry.id)

    if draft.meta.get("repoint"):
        # R6 / U5: the mechanical, LLM-free companion to carry_forward. When a revert-and-replace
        # advances the target to a new content version, its dependents' `requires` edges go stale;
        # the entries name the exact `(symbol, old_version) -> (symbol, new_version)` remap up front
        # (the new version is already known at draft time on this path).
        for key, op in _mint_repoints(store, draft.meta["repoint"]).items():
            fulfilled[key] = op
            candidate_ids.add(op.id)

    if draft.verb == "edit" and draft.meta.get("repoint_deps"):
        # U4 / KTD5: the same mechanical repoint, but the target's *new* version is unknown until
        # the edit hollow is fulfilled -- so `edit_op` records only the dependent op-ids and the old
        # version, and the remap entries are finalized here from the just-fulfilled edit op. Every
        # recorded dependent named the old version (blast, direct reference edge), so none is skipped.
        (hollow_id,) = draft.hollow_ids
        sym = draft.meta["edit_symbol"]
        new_version = fulfilled[hollow_id].footprint[sym][1]
        entries = [
            {"op_id": dep_id, "symbol": sym,
             "old_version": draft.meta["old_version"], "new_version": new_version}
            for dep_id in draft.meta["repoint_deps"]
        ]
        for key, op in _mint_repoints(store, entries).items():
            fulfilled[key] = op
            candidate_ids.add(op.id)

    ops = store.all_ops() + list(fulfilled.values())
    try:
        candidate = Ideal.from_ops(candidate_ids, ops)
    except ValueError as e:
        raise RewriteError(f"fulfilling {draft.verb} would leave an invalid ideal, refused: {e}") from e

    return candidate, fulfilled


def stage(
    repo: str | Path, draft: RewriteDraft, images: dict[str, bytes] | None = None, *,
    from_tree: bool = False,
) -> Ideal:
    """Fulfills every hollow in `draft` via `build_candidate`, `store.add()`s the resulting real
    `Op`(s), and folds + writes the validated candidate to the working tree -- **no commit**.
    Consumed hollow files are deleted (fulfilled, not pending anymore); `land` is the only later
    step that commits."""
    if not draft.ok:
        raise RewriteError(draft.message or f"{draft.verb} draft refused")
    repo = Path(repo)
    lens.get(repo)  # absorb any pre-existing dirty tree / foreign commit first (R9)
    store = Store(repo)
    candidate, fulfilled = build_candidate(repo, draft, images, from_tree=from_tree)

    # Same refusal `put()` makes, for the same reason: this writes the candidate over the working
    # tree, so a path whose on-disk bytes are uncommitted *and* differ from what the candidate
    # materializes is someone's unsaved work. `stage` writes through `_write_working_tree`
    # directly, so it skipped the guard entirely -- a pilot participant ran the
    # `sgt fulfill <draft> --from-tree` line the tool itself printed and lost their uncommitted
    # edits, with deleted code restored on top, under a `✓`.
    #
    # Checked here, BEFORE the ops are stored and the hollows unlinked, so a refusal leaves the
    # draft exactly as it was and re-runnable. (Checking after consuming the hollows makes the
    # refusal itself destructive: the draft survives but its hollows are gone, and the retry the
    # message asks for dies with "hollow not found".) `code` is given the fulfilled ops
    # explicitly rather than reading them back from the store, since they are not in it yet.
    from sgt.store.gitbind import GitBinding

    ops = store.all_ops() + list(fulfilled.values())
    materialized = code(candidate, ops)
    # Scoped the way `put()`'s own delta guard is scoped: a path this draft authors is *expected*
    # to be dirty -- `--from-tree` reads the hollow's image out of exactly those uncommitted bytes,
    # so flagging them would refuse the normal flow. Everything else the fold rewrites is not this
    # edit's business, and that is where the loss happened: the participant's uncommitted work sat
    # in files the draft never touched.
    authored = {sym.split("::", 1)[0] for op in fulfilled.values() for sym in op.footprint}
    conflicts = lens._dirty_conflicts(repo, GitBinding(repo), materialized) - authored
    if conflicts:
        raise lens.DirtyWorkingTreeError(
            f"fulfill would overwrite uncommitted changes: {sorted(conflicts)} "
            f"-- record them with `sgt save`, or commit / `git restore` those files, then re-run "
            f"(the draft is untouched; nothing has been staged)"
        )

    for op in fulfilled.values():
        store.add(op)
    for hollow_id in draft.hollow_ids:
        (store.hollow_dir / hollow_id).unlink(missing_ok=True)

    # The staged bytes (working tree) and the staged record must move together (R5): a crash
    # between them would leave a dirty tree with no record, or a record for bytes never written.
    # Ops were added above, before this section, so `Store.add`'s lock never nests here.
    with locked_section(repo):
        lens._write_working_tree(repo, materialized, ops)
        _save_staged(repo, candidate, draft.verb, draft.target)
    return candidate


def resolve_draft(repo: str | Path, draft_id: str) -> RewriteDraft | None:
    """Look up a draft registered by `merge_op`/`split_op`/`transplant`/`revert_keep_dependents`
    by its `draft_id`, reconstructing the `RewriteDraft` a separate process -- a later `sgt
    fulfill`, or `sgt.repair.loop.repair` (plan U5/U6) -- needs without holding the original
    object. `None` if no such draft is registered."""
    repo = Path(repo)
    record = _load_drafts(repo).get(draft_id)
    if record is None:
        return None
    return RewriteDraft(
        ok=True, verb=record["verb"], target=record["target"], hollow_ids=tuple(record["hollow_ids"]),
        meta=record.get("meta", {}), draft_id=draft_id,
    )


def fulfill(
    repo: str | Path, draft_id: str, *, images: dict[str, bytes] | None = None, from_tree: bool = False,
) -> Ideal:
    """The CLI-facing entry point: look up a draft by its `draft_id` (so a separate `sgt fulfill`
    process doesn't need the draft object itself), then `stage` it. Removes the draft record on
    success -- its hollows are consumed either way."""
    repo = Path(repo)
    draft = resolve_draft(repo, draft_id)
    if draft is None:
        raise RewriteError(f"no draft {draft_id!r} -- see merge-op/split-op/transplant/revert --keep-dependents")
    candidate = stage(repo, draft, images, from_tree=from_tree)
    table = _load_drafts(repo)
    del table[draft_id]
    _save_drafts(repo, table)
    return candidate


def _stale_paths(repo: Path, candidate: Ideal, ops: list[Op]) -> list[str]:
    """Paths where the working tree no longer equals the staged candidate's `code(I)` -- an edit or
    a sync landed after `fulfill` staged it. Symlink-through paths are unmanaged (`stage` never
    wrote them), so they can't go stale here. A non-empty result means landing would commit a
    mixture of the reviewed candidate and later drift, so `land` refuses on it (R9)."""
    materialized = code(candidate, ops)
    stale: list[str] = []
    for path, data in materialized.items():
        if path.startswith(".sgt/") or lens._writes_through_symlink(repo, path):
            continue
        full = repo / path
        on_disk = full.read_bytes() if full.is_file() else None
        if on_disk != data:
            stale.append(path)
    return sorted(stale)


def _close_resolved_forks(repo: Path, candidate: Ideal, ops: list[Op]) -> None:
    """Drop from committed `.sgt/forks.json` (C4) any recorded fork the landed candidate resolved.
    A sync parks a forked symbol at the *common ancestor* (neither tip in the ideal) yet keeps the
    fork open, so "one tip missing" can't be the signal. The signal that closes it is a
    reconciliation op *chained onto* a tip -- exactly what `merge-op` drafts (its hollow's
    `before_version` is a tip's own `after_version`) -- now present in the landed ideal. Written
    just before the landing commit so the closed record travels in that commit's tree."""
    records = state.load_json(repo, "forks", default=[])
    if not records:
        return
    by_id = {op.id: op for op in ops}
    live = candidate.op_ids

    def resolved(rec: dict) -> bool:
        sym = rec["symbol"]
        tip_afters = {
            by_id[t].footprint[sym][1]
            for t in rec["tips"] if t in by_id and sym in by_id[t].footprint
        }
        return any(
            sym in by_id[oid].footprint and by_id[oid].footprint[sym][0] in tip_afters
            for oid in live if oid in by_id
        )

    remaining = [r for r in records if not resolved(r)]
    if len(remaining) != len(records):
        state.save_json(repo, "forks", remaining)


def land(
    repo: str | Path, *, message: str | None = None, override: tuple[str, str, str | None] | None = None,
) -> str:
    """Commits the last-`stage`d candidate (R14's landing gate): refused unless the oracle's
    verdict for that exact ideal is "pass", or `override` (status, reason, by) is supplied and
    itself resolves to "pass". The staged bytes are already on disk and the ideal is exact, so it
    commits them *directly* (`lens.commit_materialized`) rather than re-mining the deliberately
    dirty tree through `lens.put` -- then `record_ideal` so the edit survives the next `get()`.
    Refuses a *stale* stage (a tree edited or synced since `fulfill`, U6) before gating, so a
    mixture can never be committed; abandon it with `sgt unstage`."""
    repo = Path(repo)
    record = _load_staged(repo)
    if record is None:
        raise RewriteError("nothing staged -- run `sgt fulfill` first")
    ops = Store(repo).all_ops()
    candidate = Ideal.from_ops(frozenset(record["op_ids"]), ops)

    stale = _stale_paths(repo, candidate, ops)
    if stale:
        raise RewriteError(
            f"staged candidate is stale -- {', '.join(stale)} changed since `sgt fulfill`; "
            f"re-fulfill, or `sgt unstage` to abandon it (refusing to land a mixture)"
        )

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

    _close_resolved_forks(repo, candidate, ops)  # the reconciliation closes the fork it lands (C4)
    sha = lens.commit_materialized(
        repo, candidate, message or f"sgt {record['verb']} {record['target']}"
    )
    lens.record_ideal(repo, candidate, sha)
    _clear_staged(repo)
    return sha


def unstage(repo: str | Path) -> Ideal:
    """`sgt unstage`: abandon the staged rewrite candidate (U6). Rematerializes the committed ideal
    over the deliberately-dirty staged tree and clears `staged.json`, so `switch`/`put`/any other
    materializing edit works again. Raises if nothing is staged."""
    repo = Path(repo)
    if _load_staged(repo) is None:
        raise RewriteError("nothing staged to abandon")
    store = Store(repo)
    ops = store.all_ops()
    committed = lens.current_ideal(repo)
    materialized = code(committed, ops)
    # Restore then clear under one lock (mirrors `stage`): the tree and the staged record move
    # together (R5). A crash after the restore but before the clear leaves a clean tree with a stale
    # record, which the next `unstage` idempotently re-clears -- never a dirty tree with no record.
    with locked_section(repo):
        lens._write_working_tree(repo, materialized, ops)
        _clear_staged(repo)
    return committed
