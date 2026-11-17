"""Materializing a fold onto disk: the `revert/restore --emit --out <dir>` counterfactual.

`fold_view` has always been able to reconstruct `code(I)` at any frontier, but only as JSON text
decoded `utf-8, "replace"` -- right for a preview pane, lossy for a file. These pin the two
properties that make the difference: the bytes are the fold's own bytes, and the write is a
*sync* against a long-lived overlay directory (the render panel's target, which also holds
`node_modules` and a dev-server cache) rather than a dump into a directory anyone may wipe.
"""

import json
import subprocess

from ugh.api import FOLD_MANIFEST, _parse_show_spec, fold_view
from ugh.core.fold import code
from ugh.core.store import Store
from ugh.core.lens import current_ideal, get, ideal_for_ref
from tests.laws import corpus


def _mined(tmp_path, name):
    repo = corpus.CORPUS[name].build(tmp_path / "repo")
    get(repo)
    return repo


def _forward_subtracting_target(repo):
    """An op whose revert changes files. Found rather than hardcoded so a re-mine that reshuffles
    op-ids fails loudly here instead of silently weakening every assertion downstream."""
    from ugh.core import planner

    for op_id in sorted(current_ideal(repo).op_ids):
        p = planner.plan(repo, {op_id}, target=op_id)
        if p.ok and p.files and p.new_ops:
            return op_id
    raise AssertionError("fixture has no revert that changes a file")


def _in(repo, argv):
    import os

    from ugh.cli import main

    cwd = os.getcwd()
    os.chdir(repo)
    try:
        return main(argv)
    finally:
        os.chdir(cwd)


def _emit(repo, argv):
    """Run a CLI verb with `--json` and return its parsed view."""
    import contextlib
    import io

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        _in(repo, argv)
    return json.loads(buf.getvalue())


def _tree(out):
    """Every file under `out` except the manifest, as `{relative path: bytes}`."""
    return {
        str(p.relative_to(out)): p.read_bytes()
        for p in sorted(out.rglob("*")) if p.is_file() and p.name != FOLD_MANIFEST
    }


def test_fold_at_now_resolves_the_current_ideal_never_a_ref(tmp_path):
    """`now` reads `lens.current_ideal` -- the table -- and consults no ref at all.

    This test used to assert `now != HEAD` after a revert, and that was true when it was written:
    `ideal_for_ref` projected by commit provenance, `apply` mints its compensating ops with none,
    so a HEAD fold returned the pre-revert tree. Fixing `ideal_for_ref` to read the ref tip's own
    `Sgt-Op:` trailers removed that divergence -- the revert commit records the post-revert ideal,
    so the two now agree, which is the correct outcome and the reason the assertion was retired
    rather than the test.

    What `now` still guarantees, and what is pinned here, is its *source*: the present as ugh
    records it, independent of whether any commit's trailers survived, whether HEAD is detached, or
    whether a ref exists to name. Numeric agreement with HEAD on a healthy repo is a property of
    that repo, not of the spelling."""
    repo = _mined(tmp_path, "linear_history")
    target = _forward_subtracting_target(repo)
    assert _in(repo, ["revert", target, "--json"]) == 0

    now = fold_view(repo, current=True)
    assert now["op_count"] == len(current_ideal(repo).op_ids)

    # ...and the trailer fix means HEAD now agrees rather than lagging. Asserted so that a
    # regression in `ideal_for_ref` surfaces here too, on the frontier the extension defaults to.
    assert fold_view(repo, ref="HEAD")["files"] == now["files"]


