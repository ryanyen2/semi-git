#!/usr/bin/env bash
# Set up one participant's workspace. Run this before the participant sits down.
#
#   scripts/setup-study-session.sh <participant> <git|sgt> <coursecraft|confplan>
#
# It makes a fresh copy of the study project, builds its test environment, and
# checks that the tests pass. For the sgt condition it also installs sgt from a
# pinned source checkout rather than from PyPI, and refreshes the history view.
#
# Why a source install. The published 0.1.0 has a bug that corrupts a file during
# request 2, so a participant on that version loses the task. Pointing at a
# checkout also means every participant runs the same build, which we record.
set -euo pipefail

SGT_SOURCE="${SGT_SOURCE:-$HOME/repos/semi-git}"
STUDY_REPOS="${STUDY_REPOS:-$HOME/repos/sgt-study}"
MATERIALS="$(cd "$(dirname "${BASH_SOURCE[0]}")/../docs/study" && pwd)/materials"

if [ $# -ne 3 ]; then
    echo "usage: $0 <participant> <git|sgt> <coursecraft|confplan>" >&2
    exit 2
fi
participant="$1"; condition="$2"; project="$3"

case "$condition" in git|sgt) ;; *) echo "condition must be git or sgt" >&2; exit 2 ;; esac
case "$project" in coursecraft|confplan) ;; *) echo "project must be coursecraft or confplan" >&2; exit 2 ;; esac

workspace="$HOME/study/$participant"
if [ -e "$workspace" ]; then
    echo "$workspace already exists. Move it aside first, so nothing is reused." >&2
    exit 1
fi

source_repo="$STUDY_REPOS/$project"
[ "$condition" = git ] && source_repo="$STUDY_REPOS/baseline-$project"
if [ ! -d "$source_repo" ]; then
    echo "no study project at $source_repo" >&2
    exit 1
fi

echo "Setting up $participant: $condition condition, $project."
mkdir -p "$workspace/notes" "$workspace/bin"
cp -R "$source_repo" "$workspace/work"

echo "Building the test environment."
(
    cd "$workspace/work"
    rm -rf .venv
    uv venv -q
    uv pip install -q -p .venv/bin/python pytest
)

if [ "$condition" = sgt ]; then
    echo "Installing sgt from $SGT_SOURCE."
    uv venv -q "$workspace/toolenv"
    uv pip install -q -p "$workspace/toolenv/bin/python" "$SGT_SOURCE"
    # A wrapper on the participant's own PATH, so a stray sgt elsewhere on the
    # machine can't be picked up by accident.
    cat > "$workspace/bin/sgt" <<WRAPPER
#!/usr/bin/env bash
exec "$workspace/toolenv/bin/sgt" "\$@"
WRAPPER
    chmod +x "$workspace/bin/sgt"

    echo "Refreshing the history view. This takes about 30 seconds."
    # A fresh copy shows a provisional set of features that collapses to a
    # different, smaller set the first time anything refreshes, and two features
    # get renamed in place. Doing it now means the participant sees one stable
    # set for the whole session.
    (cd "$workspace/work" && "$workspace/bin/sgt" log --refresh >/dev/null 2>&1)

    build="$(cd "$SGT_SOURCE" && git rev-parse --short HEAD)"
    echo "$build" > "$workspace/notes/sgt-build.txt"
    echo "sgt build recorded: $build"
fi

cp "$MATERIALS/00-welcome.md" "$workspace/"
cp "$MATERIALS/02-tutorial-$condition.md" "$workspace/tutorial.md"
cp "$MATERIALS/03-tasks-$project.md" "$workspace/tasks.md"

echo "Checking the tests pass before the participant starts."
tests="$(cd "$workspace/work" && .venv/bin/python -m pytest -q 2>&1 | tail -1)"
echo "  $tests"
case "$tests" in
    *"38 passed"*) ;;
    *) echo "Expected 38 passing tests. Do not run the session until this is fixed." >&2; exit 1 ;;
esac

echo
echo "Ready. Workspace: $workspace"
echo "Hand the participant $workspace/00-welcome.md, then tutorial.md."
echo "Give them tasks.md only after the tutorial."
