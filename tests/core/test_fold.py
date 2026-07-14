"""Tests for sgt.core.fold -- code(I) materialization (plan U5, R3/R6/R7)."""

from __future__ import annotations

import pytest

from sgt.core.fold import code
from sgt.core.ideal import Ideal
from sgt.core.mine import _ANCHOR_FIRST, _RESIDUE_HEAD, mine
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


def _residue_op(path, anchor, text: bytes, before=None):
    sym = f"{path}::__residue__::{anchor}"
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
    # No residue op in this ideal at all -- the fold is pure verbatim splicing, so with no
    # trailing-gap segment there is no synthesized trailing newline either (a real mined ideal
    # always carries a trailing residue segment, even if empty; this fixture isolates the
    # entity's own span).
    assert code(ideal, ops)["a.py"] == body


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

    # No residue/gap ops in this fixture -- pure verbatim concatenation with zero synthesized
    # bytes between entities. Gap fidelity has its own dedicated coverage (test_residue_*
    # below); this test isolates anchor-fact ordering.
    assert code(feature_a, all_ops)["a.py"] == b"def bar():\n    return 1def foo():\n    return 2"
    assert code(feature_b, all_ops)["a.py"] == b"def bar():\n    return 1def baz():\n    return 3"
    # baz and foo are both anchored directly after bar -- a genuine tie, broken alphabetically
    # so the fold stays deterministic without needing the two features to coordinate.
    assert code(union, all_ops)["a.py"] == (
        b"def bar():\n    return 1def baz():\n    return 3def foo():\n    return 2"
    )


def test_import_lives_as_residue_and_survives_its_consumers_removal():
    """D3 (byte-fidelity fold, 2026-07-08): the fold is pure verbatim splicing -- it no longer
    derives or prunes an import block from `requires`. An import is just residue; reverting its
    only consumer leaves it exactly where it was, byte for byte (surfacing "this leaves an
    unused import" moves to the verb/preview layer, which still has the reference edges)."""
    head = _residue_op("a.py", _RESIDUE_HEAD, b"from b import helper\n\n\n")
    user = _entity_op("a.py", "user", b"def user():\n    return helper()")
    all_ops = [head, user]

    with_user = Ideal.from_ops({op.id for op in all_ops}, all_ops)
    assert code(with_user, all_ops)["a.py"] == b"from b import helper\n\n\ndef user():\n    return helper()"

    without_user = Ideal.from_ops({head.id}, all_ops)
    materialized = code(without_user, all_ops)["a.py"]
    assert materialized == b"from b import helper\n\n\n"  # import residue persists untouched


def test_future_import_ordering_preserved_in_residue():
    residue_text = b"from __future__ import annotations\n\nX = 1"
    residue = _residue_op("a.py", _RESIDUE_HEAD, residue_text)
    out = code(Ideal.from_ops({residue.id}, [residue]), [residue])
    assert out["a.py"] == residue_text
    assert out["a.py"].index(b"from __future__") < out["a.py"].index(b"X = 1")


def test_module_level_constant_residue_materializes():
    residue = _residue_op("consts.py", _RESIDUE_HEAD, b"MAX = 100")
    ideal = Ideal.from_ops({residue.id}, [residue])
    assert code(ideal, [residue])["consts.py"] == b"MAX = 100"


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
    assert code(ideal, all_ops)["a.py"] == b"def foo():\n    return 1"


@pytest.mark.parametrize("case_name", ["linear_history", *corpus.GENERAL_CODE_CASES])
def test_get_put_roundtrip_on_real_mined_corpus(tmp_path, case_name):
    """Verification (U5): the get-put law at entity granularity, exercised through the real
    mining pipeline (ahead of U6's sgt.core.lens, which will run this as the actual law).
    Parametrized over the general-code-robustness fixtures (2026-07-08) as well as the original
    mining-edge-case corpus."""
    repo = corpus.CORPUS[case_name].build(tmp_path / "repo")
    ops = mine(repo)
    ideal = Ideal.from_ops({op.id for op in ops}, ops)
    materialized = code(ideal, ops)
    for path in corpus.tracked_paths(repo):
        assert materialized.get(path) == (repo / path).read_bytes(), f"{path} mismatch"
