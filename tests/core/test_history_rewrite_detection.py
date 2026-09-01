"""Detecting the backward-git-history desync (P0-1) and routing to the remedy.

`_sync` seeds `base_ids` from the persisted `ideal_table[key]` and treats it as authoritative
afterwards -- deliberately, since that is what keeps an explicit revert durable across a rewrite
(F11/F20) -- but it never intersects it with the ref's *current* ancestry. So after `git reset
--hard` / `commit --amend` / `branch -f`, the recorded ideal still names ops from commits that no
longer exist: every count over-reports and a later `save` can dead-end.

`sgt advanced resync` has always been the fix. What was missing was any way for the user to learn
that: the desync presented as ordinary working-tree drift, so the surfaces suggested `sgt save`,
which finds nothing new, prints "nothing to save", and exits 0 -- reporting success while the
discrepancy stays. These tests pin the detection, the routing, and that it clears.
"""

from __future__ import annotations

import subprocess

import pytest

from sgt import api
from sgt.cli import main
from sgt.core import lens
from sgt.store.gitbind import init_store


@pytest.fixture()
def repo(tmp_path):
    """Three commits, each adding a module, with sgt having recorded the ideal at the tip."""
    gb, _ = init_store(tmp_path)
    for i in (1, 2, 3):
        (tmp_path / "a.py").write_text(f"def foo():\n    return {i}\n", encoding="utf-8")
        (tmp_path / f"m{i}.py").write_text(f"def m{i}():\n    return {i}\n", encoding="utf-8")
        gb.commit_all(f"v{i}")
    lens.get(tmp_path)  # record the ideal/witness at v3 -- the state the rewrite invalidates
    return tmp_path


def _reset_hard(repo, spec: str):
    subprocess.run(["git", "-C", str(repo), "reset", "--hard", spec],
                   capture_output=True, check=True)


def test_a_clean_repo_reports_no_rewrite(repo):
    assert lens.dropped_ideal_ops(repo) == []
    assert lens.sync_status(repo)["history_rewritten"] is False


def test_forward_out_of_band_commits_are_not_a_rewrite(repo):
    """The distinction that matters: `_sync`'s catch-up mines *forward* moves on its own, so an
    ordinary out-of-band commit needs no user action and must not raise this alarm."""
    gb, _ = init_store(repo)
    (repo / "m4.py").write_text("def m4():\n    return 4\n", encoding="utf-8")
    gb.commit_all("v4 out of band")
    lens.get(repo)
    assert lens.dropped_ideal_ops(repo) == []
    assert lens.sync_status(repo)["history_rewritten"] is False


def test_backward_reset_is_detected(repo):
    _reset_hard(repo, "HEAD~2")
    lens.get(repo)  # mine-on-contact, as any verb would do first

    dropped = lens.dropped_ideal_ops(repo)
    assert dropped, "ops from the dropped commits are still in the recorded ideal"
    assert lens.sync_status(repo)["history_rewritten"] is True


def test_comparing_the_witness_to_head_would_not_have_caught_it(repo):
    """Why the detector is per-op rather than witness-vs-HEAD: mine-on-contact advances the witness
    to the new head while leaving the ideal stale, so by the time any surface reads, the witness
    already agrees with HEAD and only the ideal is wrong. Pinned so a future "simplification" back to
    an ancestry check on the witness fails loudly instead of silently never firing."""
    _reset_hard(repo, "HEAD~2")
    lens.get(repo)

    gb, _ = init_store(repo)
    head = gb.head()
    witness = lens._load_witnesses(repo).get(lens._ref_key(gb) or head)
    assert witness == head, "the witness caught up; only the ideal is stale"
    assert lens.sync_status(repo)["history_rewritten"] is True


def test_a_content_only_amend_stays_on_the_save_path(repo):
    """The boundary, and the reason the detector is about *dropped ops* rather than "history was
    rewritten" in the abstract.

    A `commit --amend` that only changes content leaves every file still present and the difference
    genuinely absorbable: the new content becomes a new op on the next `sgt save`. So the generic
    drift advice is the *correct* advice there, and raising the resync alarm would send the user to a
    heavier remedy than the situation calls for. Only a move that leaves the ideal naming ops from
    unreachable commits -- where the counts over-report and no save can fix it -- routes to resync."""
    (repo / "m3.py").write_text("def m3():\n    return 33\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "commit", "-aq", "--amend", "--no-edit"],
                   capture_output=True, check=True)
    lens.get(repo)

    view = api.status_view(repo)
    assert view["sync_status"]["history_rewritten"] is False
    assert view["drift"]["any"] and "m3.py" in view["drift"]["paths"]
    assert view["files"] == 4, "every file is still present; nothing was dropped"


