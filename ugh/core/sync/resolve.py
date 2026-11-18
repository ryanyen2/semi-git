"""Sync stage 3 -- reconcile the union into a mergeable state (plan U19/U20, D4/D5).

Pure over the in-memory `Ingested` picture, no disk writes. A same-symbol chain fork no longer
aborts (D5, divergence-as-state): the union is advanced by its *fork-free part* while the forked
tips are surfaced as durable state. The construction removes each forked symbol's two tips'
up-sets from the union -- a downward-closed set minus an upward-closed set is still downward-closed,
so the remainder is a valid ideal *by construction* (the forked tips, and everything transitively
built on either of them, are the only ops excluded; the common ancestor below the fork and every
unrelated chain proceed). The names tables merge three-way per op (`ugh.names.merge`).

`plan_sync` reuses `ingest -> resolve` as a dry run, so this stage takes its inputs explicitly and
produces its outputs without persisting anything.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ugh import names
from ugh.core import order
from ugh.core.ideal import Ideal
from ugh.intent import prompts as intent_prompts

from .ingest import Ingested


@dataclass(frozen=True)
class Resolution:
    forks: tuple[tuple[str, str, str], ...]
    merged_ideal: Ideal | None = None
    prompts: dict[str, str] = field(default_factory=dict)  # union-by-key (U5/KTD5)
    names: dict[str, str] = field(default_factory=dict)
    # (op id, ours, theirs) for every op both sides relabelled differently; ours was kept.
    name_conflicts: tuple[tuple[str, str, str], ...] = ()


def resolve(repo: Path, ing: Ingested) -> Resolution:
    ours = ing.ours_ideal.op_ids
    theirs = ing.theirs_ideal_ids
    union_ids = ours | theirs

    fork_triples = order.forks(ing.all_ops, union_ids)

    # Three-way subtraction (U8/R10-R11): a blind `ours ∪ theirs` resurrects anything a teammate
    # reverted -- the op is still in the *other* side's ideal, so the union re-adds it. An op present
    # in the recovered `base` (U7) but dropped by a side's *full* ideal was reverted there, so it and
    # everything grounded only through it (its up-set) must leave the union. `upset_in_many` computes
    # that collision-safely (union minus what stays grounded once the reverted base ops are gone).
    # `base == ∅` (base_recovery "none") -> `removed_seed == ∅` -> no removals -> today's plain union.
    base = ing.base_ideal_ids
    # The subtraction needs each side's *full* ideal against `base`. Ours always is (it's
    # `current_ideal`, so `base - ours` is a genuine ours-side revert). Theirs' revert shows as an op
    # *absent* from its ideal only when U7 recovered a full ideal (`trailers`). A `mined` recovery is
    # theirs' *divergent*
    # set (theirs kept `base` and added on top; a revert there rode in as a BOTTOM op via the union,
    # not as an absence), and `none` is unknown -- so for those we do not infer a theirs-side revert,
    # leaving today's union semantics for theirs.
    removed_seed = set(base - ours)
    if ing.theirs_recovery == "trailers":
        removed_seed |= base - theirs

    removals = set(order.upset_in_many(removed_seed, union_ids, ing.all_ops))

    # Divergence-as-state (D5/C4) survives subtraction: a fork-recorded tip present in `base` but
    # dropped on one side would otherwise be swept into `removed_seed`'s up-set and silently deleted.
    # Protect both tips of every detected fork (and their up-sets) from removal, so the fork is
    # *surfaced* rather than resolved by deletion -- `reduce_to_ideal` below still excludes the tips
    # from the folded ideal (parked at the common ancestor), exactly as before.
    if fork_triples:
        protected: set[str] = set()
        for _sym, tip_a, tip_b in fork_triples:
            protected |= order.upset(tip_a, ing.all_ops)
            protected |= order.upset(tip_b, ing.all_ops)
        removals -= protected

    # `reduce_to_ideal` restores the grounding pass whose absence crashed today's resolve on an
    # ungrounded union (grounding first, then fork-free): drop forked tips and their up-sets, keeping
    # only the well-founded remainder -- a valid ideal by construction.
    merged_seed = set(union_ids) - removals
    merged_ideal = Ideal.from_ops(
        order.reduce_to_ideal(merged_seed, ing.all_ops), ing.all_ops
    )

    prompts = intent_prompts.merge(ing.ours_prompts, ing.theirs_prompts)
    merged_names, name_conflicts = names.merge(ing.base_names, ing.ours_names, ing.theirs_names)

    # Surface only *resolvable* forks (synthetic-symbol and same-after pseudo-forks carry no user
    # remedy and are neutralized by `reduce_to_ideal` anyway). The full `fork_triples` still drives
    # tip protection above -- surfacing filters, structural reduction sees every collision.
    surfaced_forks = order.resolvable_forks(fork_triples, {o.id: o for o in ing.all_ops})

    return Resolution(
        forks=tuple(surfaced_forks),
        merged_ideal=merged_ideal,
        prompts=prompts,
        names=merged_names,
        name_conflicts=tuple(name_conflicts),
    )
