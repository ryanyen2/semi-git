"""Tests for sgt.core.rewrite -- the explicit escape hatch (plan U11, R14/R17).

Each draft-producing verb (`merge_op`/`split_op`/`transplant`/`revert_keep_dependents`) computes
the exact part and writes hollow op(s) off-chain; `stage`/`fulfill` supplies real images and
folds+writes the candidate to the working tree without committing; `land` is the only step that
commits, gated on the oracle's verdict for that exact candidate (R14) -- distinct from R13's
async, non-blocking materialization gate that ordinary ideal-edit verbs (U8) use.
"""

from __future__ import annotations

import json

from sgt.config import load_identity_constraints
from sgt.core import identity, mine, oracle, order, rewrite
from sgt.core.fold import code
from sgt.core.ideal import Ideal
from sgt.core.lens import get, ideal_for_ref
from sgt.core.op import make_op
from sgt.core.order import is_valid_ideal
from sgt.core.rewrite import RewriteDraft
from sgt.core.store import Store
from sgt.store.gitbind import init_store
from tests.laws import corpus


def _op_with(ops, sym: str, needle: bytes):
    return next(
        o for o in ops
        if sym in o.footprint and o.images.get(sym) is not None and needle in o.images[sym]
    )


# -- merge-op ---------------------------------------------------------------------------------

def test_merge_op_drafts_a_hollow_for_the_forked_symbol(tmp_path):
    """A genuine chain fork (the diverged_chain corpus, same shape U8's cherry-pick refuses on,
    AE2) drafts exactly one hollow for the forked symbol, chain-extending the current ref's own
    tip (see rewrite.py's module docstring for why `tip_b` rides in `intent`, not `requires`)."""
    repo = corpus.CORPUS["diverged_chain"].build(tmp_path / "repo")
    corpus.checkout(repo, "release")
    release_ideal = get(repo)
    corpus.checkout(repo, "main")
    main_ideal = get(repo)
    ops = Store(repo).all_ops()
    by_id = {o.id: o for o in ops}

    main_tip = main_ideal.frontier(ops)["slugify.py::slugify"]
    release_tip = release_ideal.frontier(ops)["slugify.py::slugify"]

    draft = rewrite.merge_op(repo, main_tip, release_tip)
    assert draft.ok and len(draft.hollow_ids) == 1 and draft.draft_id

    hollow = Store(repo).get_hollow(draft.hollow_ids[0])
    # the hollow chain-extends main's own tip: before_version == main_tip's own produced version.
    assert hollow.footprint["slugify.py::slugify"][0] == by_id[main_tip].footprint["slugify.py::slugify"][1]
    assert hollow.off_chain
    assert main_tip[:12] in hollow.intent and release_tip[:12] in hollow.intent
    # D5: the same resolution is also recorded as a structured, queryable field.
    assert hollow.resolves == frozenset({main_tip, release_tip})


def test_merge_op_fulfilled_op_carries_resolves_forward(tmp_path):
    """D5: fulfilling a merge-op hollow carries its `resolves` onto the real, committed op --
    and doing so does not move the op's id (`resolves` is excluded from `compute_id`)."""
    repo = corpus.CORPUS["diverged_chain"].build(tmp_path / "repo")
    corpus.checkout(repo, "release")
    release_ideal = get(repo)
    corpus.checkout(repo, "main")
    main_ideal = get(repo)
    ops = Store(repo).all_ops()
    main_tip = main_ideal.frontier(ops)["slugify.py::slugify"]
    release_tip = release_ideal.frontier(ops)["slugify.py::slugify"]

    draft = rewrite.merge_op(repo, main_tip, release_tip)
    (repo / "slugify.py").write_text(
        "def slugify(s):\n    return s.lower().strip().replace(' ', '-')\n", encoding="utf-8"
    )
    candidate = rewrite.fulfill(repo, draft.draft_id, from_tree=True)

    real = next(o for o in Store(repo).all_ops() if o.id in candidate.op_ids and o.resolves)
    assert real.resolves == frozenset({main_tip, release_tip})
    # id-exclusion: the same footprint+image without `resolves` hashes identically.
    from sgt.core.op import compute_id
    assert real.id == compute_id(real.footprint, real.images, real.requires, real.kind, real.miner_version)


