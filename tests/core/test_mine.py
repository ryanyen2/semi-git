"""Tests for sgt.core.mine -- the operation stream (plan U2, R1/R2/R7/R12/R22)."""

from __future__ import annotations

from sgt.core import order
from sgt.core.mine import mine
from sgt.store.gitbind import GitBinding, init_store
from tests.laws import corpus


def _ops_for_commit(ops, sha):
    return [op for op in ops if sha in op.provenance]


_BOOKKEEPING_MARKERS = ("::__residue__", "::__anchor__::")


def _entity_ops(ops):
    """Ops that touch real code entities, excluding anchor/residue file-level bookkeeping."""
    return [
        op for op in ops
        if not any(marker in sym for sym in op.footprint for marker in _BOOKKEEPING_MARKERS)
    ]


def test_tangled_commit_untangles_into_two_ops(tmp_path):
    """A commit touching two def-use-disjoint symbol groups (baz added to b.py, qux edited in
    c.py, no calls between them) yields two separate ops, never one tangled op (BET-A)."""
    repo = corpus.CORPUS["linear_history"].build(tmp_path / "repo")
    tangled_sha = corpus.commit_shas(repo)[4]  # "tangled: add baz ... and edit unrelated qux ..."

    ops = mine(repo)
    entity_ops = _entity_ops(_ops_for_commit(ops, tangled_sha))
    assert len(entity_ops) == 2, [op.footprint for op in entity_ops]
    touched = {sym for op in entity_ops for sym in op.footprint}
    assert touched == {"b.py::baz", "c.py::qux"}


def test_yaml_edit_yields_one_whole_file_op(tmp_path):
    """A non-parseable path (config.yaml) gets exactly one whole-file pseudo-symbol op (R7)."""
    repo = corpus.CORPUS["linear_history"].build(tmp_path / "repo")
    yaml_sha = corpus.commit_shas(repo)[6]  # "edit non-parseable config"

    ops = mine(repo)
    yaml_ops = _ops_for_commit(ops, yaml_sha)
    assert len(yaml_ops) == 1
    assert list(yaml_ops[0].footprint) == ["config.yaml"]
    assert yaml_ops[0].images["config.yaml"] == b"setting: changed\nextra: true\n"


def test_binary_change_yields_blob_oid_version(tmp_path):
    """A binary path's version is the git blob OID, not a re-derived content hash (R7)."""
    repo = corpus.CORPUS["linear_history"].build(tmp_path / "repo")
    first_sha = corpus.commit_shas(repo)[0]
    gb = GitBinding(repo)
    expected_oid = gb.blob_oid(first_sha, "logo.bin")
    assert expected_oid is not None

    ops = mine(repo)
    binary_ops = [op for op in _ops_for_commit(ops, first_sha) if "logo.bin" in op.footprint]
    assert len(binary_ops) == 1
    _before, after_version = binary_ops[0].footprint["logo.bin"]
    assert after_version == expected_oid


