#!/usr/bin/env bash
# Build one participant folder, ready to hand out.
#
#   scripts/make-study-bundle.sh <git|sgt> <bikecount|footfall>
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
    echo "usage: $0 <git|sgt> <bikecount|footfall>" >&2
    exit 2
fi
condition="$1"; project="$2"

case "$condition" in git|sgt) ;; *) echo "condition must be git or sgt" >&2; exit 2 ;; esac
case "$project" in bikecount|footfall) ;; *) echo "project must be bikecount or footfall" >&2; exit 2 ;; esac

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

# The two task scripts travel with the work repo. Protocol v2 opens every stage
# with `./stage N` and closes the two operating stages with `./check N`, so
# these are not optional extras: without them the participant cannot start.
cp "$SGT_SOURCE/scripts/study/task-scripts/stage" "$staging/work/stage"
cp "$SGT_SOURCE/scripts/study/task-scripts/check" "$staging/work/check"
chmod +x "$staging/work/stage" "$staging/work/check"

# The stage states -- tags, the stage-1 patch, and (in the sgt arm) a pristine
# copy of sgt's own record -- are built into the SOURCE repos by
# `scripts/study/prep-stages.sh` and travel here with the `cp -R` above.
#
# Not built here, and the reason is ordering: the git arm's removed state is
# checked against the sgt arm's, so the two have to be built together, in order,
# against each other. A bundle is built one arm at a time and has no view of its
# twin. Verified rather than assumed, because a bundle missing these is one a
# participant cannot start a single stage with, and nothing else would say so.
# The two arms need different things, because they reach stage 4 differently.
# The git arm checks out a `study/removed` holding its three revert commits. The
# sgt arm performs the removal live there (a restore has to reverse a removal
# this repo recorded making), so it has no such tag and must not have one -- a
# reachable build-time revert is what made restore resolve against the wrong
# removal.
needed=(study/full)
[ "$condition" = git ] && needed+=(study/removed)
for tag in "${needed[@]}"; do
    git -C "$staging/work" rev-parse --verify "$tag" >/dev/null 2>&1 || {
        echo "$source_repo has no $tag -- run scripts/study/prep-stages.sh first" >&2; exit 1; }
done
# The `cp -R` above copies the source repo's WORKING TREE, so whatever branch it
# was left on is the branch the participant gets. Rehearsing the stages in a
# source repo leaves it wherever the last `./stage N` put it -- at the three
# revert commits of `study/removed`, say -- and a bundle built from that
# ships a project already one or two pieces of work short, with nothing saying
# so. Every stage still "works", because each one resets first; what breaks is
# the participant reading a history that is missing the end of itself.
head_sha="$(git -C "$staging/work" rev-parse HEAD)"
if [ "$head_sha" != "$(git -C "$staging/work" rev-parse study/full)" ]; then
    echo "$source_repo is not checked out at study/full (HEAD is $(git -C "$staging/work" rev-parse --short HEAD))." >&2
    echo "Run \`./stage 2\` in it, then build again." >&2
    exit 1
fi
if [ -n "$(git -C "$staging/work" status --porcelain --untracked-files=no)" ]; then
    echo "$source_repo has uncommitted changes; a bundle would ship them. Run \`./stage 2\` in it." >&2
    exit 1
fi
if [ "$condition" = sgt ]; then
    [ -f "$staging/work/.study/sgt-pristine.tar" ] || {
        echo "$source_repo has no .study/sgt-pristine.tar -- run scripts/study/prep-stages.sh first" >&2; exit 1; }
    if git -C "$staging/work" rev-parse --verify study/removed >/dev/null 2>&1; then
        echo "$source_repo still has a study/removed tag; an sgt arm must not ship one" >&2; exit 1
    fi
    # The rendered pages are the git arm's reference, not the participant's, and
    # they are several hundred KB of text. They do not travel.
    rm -rf "$staging/work/.study/removed-pages"
fi
# Stage 1 stopped replaying an unrecorded agent change, so nothing applies this
# any more. Removed here as well as in `build_stages.sh`, because a source repo
# prepared before that change still carries one and a rebuild would ship it.
rm -f "$staging/work/.study/stage1.patch"

# The stage script needs these to survive its own `git clean`; the source repo's
# `.git/info/exclude` does not travel through `cp -R` of the working tree alone.
mkdir -p "$staging/work/.git/info"
for keep in '/.study/' '/stage' '/check'; do
    grep -qxF "$keep" "$staging/work/.git/info/exclude" 2>/dev/null \
        || echo "$keep" >> "$staging/work/.git/info/exclude"
