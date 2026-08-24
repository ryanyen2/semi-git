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

from dataclasses import dataclass, replace
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
    new_ops: tuple[Op, ...] = ()  # forward-subtraction ops (splices/prunes) `apply` must store
    subtracted_symbols: tuple[str, ...] = ()  # shared symbols spliced at their tip
    pruned_symbols: tuple[str, ...] = ()  # target-introduced symbols bottomed at their tip
    kept_conflicts: tuple[str, ...] = ()  # symbols left unchanged that need a manual edit
    broken_references: tuple[str, ...] = ()  # surviving symbols still naming removed code
    target_ops: frozenset[str] = frozenset()  # the ops the user actually named. Distinct from
    # `removed`, which is empty whenever the edit rewrites symbols in place instead of dropping
    # ops -- the ordinary case for reverting one checkpoint of a symbol later work has touched.
    # Without this the preview could not tell which chapter had been asked for and marked it
    # `kept`, the one word it must never say about the thing being reverted.

    @property
    def removed(self) -> frozenset[str]:
        return self.before_ids - self.after_ids

    @property
    def added(self) -> frozenset[str]:
        return self.after_ids - self.before_ids


def resolve_target(ideal: Ideal, ops: list[Op], ref: str) -> tuple[str | None, str]:
    """Resolve a user-supplied `ref` to a single op-id within `ideal`. Accepts an exact op-id, a
    unique op-id prefix, a `file::name` symbol, or a whole-file path (each → that symbol's frontier
    tip op). Returns `(op_id, "")` on a unique hit, or `(None, message)` on missing/ambiguous."""
    ids = ideal.op_ids
    if ref in ids:
        return ref, ""
    if "::" in ref:
        tip = order.frontier(ids, ops).get(ref)
        if tip is not None:
            return tip, ""
        return None, f"symbol {ref!r} is not live in the ideal"
    # A non-code file (`README.md`, `config.yaml`, a binary) is tracked as one whole-file symbol, so
    # its frontier key is the bare path with no `::`. Checked before the hex-prefix rung -- an exact
    # frontier hit is more specific than a prefix, and `sgt revert README.md` is a command a user
    # types straight off a `log`/`blame` line. Without this it reached neither symbol rung and fell
    # through to the NL/LLM resolver, which is both slow and needless for an exact name.
    tip = order.frontier(ids, ops).get(ref)
    if tip is not None:
        return tip, ""
    matches = sorted(oid for oid in ids if oid.startswith(ref))
    if len(matches) == 1:
        return matches[0], ""
    if matches:
        return None, f"ambiguous op-id prefix {ref!r}: {matches[:5]}"
    return None, f"{ref!r} is neither an op-id in the ideal nor a live symbol"


def _load(repo: str | Path) -> tuple[list[Op], Ideal, frozenset[tuple[str, str]]]:
    """(all stored ops, current committed ideal, declared edges) -- the pure plan-time inputs.
    Footprint-only (`opindex.index_ops`, never `Store.all_ops`'s images decode): every consumer
    in this module is a plan/preview built from footprints, frontiers, and order math alone --
    nothing here materializes bytes."""
    from sgt.core import opindex

    repo = Path(repo)
    return opindex.index_ops(repo), lens.current_ideal(repo), lens._load_declared(repo)


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


def _invalid_ideal_reason(after_ids, ops: list[Op], declared) -> str:
    """Why `Ideal.from_ops` refused, in the reader's terms. F39's second collateral defect: that
    exception carries `sorted(ids)` -- the whole *proposed* set, thousands of 64-hex ids on a real
    repository and never the offending one -- and the refusal used to print it verbatim. Name the
    symbol whose chain forked, or the edit that lost its prerequisites."""
    ids = frozenset(after_ids)
    forked = order.forks(ops, ids)
    if forked:
        sym, a, b = forked[0]
        more = f" (+{len(forked) - 1} more symbol(s))" if len(forked) > 1 else ""
        return (f"would leave two live versions of {sym}: {a[:8]} and {b[:8]} both claim the same "
                f"next version, refused{more}")
    ungrounded = sorted(ids - order._grounded(ids, ops, declared))
    if ungrounded:
        more = f" (+{len(ungrounded) - 1} more)" if len(ungrounded) > 1 else ""
        by_id = {op.id: op for op in ops}
        syms = sorted(by_id[ungrounded[0]].footprint) if ungrounded[0] in by_id else []
        where = f" ({syms[0]})" if syms else ""
        return (f"would include {ungrounded[0][:8]}{where} without the edit(s) it was built on, "
                f"refused{more}")
    return "would leave an invalid ideal, refused"


