#!/usr/bin/env python3
"""How long the sync daemon lives.

    python3 scripts/study-bundle/tests/test_sync_daemon.py

No emulator and no network: the daemon's pushes are pointed at a dead port and
allowed to fail, because what is being tested is when the daemon stops, not what
it uploads. Its siblings cover the rest -- test_shim.py the local log,
test_telemetry.py the upload.

The property being protected is that the daemon does not outlive the session.
bin/study-shell starts it in the background and traps EXIT to kill it, but that
trap only runs when the shell exits cleanly; a participant who closes the
terminal window instead leaves the daemon orphaned onto launchd. It then keeps
pushing every twenty seconds, and because client.telemetry_dir() creates the
directory it is about to write into, the study folder a participant deletes
afterwards reappears seconds later. Two pilots deleted `~/Downloads/study-*`,
watched it come back, and had no way to tell what was doing it. Three orphaned
daemons were still running days after their sessions ended.

So: an orphan stops, and a daemon whose bundle has been deleted stops without
recreating it.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

BUNDLE = Path(__file__).resolve().parent.parent

passed = 0
failed = 0


def check(label: str, condition: bool, detail: str = "") -> None:
    global passed, failed
    if condition:
        passed += 1
        print(f"  ok    {label}")
    else:
        failed += 1
        print(f"  FAIL  {label}" + (f"  — {detail}" if detail else ""))


def make_bundle() -> Path:
    """A bundle with just enough in it for sync.py to start and tick."""
    home = Path(tempfile.mkdtemp(prefix="study-daemon-test-"))
    shutil.copytree(BUNDLE / "telemetry", home / "telemetry")
    (home / "work").mkdir()
    # A dead port, so every push fails fast instead of reaching the real study.
    (home / "study.json").write_text(
        json.dumps(
            {
                "condition": "git",
                "project": "coursecraft",
                "bundleVersion": "test",
                "firestoreHost": "127.0.0.1:9999",
            }
        )
    )
    return home


def env_for(home: Path) -> dict[str, str]:
    env = dict(os.environ)
    env["STUDY_HOME"] = str(home)
    env["STUDY_CODE"] = "TEST-CODE"
    env.pop("FIRESTORE_EMULATOR_HOST", None)
    return env


def alive(pid: int) -> bool:
    """Whether pid is a live process, counting a zombie as dead.

    An orphan is reparented onto launchd, which reaps it, so os.kill would be
    enough there. Checked through ps anyway, so the same helper can be trusted
    in the case where the parent is still around and has not waited yet.
    """
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError):
        return False
    state = subprocess.run(
        ["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True
    ).stdout.strip()
    return bool(state) and not state.startswith("Z")


def wait_until(predicate, limit: float = 20.0, step: float = 0.25) -> bool:
    deadline = time.monotonic() + limit
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(step)
    return predicate()


def test_orphan_stops() -> None:
    """The shell that started the daemon is gone, so the daemon should go."""
    print("\nAn orphaned daemon stops")
    home = make_bundle()
    pidfile = home / "daemon.pid"
    launcher = subprocess.Popen(
        [
            "bash",
            "-c",
            f'"{sys.executable}" "{home}/telemetry/sync.py" --daemon & '
            f'echo $! > "{pidfile}"; wait',
        ],
        env=env_for(home),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    if not wait_until(lambda: pidfile.is_file() and pidfile.read_text().strip(), limit=10):
        check("the daemon started", False, "no pid file")
        launcher.kill()
        shutil.rmtree(home, ignore_errors=True)
        return
    pid = int(pidfile.read_text().strip())
    check("the daemon started and stayed up while its shell was alive", alive(pid))

    # What closing the terminal window does: the shell dies without running its
    # EXIT trap, and the daemon is reparented onto pid 1.
    launcher.send_signal(signal.SIGKILL)
    launcher.wait(timeout=10)

    check(
        "the daemon stops once the shell that started it is gone",
        wait_until(lambda: not alive(pid)),
        f"pid {pid} still running 20s after its parent was killed",
    )

    if alive(pid):
        os.kill(pid, signal.SIGKILL)
    shutil.rmtree(home, ignore_errors=True)


def test_deleted_bundle_stays_deleted() -> None:
    """The participant deletes the folder, so it should stay deleted."""
    print("\nA deleted bundle is not recreated")
    home = make_bundle()
    # Parent is this test, which is alive and is not pid 1, so the only thing
    # that can stop this daemon is noticing its own bundle has gone.
    proc = subprocess.Popen(
        [sys.executable, str(home / "telemetry" / "sync.py"), "--daemon"],
        env=env_for(home),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    # Let it complete a tick, so the test is about a running daemon rather than
    # one that had not started yet.
    wait_until(lambda: (home / "telemetry" / "state.json").is_file(), limit=10)
    check("the daemon wrote its state before the folder went", proc.poll() is None)

    shutil.rmtree(home, ignore_errors=True)

    check(
        "the daemon stops when its bundle has been deleted",
        wait_until(lambda: proc.poll() is not None),
        "still running 20s after its bundle was deleted",
    )
    check(
        "the deleted folder is not recreated",
        not home.exists(),
        f"{home} came back: {sorted(p.name for p in home.rglob('*'))}",
    )

    if proc.poll() is None:
        proc.kill()
        proc.wait(timeout=10)
    shutil.rmtree(home, ignore_errors=True)


def main() -> int:
    test_orphan_stops()
    test_deleted_bundle_stays_deleted()
    print(f"\n{passed} passed, {failed} failed\n")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
