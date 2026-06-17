"""Binding between the semantic DAG and the underlying git repo.

A node's persistent identity lives in a commit trailer (``Sgt-Node-Id: <id>``),
which survives ``git commit --amend`` and rebase the way Gerrit's Change-Id does
(origin R2). Commits not mapped to any node are detected as out-of-band changes
(origin R4) so the graph never silently drifts from git.
"""

from __future__ import annotations

import subprocess
import uuid
from pathlib import Path

from sgt.store.graph import SemanticGraph

TRAILER_KEY = "Sgt-Node-Id"


class GitError(Exception):
    """A git command failed."""


def new_node_id() -> str:
    """A short, stable node identity."""
    return uuid.uuid4().hex[:12]


def format_trailer(node_id: str) -> str:
    return f"{TRAILER_KEY}: {node_id}"


def parse_node_id(commit_message: str) -> str | None:
    """Return the node id embedded in a commit message trailer, if any."""
    for line in commit_message.splitlines():
        stripped = line.strip()
        if stripped.startswith(f"{TRAILER_KEY}:"):
            return stripped.split(":", 1)[1].strip()
    return None


class GitBinding:
    """Thin wrapper over the git CLI for one repository."""

    def __init__(self, repo_path: str | Path) -> None:
        self.repo = Path(repo_path)

    def _git(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        proc = subprocess.run(
            ["git", "-C", str(self.repo), *args],
            capture_output=True,
            text=True,
        )
        if check and proc.returncode != 0:
            raise GitError(
                f"git {' '.join(args)} failed ({proc.returncode}): {proc.stderr.strip()}"
            )
        return proc

    # -- repo lifecycle ----------------------------------------------------
    def is_repo(self) -> bool:
        proc = self._git("rev-parse", "--is-inside-work-tree", check=False)
        return proc.returncode == 0 and proc.stdout.strip() == "true"

    def init(self) -> None:
        """Initialize the repo if needed and ensure a usable committer identity.

        Identity is set at repo scope only when unset, so a `.sgt`-managed repo can
        commit without depending on the user's global git config.
        """
        if not self.is_repo():
            self.repo.mkdir(parents=True, exist_ok=True)
            self._git("init", "-q")
        if not self._has_identity("user.email"):
            self._git("config", "user.email", "sgt@semi-git.local")
        if not self._has_identity("user.name"):
            self._git("config", "user.name", "semi-git")

    def _has_identity(self, key: str) -> bool:
        proc = self._git("config", "--get", key, check=False)
        return proc.returncode == 0 and bool(proc.stdout.strip())

    # -- commits -----------------------------------------------------------
    def commit_shas(self) -> list[str]:
        """All commit SHAs, newest first. Empty before the first commit."""
        proc = self._git("log", "--format=%H", check=False)
        if proc.returncode != 0:
            return []  # no commits yet
        return [line for line in proc.stdout.splitlines() if line]

    def head(self) -> str | None:
        proc = self._git("rev-parse", "HEAD", check=False)
        return proc.stdout.strip() if proc.returncode == 0 else None

    def commit_message(self, sha: str) -> str:
        return self._git("log", "-1", "--format=%B", sha).stdout

    def node_id_for_commit(self, sha: str) -> str | None:
        return parse_node_id(self.commit_message(sha))

    def stage_all(self) -> None:
        self._git("add", "-A")

    def commit_all(self, message: str, node_id: str | None = None) -> str:
        """Stage everything and commit, embedding the node-id trailer when given."""
        self.stage_all()
        full = message if node_id is None else f"{message}\n\n{format_trailer(node_id)}"
        self._git("commit", "-q", "-m", full)
        head = self.head()
        if head is None:
            raise GitError("commit succeeded but HEAD is unresolved")
        return head

    def amend_no_edit(self) -> str:
        """Amend HEAD keeping its message (and thus its node-id trailer)."""
        self._git("commit", "-q", "--amend", "--no-edit")
        head = self.head()
        if head is None:
            raise GitError("amend succeeded but HEAD is unresolved")
        return head

    # -- drift detection ---------------------------------------------------
    def detect_orphans(self, known_commit_ids: set[str]) -> list[str]:
        """Commits present in git but unknown to the graph (out-of-band changes)."""
        return [sha for sha in self.commit_shas() if sha not in known_commit_ids]


def known_commit_ids(graph: SemanticGraph) -> set[str]:
    """The set of commit SHAs the graph has mapped to a node."""
    ids: set[str] = set()
    for node in graph.nodes():
        ids.update(node.commit_ids)
    return ids


def init_store(repo_path: str | Path) -> tuple[GitBinding, Path]:
    """`sgt init`: bind (or create) a git repo and an empty `.sgt/graph.json`."""
    gb = GitBinding(repo_path)
    gb.init()
    sgt_dir = Path(repo_path) / ".sgt"
    sgt_dir.mkdir(parents=True, exist_ok=True)
    graph_path = sgt_dir / "graph.json"
    if not graph_path.exists():
        SemanticGraph().save(graph_path)
    return gb, graph_path
