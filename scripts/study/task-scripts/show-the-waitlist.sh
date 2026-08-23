#!/usr/bin/env bash
# Step 4a: what the waitlist actually does, before being asked to remove it.
#
# Removal tasks in the pilots were answered against a guess at what the feature
# was. "Gone" then meant whatever the participant had assumed, and two people
# removing different amounts of the same feature scored differently for reasons
# that had nothing to do with the representation. This shows the whole thing
# working, once, so "gone" means the same thing to everybody.
#
# It is deliberately a tour and not a test. Every line is a command a person
# would actually run, in the order a person would hit them: queue up, look at
# the queue, free a seat, watch it fill, read the notice.
#
# Scratch store under $TMPDIR, so it never touches the project's data and can be
# re-run at any point.
set -euo pipefail

cd "$(dirname "$0")"

if [ -d coursecraft ]; then
    app=coursecraft; pkg=coursecraft; day=Mon
    parent="course add CS101 Intro"; add='section add CS101 ada'
    join=enroll; leave=drop
    p1='student add Ada ada@example.org'
    p2='student add Bo bo@example.org'
    p3='student add Cy cy@example.org'
else
    app=confplan; pkg=confplan; day=Sat
    parent="talk add T1 Opening"; add='session add T1 ada'
    join=register; leave=cancel
    p1='attendee add Ada ada@example.org'
    p2='attendee add Bo bo@example.org'
    p3='attendee add Cy cy@example.org'
fi

store="${TMPDIR:-/tmp}/study-waitlist-$$.json"
trap 'rm -f "$store"' EXIT
py="$(command -v python3)"
[ -x .venv/bin/python ] && py=.venv/bin/python

quiet() { "$py" -m "$pkg.cli" --data "$store" "$@" >/dev/null 2>&1 || true; }
run() {
    printf '\n\033[1m$ %s %s\033[0m\n' "$app" "$*"
    "$py" -m "$pkg.cli" --data "$store" "$@" || true
}

echo "A scratch copy with one booking that holds exactly one person."
quiet init
# shellcheck disable=SC2086
quiet $parent
quiet $add --slot "$day 09:00-10:30" --room R1 --capacity 1
quiet $p1; quiet $p2; quiet $p3

printf '\n\033[1m--- the one seat goes to the first person ---\033[0m\n'
run "$join" 1 1
printf '\n\033[1m--- so the next two have to queue ---\033[0m\n'
run "$join" 2 1
run waitlist join 2 1
run waitlist join 3 1

printf '\n\033[1m--- the queue, in the order they joined ---\033[0m\n'
run waitlist show 1

printf '\n\033[1m--- somebody leaves, and the seat fills itself ---\033[0m\n'
run "$leave" 1 1
run waitlist show 1

printf '\n\033[1m--- and the person who got it is told ---\033[0m\n'
run notices

printf '\n\033[2mThat is all of it: queueing, the queue, the automatic fill, and the notice.\033[0m\n'
