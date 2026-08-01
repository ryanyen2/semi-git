"""Tests for sgt.core.sync -- the U15 `sgt sync` pipeline (plan R19/AE4).

Op-store union is nearly free (git's own file merge dedups by content-addressed path); the real
surface under test is what isn't free: same-symbol chain forks must be *surfaced* with a concrete
remedy rather than silently resolved, a same-id op independently mined on both clones must end up
with both sides' provenance (git's `-X ours` alone would drop one), pins/declared-edges/the
feature tree must reconcile across the union, and a second `sync` must be a no-op.

Two-clone fixtures are built directly with `GitBinding`/`lens` (no `tests/laws/corpus.py` case
fits a bare-remote-plus-two-working-clones shape) but follow the same hermetic discipline: real
`git` subprocess calls, no network, no wall-clock/LLM dependency.
"""

from __future__ import annotations

from pathlib import Path

from sgt.core import lens, sync, verbs
from sgt.core.store import Store
from sgt.lens.pins import Pins, load_pins, save_pins
from sgt.store.gitbind import GitBinding
from tests.conftest import _clone, _init_bare, _push


def _edit_and_commit(repo: Path, path: str, content: str, message: str) -> str:
    """Write content, commit it for real (works whether or not HEAD exists yet -- dirty-tree
    mining can't see anything on an unborn branch, so a real commit comes first), mine it, then
    re-commit with `Sgt-Op:` trailers via `lens.put`. `sync.py` reads a remote ref's ideal purely
    from its tip commit's trailers, without ever checking that ref out -- so any commit that can
    become a ref's tip in these tests must carry them, exactly like `lens.put` does on every real
    commit outside tests."""
    (repo / path).write_text(content, encoding="utf-8")
    content_sha = GitBinding(repo).commit_all(message)
    ideal = lens.get(repo)
    put_sha = lens.put(repo, ideal, message=f"sgt: mine {message}")
    lens.record_ideal(repo, ideal, put_sha)
    return content_sha  # the commit that actually witnesses the diff -- ops' provenance points here


def _two_clones(tmp_path: Path, main_py: str) -> tuple[Path, Path]:
    """A bare remote plus two clones, both past one shared init commit that writes `main_py`."""
    remote = _init_bare(tmp_path)
    a = _clone(remote, tmp_path / "a")
    lens.init(a)
    _edit_and_commit(a, "main.py", main_py, "init")
    _push(a)
    b = _clone(remote, tmp_path / "b")
    lens.get(b)  # baseline mine, mirrors a fresh teammate clone
    return a, b


_BASE = "def foo():\n    return 1\n\n\ndef bar():\n    return 2\n"


def test_sync_merges_disjoint_edits_with_zero_interaction(tmp_path):
    """AE4: two clones edit disjoint symbols; sync merges with zero interaction and no fork."""
    a, b = _two_clones(tmp_path, _BASE)

    _edit_and_commit(a, "main.py", "def foo():\n    return 100\n\n\ndef bar():\n    return 2\n", "bump foo")
    _push(a)
    _edit_and_commit(b, "main.py", "def foo():\n    return 1\n\n\ndef bar():\n    return 200\n", "bump bar")

    report = sync.sync(b, remote="origin", branch="main")

    assert report.merged
    assert not report.forks
    text = (b / "main.py").read_text(encoding="utf-8")
    assert "return 100" in text  # A's edit
    assert "return 200" in text  # B's edit, folded together with zero interaction


def test_sync_is_idempotent_and_double_mine_is_deterministic(tmp_path):
    a, b = _two_clones(tmp_path, _BASE)
    _edit_and_commit(a, "main.py", "def foo():\n    return 100\n\n\ndef bar():\n    return 2\n", "bump foo")
    _push(a)

    first = sync.sync(b, remote="origin", branch="main")
    assert first.merged

    ideal_after_sync = lens.get(b)  # re-mining B's own just-written merge commit is a no-op
    assert ideal_after_sync.op_ids == lens.current_ideal(b).op_ids

    second = sync.sync(b, remote="origin", branch="main")
    assert not second.merged
    assert second.message == "already up to date"