done

# No project brief travels any more. Protocol v2 has no brief step: each stage
# card carries the two sentences of context it needs, which is the point of the
# design -- the participant is told what just happened one stage at a time
# rather than asked to hold a page of background.

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

# The dashboards have no test suite. They have `check.py`, which renders every page
# the app knows about and fails loudly if one throws, and which grows on its own as
# pages are added because it walks `pages.discover()` rather than a list. That is
# the thing to run before shipping a bundle: not a count of passing tests, which was
# the old command line testbeds' shape and pinned at "38 passed", but the question
# a participant will ask on their first card, which is whether the dashboard comes up.
echo "  Checking the dashboard renders."
smoke="$(cd "$staging/work" && python3 check.py 2>&1 | tail -1)"
echo "  $smoke"
case "$smoke" in
    ok:*) ;;
    *) echo "The dashboard does not render. Not shipping this." >&2; exit 1 ;;
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
    # A provenance-less op is working-tree state from the BUILD machine -- prep's own stage
    # flips mine the dirty tree on contact -- never part of the replayed history. Left in the
    # store it forks the committed chain, and resync's reduction then parks the fork and drops
    # committed producers out of the recorded ideal: the participant's stage-1 `sgt save` said
    # "nothing to save" over eleven modified files (2026-08-31). Strip them BEFORE the rebuild,
    # so nothing downstream (tree, op_leaf, index, pristine tar) ever references one.
    echo "  Stripping leftover working-tree ops from the store."
    "$staging/toolenv/bin/python" - "$staging/work" <<'PY'
import json, pathlib, sys
ops = pathlib.Path(sys.argv[1]) / ".sgt" / "ops"
dropped = 0
if ops.is_dir():
    for f in ops.iterdir():
        if not f.is_file() or f.name.startswith("."):
            continue
        try:
            body = json.loads(f.read_text())
        except Exception:
            continue
        if not body.get("provenance"):
            f.unlink()
            dropped += 1
print(f"  dropped {dropped} pending op(s)")
PY
    (cd "$staging/work" && "$staging/bin/sgt" advanced resync) >/dev/null 2>&1 || true

    # Mining is chunked against a per-contact deadline, so ONE contact on a loaded machine can
    # leave the backfill mid-walk -- and a rebuild over an incomplete ideal ships a graph with
    # real symbols in no frontier (the integrity gate below refused exactly that during a busy
    # publish). Drive the sync to completion first; each contact advances at least one chunk.
    echo "  Syncing the history to completion."
    "$staging/toolenv/bin/python" - "$staging/work" <<'PY'
import sys
from sgt.core.lens import get, sync_status
for i in range(24):
    get(sys.argv[1])
    if sync_status(sys.argv[1])["complete"]:
        print(f"  complete after {i + 1} contact(s)")
        break
else:
    sys.exit("  history sync did not complete after 24 contacts")
PY

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

    # The pristine snapshot must be the SAME tree this bundle ships. build_stages
    # tarred the source repo's .sgt before this staging rebuild existed, so the
    # first `./stage N` flipped the participant onto an older tree with different
    # feature names -- contradicting the tutorial, the answer key, and the search
    # index embedded above (which indexes the rebuilt set). Re-tar from the
    # just-rebuilt state: the staging copy sits at study/full and a rebuild
    # records no removals, so stage 4's restore inverse is untouched.
    echo "  Refreshing the pristine .sgt snapshot to the rebuilt tree."
    rm -f "$staging/work/.study/sgt-pristine.tar"
    tar -cf "$staging/work/.study/sgt-pristine.tar" -C "$staging/work" .sgt

    # Freeze the names. The rebuild's LLM answers live in caches inside this
    # staging copy, which is thrown away after packing -- so the NEXT build
    # re-asked the LLM and every merged leaf's label re-rolled per publish
    # ("Hourly Side Comparisons" one build, "Hourly Side Analysis" the next),
    # silently drifting from the answer key and the printed sheets. Copying the
    # caches back makes the next rebuild a cache hit: same clustering in, same
    # names out. The one deliberate write a bundle build makes outside its own
    # staging directory.
    for cache in local/label_cache.json local/intent_cache.json; do
        if [ -f "$staging/work/.sgt/$cache" ]; then
            mkdir -p "$(dirname "$source_repo/.sgt/$cache")"
            cp "$staging/work/.sgt/$cache" "$source_repo/.sgt/$cache"
        fi
    done
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
# sgt condition that is this project's own extension, shipped inside the bundle
# at a fixed version, because installing from the marketplace during a session
# would mean participant three and participant nine ran different software with
# nothing in the data saying so.
#
# In the git condition it is Visual Studio Code's own Source Control: the view,
# the Source Control Graph, the Timeline, and blame in the editor gutter. This
# used to be GitLens 19.0.1, and dropping it was not a simplification for its
# own sake. GitLens 19 opens on an account: Launchpad wants a GitHub connection
# from the status bar, and `gitlens.ai.enabled` defaults on, which put an AI
# panel -- explain changes, generate commit message, review changes -- inside a
# task block the protocol gives no assistant. Every minute a participant spends
# dismissing a sign-up is a minute charged to git, and an assistant one arm has
# and the other does not is not a difference between two ways of recording
# history.
#
# What it costs: GitLens searches history better than the Timeline does, and
# stage 2 is the locate stage. That is the honest weakness of this arm and
# section 3 of the protocol says so rather than leaving a reader to find it.

