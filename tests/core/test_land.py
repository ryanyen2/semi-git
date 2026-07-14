"""Tests for `sgt land` -- the U23 SYNC-2 CAS-gated shared-branch advance (plan C9/LAW-G).

The load-bearing surface is concurrency: two sessions racing to advance one shared branch record.
The concurrency tests run each lander in its own `git worktree` of a shared *bare* repo, which is
exactly the model the store-lock audit prescribed -- the worktrees share the bare repo's ref store
(so `git update-ref refs/heads/main <new> <old>` from either contends on the *same* ref: genuine
compare-and-swap contention), while each has its own working tree and index (so materializing the
reconciled union into one never clobbers the other). A filesystem barrier makes both landers reach
the CAS at nearly the same instant, so a round is genuinely contended. Real `git` subprocesses, a
trivial `exit 0`/`exit 1` oracle, no network, no LLM.
"""

from __future__ import annotations

import json
import multiprocessing as mp
import subprocess
import time
from pathlib import Path

from sgt import state
from sgt.core import lens, oracle, sync
from sgt.core.store import Store, fsck
from sgt.store.gitbind import GitBinding

_BASE = "def foo():\n    return 1\n\n\ndef bar():\n    return 2\n"


def _seed_shared(root: Path, oracle_cmd: str | None = "exit 0") -> Path:
    """A shared *bare* repo whose `main` carries `Sgt-Op:` trailers and (optionally) a committed
    one-tier oracle. Built via a throwaway normal clone, then `git clone --bare` for the shared ref
    store every worktree contends on."""
    src = root / "src"
    GitBinding(src).init()
    (src / "main.py").write_text(_BASE, encoding="utf-8")
    if oracle_cmd is not None:
        state.save_json(src, "oracle_config", {"tiers": [{"name": "gate", "command": oracle_cmd}]})
    GitBinding(src).commit_all("init")
    ideal = lens.get(src)
    put_sha = lens.put(src, ideal, message="sgt: init")
    lens.record_ideal(src, ideal, put_sha)

    bare = root / "shared.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(src), str(bare)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(bare), "config", "user.email", "sgt@semi-git.local"], check=True)
    subprocess.run(["git", "-C", str(bare), "config", "user.name", "semi-git"], check=True)
    return bare


def _add_worktree(bare: Path, path: Path, at_commit: str) -> None:
    subprocess.run(
        ["git", "-C", str(bare), "worktree", "add", "-q", "--detach", str(path), at_commit],
        check=True, capture_output=True,
    )


def _remove_worktree(bare: Path, path: Path) -> None:
    subprocess.run(
        ["git", "-C", str(bare), "worktree", "remove", "--force", str(path)],
        check=False, capture_output=True,
    )


def _stage_local_op(wt: Path, symbol: str, content: str | None = None) -> None:
    """In worktree `wt` (detached at the branch tip), commit a new op that diverges from the shared
    branch tip -- a session's local work that `land` will union onto the branch."""
    src = (wt / "main.py").read_text(encoding="utf-8")
    new = content if content is not None else src + f"\n\ndef {symbol}():\n    return 0\n"
    (wt / "main.py").write_text(new, encoding="utf-8")
    GitBinding(wt).init()  # ensure committer identity (idempotent; shares the bare repo's config)
    GitBinding(wt).commit_all(f"add {symbol}")
    ideal = lens.get(wt)
    put_sha = lens.put(wt, ideal, message=f"sgt: add {symbol}")
    lens.record_ideal(wt, ideal, put_sha)