def test_sync_records_a_fork_and_lands_the_forked_symbol_at_the_common_ancestor(tmp_path):
    """Divergence-as-state (U20/C4, updated from the pre-U20 abort-on-fork behavior): a same-symbol
    fork no longer aborts the sync. Here the *only* divergence is the forked symbol, so there is no
    fork-free advance -- but the fork is still recorded as durable, committed `.sgt/forks.json`
    state, and the forked symbol materializes at the pre-fork common ancestor (never either tip).
    `merged` is False (an open fork needs attention) even though the reconciling merge commit
    lands."""
    a, b = _two_clones(tmp_path, _BASE)

    _edit_and_commit(a, "main.py", "def foo():\n    return 999\n\n\ndef bar():\n    return 2\n", "A: rework foo")
    _push(a)
    _edit_and_commit(b, "main.py", "def foo():\n    return 42\n\n\ndef bar():\n    return 2\n", "B: rework foo")

    gb = GitBinding(b)
    before_head = gb.head()

    report = sync.sync(b, remote="origin", branch="main")

    assert not report.merged  # an open fork -- attention needed
    assert "merge-op" in report.message
    assert len(report.forks) == 1
    symbol, _tip_a, _tip_b = report.forks[0]
    assert symbol == "main.py::foo"

    assert (b / ".sgt" / "forks.json").is_file()  # the fork is durable, shared state (LAW-R)
    text = (b / "main.py").read_text(encoding="utf-8")
    assert "return 1" in text  # the forked symbol sits at the common ancestor...
    assert "return 42" not in text and "return 999" not in text  # ...never either tip
    assert gb.is_clean()  # the reconciling merge landed cleanly, not left half-applied
    assert gb.head() == report.merge_sha and gb.head() != before_head  # branch advanced past the fork


def test_forks_view_and_fork_detail_view_over_a_real_fork(tmp_path):
    """`forks_view` adds a cheap `file` field per record; `fork_detail_view` folds each tip on its
    own downward closure (`order.downset`) so a resolution UI sees both sides' real content -- the
    forked symbol itself sits at the pre-fork common ancestor in `code(current_ideal)`, never
    either tip, so this is the only way to see what each side actually wrote."""
    from sgt.api import fork_detail_view, forks_view

    a, b = _two_clones(tmp_path, _BASE)
    _edit_and_commit(a, "main.py", "def foo():\n    return 999\n\n\ndef bar():\n    return 2\n", "A: rework foo")
    _push(a)
    _edit_and_commit(b, "main.py", "def foo():\n    return 42\n\n\ndef bar():\n    return 2\n", "B: rework foo")
    report = sync.sync(b, remote="origin", branch="main")
    assert len(report.forks) == 1
    _symbol, tip_a, tip_b = report.forks[0]

    fv = forks_view(b)
    assert fv["open"] == 1
    assert fv["forks"][0]["symbol"] == "main.py::foo"
    assert fv["forks"][0]["file"] == "main.py"
    assert fv["forks"][0]["tips"] == [tip_a, tip_b]  # untouched, still there alongside `file`

    detail = fork_detail_view(b, "main.py::foo")
    assert [t["op_id"] for t in detail["tips"]] == [tip_a, tip_b]
    contents = {t["op_id"]: t["files"]["main.py"] for t in detail["tips"]}
    assert "return 999" in contents[tip_a]
    assert "return 42" in contents[tip_b]
    assert detail["remedy"] == fv["forks"][0]["remedy"]

    assert fork_detail_view(b, "main.py::no_such_symbol") == {
        "error": "no open fork for 'main.py::no_such_symbol'", "symbol": "main.py::no_such_symbol",
    }


def test_resolve_cli_lands_a_fork_with_a_manual_override_when_no_oracle_is_configured(tmp_path):
    """The guided `sgt resolve <sym> --apply` must be completable on the common path where no test
    runner is configured. The land gate's own refusal names "land with an override", so the verb
    forwards `--override pass` to `land()` (and skips the pointless pre-land `oracle.run` that would
    otherwise reset the verdict to pending). Regression: before the override forwarding, an
    unconfigured oracle left the guided verb with no way to close a fork it had drafted."""
    from sgt.api import forks_view
    from sgt.cli.resolve import _resolve

    a, b = _two_clones(tmp_path, _BASE)
    _edit_and_commit(a, "main.py", "def foo():\n    return 999\n\n\ndef bar():\n    return 2\n", "A: rework foo")
    _push(a)
    _edit_and_commit(b, "main.py", "def foo():\n    return 42\n\n\ndef bar():\n    return 2\n", "B: rework foo")
    report = sync.sync(b, remote="origin", branch="main")
    assert len(report.forks) == 1
    symbol = report.forks[0][0]  # main.py::foo
    assert forks_view(b)["open"] == 1

    # draft the reconciliation, then hand-merge both sides into the working tree
    assert _resolve(str(b), symbol, apply=False, as_json=False) == 0
    (b / "main.py").write_text(
        "def foo():\n    return 999 + 42\n\n\ndef bar():\n    return 2\n", encoding="utf-8"
    )

    rc = _resolve(str(b), symbol, apply=True, as_json=False,
                  override="pass", reason="both diffs reconciled by hand", by="reviewer")
    assert rc == 0
    assert forks_view(b)["open"] == 0  # the fork is closed


