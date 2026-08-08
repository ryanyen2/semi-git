"""Binding between the semantic DAG and the underlying git repo.

A node's persistent identity lives in a commit trailer (``Sgt-Node-Id: <id>``),
which survives ``git commit --amend`` and rebase the way Gerrit's Change-Id does
(origin R2). Commits not mapped to any node are detected as out-of-band changes
(origin R4) so the graph never silently drifts from git.
"""

from __future__ import annotations

import atexit
import hashlib
import os
import subprocess
import tempfile
import threading
import uuid
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

TRAILER_KEY = "Sgt-Node-Id"

# The kernel's witness trailer (plan U6): one line per op a materializing commit's tree
# embodies. Multi-valued like `Co-Authored-By` -- a commit can witness many ops at once.
OP_TRAILER_KEY = "Sgt-Op"

# D1's append-only land-log trailer: the shared-branch commit sha a log entry records landing.
LANDED_SHA_KEY = "Sgt-Landed-Sha"

# Marks a commit sgt made for its OWN mechanics -- the materialization behind a revert, a restore,
# or an undo -- rather than work the developer did. History is append-only, so undoing is a forward
# commit; without this mark those commits are indistinguishable from real work and `sgt now` reports
# "sgt restore f-08ccdb12..." back to the developer as a thing they accomplished. The mark is what
# lets every human-facing list fold them and count them separately, while every *semantic* read
# (ops, ideals, provenance) keeps treating them exactly as before.
BOOKKEEPING_KEY = "Sgt-Bookkeeping"

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


def format_bookkeeping_trailer() -> str:
    return f"{BOOKKEEPING_KEY}: 1"


# Subjects sgt gave its own materialization commits before the trailer existed. Used ONLY to fold
# pre-existing history in display: a repo mined before this change has no trailer to read, and a
# developer should not have to see years of `sgt revert <hex>` rows to get the benefit. Never used
# for anything semantic -- a real commit a user happened to title "sgt revert x" would be hidden
# from a list, which is recoverable, but must never be treated as sgt's own for op purposes.
_LEGACY_BOOKKEEPING_PREFIXES = ("sgt revert ", "sgt restore ", "sgt pin ", "sgt cherry-pick ",
                                "sgt undo:", "sgt: materialize ideal")


def is_bookkeeping_message(commit_message: str) -> bool:
    """Whether a commit is sgt's own plumbing rather than the developer's work -- by trailer, or by
    subject shape for commits made before the trailer existed."""
    if any(line.strip().startswith(f"{BOOKKEEPING_KEY}:")
           for line in commit_message.splitlines()):
        return True
    subject = commit_message.strip().splitlines()[0].strip() if commit_message.strip() else ""
    return subject.startswith(_LEGACY_BOOKKEEPING_PREFIXES)


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


