"""Materializing a fold onto disk: `sgt advanced fold --at <spec> --out <dir>` and the
`revert/restore --emit --out <dir>` counterfactual.

`fold_view` has always been able to reconstruct `code(I)` at any frontier, but only as JSON text
decoded `utf-8, "replace"` -- right for a preview pane, lossy for a file. These pin the two
properties that make the difference: the bytes are the fold's own bytes, and the write is a
*sync* against a long-lived overlay directory (the render panel's target, which also holds
`node_modules` and a dev-server cache) rather than a dump into a directory anyone may wipe.
"""

import json
import subprocess

from sgt.api import FOLD_MANIFEST, blame_all_view, fold_out_view, fold_view, history_view
from sgt.core.lens import current_ideal, get, ideal_for_ref
from tests.laws import corpus


def _mined(tmp_path, name):
    repo = corpus.CORPUS[name].build(tmp_path / "repo")
    get(repo)
    return repo


def _forward_subtracting_target(repo):
    """An op whose revert *rewrites* shared symbols at their tip (minting `new_ops`) rather than
    dropping the target's own ops -- the ordinary case for reverting one checkpoint of a symbol
    later work has since touched, and the only case in which `result_op_ids` says something a
    client could not have derived. Found rather than hardcoded so a re-mine that reshuffles op-ids
    fails loudly here instead of silently weakening every assertion downstream."""
    from sgt.core import verbs

    for op_id in sorted(current_ideal(repo).op_ids):
        preview = verbs.plan_revert(repo, op_id)
        if preview.ok and preview.added:
            return op_id
    raise AssertionError("fixture no longer exercises forward subtraction")


def _in(repo, argv):
    import os

    from sgt.cli import main

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


def test_fold_out_writes_the_folds_raw_bytes_not_the_json_views_text(tmp_path):
    """The whole reason `--out` exists rather than a client writing `fold_view`'s strings: that
    view decodes with `errors="replace"`, so a binary asset round-tripped through it comes back
    corrupted. `linear_history` carries `logo.bin` (`\\x89PNG\\x00\\x01\\x02`), which is neither
    valid UTF-8 nor NUL-free."""
    repo = _mined(tmp_path, "linear_history")
    out = tmp_path / "overlay"

    summary = fold_out_view(repo, out, ref="HEAD")
    assert summary["ok"]

    original = (repo / "logo.bin").read_bytes()
    assert b"\x00" in original and original == b"\x89PNG\x00\x01\x02"
    assert (out / "logo.bin").read_bytes() == original

    # The lossy path, spelled out, so this test fails loudly if `--out` is ever re-routed
    # through `fold_view` for convenience.
    via_json = fold_view(repo, ref="HEAD")["files"]["logo.bin"].encode("utf-8")
    assert via_json != original


def test_fold_out_at_a_ref_matches_a_real_git_checkout_byte_for_byte(tmp_path):
    """`--at <sha>` materializes what `git checkout <sha>` would, for every path sgt mines.
    Extracted with `git archive` rather than compared through sgt's own reader, so the fold is
    checked against git's bytes and not against itself."""
    import tarfile

    repo = _mined(tmp_path, "linear_history")
    sha = history_view(repo, full=True)["commits"][-1]["sha"]

    checkout = tmp_path / "checkout"
    checkout.mkdir()
    archive = tmp_path / "at.tar"
    archive.write_bytes(
        subprocess.run(["git", "-C", str(repo), "archive", sha], check=True,
                       capture_output=True).stdout
    )
    with tarfile.open(archive) as tf:
        tf.extractall(checkout, filter="data")

    out = tmp_path / "overlay"
    fold_out_view(repo, out, ref=sha)

    materialized = _tree(out)
    # Non-vacuity, and specifically the path most likely to be silently dropped: a fold that
    # stopped covering the binary would still pass the loop below.
    assert "logo.bin" in materialized and len(materialized) > 1
    for path, data in materialized.items():
        assert data == (checkout / path).read_bytes(), path


