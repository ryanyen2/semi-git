"""Tests for sgt.core.fold -- code(I) materialization (plan U5, R3/R6/R7)."""

from __future__ import annotations

from sgt.core.fold import code
from sgt.core.ideal import Ideal
from sgt.core.mine import _ANCHOR_FIRST, mine
from sgt.core.op import BOTTOM, make_op
from tests.laws import corpus


def _entity_op(path, name, body: bytes, before=None, requires=frozenset()):
    sym = f"{path}::{name}"
    return make_op({sym: (before, body.hex())}, {sym: body}, requires=requires)


def _remove_op(path, name, before_version):
    sym = f"{path}::{name}"
    return make_op({sym: (before_version, BOTTOM)}, {sym: None})


def _anchor_op(path, name, predecessor=None):
    marker = (predecessor or _ANCHOR_FIRST).encode()
    sym = f"{path}::__anchor__::{name}"
    return make_op({sym: (None, marker.hex())}, {sym: marker})


def _residue_op(path, text: bytes, before=None):
    sym = f"{path}::__residue__"
    return make_op({sym: (before, text.hex())}, {sym: text})


def _whole_file_op(path, content: bytes, before=None):
    return make_op({path: (before, content.hex())}, {path: content})


def _whole_file_removed(path, before_version):
    return make_op({path: (before_version, BOTTOM)}, {path: None})


def test_untouched_entity_byte_identical_including_comments_and_odd_formatting():
    body = b"def foo():\n    return 1   # weird   spacing , comment"
    op = _entity_op("a.py", "foo", body)
    anchor = _anchor_op("a.py", "foo")
    ops = [op, anchor]
    ideal = Ideal.from_ops({op.id, anchor.id}, ops)
    assert code(ideal, ops)["a.py"] == body + b"\n"


def test_two_features_at_different_anchors_materialize_alone_and_in_union():
    bar = _entity_op("a.py", "bar", b"def bar():\n    return 1")
    bar_anchor = _anchor_op("a.py", "bar")
    foo = _entity_op("a.py", "foo", b"def foo():\n    return 2")
    foo_anchor = _anchor_op("a.py", "foo", predecessor="bar")
    baz = _entity_op("a.py", "baz", b"def baz():\n    return 3")
    baz_anchor = _anchor_op("a.py", "baz", predecessor="bar")
    all_ops = [bar, bar_anchor, foo, foo_anchor, baz, baz_anchor]

    feature_a = Ideal.from_ops({bar.id, bar_anchor.id, foo.id, foo_anchor.id}, all_ops)
    feature_b = Ideal.from_ops({bar.id, bar_anchor.id, baz.id, baz_anchor.id}, all_ops)
    union = Ideal.from_ops(feature_a.op_ids | feature_b.op_ids, all_ops)

    assert code(feature_a, all_ops)["a.py"] == b"def bar():\n    return 1\n\n\ndef foo():\n    return 2\n"
    assert code(feature_b, all_ops)["a.py"] == b"def bar():\n    return 1\n\n\ndef baz():\n    return 3\n"
    # baz and foo are both anchored directly after bar -- a genuine tie, broken alphabetically
    # so the fold stays deterministic without needing the two features to coordinate.
    assert code(union, all_ops)["a.py"] == (
        b"def bar():\n    return 1\n\n\ndef baz():\n    return 3\n\n\ndef foo():\n    return 2\n"
    )


def test_reverting_a_feature_removes_its_derived_import():
    helper = _entity_op("b.py", "helper", b"def helper():\n    return 1")
    keeper = _entity_op("a.py", "keeper", b"def keeper():\n    return 0")
    keeper_anchor = _anchor_op("a.py", "keeper")
    helper_version = helper.footprint["b.py::helper"][1]
    user = _entity_op(
        "a.py", "user", b"def user():\n    return helper()",
        requires=frozenset({("b.py::helper", helper_version)}),
    )
    user_anchor = _anchor_op("a.py", "user", predecessor="keeper")
    all_ops = [helper, keeper, keeper_anchor, user, user_anchor]

    with_user = Ideal.from_ops({op.id for op in all_ops}, all_ops)
    assert b"from b import helper" in code(with_user, all_ops)["a.py"]

    without_user = Ideal.from_ops({helper.id, keeper.id, keeper_anchor.id}, all_ops)
    materialized = code(without_user, all_ops)["a.py"]
    assert b"from b import helper" not in materialized
    assert b"def keeper" in materialized


def test_future_import_ordering_preserved_in_residue():
    residue_text = b"from __future__ import annotations\n\nX = 1"
    residue = _residue_op("a.py", residue_text)
    out = code(Ideal.from_ops({residue.id}, [residue]), [residue])
    assert out["a.py"] == residue_text + b"\n"
    assert out["a.py"].index(b"from __future__") < out["a.py"].index(b"X = 1")


def test_module_level_constant_residue_materializes():
    residue = _residue_op("consts.py", b"MAX = 100")
    ideal = Ideal.from_ops({residue.id}, [residue])
    assert code(ideal, [residue])["consts.py"] == b"MAX = 100\n"


def test_whole_file_only_chain_materializes_exact_bytes():
    content = b"setting: value\nother: 2\n"
    op = _whole_file_op("config.yaml", content)
    ideal = Ideal.from_ops({op.id}, [op])
    assert code(ideal, [op])["config.yaml"] == content


def test_empty_file_materializes_as_empty_bytes():
    op = _whole_file_op("empty.txt", b"")
    ideal = Ideal.from_ops({op.id}, [op])
    assert code(ideal, [op])["empty.txt"] == b""


def test_deleted_file_is_absent_from_output():
    add_op = _whole_file_op("gone.txt", b"content")
    remove_op = _whole_file_removed("gone.txt", add_op.footprint["gone.txt"][1])
    ideal = Ideal.from_ops({add_op.id, remove_op.id}, [add_op, remove_op])
    assert "gone.txt" not in code(ideal, [add_op, remove_op])


def test_removed_entity_vanishes_but_sibling_survives():
    foo = _entity_op("a.py", "foo", b"def foo():\n    return 1")
    foo_anchor = _anchor_op("a.py", "foo")
    bar = _entity_op("a.py", "bar", b"def bar():\n    return 2", requires=frozenset())
    bar_anchor = _anchor_op("a.py", "bar", predecessor="foo")
    bar_removed = _remove_op("a.py", "bar", bar.footprint["a.py::bar"][1])
    all_ops = [foo, foo_anchor, bar, bar_anchor, bar_removed]

    ideal = Ideal.from_ops({op.id for op in all_ops}, all_ops)
    assert code(ideal, all_ops)["a.py"] == b"def foo():\n    return 1\n"


def test_get_put_roundtrip_on_real_mined_corpus(tmp_path):
    """Verification (U5): the get-put law at entity granularity, exercised through the real
    mining pipeline (ahead of U6's sgt.core.lens, which will run this as the actual law)."""
    repo = corpus.CORPUS["linear_history"].build(tmp_path / "repo")
    ops = mine(repo)
    ideal = Ideal.from_ops({op.id for op in ops}, ops)
    materialized = code(ideal, ops)
    for path in corpus.tracked_paths(repo):
        assert materialized.get(path) == (repo / path).read_bytes(), f"{path} mismatch"
