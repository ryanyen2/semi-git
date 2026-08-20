#!/usr/bin/env python3
"""Logging wrapper for the commands a participant runs themselves.

The assistant's own tool calls arrive through hooks. What the participant types
into the terminal does not, and it is half the data: which commands they reach
for first, whether they check their work, how they get out of trouble.

The wrapper is a passthrough. It finds the real binary further along PATH, runs
it with the same argv on the same file descriptors so pagers and interactive
prompts still work, and exits with the same status. It records the command line,
the exit code and how long it took. It never inspects or alters output.

  usage: shim.py <command> [args...]
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import client  # noqa: E402

# Repo snapshots are taken after commands that could move the tree, so the
# analysis can tell an edit the participant made by hand from one the assistant
# made. A snapshot costs two extra git processes, so it is not worth taking
# after a read. With an editor open, reads are most of the traffic: the pilot
# log holds 376 snapshots for 26 real commands.
GIT_WRITES = {
    "add", "am", "apply", "bisect", "branch", "checkout", "cherry-pick", "clean",
    "commit", "fetch", "filter-branch", "filter-repo", "gc", "init", "merge", "mv",
    "notes", "pull", "rebase", "replace", "reset", "restore", "revert", "rm",
    "sparse-checkout", "stash", "submodule", "switch", "tag", "update-index",
    "update-ref", "worktree",
}
SGT_READS = {
    "log", "now", "status", "show", "why", "recall", "diff", "map", "blame",
    "explain", "drift", "feature", "sessions", "help",
}

# Commands an editor runs on its own, to keep its own views in step. They are
# not moves the participant made, and counting them as such would swamp every
# sequence measure the study has. Recorded, so "the editor was live" stays
# visible, but flagged.
EDITOR_POLL = {
    "status", "ls-files", "rev-parse", "config", "for-each-ref", "symbolic-ref",
    "show-ref", "check-ignore", "check-attr", "merge-base", "remote", "version",
    "cat-file", "ls-tree", "var",
}


def is_a_shim(path: Path) -> bool:
    """Recognise our own wrapper, whatever directory it turned up in."""
    try:
        with path.open("rb") as handle:
            return b"telemetry/shim.py" in handle.read(512)
    except Exception:
        return False


def resolve(name: str) -> str | None:
    """The next binary called `name` on PATH, skipping our own wrappers.

    Two independent guards against calling ourselves. The shim directory is
    baked into each wrapper as an environment variable, and any candidate that
    looks like one of our wrappers is skipped on sight. A shim that recursed
    would spawn processes until the machine gave up, in the middle of somebody's
    session, so one guard is not enough.
    """
    shim_dir = os.environ.get("STUDY_SHIM_DIR", "")
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        if not entry:
            continue
        if shim_dir and os.path.realpath(entry) == os.path.realpath(shim_dir):
            continue
        candidate = Path(entry) / name
        if not (candidate.is_file() and os.access(candidate, os.X_OK)):
            continue
        if is_a_shim(candidate):
            continue
        return str(candidate)

    # PATH is not always the study's. An editor started from the Dock inherits
    # the login shell's environment, and `sgt` lives in a virtualenv only the
    # session shell knows about. Without this fallback the wrapper would report
    # "command not found" for a tool that is installed two directories away,
    # and the participant would conclude the tool is broken.
    fallback = client.study_home() / "bin" / name
    if fallback.is_file() and os.access(fallback, os.X_OK) and not is_a_shim(fallback):
        return str(fallback)
    return None


def tree_hash() -> str | None:
    """A fingerprint of the working tree, including uncommitted and new files.

    Read-only, and it has to stay that way.

    This previously ran `git add -A --intent-to-add` so that untracked files
    would show up in a diff. That writes index entries pointing at git's empty
    blob, which is not in the object store, and from then on **every**
    `git write-tree` in that repository fails with

        error: invalid object 100644 e69de29... for '.sgt/oracle.json'
        fatal: git-write-tree: error building trees

    sgt calls write-tree whenever it snapshots, so a measurement instrument
    quietly disabled `sgt save` and `sgt revert` for the rest of the session --
    the exact half of the study it was there to measure. It also staged sgt's
    own `.sgt/` runtime directory, which was never anybody's edit.

    `git status --porcelain` reports tracked modifications and untracked files
    without touching the index at all, which is what was wanted in the first
    place. `.sgt/` is excluded because the tool rewrites it constantly and none
    of that is a change the participant made.
    """
    work = client.study_home() / "work"
    git = resolve("git")
    if not git or not (work / ".git").exists():
        return None
    try:
        status = subprocess.run(
            [git, "status", "--porcelain=v1", "--untracked-files=all"],
            cwd=work,
            capture_output=True,
            text=True,
            timeout=15,
        )
        head = subprocess.run(
            [git, "rev-parse", "HEAD"],
            cwd=work,
            capture_output=True,
            text=True,
            timeout=15,
        )
        lines = [
            line
            for line in status.stdout.splitlines()
            if ".sgt/" not in line and not line.strip().endswith(".sgt")
        ]
        import hashlib

        basis = (head.stdout.strip() + "\n" + "\n".join(sorted(lines))).encode()
        return hashlib.sha256(basis).hexdigest()[:16]
    except Exception:
        return None


def subcommand(args: list[str]) -> str:
    """The first word that is not a flag. `git -c foo=bar status` is a status."""
    skip_value = False
    for arg in args:
        if skip_value:
            skip_value = False
            continue
        if arg == "-c" or arg == "-C":
            skip_value = True
            continue
        if arg.startswith("-"):
            continue
        return arg
    return ""


def moves_the_tree(name: str, sub: str) -> bool:
    if name == "git":
        return sub in GIT_WRITES
    if name == "sgt":
        return sub not in SGT_READS
    return False


SNAPSHOT_EVERY_SECONDS = 30


def should_snapshot(name: str, sub: str, auto: bool) -> bool:
    """Always after something that can move the tree; otherwise at most twice a
    minute.

    A snapshot is how a hand edit is inferred: the tree moved and no assistant
    edit accounts for it. So they cannot only follow writes, or an edit made by
    hand and never committed would never be seen. But they cost two git
    processes, and an open editor runs dozens of reads a minute, so the ones
    that ride along with a read are rate-limited and the editor's own polling
    never triggers one.
    """
    if moves_the_tree(name, sub):
        return True
    if auto:
        return False
    try:
        last = float(client.read_state().get("lastSnapshotAt") or 0)
        if time.time() - last < SNAPSHOT_EVERY_SECONDS:
            return False
        client.write_state({"lastSnapshotAt": time.time()})
        return True
    except Exception:
        return False


def surface() -> str:
    """Where this command was run: the terminal, the editor, or the assistant.

    The launchers say so in the environment rather than the analysis guessing
    from argv later. Guessing works until it doesn't: `git log` typed in a
    terminal and `git log` run by an editor view are the same string, and the
    difference between them is the whole point of adding an editor to the study.
    """
    if os.environ.get("CLAUDECODE"):
        return "agent"
    return os.environ.get("STUDY_SURFACE") or "terminal"


def parent_name() -> str | None:
    """The command that spawned us, so an editor's own terminal is separable
    from the editor's views -- both inherit the same environment."""
    try:
        out = subprocess.run(
            ["ps", "-o", "comm=", "-p", str(os.getppid())],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return (out.stdout.strip().rsplit("/", 1)[-1] or None) if out.returncode == 0 else None
    except Exception:
        return None


SYNC_EVERY_SECONDS = 90


def maybe_sync() -> None:
    """Push to the study occasionally, from wherever the participant is working.

    The session shell starts a background syncer, and for a while that was the
    only thing that ever uploaded. It is not enough. In the pilot the shell was
    never the long-lived thing the design assumed, the syncer therefore never
    ran, and **325 recorded events sat on disk while the facilitator's screen
    showed five** -- a stale heartbeat and no idea that the session it was
    watching had gone dark twenty minutes earlier.

    Nothing was lost, because the local log is the record and a final sync
    recovers all of it. What was lost was the facilitator's ability to see a
    participant in trouble while there was still time to help, which is the
    entire purpose of watching.

    So uploading now rides on the thing that actually happens throughout a
    session: the participant running commands. Detached and time-limited, so it
    can never make a command feel slow, and failures stay silent -- a participant
    must not see network noise while they are working.
    """
    try:
        state = client.read_state()
        last = float(state.get("lastSyncAt") or 0)
        if time.time() - last < SYNC_EVERY_SECONDS:
            return
        client.write_state({"lastSyncAt": time.time()})
        sync = Path(__file__).resolve().parent / "sync.py"
        if not sync.exists():
            return
        subprocess.Popen(
            [sys.executable, str(sync), "--quiet"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception:
        pass


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: shim.py <command> [args...]", file=sys.stderr)
        return 2

    name = sys.argv[1]
    args = sys.argv[2:]
    real = resolve(name)

    if not real:
        print(f"{name}: command not found", file=sys.stderr)
        try:
            client.append("command", name=name, text=" ".join([name, *args]), ok=False, exitCode=127)
        except Exception:
            pass
        return 127

    started = time.time()
    # The child owns the terminal, including Ctrl-C. If the parent died first we
    # would lose the exit code, which is the thing we came for.
    previous = signal.signal(signal.SIGINT, signal.SIG_IGN)
    # Anything this command starts is that command's business, not a move the
    # participant made. The wrapper reads this and steps aside, so a nested call
    # is neither recorded nor slowed.
    child_env = dict(os.environ)
    child_env["STUDY_PARENT_TOOL"] = name
    try:
        code = subprocess.call([real, *args], env=child_env)
    except KeyboardInterrupt:
        code = 130
    except Exception as exc:
        print(f"[study] could not run {name}: {exc}", file=sys.stderr)
        code = 126
    finally:
        signal.signal(signal.SIGINT, previous)

    duration = int((time.time() - started) * 1000)

    # The instrument must not appear in its own measurements. `study-sync` runs
    # git to work out what the participant has changed, and it inherits this
    # PATH, so before this guard existed those calls were recorded as if the
    # participant had typed them: 450 of the 476 command events in the first
    # pilot log were the sync daemon looking at the repo every twenty seconds.
    if os.environ.get("STUDY_NO_LOG"):
        return code

    maybe_sync()
    try:
        # Commands the assistant runs arrive here too, because it inherits this
        # PATH. Marking them lets the analysis tell "they typed it" from "they
        # asked for it", and lets the pipeline fold each one together with the
        # matching hook record rather than counting it twice.
        where = surface()
        sub = subcommand(args)
        # Machine, not participant. Either the editor keeping its own views in
        # step, or an extension working out which Python this project uses --
        # opening a file in the git arm installed the Python extension pack,
        # which then probed the interpreter a dozen times. A participant who
        # wants to run Python does it in a terminal, and that arrives here as
        # `terminal`.
        polling = where == "editor" and (
            (name == "git" and sub in EDITOR_POLL) or name in ("python", "python3")
        )
        client.append(
            "command",
            name=name,
            text=" ".join([name, *args]),
            exitCode=code,
            ok=(code == 0),
            durationMs=duration,
            cwd=os.getcwd(),
            agent=(where == "agent"),
            surface=where,
            parent=parent_name(),
            auto=polling or None,
            sessionId=os.environ.get("CLAUDE_CODE_SESSION_ID"),
        )
        if should_snapshot(name, sub, auto=polling):
            digest = tree_hash()
            if digest:
                client.append("repo", name="tree", treeHash=digest)
    except Exception:
        pass

    return code


if __name__ == "__main__":
    sys.exit(main())