def test_fold_out_scrubbing_backward_deletes_a_file_that_left_the_fold(tmp_path):
    """Deletion is part of materializing: `removed_paths` has `gone.py` at commit-index 0 and
    not at 1, so scrubbing forward must remove it and scrubbing back must bring it back. A
    playhead that only ever adds files shows a state that never existed."""
    repo = _mined(tmp_path, "removed_paths")
    out = tmp_path / "overlay"

    first = fold_out_view(repo, out, at_commit_index=0)
    assert (out / "gone.py").exists()
    assert first["deleted"] == 0  # nothing to scrub on the first pass

    second = fold_out_view(repo, out, at_commit_index=1)
    assert not (out / "gone.py").exists()
    assert second["deleted"] == 1

    third = fold_out_view(repo, out, at_commit_index=0)
    assert (out / "gone.py").exists()
    assert third["deleted"] == 0


def test_fold_out_never_deletes_anything_it_did_not_write(tmp_path):
    """The overlay is long-lived and holds what the fold cannot: everything `.gitignore` matches
    is `ignored` tier, so `node_modules` and a dev-server cache are correctly absent from
    `code(I)` and must survive every scrub. The delete authority is the manifest, never the
    directory listing."""
    repo = _mined(tmp_path, "removed_paths")
    out = tmp_path / "overlay"
    fold_out_view(repo, out, at_commit_index=0)

    (out / "node_modules" / "pkg").mkdir(parents=True)
    (out / "node_modules" / "pkg" / "index.js").write_text("module.exports = 1\n")
    (out / ".vite-cache").write_text("warm\n")
    (out / "NOTES.txt").write_text("hand-written\n")

    summary = fold_out_view(repo, out, at_commit_index=1)

    assert summary["deleted"] == 1  # gone.py only
    assert (out / "node_modules" / "pkg" / "index.js").read_text() == "module.exports = 1\n"
    assert (out / ".vite-cache").read_text() == "warm\n"
    assert (out / "NOTES.txt").read_text() == "hand-written\n"


def test_fold_out_into_a_populated_directory_with_no_manifest_deletes_nothing(tmp_path):
    """Pointed at a directory it has never written to, `--out` inventories rather than scrubs.
    Without this the first scrub into someone's existing project directory is a data-loss bug,
    and there is no manifest yet to tell sgt's files from theirs."""
    repo = _mined(tmp_path, "removed_paths")
    out = tmp_path / "existing"
    (out / "node_modules").mkdir(parents=True)
    (out / "node_modules" / "a.js").write_text("x\n")
    (out / "stray.txt").write_text("y\n")

    summary = fold_out_view(repo, out, at_commit_index=1)

    assert summary["deleted"] == 0
    assert (out / "stray.txt").exists() and (out / "node_modules" / "a.js").exists()
    # ...and it *is* inventoried now, so the next scrub can clean up after itself.
    assert set(json.loads((out / FOLD_MANIFEST).read_text())["paths"]) == set(_tree(out)) - {
        "node_modules/a.js", "stray.txt"
    }


def test_fold_at_now_matches_the_working_tree_after_an_applied_revert(tmp_path):
    """The contract `--at now` exists for: after a real, committed ideal edit, the fold agrees with
    the files on disk.

    `--at HEAD` does not, and cannot. `lens.ideal_for_ref` selects ops whose provenance intersects
    the ref's commit ancestry -- documented behaviour that `sync` and ref-to-ref `diff` depend on --
    but `apply` mints a revert's forward-subtraction ops with **empty provenance**, so no ref can
    ever select them, while the ops they compensate for still carry provenance inside HEAD and stay
    selected. The ref fold therefore returns the *pre-revert* ideal. On bikecount that was 111 ops
    against the current ideal's 113, disagreeing with the working tree on 7 of 16 files, and it is
    what the workbench's code(I) panel was showing after every revert."""
    repo = _mined(tmp_path, "linear_history")
    target = _forward_subtracting_target(repo)
    assert _in(repo, ["revert", target, "--json"]) == 0

    out = tmp_path / "now"
    fold_out_view(repo, out, current=True)
    assert _tree(out) == _worktree_managed(repo)