def _lander_worker(worktree: str, symbol: str, content: str | None, ready_dir: str, out_path: str) -> None:
    """A separate process: stage a local op, wait at the filesystem barrier for the peer, then
    `land`. Writes the LandReport essentials to `out_path` (including any exception)."""
    wt = Path(worktree)
    try:
        _stage_local_op(wt, symbol, content)
        Path(ready_dir, symbol).write_text("ready", encoding="utf-8")
        deadline = time.monotonic() + 15
        while len(list(Path(ready_dir).iterdir())) < 2 and time.monotonic() < deadline:
            time.sleep(0.001)
        report = sync.land(wt, branch="main")
        result = {
            "landed": report.landed, "attempts": report.attempts, "land_sha": report.land_sha,
            "blocked_reason": report.blocked_reason, "forks": [list(t) for t in report.forks],
            "ops_added": report.ops_added,
        }
    except BaseException as e:  # surface any failure to the parent rather than a silent exitcode
        result = {"error": f"{type(e).__name__}: {e}"}
    Path(out_path).write_text(json.dumps(result), encoding="utf-8")


def _run_round(bare: Path, root: Path, rnd: int, sym_a: str, sym_b: str,
               content_a: str | None = None, content_b: str | None = None) -> tuple[dict, dict]:
    """Two landers race to advance `main` from its current tip, each in its own worktree."""
    tip = GitBinding(bare).rev_parse("refs/heads/main")
    wt_a, wt_b = root / f"wt{rnd}a", root / f"wt{rnd}b"
    _add_worktree(bare, wt_a, tip)
    _add_worktree(bare, wt_b, tip)
    ready = root / f"ready{rnd}"
    ready.mkdir()
    out_a, out_b = root / f"out{rnd}a.json", root / f"out{rnd}b.json"

    ctx = mp.get_context("spawn")
    pa = ctx.Process(target=_lander_worker, args=(str(wt_a), sym_a, content_a, str(ready), str(out_a)))
    pb = ctx.Process(target=_lander_worker, args=(str(wt_b), sym_b, content_b, str(ready), str(out_b)))
    pa.start(); pb.start(); pa.join(); pb.join()

    ra, rb = json.loads(out_a.read_text()), json.loads(out_b.read_text())
    _remove_worktree(bare, wt_a)
    _remove_worktree(bare, wt_b)
    return ra, rb


# -- single-process paths (fast, always run) ---------------------------------------------------

def test_land_happy_path_advances_the_branch_to_a_green_op_set(tmp_path):
    """One session lands its local op onto the shared branch: the ref advances to a real 2-parent
    merge commit whose op-set is oracle-green, and the store stays fsck-clean."""
    bare = _seed_shared(tmp_path, oracle_cmd="exit 0")
    wt = tmp_path / "wt"
    _add_worktree(bare, wt, GitBinding(bare).rev_parse("refs/heads/main"))
    _stage_local_op(wt, "baz")

    before = GitBinding(bare).rev_parse("refs/heads/main")
    report = sync.land(wt, branch="main")
    after = GitBinding(bare).rev_parse("refs/heads/main")

    assert report.landed and report.land_sha == after
    assert after != before  # the shared tip advanced
    assert report.ops_added > 0
    parents = GitBinding(wt)._git("rev-list", "--parents", "-n", "1", after).stdout.split()
    assert len(parents) == 3  # a real 2-parent merge (branch tip + this session's HEAD)
    assert fsck(wt).ok
    assert "def baz" in GitBinding(wt).file_at(after, "main.py")  # the landed op is in the tree


def test_land_blocked_on_a_red_oracle_does_not_move_the_tip(tmp_path):
    """LAW-G at the verb level: a red oracle refuses the land and the shared tip does not move."""
    bare = _seed_shared(tmp_path, oracle_cmd="exit 1")
    wt = tmp_path / "wt"
    _add_worktree(bare, wt, GitBinding(bare).rev_parse("refs/heads/main"))
    _stage_local_op(wt, "baz")

    before = GitBinding(bare).rev_parse("refs/heads/main")
    report = sync.land(wt, branch="main")

    assert not report.landed and report.land_sha is None
    assert "oracle-green" in report.blocked_reason
    assert GitBinding(bare).rev_parse("refs/heads/main") == before  # tip frozen


