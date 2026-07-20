"""Unit coverage for `sgt.lens.reconcile.union_pins`'s witness-topo + hash tie-break (U21/D6).

The LAW-U convergence test (`tests/laws/test_convergence.py`) proves the end-to-end sync property;
these tests pin the tie-break's three branches directly: causally-later (ancestry) wins, concurrent
falls to a deterministic content hash, and the whole join is symmetric in ours/theirs so any sync
schedule converges. The `is_ancestor` callable is a plain in-memory fake -- no git repo needed.
"""

from __future__ import annotations

from sgt.lens.pins import Pins
from sgt.lens.reconcile import _assign_winner, resolve_alias, union_aliases, union_pins


def _ancestry(edges: set[tuple[str, str]]):
    """An `is_ancestor(a, b)` over an explicit ancestor->descendant edge set (reflexive)."""
    return lambda a, b: a == b or (a, b) in edges


def test_concurrent_assign_collision_is_hash_deterministic_and_symmetric():
    ours = Pins(assign={"m1": "featureA"})
    theirs = Pins(assign={"m1": "featureB"})
    merged, _ = union_pins(ours, theirs)
    swapped, _ = union_pins(theirs, ours)  # swap sides -> must land the same winner (symmetry)
    assert merged.assign == swapped.assign
    assert merged.assign["m1"] in ("featureA", "featureB")


def test_causally_later_assign_wins_over_the_stale_one():
    # theirs' witness descends from ours' -> theirs is a deliberate re-pin, it wins regardless of
    # what the content hash would have picked.
    ours = Pins(assign={"m1": "featureA"}, assign_witness={"m1": "sha_old"})
    theirs = Pins(assign={"m1": "featureB"}, assign_witness={"m1": "sha_new"})
    is_anc = _ancestry({("sha_old", "sha_new")})
    merged, _ = union_pins(ours, theirs, is_ancestor=is_anc)
    swapped, _ = union_pins(theirs, ours, is_ancestor=is_anc)
    assert merged.assign["m1"] == "featureB"
    assert swapped.assign["m1"] == "featureB"  # ancestry is not order-dependent
    assert merged.assign_witness["m1"] == "sha_new"  # the winning assignment keeps its witness


def test_missing_witness_falls_through_to_hash_tie_break():
    # one side has a witness, the other doesn't -> no ancestry info -> hash path, still symmetric.
    ours = Pins(assign={"m1": "featureA"}, assign_witness={"m1": "sha_a"})
    theirs = Pins(assign={"m1": "featureB"})
    is_anc = _ancestry(set())  # nothing is anyone's ancestor
    merged, _ = union_pins(ours, theirs, is_ancestor=is_anc)
    swapped, _ = union_pins(theirs, ours, is_ancestor=is_anc)
    assert merged.assign == swapped.assign


def test_disjoint_pins_and_set_fields_union_unchanged():
    ours = Pins(assign={"m1": "fA"}, must_link=frozenset({("a", "b")}), labels={"fA": "Auth"})
    theirs = Pins(assign={"m2": "fB"}, cannot_link=frozenset({("c", "d")}), labels={"fB": "DB"})
    merged, _ = union_pins(ours, theirs)
    assert merged.assign == {"m1": "fA", "m2": "fB"}
    assert merged.must_link == frozenset({("a", "b")})
    assert merged.cannot_link == frozenset({("c", "d")})
    assert merged.labels == {"fA": "Auth", "fB": "DB"}


def test_contradicting_label_rename_is_hash_deterministic():
    ours = Pins(labels={"fA": "Login"})
    theirs = Pins(labels={"fA": "SignIn"})
    merged, _ = union_pins(ours, theirs)
    swapped, _ = union_pins(theirs, ours)
    assert merged.labels == swapped.labels  # order-independent, not theirs-wins


def test_union_aliases_disjoint_is_a_plain_union():
    a = frozenset({("F0", "f-a")})
    b = frozenset({("F1", "f-b")})
    assert union_aliases(a, b) == frozenset({("F0", "f-a"), ("F1", "f-b")})


def test_union_aliases_collision_elects_one_winner_and_aliases_the_loser():
    # the same old id re-minted to two different new ids (divergent unsynced curation).
    a = frozenset({("F0", "f-alpha")})
    b = frozenset({("F0", "f-beta")})
    merged = union_aliases(a, b)
    assert union_aliases(a, b) == union_aliases(b, a)  # order-independent (LAW-U)
    winner = resolve_alias(merged, "F0")
    assert winner in ("f-alpha", "f-beta")
    # every reference -- the old id and *either* minted new id -- resolves to the one winner.
    assert resolve_alias(merged, "f-alpha") == winner
    assert resolve_alias(merged, "f-beta") == winner


def test_resolve_alias_follows_a_chain_to_the_terminal():
    aliases = frozenset({("F0", "f-a"), ("f-a", "f-b")})
    assert resolve_alias(aliases, "F0") == "f-b"  # F0 -> f-a -> f-b


def test_authored_label_register_reuses_assign_winner_not_pins_labels():
    # An authored feature's label must follow the witness-topological `assign`/`_assign_winner`
    # register (causally-later rename wins), NOT the weaker hash-only `pins.labels` merge -- which
    # has no witness input and so cannot guarantee "latest rename wins" (U6/KTD3). This pins that
    # `authored.merge_feature` reuses the *exact* `_assign_winner` decision `union_pins`'s `assign`
    # path makes for the same witness/label inputs.
    from sgt.lens import authored

    f = authored.create(["m1"], "Login")
    ours = authored.rename(f, "Login", witness="sha_old")
    theirs = authored.rename(f, "SignIn", witness="sha_new")
    is_anc = _ancestry({("sha_old", "sha_new")})
    merged = authored.merge_feature(ours, theirs, is_ancestor=is_anc)

    winner, witness = _assign_winner("f1", "Login", "sha_old", "SignIn", "sha_new", is_anc)
    assert (merged.label, merged.label_witness) == (winner, witness) == ("SignIn", "sha_new")
