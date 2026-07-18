"""Tests for sgt.intent.group -- the intent overlay's deterministic rung 0/1 partition (U1) and
dependency-graph-backed cross-feature tiering (U2). No LLM/network involved anywhere here; every
assertion is about pure, rebuildable derivation from the mined store + git history + a hand-authored
feature tree (same idiom as `tests/lens/test_select.py`: real mining for genuine ops/edges, feature
membership authored directly since clustering quality isn't what's under test)."""

from __future__ import annotations

from sgt.core.lens import get
from sgt.core.store import Store
from sgt.intent import group
from sgt.lens import tree
from sgt.store.gitbind import init_store


def _leaf(members: list[str], label: str) -> dict:
    return {"parent": None, "children": [], "members": sorted(members), "size": len(members),
            "dir": "", "label": label}


def _save_tree(repo, leaves: dict[str, list[str]]) -> None:
    nodes = {fid: _leaf(members, fid) for fid, members in leaves.items()}
    ops = Store(repo).all_ops()
    result = {
        "nodes": nodes, "roots": sorted(nodes), "op_leaf": tree.assign_ops_to_leaves(nodes, ops),
        "max_depth": 0, "cannot_link_moves": [], "identity_events": [],
    }
    tree.save(repo, result)
    return result["op_leaf"]


def _op_for(repo, symbol: str):
    ops = Store(repo).all_ops()
    return next(op for op in ops if symbol in op.footprint)


# -- U1: atoms / scope_bundles ---------------------------------------------------------------------


def test_ops_from_one_commit_form_one_atom(tmp_path):
    gb, _ = init_store(tmp_path)
    (tmp_path / "a.py").write_text(
        "def foo():\n    return 1\n\n\ndef bar():\n    return 2\n", encoding="utf-8",
    )
    gb.commit_all("feat(store): add foo and bar")
    get(tmp_path)

    foo_op, bar_op = _op_for(tmp_path, "a.py::foo"), _op_for(tmp_path, "a.py::bar")
    atoms = group.atoms(tmp_path)
    all_op_ids = frozenset(op.id for op in Store(tmp_path).all_ops())

    assert len(atoms) == 1
    assert {foo_op.id, bar_op.id} <= atoms[0].op_ids
    assert atoms[0].op_ids == all_op_ids  # one commit -> every mined op (incl. anchors/residue)
    assert atoms[0].scope == "store"