def test_sync_dedups_an_op_independently_mined_on_both_clones(tmp_path):
    """The identification law at sync time: the same symbol added identically on both clones
    mines to one op id on each side; the union must not double it, and must keep both sides'
    provenance (git's own `-X ours` merge would otherwise drop one side's witness commit)."""
    a, b = _two_clones(tmp_path, _BASE)
    baz = _BASE + "\n\ndef baz():\n    return 42\n"

    a_sha = _edit_and_commit(a, "main.py", baz, "A: add baz")
    _push(a)
    b_sha = _edit_and_commit(b, "main.py", baz, "B: add baz (same content, independently)")

    before_ids = {op.id for op in Store(b).all_ops()}
    report = sync.sync(b, remote="origin", branch="main")

    assert report.merged
    assert not report.forks
    after_ids = {op.id for op in Store(b).all_ops()}
    assert after_ids == before_ids  # zero new op ids -- both sides had already identified it
    assert report.ops_added == 0

    baz_op = next(op for op in Store(b).all_ops() if "main.py::baz" in op.footprint)
    assert {a_sha, b_sha} <= set(baz_op.provenance)


def test_sync_reports_a_pin_contradiction_and_still_merges(tmp_path):
    a, b = _two_clones(tmp_path, _BASE)

    save_pins(a, Pins(assign={"m1": "featureA", "m2": "featureB"}))
    GitBinding(a).commit_all("A: pin m1/m2 to separate features")
    _push(a)

    save_pins(b, Pins(must_link=frozenset({("m1", "m2")})))
    GitBinding(b).commit_all("B: must-link m1 and m2")

    report = sync.sync(b, remote="origin", branch="main")

    assert report.merged
    assert len(report.pin_contradictions) == 1
    contradiction = report.pin_contradictions[0]
    assert contradiction.kind == "assign_conflict_in_must_link_group"

    unioned = load_pins(b)
    assert unioned.must_link == frozenset({("m1", "m2")})
    assert unioned.assign == {"m1": "featureA", "m2": "featureB"}


def test_sync_reports_a_declared_edge_cycle_and_declared_edges_travel(tmp_path):
    a, b = _two_clones(tmp_path, _BASE)

    verbs.after(a, "main.py::foo", "main.py::bar")  # A declares foo <= bar
    GitBinding(a).commit_all("A: declare foo <= bar")
    _push(a)

    verbs.after(b, "main.py::bar", "main.py::foo")  # B declares bar <= foo -- a cycle once unioned
    GitBinding(b).commit_all("B: declare bar <= foo")

    report = sync.sync(b, remote="origin", branch="main")

    assert report.merged
    assert len(report.declared_cycles) > 0

    ops = Store(b).all_ops()
    foo_id = next(op.id for op in ops if "main.py::foo" in op.footprint)
    bar_id = next(op.id for op in ops if "main.py::bar" in op.footprint)
    declared = lens._load_declared(b)
    assert (foo_id, bar_id) in declared  # A's declared edge travelled to B post-sync
    assert (bar_id, foo_id) in declared


# -- U8: three-way resolve -- reverts travel, sync stops resurrecting removed work ---------------

_WITH_BAZ = _BASE + "\n\ndef baz():\n    return 42\n"


