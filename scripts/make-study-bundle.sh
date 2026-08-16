#!/usr/bin/env bash
# Build one participant folder, ready to hand out.
#
#   scripts/make-study-bundle.sh <git|sgt> <coursecraft|confplan>
#
# One bundle per condition and project, not per participant: four in total,
# built once and reused. Everything specific to a person -- which half they are
# on, and the keys for their session -- is fetched from the study by the setup
# script, using the code from their study page. That is what makes the bundles
# reusable, and it is also what lets the setup script refuse to configure itself
# if somebody has been handed the wrong one.
#
# Everything slow or secret happens here rather than on their machine: the
# history view is pre-refreshed so their first command is fast, and no key ever
# travels in the file.
set -euo pipefail

SGT_SOURCE="${SGT_SOURCE:-$HOME/repos/semi-git}"
STUDY_REPOS="${STUDY_REPOS:-$HOME/repos/sgt-study}"
# Straight into the website's static files. `firebase deploy --only hosting`
# then publishes the bundles alongside the site, and the download link on the
# participant's setup page already points at them. Uploading a bundle somewhere
# and pasting a URL back in is a step that can be forgotten between building a
# bundle and handing out a link, and it fails silently when it is.
OUT="${OUT:-$SGT_SOURCE/web/public/bundles}"
BUNDLE_SRC="$SGT_SOURCE/scripts/study-bundle"

if [ $# -ne 2 ]; then
    echo "usage: $0 <git|sgt> <coursecraft|confplan>" >&2
    exit 2
fi
condition="$1"; project="$2"

case "$condition" in git|sgt) ;; *) echo "condition must be git or sgt" >&2; exit 2 ;; esac
case "$project" in coursecraft|confplan) ;; *) echo "project must be coursecraft or confplan" >&2; exit 2 ;; esac

# The participant sees this name. It says nothing about which setup is which.
token=a; [ "$condition" = sgt ] && token=b
name="study-$project-$token"
staging="${STAGING:-$HOME/study/_build}/$name"

source_repo="$STUDY_REPOS/$project"
[ "$condition" = git ] && source_repo="$STUDY_REPOS/baseline-$project"
[ -d "$source_repo" ] || { echo "no study project at $source_repo" >&2; exit 1; }
[ -d "$BUNDLE_SRC" ] || { echo "no bundle template at $BUNDLE_SRC" >&2; exit 1; }

echo "Building $name  ($condition, $project)"
rm -rf "$staging"
mkdir -p "$staging/install" "$staging/notes"

cp -R "$source_repo" "$staging/work"
cp -R "$BUNDLE_SRC/bin" "$staging/bin"
cp -R "$BUNDLE_SRC/telemetry" "$staging/telemetry"
cp "$BUNDLE_SRC/install/setup.sh" "$staging/install/setup.sh"
chmod +x "$staging/install/setup.sh" "$staging"/bin/study-*

# A key from a previous build must never travel in a bundle.
rm -f "$staging/work/.env"
rm -rf "$staging/work/.venv" "$staging/telemetry/state.json" \
       "$staging/telemetry/events.jsonl" "$staging/telemetry/uploaded.txt"

echo "  Building the test environment."
(
    cd "$staging/work"
    rm -rf .venv
    uv venv -q -p 3.12
    uv pip install -q -p .venv/bin/python pytest
)

tests="$(cd "$staging/work" && .venv/bin/python -m pytest -q 2>&1 | tail -1)"
echo "  $tests"
case "$tests" in
    *"38 passed"*) ;;
    *) echo "Expected 38 passing tests. Not shipping this." >&2; exit 1 ;;
esac

