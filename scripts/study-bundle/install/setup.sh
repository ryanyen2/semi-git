#!/usr/bin/env bash
# One command, run once, from inside the folder you unpacked.
#
#   bash install/setup.sh <your code>
#
# It installs everything this session needs inside this folder, using its own
# Python. It does not touch your shell configuration, your global packages, or
# your own AI assistant account. Removing this folder at the end removes all of
# it.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$here"

CODE="${1:-}"

say()  { printf '\n\033[1m%s\033[0m\n' "$*"; }
note() { printf '  %s\n' "$*"; }
die()  { printf '\n\033[31m%s\033[0m\n\n' "$*" >&2; exit 1; }

# ---------------------------------------------------------------------------
# Before anything is installed, check it can work at all
# ---------------------------------------------------------------------------

say "Checking this machine can run the session"

case "$(uname -s 2>/dev/null || echo unknown)" in
    Darwin|Linux) ;;
    MINGW*|MSYS*|CYGWIN*)
        die "This needs macOS, Linux, or Windows Subsystem for Linux. Please tell your facilitator: running it in Git Bash on Windows will not work."
        ;;
    *) note "Unrecognised system. Carrying on, but tell your facilitator if anything below fails." ;;
esac

command -v git  >/dev/null 2>&1 || die "git is not installed. Install it, then run this again."
command -v curl >/dev/null 2>&1 || die "curl is not installed. Install it, then run this again."
[ -d "$here/work" ] || die "This does not look like the study folder: there is no work/ directory next to install/. Unpack the file again and run this from inside the folder it created."

if [ -z "$CODE" ]; then
    die "Please run it with the code from your study page:

    bash install/setup.sh YOURCODE

The code is on the setup step, in the command you can copy."
fi

note "$(uname -s), $(uname -m)"

# ---------------------------------------------------------------------------
# Python, ours not theirs
# ---------------------------------------------------------------------------

say "Setting up Python"
note "Your own Python version does not matter. This fetches its own and uses only that."

export PATH="$HOME/.local/bin:$PATH"
if ! command -v uv >/dev/null 2>&1; then
    note "Installing uv, which manages the Python for this session."
    curl -LsSf https://astral.sh/uv/install.sh | sh >/dev/null 2>&1 \
        || die "Could not install uv. Check your internet connection and try again."
    export PATH="$HOME/.local/bin:$PATH"
fi
command -v uv >/dev/null 2>&1 || die "uv installed but is not on PATH. Open a new terminal tab and run this again."

uv python install 3.12 >/dev/null 2>&1 || die "Could not fetch Python 3.12. Check your internet connection and try again."
note "Python 3.12 ready."

say "Building the project environment"
(
    cd "$here/work"
    rm -rf .venv
    uv venv -q --clear -p 3.12
    uv pip install -q -p .venv/bin/python pytest
) || die "Could not build the project environment. Send your facilitator everything printed above."
PY="$here/work/.venv/bin/python"
note "Done."

# ---------------------------------------------------------------------------
# The history tool, in the half that has one
# ---------------------------------------------------------------------------

wheel="$(ls "$here"/install/*.whl 2>/dev/null | head -1 || true)"
if [ -n "$wheel" ]; then
    say "Installing the history tool"
    uv venv -q --clear -p 3.12 "$here/toolenv"
    uv pip install -q -p "$here/toolenv/bin/python" "$wheel" \
        || die "Could not install the history tool. Send your facilitator everything printed above."
    mkdir -p "$here/bin"
    printf '#!/usr/bin/env bash\nexec "%s/toolenv/bin/sgt" "$@"\n' "$here" > "$here/bin/sgt"
    chmod +x "$here/bin/sgt"
    note "Done."
fi

# ---------------------------------------------------------------------------
# The assistant, on our key and our profile
# ---------------------------------------------------------------------------

say "Setting up the AI assistant"

CLAUDE_VERSION="$("$PY" - <<'PYEOF' 2>/dev/null || true
import json, pathlib
try:
    print(json.loads(pathlib.Path("study.json").read_text()).get("claudeVersion") or "")
except Exception:
    print("")
PYEOF
)"

if ! command -v claude >/dev/null 2>&1; then
    note "Installing it. This does not sign you in and does not use your account."
    if [ -n "$CLAUDE_VERSION" ]; then
        curl -fsSL https://claude.ai/install.sh | bash -s "$CLAUDE_VERSION" >/dev/null 2>&1 || true
    else
        curl -fsSL https://claude.ai/install.sh | bash >/dev/null 2>&1 || true
    fi
    export PATH="$HOME/.local/bin:$PATH"
fi

if command -v claude >/dev/null 2>&1; then
    note "$(claude --version 2>/dev/null || echo 'installed')"
else
    note "Could not install it automatically. Tell your facilitator; everything else below will still finish."
fi

# ---------------------------------------------------------------------------
# Recording wrappers
# ---------------------------------------------------------------------------

say "Setting up session recording"
mkdir -p "$here/bin/shims"
for cmd in git sgt pytest python python3; do
    cat > "$here/bin/shims/$cmd" <<SHIM
#!/bin/sh
# Records the command, then runs the real one. It changes nothing about what
# the command does, and it only exists inside the session shell.
STUDY_HOME="$here"
STUDY_SHIM_DIR="$here/bin/shims"
export STUDY_HOME STUDY_SHIM_DIR
exec "$PY" "$here/telemetry/shim.py" $cmd "\$@"
SHIM
    chmod +x "$here/bin/shims/$cmd"
done
chmod +x "$here"/bin/study-* 2>/dev/null || true
note "Done."

# ---------------------------------------------------------------------------
# Who you are, and the keys for the session
# ---------------------------------------------------------------------------

say "Getting this session's settings"
STUDY_HOME="$here" "$PY" "$here/telemetry/provision.py" "$CODE" \
    || die "Setup stopped. Nothing is broken; fix what it says above, or ask your facilitator, then run this again."

# ---------------------------------------------------------------------------
# Prove it works
# ---------------------------------------------------------------------------

say "Checking everything works"
note "This runs the project's tests and asks the assistant one question. A minute or two."
set +e
STUDY_HOME="$here" STUDY_CODE="$CODE" "$PY" "$here/telemetry/doctor.py" --code "$CODE"
status=$?
set -e

if [ "$status" -eq 0 ]; then
    say "Ready"
    note "Go back to your study page. The checklist there is already green."
    note "When it tells you to, start the session shell with:"
    printf '\n      ./bin/study-shell\n\n'
else
    say "Some checks did not pass"
    note "Your study page shows the same list. Show it to your facilitator before starting."
    note "Nothing is broken and nothing is lost; most of these take a minute to fix."
fi

exit "$status"