class _CatFileBatch:
    """One long-lived ``git cat-file --batch`` process for a repo. Blob reads used to spawn one
    git subprocess per call (`blob_bytes`) or per batch (`blob_bytes_many`); mining a chunk of
    history issues hundreds of such calls, and at ~15-20ms per spawn the process startup itself
    -- not the object reads -- dominated `mine()` wall-clock. A persistent batch process answers
    every read over one pipe. Requests are ``<rev>:<path>`` lines, so ref resolution happens at
    request time (new commits/refs made after the process started resolve fine; git also
    re-scans its object store on a lookup miss, so freshly-written loose objects are found)."""

    def __init__(self, repo: str) -> None:
        self.repo = repo
        self.lock = threading.Lock()
        self.proc: subprocess.Popen | None = None
        self.check_proc: subprocess.Popen | None = None  # `--batch-check`: object info, no content

    def _start(self) -> None:
        self.proc = subprocess.Popen(
            ["git", "-C", self.repo, "cat-file", "--batch"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        )

    def _start_check(self) -> None:
        self.check_proc = subprocess.Popen(
            ["git", "-C", self.repo, "cat-file", "--batch-check"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        )

    def close(self) -> None:
        with self.lock:
            self._close_locked()

    def _close_locked(self) -> None:
        for attr in ("proc", "check_proc"):
            proc = getattr(self, attr)
            if proc is None:
                continue
            for stream in (proc.stdin, proc.stdout):
                try:
                    stream.close()
                except OSError:
                    pass
            try:
                proc.terminate()
            except OSError:
                pass
            setattr(self, attr, None)

    def read_many(self, specs: list[tuple[str, str]]) -> list[bytes | None]:
        """Blob bytes for each ``(rev, path)`` spec, aligned with input order; None for a spec
        that is missing or not a blob. Retries once on a dead/broken process (e.g. a repo whose
        objects moved underneath us), then degrades by raising ``GitError``."""
        with self.lock:
            try:
                return self._read_many_locked(specs)
            except (OSError, ValueError, GitError):
                self._close_locked()  # restart once: the process may simply have died
                try:
                    return self._read_many_locked(specs)
                except (OSError, ValueError) as exc:
                    self._close_locked()
                    raise GitError(f"git cat-file --batch failed for {self.repo}: {exc}") from exc

    def info_many(self, specs: list[tuple[str, str]]) -> list[tuple[str, str, int] | None]:
        """``(oid, type, size)`` for each ``(rev, path)`` spec via ``--batch-check`` -- object
        identity without paying for content. None for a spec that doesn't resolve."""
        with self.lock:
            try:
                return self._info_many_locked(specs)
            except (OSError, ValueError, GitError):
                self._close_locked()
                try:
                    return self._info_many_locked(specs)
                except (OSError, ValueError) as exc:
                    self._close_locked()
                    raise GitError(f"git cat-file --batch-check failed for {self.repo}: {exc}") from exc

    def _info_many_locked(self, specs: list[tuple[str, str]]) -> list[tuple[str, str, int] | None]:
        if self.check_proc is None or self.check_proc.poll() is not None:
            self._start_check()
        stdin, stdout = self.check_proc.stdin, self.check_proc.stdout
        payload = "".join(f"{rev}:{path}\n" for rev, path in specs).encode()

        def _feed() -> None:
            try:
                stdin.write(payload)
                stdin.flush()
            except OSError:
                pass

        feeder = threading.Thread(target=_feed, daemon=True)
        feeder.start()
        results: list[tuple[str, str, int] | None] = []
        try:
            for _ in specs:
                header = stdout.readline()
                if not header:
                    raise GitError("cat-file --batch-check stream ended early")
                header = header.rstrip(b"\n")
                fields = header.split()
                if header.endswith(b" missing") or len(fields) != 3:
                    results.append(None)
                    continue
                results.append((fields[0].decode(), fields[1].decode(), int(fields[2])))
        finally:
            feeder.join()
        return results

    def _read_many_locked(self, specs: list[tuple[str, str]]) -> list[bytes | None]:
        if self.proc is None or self.proc.poll() is not None:
            self._start()
        stdin, stdout = self.proc.stdin, self.proc.stdout
        payload = "".join(f"{rev}:{path}\n" for rev, path in specs).encode()

        # Feed stdin from a helper thread: a large request batch can overfill the OS pipe buffer
        # while git is itself blocked writing responses we haven't read yet -- the classic
        # two-pipe deadlock. The reader (this thread) drains stdout concurrently.
        def _feed() -> None:
            try:
                stdin.write(payload)
                stdin.flush()
            except OSError:
                pass  # surfaces as a truncated read below

        feeder = threading.Thread(target=_feed, daemon=True)
        feeder.start()
        results: list[bytes | None] = []
        try:
            for _ in specs:
                header = stdout.readline()
                if not header:
                    raise GitError("cat-file --batch stream ended early")
                header = header.rstrip(b"\n")
                fields = header.split()
                if header.endswith(b" missing") or len(fields) != 3:
                    results.append(None)
                    continue
                size = int(fields[2])
                body = stdout.read(size + 1)  # content + trailing newline
                if len(body) != size + 1:
                    raise GitError("cat-file --batch stream ended early")
                # A non-blob object (e.g. a directory path resolving to a tree) is not file
                # content -- report absent, exactly like a missing path.
                results.append(body[:size] if fields[1] == b"blob" else None)
        finally:
            feeder.join()
        return results


# Shared batch processes, one per repo path, capped so long test runs over many scratch repos
# don't accumulate live subprocesses. LRU: evicting (or exiting) closes the process.
_BATCH_PROCS: "OrderedDict[str, _CatFileBatch]" = OrderedDict()
_BATCH_PROCS_LOCK = threading.Lock()
_BATCH_PROCS_MAX = 8


def _batch_for(repo: Path) -> _CatFileBatch:
    # Keyed by the *resolved* path: callers routinely construct `GitBinding(".")`, and a bare
    # "." names a different repo whenever the process's cwd moves (tests chdir constantly) --
    # a relative key would hand one repo's batch process to another repo's reads.
    key = os.path.realpath(repo)
    with _BATCH_PROCS_LOCK:
        batch = _BATCH_PROCS.get(key)
        if batch is None:
            batch = _CatFileBatch(key)
            _BATCH_PROCS[key] = batch
            while len(_BATCH_PROCS) > _BATCH_PROCS_MAX:
                _BATCH_PROCS.popitem(last=False)[1].close()
        else:
            _BATCH_PROCS.move_to_end(key)
        return batch


@atexit.register
def _close_batch_procs() -> None:
    with _BATCH_PROCS_LOCK:
        for batch in _BATCH_PROCS.values():
            batch.close()
        _BATCH_PROCS.clear()


class GitBinding:
    """Thin wrapper over the git CLI for one repository."""

    def __init__(self, repo_path: str | Path) -> None:
        self.repo = Path(repo_path)

    def repo_key(self) -> str:
        """A stable process-wide identity for this repo: the resolved absolute path. The cache
        key every content-addressed memo over this binding must use -- `self.repo` is often a
        relative `"."`, which silently renames the repo whenever the process chdirs."""
        return os.path.realpath(self.repo)

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

    def init(self) -> bool:
        """Initialize the repo if needed and ensure a usable committer identity. Returns whether a
        placeholder identity had to be planted.

        Identity is set at repo scope only when unset (`_has_identity` reads the full config
        cascade, so a user with a global identity is never touched), which lets a bare container or
        a test fixture commit at all. But planting it *silently* meant every commit a real developer
        made through sgt was authored "semi-git <sgt@semi-git.local>" -- wrong in `git log`, wrong
        in blame, wrong on the remote, and invisible until someone else noticed. So the fact is
        returned, and every caller that a human can reach says so out loud.
        """
        if not self.is_repo():
            self.repo.mkdir(parents=True, exist_ok=True)
            self._git("init", "-q")
        planted = False
        if not self._has_identity("user.email"):
            self._git("config", "user.email", "sgt@semi-git.local")
            planted = True
        if not self._has_identity("user.name"):
            self._git("config", "user.name", "semi-git")
            planted = True
        return planted

    def placeholder_identity(self) -> bool:
        """Whether this repo currently commits as the planted placeholder rather than as a person.
        Read at commit time so the warning follows the *state*, not just the one `init` call that
        created it -- a repo initialized months ago is the case that actually bites."""
        return self._git(
            "config", "--get", "user.email", check=False
        ).stdout.strip() == "sgt@semi-git.local"

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

    def parents(self, sha: str) -> list[str]:
        """The parent SHAs of `sha` in git's own order (first parent first) -- empty for a root
        commit. `len(parents) >= 2` marks a merge, which the miner treats specially (1.3, F7):
        mining a merge against its first parent alone re-attributes the second parent's whole
        cumulative delta as one op, so `_mine_one` restricts a merge to the paths it resolved
        differently from *both* sides and lets each branch's own commits carry the rest."""
        proc = self._git("rev-list", "--parents", "-n", "1", sha, check=False)
        out = proc.stdout.split()
        return out[1:] if proc.returncode == 0 and len(out) > 1 else []

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
        as text -- the safe way to read a path that might be binary. Served by the repo's
        persistent ``cat-file --batch`` process, not a subprocess spawn per call."""
        return self.blob_bytes_many([(sha, path)])[0]

    def blob_bytes_many(self, specs: list[tuple[str, str]]) -> list[bytes | None]:
        """Raw bytes for many ``(sha, path)`` pairs, aligned with ``specs`` order -- the batched
        counterpart to ``blob_bytes``. Mining one commit needs many blobs at once (every tracked
        file for ``tree_at``, or every changed file for a diff); one subprocess per blob made
        mining scale as O(commits x files) subprocess spawns. All reads go through the repo's
        one persistent ``git cat-file --batch`` process (``<rev>:<path>`` object specs, so no
        oid lookup is needed first) -- even the per-batch spawn this method used to pay was the
        dominant mining cost at hundreds of batches per history chunk.

        A spec whose rev or path contains a newline can't be framed as a ``<rev>:<path>`` line in
        that pipe protocol -- it would split into two requests and silently desync the shared
        stream for every later reader in the process -- so it detours through a one-shot ``git
        show`` argv read. Newlines in tracked paths are legal in git trees and exotic in practice,
        but the tool's bar is that a read never errors on a noisy repo."""
        if not specs:
            return []
        results: list[bytes | None] = [None] * len(specs)
        batchable: list[tuple[int, tuple[str, str]]] = []
        for i, (rev, path) in enumerate(specs):
            if "\n" in rev or "\n" in path:
                results[i] = self._show_blob(rev, path)  # argv: immune to newline framing
            else:
                batchable.append((i, (rev, path)))
        if batchable:
            batched = _batch_for(self.repo).read_many([spec for _, spec in batchable])
            for (i, _), value in zip(batchable, batched):
                results[i] = value
        return results

    def _show_blob(self, sha: str, path: str) -> bytes | None:
        """One-shot raw-bytes read of ``path`` at ``sha`` via ``git show`` argv -- the fallback
        `blob_bytes_many` uses for a spec a newline makes unframeable in the shared batch pipe.
        Never text-decodes, so a binary blob comes back as its bytes rather than raising."""
        proc = subprocess.run(
            ["git", "-C", str(self.repo), "show", f"{sha}:{path}"], capture_output=True
        )
        return proc.stdout if proc.returncode == 0 else None

    def list_tree(self, sha: str, prefix: str) -> list[str]:
        """Every tracked path under ``prefix`` at ``sha`` -- e.g. every op file a remote's commit
        carries, for `sgt sync`'s (U15) provenance-union pass without reading the whole tree via
        ``tree_at``."""
        proc = self._git("ls-tree", "-r", "--name-only", sha, "--", prefix, check=False)
        return [line for line in proc.stdout.splitlines() if line]

    def blob_oid(self, sha: str, path: str) -> str | None:
        """The git blob object id of ``path`` at ``sha`` -- the stable content address a binary
        file's image can point at without embedding the bytes themselves. Served by the repo's
        persistent ``cat-file --batch-check`` process (object info only, no content transfer)
        instead of an ``ls-tree`` subprocess spawn per call. ``--batch-check`` reports a tree
        entry whose object is absent from this repo's odb as missing -- submodule gitlinks (the
        commit lives in the submodule's odb) and promisor-filtered blobs in partial clones --
        so a miss falls back to parsing ``ls-tree``, which reads the tree entry itself and
        returns the recorded oid for ANY entry type, exactly as this method always did."""
        info = _batch_for(self.repo).info_many([(sha, path)])[0]
        if info is not None:
            return info[0]
        # A gitlink (submodule, mode 160000) or a promisor-filtered blob on a partial clone is
        # not in this repo's object store, so `--batch-check` reports it `missing` -- but the tree
        # still records the oid (the submodule's commit sha, or the filtered blob's id), which
        # `ls-tree` reads straight from the tree object without touching the odb. Fall back to it
        # so those entries keep their stable content address instead of folding to a `None`
        # (unchained) whole-file version and silently breaking chain continuity across the commit.
        proc = self._git("ls-tree", sha, "--", path, check=False)
        if proc.returncode != 0 or not proc.stdout:
            return None
        fields = proc.stdout.splitlines()[0].split()  # <mode> <type> <oid>\t<path>
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
        return [(sha, parent, subject) for sha, parent, subject, _t, _bk
                in self.history_meta(since, target)]

    def history_meta(
        self, since: str | None = None, target: str = "HEAD",
    ) -> list[tuple[str, str | None, str, int | None, bool]]:
        """`history()` plus each commit's committer time and whether it is sgt's own bookkeeping --
        `(sha, first_parent, subject, ts, bookkeeping)`, oldest-first.

        Both extras ride along in the format string of the walk `history()` already does, because
        both were previously separate full-history `git log` calls (`head_time`, a `--grep` for the
        trailer) on surfaces that had just run this one. Widening the format measured free; the
        extra walks did not. `history()` stays a 3-tuple so the miner and every other caller are
        untouched -- only the two callers that want the extras take this."""
        rev_range = f"{since}..{target}" if since is not None else target
        fmt = "%H%x1f%P%x1f%s%x1f%ct%x1f%(trailers:key=" + BOOKKEEPING_KEY + ",valueonly)"
        proc = self._git("log", "--reverse", f"--format={fmt}", rev_range, check=False)
        if proc.returncode != 0:
            return []
        rows: list[tuple[str, str | None, str, int | None, bool]] = []
        for line in proc.stdout.splitlines():
            if not line:
                continue
            parts = line.split("\x1f")
            if len(parts) < 5:
                continue
            sha, parents, subject, ts, trailer = parts[0], parts[1], parts[2], parts[3], parts[4]
            first_parent = parents.split()[0] if parents.strip() else None
            rows.append((sha, first_parent, subject,
                         int(ts) if ts.strip().isdigit() else None,
                         trailer.strip() == "1" or is_bookkeeping_message(subject)))
        return rows

    def commit_times(self, target: str = "HEAD") -> dict[str, int]:
        """``sha -> committer unix timestamp`` for every commit reachable from ``target``. One
        ``git log`` call, no per-op cost. The committer date is when the commit was *created* --
        for sgt that is the save beat, so it is the wall-clock "when this work landed" that the
        alignment pipeline's temporal generator compares against a conversation turn's own
        wall-clock. Empty on an unborn/failed ref (never raises)."""
        proc = self._git("log", "--format=%H%x1f%ct", target, check=False)
        if proc.returncode != 0:
            return {}
        out: dict[str, int] = {}
        for line in proc.stdout.splitlines():
            if not line:
                continue
            sha, _, ct = line.partition("\x1f")
            out[sha] = int(ct)
        return out

    def graph_topology(self, target: str = "HEAD") -> dict:
        """`{"mainline": set[str], "merges": set[str]}` for every commit reachable from `target`,
        in two cheap passes. `mainline` is the first-parent chain from the tip -- the "trunk" a
        `sgt log` spine draws as a continuous line; a commit not in it landed on a side branch that
        was later merged. `merges` is the commits with two or more parents (where a side branch
        folded back in). The default `sgt log` uses this to draw a narrow git-log-style spine to the
        left of each save without re-deriving topology per row."""
        merges: set[str] = set()
        proc = self._git("log", "--format=%H%x1f%P", target, check=False)
        if proc.returncode == 0:
            for line in proc.stdout.splitlines():
                if not line:
                    continue
                sha, _, parents = line.partition("\x1f")
                if len(parents.split()) >= 2:
                    merges.add(sha)
        fp = self._git("log", "--first-parent", "--format=%H", target, check=False)
        mainline = set(fp.stdout.split()) if fp.returncode == 0 else set()
        return {"mainline": mainline, "merges": merges}

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

    def op_ids_by_commit(self, target: str = "HEAD") -> dict[str, set[str]]:
        """``sha -> {op id, ...}`` from each commit's ``Sgt-Op:`` trailers, over ``target``'s
        first-parent history. One ``git log`` call rather than a ``commit_message`` per commit --
        the committed, tree-witnessed record of which ops each commit's tree embodies. Used to
        place ops whose *store* provenance is empty (work materialized by a witness commit that a
        later `record_ideal` advanced the witness past, so `_sync` never re-mined it to stamp
        provenance) at the commit that actually introduced them, so just-saved work is not dropped
        from the time-aware views."""
        # %B is the raw body (trailers live there); \x1e separates commits, \x1f splits sha/body.
        proc = self._git("log", "--format=%H%x1f%B%x1e", target, check=False)
        if proc.returncode != 0:
            return {}
        out: dict[str, set[str]] = {}
        for record in proc.stdout.split("\x1e"):
            record = record.strip("\n")
            if not record or "\x1f" not in record:
                continue
            sha, body = record.split("\x1f", 1)
            out[sha.strip()] = set(parse_op_ids(body))
        return out

    def commits_touching(self, ref: str, path: str) -> list[tuple[str, str | None]]:
        """``(sha, first_parent)`` for every commit reachable from ``ref`` that changed ``path``,
        newest-first. Powers the miner's rebirth/flip lookback (U9): the ancestor commit that last
        *closed* a symbol is found by walking the path's own history -- a pure function of git
        (LAW-0), never the local op store, so it holds across clones and even when the closing
        commit predates a ``since``-restricted incremental mine. First-parent of each row matches
        the parent ``mine`` diffs that commit against.

        Scoped to a single path deliberately: a union pathspec over several paths is NOT a superset
        of each path's own walk, because git's history simplification follows only one TREESAME
        parent at a merge and a wider pathspec can flip which parent that is (see the caller in
        `mine._apply_rebirth_chaining`)."""
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
        additional pre-formatted trailer block (e.g. `Sgt-Op:` lines, U6's witness commits).

        `--allow-empty`: since Phase 1.2 moved sgt's `.sgt/` state onto `refs/sgt/state` (off the
        branch tree, gitignored), a witness commit's payload is its `Sgt-Op:` trailers -- not a tree
        delta. When the fold reproduces already-committed source (e.g. an ideal edit that leaves the
        materialized bytes unchanged), there is nothing to stage, but the commit must still advance
        HEAD to carry the current ideal's trailers. An empty tree delta is therefore legitimate."""
        self.stage_all()
        parts = [message]
        if node_id is not None:
            parts.append(format_trailer(node_id))
        if trailers:
            parts.append(trailers)
        full = "\n\n".join(parts)
        self._git("commit", "-q", "--allow-empty", "-m", full)
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

    def dirty_source_digest(self) -> str | None:
        """A content hash of every uncommitted non-`.sgt` source change: the `git diff HEAD` patch
        (all tracked worktree changes, staged or not) plus each untracked source file's bytes. This
        is the signal `_sync` gates on -- an unchanged digest means re-running the O(files) dirty
        mining pass would produce byte-identical ops, so the whole mine is a provable no-op. Returns
        None on a git error (unborn HEAD, etc.), so the caller falls back to mining rather than
        trusting a fingerprint it couldn't compute."""
        diff = self._git("diff", "HEAD", "--", ".", ":(exclude).sgt", check=False)
        others = self._git(
            "ls-files", "-o", "--exclude-standard", "-z", "--", ".", ":(exclude).sgt", check=False
        )
        if diff.returncode != 0 or others.returncode != 0:
            return None
        h = hashlib.sha256()
        h.update(diff.stdout.encode("utf-8", "surrogatepass"))
        for path in sorted(p for p in others.stdout.split("\x00") if p):
            h.update(b"\x00")
            h.update(path.encode("utf-8", "surrogatepass"))
            try:
                h.update((self.repo / path).read_bytes())
            except OSError:
                pass  # vanished between listing and read -- the name in the digest already marks it
        return h.hexdigest()

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

    # -- index surgery (Phase 1.2 `sgt migrate state-ref`) ------------------
    def tracked_paths(self, *pathspecs: str) -> list[str]:
        """Repo-relative paths currently tracked in the index under `pathspecs` (default: all).
        The migration uses this to find the `.sgt/**` files a pre-1.2 repo still tracks so it can
        untrack exactly those and no more."""
        proc = self._git("ls-files", "-z", "--", *pathspecs, check=False)
        if proc.returncode != 0:
            return []
        return [p for p in proc.stdout.split("\x00") if p]

    def rm_cached(self, paths: list[str]) -> None:
        """Remove `paths` from the index but keep the working-tree files (`git rm --cached`). With
        `.sgt/.gitignore` in place this is what actually untracks the moved state -- gitignore alone
        never drops an already-tracked file. `--ignore-unmatch` makes a re-run over an
        already-untracked set a no-op (idempotent resume)."""
        if paths:
            self._git("rm", "-r", "--cached", "-q", "--ignore-unmatch", "--", *paths)

    def commit_staged(self, message: str) -> str | None:
        """Commit exactly what is staged in the index (no `git add`, no `--allow-empty`), returning
        the new HEAD -- or None when nothing is staged, so an idempotent re-run creates no empty
        commit. Used for the single intentional migration commit that records the untracking."""
        if self._git("diff", "--cached", "--quiet", check=False).returncode == 0:
            return None  # nothing staged
        self._git("commit", "-q", "-m", message)
        return self.head()

    def fetch_ref(self, remote: str, refspec: str) -> bool:
        """Best-effort `git fetch <remote> <refspec>` for an arbitrary ref (D1's land log lives
        outside `refs/heads/`, so it needs its own refspec rather than `fetch`'s plain branch
        name). Swallows failure -- an older remote that has never pushed the ref is not an error,
        just nothing to recover from yet. Returns whether the fetch succeeded."""
        proc = self._git("fetch", remote, refspec, check=False)
        return proc.returncode == 0

    def local_branch_exists(self, branch: str) -> bool:
        """Whether `branch` names an existing *local* branch (`refs/heads/<branch>`). `sgt switch`
        gates on this: its argument goes straight to `git checkout`, so a commit sha or tag would
        silently detach HEAD, and a `sgt save` on a detached HEAD commits onto no branch at all --
        work that vanishes from every branch the moment the user switches away."""
        return self._git(
            "show-ref", "--verify", "--quiet", f"refs/heads/{branch}", check=False
        ).returncode == 0

    def local_branches(self) -> list[str]:
        """Every local branch name, sorted -- the candidate list `sgt switch` shows when its
        argument doesn't name one."""
        proc = self._git("for-each-ref", "--format=%(refname:short)", "refs/heads/", check=False)
        if proc.returncode != 0:
            return []
        return sorted(b for b in proc.stdout.split("\n") if b.strip())

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

    def _git_stdin(
        self, *args: str, stdin: bytes, check: bool = True, env: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess:
        """`_git` for a command fed raw bytes on stdin (`hash-object --stdin`, `update-index
        --index-info`). No text mode: stdin/stdout stay bytes so a binary blob round-trips
        byte-for-byte and never trips a UTF-8 decode. Callers decode the (ASCII) stdout themselves."""
        proc = subprocess.run(
            ["git", "-C", str(self.repo), *args],
            input=stdin,
            capture_output=True,
            env={**os.environ, **env} if env is not None else None,
        )
        if check and proc.returncode != 0:
            raise GitError(
                f"git {' '.join(args)} failed ({proc.returncode}): "
                f"{proc.stderr.decode('utf-8', 'replace').strip()}"
            )
        return proc

    def write_tree_from_blobs(self, entries: dict[str, bytes]) -> str:
        """A git tree object id built from an in-memory `{repo-relative-path: content}` mapping,
        without touching the working tree or the real index -- the write half of `refs/sgt/state`
        (plan 1.2), as `working_tree_snapshot` is the write half for the working directory. Each
        blob is written with `hash-object -w`, the whole set staged into a scratch `GIT_INDEX_FILE`
        in one `update-index --index-info`, then serialized with `write-tree` (which builds the
        nested tree objects for `.sgt/ops/<id>`-style paths automatically). An empty mapping yields
        git's empty-tree oid. The scratch index lives in the *real* git dir (`--absolute-git-dir`,
        correct under a linked worktree) and is removed after, exactly like `working_tree_snapshot`."""
        git_dir = Path(self._git("rev-parse", "--absolute-git-dir").stdout.strip())
        fd, scratch_path = tempfile.mkstemp(dir=str(git_dir), prefix=".sgt-scratch-index-")
        os.close(fd)
        os.unlink(scratch_path)  # git errors on an existing-but-empty index; `update-index` creates it
        env = {"GIT_INDEX_FILE": scratch_path}
        try:
            lines = []
            for path in sorted(entries):
                oid = self._git_stdin("hash-object", "-w", "--stdin", stdin=entries[path]).stdout.decode().strip()
                lines.append(f"100644 {oid}\t{path}")
            if lines:
                self._git_stdin("update-index", "--index-info", stdin=("\n".join(lines) + "\n").encode(), env=env)
            return self._git("write-tree", env=env).stdout.strip()
        finally:
            try:
                os.unlink(scratch_path)
            except OSError:
                pass

    def read_tree_blobs(self, tree_ish: str, prefix: str = "") -> dict[str, bytes]:
        """Every path under `prefix` (all paths when empty) at `tree_ish`, mapped to raw bytes --
        the bytes-preserving, prefix-scoped counterpart to `tree_at` (which text-decodes and drops
        binary). The read half of `refs/sgt/state` materialization (plan 1.2). Missing tree-ish or
        empty prefix match yields `{}`."""
        args = ["ls-tree", "-r", "--name-only", tree_ish]
        if prefix:
            args += ["--", prefix]
        proc = self._git(*args, check=False)
        if proc.returncode != 0:
            return {}
        paths = [line for line in proc.stdout.splitlines() if line]
        blobs = self.blob_bytes_many([(tree_ish, p) for p in paths])
        return {p: b for p, b in zip(paths, blobs) if b is not None}

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
