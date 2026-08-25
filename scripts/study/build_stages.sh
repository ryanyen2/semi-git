#!/usr/bin/env bash
# Build the four stage states protocol v2 asks for, inside an already-built study repo.
#
#   scripts/study/build_stages.sh <repo-dir> sgt
#   scripts/study/build_stages.sh <repo-dir> git --match <sgt-repo-dir>
#
# Each stage starts from a fixed state, whatever the participant did before it
# (docs/study/protocol-v2.md §3). Rather than have the participant's machine
# compute those states at run time -- where a slow or failed step lands in the
# middle of a timed stage -- they are computed once here and left in the repo as
# tags, so `./stage N` is a reset and nothing else.
#
#   study/full      the whole history, clean. Stages 2 and 3 start here.
#   study/removed   the same history with the target work taken out. Stage 4
#                   starts here, and putting the work back is the task.
#   study/stage1    the history one piece of work short of `study/full`; the
#                   missing work is left in `.study/stage1.patch` for the tree.
#
# THE TWO ARMS HAVE TO END UP SHOWING THE SAME THING, AND RECORDING IT DIFFERENTLY.
#
# That split is the whole experiment. Stage 4 asks both arms to put the work
# back, so both must start from a dashboard in the same state, or the two arms
# are restoring different things and the comparison says nothing. What legitimately differs is
# how each history records the removal: one `sgt revert` of a named piece of
# work, or three `git revert` commits whose conflicts a maintainer resolved.
#
# So the sgt arm is built first and its removed state is the definition. The git
# arm then performs its own three reverts -- keeping the three-commit shape its
# participant will have to undo -- and resolves the conflicts against that
# already-known-correct content, rather than by a rule like "take theirs" that
# produced an app which would not start. The script refuses to finish unless both
# arms then render the same pages; see the check at the bottom for why page
# output, and not tree bytes, is the thing that has to agree.
set -euo pipefail

# Captured before the `cd` below: `$0` is relative on most invocations, so
# resolving it afterwards looks for the repo's own `scripts/` and fails.
script_dir="$(cd "$(dirname "$0")" && pwd)"

repo=""; arm=""; match=""
while [ $# -gt 0 ]; do
    case "$1" in
        --match) match="$(cd "$2" && pwd)"; shift 2 ;;
        *) if [ -z "$repo" ]; then repo="$1"; elif [ -z "$arm" ]; then arm="$1"; fi; shift ;;
    esac
done
[ -n "$repo" ] && [ -n "$arm" ] || { echo "usage: build_stages.sh <repo> <git|sgt> [--match <sgt-repo>]" >&2; exit 2; }
repo="$(cd "$repo" && pwd)"
cd "$repo"

# The work the removal stages target. One theme in sgt's vocabulary, three
# commits in git's -- which is the comparison, so it is named in both here
# rather than derived on the fly.
THEME_LABEL="${STUDY_THEME_LABEL:-Event-Day Handling}"
# Oldest first; reverted newest first below.
GIT_SUBJECTS=(
    "track known non-normal days like storms and holidays"
    "mark tracked event days on the daily and monthly charts"
    "exclude event days from averages, keep them in totals"
)

say() { printf '  %s\n' "$*"; }

# `git clean -fd` runs between the steps below and would delete anything this
# script leaves in the tree. Git does not clean *ignored* files without `-x`, so
# the stage artifacts live in an ignored directory. `.git/info/exclude` rather
# than `.gitignore`, because `.gitignore` is a tracked file a participant can see
# and this is not part of the project they are looking after.
# The two task scripts live in the work dir and are untracked, so they need the
# same protection: without it the first `./stage N` deletes `./check` and the
# `./stage` script it is running from, and the next stage has nothing to call.
mkdir -p "$repo/.study"
for keep in '/.study/' '/stage' '/check'; do
    grep -qxF "$keep" "$repo/.git/info/exclude" 2>/dev/null \
        || echo "$keep" >> "$repo/.git/info/exclude"
done

