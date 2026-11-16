"""Sync stage 3 -- reconcile the union into a mergeable state (plan U19/U20, D4/D5).

Pure over the in-memory `Ingested` picture, no disk writes. A same-symbol chain fork no longer
aborts (D5, divergence-as-state): the union is advanced by its *fork-free part* while the forked
tips are surfaced as durable state. The construction removes each forked symbol's two tips'
up-sets from the union -- a downward-closed set minus an upward-closed set is still downward-closed,
so the remainder is a valid ideal *by construction* (the forked tips, and everything transitively
built on either of them, are the only ops excluded; the common ancestor below the fork and every
unrelated chain proceed). Pins union structurally with contradictions reported, and the feature
tree is rebuilt from the fork-free union (Greene-matched against ours' own last tree so our feature
ids stay stable).

`plan_sync` reuses `ingest -> resolve` as a dry run, so this stage takes its inputs explicitly and
produces its outputs without persisting anything.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ugh.core import lens, order
from ugh.core.ideal import Ideal
from ugh.intent import prompts as intent_prompts
from ugh.lens import authored, reconcile
from ugh.lens.authored import AuthoredFeature
from ugh.lens.pins import Contradiction, Pins
from ugh.store.gitbind import GitBinding

from .ingest import Ingested


@dataclass(frozen=True)
class Resolution:
    forks: tuple[tuple[str, str, str], ...]
    merged_ideal: Ideal | None = None
    unioned_pins: Pins | None = None
    pin_contradictions: tuple[Contradiction, ...] = ()
    tree_result: dict | None = None
    prompts: dict[str, str] = field(default_factory=dict)  # union-by-key (U5/KTD5)
    # Authored features merged field-by-field (U6/R3): OR-Set membership + witness-topo LWW label +
    # carried af- id -- first-class merged state, never rebuilt from the op store like the tree.
    unioned_authored: dict[str, AuthoredFeature] = field(default_factory=dict)
    # Shared exclusion OR-Sets (Phase 1.2 §E): the per-ref-key union of both clones' revert records,
    # persisted by `flush_reconciled_metadata` so the merged exclusion becomes authoritative on both
    # sides (never re-minted by `record_ideal`, which runs with `record_exclusions=False` here).
    exclusions: dict[str, lens.ExclusionORSet] = field(default_factory=dict)


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

    # Shared exclusion CRDT (Phase 1.2 §E): union both clones' per-ref revert records by tag, then
    # seed the subtraction with this ref's *live* exclusions. Unlike the base-diff above -- which only
    # sees a revert the recovered base still records -- the exclusion is the durable positive record
    # that survives a git history rewrite and a fresh clone's cold mine (F20), so a teammate's revert
    # subtracts here even when the base degraded to ∅ or the op rode in as a BOTTOM op via the union.
    # A *restored* op is a tombstoned tag, so `live()` already drops it -- no separate protection.
    merged_exclusions = lens.merge_exclusions(ing.ours_exclusions, ing.theirs_exclusions)
    ref_key = lens.current_ref_key(repo)
    live_excl = merged_exclusions.get(ref_key, lens.ExclusionORSet()).live() if ref_key else set()
    removed_seed |= set(live_excl)

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

    # Witness-topo tie-break (D6): the git DAG supplies the causal order over pin witnesses, so a
    # deliberate re-pin (its witness a descendant of the stale one) wins regardless of sync order.
    is_ancestor = GitBinding(repo).is_ancestor
    unioned_pins, pin_contradictions = reconcile.union_pins(
        ing.ours_pins, ing.theirs_pins, is_ancestor=is_ancestor
    )
    # Authored features are first-class merged state (U6/R3/KTD3), not rebuilt from the op store like
    # the tree below: membership unions as an OR-Set, the label uses the same witness-topo LWW rule
    # as pins, and the carried af- id is never recomputed. Reuses the exact `_assign_winner` tie-break.
    unioned_authored = authored.merge(ing.ours_authored, ing.theirs_authored, is_ancestor=is_ancestor)
    tree_result = reconcile.reconcile_tree(
        repo, ing.all_ops, merged_ideal, unioned_pins, ing.ours_tree
    )
    # Prompt sidecar unions as a plain G-Set-by-key (U5/KTD5): write-once per key makes this
    # conflict-free, unlike the tree (which is rebuilt, not merged -- themes follow the same
    # re-derive rule and are rebuilt post-commit, once the merged history is real; see materialize).
    prompts = intent_prompts.merge(ing.ours_prompts, ing.theirs_prompts)

    # Surface only *resolvable* forks (synthetic-symbol and same-after pseudo-forks carry no user
    # remedy and are neutralized by `reduce_to_ideal` anyway). The full `fork_triples` still drives
    # tip protection above -- surfacing filters, structural reduction sees every collision.
    surfaced_forks = order.resolvable_forks(fork_triples, {o.id: o for o in ing.all_ops})

    return Resolution(
        forks=tuple(surfaced_forks),
        merged_ideal=merged_ideal,
        unioned_pins=unioned_pins,
        pin_contradictions=tuple(pin_contradictions),
        tree_result=tree_result,
        prompts=prompts,
        unioned_authored=unioned_authored,
        exclusions=merged_exclusions,
    )
