"""Safe revert planning: semantic removal + forward subtraction (2026-08-09).

The old default, `ideal \\ upset_in(X)`, removes X and *everything that loses grounding* -- on a
history whose features interleave inside shared symbols (one `build_parser` reworked by every
episode), that is a demolition: every later feature's op chains above X through the shared
symbol and gets swept, silently, with a preview that counts ops rather than consequences.

The safe plan partitions instead:

- **Semantic removal R**: X, plus later edits of any symbol X *introduced*, plus declared-edge
  dependents, to a fixpoint -- all within `upset_in_many(X)`, the maximal blast radius. A mere
  reference dependent (code that calls something removed) is deliberately NOT removed: when it
  is a rework of a shared symbol, the tip splice subtracts the stale call lines mechanically;
  when it is its own surviving symbol, it is reported as a broken reference for a human edit --
  removed code must never take its callers down with it silently.
- **Exclusion set**: the largest part of R that is upward-closed in the live ideal
  (`upset_in(op) ⊆ R`), removed from the ideal exactly as before -- tip reverts and
  whole-feature removals keep their existing shape and their exclusion durability.
- **Forward subtraction**: every other op in R stays in history; its per-symbol contribution is
  subtracted at the live tip instead -- a prune op for a symbol the target introduced, a
  `merge3` inverse-patch splice for a shared symbol. Later work survives by construction:
  forward ops can never orphan a chain.
- **Kept conflicts**: a symbol where the subtraction overlaps later work is left byte-identical
  and *reported* ("needs your edit"), never guessed at and never used as a reason to demolish.

Everything here is pure planning: minted ops are returned, not stored; `verbs.apply` stores
them. Anchors ride the same path (their images are one-line markers, so a displaced marker
merges or reports as kept); residues are ordinary text and merge like any other symbol.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from sgt.core import order
from sgt.core.mine import _ANCHOR_FIRST, _content_version, _positional_version
from sgt.core.op import BOTTOM, Op, _symbol_kind, is_behavioral, is_bottom, make_op
from sgt.core.patch import merge3
from sgt.core.store import Store


@dataclass(frozen=True)
class SubtractionPlan:
    ok: bool
    after_ids: frozenset[str]
    new_ops: tuple[Op, ...] = ()
    excluded: frozenset[str] = frozenset()  # ops removed from the ideal (upward-closed part)
    subtracted_symbols: tuple[str, ...] = ()  # spliced at tip
    pruned_symbols: tuple[str, ...] = ()  # introduced-by-target, bottomed at tip
    kept_conflicts: tuple[str, ...] = ()  # left unchanged, need a manual edit
    broken_references: tuple[str, ...] = ()  # surviving symbols still naming removed code
    message: str = ""


def _born_symbols(op_ids, by_id) -> set[str]:
    out: set[str] = set()
    for oid in op_ids:
        for sym, (before, _after) in by_id[oid].footprint.items():
            if before is None:
                out.add(sym)
    return out


def _semantic_closure(target_ids, blast, by_id, declared_dependents_of) -> set[str]:
    removal = set(target_ids)
    changed = True
    while changed:
        changed = False
        born = _born_symbols(removal, by_id)
        declared = set().union(*(declared_dependents_of.get(rid, set()) for rid in removal)) \
            if removal else set()
        for oid in blast - removal:
            op = by_id[oid]
            if any(sym in born for sym in op.footprint) or oid in declared:
                removal.add(oid)
                changed = True
    return removal


def _broken_references(
    ops, survivors, removal, born, by_id, images_of, post_frontier, new_ops,
) -> tuple[str, ...]:
    """Surviving symbols whose bytes still name a removed entity: never swept, always reported.

    Two complementary sweeps. (1) `requires`-level, any file: ops whose recorded references name a
    removed symbol (the def-use dependents left in place -- e.g. a promotion module calling a removed
    queue). (2) Byte-level over the files the removal touched: a reference the extractor missed (a
    callback handed to `set_defaults`, a name inside a string) still NameErrors the moment the pruned
    symbol is gone. Both scan only the removal's own files and dependents, against post-splice images.

    Called from both of `plan_subtraction`'s returns. It used to sit only on the splicing path, which
    made the warning unreachable in the one shape whose `born` set is reliably non-empty: a removal
    that takes an entity outright needs no splice, so `forward` is empty and the early return fired
    before the sweep. Reverting a function while a string literal in the same file still named it
    said nothing, and the same removal *did* warn as soon as any unrelated symbol needed a splice
    (F123 -- the reason `still references removed code` never fired in the WP-V4 sweep).
    """
    removed_names = {sym.rsplit("::", 1)[1].encode() for sym in born
                     if "::" in sym and "::__" not in sym}
    if not removed_names:
        return ()
    spliced_by_sym = {next(iter(op.footprint)): op for op in new_ops}
    broken: set[str] = set()

    def _flag_if_naming_removed(sym: str) -> None:
        if "::__" in sym or sym in born or sym in broken:
            return
        tip_id = post_frontier.get(sym)
        if tip_id is None:
            return
        spliced = spliced_by_sym.get(sym)
        image = spliced.images[sym] if spliced is not None else images_of(tip_id).get(sym)
        if image and any(name in image for name in removed_names):
            broken.add(sym)

    for op in ops:
        if op.id in survivors and any(req_sym in born for (req_sym, _v) in op.requires):
            for sym in op.footprint:
                _flag_if_naming_removed(sym)
    touched_files = {sym.split("::", 1)[0] for oid in removal
                     for sym in by_id[oid].footprint}
    for sym in post_frontier:
        if sym.split("::", 1)[0] in touched_files:
            _flag_if_naming_removed(sym)
    return tuple(sorted(broken))


def layout_ops_of(op_ids, by_id, pool) -> set[str]:
    """The ops in `pool` carrying the residue/anchor facts of the entities *born* in `op_ids`.

    An entity's trailing gap and its anchor fact are siblings of the entity, not dependents and not
    prerequisites, so neither `upset_in` nor `downset_in` reaches them. Both directions of the ideal
    edit have to move them by hand or the fold's byte partition stops matching the file; see the two
    call sites (`plan_subtraction` here, `verbs.plan_restore`) for what each one costs.
    """
    wanted: set[str] = set()
    for sym in _born_symbols(op_ids, by_id):
        path, sep, name = sym.partition("::")
        if not sep or not name or "::__" in sym or "." in name:
            continue  # only top-level entities own residue/anchor facts
        wanted.add(f"{path}::__residue__::{name}")
        wanted.add(f"{path}::__anchor__::{name}")
    if not wanted:
        return set()
    return {oid for oid in pool if wanted & set(by_id[oid].footprint)}


def _repair_layout(
    path: str, pre_frontier, live_after: dict[str, str], images_of, by_id, tag: str,
    targeted: frozenset[str] = frozenset(),
) -> list[Op]:
    """Re-ground the layout facts of entities the removal *keeps*.

    The fold reconstructs a file as a verbatim byte partition -- each live top-level entity's
    image followed by that entity's own residue gap, ordered by anchor facts -- and synthesizes
    zero bytes of its own (`sgt.core.fold`). So a live entity whose residue or anchor died with
    the removal has no separator and no place: the fold concatenates it straight onto its
    neighbour (`    passdef find_section(...)`, a SyntaxError) and, with no anchor, drops it into
    the sorted end-of-file fallback.

    That happens whenever the target owns the save that first recorded the file's partition: those
    layout chains are upward-closed inside the removal and get excluded, while the entities
    themselves survive on later ops. `sym in born` above deliberately bottoms the artifacts of
    entities being *removed*; this is its counterpart for the ones being kept. Anchors are also
    re-pointed when they name an entity that is no longer live, since a dead predecessor sends the
    fold down the same fallback.

    Returns rebirth/rework ops carrying the *recorded* images -- no bytes are invented here
    either; only the anchor's predecessor marker (pure metadata, never file content) is rewritten.
    """
    def _entities(frontier) -> set[str]:
        """Every symbol in `path` that occupies a slot in the document order -- top-level
        entities and imports alike.

        This asks `_symbol_kind` rather than testing the id by hand. The hand-rolled form
        (`"::__" in sym or "." in name`) excluded imports twice over: once for the `__import__::`
        marker and again because a module specifier is full of dots. Every import then fell out
        of `kept`, the repair loop skipped it, and the first surviving *function* was re-anchored
        as FIRST -- so reverting anything moved the whole import block below the code. It still
        compiled, because ES imports hoist, which is exactly what made it hard to see."""
        out = set()
        for sym in frontier:
            head, sep, name = sym.partition("::")
            if head != path or not sep or not name:
                continue
            if _symbol_kind(sym) not in ("entity", "import"):
                continue
            op = by_id[frontier[sym]]
            if not is_bottom(op.footprint[sym][1]):
                out.add(name)
        return out

    kept = _entities(live_after)
    if not kept:
        return []

    # Pre-removal document order, from the anchor chain the fold itself walks.
    successor: dict[str | None, str] = {}
    for sym, op_id in pre_frontier.items():
        if not sym.startswith(f"{path}::__anchor__::"):
            continue
        name = sym.split("::__anchor__::", 1)[1]
        marker = (images_of(op_id).get(sym) or b"").decode("utf-8", "replace")
        successor[None if marker == _ANCHOR_FIRST else marker] = name
    order_pre: list[str] = []
    seen: set[str] = set()
    cur = successor.get(None)
    while cur is not None and cur not in seen:
        seen.add(cur)
        order_pre.append(cur)
        cur = successor.get(cur)
    for name in sorted(_entities(pre_frontier) - seen):
        order_pre.append(name)

    repairs: list[Op] = []

    def _emit(sym: str, image: bytes) -> None:
        tip = live_after.get(sym)
        before = by_id[tip].footprint[sym][1] if tip is not None else None
        after_v = _positional_version(sym, _content_version(image))
        if before == after_v:
            return
        repairs.append(make_op(
            {sym: (before, after_v)}, {sym: image},
            kind="rework" if before is not None else "touched",
            intent=f"revert {tag}: keep {sym.split('::')[-1]}'s place in {path}",
        ))

    predecessor: str | None = None
    for name in order_pre:
        if name not in kept:
            continue

        residue = f"{path}::__residue__::{name}"
        # Never put back the very thing that was asked for. `targeted` holds the symbols the user
        # named directly, as opposed to everything the removal's closure swept up, and a residue in
        # it is a residue somebody chose to remove. Re-emitting it made a revert of that op a no-op
        # that reported success, and a caller looping "revert whatever still covers this path"
        # never terminated -- `tests/core/test_tiers.py` hung there, and so did CI.
        if residue in targeted:
            continue
        if residue not in live_after:
            prior = pre_frontier.get(residue)
            if prior is not None:
                image = images_of(prior).get(residue) or b""
                if image:
                    _emit(residue, image)

        anchor = f"{path}::__anchor__::{name}"
        want = (predecessor or _ANCHOR_FIRST).encode("utf-8")
        tip = live_after.get(anchor)
        have = (images_of(tip).get(anchor) if tip is not None else None)
        if have != want:
            _emit(anchor, want)

        predecessor = name

    return repairs


def _prune_emptied_paths(
    paths, pre_frontier, live_after, by_id, images_of, removal, tag: str,
) -> list[Op]:
    """The counterpart of `_repair_layout` for a path the removal leaves with *no* entity at all.

    A file's end-of-file gap is `path::__residue__::\\x00HEAD\\x00` -- a sentinel, not any entity's
    name -- so `layout_ops_of` never reaches it and no removal ever takes it. Left behind it is still
    live and still content-bearing (`op.CONTENT_BEARING_KINDS` includes `residue`), so `fold.code`
    keeps covering the path and folds it to blank: reverting a file's last entity left a zero-byte
    tracked file, and for Python an importable module with none of its symbols (F42, 2026-08-16).
    Bottoming what remains makes the ideal genuinely stop covering the path, so
    `lens._write_working_tree` deletes it through the existing R4 backstop.

    Two gates, and both were found by trying to do without them:

    - The path must have had a live entity *before*. A comment-only file and an empty file are each
      represented by that same lone sentinel op and nothing else, so a predicate reading only the
      result would delete them -- a worse bug than the phantom it fixes.
    - What remains must be **blank**. A file's leading gap carries any header comment, and it is that
      same `\\x00HEAD\\x00` symbol: bottoming it discarded those bytes, so `restore` brought the entity
      back without the comment (byte loss), and in the meantime `code(I)` covered no symbol at all for
      the path, which sent `lens._write_working_tree` into its R4 backstop and left the reverted code
      sitting on disk under a `✓ revert applied` (a silent success -- strictly worse than a phantom).
      Leaving a non-blank path alone is also the right answer on its own terms: reverting the only
      function of a file that has a header comment should leave the header comment.
    """
    def live_syms(frontier, in_path: str) -> dict[str, str]:
        return {sym: oid for sym, oid in frontier.items()
                if sym.split("::", 1)[0] == in_path
                and not is_bottom(by_id[oid].footprint[sym][1])}

    out: list[Op] = []
    for path in sorted(paths):
        orphaned = live_syms(live_after, path)
        if any(is_behavioral(sym) for sym in orphaned):
            continue
        if not any(is_behavioral(sym) for sym in live_syms(pre_frontier, path)):
            continue  # never had an entity (comment-only, empty): not ours to remove
        if any((images_of(oid).get(sym) or b"").strip() for sym, oid in orphaned.items()):
            continue  # real bytes still live here (a header comment): never ours to discard
        for sym, oid in sorted(orphaned.items()):
            _before, sym_after = by_id[oid].footprint[sym]
            out.append(make_op(
                {sym: (sym_after, BOTTOM)}, {sym: None}, kind="prune",
                intent=f"revert {tag}: remove {path}, now empty",
                resolves=frozenset(o for o in removal if sym in by_id[o].footprint),
            ))
    return out


def plan_subtraction(
    repo: str | Path, target_ids, ops: list[Op], ideal_ids, declared, *, tag: str,
) -> SubtractionPlan:
    repo = Path(repo)
    by_id = {op.id: op for op in ops}
    live = frozenset(ideal_ids)
    targets = frozenset(target_ids) & live
    if not targets:
        return SubtractionPlan(ok=True, after_ids=live,
                               message=f"{tag}: none of its ops are in the current ideal; no change")

    declared_dependents_of: dict[str, set[str]] = {}
    for a, b in declared:
        if a in live and b in live:
            declared_dependents_of.setdefault(a, set()).add(b)

    # An entity's `__residue__`/`__anchor__` ops are its *siblings*, not its dependents, so no up-set
    # reaches them. Reverting the op that introduced `mod.py::only` used to leave that entity's
    # trailing gap live and orphaned, and `fold._fold_file` appends an orphaned gap at the end of the
    # file -- so `code(ideal)` stayed one gap longer than whatever a developer typed into that path
    # next, and `put()` refused every save there from then on, `sgt undo` included (F35). Pull the
    # layout facts in as *targets* rather than pruning them forward, so they ride the same exclusion
    # the entity does and `sgt restore` brings gap, anchor and entity back together.
    while True:
        blast = set(order.upset_in_many(targets, live, ops, declared))
        removal = _semantic_closure(targets, blast, by_id, declared_dependents_of)
        also = layout_ops_of(removal, by_id, live) - removal
        if not also:
            break
        targets = targets | also

    # The upward-closed part of the removal is excludable exactly as before; each member's whole
    # up-set lies inside the removal, so excluding it can never orphan a survivor.
    excludable = {oid for oid in removal
                  if order.upset_in(oid, live, ops, declared) <= removal}
    excluded = order.upset_in_many(excludable, live, ops, declared) if excludable else frozenset()
    survivors = live - excluded
    forward = removal - excluded

    layout_paths = {sym.split("::", 1)[0] for oid in removal
                    for sym in by_id[oid].footprint if "::" in sym}
    pre_frontier = order.frontier(live, ops)

    store = Store(repo)

    def _images(op_id: str) -> dict[str, bytes | None]:
        op = by_id[op_id]
        if op.images:
            return op.images
        stored = store.get(op_id)
        return stored.images if stored is not None else {}

    if not forward:
        # Nothing to splice forward -- but the file still needs both layout passes, the same two
        # the spliced path runs below. It used to run only the pruning one, and the gap that left
        # is not cosmetic: a plain feature revert is upward-closed, so it arrives *here* rather
        # than below, and the entities it keeps lost their separators. The observed damage was
        # `return sorted(months.items())def hourly_averages(readings):`, a file Python cannot
        # parse, plus a module-level constant that lived in the removed gap and took every later
        # reference down with it. `_repair_layout` re-grounds exactly that, from the recorded
        # pre-removal image, and had simply never been reached on this branch.
        post_frontier = order.frontier(survivors, ops)
        repairs: list[Op] = []
        all_by_id = dict(by_id)

        def _images_any(op_id: str) -> dict[str, bytes | None]:
            op = all_by_id.get(op_id)
            if op is not None and op.images:
                return op.images
            stored = store.get(op_id)
            return stored.images if stored is not None else {}

        targeted_syms = frozenset(
            sym for oid in targets for sym in by_id[oid].footprint
        )
        for layout_path in sorted(layout_paths):
            repairs.extend(
                _repair_layout(layout_path, pre_frontier, post_frontier, _images_any, all_by_id,
                               tag, targeted_syms)
            )
        all_by_id.update({o.id: o for o in repairs})
        live_after = order.frontier(survivors | {o.id for o in repairs}, ops + repairs)

        emptied = _prune_emptied_paths(layout_paths, pre_frontier, live_after,
                                       all_by_id, _images_any, removal, tag)
        new_ops = repairs + list(emptied)
        return SubtractionPlan(
            ok=True, after_ids=frozenset(survivors | {o.id for o in new_ops}),
            new_ops=tuple(new_ops), excluded=frozenset(excluded),
            broken_references=_broken_references(
                ops, survivors, removal, _born_symbols(removal, by_id), by_id, _images_any,
                live_after, new_ops),
        )

    chains = order._ordered_chains(live, ops)
    post_frontier = order.frontier(survivors, ops)
    born = _born_symbols(removal, by_id)

    new_ops: list[Op] = []
    subtracted: list[str] = []
    pruned: list[str] = []
    kept: list[str] = []
    handled: set[str] = set()

    for oid in sorted(forward):
        for sym in by_id[oid].footprint:
            if sym in handled:
                continue
            handled.add(sym)
            tip_id = post_frontier.get(sym)
            if tip_id is None:
                continue  # the exclusion already took the whole live chain
            tip_op = by_id[tip_id]
            tip_before, tip_after = tip_op.footprint[sym]
            ours = _images(tip_id).get(sym) or b""

            if sym in born:
                # The target introduced this symbol and its birth could not be excluded
                # (a multi-symbol op pins it in history): bottom it at the tip. Its own layout
                # artifacts (trailing residue gap, anchor fact) go with it -- leaving them live
                # re-partitions every later gap the miner derives from the materialized blob,
                # ungrounding all subsequent residue edits of the file (the blank-line drift
                # found finishing the S2 flow, 2026-08-09).
                resolves = frozenset(o for o in removal if sym in by_id[o].footprint)
                to_bottom = [sym]
                if "::" in sym and "::__" not in sym:
                    path, _, name = sym.partition("::")
                    for artifact in (f"{path}::__residue__::{name}",
                                     f"{path}::__anchor__::{name}"):
                        if artifact in post_frontier and artifact not in handled:
                            handled.add(artifact)
                            to_bottom.append(artifact)
                for bottom_sym in to_bottom:
                    artifact_tip = post_frontier[bottom_sym]
                    _b, artifact_after = by_id[artifact_tip].footprint[bottom_sym]
                    prune = make_op(
                        {bottom_sym: (artifact_after, BOTTOM)}, {bottom_sym: None},
                        kind="prune", intent=f"revert {tag}: remove {bottom_sym}",
                        resolves=resolves,
                    )
                    new_ops.append(prune)
                pruned.append(sym)
                continue

            chain = chains.get(sym, [])
            removed_here = [o for o in chain if o in forward]
            if not removed_here:
                continue
            producer_of = {}
            for chain_oid in chain:
                producer_of[by_id[chain_oid].footprint[sym][1]] = chain_oid

            conflicted = False
            current = ours
            for r_id in reversed(removed_here):
                r_op = by_id[r_id]
                r_before, _r_after = r_op.footprint[sym]
                base = _images(r_id).get(sym) or b""
                if r_before is None:
                    theirs = b""
                else:
                    producer = producer_of.get(r_before)
                    theirs = (_images(producer).get(sym) or b"") if producer else b""
                result = merge3(base, current, theirs)
                if result.conflicted:
                    conflicted = True
                    break
                current = result.merged

            if conflicted:
                kept.append(sym)
                continue
            if current == ours:
                continue  # nothing of the target's contribution is still present
            splice = make_op(
                {sym: (tip_after, _positional_version(sym, _content_version(current)))},
                {sym: current}, kind="rework",
                intent=f"revert {tag}: subtract {', '.join(o[:12] for o in removed_here)}",
                resolves=frozenset(removed_here),
            )
            new_ops.append(splice)
            subtracted.append(sym)

    broken = _broken_references(ops, survivors, removal, born, by_id, _images,
                                post_frontier, new_ops)

    # Keep the fold's layout invariant: every entity still live needs a live residue gap and an
    # anchor naming a live predecessor. The removal can strip both off symbols it never intended
    # to touch (their layout chains are upward-closed inside it), and the fold synthesizes no
    # separators of its own, so without this the materialized file loses its line breaks.
    all_by_id = dict(by_id)
    all_by_id.update({op.id: op for op in new_ops})

    def _images_any(op_id: str) -> dict[str, bytes | None]:
        op = all_by_id[op_id]
        if op.images:
            return op.images
        stored = store.get(op_id)
        return stored.images if stored is not None else {}

    live_after = order.frontier(survivors | {op.id for op in new_ops}, ops + new_ops)
    targeted_syms = frozenset(sym for oid in targets for sym in by_id[oid].footprint)
    for layout_path in sorted(layout_paths):
        new_ops.extend(
            _repair_layout(layout_path, pre_frontier, live_after, _images_any, all_by_id, tag,
                           targeted_syms)
        )

    new_ops.extend(_prune_emptied_paths(layout_paths, pre_frontier, live_after,
                                        all_by_id, _images_any, removal, tag))

    after = frozenset(survivors | {op.id for op in new_ops})
    return SubtractionPlan(
        ok=True, after_ids=after, new_ops=tuple(new_ops), excluded=frozenset(excluded),
        subtracted_symbols=tuple(sorted(subtracted)), pruned_symbols=tuple(sorted(pruned)),
        kept_conflicts=tuple(sorted(kept)), broken_references=broken,
    )
