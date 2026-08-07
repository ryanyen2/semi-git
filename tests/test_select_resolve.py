"""The shared selection ladder (`sgt.select.resolve`).

The property worth pinning is not that each rung works in isolation -- it is that `identify` and the
`revert` ladder in `cli/ideal_edit.py` *agree*. A user copies an id out of one view and types it into
another verb; if `sgt show <x>` reads `<x>` as a feature while `sgt revert <x>` acts on the single op
whose hex the handle shadows, the user destroys something they never named. The extraction exists to
make that disagreement structurally impossible, so these tests check the shape predicates the two
share and the precedence they both follow.
"""

from __future__ import annotations

from sgt.core.lens import get
from sgt.lens import map as lensmap
from sgt.select import resolve as sel
from tests.laws import corpus


def _repo(tmp_path):
    repo = corpus.CORPUS["linear_history"].build(tmp_path / "repo")
    get(repo)
    lensmap.build_map(repo)
    return repo


# -- shape predicates (the part revert/restore now share verbatim) ------------------------------


def test_checkpoint_shape_requires_a_digit_index_or_a_slug():
    assert sel.is_checkpoint_shaped("f-ab12@3")
    assert sel.is_checkpoint_shaped("f-ab12:add-retry")
    assert sel.is_checkpoint_shaped("auth work:tidy")
    # `@` followed by a non-digit is not an index; with no `:` either, this is not a checkpoint.
    assert not sel.is_checkpoint_shaped("f-ab12@tip")
    assert not sel.is_checkpoint_shaped("f-ab12")
    # A `file::name` symbol carries colons but is never a checkpoint spec.
    assert not sel.is_checkpoint_shaped("a.py::foo")
    assert not sel.is_checkpoint_shaped("pkg/mod.py::Cls.method")


def test_handle_shape_excludes_symbols_and_labels():
    assert sel.is_handle_shaped("f-ab12cd")
    assert sel.is_handle_shaped("ab12cd")            # the bare-hex copy token the graph prints
    assert sel.is_handle_shaped("a" * 64)            # a full op id is all-hex too
    assert not sel.is_handle_shaped("ab")            # under the 3-char floor
    assert not sel.is_handle_shaped("a.py::foo")
    assert not sel.is_handle_shaped("auth refactor")
    assert not sel.is_handle_shaped("f-ab12@3")


# -- the ladder --------------------------------------------------------------------------------


def test_unresolvable_token_returns_none_rather_than_guessing(tmp_path):
    """`None` is the signal that lets `revert` fall through to its NL rungs and lets `show` explain
    and stop. A wrong-but-plausible guess here would be far worse than no answer."""
    repo = _repo(tmp_path)
    assert sel.identify(repo, "definitely-not-a-thing") is None
    assert sel.identify(repo, "") is None
    assert sel.identify(repo, "ffffffffffff") is None  # handle-shaped but names nothing


def _frontier(repo):
    from sgt.core import opindex, order
    from sgt.core.lens import current_ideal

    return order.frontier(current_ideal(repo).op_ids, opindex.index_ops(repo))


def test_symbol_resolves_to_its_frontier_tip_op(tmp_path):
    repo = _repo(tmp_path)
    frontier = _frontier(repo)
    # A real authored symbol, not one of the `__residue__`/`__anchor__` bookkeeping sentinels.
    symbol = next(s for s in sorted(frontier)
                  if "::" in s and "__residue__" not in s and "__anchor__" not in s)

    found = sel.identify(repo, symbol)
    assert found is not None
    assert found.kind == "symbol"
    assert found.op_ids == frozenset({frontier[symbol]})
    assert not found.is_group  # one edit, not a set the user thinks of as one thing


def test_a_whole_file_path_resolves_as_a_symbol(tmp_path):
    """A non-code file is tracked as one whole-file symbol, so its frontier key is a bare path with
    no `::`. `sgt revert README.md` is typed straight off a log line; before the `resolve_target`
    whole-file rung it matched neither symbol nor op-id prefix and fell through to the LLM."""
    repo = _repo(tmp_path)
    frontier = _frontier(repo)
    path = next(s for s in sorted(frontier) if "::" not in s)

    found = sel.identify(repo, path)
    assert found is not None, f"{path!r} is a live frontier key but did not resolve"
    assert found.op_ids == frozenset({frontier[path]})
    # It is a symbol, not an "op" -- `show` must name back the thing the user typed.
    assert found.kind == "symbol"


def test_exact_op_id_resolves_to_that_one_op(tmp_path):
    """A full op id is handle-shaped, so it goes down the feature rung first. For an op that is *not*
    some feature's founding op, no feature claims it and the op rung must still catch it."""
    repo = _repo(tmp_path)
    result = lensmap.build_map(repo)
    founding = {nid[2:] for nid in result["nodes"] if nid.startswith("f-")}
    op_id = next(o for o in sorted(result["op_leaf"]) if o not in founding)

    found = sel.identify(repo, op_id)
    assert found is not None
    assert found.kind == "op"
    assert found.op_ids == frozenset({op_id})


def test_a_founding_op_id_reads_as_its_feature_not_as_one_op(tmp_path):
    """A feature's id is literally `f-` + its founding op's id, so that op's full hex names both. The
    documented precedence is that the feature scope wins -- the handle the log prints means the whole
    feature. Pinned because it is the one case where the *same* string has two honest readings, and
    silently picking the narrow one would make `revert <handle>` drop far less than the user meant."""
    repo = _repo(tmp_path)
    result = lensmap.build_map(repo)
    fid = next(nid for nid in result["nodes"] if nid.startswith("f-"))
    founding_op = fid[2:]

    found = sel.identify(repo, founding_op)
    assert found is not None
    assert found.kind == "feature"
    assert found.feature_id == fid
    assert len(found.op_ids) >= 1


def test_feature_handle_resolves_to_the_whole_feature_not_the_op_it_shadows(tmp_path):
    """The precedence that matters most: the log prints a feature's handle as its founding op's hex,
    so the *same string* is both. The feature reading must win -- for `identify` and for `revert`."""
    repo = _repo(tmp_path)
    result = lensmap.build_map(repo)
    fid = next(iter(result["nodes"]))
    expected = frozenset(op for op, leaf in result["op_leaf"].items() if leaf == fid)

    found = sel.identify(repo, fid)
    assert found is not None
    assert found.kind == "feature"
    assert found.feature_id == fid
    assert found.op_ids == expected
    assert found.is_group


def test_identify_matches_the_revert_ladders_reading_of_the_same_token(tmp_path):
    """The agreement test. `_kernel_edit_verb` routes a handle-shaped token to the feature plan iff
    `resolve_feature` claims it; `identify` must classify that same token as a feature."""
    repo = _repo(tmp_path)
    result = lensmap.build_map(repo)
    fid = next(iter(result["nodes"]))

    from sgt.lens.verbs import resolve_feature

    ladder_sees_a_feature = resolve_feature(repo, fid) is not None
    found = sel.identify(repo, fid)
    assert ladder_sees_a_feature == (found is not None and found.kind == "feature")