def test_merge_op_refuses_when_nothing_is_forked(tmp_path):
    repo = corpus.CORPUS["linear_history"].build(tmp_path / "repo")
    get(repo)
    ops = Store(repo).all_ops()
    a = _op_with(ops, "a.py::foo", b"return 1")
    same = a.id  # trivially: an op and itself share no *forked* symbol

    draft = rewrite.merge_op(repo, a.id, same)
    assert not draft.ok and draft.hollow_ids == ()


def test_merge_op_land_gating_pending_then_override(tmp_path):
    """Landing a merge-op refuses while the oracle verdict is pending, and refuses an override
    that itself resolves to fail; a passing override lands and the ideal is valid."""
    repo = corpus.CORPUS["diverged_chain"].build(tmp_path / "repo")
    corpus.checkout(repo, "release")
    release_ideal = get(repo)
    corpus.checkout(repo, "main")
    main_ideal = get(repo)
    ops = Store(repo).all_ops()
    main_tip = main_ideal.frontier(ops)["slugify.py::slugify"]
    release_tip = release_ideal.frontier(ops)["slugify.py::slugify"]

    draft = rewrite.merge_op(repo, main_tip, release_tip)
    (repo / "slugify.py").write_text(
        "def slugify(s):\n    return s.lower().strip().replace(' ', '-')\n", encoding="utf-8"
    )
    candidate = rewrite.fulfill(repo, draft.draft_id, from_tree=True)
    assert is_valid_ideal(Store(repo).all_ops(), candidate.op_ids)
    assert draft.draft_id not in rewrite.pending_drafts(repo)  # consumed

    try:
        rewrite.land(repo)
        assert False, "expected a pending-verdict refusal"
    except rewrite.RewriteError as e:
        assert "pending" in str(e)

    try:
        rewrite.land(repo, override=("fail", "still broken", "reviewer"))
        assert False, "expected a failing-override refusal"
    except rewrite.RewriteError as e:
        assert "fail" in str(e)

    sha = rewrite.land(repo, override=("pass", "both diffs reconciled by hand", "reviewer"))
    assert sha

    after = get(repo)
    assert is_valid_ideal(Store(repo).all_ops(), after.op_ids)
    materialized = code(after, Store(repo).all_ops())
    assert materialized["slugify.py"] == b"def slugify(s):\n    return s.lower().strip().replace(' ', '-')\n"
    assert rewrite.staged_candidate(repo) is None  # cleared on landing


# -- split-op -----------------------------------------------------------------------------------

def test_split_op_produces_original_intermediate_after_chain(tmp_path):
    """split-op inserts an agent-authored intermediate checkpoint; the tail back to the
    original's own final bytes is minted automatically, verbatim -- net materialized content is
    unchanged, only the chain granularity gains a checkpoint."""
    repo = tmp_path / "repo"
    gb, _ = init_store(repo)
    (repo / "m.py").write_text(
        "def process(items):\n"
        "    total = 0\n"
        "    for i in items:\n"
        "        total += i\n"
        "    unique = list(set(items))\n"
        "    return total, unique\n",
        encoding="utf-8",
    )
    gb.commit_all("add process (two concerns)")
    ideal = get(repo)
    ops = Store(repo).all_ops()
    original = next(o for o in ops if "m.py::process" in o.footprint)
    original_after = original.footprint["m.py::process"][1]

    draft = rewrite.split_op(repo, original.id)
    assert draft.ok and len(draft.hollow_ids) == 1
    assert draft.meta["removed_ids"] == [original.id]

    (repo / "m.py").write_text(
        "def process(items):\n"
        "    total = 0\n"
        "    for i in items:\n"
        "        total += i\n"
        "    return total\n",
        encoding="utf-8",
    )
    candidate = rewrite.fulfill(repo, draft.draft_id, from_tree=True)
    assert original.id not in candidate.op_ids
    assert is_valid_ideal(Store(repo).all_ops(), candidate.op_ids)

    ops = Store(repo).all_ops()
    by_id = {o.id: o for o in ops}
    chain = order._ordered_chains(candidate.op_ids, ops)["m.py::process"]
    assert len(chain) == 2
    intermediate, tail = (by_id[oid] for oid in chain)
    assert intermediate.footprint["m.py::process"][0] is None  # original's own before_version
    assert intermediate.images["m.py::process"].endswith(b"return total")
    assert tail.footprint["m.py::process"] == (intermediate.footprint["m.py::process"][1], original_after)
    assert tail.images["m.py::process"] == original.images["m.py::process"]  # reused verbatim

    sha = rewrite.land(repo, override=("pass", "finer checkpoint for future pins", "reviewer"))
    assert sha
    materialized = code(get(repo), Store(repo).all_ops())
    assert materialized["m.py"] == original.images["m.py::process"] + b"\n"