sha_for_subject() {
    # Matched on the whole subject via git's own `--grep` with a literal string.
    # An earlier version split `%H%x1f%s` in awk with `-F'\x1f'`, which BSD awk
    # does not read as a separator, so every lookup silently returned nothing.
    git log --format='%H' --fixed-strings --grep="$1" -1
}

git rev-parse --verify HEAD >/dev/null
git tag -f study/full HEAD >/dev/null
say "study/full = $(git rev-parse --short HEAD)"

# --- stage 1: the last two pieces of work, left in the tree unrecorded -------
#
# Two, not one, and the reason is what stage 1 measures. The card asks how many
# separate jobs the change was, and whether the participant could tell what it
# touched without reading every line -- so the change has to span several files
# and contain more than one job, which is the shape agent work actually arrives
# in. The newest commit alone is a two-line edit to one file: legible at a
# glance in either setup, and therefore incapable of separating them.
#
# sgt's own materialization commits are skipped when counting. Landing one is
# not a piece of the developer's work, and a participant asked to record it
# would be reading sgt's plumbing.
STAGE1_JOBS="${STAGE1_JOBS:-2}"
reals=()
while read -r sha; do
    case "$(git log -1 --format='%s' "$sha")" in
        "sgt land: "*|"sgt revert "*|"sgt restore "*|"sgt undo:"*) continue ;;
    esac
    reals+=("$sha")
    [ "${#reals[@]}" -ge "$STAGE1_JOBS" ] && break
done < <(git rev-list HEAD)
[ "${#reals[@]}" -ge "$STAGE1_JOBS" ] || { echo "fewer than $STAGE1_JOBS real commits" >&2; exit 1; }

oldest="${reals[${#reals[@]}-1]}"
git diff "$oldest^" study/full > "$repo/.study/stage1.patch"
git tag -f study/stage1 "$oldest^" >/dev/null
files=$(git diff --name-only "$oldest^" study/full | wc -l | tr -d ' ')
say "study/stage1 = $(git rev-parse --short "$oldest^") (+ stage1.patch: $STAGE1_JOBS jobs, $files files)"
# The card claims the change spans more than one file. Checked, because a
# testbed rebuild could quietly make it one again.
[ "$files" -ge 2 ] || { echo "stage 1's change touches only $files file(s)" >&2; exit 1; }

# --- a pristine copy of sgt's own state, taken BEFORE anything is removed ----
#
# `./stage N` restores this, because resetting the files is not the whole reset:
# sgt keeps an append-only record of every removal, so a revert in stage 3 is
# still in the store during stage 4, and the restore there reverses the wrong
# removal and puts back two files out of five.
#
# Taken here rather than at the end, which is the mistake the first version made.
# Snapshotting after the build's own `sgt revert` shipped a "pristine" copy that
# already contained a removal, so every participant hit the same broken stage 4
# the accumulated state would have caused.
if [ "$arm" = sgt ]; then
    git checkout -q -f -B main study/full
    git clean -qfd
    sgt advanced resync >/dev/null 2>&1 || true
    rm -f "$repo/.study/sgt-pristine.tar"
    tar -cf "$repo/.study/sgt-pristine.tar" -C "$repo" .sgt
    say "saved a pristine .sgt ($(du -h "$repo/.study/sgt-pristine.tar" | cut -f1 | tr -d ' '))"
fi

# --- study/removed: the target work taken out --------------------------------
git checkout -q -f -B main study/full
git clean -qfd

if [ "$arm" = sgt ]; then
    sgt revert "$THEME_LABEL" --yes >/dev/null
    say "removed via: sgt revert \"$THEME_LABEL\" --yes"