tool_build=""
tool_version=""
if [ "$condition" = sgt ]; then
    echo "  Building the tool wheel."
    (cd "$SGT_SOURCE" && uv build --wheel -o "$staging/install" -q)
    # The released version, beside the commit id. The sha is the precise answer
    # to "which build" and the version is the one a reader of the paper can act
    # on, so both are recorded rather than either being derived later.
    tool_version="$(cd "$SGT_SOURCE" && python3 -c \
        'import re,pathlib;print(re.search(r"^version = \"([^\"]+)\"", pathlib.Path("pyproject.toml").read_text(), re.M).group(1))')"
    # The wheel is built from the working tree, so a commit id alone describes
    # the shipped code only when that tree is clean. Recording a bare sha beside
    # uncommitted changes claims a reproducibility the bundle does not have, and
    # "which build did participant 7 run" is a question the paper has to answer.
    tool_build="$(cd "$SGT_SOURCE" && git rev-parse --short HEAD)"
    if [ -n "$(cd "$SGT_SOURCE" && git status --porcelain -- sgt/ pyproject.toml)" ]; then
        tool_build="$tool_build-dirty"
        echo
        echo "  WARNING: sgt has uncommitted changes, so this wheel is not any commit."
        echo "  Recorded as $tool_build. Commit before building the bundles you hand"
        echo "  out, or the study cannot say which build each participant ran."
        echo
    fi

    echo "  Installing it here, to warm the history view."
    uv venv -q --clear "$staging/toolenv"
    uv pip install -q -p "$staging/toolenv/bin/python" "$SGT_SOURCE"
    cat > "$staging/bin/sgt" <<WRAPPER
#!/usr/bin/env bash
exec "$staging/toolenv/bin/sgt" "\$@"
WRAPPER
    chmod +x "$staging/bin/sgt"

    # A fresh copy shows a provisional set of features that collapses to a
    # different, smaller set on the first refresh, with two renamed in place.
    # Doing it here means every participant sees one stable set all session.
    echo "  Refreshing the history view. About thirty seconds."
    (cd "$staging/work" && "$staging/bin/sgt" log --refresh >/dev/null 2>&1 || true)
fi

echo "  Building the practice copy."
"$SGT_SOURCE/scripts/make-practice-repo.sh" "$staging/practice" "$condition" "$staging" >/dev/null

claude_version="$(claude --version 2>/dev/null | awk '{print $1}' || true)"

# A bundle built for a rehearsal has to say so, in itself. The participant runs
# the same printed command either way and has no reason to know an environment
# variable exists, so a rehearsal bundle that only works when you already know
# the trick rehearses nothing. Set STUDY_FIRESTORE_HOST when building to point a
# bundle at a local emulator; leave it unset for the real study.
cat > "$staging/study.json" <<META
{
  "condition": "$condition",
  "project": "$project",
  "bundleVersion": "$(date +%Y%m%d)-$token",
  "toolVersion": "${tool_version:-}",
  "toolBuild": "${tool_build:-null}",
  "claudeVersion": "${claude_version:-}",
  "firestoreHost": "${STUDY_FIRESTORE_HOST:-}"
}
META

cat > "$staging/START-HERE.txt" <<'START'
Before your session, open a terminal in this folder and run:

    bash install/setup.sh YOURCODE

Your code is on your study page, in the command you can copy from the setup
step. The setup takes a few minutes and downloads its own Python, so it will
not change anything else on your machine.

When it finishes, the checklist on your study page will already be green.

Nothing here uses your own AI assistant account. The assistant runs on a key we
issue for this session and revoke afterwards.
START

# Anything built for this machine is left out. A virtualenv bakes in absolute
# paths, so shipping ours would break their install rather than save them time.
mkdir -p "$OUT"
tar czf "$OUT/$name.tgz" -C "$(dirname "$staging")" \
    --exclude="$name/work/.venv" \
    --exclude="$name/toolenv" \
    --exclude="$name/practice/.venv" \
    --exclude="__pycache__" \
    --exclude=".DS_Store" \
    --exclude=".pytest_cache" \
    "$name"

echo
echo "Bundle: $OUT/$name.tgz  ($(du -h "$OUT/$name.tgz" | cut -f1))"
echo "This is the $condition condition on $project. The filename does not say so, deliberately."
[ -n "$tool_build" ] && echo "Tool build shipped: $tool_build"
echo
echo "It will be served at /bundles/$name.tgz, which is where the participant's"
echo "setup page already looks. Publish it with:"
echo
echo "    cd $SGT_SOURCE/web && npm run build && firebase deploy --only hosting"
exit 0