def test_fold_at_now_resolves_the_current_ideal_never_a_ref(tmp_path):
    """`now` reads `lens.current_ideal` -- the table -- and consults no ref at all.

    This test used to assert `now != HEAD` after a revert, and that was true when it was written:
    `ideal_for_ref` projected by commit provenance, `apply` mints its compensating ops with none,
    so a HEAD fold returned the pre-revert tree. Fixing `ideal_for_ref` to read the ref tip's own
    `Sgt-Op:` trailers removed that divergence -- the revert commit records the post-revert ideal,
    so the two now agree, which is the correct outcome and the reason the assertion was retired
    rather than the test.

    What `now` still guarantees, and what is pinned here, is its *source*: the present as sgt
    records it, independent of whether any commit's trailers survived, whether HEAD is detached, or
    whether a ref exists to name. Numeric agreement with HEAD on a healthy repo is a property of
    that repo, not of the spelling."""
    repo = _mined(tmp_path, "linear_history")
    target = _forward_subtracting_target(repo)
    assert _in(repo, ["revert", target, "--json"]) == 0

    assert fold_view(repo, current=True)["op_count"] == len(current_ideal(repo).op_ids)

    # Raw bytes via `fold_out_view`, never `fold_view`'s strings: those are decoded
    # `utf-8, "replace"`, so `linear_history`'s `logo.bin` would not survive the round trip.
    now_dir = tmp_path / "now"
    fold_out_view(repo, now_dir, current=True)
    assert _tree(now_dir) == _worktree_managed(repo)

    # ...and the trailer fix means HEAD now agrees rather than lagging. Asserted so that a
    # regression in `ideal_for_ref` surfaces here too, on the frontier the extension defaults to.
    head_dir = tmp_path / "head"
    fold_out_view(repo, head_dir, ref="HEAD")
    assert _tree(head_dir) == _tree(now_dir)


def test_ideal_for_ref_reads_trailers_so_save_authored_history_is_not_lost(tmp_path):
    """The seedbank defect, at corpus scale.

    `op.provenance` is stamped only by *mining* pre-existing git history. Ops minted by `sgt save`
    and by `verbs.apply` carry none, so provenance projection cannot select them and
    `ideal_for_ref` returned whatever fraction of the store happened to be mined. On a React app
    authored the way sgt asks people to author -- one scaffold commit mined, twelve `sgt save`
    commits on top -- that was **10 of 151 ops, 7 of 30 files, and 0 of its 23 source files**. The
    repo rendered as if it had no source code. bikecount hid it at 111 of 189, because its history
    was authored as real git commits and then mined, which is the one shape that looks healthy.

    Reading the ref tip's own `Sgt-Op:` trailers fixes it, because `record_ideal` writes the whole
    ideal into every commit sgt makes. Here that is asserted the strict way: the ref fold must equal
    the current ideal exactly, so a re-mine that happens to restore provenance cannot mask a
    regression in the trailer path."""
    repo = _mined(tmp_path, "linear_history")
    target = _forward_subtracting_target(repo)
    assert _in(repo, ["revert", target, "--json"]) == 0  # mints ops with no provenance

    from sgt.core import opindex

    ops = opindex.index_ops(repo)
    provenanced = {o.id for o in ops if o.provenance}
    live = current_ideal(repo).op_ids
    assert live - provenanced, "fixture must contain ops no ref could select by provenance"

    assert set(ideal_for_ref(repo, "HEAD").op_ids) == set(live)


def test_an_unresolvable_ref_stays_a_clean_refusal(tmp_path):
    """`commit_shas` answers `[]` for a ref that does not resolve, so the provenance rung has always
    degraded to an empty ideal rather than raising. Reading trailers introduced a way to break that:
    `commit_message` raises `GitError` where `commit_shas` returns empty, which turned
    `sgt advanced fold --at no-such-branch` into a traceback instead of a refusal. Caught in review
    of my own change, pinned here so the trailer rung cannot re-tighten what the fallback keeps
    loose."""
    repo = _mined(tmp_path, "linear_history")

    assert ideal_for_ref(repo, "no-such-branch").op_ids == frozenset()
    view = fold_view(repo, ref="no-such-branch")
    assert view["op_count"] == 0 and view["files"] == {}


def test_at_now_parses_to_the_current_ideal_and_never_to_a_ref(tmp_path):
    """The grammar rung itself. `now` shadows a branch of that name, exactly as the all-digit rung
    already shadows a branch named `5`; `refs/heads/now` is the escape hatch."""
    from sgt.cli.inspect import _parse_at

    assert _parse_at("now") == {"current": True}
    assert _parse_at("refs/heads/now") == {"ref": "refs/heads/now"}
    assert _parse_at("5") == {"at_commit_index": 5}
    assert _parse_at("main") == {"ref": "main"}

    repo = _mined(tmp_path, "linear_history")
    assert _emit(repo, ["advanced", "fold", "--at", "now", "--json"])["op_count"] == len(
        current_ideal(repo).op_ids
    )


