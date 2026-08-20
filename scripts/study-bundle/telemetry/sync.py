#!/usr/bin/env python3
"""Push the local event log to the study, and keep a heartbeat going.

Three modes:

  study-sync              push once and say what happened
  study-sync --daemon     push every 20 seconds, quietly, until killed
  study-sync --final      push, then insist on a clean result before saying so

Nothing here can lose data. The log on disk is the record; this only copies it.
If the network is down the exit status says so and the participant is told to
leave the folder in place, which is the honest instruction: their session is
intact on their own disk.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import client  # noqa: E402
import shim  # noqa: E402

DAEMON_INTERVAL = 20


def resolve_code(explicit: str | None) -> str | None:
    return explicit or client.participant_code()


def _git(args: list[str], cwd, timeout: int = 20) -> str:
    """Run git without appearing in the log as if a participant had run it.

    This function is called every twenty seconds by the daemon. Through the
    PATH shim that produced 450 of the 476 command events in the first pilot
    log, all of them the instrument watching itself. Two guards: skip the shim
    when resolving the binary, and tell the shim to stay quiet in case some
    other path reaches it anyway.
    """
    env = dict(os.environ)
    env["STUDY_NO_LOG"] = "1"
    try:
        out = subprocess.run(
            [shim.resolve("git") or "git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
        return out.stdout.strip()
    except Exception:
        return ""


def capture_repo_outcome(with_tests: bool = False) -> None:
    """Record what the participant has actually done to the project.

    Four of the six requests are judged by the state of the code, not by
    anything written on a form. Without this the facilitator has nothing to
    score them with: in the pilot they reached the scoring screen, found an
    empty box asking them to paste a script's output, and had no way to obtain
    a copy of the participant's repository at all. Four of six requests were
    unscorable from the console, and they only found the script's name by
    reading source.

    The diff against the `study-start` tag is the answer to "what did they do",
    and it costs milliseconds, so it rides along with every sync. Running the
    test suite is expensive and noisy, so it only happens on a final sync.
    """
    work = client.study_home() / "work"
    if not (work / ".git").exists():
        return

    baseline = "study-start"
    if not _git(["rev-parse", "--verify", "--quiet", baseline], work):
        baseline = _git(["rev-list", "--max-parents=0", "HEAD"], work).split("\n")[0]
    if not baseline:
        return

    stat = _git(["diff", "--stat", baseline], work)
    names = _git(["diff", "--name-status", baseline], work)
    head = _git(["rev-parse", "--short", "HEAD"], work)
    dirty = _git(["status", "--porcelain"], work)

    tests = None
    if with_tests:
        python = work / ".venv" / "bin" / "python"
        if python.exists():
            try:
                out = subprocess.run(
                    [str(python), "-m", "pytest", "-q"],
                    cwd=str(work),
                    capture_output=True,
                    text=True,
                    timeout=420,
                )
                lines = [x for x in (out.stdout + out.stderr).strip().splitlines() if x.strip()]
                tests = lines[-1][:300] if lines else "no output"
            except Exception as exc:
                tests = f"could not run the tests: {exc}"[:300]

    client.append(
        "repo",
        name="outcome",
        text=stat[:4000] or "no changes yet",
        head=head,
        files=names[:4000],
        uncommitted=len([x for x in dirty.splitlines() if x.strip()]),
        tests=tests,
        baseline=baseline,
    )


def once(code: str, quiet: bool, verbose: bool = False, with_tests: bool = False) -> int:
    try:
        capture_repo_outcome(with_tests=with_tests)
        sent, skipped, pending = client.sync(code, verbose=verbose)
        uploaded = len(client.read_ledger())
        client.heartbeat(code, uploaded=uploaded)
        if not quiet:
            if pending == 0:
                print(f"Nothing new to send. {uploaded} records already delivered.")
            else:
                note = f", {skipped} already there" if skipped else ""
                print(f"Sent {sent} of {pending} records{note}. {uploaded} delivered in total.")
        return 0
    except client.UploadError as exc:
        if not quiet:
            print(f"Could not reach the study: {exc}", file=sys.stderr)
            print(
                "Your session is safe on this machine. Tell your facilitator and leave the folder"
                " in place.",
                file=sys.stderr,
            )
        return 1


def daemon(code: str) -> int:
    # A long-lived pusher so the facilitator's screen is never more than twenty
    # seconds behind. Failures are silent on purpose: a participant should not
    # see network noise while they are working.
    while True:
        try:
            capture_repo_outcome()
            client.sync(code)
            client.heartbeat(code, uploaded=len(client.read_ledger()))
        except Exception:
            pass
        time.sleep(DAEMON_INTERVAL)


def main() -> int:
    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument("--code", default=None)
    parser.add_argument("--daemon", action="store_true")
    parser.add_argument("--final", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    code = resolve_code(args.code)
    if not code:
        if not args.quiet:
            print(
                "No participant code on this machine. Run install/setup.sh with the code from your"
                " study page.",
                file=sys.stderr,
            )
        return 2

    if args.daemon:
        return daemon(code)

    status = once(code, quiet=args.quiet, verbose=args.verbose, with_tests=args.final)

    if args.final:
        if status != 0:
            return status
        remaining = len(
            [e for e in client.read_log() if e.get("id") not in client.read_ledger()]
        )
        if remaining:
            print(f"{remaining} records still waiting. Run this again in a moment.", file=sys.stderr)
            return 1
        print()
        print("All of it is with us. You can run study-cleanup now.")
    return status


if __name__ == "__main__":
    os.environ.setdefault("STUDY_HOME", str(Path(__file__).resolve().parent.parent))
    sys.exit(main())