# -- revert --keep-dependents --------------------------------------------------------------------

def test_revert_keep_dependents_drops_target_but_keeps_dependent_symbol_live(tmp_path):
    """Removing `helper` drops it (and its direct dependent's *old* op) from the ideal, but
    drafts a continuation hollow for the dependent's own symbol so it stays present, only
    needing content that no longer depends on the removed symbol."""
    repo = tmp_path / "repo"
    gb, _ = init_store(repo)
    # separate commits: touched together in one commit, def-use untangling (BET-A) would fold
    # helper and user into a *single* op (they're directly connected), leaving no cross-op
    # reference edge to test at all.
    (repo / "a.py").write_text("def helper():\n    return 1\n", encoding="utf-8")
    gb.commit_all("add helper")
    (repo / "b.py").write_text("from a import helper\n\ndef user():\n    return helper() + 1\n", encoding="utf-8")
    gb.commit_all("add user, depending on helper")
    ideal = get(repo)
    ops = Store(repo).all_ops()
    by_id = {o.id: o for o in ops}
    helper_op = next(o for o in ops if "a.py::helper" in o.footprint)
    user_op = next(o for o in ops if "b.py::user" in o.footprint)
    assert (helper_op.id, user_op.id) in order.reference_edges(ops)  # sanity: a real dependency

    draft = rewrite.revert_keep_dependents(repo, helper_op.id)
    assert draft.ok
    assert set(draft.meta["removed_ids"]) == {helper_op.id, user_op.id}
    assert len(draft.hollow_ids) == 1
    hollow = Store(repo).get_hollow(draft.hollow_ids[0])
    assert "b.py::user" in hollow.footprint
    assert "a.py::helper" in hollow.intent

    (repo / "b.py").write_text("def user():\n    return 99\n", encoding="utf-8")
    candidate = rewrite.fulfill(repo, draft.draft_id, from_tree=True)
    assert helper_op.id not in candidate.op_ids and user_op.id not in candidate.op_ids
    assert is_valid_ideal(Store(repo).all_ops(), candidate.op_ids)

    sha = rewrite.land(repo, override=("pass", "dependency removed by hand", "reviewer"))
    assert sha
    after = get(repo)
    ops = Store(repo).all_ops()
    assert "a.py::helper" not in after.frontier(ops)
    fulfilled_user = next(o for o in ops if "b.py::user" in o.footprint and o.id != user_op.id)
    assert fulfilled_user.images["b.py::user"] == b"def user():\n    return 99"


def test_revert_keep_dependents_carries_transitive_dependent_forward_unchanged(tmp_path):
    """`caller` is two hops from `helper` (it calls `user`, never `helper` itself). U7: it must not
    be dropped like a plain revert, but it also must not get a hollow -- its own bytes never
    named the removed symbol, so it's carried forward unchanged (`build_candidate`'s
    `carry_forward` step) rather than costing the repair loop a backend call it doesn't need."""
    repo = tmp_path / "repo"
    gb, _ = init_store(repo)
    (repo / "a.py").write_text("def helper():\n    return 1\n", encoding="utf-8")
    gb.commit_all("add helper")
    (repo / "b.py").write_text("from a import helper\n\ndef user():\n    return helper() + 1\n", encoding="utf-8")
    gb.commit_all("add user, depending on helper")
    original_c_py = b"from b import user\n\ndef caller():\n    return user() + 1\n"
    (repo / "c.py").write_bytes(original_c_py)
    gb.commit_all("add caller, depending on user (not helper)")
    get(repo)
    ops = Store(repo).all_ops()
    helper_op = next(o for o in ops if "a.py::helper" in o.footprint)
    user_op = next(o for o in ops if "b.py::user" in o.footprint)
    caller_op = next(o for o in ops if "c.py::caller" in o.footprint)
    assert (helper_op.id, user_op.id) in order.reference_edges(ops)  # sanity: real dependencies
    assert (user_op.id, caller_op.id) in order.reference_edges(ops)

    draft = rewrite.revert_keep_dependents(repo, helper_op.id)
    assert draft.ok
    assert set(draft.meta["removed_ids"]) == {helper_op.id, user_op.id, caller_op.id}
    assert len(draft.hollow_ids) == 1  # only `user` names the removed symbol -- only it needs a rewrite
    hollow = Store(repo).get_hollow(draft.hollow_ids[0])
    assert "b.py::user" in hollow.footprint
    assert draft.meta["carry_forward"] == ["c.py::caller"]

    (repo / "b.py").write_text("def user():\n    return 99\n", encoding="utf-8")
    candidate = rewrite.fulfill(repo, draft.draft_id, from_tree=True)
    assert {helper_op.id, user_op.id, caller_op.id}.isdisjoint(candidate.op_ids)
    ops = Store(repo).all_ops()
    assert is_valid_ideal(ops, candidate.op_ids)
    carried = next(o for o in ops if "c.py::caller" in o.footprint and o.id != caller_op.id)
    assert carried.id in candidate.op_ids
    assert carried.images["c.py::caller"] == caller_op.images["c.py::caller"]  # bytes unchanged
    assert carried.requires == frozenset()  # cleared, same as a direct-dependent hollow's fulfillment

    sha = rewrite.land(repo, override=("pass", "transitive dependent carried forward", "reviewer"))
    assert sha
    after = get(repo)
    ops = Store(repo).all_ops()
    assert "a.py::helper" not in after.frontier(ops)
    assert code(after, ops)["c.py"] == original_c_py