def _validated(
    verb: str, target: str, before_ids, after_ids, ops: list[Op],
    declared: frozenset[tuple[str, str]],
) -> VerbPreview:
    """Build a preview whose `after_ids` is checked through `Ideal.from_ops` -- a fork or
    downward-closure violation becomes a refusal (`ok=False, forked=True`), never a bad commit."""
    try:
        Ideal.from_ops(after_ids, ops, declared)
    except ValueError:
        return _preview(verb, target, before_ids, before_ids, ops, ok=False, forked=True,
                        message=_invalid_ideal_reason(after_ids, ops, declared))
    return _preview(verb, target, before_ids, after_ids, ops)


def _with_layout_siblings(added, ops: list[Op], before_ids, source_ids,
                          declared: frozenset[tuple[str, str]]):
    """`added` plus the layout ops (gap/anchor) of every entity it brings back. Layout facts ride
    with the entity in both directions: `plan_subtraction` removes an entity's trailing gap and
    anchor along with it (F35), so restoring the entity has to bring them back, or the fold has no
    separator to place and composes `    return 2def revived():`. They are siblings, so the downset
    does not reach them -- pull them in with their own prerequisites.

    One chain per layout symbol, and only for symbols the result does not already ground. F39:
    `_repair_layout` mints an anchor/residue repair with `before=None` whenever a removal leaves
    that symbol no live tip, so a symbol removed and reborn owns several chain *heads* in the
    store -- legal there (the store is a forest of versions), fatal inside an ideal. Taking every
    sibling match therefore forked the chain and `_validated` refused a restore whose bytes were
    sitting in the store the whole time: the WP-V4 sweep hit that as a file left at 1 byte with no
    documented command able to bring it back. The deepest candidate is the tip of one chain and its
    downset is that chain, never a second head."""
    from sgt.core.subtract import layout_ops_of

    by_id = {op.id: op for op in ops}
    grounded = order.frontier(before_ids | added, ops)
    reach = {oid: order.downset_in(oid, source_ids, ops, declared)
             for oid in layout_ops_of(added, by_id, source_ids)}
    pick: dict[str, str] = {}
    for oid in sorted(reach, key=lambda o: (-len(reach[o]), o)):
        for sym in by_id[oid].footprint:
            if sym not in grounded:
                pick.setdefault(sym, oid)
    for oid in dict.fromkeys(pick.values()):
        added = added | reach[oid]
    return added


# -- plans (pure) ---------------------------------------------------------------------------------

def _plan_removal(
    repo: str | Path, verb: str, tag: str, target_ids, ops, ideal, declared, *,
    take_dependents: bool,
) -> VerbPreview:
    """The one removal planner behind every revert shape. Default: semantic removal plus forward
    subtraction (`sgt.core.subtract`) -- later work layered above the target inside shared
    symbols survives, mechanically spliced where clean, reported where not. `take_dependents`
    is the old blanket `ideal \\ ↑X`: X and everything that loses grounding, demolition
    included -- explicit, never the default (the 2026-08-09 study-testbed demolition)."""
    if take_dependents:
        after = ideal.op_ids - order.upset_in_many(target_ids, ideal.op_ids, ops, declared)
        out = _validated(verb, tag, ideal.op_ids, after, ops, declared)
        return replace(out, target_ops=frozenset(target_ids))

    from sgt.core.subtract import plan_subtraction

    plan = plan_subtraction(repo, target_ids, ops, ideal.op_ids, declared, tag=tag)
    if not plan.ok:
        return _preview(verb, tag, ideal.op_ids, ideal.op_ids, ops, ok=False, message=plan.message)
    all_ops = ops + list(plan.new_ops)
    try:
        Ideal.from_ops(plan.after_ids, all_ops, declared)
    except ValueError:
        return _preview(verb, tag, ideal.op_ids, ideal.op_ids, ops, ok=False, forked=True,
                        message=_invalid_ideal_reason(plan.after_ids, all_ops, declared))
    base = _preview(verb, tag, ideal.op_ids, plan.after_ids, all_ops, message=plan.message)
    return VerbPreview(
        ok=True, verb=verb, target=tag, before_ids=base.before_ids, after_ids=base.after_ids,
        affected_symbols=base.affected_symbols, message=plan.message,
        new_ops=plan.new_ops, subtracted_symbols=plan.subtracted_symbols,
        pruned_symbols=plan.pruned_symbols, kept_conflicts=plan.kept_conflicts,
        broken_references=plan.broken_references, target_ops=frozenset(target_ids),
    )