def test_unparseable_midedit_degrades_to_whole_file(tmp_path):
    """A Python file broken mid-edit degrades to a whole-file symbol for that commit -- not
    zero entities, not a crash (R7). The degraded op must also actually be *live*: its
    before_version has to chain onto a producer some prior op actually emitted (the entity
    tier's chain never minted `symbol == path`), or it's permanently ungrounded and silently
    dropped from every ideal despite appearing in `mine()`'s raw output."""
    gb, _ = init_store(tmp_path)
    (tmp_path / "a.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    gb.commit_all("add foo")
    (tmp_path / "a.py").write_text("def foo(: %%% broken >>> \n", encoding="utf-8")
    sha2 = gb.commit_all("break foo mid-edit")

    ops = mine(tmp_path)
    broken_ops = _ops_for_commit(ops, sha2)
    assert len(broken_ops) == 1
    assert list(broken_ops[0].footprint) == ["a.py"]

    from sgt.core.fold import code
    from sgt.core.lens import get
    from sgt.core.store import Store

    ideal = get(tmp_path)
    assert broken_ops[0].id in ideal.op_ids
    materialized = code(ideal, Store(tmp_path).all_ops())
    assert materialized["a.py"] == (tmp_path / "a.py").read_bytes()


def test_rename_with_reformat_links_as_one_move(tmp_path):
    """A rename plus a pure reformat (whitespace only, no logic change) still links as one
    move rather than delete+add. The renamed identifier is itself inside the hashed span, so
    neither content_hash nor structural_hash can catch a rename (both include the name) -- this
    goes through the fuzzy tier, which is exactly why the size/kind guards on that tier matter."""
    gb, _ = init_store(tmp_path)
    (tmp_path / "a.py").write_text(
        "def foo(nodes):\n    total = 0\n    for n in nodes:\n        total = total + n\n    return total\n",
        encoding="utf-8",
    )
    gb.commit_all("add foo")
    (tmp_path / "a.py").write_text(
        "def bar(nodes):\n    total  =  0\n\n    for n in nodes:\n        total = total + n\n    return total\n",
        encoding="utf-8",
    )
    sha2 = gb.commit_all("rename foo -> bar, reformat body")

    ops = mine(tmp_path)
    moved = [op for op in _ops_for_commit(ops, sha2) if op.kind == "move"]
    assert len(moved) == 1
    assert set(moved[0].footprint) == {"a.py::foo"}  # canonical id anchors to the older side


def test_cross_scope_move_is_delete_add_not_silent_weld(tmp_path):
    """A top-level function becoming a class method is a *kind* change (function -> method):
    even when body/structure match, this is delete + add (split provenance), never a single
    move op silently welding two different scopes together."""
    gb, _ = init_store(tmp_path)
    (tmp_path / "a.py").write_text(
        "def helper():\n    total = 0\n    for i in range(3):\n        total += i\n    return total\n",
        encoding="utf-8",
    )
    gb.commit_all("add top-level helper")
    (tmp_path / "a.py").write_text(
        "class Box:\n    def helper():\n        total = 0\n        for i in range(3):\n            total += i\n        return total\n",
        encoding="utf-8",
    )
    sha2 = gb.commit_all("move helper into Box as a method")

    ops = mine(tmp_path)
    reshape_ops = [
        op for op in _ops_for_commit(ops, sha2)
        if any("helper" in sym or sym == "a.py::Box" for sym in op.footprint)
    ]
    assert not any(op.kind == "move" for op in reshape_ops)
    assert any(op.kind == "prune" for op in reshape_ops)


def test_mining_is_repeatable_across_two_calls(tmp_path):
    """Mining the same history twice yields byte-identical Op payloads, not just ids."""
    repo = corpus.CORPUS["linear_history"].build(tmp_path / "repo")
    first = mine(repo)
    second = mine(repo)
    assert first == second


def test_rename_out_of_sgt_dir_stays_excluded_after_a_later_delete(tmp_path):
    """A path renamed *out of* `.sgt/` (e.g. a `.sgt/` -> `.sgt.bak/` migration, which git's -M
    detection reports as a rename since the content is unchanged) must stay excluded for the
    rest of this mine() call. The rename touch itself is already skipped by the `.sgt/` prefix
    check on its old_path -- but without carrying the exclusion forward, a later *plain* delete
    of the renamed destination (no longer matching the `.sgt/` prefix) mines a genuine prune op
    whose before_version was never produced by any op in the stream, breaking downward-closure."""
    gb, sgt_dir = init_store(tmp_path)
    (tmp_path / "a.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    (sgt_dir / "frontier.json").write_text('{"x": 1}\n', encoding="utf-8")
    gb.commit_all("seed .sgt state")

    bak_dir = tmp_path / ".sgt.bak"
    bak_dir.mkdir()
    (sgt_dir / "frontier.json").rename(bak_dir / "frontier.json")
    gb.commit_all("migrate .sgt -> .sgt.bak")  # git detects this as a 100% rename

    (bak_dir / "frontier.json").unlink()
    gb.commit_all("drop the old backup")  # a plain delete, not part of any rename this time

    ops = mine(tmp_path)
    assert not any(".sgt.bak/frontier.json" in op.footprint for op in ops)

    ids = {op.id for op in ops}
    assert order.is_valid_ideal(ops, ids)
