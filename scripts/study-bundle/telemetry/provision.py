#!/usr/bin/env python3
"""Configure this bundle for one participant, from their code alone.

Two jobs.

First, credentials. The keys are fetched here rather than pasted by the
participant, because a key that has to be copied by hand is a key that ends up
in the wrong window, and because the assistant then has to be told to trust it.
They land in a profile inside this folder, so the participant's own assistant
account and billing are never involved.

Second, and more important, it refuses to configure a bundle that does not match
what the participant is supposed to be doing. Handing someone the wrong bundle
produces a session that looks perfectly normal and is worthless, which is the
kind of mistake that is only ever found during analysis.

  usage: provision.py <CODE>
"""

from __future__ import annotations

import json
import stat
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import client  # noqa: E402

HOOK_EVENTS = [
    "SessionStart",
    "UserPromptSubmit",
    "PreToolUse",
    "PostToolUse",
    "PostToolUseFailure",
    "Stop",
    "SessionEnd",
]

# Events with no matcher support must not carry one, and tool events want every
# tool, so the matcher is only set where it means something.
TOOL_EVENTS = {"PreToolUse", "PostToolUse", "PostToolUseFailure"}


# What the assistant runs as, when the study has not said otherwise. The model
# is part of the condition: two participants on different models are not two
# runs of the same study, and the difference would be invisible afterwards.
DEFAULT_MODEL = "claude-sonnet-5"

# What the history tool names features with. Cheap on purpose: it fires once per
# feature on every refresh, and the study rebuilds the graph for every bundle.
SGT_MODEL = "gpt-5.6-luna"


def build_settings(study_home: Path, python: str, model: str = DEFAULT_MODEL) -> dict:
    hook_script = str(study_home / "telemetry" / "hook.py")
    hooks: dict[str, list] = {}
    for event in HOOK_EVENTS:
        entry: dict = {
            "hooks": [
                {
                    "type": "command",
                    "command": python,
                    "args": [hook_script, event],
                    # Nothing here may make the assistant feel slow, and a hook
                    # that hangs must not be able to hold up a session.
                    "async": True,
                    "timeout": 20,
                }
            ]
        }
        if event in TOOL_EVENTS:
            entry["matcher"] = "*"
        hooks[event] = [entry]

    return {
        "apiKeyHelper": str(study_home / ".claude-study" / "api-key.sh"),
        "hooks": hooks,
        "theme": "light",
        # Pinned in two places on purpose. `model` is what the assistant starts
        # on; `ANTHROPIC_MODEL` is what it falls back to if a future version
        # reads the setting differently. Neither can be changed from inside the
        # session without it showing up in the settings file we ship.
        "model": model,
        "env": {
            # The version is part of the condition. An assistant that upgraded
            # itself between participant three and participant four would be a
            # confound nobody could reconstruct afterwards.
            "DISABLE_AUTOUPDATER": "1",
            "ANTHROPIC_MODEL": model,
        },
    }


def fail(message: str) -> None:
    print()
    print("  " + message)
    print()
    sys.exit(1)


def main() -> int:
    if len(sys.argv) < 2:
        fail("usage: provision.py <CODE>")
    code = sys.argv[1].strip()

    study_home = client.study_home()
    meta = client.study_meta()
    expected_condition = meta.get("condition")
    expected_project = meta.get("project")

    try:
        participant = client.fetch_document(f"participants/{code}")
    except client.UploadError as exc:
        fail(f"Could not reach the study to check your code: {exc}")
        return 1

    if not participant:
        fail(
            "That code does not match a participant. Check you copied the whole thing from your"
            " study page, then run this again."
        )
        return 1

    blocks = participant.get("blocks") or []
    assigned = next(
        (
            b
            for b in blocks
            if b.get("condition") == expected_condition and b.get("project") == expected_project
        ),
        None,
    )
    if assigned is None:
        have = ", ".join(f"{b.get('condition')}/{b.get('project')}" for b in blocks) or "nothing"
        fail(
            "This folder is not one of the two you are assigned.\n\n"
            f"  the folder holds: {expected_condition} / {expected_project}\n"
            f"  you are assigned: {have}\n\n"
            "  Stop and tell your facilitator. Working from the wrong folder produces a session"
            " that looks perfectly normal and is unusable, and it is a two-minute fix now."
        )
        return 1

    expected_half = assigned.get("half")
    client.write_state({"half": expected_half})

    secrets = client.fetch_document(f"participants/{code}/secrets/session") or {}
    anthropic_key = str(secrets.get("anthropicApiKey") or "").strip()
    openai_key = str(secrets.get("openaiApiKey") or "").strip()

    # --- the assistant profile, inside this folder --------------------------
    profile = study_home / ".claude-study"
    profile.mkdir(parents=True, exist_ok=True)

    key_file = profile / "api-key"
    key_file.write_text(anthropic_key + "\n")
    key_file.chmod(stat.S_IRUSR | stat.S_IWUSR)

    helper = profile / "api-key.sh"
    helper.write_text(f'#!/bin/sh\ncat "{key_file}"\n')
    helper.chmod(0o700)

    python = str(study_home / "work" / ".venv" / "bin" / "python")
    model = str(secrets.get("claudeModel") or "").strip() or DEFAULT_MODEL
    (profile / "settings.json").write_text(
        json.dumps(build_settings(study_home, python, model), indent=2) + "\n"
    )

    # Skip the first-run walkthrough. The participant has ten minutes of
    # practice ahead of them and it should be spent on the study, not on
    # choosing a colour scheme.
    claude_json = profile / ".claude.json"
    existing = {}
    if claude_json.exists():
        try:
            existing = json.loads(claude_json.read_text())
        except json.JSONDecodeError:
            existing = {}
    existing.update({"hasCompletedOnboarding": True, "theme": "light"})
    claude_json.write_text(json.dumps(existing, indent=2) + "\n")

    # --- the key sgt needs --------------------------------------------------
    #
    # The model is pinned here for the same reason the assistant's is: it names
    # every feature the participant reads, and two halves labelled by two
    # different models are not two runs of one study. Written next to the key so
    # the tool cannot pick up whatever a machine happens to default to.
    if expected_condition == "sgt" and openai_key:
        env_file = study_home / "work" / ".env"
        env_file.write_text(
            f"OPENAI_API_KEY={openai_key}\nSGT_MODEL={SGT_MODEL}\n"
        )
        env_file.chmod(stat.S_IRUSR | stat.S_IWUSR)

    # --- what the shell and the shims need ---------------------------------
    (study_home / "study.env").write_text(
        "\n".join(
            [
                f'STUDY_HOME="{study_home}"',
                f'STUDY_CODE="{code}"',
                f'STUDY_PY="{python}"',
                f'STUDY_SHIM_DIR="{study_home}/bin/shims"',
                "export STUDY_HOME STUDY_CODE STUDY_PY STUDY_SHIM_DIR",
                "",
            ]
        )
    )
    client.write_state({"code": code})
    client.append(
        "session",
        name="provisioned",
        text=f"half {expected_half}, {expected_condition}, {expected_project}, {model}",
        model=model,
    )

    label = participant.get("label") or code[:6]
    print(f"  Set up for {label}, half {expected_half}.")
    if not anthropic_key:
        print("  No assistant key was issued yet. Tell your facilitator before you start.")
    if expected_condition == "sgt" and not openai_key:
        print("  No key for the history tool yet. Tell your facilitator before you start.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