def test_sync_revert_travels_and_removes_the_bytes(tmp_path):
    """The review's resurrection reproduction, inverted (U8/R10-R11): A adds baz, B syncs it, then A
    *reverts* baz and pushes. On B's next sync the revert travels -- baz leaves B's ideal *and* its
    bytes leave the working tree -- instead of the blind union resurrecting it from B's own side."""
    a, b = _two_clones(tmp_path, _BASE)
    _edit_and_commit(a, "main.py", _WITH_BAZ, "A: add baz")
    _push(a)
    sync.sync(b, remote="origin", branch="main")
    assert "def baz" in (b / "main.py").read_text(encoding="utf-8")  # B has it after the first sync

    baz_op = next(o for o in Store(a).all_ops() if "main.py::baz" in o.footprint)
    verbs.revert(a, baz_op.id)  # A reverts baz on its own clone
    _push(a)

    sync.sync(b, remote="origin", branch="main")
    assert "def baz" not in (b / "main.py").read_text(encoding="utf-8")  # the revert traveled
    assert baz_op.id not in lens.current_ideal(b).op_ids  # ...in the ideal, not just the bytes


def test_sync_revert_of_a_base_op_removes_the_dependents_that_rode_its_upset(tmp_path):
    """Scenario 3: A reverts a base op while B extended that op's symbol. The extension rides the
    reverted op's up-set and is removed with it (it stops being grounded once its base is gone) --
    B's work is not silently duplicated onto a resurrected base."""
    a, b = _two_clones(tmp_path, _BASE)
    _edit_and_commit(a, "main.py", _WITH_BAZ, "A: add baz")  # base op for baz
    _push(a)
    sync.sync(b, remote="origin", branch="main")

    # B extends baz (a new op chaining onto A's baz), A reverts baz's original add.
    _edit_and_commit(b, "main.py", _BASE + "\n\ndef baz():\n    return 43\n", "B: bump baz")
    baz_add = next(o for o in Store(a).all_ops() if "main.py::baz" in o.footprint)
    verbs.revert(a, baz_add.id)
    _push(a)

    sync.sync(b, remote="origin", branch="main")
    # baz's whole chain (add + B's extension) leaves the ideal: reverting the base removes its up-set.
    live = lens.current_ideal(b)
    assert not any("main.py::baz" in Store(b).get(oid).footprint for oid in live.op_ids)
    assert "def baz" not in (b / "main.py").read_text(encoding="utf-8")


def test_revert_travels_to_a_fresh_clone_and_does_not_resurrect(tmp_path):
    """Phase 1.2 §E (shared exclusion CRDT): a revert must survive a *fresh clone's* cold bootstrap.
    A adds baz then reverts it and pushes; a brand-new clone C then bootstraps from the shared state
    ref + branch history. The reverted op is still in git history, so C's cold mine re-adds it via
    provenance -- the exclusion OR-Set is the only record that it was reverted, and it must travel on
    `refs/sgt/state` for C to subtract it. Before exclusions travelled, C's exclusion table was empty
    and baz silently resurrected in C's ideal (F20), diverging from A -- a later fold would even
    write its bytes back."""
    remote = _init_bare(tmp_path)
    a = _clone(remote, tmp_path / "a")
    lens.init(a)
    _edit_and_commit(a, "main.py", _WITH_BAZ, "A: init with baz")
    _push(a)

    baz_op = next(o for o in Store(a).all_ops() if "main.py::baz" in o.footprint)
    verbs.revert(a, baz_op.id)  # A reverts baz on its own clone
    _push(a)
    assert baz_op.id not in lens.current_ideal(a).op_ids

    c = _clone(remote, tmp_path / "c")  # a brand-new teammate, no local state to inherit the revert
    lens.get(c)  # cold bootstrap: mine branch history + read the shared state ref
    # The exclusion travelled on the ref, so C subtracts baz rather than resurrecting it by provenance.
    assert baz_op.id not in lens.current_ideal(c).op_ids
    assert "def baz" not in (c / "main.py").read_text(encoding="utf-8")


# -- plan_sync: the side-effect-free dry run behind the `sgt sync` feedforward pane ------------

