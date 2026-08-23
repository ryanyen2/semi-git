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
# Pinned, and recorded in study.json. Which GitLens a participant had is a
# question the paper has to be able to answer, and it ships a new version most
# weeks.
GITLENS_VERSION="${GITLENS_VERSION:-19.0.1}"

# Installed into both conditions' editor profiles at setup, pinned.
#
# Not optional, and not left to the editor. VS Code offers to install Python
# support the first time a .py file is opened, so whether a participant got
# Pylance came down to whether their condition led them to open a file --
# during the first editor rehearsal the git arm ended up with 198 MB of Python
# tooling and the sgt arm with none. On a task about reading unfamiliar Python,
# go-to-definition in one arm and not the other is not a difference between two
# ways of recording history.
#
# Too big to ship inside a bundle, so they come from the marketplace during
# setup, at fixed versions. The doctor fails if the set that lands is not this.
EDITOR_EXTENSIONS='["ms-python.python@2026.4.0", "ms-python.vscode-pylance@2026.3.1", "ms-python.debugpy@2026.6.0", "ms-python.vscode-python-envs@1.36.0"]'

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

# The three prescribed steps the task cards name, beside the project because
# each one starts with `cd "$(dirname "$0")"` and reads the project's own
# `pytest.ini` and `.venv`. Card 1 is the sentence "run ./show-the-problem.sh",
# so a bundle without these is a session that stops on its first card -- and the
# remote bundle is the default path, not the exception.
#
# Both conditions get byte-identical copies. That is the point of prescribing
# the step: the arms are compared on what they could do about the defect, not on
# whether they typed the same eight commands to see it.
for s in show-the-problem check show-the-waitlist; do
    cp "$SGT_SOURCE/scripts/study/task-scripts/$s.sh" "$staging/work/$s.sh"
    chmod +x "$staging/work/$s.sh"
done

# The project brief travels too. It is read once on the website with no clock
# running, and then wanted again mid-card -- "what was it allowed to refuse to
# do?" -- at which point the only copy is behind the card being timed.
cp "$SGT_SOURCE/docs/study/materials/03-project-$project.md" "$staging/project.md"

# A key from a previous build must never travel in a bundle.
rm -f "$staging/work/.env"
rm -rf "$staging/work/.venv" "$staging/telemetry/state.json" \
       "$staging/telemetry/events.jsonl" "$staging/telemetry/uploaded.txt"

# Nor may anything that names this machine. The study projects are built here,
# and an untracked `.claude/` picks up hooks pointing at an absolute path in
# this checkout. On a participant's machine that path does not exist, so every
# prompt in the sgt condition would fire a hook that fails, and the condition
# would quietly lose the intent capture it depends on. The sgt bundles get this
# file written back, against their own copy of the tool, during setup.
rm -rf "$staging/work/.claude"

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
    #
    # Built with the key from the source checkout's `.env`, not with whatever
    # the builder's shell happens to hold. Feature names come from an LLM call,
    # and without a key every one of them falls back to a joined list of symbol
    # names -- `add_item apply_discount…` instead of `Shopping Cart`. That is
    # not hypothetical: it is what the practice repo shipped with, so the first
    # thing a participant met in the sgt condition was the one thing the sgt
    # condition is supposed to have. The refresh prints a warning to stderr when
    # a labeling call is rejected, so the check below reads for it.
    # `--rebuild`, not `--refresh`, and the reason is provenance rather than
    # quality. Both now produce the same graph -- the gap that first prompted
    # this (nine symbols owned by no feature on `confplan`) was one aliased id in
    # `_apply_assign_pins` and is fixed. What a refresh still does is splice from
    # whatever tree the source repository happens to carry, so the bundle's graph
    # depends on that repo's build history. A bundle is built once, from a
    # pristine copy, and handed to one participant who cannot rebuild it; a
    # minute buys a graph that is a function of the code alone, which is the same
    # argument the wheel-provenance check above makes.
    echo "  Rebuilding the history view. About a minute."
    refresh_log="$staging/.refresh.log"
    (
        set -a
        # shellcheck disable=SC1091
        [ -f "$SGT_SOURCE/.env" ] && . "$SGT_SOURCE/.env"
        set +a
        cd "$staging/work" && "$staging/bin/sgt" log --rebuild
    ) > "$refresh_log" 2>&1 || true
    if grep -q "LLM labeling call was rejected" "$refresh_log"; then
        echo
        echo "  WARNING: feature naming fell back to terse symbol lists. Usually a missing" >&2
        echo "  or out-of-credit OPENAI_API_KEY in $SGT_SOURCE/.env. Fix it and rebuild," >&2
        echo "  or the sgt condition ships without the readable names it is being tested on." >&2
        echo
    fi
    rm -f "$refresh_log"

    # The graph the participant will actually navigate, checked rather than
    # assumed. A degenerate build is silent from every angle a builder would
    # look at -- `sgt log` still lists every save, `sgt find` still ranks
    # everything -- and only shows itself when a participant asks a feature what
    # it contains and is told "0 symbols in 0 files".
    echo "  Checking the feature graph."
    if ! "$staging/toolenv/bin/python" "$SGT_SOURCE/scripts/check_graph_integrity.py" "$staging/work"; then
        echo >&2
        echo "  Not shipping this bundle. Re-run the build; if it happens twice on the" >&2
        echo "  same project, the repo needs looking at rather than rebuilding." >&2
        exit 1
    fi

    # The search index, embedded once here rather than on first use in a
    # session. Built after the refresh so it indexes the feature set the
    # participant will actually see, and checked rather than assumed: an index
    # that fell back to word matching still answers, so nothing in a session
    # would ever say that half of what `find` promises is missing.
    # Built with this machine's key, taken from the source checkout's `.env`.
    # The staged repo has no `.env` yet -- provisioning writes the participant's
    # key at setup -- so without this the index is embedded with whatever the
    # builder's shell happens to hold, which is how the first one shipped with
    # no embeddings at all.
    echo "  Building the search index."
    embedded="$(
        set -a
        # shellcheck disable=SC1091
        [ -f "$SGT_SOURCE/.env" ] && . "$SGT_SOURCE/.env"
        set +a
        "$staging/toolenv/bin/python" - <<PY
