"""Unit coverage for `sgt.lens.authored` -- the authored-feature CRDT (U6/R3, KTD3).

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

from sgt.lens import authored


def _ancestry(edges):
    """An `is_ancestor(a, b)` over an explicit ancestor->descendant edge set (reflexive)."""
    return lambda a, b: a == b or (a, b) in edges


# -- happy path -----------------------------------------------------------------------------------


def test_create_rename_add_remove_reads_back(tmp_path):
    f = authored.create(["m1", "m2"], "Auth")
    assert f.id.startswith("af-")
    assert f.live_members() == frozenset({"m1", "m2"})

    f = authored.rename(f, "Authentication")
    f = authored.add_member(f, "m3")
    f = authored.remove_member(f, "m1")

    authored.save_authored(tmp_path, {f.id: f})
    loaded = authored.load_authored(tmp_path)[f.id]
    assert loaded.label == "Authentication"
    assert loaded.live_members() == frozenset({"m2", "m3"})
    assert loaded.id == f.id  # carried verbatim through persistence


# -- rule 1: membership OR-Set --------------------------------------------------------------------


def test_concurrent_add_of_different_members_unions():
    base = authored.create(["m1"], "F")
    ours = authored.add_member(base, "m2")
    theirs = authored.add_member(base, "m3")
    merged = authored.merge_feature(ours, theirs)
    assert merged.live_members() == frozenset({"m1", "m2", "m3"})


def test_delete_wins_only_if_it_observed_the_add():
    base = authored.create(["m1"], "F")
    # Replica A deletes (tombstones every tag it locally observed). Replica B concurrently adds m2,
    # whose tag A never saw. OR-Set semantics: m1's observed add is tombstoned and gone; m2's
    # concurrent add survives the delete.
    deleted = authored.delete(base)
    added = authored.add_member(base, "m2")
    merged = authored.merge_feature(deleted, added)
    assert merged.live_members() == frozenset({"m2"})
    # Commutative in this scenario too.
    assert authored.merge_feature(added, deleted).live_members() == frozenset({"m2"})


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


# -- integration: commutativity + idempotence (LAW-U) ---------------------------------------------


def test_merge_is_commutative_and_idempotent():
    seed = authored.create(["m1", "m2"], "Base", witness="sha0")
    # A: renames + adds a member. B: renames differently + removes a member. Plus a solo feature on
    # each side that the other has never seen.
    a = authored.rename(authored.add_member(seed, "m3"), "Alpha", witness="sha_a")
    b = authored.remove_member(authored.rename(seed, "Beta", witness="sha_b"), "m1")
    ours = {seed.id: a, "af-onlyours": authored.create(["x"], "OnlyOurs")}
    theirs = {seed.id: b, "af-onlytheirs": authored.create(["y"], "OnlyTheirs")}
    is_anc = _ancestry(set())  # concurrent -> label decided by the deterministic hash tie-break

    ab = authored.merge(ours, theirs, is_ancestor=is_anc)
    ba = authored.merge(theirs, ours, is_ancestor=is_anc)
    assert ab == ba  # commutative

    assert authored.merge(ab, ab, is_ancestor=is_anc) == ab  # idempotent

    # sanity: the shared feature carries the union of both member edits and keeps its id.
    shared = ab[seed.id]
    assert shared.id == seed.id
    assert shared.live_members() == frozenset({"m2", "m3"})  # m1 removed by B, m3 added by A
