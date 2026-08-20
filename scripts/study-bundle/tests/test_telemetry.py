#!/usr/bin/env python3
"""End-to-end check of the recording path, against a local Firestore emulator.

Run it with the emulator up:

    java -jar ~/.cache/firebase/emulators/cloud-firestore-emulator-v*.jar \
        --host=127.0.0.1 --port=8080 &
    FIRESTORE_EMULATOR_HOST=127.0.0.1:8080 \
        python3 scripts/study-bundle/tests/test_telemetry.py

It builds a throwaway bundle in a temp directory, drives the hook and the
command wrapper the way Claude Code and the session shell would, and checks that
what lands in Firestore is what was recorded on disk. The properties it is
protecting are the ones that cannot be fixed after a session: nothing is lost
when the network drops, and nothing is counted twice when the sync is re-run.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

BUNDLE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BUNDLE / "telemetry"))

CODE = "pytestcode0000000000abcd"
EMULATOR = os.environ.get("FIRESTORE_EMULATOR_HOST", "127.0.0.1:8080")
PROJECT = "sem-git"
BASE = f"http://{EMULATOR}/v1/projects/{PROJECT}/databases/(default)/documents"

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


def clear_previous_run() -> None:
    """Start from an empty participant, so counts mean what they say."""
    for sub in ("events", "devices"):
        try:
            req = urllib.request.Request(f"{BASE}/participants/{CODE}/{sub}?pageSize=500")
            req.add_header("Authorization", "Bearer owner")
            body = json.loads(urllib.request.urlopen(req, timeout=15).read())
        except Exception:
            continue
        for docu in body.get("documents", []):
            name = docu["name"].split("/documents/", 1)[1]
            req = urllib.request.Request(f"{BASE}/{name}", method="DELETE")
            req.add_header("Authorization", "Bearer owner")
            try:
                urllib.request.urlopen(req, timeout=15).read()
            except Exception:
                pass


def seed_participant() -> None:
    body = {
        "fields": {
            "code": {"stringValue": CODE},
            "label": {"stringValue": "P99"},
            "ordinal": {"integerValue": "99"},
            "group": {"integerValue": "1"},
            "blocks": {
                "arrayValue": {
                    "values": [
                        {
                            "mapValue": {
                                "fields": {
                                    "half": {"integerValue": "1"},
                                    "condition": {"stringValue": "sgt"},
                                    "project": {"stringValue": "coursecraft"},
                                    "label": {"stringValue": "Setup A"},
                                }
                            }
                        },
                        {
                            "mapValue": {
                                "fields": {
                                    "half": {"integerValue": "2"},
                                    "condition": {"stringValue": "git"},
                                    "project": {"stringValue": "confplan"},
                                    "label": {"stringValue": "Setup B"},
                                }
                            }
                        },
                    ]
                }
            },
        }
    }
    req = urllib.request.Request(
        f"{BASE}/participants/{CODE}",
        data=json.dumps(body).encode(),
        method="PATCH",
    )
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", "Bearer owner")
    urllib.request.urlopen(req, timeout=15).read()

    secrets = {
        "fields": {
            "anthropicApiKey": {"stringValue": "sk-ant-test-key-0123456789abcdef"},
            "openaiApiKey": {"stringValue": "sk-test-key-0123456789abcdef"},
            "claudeModel": {"stringValue": "claude-sonnet-5"},
        }
    }
    req = urllib.request.Request(
        f"{BASE}/participants/{CODE}/secrets/session",
        data=json.dumps(secrets).encode(),
        method="PATCH",
    )
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", "Bearer owner")
    urllib.request.urlopen(req, timeout=15).read()


def landed_events() -> list[dict]:
    req = urllib.request.Request(f"{BASE}/participants/{CODE}/events?pageSize=300")
    req.add_header("Authorization", "Bearer owner")
    body = json.loads(urllib.request.urlopen(req, timeout=15).read())
    return body.get("documents", [])


def landed_device() -> dict | None:
    req = urllib.request.Request(f"{BASE}/participants/{CODE}/devices?pageSize=10")
    req.add_header("Authorization", "Bearer owner")
    body = json.loads(urllib.request.urlopen(req, timeout=15).read())
    docs = body.get("documents", [])
    return docs[0] if docs else None


def main() -> int:
    print(f"\nEmulator at {EMULATOR}\n")
    seed_participant()
    clear_previous_run()

    home = Path(tempfile.mkdtemp(prefix="study-bundle-test-"))
    shutil.copytree(BUNDLE / "telemetry", home / "telemetry")
    shutil.copytree(BUNDLE / "bin", home / "bin")
    (home / "work").mkdir()
    (home / "study.json").write_text(
        json.dumps({"condition": "sgt", "project": "coursecraft", "bundleVersion": "test"})
    )

    env = dict(os.environ)
    env["STUDY_HOME"] = str(home)
    env["FIRESTORE_EMULATOR_HOST"] = EMULATOR
    env.pop("CLAUDECODE", None)

    py = sys.executable

    # --- provisioning -----------------------------------------------------
    print("Provisioning")
    proc = subprocess.run(
        [py, str(home / "telemetry" / "provision.py"), CODE],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )
    check("accepts a matching bundle", proc.returncode == 0, proc.stdout + proc.stderr)
    check("writes the assistant key", (home / ".claude-study" / "api-key").exists())
    check("writes a key helper that does not prompt", (home / ".claude-study" / "api-key.sh").exists())
    check("writes the .env sgt needs", (home / "work" / ".env").exists())
    check("records the half from the assignment", json.loads((home / "telemetry" / "state.json").read_text()).get("half") == 1)

    settings = json.loads((home / ".claude-study" / "settings.json").read_text())
    check("hooks cover prompts and tools", {"UserPromptSubmit", "PreToolUse", "Stop"} <= set(settings["hooks"]))
    check("hooks are async so nothing blocks a session", settings["hooks"]["Stop"][0]["hooks"][0]["async"] is True)
    check("the assistant will not silently upgrade mid-study", settings["env"]["DISABLE_AUTOUPDATER"] == "1")
    # The model is part of the condition. It is issued per participant and has
    # to arrive in the profile, or two halves of one study ran on two models
    # with nothing in the data saying which.
    check(
        "the model the study issued is the one pinned",
        settings.get("model") == "claude-sonnet-5"
        and settings["env"].get("ANTHROPIC_MODEL") == "claude-sonnet-5",
        json.dumps({"model": settings.get("model"), "env": settings.get("env")}),
    )

    # The check that matters most: the wrong bundle must be refused.
    (home / "study.json").write_text(
        json.dumps({"condition": "git", "project": "coursecraft", "bundleVersion": "test"})
    )
    proc = subprocess.run(
        [py, str(home / "telemetry" / "provision.py"), CODE],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )
    check(
        "refuses a bundle the participant is not assigned",
        proc.returncode != 0 and "not one of the two" in proc.stdout,
        proc.stdout,
    )
    (home / "study.json").write_text(
        json.dumps({"condition": "sgt", "project": "coursecraft", "bundleVersion": "test"})
    )

    # --- which study this bundle belongs to -------------------------------
    print("\nKnowing which study to talk to")
    probe = (
        "import sys; sys.path.insert(0, %r); import client; print(client.firestore_base())"
        % str(home / "telemetry")
    )
    clean = {k: v for k, v in env.items() if k not in ("FIRESTORE_EMULATOR_HOST", "STUDY_FIRESTORE_HOST")}
    meta = json.loads((home / "study.json").read_text())

    meta["firestoreHost"] = "127.0.0.1:9999"
    (home / "study.json").write_text(json.dumps(meta))
    out = subprocess.run([py, "-c", probe], capture_output=True, text=True, env=clean, timeout=60)
    check(
        "a rehearsal bundle points at the rehearsal study on its own",
        "127.0.0.1:9999" in out.stdout,
        out.stdout + out.stderr,
    )

    meta["firestoreHost"] = ""
    (home / "study.json").write_text(json.dumps(meta))
    out = subprocess.run([py, "-c", probe], capture_output=True, text=True, env=clean, timeout=60)
    check(
        "a real bundle points at the real study",
        "firestore.googleapis.com" in out.stdout,
        out.stdout + out.stderr,
    )

    # --- the hook ---------------------------------------------------------
    print("\nRecording what the assistant does")
    payloads = [
        ("UserPromptSubmit", {"session_id": "s1", "prompt": "remove the waitlist feature f-02c4a091"}),
        ("PreToolUse", {"session_id": "s1", "tool_name": "Bash", "tool_input": {"command": "git log --oneline"}}),
        ("PreToolUse", {"session_id": "s1", "tool_name": "Edit", "tool_input": {"file_path": "app/waitlist.py", "old_string": "a", "new_string": "b"}}),
        ("PostToolUse", {"session_id": "s1", "tool_name": "Edit", "tool_input": {"file_path": "app/waitlist.py"}}),
        ("Stop", {"session_id": "s1"}),
    ]
    for name, payload in payloads:
        subprocess.run(
            [py, str(home / "telemetry" / "hook.py"), name],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            env=env,
            timeout=30,
        )

    log = [json.loads(x) for x in (home / "telemetry" / "events.jsonl").read_text().splitlines() if x.strip()]
    prompts = [e for e in log if e["kind"] == "prompt"]
    check("the prompt is captured verbatim", any("f-02c4a091" in (e.get("text") or "") for e in prompts))
    check("tool calls are captured", any(e["kind"] == "tool" and e["name"] == "Edit" for e in log))
    check("the edited path is captured", any("app/waitlist.py" in (e.get("paths") or []) for e in log))
    check("a hook error log was not needed", not (home / "telemetry" / "hook-errors.log").exists())

    # --- the command wrapper ---------------------------------------------
    print("\nRecording what the participant types")
    shim_dir = home / "bin" / "shims"
    shim_dir.mkdir(parents=True, exist_ok=True)
    for cmd in ("git", "echo"):
        wrapper = shim_dir / cmd
        wrapper.write_text(
            "#!/bin/sh\n"
            f'STUDY_HOME="{home}"\nSTUDY_SHIM_DIR="{shim_dir}"\nexport STUDY_HOME STUDY_SHIM_DIR\n'
            f'exec "{py}" "{home}/telemetry/shim.py" {cmd} "$@"\n'
        )
        wrapper.chmod(0o755)

    shim_env = dict(env)
    shim_env["PATH"] = f"{shim_dir}{os.pathsep}{env.get('PATH', '')}"
    shim_env["STUDY_SHIM_DIR"] = str(shim_dir)

    proc = subprocess.run(["echo", "hello from the shim"], capture_output=True, text=True, env=shim_env, timeout=30)
    check("the wrapper passes output through unchanged", proc.stdout.strip() == "hello from the shim", proc.stdout)
    check("the wrapper passes the exit code through", proc.returncode == 0)

    proc = subprocess.run(["git", "rev-parse", "--is-inside-work-tree"], capture_output=True, text=True, env=shim_env, cwd=str(home), timeout=30)
    check("a failing command still exits non-zero", proc.returncode != 0)

    log = [json.loads(x) for x in (home / "telemetry" / "events.jsonl").read_text().splitlines() if x.strip()]
    commands = [e for e in log if e["kind"] == "command"]
    check("commands are recorded with their exit code", any(e["name"] == "echo" and e["exitCode"] == 0 for e in commands))
    check("a failure is recorded as a failure", any(e["name"] == "git" and e["ok"] is False for e in commands))
    check("commands the participant typed are not marked as the assistant's", all(e.get("agent") in (False, None) for e in commands))

    # A wrapper that could find itself would fork until the machine gave up.
    proc = subprocess.run(["git", "--version"], capture_output=True, text=True, env=shim_env, timeout=30)
    check("the wrapper never calls itself", proc.returncode in (0, 1, 127) and "shim.py" not in proc.stdout)

    # --- the instrument must not disturb what it measures ------------------
    print("\nRecording without changing the repository")
    work = home / "work"
    subprocess.run(["git", "init", "-q", "."], cwd=work, timeout=60)
    subprocess.run(["git", "config", "user.email", "t@example.org"], cwd=work, timeout=30)
    subprocess.run(["git", "config", "user.name", "T"], cwd=work, timeout=30)
    (work / "app.py").write_text("x = 1\n")
    subprocess.run(["git", "add", "-A"], cwd=work, timeout=60)
    subprocess.run(["git", "commit", "-qm", "first"], cwd=work, timeout=60)
    # The tool's own runtime directory, untracked, exactly as sgt leaves it.
    (work / ".sgt").mkdir(exist_ok=True)
    (work / ".sgt" / "oracle.json").write_text("{}")
    (work / "untracked.py").write_text("y = 2\n")

    before = subprocess.run(
        ["git", "ls-files", "-s"], cwd=work, capture_output=True, text=True, timeout=30
    ).stdout

    for _ in range(3):
        subprocess.run(["git", "status"], capture_output=True, env=shim_env, cwd=str(work), timeout=60)

    after = subprocess.run(
        ["git", "ls-files", "-s"], cwd=work, capture_output=True, text=True, timeout=30
    ).stdout
    check("recording a command leaves the git index untouched", before == after, f"{before!r} -> {after!r}")

    empty_blob = "e69de29bb2d1d6434b8b29ae775ad8c2e48c5391"
    check("no phantom empty-blob entries are staged", empty_blob not in after, after)

    # The failure this is really guarding: sgt snapshots via write-tree, and a
    # poisoned index disables save and revert for the rest of the session.
    wt = subprocess.run(
        ["git", "write-tree"], cwd=work, capture_output=True, text=True, timeout=30
    )
    check(
        "the tool under study can still snapshot the repository",
        wt.returncode == 0,
        (wt.stdout + wt.stderr).strip(),
    )

    tracked = subprocess.run(
        ["git", "ls-files"], cwd=work, capture_output=True, text=True, timeout=30
    ).stdout
    check("the tool's own runtime directory is never staged", ".sgt/" not in tracked, tracked)

    # --- uploading --------------------------------------------------------
    print("\nDelivering it")
    import client  # noqa: E402  (imported here so STUDY_HOME is already set)

    os.environ["STUDY_HOME"] = str(home)
    os.environ["FIRESTORE_EMULATOR_HOST"] = EMULATOR
    client.__dict__["study_home"] = lambda: home

    on_disk = [
        json.loads(x)
        for x in (home / "telemetry" / "events.jsonl").read_text().splitlines()
        if x.strip()
    ]
    sent, skipped, pending = client.sync(CODE)
    check("everything on disk is delivered", sent == pending and pending > 0, f"sent {sent} of {pending}")

    remote = landed_events()
    check(
        "what landed matches what was recorded",
        len(remote) == len(on_disk),
        f"{len(remote)} remote vs {len(on_disk)} local",
    )

    # Re-running the sync is the common case: the daemon, the Stop hook and the
    # participant can all trigger it within a second of each other.
    sent2, skipped2, pending2 = client.sync(CODE)
    check("re-running the sync sends nothing new", pending2 == 0)
    check("and does not duplicate anything", len(landed_events()) == len(remote))

    # Losing the ledger is what happens when a folder is copied or restored.
    (home / "telemetry" / "uploaded.txt").unlink()
    sent3, skipped3, pending3 = client.sync(CODE)
    check("a lost ledger still cannot double-count", len(landed_events()) == len(remote), f"{len(landed_events())} events")
    check("and the server is what rejected the duplicates", skipped3 == pending3, f"skipped {skipped3} of {pending3}")

    client.heartbeat(CODE, checks={"uv": {"ok": True, "detail": "found"}}, uploaded=len(remote))
    device = landed_device()
    check("the machine reports itself for the setup page", device is not None)
    if device:
        fields = device.get("fields", {})
        check("the setup checks reach the page", "checks" in fields)
        check("the half is reported", fields.get("half", {}).get("integerValue") == "1")

    shutil.rmtree(home, ignore_errors=True)

    print(f"\n{passed} passed, {failed} failed\n")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