from sgt.lens.search import build_index
print("yes" if build_index("$staging/work")["embedded"] else "no")
PY
    )"
    if [ "$embedded" != "yes" ]; then
        echo
        echo "  WARNING: the search index has no embeddings, so \`sgt find\` in this"
        echo "  bundle will match on words rather than meaning. Usually a missing or"
        echo "  out-of-credit OPENAI_API_KEY. Fix it and rebuild, or the sgt condition"
        echo "  ships with half of its search."
        echo
    fi
else
    # The assistant's guidance for the half without a history tool. The sgt half
    # ships three skills and an MCP server (installed at setup, above); with
    # nothing here the git half would be plain Claude Code against git, and the
    # agent-half comparison would be a guided tool against an unguided one rather
    # than two tools. One skill, teaching git at the same depth: read history
    # before changing it, add commits on top by default, and never rewrite shared
    # history unasked. `setup.sh` copies it into `work/.claude/skills/`.
    echo "  Packaging the assistant skill."
    cp -R "$BUNDLE_SRC/git-skills" "$staging/install/git-skills"
fi

# ---------------------------------------------------------------------------
# The editor extension for this condition
# ---------------------------------------------------------------------------
#
# Both conditions get a graphical way to read history, or the comparison is
# between a tool and a terminal rather than between two representations. In the
# sgt condition that is this project's own extension; in the git condition it is
# GitLens, which is what people actually use to read git history in an editor.
#
# Both travel inside the bundle at a fixed version. Installing from the
# marketplace during a session would mean participant three and participant nine
# ran different software, with nothing in the data saying so.

echo "  Packaging the editor extension."
if [ "$condition" = sgt ]; then
    (cd "$SGT_SOURCE/editor/vscode" && npm run package >/dev/null 2>&1) \
        || { echo "Could not build the extension." >&2; exit 1; }
    (cd "$SGT_SOURCE/editor/vscode" && npx --no-install @vscode/vsce package \
        --no-dependencies --allow-missing-repository -o "$staging/install/semi-git.vsix" >/dev/null 2>&1) \
        || { echo "Could not package the extension. Is @vscode/vsce available?" >&2; exit 1; }
    editor_ext="semi-git $(python3 -c 'import json;print(json.load(open("'"$SGT_SOURCE"'/editor/vscode/package.json"))["version"])')"
else
    cached="${GITLENS_VSIX_CACHE:-$HOME/.cache/study-bundles}/gitlens-$GITLENS_VERSION.vsix"
    if [ ! -s "$cached" ]; then
        mkdir -p "$(dirname "$cached")"
        echo "  Fetching GitLens $GITLENS_VERSION."
        curl -sSL -o "$cached.gz" \
            "https://marketplace.visualstudio.com/_apis/public/gallery/publishers/eamodio/vsextensions/gitlens/$GITLENS_VERSION/vspackage" \
            || { echo "Could not download GitLens." >&2; exit 1; }
        # The marketplace serves the package gzipped, whatever the extension says.
        gunzip -c "$cached.gz" > "$cached" && rm -f "$cached.gz"
    fi
    cp "$cached" "$staging/install/gitlens.vsix"
    editor_ext="gitlens $GITLENS_VERSION"
fi
echo "  $editor_ext"

# The practice copy, built with the same key for the same reason as the work
# copy above -- and NOT silenced, because it pins the feature names the practice
# sheet quotes literally and says so on stderr when a pin does not stick.
echo "  Building the practice copy."
(
    set -a
    # shellcheck disable=SC1091
    [ -f "$SGT_SOURCE/.env" ] && . "$SGT_SOURCE/.env"
    set +a
    "$SGT_SOURCE/scripts/make-practice-repo.sh" "$staging/practice" "$condition" "$staging"
) | sed 's/^/  /'

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
  "editorExtension": "${editor_ext:-}",
  "editorExtensions": $EDITOR_EXTENSIONS,
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