def _worktree_state(repo: Path) -> dict[str, bytes]:
    """A byte snapshot of everything a land might mutate: `main.py` plus every `.sgt` file except
    the monotone op store (`.sgt/ops/`, append-only) and the local verdict cache (`.sgt/local/
    oracle.json`, the legitimate product of running the oracle) and the lock file. A transactional
    land that does not land must leave this snapshot byte-identical (R7)."""
    state_map: dict[str, bytes] = {}
    main = repo / "main.py"
    if main.is_file():
        state_map["main.py"] = main.read_bytes()
    sgt = repo / ".sgt"
    for p in sorted(sgt.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(repo).as_posix()
        if rel.startswith(".sgt/ops/") or rel in (
            ".sgt/local/oracle.json", ".sgt/local/lock", ".sgt/local/land_pending.json"
        ):
            continue
        if p.name.startswith(".tmp-"):
            continue
        state_map[rel] = p.read_bytes()
    return state_map


def test_red_land_leaves_no_trace_transactional(tmp_path):
    """R7 (U5): a land blocked by a red oracle rolls back completely -- the working tree is
    byte-identical to pre-land and not one reconciled `.sgt` artifact (pins/declared/aliases/tree/
    forks/ideal or the ideal table) was written. The review's reproduction: a red-gated land used
    to leave six mutated `.sgt` artifacts plus a rewritten tree."""
    bare = _seed_shared(tmp_path, oracle_cmd="exit 1")
    wt = tmp_path / "wt"
    _add_worktree(bare, wt, GitBinding(bare).rev_parse("refs/heads/main"))
    _stage_local_op(wt, "baz")  # a real local op that a green land would union onto the branch

    before = _worktree_state(wt)
    report = sync.land(wt, branch="main")
    after = _worktree_state(wt)

    assert not report.landed and "oracle-green" in report.blocked_reason
    assert after == before, (
        "red land mutated state that should have rolled back: "
        f"{sorted(set(before) ^ set(after)) or [k for k in before if before[k] != after.get(k)]}"
    )


def test_land_refuses_without_an_oracle(tmp_path):
    """No oracle configured -> a green verdict cannot exist, so `land` refuses (LAW-G) with zero
    mutation: not even a monotone op is added before the refusal (U5)."""
    bare = _seed_shared(tmp_path, oracle_cmd=None)
    wt = tmp_path / "wt"
    _add_worktree(bare, wt, GitBinding(bare).rev_parse("refs/heads/main"))
    _stage_local_op(wt, "baz")

    before = GitBinding(bare).rev_parse("refs/heads/main")
    before_state = _worktree_state(wt)
    report = sync.land(wt, branch="main")

    assert not report.landed and "no oracle configured" in report.blocked_reason
    assert GitBinding(bare).rev_parse("refs/heads/main") == before
    assert _worktree_state(wt) == before_state  # refused before touching anything


def test_cas_exhaustion_restores_and_persists_nothing(tmp_path, monkeypatch):
    """R7 (U5): when every CAS attempt loses (forced here), the land reports contention and rolls
    back completely -- the shared tip is frozen and no reconciled artifact survives."""
    bare = _seed_shared(tmp_path, oracle_cmd="exit 0")
    wt = tmp_path / "wt"
    _add_worktree(bare, wt, GitBinding(bare).rev_parse("refs/heads/main"))
    _stage_local_op(wt, "baz")

    before_tip = GitBinding(bare).rev_parse("refs/heads/main")
    before_state = _worktree_state(wt)
    monkeypatch.setattr(GitBinding, "update_ref_cas", lambda self, ref, new, old: False)
    report = sync.land(wt, branch="main", retries=3)

    assert not report.landed and "contention" in report.blocked_reason and report.attempts == 3
    assert GitBinding(bare).rev_parse("refs/heads/main") == before_tip  # tip frozen
    assert _worktree_state(wt) == before_state  # every attempt's flush was rolled back


def test_crashed_land_is_recovered_on_next_land(tmp_path):
    """R7 (U5): a `land` that crashed after materializing its candidate tree but before the CAS
    leaves a `land_pending` journal; `fsck` names the interrupted ref, and the next `land` rolls
    the working tree back to the journaled snapshot before proceeding to land cleanly."""
    bare = _seed_shared(tmp_path, oracle_cmd="exit 0")
    wt = tmp_path / "wt"
    _add_worktree(bare, wt, GitBinding(bare).rev_parse("refs/heads/main"))
    _stage_local_op(wt, "baz")
    snapshot = GitBinding(wt).head()

    # simulate the crash: a half-written candidate tree plus the surviving pending journal
    (wt / "main.py").write_text("HALF-WRITTEN CANDIDATE\n", encoding="utf-8")
    state.save_json(wt, "land_pending", {"ref": "refs/heads/main", "snapshot": snapshot})
    assert fsck(wt).pending_land == ("refs/heads/main",)  # fsck names the interrupted state

    report = sync.land(wt, branch="main")
    assert report.landed
    assert fsck(wt).pending_land == ()  # journal cleared
    assert "HALF-WRITTEN" not in (wt / "main.py").read_text(encoding="utf-8")  # rolled back first


# -- checked-out vs other-branch landing (plan U5) ---------------------------------------------

def _seed_checked_out(root: Path) -> tuple[GitBinding, str]:
    """A normal (non-bare) repo checked out on its default branch, seeded with one green op-set.
    Returns the binding and the checked-out ref (e.g. `refs/heads/main`)."""
    from sgt.store.gitbind import init_store
    gb, _ = init_store(root)
    (root / "main.py").write_text(_BASE, encoding="utf-8")
    state.save_json(root, "oracle_config", {"tiers": [{"name": "gate", "command": "exit 0"}]})
    gb.commit_all("init")
    ideal = lens.get(root)
    put = lens.put(root, ideal, message="sgt: init")
    lens.record_ideal(root, ideal, put)
    return gb, gb.symbolic_ref()


def _commit_op(gb: GitBinding, root: Path, symbol: str) -> str:
    """Commit a new op onto the checked-out branch via the normal put path (so `.sgt/ops` is
    tracked and the tree is clean afterward). Returns the post-put HEAD."""
    (root / "main.py").write_text(
        (root / "main.py").read_text(encoding="utf-8") + f"\n\ndef {symbol}():\n    return 0\n",
        encoding="utf-8",
    )
    gb.commit_all(f"add {symbol}")
    ideal = lens.get(root)
    put = lens.put(root, ideal, message=f"sgt: add {symbol}")
    lens.record_ideal(root, ideal, put)
    return put


def test_green_land_on_checked_out_ref_leaves_no_phantom_diff(tmp_path):
    """U5: landing the checked-out branch leaves working tree, index, and the moved ref in
    agreement -- `git status` is clean afterward (no phantom diff)."""
    repo = tmp_path / "repo"
    gb, ref = _seed_checked_out(repo)
    _commit_op(gb, repo, "baz")
    branch = ref.rsplit("/", 1)[-1]

    report = sync.land(repo, branch=branch)

    assert report.landed
    assert gb.rev_parse(ref) == report.land_sha  # the checked-out ref advanced to the landed commit
    assert gb.head() == report.land_sha          # HEAD followed it (symbolic ref)
    assert gb.is_clean()                          # no phantom git status diff
    assert state.load_json(repo, "witness")[ref] == report.land_sha
    # the landed ideal is left oracle-green, so later verbs see it verified (not pending)
    assert oracle.overall_status(oracle.verdict_for(repo, lens.current_ideal(repo))) == "pass"


def test_land_other_branch_updates_only_that_branch_and_restores_session_tree(tmp_path):
    """U5: landing a *non-checked-out* branch advances only that branch's ref/table/witness, leaves
    the checked-out ref untouched, and restores the session's own working tree (undo stays scoped
    to the checked-out ref, so no journal entry is written for the landed branch)."""
    repo = tmp_path / "repo"
    gb, cur = _seed_checked_out(repo)
    gb._git("branch", "release")           # a target branch at the seed tip, not checked out
    session_head = _commit_op(gb, repo, "baz")  # advance the checked-out branch past release

    session_tree_before = (repo / "main.py").read_bytes()
    wit_before = dict(state.load_json(repo, "witness", default={}))
    report = sync.land(repo, branch="release")

    assert report.landed
    assert gb.rev_parse("refs/heads/release") == report.land_sha  # release advanced
    assert gb.rev_parse(cur) == session_head and gb.head() == session_head  # checked-out ref frozen
    assert gb.is_clean()
    assert (repo / "main.py").read_bytes() == session_tree_before  # session tree restored
    wit_after = state.load_json(repo, "witness", default={})
    assert wit_after["refs/heads/release"] == report.land_sha  # target branch's witness updated
    assert wit_after[cur] == wit_before[cur]                    # checked-out ref's witness untouched
    assert "refs/heads/release" not in state.load_json(repo, "ideal_journal", default={})  # no undo entry


# -- concurrency (the SYNC-2 core) -------------------------------------------------------------

def test_concurrent_disjoint_landers_one_cas_winner_loser_reunions(tmp_path):
    """Two sessions land disjoint ops onto one shared branch, concurrently, over many randomized
    rounds. In every round: both eventually land (disjoint -> no fork), and NOT both can win the
    first CAS (exactly one winner per contended round -- the loser re-unions against the moved tip
    and lands on it). After all rounds the shared tip carries *every* op from *both* landers (no
    lost provenance) and the store is fsck-clean. At least one round genuinely contends."""
    bare = _seed_shared(tmp_path, oracle_cmd="exit 0")
    rounds = 10
    any_contended = False
    for rnd in range(rounds):
        ra, rb = _run_round(bare, tmp_path, rnd, f"a{rnd}", f"b{rnd}")
        assert "error" not in ra, ra.get("error")
        assert "error" not in rb, rb.get("error")
        assert ra["landed"] and rb["landed"]  # disjoint -> both land, no fork
        # exactly one CAS winner: they cannot both have needed a retry against the same old tip.
        assert not (ra["attempts"] > 1 and rb["attempts"] > 1)
        if ra["attempts"] > 1 or rb["attempts"] > 1:
            any_contended = True

    # every op from every round survived onto the shared tip -- no lost update across the races.
    final = tmp_path / "final"
    _add_worktree(bare, final, GitBinding(bare).rev_parse("refs/heads/main"))
    lens.get(final)
    main_py = (final / "main.py").read_text(encoding="utf-8")
    for rnd in range(rounds):
        assert f"def a{rnd}" in main_py and f"def b{rnd}" in main_py
    assert fsck(final).ok
    assert any_contended  # the CAS-retry path was actually exercised


def test_concurrent_same_symbol_landers_one_lands_one_surfaces_a_fork(tmp_path):
    """When two sessions rework the *same* symbol, the union forks: exactly one wins the CAS and
    advances the branch to its own op-set, while the loser -- re-unioning against a tip that now
    holds the winner's conflicting op -- surfaces a genuine fork (blocked, with the `merge-op`
    remedy) rather than advancing over it."""
    bare = _seed_shared(tmp_path, oracle_cmd="exit 0")
    before = GitBinding(bare).rev_parse("refs/heads/main")
    a_content = _BASE.replace("return 1", "return 111")  # both rework foo, differently
    b_content = _BASE.replace("return 1", "return 222")

    ra, rb = _run_round(bare, tmp_path, 0, "foo", "foo", content_a=a_content, content_b=b_content)
    assert "error" not in ra, ra.get("error")
    assert "error" not in rb, rb.get("error")

    landed = [r for r in (ra, rb) if r["landed"]]
    blocked = [r for r in (ra, rb) if not r["landed"]]
    assert len(landed) == 1 and len(blocked) == 1  # one advances, one is blocked by the fork
    assert blocked[0]["forks"]  # the loser surfaced a genuine same-symbol fork
    after = GitBinding(bare).rev_parse("refs/heads/main")
    assert after == landed[0]["land_sha"] and after != before  # tip advanced to the winner only