def test_revert_keep_dependents_refuses_an_unresolvable_target(tmp_path):
    repo = tmp_path / "repo"
    gb, _ = init_store(repo)
    (repo / "a.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    gb.commit_all("add foo")
    get(repo)
    draft = rewrite.revert_keep_dependents(repo, "not-a-real-op-id")
    assert not draft.ok and draft.hollow_ids == ()


# -- U5: mechanical repoint (one crisp LLM rule; R6) --------------------------------------------

def _helper_user_repo(tmp_path):
    """The same separate-commit helper/user pair the revert tests use: a cross-op reference edge
    (`user` requires `helper`'s exact version) is the edge a repoint rewrites."""
    repo = tmp_path / "repo"
    gb, _ = init_store(repo)
    (repo / "a.py").write_text("def helper():\n    return 1\n", encoding="utf-8")
    gb.commit_all("add helper")
    (repo / "b.py").write_text("from a import helper\n\ndef user():\n    return helper() + 1\n", encoding="utf-8")
    gb.commit_all("add user, depending on helper")
    get(repo)
    ops = Store(repo).all_ops()
    helper_op = next(o for o in ops if "a.py::helper" in o.footprint)
    user_op = next(o for o in ops if "b.py::user" in o.footprint)
    assert (helper_op.id, user_op.id) in order.reference_edges(ops)
    return repo, helper_op, user_op


def test_repoint_mints_a_requires_updated_op_byte_identical_in_image(tmp_path):
    """U5/R6: when the target advances to a new content version, a dependent that named the target's
    *old* version has a stale `requires` edge but unchanged bytes. `build_candidate`'s repoint step
    mints a fresh producer with the *same footprint and same image*, rewriting only that one edge
    old->new -- pure and content-addressed, no hollow, no backend, no LLM."""
    repo, helper_op, user_op = _helper_user_repo(tmp_path)
    helper_sym = "a.py::helper"
    v1 = helper_op.footprint[helper_sym][1]
    assert (helper_sym, v1) in user_op.requires  # the edge that will go stale

    # A chain-extension of `helper` to a new version -- exactly what `edit` (U4) drafts for the target.
    new_bytes = b"def helper():\n    return 2"
    v2 = mine._positional_version(helper_sym, mine._content_version(new_bytes))
    helper2 = make_op({helper_sym: (v1, v2)}, {helper_sym: new_bytes}, kind="extend")
    Store(repo).add(helper2)

    draft = RewriteDraft(
        ok=True, verb="edit", target=helper_op.id,
        meta={
            "removed_ids": [user_op.id],
            "required_ids": [helper2.id],
            "repoint": [{"op_id": user_op.id, "symbol": helper_sym,
                         "old_version": v1, "new_version": v2}],
        },
    )
    candidate, fulfilled = rewrite.build_candidate(repo, draft)

    repointed = fulfilled[f"repoint:{user_op.id}"]
    assert repointed.images["b.py::user"] == user_op.images["b.py::user"]  # byte-identical image
    assert repointed.footprint == user_op.footprint  # same footprint
    assert (helper_sym, v2) in repointed.requires and (helper_sym, v1) not in repointed.requires
    # distinct provenance label from carry_forward (kind "rework") and from LLM-filled hollows.
    assert repointed.kind == "repoint"
    assert user_op.id not in candidate.op_ids and repointed.id in candidate.op_ids
    # `build_candidate` already validated the candidate via `Ideal.from_ops`; the fulfilled ops
    # aren't in the store yet, so validate against the store plus what it just minted.
    assert is_valid_ideal(Store(repo).all_ops() + list(fulfilled.values()), candidate.op_ids)


def test_repoint_leaves_an_op_with_no_edge_to_the_target_untouched(tmp_path):
    """U5: repoint only rewrites a dependent that actually named the (target, old_version) pair it's
    asked to remap. An op with no such `requires` edge is left alone -- no op minted for it."""
    repo, helper_op, user_op = _helper_user_repo(tmp_path)
    helper_sym = "a.py::helper"
    v1 = helper_op.footprint[helper_sym][1]

    # `helper_op` is a fresh add with empty `requires`: it has no edge to the target, so repointing
    # it is a no-op even though it's named in the repoint list.
    assert (helper_sym, v1) not in helper_op.requires
    draft = RewriteDraft(
        ok=True, verb="edit", target=helper_op.id,
        meta={"repoint": [{"op_id": helper_op.id, "symbol": helper_sym,
                           "old_version": v1, "new_version": "brand-new-version"}]},
    )
    candidate, fulfilled = rewrite.build_candidate(repo, draft)
    assert not any(k.startswith("repoint:") for k in fulfilled)  # nothing minted
    assert helper_op.id in candidate.op_ids and user_op.id in candidate.op_ids
    assert is_valid_ideal(Store(repo).all_ops(), candidate.op_ids)


# -- transplant ---------------------------------------------------------------------------------

def test_transplant_drafts_with_destination_tip_as_before_version(tmp_path):
    """AE3: transplanting main's op onto `release` drafts a hollow whose `before_version` is
    release's *own* chain tip for that symbol, not main's -- so fulfilling it extends release's
    chain rather than reproducing main's fork."""
    repo = corpus.CORPUS["diverged_chain"].build(tmp_path / "repo")
    corpus.checkout(repo, "release")
    release_ideal = get(repo)
    corpus.checkout(repo, "main")
    main_ideal = get(repo)
    ops = Store(repo).all_ops()
    by_id = {o.id: o for o in ops}
    main_tip = main_ideal.frontier(ops)["slugify.py::slugify"]
    release_tip = release_ideal.frontier(ops)["slugify.py::slugify"]

    draft = rewrite.transplant(repo, [main_tip], "release")
    assert draft.ok and len(draft.hollow_ids) >= 1

    hollow = next(
        h for h in (Store(repo).get_hollow(hid) for hid in draft.hollow_ids)
        if "slugify.py::slugify" in h.footprint
    )
    # before_version == release's own frontier tip's produced version, not main's.
    assert hollow.footprint["slugify.py::slugify"][0] == by_id[release_tip].footprint["slugify.py::slugify"][1]
    assert "release" in hollow.intent and main_tip[:12] in hollow.intent


def test_transplant_refuses_unresolvable_source(tmp_path):
    repo = corpus.CORPUS["linear_history"].build(tmp_path / "repo")
    get(repo)
    draft = rewrite.transplant(repo, ["not-a-real-op"], "main")
    assert not draft.ok


# -- identity split / join ------------------------------------------------------------------------

def _snap(name, file, kind, body):
    import dataclasses
    from sgt.entities.extract import extract_file

    (base,) = extract_file("t.py", body)
    e = dataclasses.replace(base, id=f"{file}::{name}", name=name, file=file, kind=kind)
    return identity.snapshot([e], body)[0]


def test_never_link_blocks_a_would_be_hash_match():
    body = "def helper(nodes):\n    total = 0\n    for n in nodes:\n        total += n\n    return total"
    before = [_snap("foo", "x.py", "function", body)]
    after = [_snap("bar", "x.py", "function", body)]  # identical body -- would tier-2 link

    unconstrained = identity.match_pair(before, after)
    assert len(unconstrained.links) == 1

    from sgt.config import IdentityConstraints
    blocked = identity.match_pair(
        before, after, IdentityConstraints(never_link=frozenset({("x.py::foo", "x.py::bar")}))
    )
    assert blocked.links == []
    assert {s.ent.id for s in blocked.added} == {"x.py::bar"}
    assert {s.ent.id for s in blocked.removed} == {"x.py::foo"}


def test_force_link_creates_a_link_hash_and_fuzzy_tiers_would_miss():
    before = [_snap("alpha", "x.py", "function", "def alpha():\n    self.a = 1\n    self.b = 2")]
    after = [_snap("beta", "x.py", "function", "def beta():\n    self.c = compute()\n    self.d = fetch()")]

    unconstrained = identity.match_pair(before, after)
    assert unconstrained.links == []  # unrelated bodies, correctly not linked by default

    from sgt.config import IdentityConstraints
    forced = identity.match_pair(
        before, after, IdentityConstraints(force_link=frozenset({("x.py::alpha", "x.py::beta")}))
    )
    assert len(forced.links) == 1
    old, new = forced.links[0]
    assert (old.ent.id, new.ent.id) == ("x.py::alpha", "x.py::beta")


_LONG_BODY = (
    "def {name}(nodes):\n"
    "    total = 0\n"
    "    accumulator = []\n"
    "    for n in nodes:\n"
    "        total = total + n\n"
    "        accumulator.append(total)\n"
    "    return accumulator\n"
)


def test_identity_split_persists_and_a_subsequent_remine_respects_it(tmp_path):
    """A rename with a mostly-unchanged body (only the name token differs) links via the fuzzy
    tier by default; `identity split` on that exact pair makes a *subsequent* `mine()` call treat
    it as delete + add instead."""
    repo = tmp_path / "repo"
    gb, _ = init_store(repo)
    (repo / "a.py").write_text(_LONG_BODY.format(name="foo"), encoding="utf-8")
    gb.commit_all("add foo")
    (repo / "a.py").write_text(_LONG_BODY.format(name="bar"), encoding="utf-8")
    gb.commit_all("rename foo -> bar, same body")

    before_ops, _last_sha = mine.mine(repo)
    assert any(o.kind == "move" and "a.py::foo" in o.footprint for o in before_ops)  # linked by default

    result = rewrite.identity_split(repo, "a.py::foo", "a.py::bar")
    assert result["never_link"] == [("a.py::bar", "a.py::foo")]
    constraints = load_identity_constraints(repo)
    assert ("a.py::bar", "a.py::foo") in constraints.never_link

    after_ops, _last_sha = mine.mine(repo)
    kinds = sorted(o.kind for o in after_ops)
    assert "move" not in kinds  # the weld is gone
    added = next(o for o in after_ops if o.kind == "add" and "a.py::bar" in o.footprint)
    removed = next(o for o in after_ops if o.kind == "prune" and "a.py::foo" in o.footprint)
    assert added and removed


def test_identity_join_persists_and_a_subsequent_remine_links_it(tmp_path):
    """The inverse: two unrelated-looking bodies the matcher would never link on its own; after
    `identity join`, a subsequent `mine()` welds them into one chain via the recorded constraint."""
    repo = tmp_path / "repo"
    gb, _ = init_store(repo)
    (repo / "a.py").write_text(
        "def alpha():\n    x = 1\n    y = 2\n    return x + y\n", encoding="utf-8"
    )
    gb.commit_all("add alpha")
    (repo / "a.py").write_text(
        "def beta():\n    p = fetch()\n    q = read()\n    return p - q\n", encoding="utf-8"
    )
    gb.commit_all("rewrite alpha -> beta -- same identity, unrelated-looking body")

    before_ops, _last_sha = mine.mine(repo)
    assert not any(o.kind == "move" for o in before_ops)  # not linked by default

    rewrite.identity_join(repo, "a.py::alpha", "a.py::beta")
    constraints = load_identity_constraints(repo)
    assert ("a.py::alpha", "a.py::beta") in constraints.force_link

    after_ops, _last_sha = mine.mine(repo)
    linked = [o for o in after_ops if o.kind == "move" and "a.py::alpha" in o.footprint]
    assert len(linked) == 1
    assert linked[0].footprint["a.py::alpha"][0] is not None  # a move, not a fresh add
    assert not any(o.kind == "add" and "a.py::beta" in o.footprint for o in after_ops)
    assert b"fetch" in linked[0].images["a.py::alpha"]


def test_identity_split_then_join_is_idempotent_and_mutually_exclusive():
    """Re-applying split/join on the same pair keeps exactly one constraint, never both."""
    repo_data = {"never_link": [], "force_link": []}
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        rewrite.identity_split(repo, "x.py::a", "x.py::b")
        c = load_identity_constraints(repo)
        assert ("x.py::a", "x.py::b") in c.never_link and not c.force_link

        rewrite.identity_join(repo, "x.py::a", "x.py::b")
        c = load_identity_constraints(repo)
        assert ("x.py::a", "x.py::b") in c.force_link and not c.never_link


# -- api projection --------------------------------------------------------------------------------

def test_rewrite_view_reports_pending_drafts_and_staged_candidate(tmp_path):
    from sgt.api import rewrite_view

    repo = tmp_path / "repo"
    gb, _ = init_store(repo)
    (repo / "a.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    gb.commit_all("add foo")
    ideal = get(repo)
    ops = Store(repo).all_ops()
    original = next(o for o in ops if "a.py::foo" in o.footprint)

    draft = rewrite.split_op(repo, original.id)
    view = rewrite_view(repo)
    assert view["staged"] is None
    assert len(view["drafts"]) == 1
    assert view["drafts"][0]["draft_id"] == draft.draft_id
    assert view["drafts"][0]["hollow_ops"][0]["symbol"] == "a.py::foo"

    (repo / "a.py").write_text("def foo():\n    return 2\n", encoding="utf-8")
    rewrite.fulfill(repo, draft.draft_id, from_tree=True)
    view = rewrite_view(repo)
    assert view["drafts"] == []
    assert view["staged"]["verb"] == "split-op"
    assert view["staged"]["oracle_status"] == "pending"


# -- U6: staged-remedy coherence ----------------------------------------------------------------

def _stage_a_merge_op(tmp_path):
    """The existing merge-op stage setup: a diverged_chain fork, checked out on main, drafted and
    fulfilled from the working tree so `slugify.py` is a live staged (uncommitted) candidate."""
    repo = corpus.CORPUS["diverged_chain"].build(tmp_path / "repo")
    corpus.checkout(repo, "release")
    release_ideal = get(repo)
    corpus.checkout(repo, "main")
    main_ideal = get(repo)
    ops = Store(repo).all_ops()
    main_tip = main_ideal.frontier(ops)["slugify.py::slugify"]
    release_tip = release_ideal.frontier(ops)["slugify.py::slugify"]
    draft = rewrite.merge_op(repo, main_tip, release_tip)
    (repo / "slugify.py").write_text(
        "def slugify(s):\n    return s.lower().strip().replace(' ', '-')\n", encoding="utf-8"
    )
    rewrite.fulfill(repo, draft.draft_id, from_tree=True)
    return repo


def test_unstage_abandons_the_stage_and_restores_the_committed_ideal(tmp_path):
    """Abandon after stage (U6): `unstage` drops `staged.json` and rematerializes the committed
    ideal, so the deliberately-dirty staged tree is fully reverted and a materializing edit (which
    `put` refuses while staged) works again."""
    from sgt.core import lens

    repo = _stage_a_merge_op(tmp_path)
    committed = lens.current_ideal(repo)
    assert rewrite.staged_candidate(repo) is not None

    # While staged, any materializing edit refuses rather than committing a mixture (the lens guard).
    try:
        lens.put(repo, committed)
        assert False, "expected `put` to refuse while a candidate is staged"
    except lens.DirtyWorkingTreeError as e:
        assert "staged" in str(e)

    restored = rewrite.unstage(repo)
    assert rewrite.staged_candidate(repo) is None
    assert restored.op_ids == committed.op_ids
    # The tree is back to the committed ideal, byte for byte...
    materialized = code(committed, Store(repo).all_ops())
    assert (repo / "slugify.py").read_bytes() == materialized["slugify.py"]
    # ...and `put` (the `switch`/`save` path) is unblocked again.
    assert lens.put(repo, committed)
    # Abandoning again is a clean refusal, not a crash.
    try:
        rewrite.unstage(repo)
        assert False, "expected a refusal when nothing is staged"
    except rewrite.RewriteError as e:
        assert "nothing staged" in str(e)


def test_land_refuses_a_stale_stage_and_unstage_still_recovers(tmp_path):
    """Edit after stage, then land (U6): a working-tree edit after `fulfill` makes the stage stale,
    so `land` refuses (rather than committing a mixture of the reviewed candidate and the drift);
    the abandon path still recovers cleanly."""
    repo = _stage_a_merge_op(tmp_path)

    # Drift the staged candidate out from under the stage.
    (repo / "slugify.py").write_text(
        "def slugify(s):\n    return 'TAMPERED'\n", encoding="utf-8"
    )
    try:
        rewrite.land(repo, override=("pass", "reviewed", "rev"))
        assert False, "expected a staleness refusal"
    except rewrite.RewriteError as e:
        assert "stale" in str(e) and "slugify.py" in str(e)

    # The abandon remedy still works after a stale edit.
    rewrite.unstage(repo)
    assert rewrite.staged_candidate(repo) is None


def test_fsck_tree_classifies_a_staged_candidate_as_staged_not_drift(tmp_path):
    """`fsck --tree` during staged state (U6, ties to U2): the divergent staged path is classified
    `staged`, never `drift` -- an in-progress rewrite candidate is planned divergence."""
    from sgt.core.lens import fsck_tree

    repo = _stage_a_merge_op(tmp_path)
    result = fsck_tree(repo)
    assert "slugify.py" in result["staged"]
    assert result["drift"] == []


# -- U6: the review's end-to-end reproduction (two-clone sync fork) ------------------------------

def _init_bare(root):
    import subprocess

    remote = root / "remote.git"
    remote.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)],
                   check=True, capture_output=True)
    return remote


