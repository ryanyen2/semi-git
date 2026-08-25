#!/usr/bin/env bash
# Build the protocol v2 stage states into the study source repos, both arms of
# both projects, in the order they have to be built in.
#
#   scripts/study/prep-stages.sh [project ...]      (default: bikecount footfall)
#
# Run this after rebuilding a testbed and before `scripts/publish-study.sh`.
# `make-study-bundle.sh` only checks the states are there; it cannot build them,
# because the git arm's removed state is verified against the sgt arm's and a
# bundle build sees one arm at a time.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
STUDY_REPOS="${STUDY_REPOS:-$HOME/repos/sgt-study}"
projects=("$@")
[ ${#projects[@]} -gt 0 ] || projects=(bikecount footfall)

for project in "${projects[@]}"; do
    sgt_repo="$STUDY_REPOS/$project"
    git_repo="$STUDY_REPOS/baseline-$project"
    [ -d "$sgt_repo" ] || { echo "no $sgt_repo" >&2; exit 1; }
    [ -d "$git_repo" ] || { echo "no $git_repo" >&2; exit 1; }

    # `< /dev/null` on both: a build step that reads stdin swallows the rest of
    # this loop, which showed up as the git arm silently never running while the
    # script still exited 0.
    echo "== $project (sgt) =="
    "$ROOT/scripts/study/build_stages.sh" "$sgt_repo" sgt < /dev/null

    # Second, and matched against the first: the git arm keeps its own
    # three-revert history but has to land on the same dashboard, or stage 4 is
    # a different task in each arm.
    echo "== $project (git) =="
    "$ROOT/scripts/study/build_stages.sh" "$git_repo" git --match "$sgt_repo" < /dev/null
done

echo
echo "Stage states built. Next: scripts/publish-study.sh"
