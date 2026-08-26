#!/usr/bin/env python3
"""That the setup check runs the session the way a session runs it.

    python3 scripts/study-bundle/tests/test_doctor.py

No emulator, no network, no assistant: this reads the files that build the
session's environment and checks they agree, on the assistant's key and on which
editor gets opened.

The property being protected comes from a real setup failure. The session
launchers unset three variables before starting the assistant, and the doctor's
ping unset two of them. On a facilitator's machine the third, ANTHROPIC_BASE_URL,
pointed at a proxy, so the ping sent the study's own key somewhere that would not
take it, retried until it hit the 75-second timeout, and reported that the key was
probably wrong. The key was fine. Setup stops there, and the message sends whoever
is holding it to the one place there is nothing to find.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

BUNDLE = Path(__file__).resolve().parent.parent

passed = 0
failed = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"  ok    {label}")
    else:
        failed += 1
        print(f"  FAIL  {label}" + (f" -- {detail}" if detail else ""))


def main() -> int:
    print("\nthe doctor and the session, on the same environment\n")

    doctor = (BUNDLE / "telemetry" / "doctor.py").read_text()
    # Whichever way the names are written -- one pop each, or one loop over a
    # tuple -- what the test needs is the set of names, so it reads both forms
    # rather than pinning the shape of the code that removes them.
    loop = re.search(r"for leaked in \(([^)]*)\)", doctor)
    popped = set(re.findall(r'env\.pop\("(ANTHROPIC_[A-Z_]+)"', doctor))
    if loop:
        popped |= set(re.findall(r'"(ANTHROPIC_[A-Z_]+)"', loop.group(1)))

    for launcher in ("study-shell", "study-code"):
        text = (BUNDLE / "bin" / launcher).read_text()
        match = re.search(r"^unset (ANTHROPIC_[A-Z_ ]+)", text, re.M)
        check(f"{launcher} scrubs the assistant's environment", match is not None)
        if not match:
            continue
        unset = set(match.group(1).split())
        # A variable the session removes and the check keeps means the check is
        # not testing the session. Which direction the difference runs decides
        # what breaks: a variable kept here can redirect or re-key the ping, and
        # one kept there leaks the participant's own account into the session.
        check(
            f"the ping removes everything {launcher} removes",
            unset <= popped,
            f"{launcher} unsets {sorted(unset - popped)}, the ping keeps them",
        )
        check(
            f"the ping removes nothing {launcher} keeps",
            popped <= unset,
            f"the ping removes {sorted(popped - unset)}, {launcher} keeps them",
        )

    # The timeout is the only branch that reports a cause it has not established,
    # and the cause it used to name was the one thing that was working.
    timeout_msg = re.search(r'f"no answer in \{PING_TIMEOUT\}s[^"]*"', doctor)
    check("the timeout says what to check rather than what is wrong", timeout_msg is not None)
    if timeout_msg:
        check(
            "the timeout does not blame the key on its own",
            "means the key is wrong" not in timeout_msg.group(0),
            timeout_msg.group(0),
        )

    # Which editor. Three files used to answer this separately and all three
    # answered it with `command -v code`, which on a machine with Cursor
    # installed is Cursor -- an AI editor, in a task block the protocol gives no
    # assistant. Nothing said so: the run reported two missing Python extensions
    # and passed everything else.
    print()
    finder = BUNDLE / "bin" / "study-find-editor"
    check("there is one place that decides which editor", finder.is_file())
    for caller in ("install/setup.sh", "bin/study-code", "telemetry/doctor.py"):
        text = (BUNDLE / caller).read_text()
        check(f"{caller} asks it", "study-find-editor" in text)
        # A second opinion is the failure being guarded against, not a tidiness
        # point: the file that keeps its own copy is the one that drifts.
        own = [
            pattern
            for pattern in ('command -v code', 'which("code")', "Visual Studio Code.app")
            if pattern in text
        ]
        check(f"{caller} has no opinion of its own", not own, ", ".join(own))

    # Whatever it names has to be the editor it claims, on whichever machine
    # this runs. The one thing that must never happen is a path handed back for
    # a build that is not Visual Studio Code.
    proc = subprocess.run(["bash", str(finder)], capture_output=True, text=True, timeout=30)
    answer = (proc.stdout + proc.stderr).strip()
    if proc.returncode == 0:
        product = Path(answer).resolve().parent.parent / "product.json"
        check(
            "the editor it names is Visual Studio Code",
            product.is_file() and '"nameLong": "Visual Studio Code"' in product.read_text(),
            answer,
        )
    else:
        check("it says what to do instead", "code.visualstudio.com" in answer, answer)

    print(f"\n{passed} passed, {failed} failed\n")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
