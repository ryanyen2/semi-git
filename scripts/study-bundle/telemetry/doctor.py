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
# The model the study runs on. A tripwire on purpose: changing which model the
# assistant uses is a change to the condition, so it should require editing a
# line that says so, not just a field in the console.
EXPECTED_MODEL = "claude-sonnet-5"


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

        # A key that is present and a key that works look identical from here,
        # and the difference is invisible during a session: the tool falls back
        # to deterministic labels and lexical search and says nothing. A
        # participant would then rate a degraded tool, for a reason nobody could
        # reconstruct afterwards. One real call is the only way to know.
        if len(key) > 20 and not args.skip_ping:
            rc, out = run(
                [
                    str(home / "toolenv" / "bin" / "python"), "-c",
                    "import sys;from sgt.config import get_client;"
                    "get_client(sys.argv[1]).embeddings.create("
                    "model='text-embedding-3-small',input=['ping'],dimensions=256);"
                    "print('ok')",
                    str(home / "work"),
                ],
                timeout=90,
            )
            checks.add(
                "tool_key_live",
                rc == 0 and "ok" in out,
                "answered" if rc == 0 and "ok" in out else out.strip().splitlines()[-1][:200]
                if out.strip() else "no answer",
            )

    # 7. The isolated assistant profile
    profile = home / ".claude-study"
    isolated = profile.exists() and (profile / "settings.json").exists()
    checks.add(
        "assistant_profile",
        isolated,
        f"{profile}" if isolated else "missing; re-run install/setup.sh",
    )

    # 7b. On the model the study pinned. Two participants on different models
    # are not two runs of the same study, and nothing in a session makes the
    # difference visible, so it is checked here or not at all.
    model = ""
    if isolated:
        try:
            model = str(json.loads((profile / "settings.json").read_text()).get("model") or "")
        except Exception:
            model = ""
    checks.add(
        "assistant_model",
        model.startswith(EXPECTED_MODEL),
        model or "no model pinned; re-run install/setup.sh",
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
            # This question is the instrument's, not the participant's. The
            # hooks in that profile record every prompt, and without this the
            # check writes one into the log that nobody typed.
            env["STUDY_NO_LOG"] = "1"
            # Their own key must not leak into the study session. Removing it
            # here is the difference between billing us and billing them.
            env.pop("ANTHROPIC_API_KEY", None)
            env.pop("ANTHROPIC_AUTH_TOKEN", None)
            started = time.time()
            try:
                # JSON, because the reply alone cannot tell us which model
                # produced it. `modelUsage` names the model that actually ran,
                # which is the only way to catch a pinned model that was
                # ignored -- a session that looks entirely normal and is not
                # comparable with any other.
                proc = subprocess.run(
                    [claude, "-p", "Reply with exactly: ok", "--output-format", "json"],
                    capture_output=True,
                    text=True,
                    timeout=PING_TIMEOUT,
                    env=env,
                    cwd=str(home),
                )
                answered = ""
                ran_on = ""
                try:
                    body = json.loads(proc.stdout)
                    answered = str(body.get("result") or "")
                    ran_on = ", ".join(body.get("modelUsage") or {})
                except Exception:
                    answered = (proc.stdout or "").strip()
                ok = proc.returncode == 0 and "ok" in answered.lower()
                first = (answered or proc.stderr or "").strip().splitlines()
                checks.add(
                    "assistant_ping",
                    ok,
                    f"answered in {time.time() - started:.0f}s"
                    if ok
                    else (first[0][:120] if first else "no answer"),
                )
                if ran_on:
                    checks.add(
                        "assistant_model_live",
                        EXPECTED_MODEL in ran_on,
                        ran_on,
                    )
            except subprocess.TimeoutExpired:
                # A wrong key does not fail fast, it retries. That is why this
                # has a hard timeout rather than waiting for an error.
                checks.add(
                    "assistant_ping",
                    False,
                    f"no answer in {PING_TIMEOUT}s, which usually means the key is wrong",
                )

    # 9b. The editor, and the one extension this condition is allowed.
    #
    # Checked rather than assumed, because a missing editor is not visible in
    # the shell: the session runs, the participant works entirely in the
    # terminal, and half of what the study set out to compare is simply absent
    # from that participant's data with nothing to mark it.
    code_cli = shutil.which("code") or next(
        (
            p
            for p in (
                "/Applications/Visual Studio Code.app/Contents/Resources/app/bin/code",
                str(Path.home() / "Applications/Visual Studio Code.app/Contents/Resources/app/bin/code"),
                "/usr/share/code/bin/code",
                "/snap/bin/code",
            )
            if os.access(p, os.X_OK)
        ),
        None,
    )
    if not code_cli:
        checks.add("editor", False, "Visual Studio Code not found; tell your facilitator")
        checks.add("editor_extension", False, "no editor")
    else:
        rc, out = run([code_cli, "--version"], timeout=60)
        checks.add("editor", rc == 0, out.splitlines()[0] if out else "could not run")

        profile = home / ".vscode-study"
        rc, out = run(
            [
                code_cli,
                "--user-data-dir", str(profile),
                "--extensions-dir", str(profile / "extensions"),
                "--list-extensions", "--show-versions",
            ],
            timeout=90,
        )
        installed = [line.strip() for line in out.splitlines() if "." in line and "@" in line]
        wanted = "semi-git" if meta.get("condition") == "sgt" else "gitlens"
        found = [x for x in installed if wanted in x.lower()]
        checks.add(
            "editor_extension",
            bool(found),
            ", ".join(found) if found else f"no {wanted} in the study profile; re-run install/setup.sh",
        )

        # The editor has to be the same in both halves, and it does not stay
        # that way on its own: VS Code offers Python support the first time a
        # .py file is opened, so one condition can finish with 198 MB of
        # tooling the other never saw. Anything missing or anything extra is
        # reported, because both directions break the comparison.
        expected = [str(x).split("@")[0].lower() for x in (meta.get("editorExtensions") or [])]
        if expected:
            have = {x.split("@")[0].lower() for x in installed}
            missing = [x for x in expected if x not in have]
            extra = sorted(
                x for x in have if x not in expected and wanted not in x
            )
            checks.add(
                "editor_toolset",
                not missing and not extra,
                "the same in both halves"
                if not missing and not extra
                else "; ".join(
                    filter(
                        None,
                        [
                            f"missing: {', '.join(missing)}" if missing else "",
                            f"unexpected: {', '.join(extra)}" if extra else "",
                        ],
                    )
                ),
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
