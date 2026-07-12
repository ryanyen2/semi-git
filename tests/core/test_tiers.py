"""Tests for sgt.core.tiers -- the three-tier file boundary (plan U27, D4): entity/opaque/
ignored resolution, LAW-0 mining determinism, the `derived` flag, and the two named safety
guards (opaque->entity promotion is a content no-op; tier->ignored refuses on live coverage)."""

from __future__ import annotations

from sgt import state
from sgt.core.fold import code
from sgt.core.lens import get
from sgt.core.mine import mine
from sgt.core.store import Store
from sgt.store.gitbind import init_store


def _ops_for_commit(ops, sha):
    return [op for op in ops if sha in op.provenance]


def _write_tiers_json(repo, overrides: dict) -> None:
    state.save_json(repo, "tiers", overrides)


def test_lockfile_edits_collapse_under_derived_flag(tmp_path):
    """A lockfile mines as whole-file ops (no grammar for `.json` at all) and every touch is
    flagged `derived` (S4) regardless of tier -- review surfaces can fold it away."""
    gb, _ = init_store(tmp_path)
    (tmp_path / "package-lock.json").write_text('{"v": 1}\n', encoding="utf-8")
    sha1 = gb.commit_all("add lockfile")

    (tmp_path / "package-lock.json").write_text('{"v": 2}\n', encoding="utf-8")
    sha2 = gb.commit_all("bump lockfile")

    ops = mine(tmp_path)

    for sha in (sha1, sha2):
        touch = _ops_for_commit(ops, sha)
        assert len(touch) == 1
        op = touch[0]
        assert list(op.footprint) == ["package-lock.json"]
        assert op.derived is True


def test_entity_override_on_ungrammared_path_is_a_content_noop(tmp_path):
    """Promoting a non-parseable path to `entity` before this kernel has a grammar for it must
    not re-mint history (D4's first safety guard): the path silently degrades to `opaque`, so
    earlier commits' ops are unaffected and the chain continues linking before/after versions
    across the override boundary exactly as an ordinary opaque edit would."""
    gb, _ = init_store(tmp_path)
    (tmp_path / "config.yaml").write_text("setting: original\n", encoding="utf-8")
    sha1 = gb.commit_all("add config")

    ops_before_override = mine(tmp_path)
    op1 = _ops_for_commit(ops_before_override, sha1)[0]

    _write_tiers_json(tmp_path, {"entity": ["config.yaml"]})
    (tmp_path / "config.yaml").write_text("setting: changed\n", encoding="utf-8")
    sha2 = gb.commit_all("promote config.yaml to entity (still no grammar) + edit it")

    ops_after = mine(tmp_path)

    # the pre-override commit's op is byte-identical -- promoting later never re-mints it
    op1_after = _ops_for_commit(ops_after, sha1)[0]
    assert op1_after.id == op1.id

    # the new edit is still a single whole-file op, chained from the prior version
    touch2 = _ops_for_commit(ops_after, sha2)
    assert len(touch2) == 1
    op2 = touch2[0]
    assert list(op2.footprint) == ["config.yaml"]
    before_version, _after_version = op2.footprint["config.yaml"]
    _, prior_after_version = op1.footprint["config.yaml"]
    assert before_version == prior_after_version


def test_ignoring_a_live_path_refuses_and_names_revert(tmp_path):
    """D4's second safety guard: `sgt tiers set` refuses to mark a currently-covered path
    `ignored` -- that would silently stop tracking live content -- and names `sgt revert` as
    the remedy. Once the path's ops are reverted out of the ideal, the same call succeeds."""
    from sgt.cli.tiers import _tiers_set
    from sgt.core import verbs
    from sgt.core.lens import get as get_ideal
    from sgt.core.op import is_content_bearing

    gb, _ = init_store(tmp_path)
    (tmp_path / "a.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    gb.commit_all("add foo")

    get(tmp_path)  # mine-on-contact, so the ideal covers a.py

    code_before = _tiers_set(str(tmp_path), "a.py", "ignored", as_json=True)
    assert code_before == 1

    # a single-function file mines an entity op *and* residue ops (the head/tail gaps around it)
    # -- all of them content-bearing and covering "a.py", so every one has to be reverted, not
    # just the entity op, before the path is no longer live. Re-check the ideal after each revert:
    # reverting one op's upset may also remove a sibling from the ideal.
    def _live_a_py_ops():
        ideal = get_ideal(tmp_path)
        ops = Store(tmp_path).all_ops()
        return [
            op for op in ops if op.id in ideal.op_ids
            and any(sym == "a.py" or sym.startswith("a.py::") for sym in op.footprint)
            and any(is_content_bearing(sym) for sym in op.footprint)
        ]

    remaining = _live_a_py_ops()
    while remaining:
        preview = verbs.plan_revert(tmp_path, remaining[0].id)
        assert preview.ok
        verbs.apply(tmp_path, preview)
        remaining = _live_a_py_ops()

    code_after = _tiers_set(str(tmp_path), "a.py", "ignored", as_json=True)
    assert code_after == 0


def test_divergent_working_tier_maps_mine_to_byte_identical_ops(tmp_path):
    """LAW-0: tier resolution reads `.sgt/tiers.json`/`.sgtignore` from the mined commit's own
    tree, never the working tree -- so two replicas with divergent *uncommitted* tier maps
    re-mine identical history to byte-identical ops."""
    gb, _ = init_store(tmp_path)
    (tmp_path / "a.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    (tmp_path / "config.yaml").write_text("setting: value\n", encoding="utf-8")
    gb.commit_all("add foo and config")

    ops_a = mine(tmp_path)

    # An uncommitted, divergent working-tree override -- never committed, so it must have zero
    # effect on mining.
    _write_tiers_json(tmp_path, {"ignored": ["a.py"]})
    (tmp_path / ".sgtignore").write_text("config.yaml\n", encoding="utf-8")

    ops_b = mine(tmp_path)

    assert {op.id for op in ops_a} == {op.id for op in ops_b}


def test_demoted_path_post_demotion_edit_materializes_as_whole_file(tmp_path):
    """A path demoted entity->opaque via `.sgt/tiers.json`: its old entity chain is frozen at
    its tip (no further touches ever emitted for it) but contributes zero bytes once a
    whole-file symbol for the same path also exists at the frontier -- `code()` prefers the
    whole-file image outright, never concatenating the two (the resolved 'two live
    representations' design tension)."""
    gb, _ = init_store(tmp_path)
    (tmp_path / "a.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    gb.commit_all("add foo")

    ops_before = mine(tmp_path)
    entity_op = next(op for op in ops_before if "a.py::foo" in op.footprint)

    _write_tiers_json(tmp_path, {"opaque": ["a.py"]})
    (tmp_path / "a.py").write_text("def foo():\n    return 1\n\n\ndef bar():\n    return 2\n",
                                    encoding="utf-8")
    gb.commit_all("demote a.py to opaque + edit it")

    ideal = get(tmp_path)
    ops = Store(tmp_path).all_ops()

    # the old entity op is still in the store (and in the ideal) -- frozen, not deleted
    assert any(op.id == entity_op.id for op in ops)
    assert entity_op.id in ideal.op_ids

    materialized = code(ideal, ops)
    assert materialized["a.py"] == (tmp_path / "a.py").read_bytes()