def _clone(remote, dest):
    import subprocess

    from sgt.store.gitbind import GitBinding
    subprocess.run(["git", "clone", "-q", str(remote), str(dest)], check=True, capture_output=True)
    GitBinding(dest).init()
    return dest


def _edit_and_commit(repo, path, content, message):
    from sgt.core import lens
    from sgt.store.gitbind import GitBinding
    (repo / path).write_text(content, encoding="utf-8")
    GitBinding(repo).commit_all(message)
    ideal = lens.get(repo)
    put_sha = lens.put(repo, ideal, message=f"sgt: mine {message}")
    lens.record_ideal(repo, ideal, put_sha)


def test_sync_fork_remedy_from_forks_json_lands_end_to_end_and_closes_the_fork(tmp_path):
    """Verification (U6): the advertised `sgt merge-op` remedy string in committed `forks.json`
    executes end-to-end on a two-clone fixture. A sync parks the forked symbol at the common
    ancestor (neither tip in the ideal) and records the fork; running that remedy -> `fulfill` ->
    `land` reconciles it, and the fork record closes."""
    import subprocess

    from sgt.core import lens, sync
    from sgt.store.gitbind import GitBinding
    from sgt import state

    base = "def foo():\n    return 1\n\n\ndef bar():\n    return 2\n"
    remote = _init_bare(tmp_path)
    a = _clone(remote, tmp_path / "a")
    lens.init(a)
    _edit_and_commit(a, "main.py", base, "init")
    subprocess.run(["git", "-C", str(a), "push", "-q", "origin", "main"],
                   check=True, capture_output=True)
    b = _clone(remote, tmp_path / "b")
    lens.get(b)

    _edit_and_commit(a, "main.py", "def foo():\n    return 999\n\n\ndef bar():\n    return 2\n", "A")
    subprocess.run(["git", "-C", str(a), "push", "-q", "origin", "main"],
                   check=True, capture_output=True)
    _edit_and_commit(b, "main.py", "def foo():\n    return 42\n\n\ndef bar():\n    return 2\n", "B")

    report = sync.sync(b, remote="origin", branch="main")
    assert len(report.forks) == 1
    records = state.load_json(b, "forks", default=[])
    assert len(records) == 1 and records[0]["symbol"] == "main.py::foo"
    # The remedy string names the two tips; run exactly that (`sgt merge-op <tip_a> <tip_b>`).
    tip_a, tip_b = records[0]["tips"]
    assert records[0]["remedy"] == f"sgt merge-op {tip_a[:8]} {tip_b[:8]}"

    draft = rewrite.merge_op(b, tip_a, tip_b)
    assert draft.ok
    (b / "main.py").write_text(
        "def foo():\n    return 1041\n\n\ndef bar():\n    return 2\n", encoding="utf-8"
    )
    candidate = rewrite.fulfill(b, draft.draft_id, from_tree=True)
    assert is_valid_ideal(Store(b).all_ops(), candidate.op_ids)

    sha = rewrite.land(b, override=("pass", "both diffs reconciled by hand", "rev"))
    assert sha

    # The fork record closes, the reconciliation is live and committed, the tree is clean.
    assert state.load_json(b, "forks", default=[]) == []
    assert rewrite.staged_candidate(b) is None
    assert GitBinding(b).is_clean()
    after = lens.get(b)
    assert code(after, Store(b).all_ops())["main.py"] == (
        b"def foo():\n    return 1041\n\n\ndef bar():\n    return 2\n"
    )