def test_two_commits_under_same_scope_form_one_bundle(tmp_path):
    """A same-scope pair with a real reference edge between them (bar calls foo) merges into one
    bundle -- structural gating (U3) preserves the legitimate case."""
    gb, _ = init_store(tmp_path)
    (tmp_path / "a.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    gb.commit_all("feat(store): add foo")
    (tmp_path / "b.py").write_text(
        "from a import foo\n\n\ndef bar():\n    return foo() + 1\n", encoding="utf-8",
    )
    gb.commit_all("fix(store): add bar")
    get(tmp_path)

    atoms = group.atoms(tmp_path)
    assert len(atoms) == 2
    ops = Store(tmp_path).all_ops()
    bundles = group.scope_bundles(atoms, ops)
    store_bundles = [b for b in bundles if b.scope == "store"]
    assert len(store_bundles) == 1
    assert len(store_bundles[0].atoms) == 2


def test_two_commits_under_same_scope_with_no_structural_edge_split_into_two_bundles(tmp_path):
    """The review's exact repro: an unrelated pair of commits coincidentally shares a scope
    string (`store`) but touches disjoint, unconnected symbols -- structural gating (U3) refuses
    to merge them into one revertable unit."""
    gb, _ = init_store(tmp_path)
    (tmp_path / "a.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    gb.commit_all("feat(store): add foo")
    (tmp_path / "b.py").write_text("def bar():\n    return 2\n", encoding="utf-8")
    gb.commit_all("fix(store): add bar")
    get(tmp_path)

    atoms = group.atoms(tmp_path)
    assert len(atoms) == 2
    ops = Store(tmp_path).all_ops()
    bundles = group.scope_bundles(atoms, ops)
    store_bundles = [b for b in bundles if b.scope == "store"]
    assert len(store_bundles) == 2
    assert {len(b.atoms) for b in store_bundles} == {1}


def test_three_same_scope_atoms_two_connected_one_isolated(tmp_path):
    gb, _ = init_store(tmp_path)
    (tmp_path / "a.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    gb.commit_all("feat(store): add foo")
    (tmp_path / "b.py").write_text(
        "from a import foo\n\n\ndef bar():\n    return foo() + 1\n", encoding="utf-8",
    )
    gb.commit_all("fix(store): add bar calling foo")
    (tmp_path / "c.py").write_text("def baz():\n    return 3\n", encoding="utf-8")
    gb.commit_all("fix(store): add baz")
    get(tmp_path)

    atoms = group.atoms(tmp_path)
    assert len(atoms) == 3
    ops = Store(tmp_path).all_ops()
    bundles = group.scope_bundles(atoms, ops)
    store_bundles = [b for b in bundles if b.scope == "store"]
    assert len(store_bundles) == 2
    assert sorted(len(b.atoms) for b in store_bundles) == [1, 2]


def test_op_keyed_on_earliest_witnessing_commit_in_history(tmp_path):
    """An op whose provenance names multiple shas (e.g. re-touched by a later commit that didn't
    change its content) is keyed on whichever sha is *earliest* in `history()`, not just the first
    one encountered -- mirrors `history_view`'s `commit_index` rule exactly. `Store.add` unions
    provenance on a same-id write (R8/D7), so re-adding the identical op with a second, later sha
    is the real-world way this happens."""
    from dataclasses import replace

    gb, _ = init_store(tmp_path)
    (tmp_path / "a.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    first_sha = gb.commit_all("add foo")
    get(tmp_path)

    foo_op = _op_for(tmp_path, "a.py::foo")
    later_sha = "0" * 40
    Store(tmp_path).add(replace(foo_op, provenance=(later_sha,)))

    atoms = group.atoms(tmp_path)
    matching = [a for a in atoms if foo_op.id in a.op_ids]
    assert len(matching) == 1
    assert matching[0].commit_sha == first_sha


def test_unwitnessed_op_lands_in_synthetic_bucket_never_dropped(tmp_path):
    """An op whose *only* provenance sha never appears in `history()` (e.g. mined from a detached
    or since-rewritten commit) still lands somewhere -- the `UNWITNESSED` bucket -- rather than
    being silently dropped from the partition."""
    from sgt.core.op import make_op

    gb, _ = init_store(tmp_path)
    (tmp_path / "a.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    gb.commit_all("add foo")
    get(tmp_path)

    detached_sha = "f" * 40
    synthetic = make_op(
        footprint={"z.py::ghost": (None, "v1")},
        images={"z.py::ghost": b"def ghost(): pass\n"},
        provenance=(detached_sha,),
    )
    Store(tmp_path).add(synthetic)

    atoms = group.atoms(tmp_path)
    unwitnessed = [a for a in atoms if a.commit_sha == group.UNWITNESSED]
    assert len(unwitnessed) == 1
    assert synthetic.id in unwitnessed[0].op_ids


def test_atoms_is_deterministic_across_repeated_calls(tmp_path):
    gb, _ = init_store(tmp_path)
    (tmp_path / "a.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    gb.commit_all("add foo")
    (tmp_path / "b.py").write_text("def bar():\n    return 2\n", encoding="utf-8")
    gb.commit_all("add bar")
    get(tmp_path)

    first = group.atoms(tmp_path)
    second = group.atoms(tmp_path)
    assert first == second


def test_empty_store_returns_empty_list(tmp_path):
    init_store(tmp_path)
    assert group.atoms(tmp_path) == []


# -- U2: feature_span / tier -----------------------------------------------------------------------


def test_coupled_when_requires_edge_crosses_feature_boundary(tmp_path):
    """`b.py::caller` (feature F-B) requires a version produced by `a.py::base` (feature F-A) --
    a real reference edge crossing the boundary -> `coupled`."""
    gb, _ = init_store(tmp_path)
    (tmp_path / "a.py").write_text("def base():\n    return 1\n", encoding="utf-8")
    gb.commit_all("feat(x): add a.py")
    (tmp_path / "b.py").write_text(
        "from a import base\n\n\ndef caller():\n    return base() + 1\n", encoding="utf-8",
    )
    gb.commit_all("feat(x): add b.py calling base")
    get(tmp_path)

    op_leaf = _save_tree(tmp_path, {"F-A": ["a.py::base"], "F-B": ["b.py::caller"]})
    ops = Store(tmp_path).all_ops()
    all_op_ids = frozenset(op.id for op in ops)

    result = group.tier(all_op_ids, frozenset({"c1", "c2"}), ops, frozenset(), op_leaf)
    assert result == group.COUPLED


def test_co_changed_same_commit_spans_features_no_dep_edge(tmp_path):
    gb, _ = init_store(tmp_path)
    (tmp_path / "a.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("def bar():\n    return 2\n", encoding="utf-8")
    gb.commit_all("chore: touch two unrelated files")
    get(tmp_path)

    op_leaf = _save_tree(tmp_path, {"F-A": ["a.py::foo"], "F-B": ["b.py::bar"]})
    ops = Store(tmp_path).all_ops()
    all_op_ids = frozenset(op.id for op in ops)

    result = group.tier(all_op_ids, frozenset({"c1"}), ops, frozenset(), op_leaf)
    assert result == group.CO_CHANGED


def test_thematic_multi_commit_scope_bundle_spans_features_no_dep_edge(tmp_path):
    gb, _ = init_store(tmp_path)
    (tmp_path / "a.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    gb.commit_all("fix(auth): add foo")
    (tmp_path / "b.py").write_text("def bar():\n    return 2\n", encoding="utf-8")
    gb.commit_all("fix(auth): add bar")
    get(tmp_path)

    op_leaf = _save_tree(tmp_path, {"F-A": ["a.py::foo"], "F-B": ["b.py::bar"]})
    ops = Store(tmp_path).all_ops()
    all_op_ids = frozenset(op.id for op in ops)

    result = group.tier(all_op_ids, frozenset({"c1", "c2"}), ops, frozenset(), op_leaf)
    assert result == group.THEMATIC


def test_feature_span_skips_ops_with_no_leaf(tmp_path):
    gb, _ = init_store(tmp_path)
    (tmp_path / "a.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    gb.commit_all("add foo")
    get(tmp_path)

    foo_op = _op_for(tmp_path, "a.py::foo")
    span = group.feature_span(frozenset({foo_op.id}), op_leaf={})
    assert span == set()


def test_tier_does_not_crash_on_a_group_missing_its_chain_head(tmp_path):
    """Regression: a scope-bundle or commit's op-set is not guaranteed to be a valid ideal --
    here `group_op_ids` holds a modify op for `a.py::foo` but not the earlier add op that
    produced the version it modifies. The old `downset_in`-based `tier()` raised `KeyError` on
    this shape (it assumes `group_op_ids` is downward-closed); `components_in` has no such
    precondition."""
    gb, _ = init_store(tmp_path)
    (tmp_path / "a.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    gb.commit_all("feat(x): add foo")
    (tmp_path / "a.py").write_text("def foo():\n    return 2\n", encoding="utf-8")
    gb.commit_all("feat(x): modify foo")
    (tmp_path / "b.py").write_text("def bar():\n    return 3\n", encoding="utf-8")
    gb.commit_all("feat(x): add bar")
    get(tmp_path)

    op_leaf = _save_tree(tmp_path, {"F-A": ["a.py::foo"], "F-B": ["b.py::bar"]})
    ops = Store(tmp_path).all_ops()
    modify_op = next(op for op in ops if "a.py::foo" in op.footprint and op.footprint["a.py::foo"][0] is not None)
    bar_op = _op_for(tmp_path, "b.py::bar")

    group_op_ids = frozenset({modify_op.id, bar_op.id})  # missing modify_op's chain head
    result = group.tier(group_op_ids, frozenset({"c1", "c2"}), ops, frozenset(), op_leaf)
    assert result in (group.CO_CHANGED, group.THEMATIC, group.COUPLED)  # must not raise


def test_tier_is_stable_across_repeated_computation(tmp_path):
    gb, _ = init_store(tmp_path)
    (tmp_path / "a.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("def bar():\n    return 2\n", encoding="utf-8")
    gb.commit_all("chore: touch two unrelated files")
    get(tmp_path)

    op_leaf = _save_tree(tmp_path, {"F-A": ["a.py::foo"], "F-B": ["b.py::bar"]})
    ops = Store(tmp_path).all_ops()
    all_op_ids = frozenset(op.id for op in ops)

    first = group.tier(all_op_ids, frozenset({"c1"}), ops, frozenset(), op_leaf)
    second = group.tier(all_op_ids, frozenset({"c1"}), ops, frozenset(), op_leaf)
    assert first == second


# -- U8: resolve_group / group_requires / apply_subset ----------------------------------------


def test_resolve_group_by_exact_theme_id(tmp_path):
    gb, _ = init_store(tmp_path)
    (tmp_path / "a.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    gb.commit_all("add foo")
    get(tmp_path)

    all_atoms = group.atoms(tmp_path)
    themes = {"theme-x": {"atom_shas": [all_atoms[0].commit_sha]}}

    resolved = group.resolve_group("theme-x", themes, all_atoms)
    assert resolved == ("theme", [all_atoms[0]])


def test_resolve_group_by_unique_commit_prefix(tmp_path):
    gb, _ = init_store(tmp_path)
    (tmp_path / "a.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    sha = gb.commit_all("add foo")
    get(tmp_path)

    all_atoms = group.atoms(tmp_path)
    resolved = group.resolve_group(sha[:10], {}, all_atoms)
    assert resolved == ("atom", [a for a in all_atoms if a.commit_sha == sha])


def test_resolve_group_unknown_target_returns_none(tmp_path):
    gb, _ = init_store(tmp_path)
    (tmp_path / "a.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    gb.commit_all("add foo")
    get(tmp_path)

    assert group.resolve_group("no-such-target", {}, group.atoms(tmp_path)) is None


def _two_commit_dependency_atoms(tmp_path):
    """base (commit 1) <- caller (commit 2), a real reference edge -- returns
    `(member_atoms, sha_base, sha_caller)`."""
    gb, _ = init_store(tmp_path)
    (tmp_path / "a.py").write_text("def base():\n    return 1\n", encoding="utf-8")
    sha_base = gb.commit_all("add a.py")
    (tmp_path / "b.py").write_text(
        "from a import base\n\n\ndef caller():\n    return base() + 1\n", encoding="utf-8",
    )
    sha_caller = gb.commit_all("add b.py calling base")
    get(tmp_path)
    return group.atoms(tmp_path), sha_base, sha_caller


def test_group_requires_reverting_a_prerequisite_names_its_dependent(tmp_path):
    all_atoms, sha_base, sha_caller = _two_commit_dependency_atoms(tmp_path)
    ops = Store(tmp_path).all_ops()

    requires = group.group_requires(all_atoms, ops, frozenset())

    assert requires[sha_base] == [sha_caller]  # reverting base alone would sweep caller away too
    assert requires[sha_caller] == []  # caller depends on nothing else in the group


def test_apply_subset_no_subset_returns_the_whole_group(tmp_path):
    all_atoms, sha_base, sha_caller = _two_commit_dependency_atoms(tmp_path)
    ops = Store(tmp_path).all_ops()
    requires = group.group_requires(all_atoms, ops, frozenset())

    chosen, err = group.apply_subset(all_atoms, requires, None)
    assert err is None
    assert chosen == all_atoms


def test_apply_subset_selecting_the_dependent_alone_is_fine(tmp_path):
    all_atoms, sha_base, sha_caller = _two_commit_dependency_atoms(tmp_path)
    ops = Store(tmp_path).all_ops()
    requires = group.group_requires(all_atoms, ops, frozenset())

    chosen, err = group.apply_subset(all_atoms, requires, [sha_caller[:10]])
    assert err is None
    assert [a.commit_sha for a in chosen] == [sha_caller]


def test_apply_subset_selecting_the_prerequisite_alone_is_refused_by_name(tmp_path):
    all_atoms, sha_base, sha_caller = _two_commit_dependency_atoms(tmp_path)
    ops = Store(tmp_path).all_ops()
    requires = group.group_requires(all_atoms, ops, frozenset())

    chosen, err = group.apply_subset(all_atoms, requires, [sha_base[:10]])
    assert chosen == []
    assert err is not None
    assert sha_caller[:8] in err


def test_apply_subset_ambiguous_or_unknown_prefix_is_refused(tmp_path):
    all_atoms, sha_base, sha_caller = _two_commit_dependency_atoms(tmp_path)
    ops = Store(tmp_path).all_ops()
    requires = group.group_requires(all_atoms, ops, frozenset())

    chosen, err = group.apply_subset(all_atoms, requires, ["deadbeef"])
    assert chosen == []
    assert err is not None
