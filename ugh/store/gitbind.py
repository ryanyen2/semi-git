"""The git binding: every git command ugh runs, with the batch readers and memos that keep them
cheap."""

from __future__ import annotations

import atexit
import hashlib
import os
import re
import subprocess
import tempfile
import threading
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

# Marks a commit ugh made for its own mechanics (a take-out, a bring-back, an undo) rather than the
# developer's work, so the list of saves leaves it out. Mining treats it like any other commit.
BOOKKEEPING_KEY = "Sgt-Bookkeeping"

# git's canonical empty-tree object: diffing against it makes a root commit (no parent)
# read as "everything added".
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"


class GitError(Exception):
    """A git command failed."""


class PushRejected(GitError):
    """A non-forcing `git push` was rejected because the remote moved. The remedy is `ugh sync`,
    then push again; ugh never force-pushes."""


@dataclass(frozen=True)
class FileChange:
    """One path changed between two commits, and ``old_path``, the path it was renamed from
    (``None`` unless renamed)."""

    path: str
    old_path: str | None


def format_bookkeeping_trailer() -> str:
    return f"{BOOKKEEPING_KEY}: 1"


# Subjects of ugh's own commits made before the trailer existed, matched for display only.
_LEGACY_BOOKKEEPING_PREFIXES = ("ugh revert ", "ugh restore ", "ugh pin ", "ugh cherry-pick ",
                                "ugh undo:", "ugh: materialize ideal", "ugh land: ")


def is_bookkeeping_message(commit_message: str) -> bool:
    """Whether a commit is ugh's own plumbing rather than the developer's work -- by trailer, or by
    subject shape for commits made before the trailer existed."""
    if any(line.strip().startswith(f"{BOOKKEEPING_KEY}:")
           for line in commit_message.splitlines()):
        return True
    subject = commit_message.strip().splitlines()[0].strip() if commit_message.strip() else ""
    return subject.startswith(_LEGACY_BOOKKEEPING_PREFIXES)


def trailer_values(commit_message: str, key: str) -> list[str]:
    """The value of each `key:` trailer line of a commit message, one per line, in order."""
    return [
        stripped.split(":", 1)[1].strip()
        for line in commit_message.splitlines()
        if (stripped := line.strip()).startswith(f"{key}:")
    ]


class _CatFileBatch:
    """One long-lived ``git cat-file --batch`` process for a repo, so mining does not pay a process
    start per blob. Requests are ``<rev>:<path>`` lines resolved at request time, so refs and
    objects made after the process started are found."""

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

        # a thread feeds stdin: a large batch can fill the pipe while git blocks writing replies
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
                # a tree (a directory path) is not file content
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
    # the resolved path: "." names another repo whenever the process changes directory
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


# -- head-state read memo --------------------------------------------------------------------
# HEAD-anchored reads are paid once per HEAD state, not once per caller. The state is a signature
# over git's own files (`_head_sig`: `HEAD`, the reflog, the branch's loose ref, `packed-refs`), so
# any commit, checkout or ref move by any process drops the entry; no explicit invalidation.
_GITDIR_CACHE: "OrderedDict[str, tuple[Path, Path]]" = OrderedDict()
_HEAD_MEMO: "OrderedDict[str, tuple[tuple, dict]]" = OrderedDict()
_HEX = frozenset("0123456789abcdef")


def _is_full_sha(ref: str) -> bool:
    return len(ref) == 40 and set(ref) <= _HEX


def _git_dirs(repo: Path, repo_key: str) -> tuple[Path, Path] | None:
    """`(gitdir, commondir)` for `repo` -- `.git` itself, or the pointed-to directory for a
    linked worktree. Successful resolutions are cached forever (a repo's gitdir never moves);
    failures are re-tried each call, so a repo `git init`-ed after the first probe is picked up."""
    hit = _GITDIR_CACHE.get(repo_key)
    if hit is not None:
        return hit
    dot = repo / ".git"
    if dot.is_dir():
        gitdir = dot
    elif dot.is_file():
        try:
            text = dot.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
        if not text.startswith("gitdir:"):
            return None
        gitdir = Path(os.path.realpath(repo / text.split(":", 1)[1].strip()))
    else:
        return None
    common = gitdir
    cfile = gitdir / "commondir"
    if cfile.is_file():
        try:
            common = Path(os.path.realpath(gitdir / cfile.read_text(encoding="utf-8").strip()))
        except OSError:
            pass
    _GITDIR_CACHE[repo_key] = (gitdir, common)
    if len(_GITDIR_CACHE) > 64:
        _GITDIR_CACHE.popitem(last=False)
    return gitdir, common