def plan_revert(repo: str | Path, target: str, *, take_dependents: bool = False) -> VerbPreview:
    ops, ideal, declared = _load(repo)
    op_id, err = resolve_target(ideal, ops, target)
    if err:
        return _preview("revert", target, ideal.op_ids, ideal.op_ids, ops, ok=False, message=err)
    return _plan_removal(repo, "revert", target, {op_id}, ops, ideal, declared,
                         take_dependents=take_dependents)


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
    source_ids = source.op_ids
    if err:
        # The reduced source parks a superseded/forked version (both competing tips are dropped by
        # `reduce_to_ideal`), so the ghost op a `revert` printed -- and a symbol whose whole chain
        # forked or is ungrounded there -- can never resolve against it. Fall back to the whole
        # store: the downset is the ghost's own chain, and `_validated` still refuses any re-entry
        # that would fork the ideal, so this widens *resolution*, never legality.
        ids = {op.id for op in ops}
        if "::" in target:
            # A non-live symbol still has its ghost ops in the store even when it has no live
            # frontier tip in the reduced source. Resolve it to the newest ghost tip (same
            # out-of-ideal ghost set `cli/ideal_edit._live_and_ghosts` lists, newest last) and
            # restore that version over the whole store -- README's `restore <file::symbol>`.
            ghosts = sorted(op.id for op in ops if target in op.footprint and op.id not in ideal.op_ids)
            if ghosts:
                op_id, err, source_ids = ghosts[-1], "", frozenset(ids)
            else:
                # `resolve_target`'s reason is written for `revert` ("not live in the ideal"), which
                # is the *premise* of a restore, not an objection to it (F94). Nothing live and no
                # ghost means the store never recorded this symbol at all -- say that.
                err = f"no recorded version of {target!r} — nothing in this history to restore"
        else:
            matches = sorted(oid for oid in ids if oid.startswith(target))
            if len(matches) == 1:
                op_id, err, source_ids = matches[0], "", frozenset(ids)
    if err:
        return _preview("restore", target, ideal.op_ids, ideal.op_ids, ops, ok=False, message=err)
    added = _with_layout_siblings(order.downset_in(op_id, source_ids, ops, declared),
                                  ops, ideal.op_ids, source_ids, declared)
    return _validated("restore", target, ideal.op_ids, ideal.op_ids | added, ops, declared)


def plan_cherry_pick(repo: str | Path, target: str, source_ref: str) -> VerbPreview:
    ops, ideal, declared = _load(repo)
    source = lens.ideal_for_ref(repo, source_ref)
    op_id, err = resolve_target(source, ops, target)
    if err:
        return _preview("cherry-pick", target, ideal.op_ids, ideal.op_ids, ops, ok=False,
                        message=f"in {source_ref}: {err}")
    after = ideal.op_ids | order.downset_in(op_id, source.op_ids, ops, declared)
    return _validated("cherry-pick", target, ideal.op_ids, after, ops, declared)


def plan_revert_session(repo: str | Path, name: str, *, take_dependents: bool = False) -> VerbPreview:
    """Resolve a session name (plan U31, S7: addressing by provenance) to the op-set it landed --
    `sgt.core.session.ops_by_session`, which reads structured attribution and so still resolves
    long after the session record itself is gone -- then the exact ideal edit
    `I \\ upset_in_many(X)`: one grounding pass for the whole set, not one per op (which cost
    O(|X|·|ops|) and could also under-remove an op OR-supported by two removed targets, leaving
    an invalid after-set for `_validated` to refuse instead of this valid maximal edit)."""
    from sgt.core import session as session_mod

    ops, ideal, declared = _load(repo)
    all_session_ops = session_mod.ops_by_session(repo, name)
    if not all_session_ops:
        return _preview("revert", name, ideal.op_ids, ideal.op_ids, ops, ok=False,
                        message=f"no op carries session {name!r} attribution")
    op_ids = all_session_ops & ideal.op_ids
    if not op_ids:
        return _preview("revert", name, ideal.op_ids, ideal.op_ids, ops,
                        message=f"session {name!r}'s ops are not in the current ideal; no change")

    return _plan_removal(repo, "revert", name, op_ids, ops, ideal, declared,
                         take_dependents=take_dependents)