def _committed_state(repo: Path) -> dict[str, bytes]:
    """A byte snapshot of everything a sync would materialize: `main.py` plus every committed `.sgt`
    file, excluding the append-only op store (`.sgt/ops/`) and the derived local caches
    (`.sgt/local/`) that `plan_sync` rolls back on its own. A dry run must leave this byte-identical."""
    state_map: dict[str, bytes] = {}
    main = repo / "main.py"
    if main.is_file():
        state_map["main.py"] = main.read_bytes()
    for p in sorted((repo / ".sgt").rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(repo).as_posix()
        if rel.startswith(".sgt/ops/") or rel.startswith(".sgt/local/") or p.name.startswith(".tmp-"):
            continue
        state_map[rel] = p.read_bytes()
    return state_map


def test_plan_sync_predicts_the_fold_and_leaves_no_trace(tmp_path):
    """The dry run (`fetch -> ingest -> resolve`, no `materialize`) reports the op count a sync would
    fold in, and -- like a blocked land (R7) -- rolls back so the working tree, the committed `.sgt`
    state, and the local branch tip stay byte-identical. A following real sync folds in exactly the
    predicted op count."""
    a, b = _two_clones(tmp_path, _BASE)
    _edit_and_commit(a, "main.py", "def foo():\n    return 100\n\n\ndef bar():\n    return 2\n", "bump foo")
    _push(a)

    gb = GitBinding(b)
    before_head = gb.head()
    lens.get(b)  # settle mine-on-contact caches first, so the snapshot below is the steady state
    before_state = _committed_state(b)

    plan = sync.plan_sync(b, remote="origin", branch="main")

    assert plan.ops_added > 0 and not plan.forks
    assert gb.head() == before_head  # the local branch did not move
    assert _committed_state(b) == before_state  # no trace (R7)

    report = sync.sync(b, remote="origin", branch="main")
    assert report.merged and report.ops_added == plan.ops_added  # the dry run predicted the real fold


def test_plan_sync_short_circuits_when_already_up_to_date(tmp_path):
    """Nothing new on the remote -> the fetch short-circuits and the plan reports `up_to_date`
    without running ingest/resolve, so the pane can say 'nothing to fold in' before the merge path."""
    a, b = _two_clones(tmp_path, _BASE)
    _edit_and_commit(a, "main.py", "def foo():\n    return 100\n\n\ndef bar():\n    return 2\n", "bump foo")
    _push(a)
    sync.sync(b, remote="origin", branch="main")  # b is now current with the remote

    plan = sync.plan_sync(b, remote="origin", branch="main")
    assert plan.up_to_date and plan.ops_added == 0 and not plan.forks


def test_plan_sync_surfaces_a_would_be_fork_without_recording_it(tmp_path):
    """A same-symbol divergence *surfaces* in the plan (`forks` non-empty) so the pane can warn, but
    the dry run must not write the committed `.sgt/forks.json` a real sync would -- the fork is
    predicted, not yet recorded."""
    a, b = _two_clones(tmp_path, _BASE)
    _edit_and_commit(a, "main.py", "def foo():\n    return 999\n\n\ndef bar():\n    return 2\n", "A: rework foo")
    _push(a)
    _edit_and_commit(b, "main.py", "def foo():\n    return 42\n\n\ndef bar():\n    return 2\n", "B: rework foo")

    plan = sync.plan_sync(b, remote="origin", branch="main")
    assert len(plan.forks) == 1 and plan.forks[0][0] == "main.py::foo"
    assert not (b / ".sgt" / "forks.json").is_file()  # nothing recorded -- a dry run leaves no fork state


# -- `sgt sync` CLI gate: the pane is the confirm step on a tty, immediate-apply otherwise --------

def _diverge_for_sync(tmp_path) -> Path:
    """A/B two clones with disjoint edits pushed on A, so a `sync` on B has real fork-free work to
    fold in (the CLI gate has something to confirm)."""
    a, b = _two_clones(tmp_path, _BASE)
    _edit_and_commit(a, "main.py", "def foo():\n    return 100\n\n\ndef bar():\n    return 2\n", "bump foo")
    _push(a)
    return b


def test_sync_cli_non_tty_merges_immediately(tmp_path):
    """The machine/CI contract: `sgt sync` on a non-tty (pytest's captured streams) skips the
    consequence confirm and folds the teammate's work in exactly as before the pane existed."""
    from sgt.cli.sync import _sync

    b = _diverge_for_sync(tmp_path)
    before = GitBinding(b).head()
    assert _sync(str(b), "origin", "main", as_json=False) == 0
    assert GitBinding(b).head() != before  # merged


def test_sync_cli_tty_abort_merges_nothing(tmp_path, monkeypatch, capsys):
    """On a tty, the pane is the confirm: an abort leaves the local branch frozen (rc 1)."""
    import sys

    from sgt.cli import _common
    from sgt.cli.sync import _sync
    from sgt.tui.consequence import Decision

    b = _diverge_for_sync(tmp_path)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True, raising=False)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True, raising=False)
    monkeypatch.setattr(_common, "maybe_confirm", lambda *a, **k: Decision(False))

    before = GitBinding(b).head()
    assert _sync(str(b), "origin", "main", as_json=False) == 1
    assert "aborted" in capsys.readouterr().out
    assert GitBinding(b).head() == before  # frozen