def _stat_sig(p: Path) -> tuple[int, int] | None:
    try:
        st = p.stat()
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size)


# Well under ARG_MAX (1 MB on macOS, ~2 MB on Linux) with room for the environment, which shares
# the same limit -- a commit landing a vendored tree changes tens of thousands of paths at once.
_ARGV_BUDGET_BYTES = 100_000


def _argv_batches(paths: list[str]) -> list[list[str]]:
    """`paths` split into runs whose encoded bytes fit one command line. Order is preserved and a
    single over-long path still gets its own batch (git will just report it missing)."""
    batches: list[list[str]] = []
    run: list[str] = []
    used = 0
    for path in paths:
        size = len(path.encode("utf-8", "surrogateescape")) + 1  # + the NUL argv separator
        if run and used + size > _ARGV_BUDGET_BYTES:
            batches.append(run)
            run, used = [], 0
        run.append(path)
        used += size
    if run:
        batches.append(run)
    return batches


class GitBinding:
    """Thin wrapper over the git CLI for one repository."""

    def __init__(self, repo_path: str | Path) -> None:
        self.repo = Path(repo_path)

    def repo_key(self) -> str:
        """The resolved absolute path, the key every memo over this binding uses: `self.repo` is
        often `"."`, which names another repo after a chdir."""
        return os.path.realpath(self.repo)

    def _head_sig(self) -> tuple | None:
        """The repo's current HEAD-state signature (see the `_HEAD_MEMO` note above), or None
        when it can't be read -- None disables memoization, never correctness."""
        dirs = _git_dirs(self.repo, self.repo_key())
        if dirs is None:
            return None
        gitdir, common = dirs
        head_file = gitdir / "HEAD"
        try:
            head_text = head_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
        detached_sha = None
        branch_sig = None
        if head_text.startswith("ref: "):
            ref = head_text[5:].strip()
            branch_sig = (ref, _stat_sig(common / ref))
        else:
            detached_sha = head_text.strip()
        return (
            _stat_sig(head_file),
            _stat_sig(gitdir / "logs" / "HEAD"),
            branch_sig,
            detached_sha,
            _stat_sig(common / "packed-refs"),
        )

    def _memo_head(self, key: tuple, compute):
        """`compute()`'s result, cached process-wide until this repo's HEAD state changes.
        Only for pure reads whose output is a function of the HEAD signature (or of an immutable
        explicit-sha ancestry embedded in `key`). Callers that return a mutable container must
        copy it on the way out -- the memoized value is shared."""
        sig = self._head_sig()
        if sig is None:
            return compute()
        rk = self.repo_key()
        ent = _HEAD_MEMO.get(rk)
        if ent is None or ent[0] != sig:
            ent = (sig, {})
            _HEAD_MEMO[rk] = ent
            if len(_HEAD_MEMO) > 4:
                _HEAD_MEMO.popitem(last=False)
        else:
            _HEAD_MEMO.move_to_end(rk)
        vals = ent[1]
        if key not in vals:
            vals[key] = compute()
        return vals[key]

    def _git(
        self, *args: str, check: bool = True, env: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess:
        # `errors="replace"`: `git diff` output embeds file content, which may not be UTF-8; callers
        # read only its ASCII markers and read content through `blob_bytes`.
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
        """Initialize the repo if needed and plant a placeholder committer identity at repo scope
        when none is configured anywhere. Returns whether it planted one, so a caller can warn."""
        if not self.is_repo():
            self.repo.mkdir(parents=True, exist_ok=True)
            self._git("init", "-q")
        planted = False
        if not self._has_identity("user.email"):
            self._git("config", "user.email", "ugh@ugh.local")
            planted = True
        if not self._has_identity("user.name"):
            self._git("config", "user.name", "ugh")
            planted = True
        return planted

    def placeholder_identity(self) -> bool:
        """Whether this repo commits as the planted placeholder rather than as a person."""
        return self._git(
            "config", "--get", "user.email", check=False
        ).stdout.strip() == "ugh@ugh.local"

    def _has_identity(self, key: str) -> bool:
        proc = self._git("config", "--get", key, check=False)
        return proc.returncode == 0 and bool(proc.stdout.strip())

    # -- commits -----------------------------------------------------------
    def commit_shas(self, ref: str = "HEAD") -> list[str]:
        """Commit SHAs reachable from ``ref`` (default HEAD), newest first. Empty before the
        first commit, or if ``ref`` doesn't resolve."""
        def compute() -> list[str]:
            proc = self._git("log", "--format=%H", ref, check=False)
            if proc.returncode != 0:
                return []  # no commits yet, or ref doesn't resolve
            return [line for line in proc.stdout.splitlines() if line]
        if ref == "HEAD" or _is_full_sha(ref):
            return list(self._memo_head(("commit_shas", ref), compute))
        return compute()

    def head(self) -> str | None:
        def compute() -> str | None:
            proc = self._git("rev-parse", "HEAD", check=False)
            return proc.stdout.strip() if proc.returncode == 0 else None
        return self._memo_head(("head",), compute)

    def symbolic_ref(self) -> str | None:
        """The branch HEAD points at (e.g. ``refs/heads/main``), or None when detached."""
        def compute() -> str | None:
            proc = self._git("symbolic-ref", "-q", "HEAD", check=False)
            return proc.stdout.strip() if proc.returncode == 0 else None
        return self._memo_head(("symbolic_ref",), compute)

    def rev_parse(self, ref: str) -> str | None:
        """Resolve any ref expression (branch, tag, `HEAD~N`, a short sha, ...) to a full sha,
        or None if it doesn't resolve."""
        proc = self._git("rev-parse", "--verify", "-q", ref, check=False)
        return proc.stdout.strip() if proc.returncode == 0 else None

    def pseudo_ref_set(self, name: str) -> bool:
        """Whether a `*_HEAD` pseudo-ref (MERGE_HEAD, CHERRY_PICK_HEAD, REVERT_HEAD) is set: one
        stat in the gitdir, else `rev_parse`. A file that does not resolve counts as set, the safe
        direction."""
        dirs = _git_dirs(self.repo, self.repo_key())
        if dirs is None:
            return self.rev_parse(name) is not None
        gitdir, _common = dirs
        return (gitdir / name).is_file()

    def parent_of(self, sha: str) -> str | None:
        """`sha`'s first parent, or None if `sha` is a root commit."""
        proc = self._git("log", "-1", "--format=%P", sha, check=False)
        if proc.returncode != 0:
            return None
        parents = proc.stdout.split()
        return parents[0] if parents else None

    def merge_base(self, a: str, b: str) -> str | None:
        """The best common ancestor of `a` and `b` (`git merge-base`), or None if they share none."""
        proc = self._git("merge-base", a, b, check=False)
        out = proc.stdout.strip()
        return out if proc.returncode == 0 and out else None

    def parents(self, sha: str) -> list[str]:
        """The parent SHAs of `sha`, first parent first; empty for a root commit."""
        proc = self._git("rev-list", "--parents", "-n", "1", sha, check=False)
        out = proc.stdout.split()
        return out[1:] if proc.returncode == 0 and len(out) > 1 else []

    def is_ancestor(self, a: str, b: str) -> bool:
        """True iff commit `a` is an ancestor of `b` (or `a == b`); False when either does not
        resolve."""
        if not a or not b:
            return False
        return self._git("merge-base", "--is-ancestor", a, b, check=False).returncode == 0

    def commit_message(self, sha: str) -> str:
        return self._git("log", "-1", "--format=%B", sha).stdout

    def blob_bytes(self, sha: str, path: str) -> bytes | None:
        """Raw bytes of ``path`` at ``sha``, or None when absent. Never decoded."""
        return self.blob_bytes_many([(sha, path)])[0]

    def blob_bytes_many(self, specs: list[tuple[str, str]]) -> list[bytes | None]:
        """Raw bytes for many ``(sha, path)`` pairs, in ``specs`` order, through the repo's one
        ``cat-file --batch`` process. A spec containing a newline cannot be framed as a request line
        without desyncing the shared pipe, so it goes through ``git show`` instead."""
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
        """Raw bytes of ``path`` at ``sha`` through ``git show``, for a spec with a newline."""
        proc = subprocess.run(
            ["git", "-C", str(self.repo), "show", f"{sha}:{path}"], capture_output=True
        )
        return proc.stdout if proc.returncode == 0 else None

    def blob_oid(self, sha: str, path: str) -> str | None:
        """The object id ``path`` has at ``sha``, or None when absent, from ``cat-file
        --batch-check``, falling back to ``ls-tree``."""
        info = _batch_for(self.repo).info_many([(sha, path)])[0]
        if info is not None:
            return info[0]
        # a submodule or a filtered blob is not in the object store, but the tree records its id
        proc = self._git("ls-tree", sha, "--", path, check=False)
        if proc.returncode != 0 or not proc.stdout:
            return None
        fields = proc.stdout.splitlines()[0].split()  # <mode> <type> <oid>\t<path>
        return fields[2] if len(fields) >= 3 else None

    def symlink_paths(self, tree_ish: str, paths: list[str]) -> set[str]:
        """The subset of ``paths`` that are symlinks (mode ``120000``) at ``tree_ish``, which mining
        skips: a symlink's blob is its target path, not content. Batched under the argv budget."""
        out: set[str] = set()
        for batch in _argv_batches(paths):
            proc = self._git("ls-tree", "-z", tree_ish, "--", *batch, check=False)
            if proc.returncode != 0:
                continue
            for entry in proc.stdout.split("\0"):
                if not entry:
                    continue
                meta, _, path = entry.partition("\t")
                if meta.split(" ", 1)[0] == "120000":
                    out.add(path)
        return out

    def history(self, since: str | None = None, target: str = "HEAD") -> list[tuple[str, str | None, str]]:
        """``(sha, first_parent, subject)`` for the commits reachable from ``target``, or
        ``since..target``, oldest first in topological order. Each row carries its own first parent,
        so a range diffs each commit against its true predecessor."""
        return [(sha, parent, subject) for sha, parent, subject, _t, _bk
                in self.history_meta(since, target)]

    def history_meta(
        self, since: str | None = None, target: str = "HEAD",
    ) -> list[tuple[str, str | None, str, int | None, bool]]:
        """`history()` plus each commit's committer time and whether it is ugh's own bookkeeping:
        `(sha, first_parent, subject, ts, bookkeeping)`, in the same one walk."""
        def compute() -> list[tuple[str, str | None, str, int | None, bool]]:
            rev_range = f"{since}..{target}" if since is not None else target
            fmt = "%H%x1f%P%x1f%s%x1f%ct%x1f%(trailers:key=" + BOOKKEEPING_KEY + ",valueonly)"
            proc = self._git("log", "--reverse", "--topo-order", f"--format={fmt}", rev_range,
                             check=False)
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
        if (since is None or _is_full_sha(since)) and (target == "HEAD" or _is_full_sha(target)):
            return list(self._memo_head(("history_meta", since, target), compute))
        return compute()

    def commit_times(self, target: str = "HEAD") -> dict[str, int]:
        """``sha -> committer unix timestamp`` for every commit reachable from ``target``; empty when
        it does not resolve."""
        def compute() -> dict[str, int]:
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
        if target == "HEAD" or _is_full_sha(target):
            return dict(self._memo_head(("commit_times", target), compute))
        return compute()

    def first_parent_path_log(self, ref: str, path: str) -> list[tuple[str, list[str]]]:
        """``(sha, parents)`` newest-first for every commit on ``ref``'s first-parent line whose
        ``path`` differs from its first parent's."""
        proc = self._git("log", "--first-parent", "--format=%H%x1f%P", ref, "--", path, check=False)
        if proc.returncode != 0:
            return []
        return [(sha, parents.split()) for sha, _, parents in
                (line.partition("\x1f") for line in proc.stdout.splitlines() if line)]

    def tree_at(self, sha: str) -> dict[str, str]:
        """Every text file in the tree at ``sha``, path -> contents; binary blobs are skipped."""
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

    def changed_paths(self, a: str | None, b: str) -> list[str]:
        """The paths whose blob differs between trees `a` (None: empty) and `b`, no renames."""
        proc = self._git("diff", "--name-only", "--no-renames", "-z", a or EMPTY_TREE, b)
        return [p for p in proc.stdout.split("\0") if p]

    def diff_paths(self, parent: str | None, sha: str) -> list[FileChange]:
        """The paths changed in ``parent..sha`` (``parent=None``: against the empty tree), in
        git's order, a rename (git's ``-M``) as one change carrying its old path."""
        base = parent if parent is not None else EMPTY_TREE
        changes = []
        for line in self._git("diff", "-M", "--name-status", base, sha).stdout.splitlines():
            parts = line.split("\t")
            if parts[0].startswith("R"):  # "R<score>\told\tnew"
                changes.append(FileChange(path=parts[2], old_path=parts[1]))
            else:
                changes.append(FileChange(path=parts[1], old_path=None))
        return changes

    def working_tree_snapshot(self) -> str:
        """A tree object id for the working directory as it is: tracked files with their uncommitted
        edits and untracked files that are not ignored, so the dirty pass can diff it against HEAD
        like a commit. Built in a scratch `GIT_INDEX_FILE` inside the real git dir (a linked
        worktree's `.git` is a file), so the real index is never touched."""
        git_dir = Path(self._git("rev-parse", "--absolute-git-dir").stdout.strip())
        fd, scratch_path = tempfile.mkstemp(dir=str(git_dir), prefix=".sgt-scratch-index-")
        os.close(fd)
        os.unlink(scratch_path)  # git rejects an existing empty index file; `add -A` creates it
        try:
            env = {"GIT_INDEX_FILE": scratch_path}
            self._git("add", "-A", env=env)
            try:
                return self._git("write-tree", env=env).stdout.strip()
            except GitError as first:
                # A file changing under `add -A` can leave an unresolvable object; staging again
                # fixes that. A second failure is not transient, so it is explained in words.
                self._git("add", "-A", env=env)
                try:
                    return self._git("write-tree", env=env).stdout.strip()
                except GitError as second:
                    detail = str(second)
                    culprit = re.search(r"for '([^']+)'", detail)
                    named = f" The file git named is {culprit.group(1)}." if culprit else ""
                    raise GitError(
                        "Could not take a snapshot of the working tree, so this change was not "
                        f"recorded.{named} Nothing has been lost and nothing you did caused it; "
                        "the repository is intact and your files are untouched. Re-run the "
                        "command, and if it happens again tell whoever set this up and show them "
                        f"this message.\n\ngit said: {detail}"
                    ) from first
        finally:
            try:
                os.unlink(scratch_path)
            except OSError:
                pass

    def stage_all(self) -> None:
        self._git("add", "-A")

    def commit_all(self, message: str, trailers: str | None = None) -> str:
        """`git add -A` and commit with any pre-formatted trailer block. Allowed to be empty, since
        a resolution that keeps the file as it is still records its trailer."""
        self.stage_all()
        parts = [message]
        if trailers:
            parts.append(trailers)
        full = "\n\n".join(parts)
        self._git("commit", "-q", "--allow-empty", "-m", full)
        head = self.head()
        if head is None:
            raise GitError("commit succeeded but HEAD is unresolved")
        return head

    # -- sync transport -----------------------------------------------------
    def is_clean(self) -> bool:
        """True if the working tree and index have no uncommitted changes."""
        proc = self._git("status", "--porcelain", check=False)
        return proc.returncode == 0 and not proc.stdout.strip()

    def has_dirty_source(self) -> bool:
        """True if any path outside `.sgt/` is modified, deleted, staged or untracked: the condition
        for the dirty mining pass. A git error reads as True, so the pass runs."""
        proc = self._git("status", "--porcelain", "--", ".", ":(exclude).sgt", check=False)
        return proc.returncode != 0 or bool(proc.stdout.strip())

    def dirty_source_digest(self) -> str | None:
        """A hash of every uncommitted change outside `.sgt/`: the `git diff HEAD` patch plus each
        untracked file's name and bytes. None on a git error (an unborn HEAD): the caller mines."""
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
                pass  # vanished since the listing; its name is already in the digest
        return h.hexdigest()

    def upstream(self) -> str | None:
        """`<remote>/<branch>` HEAD's branch tracks, or None (detached HEAD, or no upstream
        configured)."""
        proc = self._git(
            "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}", check=False
        )
        return proc.stdout.strip() if proc.returncode == 0 else None

    def default_remote(self) -> str:
        """The remote `ugh sync` targets when the caller doesn't name one: the upstream's
        remote if HEAD tracks one, else `origin`."""
        up = self.upstream()
        if up and "/" in up:
            return up.split("/", 1)[0]
        return "origin"

    def default_branch(self) -> str | None:
        """The remote branch `ugh sync` targets when the caller doesn't name one: the
        upstream's branch name if HEAD tracks one, else the current local branch's own name."""
        up = self.upstream()
        if up and "/" in up:
            return up.split("/", 1)[1]
        ref = self.symbolic_ref()
        return ref.rsplit("/", 1)[-1] if ref else None

    def fetch(self, remote: str, branch: str) -> None:
        self._git("fetch", remote, branch)

    def fetch_ref(self, remote: str, refspec: str) -> bool:
        """`git fetch <remote> <refspec>` for a ref outside `refs/heads/` (the state ref). Returns
        whether it succeeded: a remote that never received the ref is not an error."""
        proc = self._git("fetch", remote, refspec, check=False)
        return proc.returncode == 0

    def push(self, remote: str, branch: str) -> str:
        """`git push <remote> <branch>` without force; returns the pushed HEAD sha. A rejection
        because the remote moved raises `PushRejected`, any other failure `GitError`."""
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

    def push_ref(self, remote: str, refspec: str) -> bool:
        """`git push <remote> <refspec>` without force, for the state ref. Returns whether it
        succeeded."""
        proc = self._git("push", remote, refspec, check=False)
        return proc.returncode == 0

    def commit_tree(
        self, tree: str, parents: list[str], message: str, trailers: str | None = None
    ) -> str:
        """`git commit-tree <tree> [-p <parent> ...] -m <msg>`: a new commit sha, moving no ref, for
        a state-ref commit that `update_ref_cas` then publishes."""
        args = ["commit-tree", tree]
        for parent in parents:
            args += ["-p", parent]
        full = message if trailers is None else f"{message}\n\n{trailers}"
        args += ["-m", full]
        return self._git(*args).stdout.strip()

    def _git_stdin(
        self, *args: str, stdin: bytes, env: dict[str, str] | None = None
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
        if proc.returncode != 0:
            raise GitError(
                f"git {' '.join(args)} failed ({proc.returncode}): "
                f"{proc.stderr.decode('utf-8', 'replace').strip()}"
            )
        return proc

    def write_tree_from_blobs(self, entries: dict[str, bytes]) -> str:
        """A tree object id built from `{repo-relative path: content}` without touching the working
        tree or the real index, through a scratch index as in `working_tree_snapshot`. An empty
        mapping gives the empty tree."""
        git_dir = Path(self._git("rev-parse", "--absolute-git-dir").stdout.strip())
        fd, scratch_path = tempfile.mkstemp(dir=str(git_dir), prefix=".sgt-scratch-index-")
        os.close(fd)
        os.unlink(scratch_path)  # git rejects an existing empty index; `update-index` creates it
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

    def commit_paths(self, message: str, files: dict[str, bytes | None],
                     trailers: str | None = None) -> str:
        """Commit HEAD with only `files` changed (None removes a path), leaving every other
        uncommitted edit in the working tree as it is. Returns the new HEAD."""
        head = self.head()
        git_dir = Path(self._git("rev-parse", "--absolute-git-dir").stdout.strip())
        fd, scratch_path = tempfile.mkstemp(dir=str(git_dir), prefix=".sgt-scratch-index-")
        os.close(fd)
        os.unlink(scratch_path)
        env = {"GIT_INDEX_FILE": scratch_path}
        try:
            self._git("read-tree", head, env=env)
            for path, data in sorted(files.items()):
                if data is None:
                    self._git("update-index", "--force-remove", "--", path, env=env)
                    continue
                oid = self._git_stdin("hash-object", "-w", "--stdin", stdin=data).stdout.decode().strip()
                listed = self._git("ls-tree", head, "--", path).stdout.split()
                mode = listed[0] if listed else "100644"
                self._git("update-index", "--add", "--cacheinfo", f"{mode},{oid},{path}", env=env)
            tree = self._git("write-tree", env=env).stdout.strip()
        finally:
            try:
                os.unlink(scratch_path)
            except OSError:
                pass
        sha = self.commit_tree(tree, [head], message, trailers)
        if not self.update_ref_cas("HEAD", sha, head):
            raise GitError("HEAD moved while committing; nothing was committed")
        self._git("reset", "-q", "--", *sorted(files))
        return sha

    def read_tree_blobs(self, tree_ish: str) -> dict[str, bytes]:
        """Every path at `tree_ish`, mapped to raw bytes; `{}` when there is none."""
        proc = self._git("ls-tree", "-r", "--name-only", tree_ish, check=False)
        if proc.returncode != 0:
            return {}
        paths = [line for line in proc.stdout.splitlines() if line]
        blobs = self.blob_bytes_many([(tree_ish, p) for p in paths])
        return {p: b for p, b in zip(paths, blobs) if b is not None}

    def update_ref_cas(self, ref: str, new: str, old: str | None) -> bool:
        """Move `ref` to `new` only if it still points at `old` (None: only if absent), atomically
        (`git update-ref`). False when the ref moved or a create raced; `GitError` otherwise."""
        old_val = old if old is not None else "0" * 40
        proc = self._git("update-ref", ref, new, old_val, check=False)
        if proc.returncode == 0:
            return True
        if "cannot lock ref" in proc.stderr:
            return False
        raise GitError(f"git update-ref {ref} failed ({proc.returncode}): {proc.stderr.strip()}")

    def _git_path(self, name: str) -> Path:
        path = Path(self._git("rev-parse", "--git-path", name).stdout.strip())
        return path if path.is_absolute() else self.repo / path

    def exclude_locally(self, rel: str) -> None:
        """Add `rel` to this clone's `info/exclude`, so git and `ugh save` never pick it up."""
        path = self._git_path("info/exclude")
        lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
        if rel not in lines:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("\n".join([*lines, rel]) + "\n", encoding="utf-8")

    def complete_merge(self, message: str, merge_parent: str, trailers: str | None = None) -> str:
        """Commit the working tree as a merge of HEAD and `merge_parent`: writing MERGE_HEAD makes
        git's own commit add the second parent while the tree stays exactly what ugh wrote."""
        self._git_path("MERGE_HEAD").write_text(f"{merge_parent}\n", encoding="utf-8")
        self.stage_all()
        full = message if trailers is None else f"{message}\n\n{trailers}"
        self._git("commit", "-q", "-m", full)
        head = self.head()
        if head is None:
            raise GitError("merge commit succeeded but HEAD is unresolved")
        return head


def init_store(repo_path: str | Path) -> tuple[GitBinding, Path]:
    """`ugh init`: bind (or create) a git repo and ensure `.sgt/` exists."""
    gb = GitBinding(repo_path)
    gb.init()
    sgt_dir = Path(repo_path) / ".sgt"
    sgt_dir.mkdir(parents=True, exist_ok=True)
    return gb, sgt_dir