def test_fold_out_prunes_a_directory_its_own_deletion_emptied_but_keeps_an_occupied_one(tmp_path):
    """An emptied directory is swept, because a stale empty `pages/` in the overlay is a
    directory that does not exist in the state being rendered. One that still holds anything --
    including something sgt did not write -- stays."""
    repo = _mined(tmp_path, "removed_paths")
    out = tmp_path / "overlay"

    # A nested layout the fold owns entirely, then one that also holds a foreign file.
    fold_out_view(repo, out, at_commit_index=0)
    manifest = out / FOLD_MANIFEST
    (out / "nested").mkdir()
    (out / "nested" / "gone.py").write_bytes((out / "gone.py").read_bytes())
    (out / "shared").mkdir()
    (out / "shared" / "gone.py").write_bytes((out / "gone.py").read_bytes())
    (out / "shared" / "keep.txt").write_text("not sgt's\n")
    manifest.write_text(json.dumps(
        {"paths": sorted(set(json.loads(manifest.read_text())["paths"])
                         | {"nested/gone.py", "shared/gone.py"})}
    ))

    fold_out_view(repo, out, at_commit_index=1)

    assert not (out / "nested").exists()  # emptied by its own deletion -> swept
    assert (out / "shared" / "keep.txt").read_text() == "not sgt's\n"  # occupied -> kept
    assert not (out / "shared" / "gone.py").exists()


def test_fold_out_reports_the_same_refusals_as_fold_view(tmp_path):
    """`--out` shares `_fold_ideal`, so a bad spec is refused identically and nothing is written
    -- an invalid frontier must not leave a half-synced overlay behind."""
    repo = _mined(tmp_path, "linear_history")
    out = tmp_path / "overlay"
    non_root = next(o["id"] for o in history_view(repo, full=True)["ops"] if o["commit_index"] > 0)

    assert fold_out_view(repo, out, op_ids=[non_root]).get("forked") is True
    assert "error" in fold_out_view(repo, out)
    assert not out.exists()


def test_emit_out_materializes_the_state_result_op_ids_names(tmp_path):
    """G3's round trip. `revert --emit` reports `result_op_ids`, the op-set of the ideal the edit
    lands on; `--emit --out` materializes exactly that state. Applying the same revert for real
    on a second copy must produce a byte-identical tree.

    The materialization cannot go through `fold --at op:<result_op_ids>`: a safe revert mints
    forward-subtraction ops that live only on the preview until `apply` stores them, so the fold
    refuses that id set as ungrounded -- which is why `verb_result_out_view` folds the preview
    object instead. That refusal is pinned below.
    """
    repo = _mined(tmp_path, "linear_history")
    applied = corpus.CORPUS["linear_history"].build(tmp_path / "applied")
    get(applied)

    target = _forward_subtracting_target(repo)
    out = tmp_path / "counterfactual"

    view = _emit(repo, ["revert", target, "--emit", "--out", str(out), "--json"])
    assert view["ok"] and view["out"]["ok"]
    assert view["out"]["op_count"] == len(view["result_op_ids"])

    assert _in(applied, ["revert", target, "--json"]) == 0
    actual = tmp_path / "actual"
    fold_out_view(applied, actual, op_ids=sorted(current_ideal(applied).op_ids))

    assert _tree(out) == _tree(actual)


def test_restore_emit_out_round_trips_the_same_way(tmp_path):
    """The inverse verb gets the same treatment: `restore --emit` names the ideal it lands on and
    `--out` materializes it. Restoring is a pure re-addition of stored ops, so this also covers
    the case where `result_op_ids` *is* addressable through the store -- both verbs answer in the
    same field either way, which is the point of putting it on the shared projection."""
    repo = _mined(tmp_path, "linear_history")
    applied = corpus.CORPUS["linear_history"].build(tmp_path / "applied")
    get(applied)

    target = _forward_subtracting_target(repo)
    assert _in(repo, ["revert", target, "--json"]) == 0
    assert _in(applied, ["revert", target, "--json"]) == 0

    out = tmp_path / "counterfactual"
    view = _emit(repo, ["restore", target, "--emit", "--out", str(out), "--json"])
    assert view["ok"] and view["out"]["ok"]
    assert view["out"]["op_count"] == len(view["result_op_ids"])

    assert _in(applied, ["restore", target, "--json"]) == 0
    actual = tmp_path / "actual"
    fold_out_view(applied, actual, op_ids=sorted(current_ideal(applied).op_ids))

    assert _tree(out) == _tree(actual)