def test_sync_cli_tty_confirm_merges(tmp_path, monkeypatch):
    """A confirm on a tty runs the real sync and advances the local branch."""
    import sys

    from sgt.cli import _common
    from sgt.cli.sync import _sync
    from sgt.tui.consequence import Decision

    b = _diverge_for_sync(tmp_path)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True, raising=False)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True, raising=False)
    monkeypatch.setattr(_common, "maybe_confirm", lambda *a, **k: Decision(True))

    before = GitBinding(b).head()
    assert _sync(str(b), "origin", "main", as_json=False) == 0
    assert GitBinding(b).head() != before  # merged


def test_sync_cli_json_never_confirms(tmp_path, monkeypatch):
    """`--json` keeps its immediate-apply contract even on a tty: the pane never launches."""
    import sys

    from sgt.cli import _common
    from sgt.cli.sync import _sync

    b = _diverge_for_sync(tmp_path)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True, raising=False)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True, raising=False)

    def boom(*a, **k):
        raise AssertionError("--json must not launch the consequence pane")

    monkeypatch.setattr(_common, "maybe_confirm", boom)
    assert _sync(str(b), "origin", "main", as_json=True) == 0


def _bare_head(remote: Path, branch: str = "main") -> str | None:
    import subprocess
    proc = subprocess.run(
        ["git", "-C", str(remote), "rev-parse", branch], capture_output=True, text=True, check=False
    )
    return proc.stdout.strip() if proc.returncode == 0 else None


def test_push_aborts_the_branch_when_state_ref_cannot_publish(tmp_path, monkeypatch):
    """Push-ordering invariant (§D, Step 6): a branch commit's `Sgt-Op:` trailers name ops that live
    only on `refs/sgt/state`, so if that ref cannot be published to the remote, `sgt push` must NOT
    push the branch -- else the branch references ops that aren't durable. Simulate an unrecoverable
    state push and assert the remote branch does not move."""
    from sgt.cli.sync import _push
    from sgt.core.sync import state_ref

    remote = tmp_path / "remote.git"
    a, _b = _two_clones(tmp_path, _BASE)
    _edit_and_commit(a, "main.py", "def foo():\n    return 42\n\n\ndef bar():\n    return 2\n", "bump foo")
    remote_before = _bare_head(remote)

    def _boom(*args, **kwargs):
        raise state_ref.StateRefError("simulated unrecoverable state push")

    monkeypatch.setattr(state_ref, "publish_and_push", _boom)

    assert _push(str(a), "origin", "main", as_json=False) != 0
    assert _bare_head(remote) == remote_before  # the branch push never ran


def test_pre_1_2_remote_without_state_ref_falls_back_to_mining(tmp_path):
    """Backward-compat bootstrap (§D): a remote predating Phase 1.2 has no `refs/sgt/state`. A fresh
    clone can't fetch it, but the branch history still carries every `Sgt-Op:` trailer, so `lens.get`
    reconstructs the ideal by mining from branch history -- never an error, just cold mining."""
    import subprocess

    from sgt.core.sync import state_ref

    remote = _init_bare(tmp_path)
    a = _clone(remote, tmp_path / "a")
    lens.init(a)
    _edit_and_commit(a, "main.py", _BASE, "init")
    _edit_and_commit(a, "main.py", "def foo():\n    return 100\n\n\ndef bar():\n    return 2\n", "bump foo")
    # Push ONLY the branch -- simulate a pre-1.2 remote that never learned about refs/sgt/state.
    subprocess.run(["git", "-C", str(a), "push", "-q", "origin", "main"], check=True, capture_output=True)
    a_ideal = lens.get(a)

    b = _clone(remote, tmp_path / "b")  # the remote has no state ref, so `_clone` fetches nothing
    assert state_ref.read_sha(GitBinding(b)) is None  # the bootstrap precondition holds
    b_ideal = lens.get(b)  # must mine from branch history rather than error

    assert b_ideal.op_ids == a_ideal.op_ids  # cold mining re-derives the same content-addressed ideal