def test_ideal_for_ref_reads_trailers_so_save_authored_history_is_not_lost(tmp_path):
    """The seedbank defect, at corpus scale.

    `op.provenance` is stamped only by *mining* pre-existing git history. Ops minted by `ugh save`
    and by `verbs.apply` carry none, so provenance projection cannot select them and
    `ideal_for_ref` returned whatever fraction of the store happened to be mined. On a React app
    authored the way ugh asks people to author -- one scaffold commit mined, twelve `ugh save`
    commits on top -- that was **10 of 151 ops, 7 of 30 files, and 0 of its 23 source files**. The
    repo rendered as if it had no source code. bikecount hid it at 111 of 189, because its history
    was authored as real git commits and then mined, which is the one shape that looks healthy.

    Reading the ref tip's own `Sgt-Op:` trailers fixes it, because `record_ideal` writes the whole
    ideal into every commit ugh makes. Here that is asserted the strict way: the ref fold must equal
    the current ideal exactly, so a re-mine that happens to restore provenance cannot mask a
    regression in the trailer path."""
    repo = _mined(tmp_path, "linear_history")
    target = _forward_subtracting_target(repo)
    assert _in(repo, ["revert", target, "--json"]) == 0  # mints ops with no provenance

    from ugh.core import opindex

    ops = opindex.index_ops(repo)
    provenanced = {o.id for o in ops if o.provenance}
    live = current_ideal(repo).op_ids
    assert live - provenanced, "fixture must contain ops no ref could select by provenance"

    assert set(ideal_for_ref(repo, "HEAD").op_ids) == set(live)


def test_an_unresolvable_ref_stays_a_clean_refusal(tmp_path):
    """`commit_shas` answers `[]` for a ref that does not resolve, so the provenance rung has always
    degraded to an empty ideal rather than raising. Reading trailers introduced a way to break that:
    `commit_message` raises `GitError` where `commit_shas` returns empty, which turned a fold at
    `no-such-branch` into a traceback instead of a refusal. Caught in review
    of my own change, pinned here so the trailer rung cannot re-tighten what the fallback keeps
    loose."""
    repo = _mined(tmp_path, "linear_history")

    assert ideal_for_ref(repo, "no-such-branch").op_ids == frozenset()
    view = fold_view(repo, ref="no-such-branch")
    assert view["op_count"] == 0 and view["files"] == {}


def test_at_now_parses_to_the_current_ideal_and_never_to_a_ref(tmp_path):
    """The grammar rung itself. `now` shadows a branch of that name, exactly as the all-digit rung
    already shadows a branch named `5`; `refs/heads/now` is the escape hatch."""
    assert _parse_show_spec("now") == {"current": True}
    assert _parse_show_spec("refs/heads/now") == {"ref": "refs/heads/now"}
    assert _parse_show_spec("5") == {"at_commit_index": 5}
    assert _parse_show_spec("main") == {"ref": "main"}

    repo = _mined(tmp_path, "linear_history")
    assert fold_view(repo, **_parse_show_spec("now"))["op_count"] == len(current_ideal(repo).op_ids)


def test_emit_out_materializes_the_tree_the_apply_leaves(tmp_path):
    """`revert --emit --out <dir>` writes the tree the plan describes; applying the same revert for
    real on a second copy produces a byte-identical tree."""
    repo = _mined(tmp_path, "linear_history")
    applied = corpus.CORPUS["linear_history"].build(tmp_path / "applied")
    get(applied)

    target = _forward_subtracting_target(repo)
    out = tmp_path / "counterfactual"

    view = _emit(repo, ["revert", target, "--emit", "--out", str(out), "--json"])
    assert view["ok"] and view["out"]["ok"]

    assert _in(applied, ["revert", target, "--json"]) == 0
    assert _tree(out) == code(current_ideal(applied), Store(applied).all_ops())


def test_restore_emit_out_round_trips_the_same_way(tmp_path):
    """The inverse verb gets the same treatment: `restore --emit --out` writes the tree restoring
    would leave, and the applied restore on a second copy matches it byte for byte."""
    repo = _mined(tmp_path, "linear_history")
    applied = corpus.CORPUS["linear_history"].build(tmp_path / "applied")
    get(applied)

    target = _forward_subtracting_target(repo)
    assert _in(repo, ["revert", target, "--json"]) == 0
    assert _in(applied, ["revert", target, "--json"]) == 0

    out = tmp_path / "counterfactual"
    view = _emit(repo, ["restore", target, "--emit", "--out", str(out), "--json"])
    assert view["ok"] and view["out"]["ok"] and view["files"]

    assert _in(applied, ["restore", target, "--json"]) == 0
    assert _tree(out) == code(current_ideal(applied), Store(applied).all_ops())


