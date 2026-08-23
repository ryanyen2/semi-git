#!/usr/bin/env bash
# Step 1 of the task block: put the defect on the screen.
#
# Prescribed rather than described because whether somebody thinks to run the
# program is not what the study is about, and because two arms typing their own
# commands would be comparing two different observations. Everyone sees the same
# output, byte for byte, and the sheet prints what this does so nobody has to
# take it on trust.
#
# It works in a scratch store under $TMPDIR and never touches the project's own
# data, so a participant can run it as many times as they like, before or after
# changing anything -- which is the point: step 3 runs `check.sh`, which repeats
# these same two cases.
#
# The suite is run at the end, and it PASSES. That is not a mistake in the
# script. The test that guards this behaviour calls the comparison helper the
# agent left behind rather than the one the program now uses, so the suite is
# green over a broken program. A participant who sees green here and stops has
# learned the thing the block is about.
set -euo pipefail

cd "$(dirname "$0")"

if [ -d coursecraft ]; then
    app=coursecraft; pkg=coursecraft
    day=Mon; parent="course add CS101 Intro"
    add_a='section add CS101 ada'; add_b='section add CS101 bob'
    person='student add Alice alice@example.org'
    join=enroll; markers='conflicts rooms'
else
    app=confplan; pkg=confplan
    day=Sat; parent="talk add T1 Opening"
    add_a='session add T1 ada'; add_b='session add T1 bob'
    person='attendee add Alice alice@example.org'
    join=register; markers='conflicts rooms'
fi

store="${TMPDIR:-/tmp}/study-scratch-$$.json"
trap 'rm -f "$store"' EXIT
py="$(command -v python3)"
[ -x .venv/bin/python ] && py=.venv/bin/python

run() {
    printf '\n\033[1m$ %s %s\033[0m\n' "$app" "$*"
    "$py" -m "$pkg.cli" --data "$store" "$@" || true
}

echo "Setting up a scratch copy. Nothing here touches the project."
run init
# shellcheck disable=SC2086
run $parent
# Two bookings that meet exactly, in one room: the first ends at the minute the
# second starts. Nothing about them overlaps.
run $add_a --slot "$day 09:00-10:30" --room R1 --capacity 5
run $add_b --slot "$day 10:30-12:00" --room R1 --capacity 5
run $person

printf '\n\033[1m--- one person, two bookings that meet exactly ---\033[0m\n'
run "$join" 1 1
run "$join" 1 2

printf '\n\033[1m--- the room audit, over those same two bookings ---\033[0m\n'
run room audit

printf '\n\033[1m--- and now the tests that cover this area ---\033[0m\n'
for m in $markers; do
    printf '\n\033[1m$ pytest -m %s\033[0m\n' "$m"
    "$py" -m pytest -q -m "$m" --tb=no -p no:cacheprovider || true
done
