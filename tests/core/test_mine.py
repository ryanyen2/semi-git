"""Tests for sgt.core.mine -- the operation stream (plan U2, R1/R2/R7/R12/R22)."""

from __future__ import annotations

import time

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

    ops, _last_sha = mine(repo)
    entity_ops = _entity_ops(_ops_for_commit(ops, tangled_sha))
    assert len(entity_ops) == 2, [op.footprint for op in entity_ops]
    touched = {sym for op in entity_ops for sym in op.footprint}
    assert touched == {"b.py::baz", "c.py::qux"}


def test_yaml_edit_yields_one_whole_file_op(tmp_path):
    """A non-parseable path (config.yaml) gets exactly one whole-file pseudo-symbol op (R7)."""
    repo = corpus.CORPUS["linear_history"].build(tmp_path / "repo")
    yaml_sha = corpus.commit_shas(repo)[6]  # "edit non-parseable config"

    ops, _last_sha = mine(repo)
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

    ops, _last_sha = mine(repo)
    binary_ops = [op for op in _ops_for_commit(ops, first_sha) if "logo.bin" in op.footprint]
    assert len(binary_ops) == 1
    _before, after_version = binary_ops[0].footprint["logo.bin"]
    assert after_version == expected_oid


def test_unparseable_midedit_degrades_to_whole_file(tmp_path):
    """A Python file broken mid-edit degrades to a whole-file symbol for that commit -- not
    zero entities, not a crash (R7). The degraded op must also actually be *live*: its
    before_version has to chain onto a producer some prior op actually emitted (the entity
    tier's chain never minted `symbol == path`), or it's permanently ungrounded and silently
    dropped from every ideal despite appearing in `mine()`'s raw output.

    v3 (U9): the parseable->whole-file flip now also emits transition prune ops closing the losing
    entity representation (`a.py::foo` and its residue), so the flip commit mints the whole-file add
    *plus* those BOTTOM ops -- exactly one of which is the whole-file symbol, and `code(I)` still
    reproduces the broken bytes because the whole-file symbol is the only live representation."""
    gb, _ = init_store(tmp_path)
    (tmp_path / "a.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    gb.commit_all("add foo")
    (tmp_path / "a.py").write_text("def foo(: %%% broken >>> \n", encoding="utf-8")
    sha2 = gb.commit_all("break foo mid-edit")

    from sgt.core.op import is_bottom

    ops, _last_sha = mine(tmp_path)
    broken_ops = _ops_for_commit(ops, sha2)
    whole_file_ops = [op for op in broken_ops if list(op.footprint) == ["a.py"] and not is_bottom(op.footprint["a.py"][1])]
    assert len(whole_file_ops) == 1  # the live whole-file degrade symbol
    # The rest of this commit's ops close the entity representation (all BOTTOM), never a second
    # live image of the same bytes.
    for op in broken_ops:
        if op is not whole_file_ops[0]:
            assert all(is_bottom(after) for _before, after in op.footprint.values())

    from sgt.core.fold import code
    from sgt.core.lens import get
    from sgt.core.store import Store

    ideal = get(tmp_path)
    assert whole_file_ops[0].id in ideal.op_ids
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

    ops, _last_sha = mine(tmp_path)
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

    ops, _last_sha = mine(tmp_path)
    reshape_ops = [
        op for op in _ops_for_commit(ops, sha2)
        if any("helper" in sym or sym == "a.py::Box" for sym in op.footprint)
    ]
    assert not any(op.kind == "move" for op in reshape_ops)
    assert any(op.kind == "prune" for op in reshape_ops)


def test_mining_is_repeatable_across_two_calls(tmp_path):
    """Mining the same history twice yields byte-identical Op payloads, not just ids."""
    repo = corpus.CORPUS["linear_history"].build(tmp_path / "repo")
    first, _last_sha = mine(repo)
    second, _last_sha = mine(repo)
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

    ops, _last_sha = mine(tmp_path)
    assert not any(".sgt.bak/frontier.json" in op.footprint for op in ops)

    ids = {op.id for op in ops}
    assert order.is_valid_ideal(ops, ids)


def test_mine_skips_symlink_paths(tmp_path):
    """U1/R3: a symlink (git mode 120000) is unmanaged. Mining must never record its
    target-string blob as ordinary file content -- otherwise a later materialization would
    splice the target string back as a regular file (or write it through the live link)."""
    gb, _ = init_store(tmp_path)
    (tmp_path / "real.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    gb.commit_all("add real")

    (tmp_path / "link.py").symlink_to("/etc/hostname")  # points outside the repo
    gb.commit_all("add symlink")

    ops, _last_sha = mine(tmp_path)
    assert not any("link.py" in sym for op in ops for sym in op.footprint), (
        "symlink path leaked into the op stream"
    )
    # the ordinary file beside it is still mined
    assert any("real.py::f" in sym for op in ops for sym in op.footprint)


def test_mine_skips_symlink_delete(tmp_path):
    """Deleting a symlink must not mine an (ungrounded) prune op for the link path -- the link
    was never modeled, so its removal is a no-op to the DAG."""
    gb, _ = init_store(tmp_path)
    (tmp_path / "real.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "link.py").symlink_to("/etc/hostname")
    gb.commit_all("add real and link")

    (tmp_path / "link.py").unlink()
    gb.commit_all("drop link")

    ops, _last_sha = mine(tmp_path)
    assert not any("link.py" in sym for op in ops for sym in op.footprint)
    ids = {op.id for op in ops}
    assert order.is_valid_ideal(ops, ids)


# -- U9: rebirth chaining and representation-flip bridging (R13/R14) --------------------------


def _readd_bottom(sha: str) -> str:
    from sgt.core.op import salted_bottom
    return salted_bottom(sha)


def test_add_delete_readd_is_one_chain(tmp_path):
    """R13: a symbol added, deleted, then re-added mines as ONE chain of three ops
    (add None->v1, prune v1->salted-bottom, re-add salted-bottom->v2) -- never two births of
    `(symbol, None)` that `fork_free` would drop. The re-add's before_version is the *deleting*
    commit's salted bottom, so it grounds and the file materializes completely in `code(I)`."""
    from sgt.core.fold import code
    from sgt.core.ideal import Ideal
    from sgt.core.op import is_bottom

    gb, _ = init_store(tmp_path)
    (tmp_path / "notes.txt").write_text("alpha\n", encoding="utf-8")
    gb.commit_all("add notes")
    (tmp_path / "notes.txt").unlink()
    del_sha = gb.commit_all("delete notes")
    (tmp_path / "notes.txt").write_text("beta\n", encoding="utf-8")
    gb.commit_all("re-add notes")

    mined, _last_sha = mine(tmp_path)
    ops = [op for op in mined if "notes.txt" in op.footprint]
    assert len(ops) == 3
    kinds = sorted(op.kind for op in ops)
    assert kinds == ["add", "prune", "rework"]  # add, prune (salted), re-add chained off the prune

    prune = next(op for op in ops if op.kind == "prune")
    readd = next(op for op in ops if op.kind == "rework")
    assert prune.footprint["notes.txt"][1] == _readd_bottom(del_sha)   # salted by the deleting commit
    assert readd.footprint["notes.txt"][0] == _readd_bottom(del_sha)   # re-add chains off THAT bottom
    assert is_bottom(prune.footprint["notes.txt"][1])

    ids = frozenset(op.id for op in ops)
    assert order.is_valid_ideal(ops, ids)  # grounded + fork-free: one clean chain
    materialized = code(Ideal.from_ops(order.reduce_to_ideal(ids, ops), ops), ops)
    assert materialized["notes.txt"] == b"beta\n"


def test_readd_cycle_produces_distinct_chained_ops(tmp_path):
    """R13: an add->del->A->del->A cycle must NOT collapse two deletions into one bottom -- each
    delete salts its own bottom (a distinct one per deletion), so each re-add chains onto its own
    specific delete and the current bytes materialize.

    Note the two flavours: with *distinct* content per rebirth (A->B->C below) the whole cycle
    linearizes into one valid fork-free chain. With *identical* content each rebirth (the trailing
    A->A case) the prune-side `before` is the content version, which is necessarily equal across
    rebirths (content-addressing: identical bytes == identical version) -- so `reduce_to_ideal`
    collapses that value-collision, but the bytes still materialize correctly. The salt's job (a
    distinct bottom per deletion, so re-adds don't re-collide on `(symbol, None)`) holds in both."""
    from sgt.core.fold import code
    from sgt.core.ideal import Ideal

    gb, _ = init_store(tmp_path)
    (tmp_path / "n.txt").write_text("A\n", encoding="utf-8")
    gb.commit_all("add A")
    (tmp_path / "n.txt").unlink()
    del1 = gb.commit_all("del 1")
    (tmp_path / "n.txt").write_text("B\n", encoding="utf-8")
    gb.commit_all("re-add B")
    (tmp_path / "n.txt").unlink()
    del2 = gb.commit_all("del 2")
    (tmp_path / "n.txt").write_text("C\n", encoding="utf-8")
    gb.commit_all("re-add C")

    mined, _last_sha = mine(tmp_path)
    ops = [op for op in mined if "n.txt" in op.footprint]
    bottoms = {op.footprint["n.txt"][1] for op in ops if op.kind == "prune"}
    assert bottoms == {_readd_bottom(del1), _readd_bottom(del2)}  # two DISTINCT salted bottoms

    ids = frozenset(op.id for op in ops)
    assert order.is_valid_ideal(ops, ids)  # distinct content -> one clean, fork-free chain
    materialized = code(Ideal.from_ops(order.reduce_to_ideal(ids, ops), ops), ops)
    assert materialized["n.txt"] == b"C\n"


def test_incremental_mine_chains_readd_when_deletion_predates_since(tmp_path):
    """R13 / LAW-0: the rebirth lookback is history-derived, not range-derived. When the deletion
    predates a `since`-restricted incremental mine, the re-add still chains onto the deleting
    commit's salted bottom (the prune op minted by the earlier mine already sits in the store)."""
    gb, _ = init_store(tmp_path)
    (tmp_path / "n.txt").write_text("A\n", encoding="utf-8")
    gb.commit_all("add")
    (tmp_path / "n.txt").unlink()
    del_sha = gb.commit_all("delete")
    (tmp_path / "other.txt").write_text("x\n", encoding="utf-8")
    since = gb.commit_all("unrelated (start of incremental range)")
    (tmp_path / "n.txt").write_text("B\n", encoding="utf-8")
    readd_sha = gb.commit_all("re-add long after the delete")

    inc, _last_sha = mine(tmp_path, since=since)  # deletion at del_sha is BEFORE `since`
    readd = next(op for op in inc if readd_sha in op.provenance and "n.txt" in op.footprint)
    assert readd.footprint["n.txt"][0] == _readd_bottom(del_sha)  # chained past the range boundary


def test_extension_flip_bridges_without_scheme_mix(tmp_path):
    """R14: a whole-file path replaced by a parseable file of the same stem (`.txt` -> `.py`)
    bridges cleanly -- the old whole-file symbol is closed with a BOTTOM op and the new entity
    symbols are born, so `code(I)` shows only the new representation, never both."""
    from sgt.core.fold import code
    from sgt.core.ideal import Ideal
    from sgt.core.op import is_bottom

    gb, _ = init_store(tmp_path)
    (tmp_path / "m.txt").write_text("def foo():\n    return 1\n", encoding="utf-8")
    gb.commit_all("add m.txt (whole-file)")
    (tmp_path / "m.txt").unlink()
    (tmp_path / "m.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    gb.commit_all("replace with parseable m.py")

    ops, _last_sha = mine(tmp_path)
    # the whole-file m.txt chain is closed (its tip is a bottom), and m.py materializes from entities
    txt_ops = [op for op in ops if "m.txt" in op.footprint]
    assert any(is_bottom(op.footprint["m.txt"][1]) for op in txt_ops)
    ids = frozenset(op.id for op in ops)
    assert order.is_valid_ideal(ops, order.reduce_to_ideal(ids, ops))
    materialized = code(Ideal.from_ops(order.reduce_to_ideal(ids, ops), ops), ops)
    assert "m.txt" not in materialized
    assert materialized["m.py"] == (tmp_path / "m.py").read_bytes()


def test_dirty_pass_syntax_error_mints_no_transition_ops(tmp_path):
    """R14: a transient syntax error in the *dirty* working tree must mint nothing permanent -- the
    flip transition ops (closing the entity representation) are suppressed for the pending pass, so
    no BOTTOM prune for a live entity lands in the store after a plain read."""
    from sgt.core.lens import get
    from sgt.core.op import is_bottom
    from sgt.core.store import Store

    gb, _ = init_store(tmp_path)
    (tmp_path / "a.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    gb.commit_all("add foo")
    # break it in the working tree only -- never committed
    (tmp_path / "a.py").write_text("def foo(: BROKEN >>>\n", encoding="utf-8")

    get(tmp_path)  # mines the dirty pass

    # No committed op in the store closes a.py::foo -- the entity's only op is still its live add.
    foo_ops = [op for op in Store(tmp_path).all_ops() if "a.py::foo" in op.footprint and op.provenance]
    assert foo_ops, "the committed add should still be present"
    assert not any(is_bottom(op.footprint["a.py::foo"][1]) for op in foo_ops), (
        "a transient dirty-pass syntax error minted a permanent transition (BOTTOM) op"
    )


# -- U1: deadline-bounded mining (groundwork for chunked incremental sync) --------------------


def test_deadline_far_in_the_future_matches_unbounded_mine(tmp_path):
    """A `deadline` that never triggers must leave `mine()`'s op output byte-identical to the
    unbounded call -- the cutoff is purely a stopping condition, never a behavior change on the
    ops actually mined."""
    repo = corpus.CORPUS["linear_history"].build(tmp_path / "repo")

    bounded_ops, bounded_last_sha = mine(repo, deadline=time.monotonic() + 10_000.0)
    unbounded_ops, unbounded_last_sha = mine(repo)

    assert bounded_ops == unbounded_ops
    assert bounded_last_sha == unbounded_last_sha == corpus.commit_shas(repo)[-1]


def test_deadline_already_expired_mines_nothing(tmp_path):
    """A `deadline` already in the past when `mine()` is called must not process even the first
    commit -- zero ops, `last_sha` stays `None`."""
    repo = corpus.CORPUS["linear_history"].build(tmp_path / "repo")

    ops, last_sha = mine(repo, deadline=time.monotonic() - 1.0)

    assert ops == []
    assert last_sha is None


def test_deadline_mid_history_stops_after_the_in_flight_commit(tmp_path, monkeypatch):
    """A deadline that expires partway through history stops the commit loop right after the
    commit that was in flight finishes -- the result is exactly that prefix of commits' ops, and
    `last_sha` names the last one actually processed. The clock is faked (monotonically increasing
    by one per `time.monotonic()` call) rather than using real `time.sleep`, so the deadline is hit
    deterministically after a chosen number of commits."""
    repo = corpus.CORPUS["linear_history"].build(tmp_path / "repo")
    shas = corpus.commit_shas(repo)
    k = 3  # stop right after the k-th commit; linear_history has 7 commits total

    counter = {"n": -1}

    def fake_monotonic():
        counter["n"] += 1
        return float(counter["n"])

    monkeypatch.setattr(time, "monotonic", fake_monotonic)

    ops, last_sha = mine(repo, deadline=float(k))
    assert last_sha == shas[k - 1]

    monkeypatch.undo()  # restore the real clock for the unbounded reference mine below
    full_ops, _full_last_sha = mine(repo)
    expected_ids = {op.id for op in full_ops if set(op.provenance) & set(shas[:k])}
    assert {op.id for op in ops} == expected_ids
    assert expected_ids, "sanity: the first k commits should mint at least one op"


def test_deadline_hit_before_target_skips_dirty_pass(tmp_path, monkeypatch):
    """`include_dirty` only makes sense once a chunk actually reaches `target` -- a deadline that
    stops the commit loop before that must skip the working-tree pass entirely, so no
    `provenance=()` (the dirty-pass marker, per `_mine_one`) op ever lands in a partial chunk's
    result."""
    gb, _ = init_store(tmp_path)
    (tmp_path / "a.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    gb.commit_all("commit 1")
    (tmp_path / "b.py").write_text("def bar():\n    return 2\n", encoding="utf-8")
    gb.commit_all("commit 2")
    # a dirty, uncommitted change -- if the dirty pass ran, this would surface as a
    # `provenance=()` op.
    (tmp_path / "c.py").write_text("def baz():\n    return 3\n", encoding="utf-8")

    counter = {"n": -1}

    def fake_monotonic():
        counter["n"] += 1
        return float(counter["n"])

    monkeypatch.setattr(time, "monotonic", fake_monotonic)

    ops, last_sha = mine(tmp_path, deadline=1.0, include_dirty=True)

    assert last_sha == gb.commit_shas()[-1]  # commit_shas() is newest-first; only the first (oldest) commit was processed
    assert not any(op.provenance == () for op in ops), "dirty pass ran despite hitting the deadline"