def _worktree_managed(repo):
    """Every git-tracked path in `repo` that ugh has an opinion about, as `{path: bytes}` --
    tracked, not `ignored` tier, not ugh's own state. The set a materialized preview is supposed
    to be able to reproduce."""
    import subprocess

    from ugh.core.lens import _outside_ughs_remit

    ignored = _outside_ughs_remit(repo)
    listed = subprocess.run(["git", "-C", str(repo), "ls-files"], check=True,
                            capture_output=True, text=True).stdout.split()
    return {
        p: (repo / p).read_bytes() for p in listed
        if not p.startswith(".sgt/") and not ignored(p) and (repo / p).is_file()
    }


def _foreign_commit(repo, message):
    """Commit the working tree with plain `git`, bypassing ugh entirely -- the "foreign edit" whose
    committed drift `put()` documents. Deliberately not `ugh save`: the point is bytes that reach
    HEAD without any op recording them."""
    import os

    env = {**os.environ, "GIT_AUTHOR_NAME": "foreign", "GIT_AUTHOR_EMAIL": "foreign@example.com",
           "GIT_COMMITTER_NAME": "foreign", "GIT_COMMITTER_EMAIL": "foreign@example.com"}
    for argv in (["add", "-A"], ["commit", "-q", "-m", message]):
        subprocess.run(["git", "-C", str(repo), *argv], check=True, capture_output=True, env=env)


def test_the_backstop_predictor_agrees_with_the_writer(tmp_path):
    """`lens.materialization_skips` exists for exactly one purpose: to say what
    `lens._write_working_tree` would do, without writing. Nothing enforced that pairing, and it has
    already broken once -- `prior_ideal` was added to the writer alone, the predictor kept
    answering the old question, and `revert --emit --out` went on promising three bikecount pages
    that the apply had begun deleting. Both directions of that mismatch are silent: a preview that
    over-promises files and one that under-promises them look equally healthy from either side.

    So this runs the pair against each other on a repo where the backstop genuinely fires, using
    the trigger `put()` itself names -- "a local merge/cherry-pick the miner mis-attributed (F7/F9),
    or a **foreign edit** ... the drift is committed (on-disk == HEAD), not an uncommitted change".
    A tracked file edited and committed with plain `git commit`, bypassing ugh, carries bytes no
    ideal produces. Every ugh-authored shape (fork, rebirth, sync, revert) keeps the tree inside
    some ideal by construction, which is why none of them strand anything.

    Why this is a function-level test and not an end-to-end one: every ugh verb mines on contact,
    so `get()` absorbs the foreign commit and the drift is gone before a CLI revert could observe
    it -- measured, `backstop_kept` is `['c.py']` before `get()` and `[]` after. The condition is
    real but has a lifetime of exactly one ugh command, so the pair has to be checked directly."""
    from ugh.core import lens
    from ugh.core.fold import code
    from ugh.core.store import Store

    repo = _mined(tmp_path, "linear_history")
    ops = Store(repo).all_ops()
    prior = current_ideal(repo)

    (repo / "c.py").write_text("def qux():\n    return 'edited outside ugh'\n")
    _foreign_commit(repo, "foreign edit: committed without ugh")
    # Everything the current ideal folds, minus the one path -- so `c.py` alone reaches the
    # delete-or-keep decision and the assertion is about that decision, not about bulk deletion.
    materialized = {p: b for p, b in code(prior, ops).items() if p != "c.py"}

    predicted = lens.materialization_skips(repo, materialized, ops, prior_ideal=prior)
    actual = lens._write_working_tree(repo, materialized, ops, prior_ideal=prior)

    assert predicted["backstop_kept"] == actual["backstop_kept"] == ["c.py"]
    assert (repo / "c.py").exists(), "the writer must actually have kept what it reported keeping"