def plan_revert_op_set(repo: str | Path, tag: str, op_ids: frozenset[str], *,
                       take_dependents: bool = False) -> VerbPreview:
    """Revert an already-resolved op-set X as the exact ideal edit `I \\ upset_in_many(X)` --
    the fully-generic form `plan_revert_session` and `lens.verbs.plan_revert_feature`
    each specialize with their own resolution step (session attribution / feature `op_leaf`).
    `sgt.intent.group.resolve_group` is a third resolution step (a theme or commit-sha's atom
    union, plan U8): the LLM only ever decides *which* op-set this is; the actual removal is this
    same collision-safe up-set union and `Ideal.from_ops` fork validation every other revert path
    uses, so a wrong theme boundary is a mis-default the caller sees and can subset around, never
    a silent destructive edit. `tag` is a human-facing label for the preview only."""
    ops, ideal, declared = _load(repo)
    op_ids = frozenset(op_ids) & ideal.op_ids
    if not op_ids:
        return _preview("revert", tag, ideal.op_ids, ideal.op_ids, ops,
                        message=f"{tag}: none of its ops are in the current ideal; no change")

    return _plan_removal(repo, "revert", tag, op_ids, ops, ideal, declared,
                         take_dependents=take_dependents)


def _revert_scaffolding_over(requested: frozenset[str], ideal, ops: list[Op]) -> frozenset[str]:
    """Live ops a previous revert authored that sit on the symbols `requested` touches.

    Kept deliberately narrow. Only ops carrying the intent a revert stamps on the ops it
    synthesizes count, and only where they overlap the symbols being restored, so an unrelated
    revert's scaffolding elsewhere in the repository is never disturbed and no op a person wrote is
    ever a candidate. Anything this drops that something else still needs comes back as an
    ungrounded refusal from `_validated`, which is the same answer the caller got before."""
    by_id = {op.id: op for op in ops}
    symbols = {sym for oid in requested if oid in by_id for sym in by_id[oid].footprint}
    if not symbols:
        return frozenset()
    return frozenset(
        oid for oid in ideal.op_ids - requested
        if oid in by_id
        and (by_id[oid].intent or "").startswith("revert ")
        and symbols & set(by_id[oid].footprint)
    )


