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

# D1's append-only land-log trailer: the shared-branch commit sha a log entry records landing.
LANDED_SHA_KEY = "Sgt-Landed-Sha"

# git's canonical empty-tree object: diffing against it makes a root commit (no parent)
# read as "everything added".
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"


class GitError(Exception):
    """A git command failed."""


class PushRejected(GitError):
    """A non-forcing `git push` was rejected because the remote moved (non-fast-forward). The
    remedy is `sgt sync` then push again -- sgt never force-pushes (design doc §3.2, C7). Kept a
    subclass of `GitError` so an unaware caller still treats it as a failure, while `sgt push`
    catches it specifically to route the user to sync."""


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


def format_landed_sha(sha: str) -> str:
    return f"{LANDED_SHA_KEY}: {sha}"


def parse_landed_sha(commit_message: str) -> str | None:
    """The landed commit sha embedded in a D1 log entry's trailer, if any."""
    for line in commit_message.splitlines():
        stripped = line.strip()
        if stripped.startswith(f"{LANDED_SHA_KEY}:"):
            return stripped.split(":", 1)[1].strip()
    return None


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
        # `errors="replace"` (not the default strict decode): `git diff` embeds a changed
        # file's raw content inline in its output, so a non-UTF-8 tracked file makes this
        # decode itself blow up otherwise. Every caller of `_git` only reads structural,
        # ASCII-safe markers out of that output (hunk headers, `+++ b/path` lines, name-status
        # letters) -- never the file's own content bytes, which are always read separately via
        # `blob_bytes` -- so a lossy replacement here never touches anything byte-fidelity
        # actually depends on.
        proc = subprocess.run(
            ["git", "-C", str(self.repo), *args],
            capture_output=True,
            text=True,
            errors="replace",
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

    def merge_base(self, a: str, b: str) -> str | None:
        """The best common ancestor of `a` and `b` (`git merge-base`), or None if they share none
        -- the point sync mines forward from (`merge_base..theirs`) to fold a teammate's foreign
        commits into the union without a checkout (U20, C3)."""
        proc = self._git("merge-base", a, b, check=False)
        out = proc.stdout.strip()
        return out if proc.returncode == 0 and out else None

    def is_ancestor(self, a: str, b: str) -> bool:
        """True iff commit `a` is an ancestor of `b` (or `a == b`) -- `git merge-base
        --is-ancestor`, exit 0 for yes, 1 for no. The causal-ordering primitive U21's pin
        witness-topo tie-break asks: a pin recorded at a witness that is an ancestor of another's
        witness is causally earlier, so the descendant's assignment wins. A witness that doesn't
        resolve (missing/foreign) makes this False, falling the tie-break through to its hash path."""
        if not a or not b:
            return False
        return self._git("merge-base", "--is-ancestor", a, b, check=False).returncode == 0

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

    def blob_bytes_many(self, specs: list[tuple[str, str]]) -> list[bytes | None]:
        """Raw bytes for many ``(sha, path)`` pairs in one ``git cat-file --batch`` process,
        aligned with ``specs`` order -- the batched counterpart to ``blob_bytes``. Mining one
        commit needs many blobs at once (every tracked file for ``tree_at``, or every changed
        file for a diff); one subprocess per blob made mining scale as O(commits x files)
        subprocess spawns (measured: minutes to mine a 169-commit repo). ``cat-file --batch``
        takes a ``<rev>:<path>`` object spec per line, so no oid lookup is needed first."""
        if not specs:
            return []
        stdin_data = "".join(f"{sha}:{path}\n" for sha, path in specs).encode()
        proc = subprocess.run(
            ["git", "-C", str(self.repo), "cat-file", "--batch"],
            input=stdin_data, capture_output=True,
        )
        data = proc.stdout
        results: list[bytes | None] = []
        pos = 0
        for _ in specs:
            nl = data.index(b"\n", pos)
            header = data[pos:nl]
            pos = nl + 1
            # A missing spec's line is the literal input echoed back + " missing" -- a path
            # containing spaces would otherwise throw off a plain field-count check, since git
            # doesn't quote it.
            if header.endswith(b" missing") or len(header.split()) != 3:
                results.append(None)
                continue
            size = int(header.split()[2])
            results.append(data[pos:pos + size])
            pos += size + 1  # the blob's trailing newline
        return results

    def list_tree(self, sha: str, prefix: str) -> list[str]:
        """Every tracked path under ``prefix`` at ``sha`` -- e.g. every op file a remote's commit
        carries, for `sgt sync`'s (U15) provenance-union pass without reading the whole tree via
        ``tree_at``."""
        proc = self._git("ls-tree", "-r", "--name-only", sha, "--", prefix, check=False)
        return [line for line in proc.stdout.splitlines() if line]

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

    def symlink_paths(self, tree_ish: str, paths: list[str]) -> set[str]:
        """The subset of ``paths`` that are symlinks (git mode ``120000``) in the tree at
        ``tree_ish`` -- one ``git ls-tree`` scoped to just those paths, not a full recursive walk.
        Symlinks are unmanaged (R3): their blob is the target-path *string*, so mining consults
        this to skip mode-120000 entries before that string leaks into the DAG as ordinary
        content. A missing path (deleted at ``tree_ish``) simply doesn't appear in the output."""
        if not paths:
            return set()
        proc = self._git("ls-tree", "-z", tree_ish, "--", *paths, check=False)
        if proc.returncode != 0:
            return set()
        out: set[str] = set()
        for entry in proc.stdout.split("\0"):
            if not entry:
                continue
            meta, _, path = entry.partition("\t")
            if meta.split(" ", 1)[0] == "120000":
                out.add(path)
        return out

    def history(self, since: str | None = None, target: str = "HEAD") -> list[tuple[str, str | None, str]]:
        """``(sha, first_parent, subject)`` oldest-first. ``since``, if given, restricts to
        commits reachable from ``target`` (default HEAD) but not from ``since`` (``since..target``)
        -- each commit's own first-parent is still returned for diffing, so incremental mining
        diffs each commit against its true predecessor regardless of where the range starts.
        ``target`` lets sync mine a *fetched* teammate branch (``merge_base..theirs_sha``) without
        checking it out (U20). First-parent only: merges never re-attribute a whole side branch
        onto the merge commit (a v1 simplification also used by the entity miner)."""
        rev_range = f"{since}..{target}" if since is not None else target
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

    def history_backward(self, tip: str, limit: int | None = None) -> list[tuple[str, str | None, str]]:
        """``(sha, first_parent, subject)`` newest-first from ``tip`` back toward the root --
        the mirror image of :meth:`history`, which walks the same shape oldest-first via
        ``--reverse``. ``limit``, if given, caps the underlying ``git log`` walk itself (``-n``)
        so a bounded backward chunk never pays for walking history it doesn't need (e.g. very
        deep repos during chunked genesis-backfill). First-parent only, matching ``history``'s
        merge-commit convention."""
        args = ["log", "--format=%H%x1f%P%x1f%s"]
        if limit is not None:
            args.extend(["-n", str(limit)])
        args.append(tip)
        proc = self._git(*args, check=False)
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

    def commits_touching(self, ref: str, path: str) -> list[tuple[str, str | None]]:
        """``(sha, first_parent)`` for every commit reachable from ``ref`` that changed ``path``,
        newest-first. Powers the miner's rebirth/flip lookback (U9): the ancestor commit that last
        *closed* a symbol is found by walking the path's own history -- a pure function of git
        (LAW-0), never the local op store, so it holds across clones and even when the closing
        commit predates a ``since``-restricted incremental mine. First-parent of each row matches
        the parent ``mine`` diffs that commit against."""
        proc = self._git("log", "--format=%H%x1f%P", ref, "--", path, check=False)
        if proc.returncode != 0:
            return []
        rows: list[tuple[str, str | None]] = []
        for line in proc.stdout.splitlines():
            if not line:
                continue
            sha, parents = line.split("\x1f", 1)
            first_parent = parents.split()[0] if parents.strip() else None
            rows.append((sha, first_parent))
        return rows

    def tree_at(self, sha: str) -> dict[str, str]:
        """Every readable text file in the tree at ``sha`` -> its contents (the past snapshot).

        Powers the scrubber's untracked-code rewind: whole-repo structure at a past commit,
        not just sgt-tracked features. Binary/unreadable blobs are skipped.
        """
        listing = self._git("ls-tree", "-r", "--name-only", sha, check=False)
        if listing.returncode != 0:
            return {}
        paths = [name.strip() for name in listing.stdout.splitlines() if name.strip()]
        blobs = self.blob_bytes_many([(sha, p) for p in paths])
        out: dict[str, str] = {}
        for path, raw in zip(paths, blobs):
            if raw is None:
                continue
            try:
                out[path] = raw.decode("utf-8")
            except UnicodeDecodeError:
                continue
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

        The scratch index lives in the *real* git dir resolved via `--absolute-git-dir`, not
        `self.repo / ".git"`: in a linked worktree (which is how `sgt land` runs concurrent
        sessions, U23) `.git` is a file pointing at the worktree's gitdir, so assuming a directory
        there fails.
        """
        git_dir = Path(self._git("rev-parse", "--absolute-git-dir").stdout.strip())
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

    # -- sync transport (U15) -----------------------------------------------
    def is_clean(self) -> bool:
        """True if the working tree and index have no uncommitted changes -- the precondition
        `sgt sync` needs before it can run `git merge`."""
        proc = self._git("status", "--porcelain", check=False)
        return proc.returncode == 0 and not proc.stdout.strip()

    def has_dirty_source(self) -> bool:
        """True if any path outside `.sgt/` has an uncommitted working-tree change -- modified,
        deleted, staged, *or* untracked -- which is exactly the precondition for `_sync`'s dirty
        mining pass (R16). Only `.sgt/` is excluded: it is sgt's own state, never mined as codebase
        content (`_mine_one` skips it), and after any `get()`/`put()` the working tree carries
        untracked `.sgt/ops/*` churn -- were that counted, the guard would never fire in a real
        repo. Untracked *source* files, by contrast, are genuine pending adds and must still be
        mined (they are, in the `save` golden). So on a tree clean by this measure the O(files)
        pending pass would produce no source ops and is skipped. A git error degrades to `True`
        (run the pass) rather than silently skipping."""
        proc = self._git("status", "--porcelain", "--", ".", ":(exclude).sgt", check=False)
        return proc.returncode != 0 or bool(proc.stdout.strip())

    def upstream(self) -> str | None:
        """`<remote>/<branch>` HEAD's branch tracks, or None (detached HEAD, or no upstream
        configured)."""
        proc = self._git(
            "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}", check=False
        )
        return proc.stdout.strip() if proc.returncode == 0 else None

    def default_remote(self) -> str:
        """The remote `sgt sync` targets when the caller doesn't name one: the upstream's
        remote if HEAD tracks one, else `origin`."""
        up = self.upstream()
        if up and "/" in up:
            return up.split("/", 1)[0]
        return "origin"

    def default_branch(self) -> str | None:
        """The remote branch `sgt sync` targets when the caller doesn't name one: the
        upstream's branch name if HEAD tracks one, else the current local branch's own name."""
        up = self.upstream()
        if up and "/" in up:
            return up.split("/", 1)[1]
        ref = self.symbolic_ref()
        return ref.rsplit("/", 1)[-1] if ref else None

    def fetch(self, remote: str, branch: str) -> None:
        self._git("fetch", remote, branch)

    def fetch_ref(self, remote: str, refspec: str) -> bool:
        """Best-effort `git fetch <remote> <refspec>` for an arbitrary ref (D1's land log lives
        outside `refs/heads/`, so it needs its own refspec rather than `fetch`'s plain branch
        name). Swallows failure -- an older remote that has never pushed the ref is not an error,
        just nothing to recover from yet. Returns whether the fetch succeeded."""
        proc = self._git("fetch", remote, refspec, check=False)
        return proc.returncode == 0

    def checkout_branch(self, branch: str) -> None:
        """Move HEAD to an existing `branch`, materializing its committed tree -- the git mechanism
        behind `sgt switch` (U26). sgt mines-on-contact on either side of it so the op store never
        drifts; creating a branch stays raw git (`sgt git checkout -b`)."""
        self._git("checkout", branch)

    def push(self, remote: str, branch: str) -> str:
        """`git push <remote> <branch>` with no force of any kind (C7). A non-fast-forward
        rejection (the remote moved) raises `PushRejected` distinctly from every other failure, so
        `sgt push` can route the user to `sgt sync` rather than ever forcing. Returns the pushed
        HEAD sha on success."""
        proc = self._git("push", remote, branch, check=False)
        if proc.returncode != 0:
            stderr = proc.stderr.strip()
            if "rejected" in stderr or "non-fast-forward" in stderr or "fetch first" in stderr:
                raise PushRejected(stderr)
            raise GitError(f"git push {remote} {branch} failed ({proc.returncode}): {stderr}")
        head = self.head()
        if head is None:
            raise GitError("push succeeded but HEAD is unresolved")
        return head

    def push_head_as(self, remote: str, branch: str) -> None:
        """`git push <remote> HEAD:refs/heads/<branch>`: publish HEAD's commit under a fresh
        remote branch name, without creating or checking out a local branch of that name -- the
        mechanism behind `sgt propose publish`'s PR branch (plan U32). Non-forcing, like `push`;
        a rejection (the remote branch already diverged) surfaces as a plain `GitError`."""
        proc = self._git("push", remote, f"HEAD:refs/heads/{branch}", check=False)
        if proc.returncode != 0:
            raise GitError(
                f"git push {remote} HEAD:refs/heads/{branch} failed "
                f"({proc.returncode}): {proc.stderr.strip()}"
            )

    def push_ref(self, remote: str, refspec: str) -> bool:
        """Best-effort `git push <remote> <refspec>` for an arbitrary ref (D1's land log). Never
        forces and swallows failure -- the branch push it accompanies is the one that must
        succeed; the log ref is advisory transport, not correctness-bearing. Returns whether the
        push succeeded."""
        proc = self._git("push", remote, refspec, check=False)
        return proc.returncode == 0

    # -- worktrees: session scratch trees (plan U30, D5) --------------------
    def worktree_add(self, path: str | Path, branch: str, base_sha: str) -> None:
        """`git worktree add -b <branch> <path> <base_sha>`: a real, isolated working directory
        sharing this repo's object store, checked out on a fresh branch off `base_sha` -- the
        mechanism behind `sgt session start`'s "ephemeral materialization of a base ideal into a
        scratch tree" (no daemon, no separate clone; git's own worktree bookkeeping owns it)."""
        self._git("worktree", "add", "-q", "-b", branch, str(path), base_sha)

    def worktree_remove(self, path: str | Path, force: bool = False) -> None:
        """`git worktree remove [--force] <path>`. `force` is needed when the scratch tree has
        uncommitted edits -- a crashed session's abandoned work (`sgt session gc`)."""
        args = ["worktree", "remove"]
        if force:
            args.append("--force")
        self._git(*args, str(path))

    # -- land: off-ref commit construction + branch-record CAS (plan U23, C9) ----------------
    def write_tree(self) -> str:
        """The tree object id of the current index (`git write-tree`). `land` stages its
        materialized union (`stage_all`) then captures the tree here to build a commit *off* any
        ref (`commit_tree`) -- nothing is visible until the CAS advances the branch onto it."""
        return self._git("write-tree").stdout.strip()

    def commit_tree(
        self, tree: str, parents: list[str], message: str, trailers: str | None = None
    ) -> str:
        """`git commit-tree <tree> [-p <parent> ...] -m <msg>` -> a new commit sha, WITHOUT moving
        any ref. The plumbing `land` uses to construct the landing commit (a real 2-parent merge
        when the session's HEAD diverges from the branch tip) before compare-and-swapping the branch
        onto it. `commit_all`/`complete_merge` can't be reused here because they move HEAD, which
        must not happen until the CAS wins the race."""
        args = ["commit-tree", tree]
        for parent in parents:
            args += ["-p", parent]
        full = message if trailers is None else f"{message}\n\n{trailers}"
        args += ["-m", full]
        return self._git(*args).stdout.strip()

    def update_ref_cas(self, ref: str, new: str, old: str | None) -> bool:
        """Compare-and-swap the branch record (plan U23, C9/LAW-G): `git update-ref <ref> <new>
        <old>` atomically moves `ref` to `new` only if it still points at `old` -- the 40-zero oid
        when `old` is None ("create only if absent"). Returns True on success, False on a CAS
        failure (the ref moved off `old`, or a create raced another create -- git reports both as
        `cannot lock ref`), and raises `GitError` on any other failure. This atomic old-value
        precondition, shared across every process touching this repo's ref store, is the *entire*
        concurrency-safety mechanism for `land`; the U23 store-lock audit confirmed the single-writer
        store lock is per-`add()` (already correct for op appends) and must not be widened around
        this."""
        old_val = old if old is not None else "0" * 40
        proc = self._git("update-ref", ref, new, old_val, check=False)
        if proc.returncode == 0:
            return True
        if "cannot lock ref" in proc.stderr:
            return False
        raise GitError(f"git update-ref {ref} failed ({proc.returncode}): {proc.stderr.strip()}")

    def complete_merge(self, message: str, merge_parent: str, trailers: str | None = None) -> str:
        """Commit a real 2-parent merge joining HEAD with `merge_parent`, whose tree is *exactly*
        the current working tree -- sgt's explicitly-reconciled union (op files, pins/tree/declared,
        folded source) -- with no textual 3-way merge run on any path. Writing `.git/MERGE_HEAD`
        ourselves is what makes git's ordinary commit path add `merge_parent` as the second parent
        (the way a real `git merge` would) while leaving the tree entirely under sgt's control; git
        clears MERGE_HEAD on a successful commit. Replaces the pre-U19 `git merge -X ours`, whose
        textual resolution sgt overwrote anyway. Embeds `Sgt-Op:` trailers for the ops this
        materialization witnesses (mirrors `commit_all`'s trailer convention)."""
        merge_head = Path(self._git("rev-parse", "--git-path", "MERGE_HEAD").stdout.strip())
        if not merge_head.is_absolute():
            merge_head = self.repo / merge_head
        merge_head.write_text(f"{merge_parent}\n", encoding="utf-8")
        self.stage_all()
        full = message if trailers is None else f"{message}\n\n{trailers}"
        self._git("commit", "-q", "-m", full)
        head = self.head()
        if head is None:
            raise GitError("merge commit succeeded but HEAD is unresolved")
        return head

    # -- transactional land: snapshot / restore (plan U5, R7) ----------------
    def restore_worktree_to(self, commit_ish: str) -> None:
        """Roll the working tree and index back to `commit_ish`, then drop any untracked file the
        rolled-back work left behind -- *except* under `.sgt/`, whose op store is monotone
        (content-addressed, append-only) and must survive a rollback (R7). `land` snapshots its
        clean pre-land HEAD and calls this on every non-landing exit (red gate, open fork, lost
        CAS, contention, crash recovery), so a land that does not land leaves no trace. Safe
        because `land` requires a clean tree at entry: any untracked file present at restore time
        was created by the candidate materialization, not by the user."""
        self._git("reset", "--hard", commit_ish)  # restore tracked files (source + `.sgt` metadata)
        self._git("clean", "-fd", "-e", ".sgt")    # remove candidate-created untracked source only


def init_store(repo_path: str | Path) -> tuple[GitBinding, Path]:
    """`sgt init`: bind (or create) a git repo and ensure `.sgt/` exists."""
    gb = GitBinding(repo_path)
    gb.init()
    sgt_dir = Path(repo_path) / ".sgt"
    sgt_dir.mkdir(parents=True, exist_ok=True)
    return gb, sgt_dir