def test_prior_ideal_does_not_suppress_genuinely_unrecoverable_bytes(tmp_path):
    """The companion to the pairing test above, and the reason `prior_ideal` is a second chance
    rather than an override. It narrows the backstop to bytes *some* ideal can still produce; bytes
    no ideal can produce are still kept, because deleting those is the unrecoverable case R4 exists
    to prevent. If this ever passes with an empty list, `prior_ideal` has become a way to lose
    data rather than a way to stop over-keeping."""
    from ugh.core import lens
    from ugh.core.store import Store

    repo = _mined(tmp_path, "linear_history")
    ops = Store(repo).all_ops()
    prior = current_ideal(repo)
    (repo / "c.py").write_text("def qux():\n    return 'edited outside ugh'\n")
    _foreign_commit(repo, "foreign edit: committed without ugh")

    assert lens.materialization_skips(repo, {}, ops)["backstop_kept"] == ["c.py"]
    assert lens.materialization_skips(repo, {}, ops, prior_ideal=prior)["backstop_kept"] == ["c.py"]


def test_emit_out_writes_the_tree_the_apply_leaves_not_the_strict_fold(tmp_path):
    """The preview must agree with the apply about **which files exist**, not just about the
    content of the ones it happens to write.

    `apply` writes `code(I)` and then deletes the tracked paths the ideal dropped -- except those
    R4's backstop keeps, whose live bytes no valid ideal can regenerate. Those files are still on
    disk, still importable, after the revert. A preview that emits the strict `code(I)` silently
    omits them, and for anything that routes off the filesystem the consequence is visible: on
    bikecount, `pages.discover()` reads the page modules that exist, so the omission rendered a
    four-tab nav where the applied app shows five.

    This pins the general contract end to end -- preview tree == applied tree, file set and bytes.

    KNOWN WEAKNESS, stated so nobody mistakes a green run here for coverage: on this corpus the
    backstop set is **empty**, so the assertion below never exercises the branch the contract is
    really about, and this test passed straight through the regression that motivated it. Six
    attempts were made to build a fixture that strands a tracked path -- an opaque-file fork, a
    delete-vs-edit fork, add/delete/re-add rebirth, two-clone sync forks over a shared base, and
    over independent births -- and every one of them left the path in the fold, because a surviving
    base op or a resolved tip keeps producing it. Under the corrected rule a path is only stranded
    when its live bytes are reproducible by *no* ideal, which on a clean mined repo means committed
    drift rather than anything a revert does. `test_the_backstop_predictor_agrees_with_the_writer`
    is the guard that actually covers the branch; this one covers the shape."""
    repo = _mined(tmp_path, "linear_history")
    applied = corpus.CORPUS["linear_history"].build(tmp_path / "applied")
    get(applied)

    target = _forward_subtracting_target(repo)
    out = tmp_path / "counterfactual"
    assert _emit(repo, ["revert", target, "--emit", "--out", str(out), "--json"])["ok"]
    assert _in(applied, ["revert", target, "--json"]) == 0

    assert _tree(out) == _worktree_managed(applied)


def test_emit_out_writes_nothing_for_a_refused_plan(tmp_path):
    """A refused plan would write today's tree and read as a counterfactual: nothing is written
    and the view carries no `out` key to mistake for one."""
    from ugh.cli.ideal_edit import _answer
    from ugh.core import planner

    repo = _mined(tmp_path, "linear_history")
    out = tmp_path / "counterfactual"
    refused = planner.Plan("revert", "x", ok=False, message="refused")

    import contextlib
    import io
    from unittest import mock

    buf = io.StringIO()
    with mock.patch.object(planner, "plan", lambda *a, **k: refused), contextlib.redirect_stdout(buf):
        _answer(str(repo), "revert", frozenset(), "x", True, True, False, out=str(out))

    assert "out" not in json.loads(buf.getvalue())
    assert not out.exists()