def _worktree_managed(repo):
    """Every git-tracked path in `repo` that sgt has an opinion about, as `{path: bytes}` --
    tracked, not `ignored` tier, not sgt's own state. The set a materialized preview is supposed
    to be able to reproduce."""
    import subprocess

    from sgt.core.lens import _outside_sgts_remit

    ignored = _outside_sgts_remit(repo)
    listed = subprocess.run(["git", "-C", str(repo), "ls-files"], check=True,
                            capture_output=True, text=True).stdout.split()
    return {
        p: (repo / p).read_bytes() for p in listed
        if not p.startswith(".sgt/") and not ignored(p) and (repo / p).is_file()
    }


def _foreign_commit(repo, message):
    """Commit the working tree with plain `git`, bypassing sgt entirely -- the "foreign edit" whose
    committed drift `put()` documents. Deliberately not `sgt save`: the point is bytes that reach
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
    A tracked file edited and committed with plain `git commit`, bypassing sgt, carries bytes no
    ideal produces. Every sgt-authored shape (fork, rebirth, sync, revert) keeps the tree inside
    some ideal by construction, which is why none of them strand anything.

    Why this is a function-level test and not an end-to-end one: every sgt verb mines on contact,
    so `get()` absorbs the foreign commit and the drift is gone before a CLI revert could observe
    it -- measured, `backstop_kept` is `['c.py']` before `get()` and `[]` after. The condition is
    real but has a lifetime of exactly one sgt command, so the pair has to be checked directly."""
    from sgt.core import lens
    from sgt.core.fold import code
    from sgt.core.store import Store

    repo = _mined(tmp_path, "linear_history")
    ops = Store(repo).all_ops()
    prior = current_ideal(repo)

    (repo / "c.py").write_text("def qux():\n    return 'edited outside sgt'\n")
    _foreign_commit(repo, "foreign edit: committed without sgt")
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
    from sgt.core import lens
    from sgt.core.store import Store

    repo = _mined(tmp_path, "linear_history")
    ops = Store(repo).all_ops()
    prior = current_ideal(repo)
    (repo / "c.py").write_text("def qux():\n    return 'edited outside sgt'\n")
    _foreign_commit(repo, "foreign edit: committed without sgt")

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


def test_emit_out_carries_a_backstop_kept_path_at_its_on_disk_bytes(tmp_path):
    """The specific branch the general test above cannot reach on this corpus: no fixture here
    produces a revert that leaves a backstop-kept path (bikecount does -- `bikecount/pages/
    monthly.py`, which is how the defect was found). So the mechanism is pinned directly.

    A backstop-kept path is carried at its *current on-disk* bytes, because the apply keeps it by
    not touching it -- never at whatever the ideal would have folded for it."""
    from sgt.core import lens

    repo = _mined(tmp_path, "linear_history")
    kept_path = "c.py"
    kept_bytes = (repo / kept_path).read_bytes()
    assert kept_bytes, "fixture must have a non-empty file to keep"

    real = lens.materialization_skips

    # `**kwargs` on purpose, not a fixed signature: this stub stands in for the real predictor, and
    # pinning its parameter list here would make the stub the thing under test. It already caught
    # one drift -- when `prior_ideal` was added, a fixed-signature stub failed with `unexpected
    # keyword argument` rather than silently passing.
    def monkey(r, materialized, ops=None, **kwargs):
        return {**real(r, materialized, ops, **kwargs), "backstop_kept": [kept_path]}

    lens.materialization_skips = monkey
    try:
        target = _forward_subtracting_target(repo)
        out = tmp_path / "counterfactual"
        assert _emit(repo, ["revert", target, "--emit", "--out", str(out), "--json"])["ok"]
    finally:
        lens.materialization_skips = real

    assert (out / kept_path).read_bytes() == kept_bytes
    assert kept_path in json.loads((out / FOLD_MANIFEST).read_text())["paths"]