else
    [ -n "$match" ] || { echo "the git arm needs --match <sgt-repo> to resolve against" >&2; exit 1; }
    want="$(git -C "$match" rev-parse study/removed 2>/dev/null)" || {
        echo "$match has no study/removed; build the sgt arm first" >&2; exit 1; }
    for ((i=${#GIT_SUBJECTS[@]}-1; i>=0; i--)); do
        sha="$(sha_for_subject "${GIT_SUBJECTS[$i]}")"
        [ -n "$sha" ] || { echo "no commit for: ${GIT_SUBJECTS[$i]}" >&2; exit 1; }
        if ! git revert --no-edit "$sha" >/dev/null 2>&1; then
            # Resolved against the sgt arm's already-verified removed content,
            # for the files that conflicted only. Resolving by a blanket rule
            # ("take theirs") threw away the later date-window work and left an
            # app that would not start, which is the trap this stage is about --
            # it is the participant's problem to solve in stage 3, not a way to
            # build the fixture.
            while read -r f; do
                [ -n "$f" ] || continue
                if git -C "$match" cat-file -e "$want:$f" 2>/dev/null; then
                    git -C "$match" show "$want:$f" > "$repo/$f"
                else
                    rm -f "$repo/$f"
                fi
            done < <(git diff --diff-filter=U --name-only)
            git add -A
            git -c core.editor=true revert --continue >/dev/null 2>&1 || {
                echo "could not complete the git-arm removal" >&2; exit 1; }
        fi
    done
    say "removed via: ${#GIT_SUBJECTS[@]} git reverts"
fi

python3 check.py >/dev/null 2>&1 || { echo "the app does not run after the removal" >&2; exit 1; }
git tag -f study/removed HEAD >/dev/null
tree="$(git rev-parse HEAD^{tree})"
say "study/removed = $(git rev-parse --short HEAD)  tree $(git rev-parse --short "$tree")"

if [ -n "$match" ]; then
    # Compared by what the dashboard renders, not by tree bytes.
    #
    # Two earlier versions of this check were wrong in opposite directions. A
    # whole-tree hash always fails, because the sgt arm carries a `.sgt/` the
    # git arm has no use for, in every state including the untouched one. A
    # file-list comparison fails on one dead file: reverting the commit that
    # created `bikecount/events.py` deletes it, while sgt removes the function
    # inside it and leaves the day list behind. Neither is wrong, and no page
    # reads that file, so neither changes what a participant sees or does.
    #
    # What has to agree is the state the stage hands them, and they meet it
    # through the running app. Both arms rendering the same six pages byte for
    # byte is that, stated in the same terms the study scores everything else in.
    snap="$repo/.study/snap"; rm -rf "$snap"; mkdir -p "$snap/mine" "$snap/theirs"
    query="${STUDY_SNAP_QUERY:-start=2013-09-01&end=2022-09-30}"
    here="$(cd "$script_dir/../.." && pwd)"
    python3 "$here/scripts/study/harvest/snap.py" "$repo" "$snap/mine" "$query" >/dev/null 2>&1
    (cd "$match" && git stash -q --include-untracked 2>/dev/null || true)
    git -C "$match" checkout -q -f -B main study/removed
    python3 "$here/scripts/study/harvest/snap.py" "$match" "$snap/theirs" "$query" >/dev/null 2>&1
    git -C "$match" checkout -q -f -B main study/full
    if ! diff -rq "$snap/mine" "$snap/theirs" >/dev/null 2>&1; then
        echo "the two arms' removed states render different pages, so stage 4" >&2
        echo "would be a different task in each. Refusing." >&2
        diff -rq "$snap/mine" "$snap/theirs" >&2 || true
        exit 1
    fi
    rm -rf "$snap"
    say "removed state renders identically to the sgt arm on every page"
fi

git checkout -q -f -B main study/full
git clean -qfd

# A pristine copy of sgt's own derived state, taken here and restored by
# `./stage N`.
#
# Resetting the files is not the whole reset. sgt keeps an append-only record of
# every removal in `.sgt/`, so a participant who reverts in stage 3 carries that
# record into stage 4, and the next revert lands on a store that already
# excludes those ops. The restore that follows then reverses the wrong one and
# puts back two files out of five, which fails the stage for a reason the
# participant did nothing to cause. `sgt advanced resync --reseed` does not clear
# it either: the ops are still in the store.
#
# So the stage script puts `.sgt/` back exactly as it shipped, which is what
# "this undoes anything left over from the last stage" has to mean for a tool
# that keeps state of its own.
[ "$arm" = sgt ] && sgt advanced resync >/dev/null 2>&1
say "left at study/full"