def test_resync_clears_it_and_corrects_the_counts(repo):
    """The remedy actually resolves the detected condition -- otherwise pointing at it is no better
    than pointing at `sgt save`."""
    before = api.status_view(repo)["symbols"]
    _reset_hard(repo, "HEAD~2")
    lens.get(repo)

    stale = api.status_view(repo)
    assert stale["sync_status"]["history_rewritten"] is True
    assert stale["symbols"] == before, "the stale ideal still counts the dropped commits' symbols"

    lens.resync(repo)

    fixed = api.status_view(repo)
    assert fixed["sync_status"]["history_rewritten"] is False
    assert fixed["symbols"] < before, "the counts now reflect what git actually holds"
    assert lens.dropped_ideal_ops(repo) == []


def test_the_surfaces_name_resync_and_not_save(repo, capsys, monkeypatch):
    """The user-facing half. `sgt log --summary` and `sgt now` must both route to `resync`; the
    generic "`sgt save` absorbs them" drift advice must not be what they see here."""
    _reset_hard(repo, "HEAD~2")
    monkeypatch.chdir(repo)
    capsys.readouterr()

    assert main(["log", "--summary", "--no-color"]) == 0
    summary = capsys.readouterr().out
    assert "sgt advanced resync" in summary
    assert "sgt save` absorbs" not in summary

    assert main(["now", "--no-color"]) == 0
    now = capsys.readouterr().out
    assert "sgt advanced resync" in now


def test_next_action_ranks_resync_above_a_plain_save(repo):
    """Ordering matters: until the ideal is re-derived, every other reading -- including the fork
    list and the unsaved-op count -- is computed over ops from commits that no longer exist."""
    _reset_hard(repo, "HEAD~2")
    lens.get(repo)
    action = api.now_view(repo)["next_action"]
    assert action["kind"] == "resync"
    assert action["command"] == "sgt advanced resync"


def test_saving_re_authored_work_after_a_backward_move_is_not_a_rewrite(repo):
    """F131: re-authoring dropped content and saving it must not read as a backward move.

    This is the study's stage 1, exactly. `./stage 1` checks the branch back to an earlier tag
    (a backward move), resyncs so the ideal matches that history, then replays the agent's edits
    into the working tree -- edits whose content is, by construction, the work that later landed
    on the fuller branch. `sgt save` mines those edits, and `store.add` dedups each one into the
    *existing* op with the same content, which carries the fuller branch's now-unreachable
    provenance. `put` then witnesses the save by trailer, not by provenance: provenance can never
    live inside its own witnessing commit, since writing it would change that commit's tree and
    so its sha.

    The result was that a save which worked perfectly ended with `sgt status` demanding `sgt
    advanced resync` and `sgt now` reporting "git history moved backward" -- on the one action the
    stage asks for, in every sgt-arm session. The remedy also undid the participant's save.
    """
    _reset_hard(repo, "HEAD~2")
    lens.resync(repo)  # what `./stage 1` does after the checkout: ideal matches this history
    assert lens.dropped_ideal_ops(repo) == []

    # Replay the dropped commits' content by hand -- the same bytes, so `store.add` dedups into
    # the ops mined from the commits the reset made unreachable.
    (repo / "a.py").write_text("def foo():\n    return 3\n", encoding="utf-8")
    (repo / "m2.py").write_text("def m2():\n    return 2\n", encoding="utf-8")
    (repo / "m3.py").write_text("def m3():\n    return 3\n", encoding="utf-8")

    from sgt.cli.porcelain import save
    assert save(str(repo), message="record the replayed work")["saved"] is True

    view = api.status_view(repo)
    assert lens.dropped_ideal_ops(repo) == [], "the save's own commit witnesses these ops by trailer"
    assert view["sync_status"]["history_rewritten"] is False
    assert not view["drift"]["any"], "the save absorbed the replayed edits"


def test_a_backward_move_past_an_sgt_commit_is_still_detected(repo):
    """The guard on the fix above: honouring the tip's trailers must not blunt the real check.

    After a genuine backward move the tip is an *older* commit, whose trailers name the ideal as it
    stood there -- so ops recorded after it are still uncovered and still reported. Pinned so a
    future simplification of the trailer fallback into "anything the ideal names is live" fails
    here instead of silently never firing again."""
    from sgt.cli.porcelain import save
    (repo / "a.py").write_text("def foo():\n    return 4\n", encoding="utf-8")
    assert save(str(repo), message="an sgt save, so the tip carries trailers")["saved"] is True
    _reset_hard(repo, "HEAD~2")
    lens.get(repo)

    assert lens.dropped_ideal_ops(repo), "ops recorded after the new tip are still dropped"
    assert lens.sync_status(repo)["history_rewritten"] is True