def plan_restore_op_set(repo: str | Path, tag: str, op_ids: frozenset[str]) -> VerbPreview:
    """`plan_revert_op_set`'s inverse: re-admit an already-resolved op-set X as the exact ideal edit
    `I ∪ downset_in_many(X)` against the full provenance ideal (`HEAD`, which still holds reverted
    ops -- that asymmetry between store and ideal is what makes any restore possible). This exists
    because `<feature>@<n>` is the rewind unit the map and the checkpoint detail both tell users to
    type, and a rewind whose inverse cannot be addressed the same way is a one-way door: before this,
    `sgt restore <feature>@<n>` fell through every deterministic rung to the natural-language one and
    exited `could not resolve ... set OPENAI_API_KEY`.

    Ops already in the ideal are dropped from X first, so a live checkpoint reports `no change`
    rather than an apply it did not make. `tag` is a human-facing label for the preview only."""
    ops, ideal, declared = _load(repo)
    source = lens.ideal_for_ref(repo, "HEAD")
    requested = frozenset(op_ids)
    op_ids = requested - ideal.op_ids
    if not op_ids:
        # Every requested op is still live, and its effect can still be gone. A checkpoint revert
        # removes nothing: it layers a rework op over the symbols the checkpoint touched, carrying
        # the bytes with that checkpoint's contribution subtracted out ("2 symbol(s) changed, no
        # whole edit removed"). So the inverse is not re-admitting anything, it is peeling that
        # stand-in back off, and reporting "already in the current ideal; no change" against a page
        # that plainly still shows the revert was the most confusing answer either verb gave.
        masking = _revert_scaffolding_over(requested, ideal, ops)
        if masking:
            return _validated("restore", tag, ideal.op_ids, ideal.op_ids - masking, ops, declared)
        return _preview("restore", tag, ideal.op_ids, ideal.op_ids, ops,
                        message=f"{tag}: already in the current ideal; no change")

    added = _with_layout_siblings(order.downset_in_many(op_ids, source.op_ids, ops, declared),
                                  ops, ideal.op_ids, source.op_ids, declared)
    candidate = ideal.op_ids | added

    # A revert does not only remove: it synthesizes stand-in ops to hold the layout the removal
    # would otherwise have torn out (`sgt.core.subtract`). Re-admitting the originals then puts two
    # ops on the same symbol claiming the same next version, and `restore` refused the exact rewind
    # `revert` had just performed -- a one-way door in the pair of verbs that are supposed to be
    # each other's inverse. The stand-in has no purpose once the thing it stood in for is back.
    #
    # Only ops a revert authored are ever dropped, identified by the intent it stamps on them.
    # Intent is advisory metadata everywhere else, and leaning on it here is a smell, but the
    # alternative rule -- "drop whichever side of the fork is not being restored" -- would happily
    # discard a teammate's later edit that legitimately competes. Narrow and conservative beats
    # general and destructive: an op with no such mark is never touched, and a fork that survives
    # this still refuses below exactly as it did before.
    by_id = {op.id: op for op in ops}

    def _is_revert_scaffolding(op_id: str) -> bool:
        op = by_id.get(op_id)
        return op is not None and (op.intent or "").startswith("revert ")

    for _ in range(len(candidate)):
        forked = order.forks(ops, candidate)
        drop = {oid for _sym, a, b in forked for oid in (a, b)
                if oid not in added and _is_revert_scaffolding(oid)}
        if not drop:
            break
        candidate -= drop

    return _validated("restore", tag, ideal.op_ids, candidate, ops, declared)


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
    if preview.new_ops:
        # Forward-subtraction ops (safe revert's splices/prunes) exist only in the preview until
        # here; store them first so `Ideal.from_ops` below sees their producers.
        store = Store(repo)
        for op in preview.new_ops:
            store.add(op)
    if preview.after_ids == preview.before_ids:
        # An ok no-op edit (e.g. `revert <lane> --to <last-commit>`, `pin` already at the tip,
        # `restore` of an already-present op): nothing changed, so there is nothing to materialize.
        # Committing an unchanged tree would fail (`git commit` with no diff), so return the current
        # HEAD unchanged rather than a spurious empty commit.
        from sgt.store.gitbind import GitBinding

        return GitBinding(repo).head() or ""
    edited = Ideal.from_ops(preview.after_ids, Store(repo).all_ops())
    # An ideal edit is sgt's own mechanics: history is append-only, so removing an edit is itself a
    # forward commit. Mark it (`bookkeeping=True`) so "what did I do today" surfaces fold it instead
    # of reporting `sgt revert f-08ccdb12...` back to the developer as an accomplishment. A caller
    # that supplies its own `message` is naming real work, so it is left unmarked.
    sha = lens.put(repo, edited, message=message or f"sgt {preview.verb} {preview.target}",
                   bookkeeping=message is None)
    lens.record_ideal(repo, edited, sha)
    return sha


# -- thin wrappers (plan, then preview or apply) --------------------------------------------------

def revert(repo: str | Path, target: str, *, emit: bool = False, message: str | None = None,
           take_dependents: bool = False) -> VerbPreview:
    preview = plan_revert(repo, target, take_dependents=take_dependents)
    if preview.ok and not emit:
        apply(repo, preview, message)
    return preview


def pin(repo: str | Path, symbol: str, version: str, *, emit: bool = False, message: str | None = None) -> VerbPreview:
    preview = plan_pin(repo, symbol, version)
    if preview.ok and not emit:
        apply(repo, preview, message)
    return preview


def restore(repo: str | Path, target: str, *, emit: bool = False, message: str | None = None) -> VerbPreview:
    preview = plan_restore(repo, target)
    if preview.ok and not emit:
        apply(repo, preview, message)
    return preview


def cherry_pick(repo: str | Path, target: str, source_ref: str, *, emit: bool = False, message: str | None = None) -> VerbPreview:
    preview = plan_cherry_pick(repo, target, source_ref)
    if preview.ok and not emit:
        apply(repo, preview, message)
    return preview


def after(repo: str | Path, a: str, b: str, *, emit: bool = False) -> VerbPreview:
    preview = plan_after(repo, a, b)
    if preview.ok and not emit:
        apply(repo, preview)
    return preview
