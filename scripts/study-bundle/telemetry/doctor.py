#!/usr/bin/env python3
"""Check that this machine is actually ready, and say so on the study page.

Every check either passes or explains itself in one line. The results go to the
participant's setup page as a list that fills in by itself, so nobody has to
read a terminal aloud over a video call, and the facilitator can see the same
list from their side.

The rule this file exists to enforce: a red line here costs two minutes now and
a whole request later. Both pilots lost work to environments that looked fine.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import client  # noqa: E402

EXPECTED_TESTS = 38
PING_TIMEOUT = 75


class Checks:
    def __init__(self) -> None:
        self.results: dict[str, dict[str, object]] = {}
        self.order: list[str] = []

    def add(self, key: str, ok: bool, detail: str = "") -> bool:
        self.results[key] = {"ok": bool(ok), "detail": detail[:300]}
        self.order.append(key)
        mark = "  ok  " if ok else " FAIL "
        line = f"[{mark}] {key}"
        if detail:
            line += f"  — {detail}"
        print(line, flush=True)
        return ok

    def all_ok(self) -> bool:
        return all(bool(r["ok"]) for r in self.results.values())


def run(cmd: list[str], cwd: Path | None = None, timeout: int = 60) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return proc.returncode, (proc.stdout + proc.stderr).strip()
    except subprocess.TimeoutExpired:
        return 124, f"timed out after {timeout}s"
    except FileNotFoundError:
        return 127, "command not found"
    except Exception as exc:
        return 126, str(exc)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--code", default=None)
    parser.add_argument("--skip-ping", action="store_true", help="skip the assistant round trip")
    parser.add_argument("--skip-tests", action="store_true")
    args = parser.parse_args()

    home = client.study_home()
    meta = client.study_meta()
    code = args.code or client.participant_code()
    checks = Checks()

    print()
    print("Checking this machine.")
    print()

    # 1. uv
    uv = shutil.which("uv")
    checks.add("uv", bool(uv), uv or "not on PATH; re-run install/setup.sh")

    # 2 and 3. The project environment
    venv_python = home / "work" / ".venv" / "bin" / "python"
    if venv_python.exists():
        rc, out = run([str(venv_python), "--version"], timeout=30)
        version = out.strip()
        checks.add("python", rc == 0 and "3.12" in version, version or "could not run")
        rc, out = run([str(venv_python), "-c", "import pytest; print(pytest.__version__)"], timeout=60)
        checks.add("venv", rc == 0, f"pytest {out}" if rc == 0 else out)
    else:
        checks.add("python", False, "no project environment; re-run install/setup.sh")
        checks.add("venv", False, "no project environment")

    # 4. The suite the participant will lean on as a safety net
    if args.skip_tests:
        checks.add("tests", True, "skipped")
    elif venv_python.exists():
        rc, out = run([str(venv_python), "-m", "pytest", "-q"], cwd=home / "work", timeout=420)
        tail = out.strip().splitlines()[-1] if out.strip() else ""
        passed = re.search(r"(\d+) passed", tail)
        count = int(passed.group(1)) if passed else 0
        checks.add(
            "tests",
            rc == 0 and count == EXPECTED_TESTS,
            tail or "no output",
        )
    else:
        checks.add("tests", False, "no project environment")

    # 5, 6 and 7. The history tool, in the condition that has one
    if meta.get("condition") == "sgt":
        sgt = home / "bin" / "sgt"
        rc, out = run([str(sgt), "--version"], timeout=90)
        checks.add("tool", rc == 0, out.splitlines()[0] if out else "could not run")
        warm = (home / "work" / ".sgt").exists()
        checks.add(
            "warm",
            warm,
            "history view is preloaded" if warm else "not preloaded; the first command will be slow",
        )

        # The key that makes plain-English selection work. It was missing from
        # these checks, which meant a participant could see an entirely green
        # page while the one capability that most distinguishes this condition
        # quietly did nothing -- and would then rate the tool worse than it is,
        # for a reason nobody could reconstruct afterwards.
        env_file = home / "work" / ".env"
        key = ""
        if env_file.exists():
            for line in env_file.read_text(errors="replace").splitlines():
                if line.startswith("OPENAI_API_KEY="):
                    key = line.split("=", 1)[1].strip()
        checks.add(
            "tool_key",
            len(key) > 20,
            "issued for this session"
            if len(key) > 20
            else "missing, so plain-English commands will not work; tell your facilitator",
        )

    # 7. The isolated assistant profile
    profile = home / ".claude-study"
    isolated = profile.exists() and (profile / "settings.json").exists()
    checks.add(
        "assistant_profile",
        isolated,
        f"{profile}" if isolated else "missing; re-run install/setup.sh",
    )

    # 8. The key the assistant will use
    key_file = profile / "api-key"
    key = key_file.read_text().strip() if key_file.exists() else ""
    checks.add(
        "assistant_key",
        len(key) > 20,
        "issued for this session" if len(key) > 20 else "no key; check with your facilitator",
    )

    # 9. Does it actually answer? This is the check that catches a key that is
    # present but wrong, which is otherwise invisible until the session starts.
    if args.skip_ping:
        checks.add("assistant_ping", True, "skipped")
    else:
        claude = shutil.which("claude")
        if not claude:
            checks.add("assistant_ping", False, "claude is not installed; re-run install/setup.sh")
        else:
            env = dict(os.environ)
            env["CLAUDE_CONFIG_DIR"] = str(profile)
            # Their own key must not leak into the study session. Removing it
            # here is the difference between billing us and billing them.
            env.pop("ANTHROPIC_API_KEY", None)
            env.pop("ANTHROPIC_AUTH_TOKEN", None)
            started = time.time()
            try:
                proc = subprocess.run(
                    [claude, "-p", "Reply with exactly: ok"],
                    capture_output=True,
                    text=True,
                    timeout=PING_TIMEOUT,
                    env=env,
                    cwd=str(home),
                )
                answer = (proc.stdout or proc.stderr).strip().splitlines()
                first = answer[0][:120] if answer else ""
                ok = proc.returncode == 0 and "ok" in (proc.stdout or "").lower()
                checks.add(
                    "assistant_ping",
                    ok,
                    f"answered in {time.time() - started:.0f}s" if ok else first or "no answer",
                )
            except subprocess.TimeoutExpired:
                # A wrong key does not fail fast, it retries. That is why this
                # has a hard timeout rather than waiting for an error.
                checks.add(
                    "assistant_ping",
                    False,
                    f"no answer in {PING_TIMEOUT}s, which usually means the key is wrong",
                )

    # 10. Can we hear this machine at all?
    if not code:
        checks.add("telemetry", False, "no participant code; re-run install/setup.sh with your code")
    else:
        client.append("session", name="doctor", text="setup check")
        try:
            sent, skipped, pending = client.sync(code)
            client.heartbeat(code, checks=checks.results, uploaded=len(client.read_ledger()))
            checks.add("telemetry", True, f"{sent + skipped} of {pending} records delivered")
            # Re-send, so the final state of every check reaches the page.
            client.heartbeat(code, checks=checks.results, uploaded=len(client.read_ledger()))
        except client.UploadError as exc:
            checks.add("telemetry", False, str(exc)[:200])

    print()
    if checks.all_ok():
        print("Everything passed. Go back to the study page and carry on.")
    else:
        failed = [k for k, v in checks.results.items() if not v["ok"]]
        print(f"{len(failed)} check(s) failed: {', '.join(failed)}")
        print("Show your facilitator before starting. It is much cheaper to fix now.")
    print()

    if os.environ.get("STUDY_DOCTOR_JSON"):
        print(json.dumps(checks.results, indent=2))

    return 0 if checks.all_ok() else 1


if __name__ == "__main__":
    os.environ.setdefault("STUDY_HOME", str(Path(__file__).resolve().parent.parent))
    sys.exit(main())