echo "  Packaging the editor extension."
if [ "$condition" = sgt ]; then
    (cd "$SGT_SOURCE/editor/vscode" && npm run package >/dev/null 2>&1) \
        || { echo "Could not build the extension." >&2; exit 1; }
    (cd "$SGT_SOURCE/editor/vscode" && npx --no-install @vscode/vsce package \
        --no-dependencies --allow-missing-repository -o "$staging/install/semi-git.vsix" >/dev/null 2>&1) \
        || { echo "Could not package the extension. Is @vscode/vsce available?" >&2; exit 1; }
    editor_ext="semi-git $(python3 -c 'import json;print(json.load(open("'"$SGT_SOURCE"'/editor/vscode/package.json"))["version"])')"
else
    # Nothing to package: it is already in the editor. Recorded all the same,
    # because "which history view did this participant have" is a question the
    # paper has to be able to answer for both arms, and for this one the answer
    # is a version of Visual Studio Code rather than a version of an extension.
    editor_ext="built-in Source Control"
fi
echo "  $editor_ext"

# No practice copy any more. The warm-up happens on the project itself, at the
# state `./stage 0` puts it in, so there is nothing separate to build and
# nothing separate to keep in step with the practice sheet. What made the old
# one worth deleting: its sheet quoted ids out of it verbatim, and a
# participant who ran `git show 44da4ad` from the project folder -- which is
# where the session shell starts -- got `unknown revision`.

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
    --exclude="__pycache__" \
    --exclude=".DS_Store" \
    --exclude=".pytest_cache" \
    "$name"

# ---------------------------------------------------------------------------
# The rehearsal gate: the packed bundle must survive its own study.
# ---------------------------------------------------------------------------
#
# Three different silent-success bugs shipped through this pipeline in one week
# -- a pristine tar that restored a different tree than the bundle shipped, a
# restore that refused on a fork, and a stage-1 save that said "nothing to save"
# over eleven modified files -- and every one of them was only caught by a
# person running the stages. So the build now runs them itself, on the exact
# tgz that would be served, and refuses to finish if any stage's own success
# check fails. Costs about a minute; the alternative costs a participant.
#
# Stage 1 has nothing to perform since it became orientation, so what it checks
# is what the participant is told to read: that the reset leaves a clean tree and
# a dashboard that renders, that the map draws lanes, that `sgt find` answers by
# meaning rather than by word overlap, and that the group covering the newest
# save resolves by its own label -- which is the target stage 1's answer key is
# measured against, and the one thing a rebuild can rename underneath it.
if [ "$condition" = sgt ]; then
    echo "  Rehearsing the stages on the packed bundle."
    rehearsal="$(mktemp -d)"
    tar -xzf "$OUT/$name.tgz" -C "$rehearsal"
    (
        set -euo pipefail
        cd "$rehearsal/$name/work"
        export PATH="$staging/toolenv/bin:$PATH"
        theme="$("$staging/toolenv/bin/python" - <<'PY'
import json, pathlib
squash = lambda t: "".join(c for c in t.casefold() if c.isalnum())
for v in json.loads(pathlib.Path(".sgt/intent/themes.json").read_text()).get("data", {}).values():
    if isinstance(v, dict) and "eventday" in squash(v.get("label", "")):
        print(v["label"]); break
PY
)"
        [ -n "$theme" ] || { echo "  REHEARSAL: no event-day theme in the bundle" >&2; exit 1; }

        newest="$("$staging/toolenv/bin/python" - <<'NEWEST'
import json, pathlib, subprocess, sys
sha = None
for line in subprocess.run(["git", "log", "--format=%H%x00%s", "--no-merges"],
                           capture_output=True, text=True).stdout.splitlines():
    h, _, subject = line.partition("\x00")
    if "sgt land" in subject:
        continue
    sha = h
    break
