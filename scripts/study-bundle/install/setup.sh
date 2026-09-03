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

    # How the tool learns what the assistant was asked to do. Written here, on
    # this machine, against this folder's own copy: the same file built into the
    # bundle would carry an absolute path from the machine that built it, and
    # would fail on every prompt without saying so.
    # What makes the tool usable by the assistant rather than only by hand: the
    # MCP server, the skills that say which verb answers which question, and the
    # pre-approval that stops Claude Code asking about the server mid-session.
    # Without these the assistant can only guess at `sgt` through a shell, which
    # is not the tool this condition is supposed to be testing.
    #
    # Two different paths on purpose. The MCP server runs the real binary: it is
    # one long-lived process, and recording it would say nothing. The editor is
    # pointed at the wrapper, because a workspace setting outranks the profile
    # one, and the stock value would route the editor's own calls around the
    # recording entirely.
    "$here/toolenv/bin/python" - <<PYEOF || note "Could not wire up the assistant. Tell your facilitator."
from pathlib import Path
from sgt.agent_assets.install import (
    install_mcp_approval, install_mcp_json, install_skills, install_vscode_settings,
)
repo = Path("$here/work")
install_mcp_json(repo, "$here/bin/sgt")
install_mcp_approval(repo)
count = install_skills(repo)
install_vscode_settings(repo, "$here/bin/shims/sgt")
print(f"  assistant wired up: mcp server, {count} skill(s)")
PYEOF

    mkdir -p "$here/work/.claude"
    cat > "$here/work/.claude/settings.local.json" <<HOOKS
{
  "hooks": {
    "UserPromptSubmit": [
      { "hooks": [ { "type": "command", "command": "\"$here/bin/sgt\" intent record" } ] }
    ],
    "PostToolUse": [
      {
        "matcher": "Edit|Write|MultiEdit",
        "hooks": [ { "type": "command", "command": "\"$here/bin/sgt\" intent activity" } ]
      }
    ]
  }
}
HOOKS
    note "Done."
else
    # The half without a history tool. Its assistant still gets guidance, or the
    # agent-half comparison is a guided tool against an unguided one. One skill,
    # teaching git at the same depth the sgt half's skills teach sgt. Auto-found
    # by Claude Code the same way, so the wiring matches; there is no MCP server
    # and no intent hook here, because git ships neither and papering over that
    # would be measuring something the git arm does not have.
    if [ -d "$here/install/git-skills" ]; then
        say "Wiring up the AI assistant"
        mkdir -p "$here/work/.claude/skills"
        cp -R "$here/install/git-skills/." "$here/work/.claude/skills/"
        note "assistant wired up: $(find "$here/install/git-skills" -name SKILL.md | wc -l | tr -d ' ') skill(s)"
    fi
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

# ---------------------------------------------------------------------------
# The editor, in its own profile
# ---------------------------------------------------------------------------

say "Setting up the editor"

# Not `CODE`. That is the participant's code, read from the command line at the
# top of this file, and overwriting it here sent a path to the study as if it
# were a person.
#
# One line either way: a path when it is Visual Studio Code, and otherwise the
# sentence to read out. See bin/study-find-editor for why a `code` on the PATH
# does not settle it.
if EDITOR_CLI="$("$here/bin/study-find-editor")"; then
    editor_problem=""
else
    editor_problem="$EDITOR_CLI"
    EDITOR_CLI=""
fi
if [ -z "$EDITOR_CLI" ]; then
    note "$editor_problem"
    note "Tell your facilitator before your session."
    note "Everything can be done from the shell, but half of what we are studying is the editor."
