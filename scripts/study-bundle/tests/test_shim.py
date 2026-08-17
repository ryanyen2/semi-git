#!/usr/bin/env python3
"""What the command wrapper writes, and what it refuses to write.

    python3 scripts/study-bundle/tests/test_shim.py

No emulator and no network: this is about the local log, which is the record of
truth. Its sibling, test_telemetry.py, covers the upload.

The property being protected is that the log describes the participant. The
first pilot's log did not: 450 of its 476 command events were the study's own
sync daemon running git every twenty seconds through the same PATH the
participant used, and nothing in the record said so. A session cannot be re-run,
so an instrument that records itself is not a bug you fix afterwards.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
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


def read_log(home: Path) -> list[dict]:
    path = home / "telemetry" / "events.jsonl"
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text().splitlines() if x.strip()]


def main() -> int:
    home = Path(tempfile.mkdtemp(prefix="study-shim-test-"))
    shutil.copytree(BUNDLE / "telemetry", home / "telemetry")
    (home / "bin").mkdir()
    (home / "work").mkdir()
    (home / "study.json").write_text(
        json.dumps({"condition": "git", "project": "coursecraft", "bundleVersion": "test"})
    )

    work = home / "work"
    git = shutil.which("git")
    for args in (
        ["init", "-q", "."],
        ["config", "user.email", "t@example.org"],
        ["config", "user.name", "T"],
    ):
        subprocess.run([git, *args], cwd=work, timeout=60)
    (work / "app.py").write_text("x = 1\n")
    subprocess.run([git, "add", "-A"], cwd=work, timeout=60)
    subprocess.run([git, "commit", "-qm", "first"], cwd=work, timeout=60)

    # A stand-in for sgt: a recorded tool that shells out to git, which is what
    # sgt does on nearly every command.
    (home / "bin" / "sgt").write_text(
        f'#!/bin/sh\n"{git}" log --oneline -n 1 >/dev/null 2>&1\n'
        f'"{git}" diff HEAD >/dev/null 2>&1\necho "sgt ran"\n'
    )
    (home / "bin" / "sgt").chmod(0o755)

    shim_dir = home / "bin" / "shims"
    shim_dir.mkdir(parents=True)
    real_for = {"git": git, "sgt": str(home / "bin" / "sgt")}
    for cmd in ("git", "sgt"):
        wrapper = shim_dir / cmd
        wrapper.write_text(
            "#!/bin/sh\n"
            f'STUDY_HOME="{home}"\nSTUDY_SHIM_DIR="{shim_dir}"\n'
            "export STUDY_HOME STUDY_SHIM_DIR\n"
            'if [ -n "$STUDY_PARENT_TOOL" ] || [ -n "$STUDY_NO_LOG" ]; then\n'
            f'    [ -x "{real_for[cmd]}" ] && exec "{real_for[cmd]}" "$@"\n'
            "fi\n"
            f'exec "{sys.executable}" "{home}/telemetry/shim.py" {cmd} "$@"\n'
        )
        wrapper.chmod(0o755)

    base = dict(os.environ)
    base["STUDY_HOME"] = str(home)
    base["STUDY_SHIM_DIR"] = str(shim_dir)
    base["PATH"] = f"{shim_dir}{os.pathsep}{base.get('PATH', '')}"
    base.pop("CLAUDECODE", None)
    base.pop("STUDY_SURFACE", None)

    def run(args: list[str], **overrides: str) -> None:
        env = dict(base)
        env.update(overrides)
        subprocess.run(args, cwd=str(work), env=env, capture_output=True, timeout=60)

    # --- where a command came from ----------------------------------------
    print("\nWhere a command came from")
    run(["git", "log", "--oneline"])
    run(["git", "show", "HEAD"], STUDY_SURFACE="editor")
    run(["git", "status"], STUDY_SURFACE="editor")
    run(["git", "diff"], CLAUDECODE="1")

    log = read_log(home)
    commands = [e for e in log if e["kind"] == "command"]
    by_text = {e["text"]: e for e in commands}

    check(
        "a command typed in the shell is a terminal command",
        by_text.get("git log --oneline", {}).get("surface") == "terminal",
        json.dumps(by_text.get("git log --oneline")),
    )
    check(
        "a command the editor ran is an editor command",
        by_text.get("git show HEAD", {}).get("surface") == "editor",
        json.dumps(by_text.get("git show HEAD")),
    )
    check(
        "a command the assistant ran is still marked as the assistant's",
        by_text.get("git diff", {}).get("surface") == "agent"
        and by_text.get("git diff", {}).get("agent") is True,
        json.dumps(by_text.get("git diff")),
    )
    check(
        "the editor's own polling is flagged, not silently mixed in",
        by_text.get("git status", {}).get("auto") is True,
        json.dumps(by_text.get("git status")),
    )
    check(
        "reading history in the editor is not flagged as polling",
        by_text.get("git show HEAD", {}).get("auto") is None,
        json.dumps(by_text.get("git show HEAD")),
    )
    check(
        "the parent process is recorded",
        all(e.get("parent") for e in commands),
        json.dumps([e.get("parent") for e in commands]),
    )

    # --- the instrument does not record itself -----------------------------
    print("\nThe instrument does not record itself")
    before = len(read_log(home))
    run(["git", "status", "--porcelain"], STUDY_NO_LOG="1")
    check("a command marked as the study's own writes nothing", len(read_log(home)) == before)

    before = len(read_log(home))
    subprocess.run(
        [sys.executable, str(home / "telemetry" / "sync.py"), "--quiet"],
        cwd=str(home),
        env=base,
        capture_output=True,
        timeout=120,
    )
    after = [e for e in read_log(home)[before:] if e["kind"] == "command"]
    check(
        "a sync records no commands of its own",
        after == [],
        f"{len(after)} command events: {[e.get('text') for e in after][:6]}",
    )

    # The setup check asks the assistant one question. It reaches the same
    # hooks a participant's prompts do, and it is not a prompt anybody wrote.
    before = len(read_log(home))
    hook_env = dict(base)
    hook_env["STUDY_NO_LOG"] = "1"
    subprocess.run(
        [sys.executable, str(home / "telemetry" / "hook.py"), "UserPromptSubmit"],
        input=json.dumps({"session_id": "doctor", "prompt": "Reply with exactly: ok"}),
        text=True,
        env=hook_env,
        capture_output=True,
        timeout=30,
    )
    check(
        "the setup check's own question is not recorded as a prompt",
        len(read_log(home)) == before,
        json.dumps(read_log(home)[before:]),
    )

    # --- one move is one event --------------------------------------------
    print("\nOne move is one event")
    before = len(read_log(home))
    run(["sgt", "log"], STUDY_SURFACE="editor")
    fresh = [e for e in read_log(home)[before:] if e["kind"] == "command"]
    check(
        "a tool that shells out to git is recorded once, as itself",
        [e["name"] for e in fresh] == ["sgt"],
        f"recorded: {[(e['name'], e.get('text')) for e in fresh]}",
    )

    # --- the editor's own housekeeping -------------------------------------
    print("\nThe editor's own housekeeping")
    before = len(read_log(home))
    run(["git", "status"], STUDY_SURFACE="editor")
    marked = [e for e in read_log(home)[before:] if e["kind"] == "command"]
    check(
        "an editor refreshing its own views is flagged",
        all(e.get("auto") is True for e in marked),
        json.dumps(marked),
    )

    # --- snapshots ---------------------------------------------------------
    print("\nSnapshots follow changes, not reads")
    snaps = lambda: len([e for e in read_log(home) if e["kind"] == "repo" and e["name"] == "tree"])
    (work / "app.py").write_text("x = 2\n")
    before = snaps()
    run(["git", "add", "-A"])
    check("a command that can move the tree is followed by a snapshot", snaps() > before)

    before = snaps()
    for _ in range(6):
        run(["git", "log", "--oneline"], STUDY_SURFACE="editor")
        run(["git", "status"], STUDY_SURFACE="editor")
    check(
        "a burst of reads does not produce a snapshot each",
        snaps() - before <= 1,
        f"{snaps() - before} snapshots for 12 reads",
    )

    print(f"\n{passed} passed, {failed} failed\n")
    if failed == 0:
        shutil.rmtree(home, ignore_errors=True)
    else:
        print(f"Left the bundle in {home} for inspection.\n")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
