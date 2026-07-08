"""Binding between the semantic DAG and the underlying git repo.

A node's persistent identity lives in a commit trailer (``Sgt-Node-Id: <id>``),
which survives ``git commit --amend`` and rebase the way Gerrit's Change-Id does
(origin R2). Commits not mapped to any node are detected as out-of-band changes
(origin R4) so the graph never silently drifts from git.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path

TRAILER_KEY = "Sgt-Node-Id"

# The kernel's witness trailer (plan U6): one line per op a materializing commit's tree
# embodies. Multi-valued like `Co-Authored-By` -- a commit can witness many ops at once.
OP_TRAILER_KEY = "Sgt-Op"

# git's canonical empty-tree object: diffing against it makes a root commit (no parent)
# read as "everything added".
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"


class GitError(Exception):
    """A git command failed."""


@dataclass(frozen=True)
class FileChange:
    """One path's change between two commits: its status, rename origin, and the line
    ranges the diff touched in the *new* file.

    ``status`` is git's name-status letter — ``"A"`` added, ``"M"`` modified, ``"D"``
    deleted, ``"R"`` renamed. ``old_path`` is the pre-rename path (``None`` unless
    renamed). ``new_ranges`` are 1-based inclusive ``(start, end)`` spans in the
    post-commit file, so a caller can intersect them against a unit's
    ``lineno..end_lineno`` to find which symbols the commit actually touched. A pure
    rename or a deletion touches no new lines, so ``new_ranges`` is empty.
    """

    status: str
    path: str
    old_path: str | None
    new_ranges: tuple[tuple[int, int], ...]


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


def format_op_trailers(op_ids) -> str:
    return "\n".join(f"{OP_TRAILER_KEY}: {oid}" for oid in op_ids)


def parse_op_ids(commit_message: str) -> list[str]:
    """Every op id a commit's `Sgt-Op:` trailers witness, in message order."""
    return [
        stripped.split(":", 1)[1].strip()
        for line in commit_message.splitlines()
        if (stripped := line.strip()).startswith(f"{OP_TRAILER_KEY}:")
    ]


def _hunk_new_range(header: str) -> tuple[int, int] | None:
    """From a ``@@ -a,b +c,d @@`` hunk header, the 1-based inclusive new-file span
    ``(c, c + d - 1)``; ``d`` defaults to 1 when omitted. A hunk whose new count is 0
    (a pure deletion) touches no new lines, so it returns ``None``."""
    try:
        plus = header.split("+", 1)[1].split(" ", 1)[0]
    except IndexError:
        return None
    start_s, _, count_s = plus.partition(",")
    try:
        start = int(start_s)
        count = int(count_s) if count_s else 1
    except ValueError:
        return None
    if count == 0:
        return None
    return (start, start + count - 1)


