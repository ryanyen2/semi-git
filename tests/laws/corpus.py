"""Deterministic corpus for the operation-ideal kernel's round-trip law harness.

The kernel's correctness is defined by the round-trip laws (put-get, get-put, idempotence,
locality, coverage, squash-remine identification, double-machine mining determinism -- plan
docs/plans/2026-07-06-001-feat-operation-ideal-kernel-plan.md R20/R22), not by prose. This module
builds the git repos those laws run against: small synthetic histories exercising the mining edge
cases the plan calls out (rename, cross-file move, a tangled commit, a squash merge, a chain fork,
a non-parseable path, a binary file) via real ``git`` subprocess calls with a pinned author and
fixed commit timestamps, so two independent builds produce byte-identical commit SHAs -- no LLM,
no network, no wall-clock leakage. This mirrors ``tests/golden/corpus.py``'s discipline of
deterministic, offline fixtures, applied to real git history instead of in-memory `Project` state.

A large (>=50k commit) external repo for BET-E (adoption scale) is opt-in only: set
``SGT_LARGE_CORPUS_REPO`` to a local path. This module never clones one itself -- that is a
multi-gigabyte, multi-minute network operation no test run should trigger silently.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

_IDENTITY = {
    "GIT_AUTHOR_NAME": "sgt-corpus",
    "GIT_AUTHOR_EMAIL": "corpus@semi-git.local",
    "GIT_COMMITTER_NAME": "sgt-corpus",
    "GIT_COMMITTER_EMAIL": "corpus@semi-git.local",
}

# Fixed, monotonically increasing commit dates (never `datetime.now()`) so commit SHAs -- which
# fold in author/committer timestamps -- are byte-identical across independent builds.
_BASE_EPOCH = 1_700_000_000


def _at(n: int) -> str:
    return f"{_BASE_EPOCH + n * 3600} +0000"


def _run(repo: Path, *args: str, env_extra: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    env = {**os.environ, **_IDENTITY, **(env_extra or {})}
    proc = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, env=env
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {proc.stderr}")
    return proc


def _init(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    _run(repo, "init", "-q", "-b", "main")
    # Hermetic regardless of the host's global git config -- these are throwaway fixture repos,
    # never the user's own, and a globally-enforced signing key would otherwise hang the suite.
    _run(repo, "config", "commit.gpgsign", "false")
    _run(repo, "config", "user.name", _IDENTITY["GIT_AUTHOR_NAME"])
    _run(repo, "config", "user.email", _IDENTITY["GIT_AUTHOR_EMAIL"])


def _write(repo: Path, path: str, content: str | bytes) -> None:
    full = repo / path
    full.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        full.write_bytes(content)
    else:
        full.write_text(content, encoding="utf-8")


def _commit(repo: Path, message: str, when: int) -> str:
    _run(repo, "add", "-A")
    _run(
        repo,
        "commit",
        "-q",
        "-m",
        message,
        env_extra={"GIT_AUTHOR_DATE": _at(when), "GIT_COMMITTER_DATE": _at(when)},
    )
    return _run(repo, "rev-parse", "HEAD").stdout.strip()


def commit_shas(repo: Path) -> list[str]:
    """Oldest-first commit SHAs on the current branch."""
    out = _run(repo, "log", "--reverse", "--format=%H").stdout
    return [line for line in out.splitlines() if line]


def changed_paths(repo: Path, before: str | None, after: str) -> list[str]:
    base = before if before is not None else "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
    out = _run(repo, "diff", "--name-only", base, after).stdout
    return [line for line in out.splitlines() if line]


def checkout(repo: Path, ref: str) -> None:
    _run(repo, "checkout", "-q", ref)


def tracked_paths(repo: Path, ref: str = "HEAD") -> list[str]:
    out = _run(repo, "ls-tree", "-r", "--name-only", ref).stdout
    return [line for line in out.splitlines() if line]


# --- corpus cases -----------------------------------------------------------------------------


def _case_linear_history(root: Path) -> Path:
    """add -> modify -> rename (same file) -> move (cross file) -> tangled commit (two
    def-use-disjoint symbols in one commit) -> delete -> non-parseable edit -> binary add.
    Exercises U2's tiered identity matcher and whole-file pseudo-symbols end to end."""
    repo = root / "linear_history"
    _init(repo)

    _write(repo, "a.py", "def foo():\n    return 1\n")
    _write(repo, "c.py", "def qux():\n    return 'unrelated'\n")
    _write(repo, "config.yaml", "setting: original\n")
    _write(repo, "logo.bin", bytes([0x89, 0x50, 0x4E, 0x47, 0x00, 0x01, 0x02]))
    _write(repo, "README.md", "# corpus\n")
    _commit(repo, "add foo, qux, config, binary", 0)

    _write(repo, "a.py", "def foo():\n    return 2  # modified body\n")
    _commit(repo, "modify foo", 1)

    _write(repo, "a.py", "def bar():\n    return 2  # modified body\n")
    _commit(repo, "rename foo -> bar within a.py", 2)

    _write(repo, "a.py", "")
    _write(repo, "b.py", "def bar():\n    return 2  # modified body\n")
    _commit(repo, "move bar from a.py to b.py", 3)

    _write(repo, "b.py", "def bar():\n    return 2  # modified body\n\n\ndef baz():\n    return 3\n")
    _write(repo, "c.py", "def qux():\n    return 'changed independently'\n")
    _commit(repo, "tangled: add baz to b.py and edit unrelated qux in c.py", 4)

    _write(repo, "b.py", "def baz():\n    return 3\n")
    _commit(repo, "delete bar", 5)

    _write(repo, "config.yaml", "setting: changed\nextra: true\n")
    _commit(repo, "edit non-parseable config", 6)

    return repo