else
    note "$("$EDITOR_CLI" --version 2>/dev/null | head -1 || echo found)"
    profile="$here/.vscode-study"

    # A profile another editor has already written to is thrown away rather
    # than added to. The first run of X08 was Cursor, which puts its own Pyright
    # in here on sight of a .py file; installing over the top would have left it
    # sitting next to Pylance, and the check reports anything extra for the same
    # reason it reports anything missing.
    stamp="$profile/.built-by"
    if [ -d "$profile" ] && [ "$(cat "$stamp" 2>/dev/null || echo)" != "$EDITOR_CLI" ]; then
        note "Starting the study profile again from empty."
        rm -rf "$profile"
    fi
    mkdir -p "$profile/extensions"
    printf '%s\n' "$EDITOR_CLI" > "$stamp"

    # Failures land in a log the facilitator can actually read. The install used to discard
    # stderr entirely, so "Could not install ms-python.python" gave a name and no reason --
    # unanswerable over a participant's shoulder. (On a healthy marketplace these pins install;
    # the observed failures were transient downloads, which is also why install_ext retries once.)
    ext_log="$here/install-extensions.log"
    : > "$ext_log"
    install_ext() {
        "$EDITOR_CLI" --user-data-dir "$profile" --extensions-dir "$profile/extensions" \
            --install-extension "$1" --force >>"$ext_log" 2>&1 \
        || "$EDITOR_CLI" --user-data-dir "$profile" --extensions-dir "$profile/extensions" \
            --install-extension "$1" --force >>"$ext_log" 2>&1
    }

    # Into this folder only. The participant's own editor, their own extensions
    # and their own settings are never read and never changed, and deleting
    # this folder at the end takes the whole profile with it.
    for vsix in "$here"/install/*.vsix; do
        [ -e "$vsix" ] || continue
        note "Installing $(basename "$vsix")"
        install_ext "$vsix" || note "Could not install $(basename "$vsix"). Tell your facilitator."
    done

    # The same Python tooling in both conditions, at the versions the study
    # pinned. Without this, whether someone got Pylance depended on whether
    # their half happened to lead them to open a .py file. Downloaded rather
    # than shipped: together they are about 200 MB.
    exts="$("$PY" - <<'PYEOF' 2>/dev/null || true
import json, pathlib
try:
    print(" ".join(json.loads(pathlib.Path("study.json").read_text()).get("editorExtensions") or []))
except Exception:
    print("")
PYEOF
)"
    if [ -n "$exts" ]; then
        note "Installing the Python tooling. A couple of minutes; it is about 200 MB."
        # One invocation for all four, not four invocations. Each one starts a
        # whole editor process before it downloads anything, and the four of
        # them are the only part of this step we control -- the 200 MB is
        # required, since both projects are Python and both halves need the
        # same tooling.
        #
        # A batched call reports failure without saying which extension failed,
        # so the per-extension loop stays as the fallback. The participant gets
        # the fast path when it works and a name they can read out when it
        # does not.
        batched=""
        for ext in $exts; do
            batched="$batched --install-extension $ext"
        done
        # shellcheck disable=SC2086
        if "$EDITOR_CLI" --user-data-dir "$profile" --extensions-dir "$profile/extensions" \
            $batched --force >>"$ext_log" 2>&1; then
            note "Done."
        else
            note "Retrying one at a time."
            for ext in $exts; do
                install_ext "$ext" || true
            done
            note "Done."
        fi
        # What matters is what is INSTALLED, not which install command exited zero: the
        # marketplace resolves dependencies, so ms-python.python arriving as pylance's dependency
        # is fine even when its own install call failed. Verify by listing, and only name what is
        # really missing.
        installed="$("$EDITOR_CLI" --user-data-dir "$profile" --extensions-dir "$profile/extensions" \
            --list-extensions 2>/dev/null || true)"
        missing=""
        for ext in $exts; do
            id="${ext%@*}"
            printf '%s\n' "$installed" | grep -qix "$id" || missing="$missing $id"
        done
        if [ -n "$missing" ]; then
            note "Could not install:$missing"
            note "Tell your facilitator — the reason is in $ext_log"
        fi
    fi
fi

# The study's own plumbing must not look like the participant's work. None of
# these files are in the project's .gitignore, so without this the first thing
# someone sees in `git status` -- or in the editor's Source Control view -- is
# six untracked files they did not create, in one condition and not the other.
# `.git/info/exclude` is the local, untracked way to say so.
if [ -d "$here/work/.git" ]; then
    # `.DS_Store` too: Finder drops one in every folder it opens, and the tool's
    # own revert commit swept three of them in as "added" files on a pilot's Mac.
    printf '%s\n' "" "# added by the study setup" ".claude/" ".mcp.json" ".vscode/" ".DS_Store" \
        >> "$here/work/.git/info/exclude"

    # git's pager and terminal editor trapped pilots under a running clock:
    # `git log` opened less and got force-killed, and `git revert HEAD` opened
    # pico over the suggested message. Neither is what the study measures, so
    # in this repo git prints straight to the terminal and commit messages are
    # taken as git suggests them (see bin/study-git-editor). Repo-local config
    # rather than the session shell's environment, because pilots also typed
    # git into the editor's terminal and their own shell, which that
    # environment never reaches.
    #
    # The pager is `sed`, not `cat`, and the one line it drops is the reason.
    # `sgt save` records which ops a commit embodies as `Sgt-Op:` trailers in the
    # commit message, and they are load-bearing -- resync and sync read them back
    # as the tree-witnessed record of what the tip contains -- so they cannot be
    # stripped from the history. What they also do is bury the author's words:
    # footfall's newest commit is a six-line message followed by 125 lines of
    # hex, and the whole sgt-arm history carries 2,050 such lines. The git arm's
    # repositories are rendered without them, so leaving them in place makes
    # plain `git log` harder to read in the sgt arm than in the git arm -- a bias
    # in sgt's own favour, on the one surface both arms share.
    #
    # Set in BOTH arms, identically, so it is not a condition difference: the git
    # arm has no such lines, so the same filter is a no-op there. It drops only
    # a whole line that is nothing but `Sgt-Op:` and a long hex id, so a diff
    # line (`+Sgt-Op: …`) survives and nothing an author wrote is touched. The
    # message body is indented four spaces by `git log`, hence the leading
    # `[[:space:]]*`. `sed` rather than `grep -v` because grep exits non-zero
    # when it matches nothing and git reports that as the pager failing.
    # Measured on the shipped footfall bundle: `git log -1` on its newest commit
    # goes from 134 lines to 12, and the six-line message survives whole.
    #
    # This covers the terminal only -- git does not page when its output is
    # piped, and the editor's own git views render the message themselves.
    # `protocol-v2.md` section 11 discloses what is left.
    git -C "$here/work" config core.pager \
        "sed -E '/^[[:space:]]*Sgt-Op: [0-9a-f]{16,}$/d'"
    git -C "$here/work" config core.editor "$here/bin/study-git-editor"
fi

say "Setting up session recording"
mkdir -p "$here/bin/shims"
for cmd in git sgt pytest python python3; do
    # Where the real one lives, worked out now rather than at every call. The
    # wrapper needs it to be able to step aside without starting Python, which
    # is what makes a nested call free.
    case "$cmd" in
        sgt)     real="$here/bin/sgt" ;;
        python)  real="$PY" ;;
        python3) real="$PY" ;;
        pytest)  real="$here/work/.venv/bin/pytest" ;;
        *)       real="$(command -v "$cmd" 2>/dev/null || true)" ;;
    esac

    cat > "$here/bin/shims/$cmd" <<SHIM
#!/bin/sh
# Records the command, then runs the real one. It changes nothing about what
# the command does, and it only exists inside the session shell.
STUDY_HOME="$here"
STUDY_SHIM_DIR="$here/bin/shims"
export STUDY_HOME STUDY_SHIM_DIR

# A command run by another recorded command is not a move somebody made. \`sgt\`
# shells out to git constantly: in a two-minute editor session it produced 136
# of the 167 git calls in the log, which would have read as "they mostly used
# git" in the condition where they mostly used sgt. Stepping aside here rather
# than in the recorder also keeps it free -- otherwise every one of those calls
# pays for a Python start, and the tool that spawns subprocesses is the only one
# that gets slower for being measured.
if [ -n "\$STUDY_PARENT_TOOL" ] || [ -n "\$STUDY_NO_LOG" ]; then
    [ -x "$real" ] && exec "$real" "\$@"
fi

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
note "This runs the project's own check and asks the assistant one question. A minute or two."
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