class GitBinding:
    """Thin wrapper over the git CLI for one repository."""

    def __init__(self, repo_path: str | Path) -> None:
        self.repo = Path(repo_path)

    def _git(
        self, *args: str, check: bool = True, env: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess:
        proc = subprocess.run(
            ["git", "-C", str(self.repo), *args],
            capture_output=True,
            text=True,
            env={**os.environ, **env} if env is not None else None,
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
    def commit_shas(self, ref: str = "HEAD") -> list[str]:
        """Commit SHAs reachable from ``ref`` (default HEAD), newest first. Empty before the
        first commit, or if ``ref`` doesn't resolve."""
        proc = self._git("log", "--format=%H", ref, check=False)
        if proc.returncode != 0:
            return []  # no commits yet, or ref doesn't resolve
        return [line for line in proc.stdout.splitlines() if line]

    def head(self) -> str | None:
        proc = self._git("rev-parse", "HEAD", check=False)
        return proc.stdout.strip() if proc.returncode == 0 else None

    def symbolic_ref(self) -> str | None:
        """The branch HEAD points at (e.g. ``refs/heads/main``), or None in detached-HEAD
        state -- the lens's key for per-ref witness tracking (U6)."""
        proc = self._git("symbolic-ref", "-q", "HEAD", check=False)
        return proc.stdout.strip() if proc.returncode == 0 else None

    def rev_parse(self, ref: str) -> str | None:
        """Resolve any ref expression (branch, tag, `HEAD~N`, a short sha, ...) to a full sha,
        or None if it doesn't resolve."""
        proc = self._git("rev-parse", "--verify", "-q", ref, check=False)
        return proc.stdout.strip() if proc.returncode == 0 else None

    def parent_of(self, sha: str) -> str | None:
        """`sha`'s first parent, or None if `sha` is a root commit."""
        proc = self._git("log", "-1", "--format=%P", sha, check=False)
        if proc.returncode != 0:
            return None
        parents = proc.stdout.split()
        return parents[0] if parents else None

    def commit_message(self, sha: str) -> str:
        return self._git("log", "-1", "--format=%B", sha).stdout

    def node_id_for_commit(self, sha: str) -> str | None:
        return parse_node_id(self.commit_message(sha))

    def file_at(self, sha: str, path: str) -> str | None:
        """The text of ``path`` as recorded at ``sha``, or None (absent / binary / unreadable).

        Reads raw bytes first (not through ``_git``'s ``text=True`` decode, which raises on
        invalid UTF-8 instead of the None this method's contract promises) and decodes
        ourselves so a binary blob degrades to None instead of crashing the caller.
        """
        raw = self.blob_bytes(sha, path)
        if raw is None:
            return None
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            return None

    def blob_bytes(self, sha: str, path: str) -> bytes | None:
        """Raw bytes of ``path`` at ``sha`` (absent -> None). Unlike ``file_at``, never decodes
        as text -- the safe way to read a path that might be binary."""
        proc = subprocess.run(
            ["git", "-C", str(self.repo), "show", f"{sha}:{path}"], capture_output=True
        )
        return proc.stdout if proc.returncode == 0 else None

    def blob_oid(self, sha: str, path: str) -> str | None:
        """The git blob object id of ``path`` at ``sha`` -- the stable content address a binary
        file's image can point at without embedding the bytes themselves."""
        proc = self._git("ls-tree", sha, "--", path, check=False)
        line = proc.stdout.strip()
        if not line:
            return None
        # "<mode> <type> <oid>\t<path>"
        fields = line.split()
        return fields[2] if len(fields) >= 3 else None

    def history(self, since: str | None = None) -> list[tuple[str, str | None, str]]:
        """``(sha, first_parent, subject)`` oldest-first. ``since``, if given, restricts to
        commits reachable from HEAD but not from ``since`` (``since..HEAD``) -- each commit's
        own first-parent is still returned for diffing, so incremental mining diffs each commit
        against its true predecessor regardless of where the range starts. First-parent only:
        merges never re-attribute a whole side branch onto the merge commit (a v1
        simplification also used by the entity miner)."""
        rev_range = f"{since}..HEAD" if since is not None else "HEAD"
        proc = self._git("log", "--reverse", "--format=%H%x1f%P%x1f%s", rev_range, check=False)
        if proc.returncode != 0:
            return []
        rows: list[tuple[str, str | None, str]] = []
        for line in proc.stdout.splitlines():
            if not line:
                continue
            sha, parents, subject = line.split("\x1f", 2)
            first_parent = parents.split()[0] if parents.strip() else None
            rows.append((sha, first_parent, subject))
        return rows

    def tree_at(self, sha: str) -> dict[str, str]:
        """Every readable text file in the tree at ``sha`` -> its contents (the past snapshot).

        Powers the scrubber's untracked-code rewind: whole-repo structure at a past commit,
        not just sgt-tracked features. Binary/unreadable blobs are skipped.
        """
        listing = self._git("ls-tree", "-r", "--name-only", sha, check=False)
        if listing.returncode != 0:
            return {}
        out: dict[str, str] = {}
        for name in listing.stdout.splitlines():
            name = name.strip()
            if not name:
                continue
            content = self.file_at(sha, name)
            if content is not None:
                out[name] = content
        return out

    def diff_name_and_text(
        self, parent: str | None, sha: str, find_renames: bool = True
    ) -> list[FileChange]:
        """Structured name-status + touched line ranges for ``parent..sha``.

        Parses ``git diff [-M] <parent> <sha>`` twice: ``--name-status`` for the change
        letter and rename old→new paths (git's own ``-M`` detection), and
        ``--unified=0`` for the ``@@`` hunk ranges in the new file. ``parent=None``
        diffs against the empty tree (a root commit). Order follows git's own.
        """
        base = parent if parent is not None else EMPTY_TREE
        # git detects renames by default (diff.renames), so disabling means --no-renames,
        # not merely omitting -M.
        rename = ["-M"] if find_renames else ["--no-renames"]

        # 1) status + paths — a rename shows as "R<score>\told\tnew".
        by_path: dict[str, dict] = {}
        order: list[str] = []
        ns = self._git("diff", *rename, "--name-status", base, sha).stdout
        for line in ns.splitlines():
            parts = line.split("\t")
            if len(parts) < 2:
                continue
            letter = parts[0][0]
            if letter == "R":
                old_path, new_path = parts[1], parts[2]
            else:
                old_path, new_path = None, parts[1]
            by_path[new_path] = {"status": letter, "old_path": old_path, "ranges": []}
            order.append(new_path)

        # 2) new-file hunk ranges (unified=0 → one hunk per contiguous change).
        diff = self._git("diff", *rename, "--unified=0", base, sha).stdout
        cur: str | None = None
        for line in diff.splitlines():
            if line.startswith("+++ "):
                target = line[4:].strip()
                cur = None if target == "/dev/null" else target[2:] if target.startswith("b/") else target
            elif line.startswith("@@") and cur is not None:
                rng = _hunk_new_range(line)
                if rng is not None and cur in by_path:
                    by_path[cur]["ranges"].append(rng)

        return [
            FileChange(
                status=by_path[p]["status"],
                path=p,
                old_path=by_path[p]["old_path"],
                new_ranges=tuple(by_path[p]["ranges"]),
            )
            for p in order
        ]

    def working_tree_snapshot(self) -> str:
        """A git tree object id for the current working directory -- tracked files, their
        uncommitted edits/deletions, and untracked-but-not-ignored files, all included -- so
        Gap 2's dirty-tree mining pass (U7.5) can diff it against HEAD exactly the way `mine()`
        diffs two real commits. Computed via a scratch index (a fresh, unique `GIT_INDEX_FILE`)
        so the real `.git/index` is never touched; `.gitignore` (including `.sgt/local/*`) is
        respected automatically since gitignore rules aren't index-specific.

        The scratch path is reserved via `mkstemp` then immediately removed -- git errors on an
        existing-but-empty index file ("index file smaller than expected"), so the path must not
        exist yet when `add -A` runs; it creates a fresh index there itself.
        """
        git_dir = self.repo / ".git"
        fd, scratch_path = tempfile.mkstemp(dir=str(git_dir), prefix=".sgt-scratch-index-")
        os.close(fd)
        os.unlink(scratch_path)
        try:
            env = {"GIT_INDEX_FILE": scratch_path}
            self._git("add", "-A", env=env)
            return self._git("write-tree", env=env).stdout.strip()
        finally:
            try:
                os.unlink(scratch_path)
            except OSError:
                pass

    def stage_all(self) -> None:
        self._git("add", "-A")

    def commit_all(self, message: str, node_id: str | None = None, trailers: str | None = None) -> str:
        """Stage everything and commit, embedding the node-id trailer when given, plus any
        additional pre-formatted trailer block (e.g. `Sgt-Op:` lines, U6's witness commits)."""
        self.stage_all()
        parts = [message]
        if node_id is not None:
            parts.append(format_trailer(node_id))
        if trailers:
            parts.append(trailers)
        full = "\n\n".join(parts)
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
        """Commits present in git but unknown to `known_commit_ids` (out-of-band changes)."""
        return [sha for sha in self.commit_shas() if sha not in known_commit_ids]


def init_store(repo_path: str | Path) -> tuple[GitBinding, Path]:
    """`sgt init`: bind (or create) a git repo and ensure `.sgt/` exists."""
    gb = GitBinding(repo_path)
    gb.init()
    sgt_dir = Path(repo_path) / ".sgt"
    sgt_dir.mkdir(parents=True, exist_ok=True)
    return gb, sgt_dir