if sha is None:
    sys.exit("no non-bookkeeping commit")
themes = json.loads(pathlib.Path(".sgt/intent/themes.json").read_text()).get("data", {})
for entry in themes.values():
    if not isinstance(entry, dict):
        continue
    for atom in entry.get("atom_shas") or ():
        if atom.startswith(sha[:7]) or sha.startswith(atom[:7]):
            print(entry.get("label") or "")
            sys.exit(0)
sys.exit("no group covers the newest save")
NEWEST
)"
        [ -n "$newest" ] || {
            echo "  REHEARSAL: stage 1 has no target -- no group covers the newest save" >&2
            exit 1
        }
        echo "    stage 1 target: $newest"

        ./stage 1 >/dev/null
        dirty="$(git status --porcelain | wc -l | tr -d ' ')"
        [ "$dirty" = 0 ] || {
            echo "  REHEARSAL: ./stage 1 left $dirty dirty path(s); it must reset to a clean tree" >&2
            git status --short | sed 's/^/    /' >&2
            exit 1
        }
        smoke="$(python3 check.py 2>&1 | tail -1)"
        case "$smoke" in
            ok:*) ;;
            *) echo "  REHEARSAL: the dashboard does not render at stage 1: $smoke" >&2; exit 1 ;;
        esac
        # The reads stage 1's tips name. A map with no lanes, or a search that
        # fell back to word overlap, is a stage nobody can do -- and both answer
        # something, so neither says so on its own.
        map_out="$(sgt log --no-color 2>&1)"
        printf '%s\n' "$map_out" | grep -q "features" || {
            echo "  REHEARSAL: sgt log drew no feature map:" >&2
            printf '%s\n' "$map_out" | head -5 >&2
            exit 1
        }
        # No `--no-color` on these two: `show` and `find` do not take it, and the
        # flag turns the whole call into an argparse usage error whose output
        # happens to contain neither of the words grepped for below -- so the
        # gate fails on a bundle that is fine. The greps match plain words that
        # no colour code sits inside.
        find_out="$(sgt find "the bit that works out the averages" 2>&1)"
        if printf '%s\n' "$find_out" | grep -q "matched on words"; then
            echo "  REHEARSAL: sgt find fell back to word matching -- the search index has no" >&2
            echo "  embeddings, so stages 1 and 2 both lose a command they are told to use." >&2
            exit 1
        fi
        # Read for the failure, not for a word in the success. `grep -q "edits"`
        # was the first attempt and it rejected bikecount, whose rounding group is
        # one edit -- and `plural()` correctly prints "1 edit". The message a
        # failed resolve prints is the thing to look for.
        show_out="$(sgt show "$newest" 2>&1)" || true
        if printf '%s\n' "$show_out" | grep -q "is not a known"; then
            echo "  REHEARSAL: sgt show cannot resolve stage 1's target by name ($newest)." >&2
            echo "  That name is what the answer key for stage 1 is measured against." >&2
            printf '%s\n' "$show_out" | head -3 >&2
            exit 1
        fi

        ./stage 3 >/dev/null
        revert_out="$(sgt revert "$theme" --yes 2>&1)" || {
            echo "  REHEARSAL: stage-3 revert refused:" >&2
            printf '%s\n' "$revert_out" | tail -3 >&2
            exit 1
        }
        check3="$(./check 3 2>&1)"
        printf '%s\n' "$check3" | grep -q "those match" || {
            echo "  REHEARSAL: check 3 does not match after revert:" >&2
            printf '%s\n' "$check3" | sed 's/^/    /' >&2
            exit 1
        }

        ./stage 4 >/dev/null
        restore_out="$(sgt restore "$theme" --yes 2>&1)" || {
            echo "  REHEARSAL: stage-4 restore refused:" >&2
            printf '%s\n' "$restore_out" | tail -3 >&2
            exit 1
        }
        check4="$(./check 4 2>&1)"
        printf '%s\n' "$check4" | grep -q "those match" || {
            echo "  REHEARSAL: check 4 does not match after restore:" >&2
            printf '%s\n' "$check4" | sed 's/^/    /' >&2
            exit 1
        }
    ) || { echo "REHEARSAL FAILED -- $OUT/$name.tgz is not fit to hand out"; rm -f "$OUT/$name.tgz"; rm -rf "$rehearsal"; exit 1; }
    rm -rf "$rehearsal"
    echo "  Rehearsal passed: orient, remove, check, restore, check."
fi

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
