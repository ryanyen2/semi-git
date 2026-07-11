"""Ideal-edit verbs (ADR S6; plan U8, R5, R14 surfacing, R20).

Each verb is an *exact* edit of the current ref's ideal -- a set operation on op-ids, not a text
merge -- exposed as a pure `plan_*` (compute the candidate op-id set + preview, no I/O) plus a
gated `apply` (materialize via the lens, persist the edit, advance the witness). The pure plan is
what `--emit` previews without touching disk; `apply` is the only writer.

    revert(X)      = I \\ ↑X                 remove X and everything that builds on it
    pin(sym, v)    = truncate sym's chain at version v (revert the op after v)
    restore(X)     = I ∪ ↓X                 re-add X and its prerequisites (revert's inverse)
    cherry-pick(X) = I ∪ ↓X, X from another ref; refuses if the union forks a chain (AE2)
    after(a, b)    = record a declared edge a ≤ b (feeds later verbs' closures)

Up/down-sets are computed with `order.upset_in`/`downset_in` -- the collision-safe, ideal-relative
forms (existential closure / fork-free ordered chains), never the universe-level `upset`/`downset`
whose `(symbol, after_version)` adjacency mis-resolves an add→modify→revert value collision (the
`revert_to_original` corpus case). Every candidate set is validated through `Ideal.from_ops`, so a
verb can never commit a downward-closure or fork-freedom violation -- a fork surfaces as a refusal,
not a broken tree.

Scope (U8): this module + `api.verb_preview_view` are the shippable surface. Re-pointing the CLI's
`revert`/`restore` (still wired to the legacy decisions-layer `Orchestrator`) onto these is U10's
characterization-gated flip, not U8's.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sgt.core import lens, order
from sgt.core.ideal import Ideal
from sgt.core.op import Op
from sgt.core.store import Store


class VerbError(Exception):
    """`apply` was called on a refused plan (invalid target, or an edit that would fork)."""


@dataclass(frozen=True)
class VerbPreview:
    """A verb's candidate edit, computed with no I/O. `ok` is False when the target didn't
    resolve or the edit would leave an invalid ideal (`forked`); `after_ids == before_ids` then."""

    ok: bool
    verb: str
    target: str
    before_ids: frozenset[str]
    after_ids: frozenset[str]
    affected_symbols: tuple[str, ...]  # symbols whose frontier tip moves
    forked: bool = False
    message: str = ""
    declared_edge: tuple[str, str] | None = None  # (a, b) for `after`; None otherwise

    @property
    def removed(self) -> frozenset[str]:
        return self.before_ids - self.after_ids

    @property
    def added(self) -> frozenset[str]:
        return self.after_ids - self.before_ids


def resolve_target(ideal: Ideal, ops: list[Op], ref: str) -> tuple[str | None, str]:
    """Resolve a user-supplied `ref` to a single op-id within `ideal`. Accepts an exact op-id, a
    unique op-id prefix, or a `file::name` symbol (→ that symbol's frontier tip op). Returns
    `(op_id, "")` on a unique hit, or `(None, message)` on missing/ambiguous."""
    ids = ideal.op_ids
    if ref in ids:
        return ref, ""
    if "::" in ref:
        tip = order.frontier(ids, ops).get(ref)
        if tip is not None:
            return tip, ""
        return None, f"symbol {ref!r} is not live in the ideal"
    matches = sorted(oid for oid in ids if oid.startswith(ref))
    if len(matches) == 1:
        return matches[0], ""
    if matches:
        return None, f"ambiguous op-id prefix {ref!r}: {matches[:5]}"
    return None, f"{ref!r} is neither an op-id in the ideal nor a live symbol"


def _load(repo: str | Path) -> tuple[list[Op], Ideal, frozenset[tuple[str, str]]]:
    """(all stored ops, current committed ideal, declared edges) -- the pure plan-time inputs."""
    repo = Path(repo)
    ops = Store(repo).all_ops()
    return ops, lens.current_ideal(repo), lens._load_declared(repo)


def _preview(
    verb: str, target: str, before_ids, after_ids, ops: list[Op], *,
    ok: bool = True, forked: bool = False, message: str = "",
    declared_edge: tuple[str, str] | None = None,
) -> VerbPreview:
    before_ids, after_ids = frozenset(before_ids), frozenset(after_ids)
    fb, fa = order.frontier(before_ids, ops), order.frontier(after_ids, ops)
    affected = tuple(sorted(s for s in set(fb) | set(fa) if fb.get(s) != fa.get(s)))
    return VerbPreview(
        ok=ok, verb=verb, target=target, before_ids=before_ids, after_ids=after_ids,
        affected_symbols=affected, forked=forked, message=message, declared_edge=declared_edge,
    )


def _validated(
    verb: str, target: str, before_ids, after_ids, ops: list[Op],
    declared: frozenset[tuple[str, str]],
) -> VerbPreview:
    """Build a preview whose `after_ids` is checked through `Ideal.from_ops` -- a fork or
    downward-closure violation becomes a refusal (`ok=False, forked=True`), never a bad commit."""
    try:
        Ideal.from_ops(after_ids, ops, declared)
    except ValueError as e:
        return _preview(verb, target, before_ids, before_ids, ops, ok=False, forked=True,
                        message=f"would leave an invalid (forked) ideal, refused: {e}")
    return _preview(verb, target, before_ids, after_ids, ops)


# -- plans (pure) ---------------------------------------------------------------------------------

def plan_revert(repo: str | Path, target: str) -> VerbPreview:
    ops, ideal, declared = _load(repo)
    op_id, err = resolve_target(ideal, ops, target)
    if err:
        return _preview("revert", target, ideal.op_ids, ideal.op_ids, ops, ok=False, message=err)
    after = ideal.op_ids - order.upset_in(op_id, ideal.op_ids, ops, declared)
    return _validated("revert", target, ideal.op_ids, after, ops, declared)


def plan_pin(repo: str | Path, symbol: str, version: str) -> VerbPreview:
    ops, ideal, declared = _load(repo)
    tag = f"{symbol}@{version[:8]}"
    seq = order._ordered_chains(ideal.op_ids, ops).get(symbol)
    if not seq:
        return _preview("pin", tag, ideal.op_ids, ideal.op_ids, ops, ok=False,
                        message=f"symbol {symbol!r} is not in the ideal")
    by_id = {op.id: op for op in ops}
    idx = next((i for i, oid in enumerate(seq) if by_id[oid].footprint[symbol][1] == version), None)
    if idx is None:
        return _preview("pin", tag, ideal.op_ids, ideal.op_ids, ops, ok=False,
                        message=f"version {version!r} not found in {symbol}'s chain")
    if idx == len(seq) - 1:
        return _preview("pin", tag, ideal.op_ids, ideal.op_ids, ops, message="already at the tip; no change")
    after = ideal.op_ids - order.upset_in(seq[idx + 1], ideal.op_ids, ops, declared)
    return _validated("pin", tag, ideal.op_ids, after, ops, declared)


def plan_restore(repo: str | Path, target: str) -> VerbPreview:
    ops, ideal, declared = _load(repo)
    source = lens.ideal_for_ref(repo, "HEAD")  # the full provenance ideal still holds reverted ops
    op_id, err = resolve_target(source, ops, target)
    if err:
        return _preview("restore", target, ideal.op_ids, ideal.op_ids, ops, ok=False, message=err)
    after = ideal.op_ids | order.downset_in(op_id, source.op_ids, ops, declared)
    return _validated("restore", target, ideal.op_ids, after, ops, declared)


def plan_cherry_pick(repo: str | Path, target: str, source_ref: str) -> VerbPreview:
    ops, ideal, declared = _load(repo)
    source = lens.ideal_for_ref(repo, source_ref)
    op_id, err = resolve_target(source, ops, target)
    if err:
        return _preview("cherry-pick", target, ideal.op_ids, ideal.op_ids, ops, ok=False,
                        message=f"in {source_ref}: {err}")
    after = ideal.op_ids | order.downset_in(op_id, source.op_ids, ops, declared)
    return _validated("cherry-pick", target, ideal.op_ids, after, ops, declared)


def plan_after(repo: str | Path, a: str, b: str) -> VerbPreview:
    ops, ideal, declared = _load(repo)
    a_id, ea = resolve_target(ideal, ops, a)
    b_id, eb = resolve_target(ideal, ops, b)
    tag = f"{a} ≤ {b}"
    if ea or eb:
        return _preview("after", tag, ideal.op_ids, ideal.op_ids, ops, ok=False, message=ea or eb)
    # `after` doesn't change the ideal's op set -- it records an ordering constraint for later
    # verbs; the preview's before == after, and the edge rides in `declared_edge`.
    return _preview("after", tag, ideal.op_ids, ideal.op_ids, ops,
                    message=f"declare {a_id[:8]} ≤ {b_id[:8]}", declared_edge=(a_id, b_id))


# -- apply (the only writer) ----------------------------------------------------------------------

def apply(repo: str | Path, preview: VerbPreview, message: str | None = None) -> str:
    """Materialize a plan and persist it. Refuses a not-ok plan. For `after`, persists the
    declared edge (no commit) and returns "". Otherwise: `get()` absorbs current reality (R9;
    raises `DirtyWorkingTreeError` if the working tree holds unabsorbed edits the target would
    clobber), the edited ideal is materialized + committed via `lens.put`, and `record_ideal`
    advances the ref's persisted ideal + witness to the post-commit truth (so the edit survives
    the next `get()` instead of being re-mined away). Returns the new commit sha."""
    if not preview.ok:
        raise VerbError(preview.message or f"{preview.verb} refused")
    repo = Path(repo)
    if preview.verb == "after":
        assert preview.declared_edge is not None
        lens.declare_after(repo, *preview.declared_edge)  # OR-Set add with a fresh tag (U21/D6)
        return ""
    edited = Ideal.from_ops(preview.after_ids, Store(repo).all_ops())
    sha = lens.put(repo, edited, message=message or f"sgt {preview.verb} {preview.target}")
    lens.record_ideal(repo, edited, sha)
    return sha


# -- thin wrappers (plan, then preview or apply) --------------------------------------------------

def revert(repo: str | Path, target: str, *, emit: bool = False, message: str | None = None) -> VerbPreview:
    preview = plan_revert(repo, target)
    if not (emit or not preview.ok):
        apply(repo, preview, message)
    return preview


def pin(repo: str | Path, symbol: str, version: str, *, emit: bool = False, message: str | None = None) -> VerbPreview:
    preview = plan_pin(repo, symbol, version)
    if not (emit or not preview.ok):
        apply(repo, preview, message)
    return preview


def restore(repo: str | Path, target: str, *, emit: bool = False, message: str | None = None) -> VerbPreview:
    preview = plan_restore(repo, target)
    if not (emit or not preview.ok):
        apply(repo, preview, message)
    return preview


def cherry_pick(repo: str | Path, target: str, source_ref: str, *, emit: bool = False, message: str | None = None) -> VerbPreview:
    preview = plan_cherry_pick(repo, target, source_ref)
    if not (emit or not preview.ok):
        apply(repo, preview, message)
    return preview


def after(repo: str | Path, a: str, b: str, *, emit: bool = False) -> VerbPreview:
    preview = plan_after(repo, a, b)
    if not (emit or not preview.ok):
        apply(repo, preview)
    return preview