def _case_squash_merge(root: Path) -> Path:
    """A feature branch mined commit-by-commit, then squash-merged into main reproducing
    byte-identical final content -- AE1 / the identification law (R8): mining the squash commit
    must identify with the already-mined feature ops rather than minting new ones."""
    repo = root / "squash_merge"
    _init(repo)
    _write(repo, "a.py", "def foo():\n    return 1\n")
    _commit(repo, "base", 0)

    _run(repo, "checkout", "-q", "-b", "feature")
    _write(repo, "a.py", "def foo():\n    return 1\n\n\ndef helper():\n    return 2\n")
    _commit(repo, "feature: add helper", 1)
    _write(repo, "a.py", "def foo():\n    return 1\n\n\ndef helper():\n    return 3\n")
    _commit(repo, "feature: tweak helper", 2)
    feature_final = (repo / "a.py").read_bytes()

    _run(repo, "checkout", "-q", "main")
    # Squash: reproduce the feature branch's final bytes in a single commit on main, the way
    # `git merge --squash` or a GitHub squash-merge would -- same content, one commit, no merge
    # parent linking back to the feature branch's individual commits.
    _write(repo, "a.py", feature_final.decode("utf-8"))
    _commit(repo, "squash-merge feature (helper tweak)", 3)

    return repo


def _case_diverged_chain(root: Path) -> Path:
    """Two branches independently edit the same function from the same base version -- a
    genuine chain fork (R5's 'only conflict'), fixture for merge-op / pin / transplant tests."""
    repo = root / "diverged_chain"
    _init(repo)
    _write(repo, "slugify.py", "def slugify(s):\n    return s.lower()\n")
    _commit(repo, "base slugify", 0)

    _run(repo, "checkout", "-q", "-b", "release")
    _write(repo, "slugify.py", "def slugify(s):\n    return s.lower().strip()\n")
    _commit(repo, "release: strip in slugify", 1)

    _run(repo, "checkout", "-q", "main")
    _write(repo, "slugify.py", "def slugify(s):\n    return s.lower().replace(' ', '-')\n")
    _commit(repo, "main: dasherize in slugify", 2)

    return repo


@dataclass(frozen=True)
class CorpusCase:
    name: str
    build: Callable[[Path], Path]
    description: str


CORPUS: dict[str, CorpusCase] = {
    "linear_history": CorpusCase(
        "linear_history", _case_linear_history,
        "add/modify/rename/move/tangle/delete/non-parseable-edit over one linear history",
    ),
    "squash_merge": CorpusCase(
        "squash_merge", _case_squash_merge,
        "feature branch squash-merged into main reproducing identical final bytes",
    ),
    "diverged_chain": CorpusCase(
        "diverged_chain", _case_diverged_chain,
        "two branches independently edit the same symbol from a shared base -- a chain fork",
    ),
}


def self_repo_clone(root: Path) -> Path:
    """A local (no-network) clone of this repo itself, for dogfood-scale smoke checks. Not used
    for byte-exact law assertions -- this repo's history is not test-authored, so its content
    isn't pinned the way the synthetic cases above are."""
    src = Path(__file__).resolve().parents[2]
    dest = root / "self_clone"
    subprocess.run(
        ["git", "clone", "--quiet", "--local", str(src), str(dest)],
        check=True, capture_output=True, text=True,
    )
    return dest


# BET-E budgets (R22): provisional numbers, to be recalibrated from real measurement once
# sgt.core exists and `sgt init` can actually run against the large corpus (see the plan's Open
# Questions -- genesis-horizon default is explicitly deferred to that measurement). Encoded as
# numbers now, not prose, per U1's Verification requirement; tightened in U6/U10.
MAX_INIT_SECONDS_PER_1K_COMMITS = 5.0
MAX_STORE_BYTES_PER_COMMIT = 20_000


def large_corpus_repo() -> Path | None:
    """An opt-in >=50k-commit repo for BET-E. Set ``SGT_LARGE_CORPUS_REPO`` to a local clone;
    this module never fetches one on its own."""
    raw = os.environ.get("SGT_LARGE_CORPUS_REPO")
    if not raw:
        return None
    path = Path(raw)
    return path if path.is_dir() else None
