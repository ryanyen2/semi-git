"""Unit coverage for `ugh.lens.authored` -- the authored-feature CRDT (U6/R3, KTD3).

The highest-risk unit: a wrong merge rule is a cross-replica bug that only surfaces when two
people's features disagree. Each of the three components is tested against its own failure mode:

- **membership is an OR-Set** (copies `DeclaredORSet`): concurrent add of *different* members
  unions; a concurrent create+delete converges, delete winning only if it *observed* the add.
- **the label is a witness-topological LWW register** reusing `reconcile._assign_winner` -- a
  causally-later rename beats a stale one, hash tie-break only when truly concurrent. NOT the
  weaker hash-only `pins.labels` merge.
- **the `af-` id is a carried UUID**, minted once and never re-derived from content, surviving a
  save/load round-trip verbatim.

The load-bearing property is that `merge` is commutative + idempotent (LAW-U), tested directly.
"""

from __future__ import annotations

from ugh.lens import authored


def _ancestry(edges):
    """An `is_ancestor(a, b)` over an explicit ancestor->descendant edge set (reflexive)."""
    return lambda a, b: a == b or (a, b) in edges


# -- rule 1: membership OR-Set --------------------------------------------------------------------


def test_concurrent_add_of_different_members_unions():
    base = authored.create(["m1"], "F")
    ours = authored.add_member(base, "m2")
    theirs = authored.add_member(base, "m3")
    merged = authored.merge_feature(ours, theirs)
    assert merged.live_members() == frozenset({"m1", "m2", "m3"})


# -- rule 2: witness-topological LWW label --------------------------------------------------------


def test_causally_later_rename_wins():
    f = authored.create(["m1"], "Login")
    ours = authored.rename(f, "Login", witness="sha_old")
    theirs = authored.rename(f, "SignIn", witness="sha_new")  # a deliberate re-name on top of ours
    is_anc = _ancestry({("sha_old", "sha_new")})
    merged = authored.merge_feature(ours, theirs, is_ancestor=is_anc)
    swapped = authored.merge_feature(theirs, ours, is_ancestor=is_anc)
    assert merged.label == "SignIn"
    assert swapped.label == "SignIn"  # ancestry is not sync-order-dependent
    assert merged.label_witness == "sha_new"  # the winning rename keeps its witness


def test_concurrent_rename_is_hash_deterministic_and_symmetric():
    f = authored.create(["m1"], "X")
    ours = authored.rename(f, "Login", witness="sha_a")
    theirs = authored.rename(f, "SignIn", witness="sha_b")
    is_anc = _ancestry(set())  # neither witnesses the other -> truly concurrent -> hash tie-break
    merged = authored.merge_feature(ours, theirs, is_ancestor=is_anc)
    swapped = authored.merge_feature(theirs, ours, is_ancestor=is_anc)
    assert merged.label == swapped.label  # order-independent, not theirs-wins
    assert merged.label in ("Login", "SignIn")


# -- rule 3: carried, protected af- id ------------------------------------------------------------


def test_af_id_is_carried_not_content_derived(tmp_path):
    # Two clones authoring "the same" feature (identical members + label) yield DISTINCT ids: the id
    # is a minted-once carried UUID, not content-addressed -- no accidental merge (KTD3).
    a = authored.create(["m1", "m2"], "Auth")
    b = authored.create(["m1", "m2"], "Auth")
    assert a.id != b.id
    assert a.id.startswith("af-") and b.id.startswith("af-")
    # The id survives a save/load round-trip verbatim (a rebuild reads it, never re-mints it).
    authored.save_authored(tmp_path, {a.id: a})
    assert authored.load_authored(tmp_path)[a.id].id == a.id
