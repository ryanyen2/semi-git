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

# The .env in the study project holds our API key. make-study-bundle.sh already
# strips it from remote bundles; the in-person path copied it straight into the
# participant's workspace, where `sgt` would also pick it up and quietly bill a
# rebuild to us. Same rule, same place in the flow.
rm -f "$workspace/work/.env"

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

    # The shipped fixture already has its history view built, every feature label
    # written by the LLM. Nothing here rebuilds it. A rebuild on this machine
    # would need a credential the bundle deliberately does not carry, and without
    # one the task-relevant features come back as raw symbol lists -- so a rebuild
    # here would quietly change what the participant is asked to read. Check that
    # the fixture matches the installed code instead, and stop if it does not.
    echo "Checking the shipped history view matches the installed sgt."
    if ! "$workspace/toolenv/bin/python" - "$workspace/work" <<'CHECK'
import json, sys
from pathlib import Path
from sgt.lens.cluster import SIGNALS_VERSION
work = Path(sys.argv[1])
tree = json.loads((work / ".sgt/tree/tree.json").read_text())["data"]
built = str(tree.get("signals_version"))
if built != str(SIGNALS_VERSION):
    sys.exit(f"  fixture was built at signals_version {built}, installed sgt is at "
             f"{SIGNALS_VERSION}. The first refresh would regroup every feature, so the "
             f"participant would not see the fixture. Rebuild the fixture with a credential.")
cache = json.loads((work / ".sgt/local/label_cache.json").read_text())["data"]
fallback = sorted(k for k, v in cache.items() if v.get("source") != "llm")
if fallback:
    sys.exit(f"  {len(fallback)} feature label(s) are fallbacks, not real labels: "
             f"{', '.join(fallback[:3])}. Rebuild the fixture with a credential.")
print(f"  {len(tree['nodes'])} nodes, {len(cache)} labels, signals_version {built}")
CHECK
    then
        echo "Do not run the session until this is fixed." >&2
        exit 1
    fi

    # A commit sha alone does not name the code that got installed: a dirty
    # working tree installs something no commit contains. Record the uncommitted
    # part too, so a session is traceable to the exact source.
    build="$(cd "$SGT_SOURCE" && git rev-parse --short HEAD)"
    dirty="$(cd "$SGT_SOURCE" && { git status --porcelain; git diff HEAD; })"
    if [ -n "$dirty" ]; then
        build="$build+$(printf '%s' "$dirty" | shasum -a 256 | cut -c1-12)"
    fi
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