def test_emit_out_writes_nothing_for_a_refused_preview(tmp_path):
    """A refused preview carries `after_ids == before_ids`, so materializing it would write the
    *current* state into the overlay and read back as a successful counterfactual -- the render
    panel would show today's app and call it "today minus this feature". Nothing is written and
    the view carries no `out` key to mistake for one."""
    from sgt.cli.ideal_edit import _emit_verb_result
    from sgt.core.verbs import VerbPreview

    repo = _mined(tmp_path, "linear_history")
    ids = current_ideal(repo).op_ids
    refused = VerbPreview(
        ok=False, verb="revert", target="c.py::qux", before_ids=ids, after_ids=ids,
        affected_symbols=(), forked=True, message="would leave two live versions",
    )
    out = tmp_path / "counterfactual"

    import contextlib
    import io

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        _emit_verb_result(str(repo), refused, emit=True, as_json=True, out=str(out))

    assert "out" not in json.loads(buf.getvalue())
    assert not out.exists()


def test_fold_out_cli_reports_what_it_wrote_and_removed(tmp_path):
    """The CLI surface: `--out` composes with the existing `--at` grammar and reports the sync,
    not the file listing the bare form prints."""
    repo = _mined(tmp_path, "removed_paths")
    out = tmp_path / "overlay"

    first = _emit(repo, ["advanced", "fold", "--at", "0", "--out", str(out), "--json"])
    assert first == {"ok": True, "path": str(out), "written": len(_tree(out)),
                     "deleted": 0, "op_count": first["op_count"]}

    second = _emit(repo, ["advanced", "fold", "--at", "1", "--out", str(out), "--json"])
    assert second["deleted"] == 1 and not (out / "gone.py").exists()


def test_result_op_ids_is_not_the_current_ideal_minus_removed(tmp_path):
    """The shortcut a client reaches for first, pinned as wrong. A safe revert rewrites shared
    symbols at their tip with new ops (`added`) rather than dropping the target's ops, so
    `before - removed` is a materially different tree -- which is the reason this field exists at
    all instead of a note in the docs."""
    repo = _mined(tmp_path, "linear_history")
    target = _forward_subtracting_target(repo)

    view = _emit(repo, ["revert", target, "--emit", "--json"])
    shortcut = current_ideal(repo).op_ids - set(view["removed"])

    assert view["added"]  # the forward-subtraction ops the shortcut cannot know about
    assert set(view["result_op_ids"]) != shortcut


def test_fold_refuses_result_op_ids_because_the_new_ops_are_not_stored_yet(tmp_path):
    """Why `--emit --out` exists rather than piping `result_op_ids` into `fold --at op:<ids>`.
    Documented as a test so the day forward-subtraction ops do become addressable, this fails and
    someone deletes the workaround."""
    repo = _mined(tmp_path, "linear_history")
    target = _forward_subtracting_target(repo)
    view = _emit(repo, ["revert", target, "--emit", "--json"])

    assert view["added"]
    assert fold_view(repo, op_ids=view["result_op_ids"]).get("forked") is True


def test_blame_all_matches_per_file_blame_and_folds_once(tmp_path):
    """`blame --all` is the repo-wide provenance map the render panel joins DOM elements against.
    Its spans must be the same objects `blame_view` returns per file -- one parser, either
    surface -- and its `features` map the union of theirs."""
    from sgt.api import blame_view

    repo = _mined(tmp_path, "mixed_coverage")
    from sgt.lens import map as lensmap

    lensmap.build_map(repo)  # blame attributes through the feature tree's op_leaf

    everything = blame_all_view(repo)
    assert everything["files"]

    merged = {}
    for file, entry in everything["files"].items():
        per_file = blame_view(repo, file)
        assert per_file["spans"] == entry["spans"], file
        merged.update(per_file["features"])
    assert merged == everything["features"]


def test_blame_all_covers_every_file_the_current_fold_contains(tmp_path):
    """One pass over the whole ideal, not a walk of the working tree: a client asking for the
    provenance map gets an entry per covered file, including the ones with no attributable span
    yet, so a missing key means "not covered" rather than "not asked about"."""
    from sgt.core.fold import code
    from sgt.core.store import Store

    repo = _mined(tmp_path, "linear_history")
    materialized = code(current_ideal(repo), Store(repo).all_ops())

    assert set(blame_all_view(repo)["files"]) == set(materialized)
