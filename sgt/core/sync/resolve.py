"""Sync stage 3 -- reconcile the union into a mergeable state (plan U19/U20, D4/D5).

Pure over the in-memory `Ingested` picture, no disk writes. A same-symbol chain fork no longer
aborts (D5, divergence-as-state): the union is advanced by its *fork-free part* while the forked
tips are surfaced as durable state. The construction removes each forked symbol's two tips'
up-sets from the union -- a downward-closed set minus an upward-closed set is still downward-closed,
so the remainder is a valid ideal *by construction* (the forked tips, and everything transitively
built on either of them, are the only ops excluded; the common ancestor below the fork and every
unrelated chain proceed). A cyclic declared union folds without the offending edges (they travel
and are reported for `sgt after` retraction, never raised), pins union structurally with
contradictions reported, and the feature tree is rebuilt from the fork-free union (Greene-matched
against ours' own last tree so our feature ids stay stable).

`propose` validation (U24) reuses `ingest -> resolve` as a dry run, so this stage takes its inputs
explicitly and produces its outputs without persisting anything.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sgt.core import lens, order
from sgt.core.ideal import Ideal
from sgt.lens import reconcile
from sgt.lens.pins import Contradiction, Pins
from sgt.store.gitbind import GitBinding

from .ingest import Ingested


@dataclass(frozen=True)
class Resolution:
    forks: tuple[tuple[str, str, str], ...]
    merged_ideal: Ideal | None = None
    declared_orset: lens.DeclaredORSet | None = None
    declared_cycles: tuple[tuple[str, str], ...] = ()
    unioned_pins: Pins | None = None
    pin_contradictions: tuple[Contradiction, ...] = ()
    aliases: frozenset[tuple[str, str]] = frozenset()
    tree_result: dict | None = None


def resolve(repo: Path, ing: Ingested) -> Resolution:
    union_ids = ing.ours_ideal.op_ids | ing.theirs_ideal_ids

    # Declared edges union as an OR-Set by tag (U21/D6): the live edge set is every edge value with
    # a surviving (non-tombstoned) tag, so a retraction on one side travels while a concurrent add
    # on the other survives. `order` still consumes the plain live edge set (`fold-time` cycle
    # exclusion is unchanged -- it runs over the live edges).
    declared_orset = ing.ours_declared_orset.union(ing.theirs_declared_orset)
    declared = declared_orset.live()

    fork_triples = order.forks(ing.all_ops, union_ids)

    # Divergence-as-state (D5/C4): a fork does not block the fork-free remainder. `order.fork_free`
    # drops each forked symbol's two tips and their up-sets, leaving a valid ideal by construction
    # (a downward-closed set minus upward-closed up-sets stays downward-closed and, both claimants
    # of every forked step gone, fork-free). The forked tips never enter this ideal.
    fork_free_ids = order.fork_free(union_ids, ing.all_ops, declared)

    # A cyclic declared union can never be honored -- fold without the offending edges (report them
    # for `sgt after` retraction) rather than letting `Ideal.from_ops` raise on it. The full union
    # (cycles included) still travels to disk, so the retraction target is visible on both clones.
    declared_cycles = order.find_declared_cycles(ing.all_ops, declared)
    usable_declared = declared - set(declared_cycles)
    merged_ideal = Ideal.from_ops(fork_free_ids, ing.all_ops, usable_declared)

    # Witness-topo tie-break (D6): the git DAG supplies the causal order over pin witnesses, so a
    # deliberate re-pin (its witness a descendant of the stale one) wins regardless of sync order.
    unioned_pins, pin_contradictions = reconcile.union_pins(
        ing.ours_pins, ing.theirs_pins, is_ancestor=GitBinding(repo).is_ancestor
    )
    # Feature-id aliases union as a G-Set with the alias-merge rule (D6): a stale old-id reference
    # from either side still resolves, and a genuine collision (two clones minting different new ids
    # for one old id) picks a single deterministic winner on every replica.
    aliases = reconcile.union_aliases(ing.ours_aliases, ing.theirs_aliases)
    tree_result = reconcile.reconcile_tree(
        repo, ing.all_ops, merged_ideal, unioned_pins, ing.ours_tree
    )

    return Resolution(
        forks=tuple(fork_triples),
        merged_ideal=merged_ideal,
        declared_orset=declared_orset,
        declared_cycles=tuple(declared_cycles),
        unioned_pins=unioned_pins,
        pin_contradictions=tuple(pin_contradictions),
        aliases=aliases,
        tree_result=tree_result,
    )
