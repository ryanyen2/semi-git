#!/usr/bin/env bash
# Where you ended up. Run after step 3 and again after step 4c.
#
# Prescribed for the same reason `show-the-problem.sh` is: whether somebody
# thinks to verify is not what the study is about. It repeats step 1's two cases
# so the participant can see the defect gone (or still there) in the same words
# they first saw it, then runs the whole suite by feature area, then starts the
# program.
#
# The program is started last and on purpose. A pilot participant finished with
# 29 passing tests and an application that would not start, because nothing in
# the suite builds the command line parser.
#
# This reports. It does not score, and it says so, because a participant reading
# a green line as "correct" would stop looking. Scoring is
# scripts/score_study_repo.py, run afterwards by the experimenter.
set -euo pipefail

cd "$(dirname "$0")"

if [ -d coursecraft ]; then
    app=coursecraft; pkg=coursecraft
    day=Mon; parent="course add CS101 Intro"
    add_a='section add CS101 ada'; add_b='section add CS101 bob'
    person='student add Alice alice@example.org'; join=enroll
else
    app=confplan; pkg=confplan
    day=Sat; parent="talk add T1 Opening"
    add_a='session add T1 ada'; add_b='session add T1 bob'
    person='attendee add Alice alice@example.org'; join=register
fi

store="${TMPDIR:-/tmp}/study-check-$$.json"
trap 'rm -f "$store"' EXIT
py="$(command -v python3)"
[ -x .venv/bin/python ] && py=.venv/bin/python

quiet() { "$py" -m "$pkg.cli" --data "$store" "$@" >/dev/null 2>&1 || true; }
run() {
    printf '\n\033[1m$ %s %s\033[0m\n' "$app" "$*"
    "$py" -m "$pkg.cli" --data "$store" "$@" || true
}

printf '\033[1m=== the two cases from step 1 ===\033[0m\n'
quiet init
# shellcheck disable=SC2086
quiet $parent
quiet $add_a --slot "$day 09:00-10:30" --room R1 --capacity 5
quiet $add_b --slot "$day 10:30-12:00" --room R1 --capacity 5
quiet $person
run "$join" 1 1
run "$join" 1 2
run room audit
printf '\n\033[2mBoth of those should go through, and the audit should say nothing.\033[0m\n'

printf '\n\033[1m=== the whole suite, by feature area ===\033[0m\n'
# `\{1,\}` is a GNU-ism that BSD sed accepts and silently matches nothing with,
# so this printed an empty feature list on macOS and looked like a suite with no
# markers rather than a broken extraction. `[[:space:]][[:space:]]*` works in both.
markers=$(sed -n '/^markers *=/,$p' pytest.ini |
    sed -n 's/^[[:space:]][[:space:]]*\([a-z_][a-z_]*\):.*/\1/p')
[ -n "$markers" ] || echo '  (no feature markers found in pytest.ini)'
for m in $markers; do
    # `|| true` because pytest exits non-zero for both a failing test (1) and a
    # marker with nothing under it (5), and under `set -euo pipefail` either one
    # ends the script at the first red feature -- which is exactly the run where
    # the participant most needs to see the other nineteen lines.
    out=$("$py" -m pytest -q -m "$m" --tb=no -p no:cacheprovider 2>&1 | tail -1) || true
    # Checked in this order because pytest's summary line for a marker with
    # nothing under it is "38 deselected", which contains neither "passed" nor
    # "failed" -- matching on the absence of trouble painted those green and made
    # a feature the participant had just deleted look like a feature that passed.
    case "$out" in
        *failed*|*error*) printf '  \033[31m%-12s %s\033[0m\n' "$m" "$out" ;;
        *passed*)         printf '  \033[32m%-12s %s\033[0m\n' "$m" "$out" ;;
        *)                printf '  \033[2m%-12s nothing left under this name\033[0m\n' "$m" ;;
    esac
done

printf '\n\033[1m=== does the program still start ===\033[0m\n'
if "$py" -m "$pkg.cli" --help >/dev/null 2>&1; then
    printf '  \033[32myes\033[0m\n'
else
    printf '  \033[31mno -- it will not even print its help\033[0m\n'
    "$py" -m "$pkg.cli" --help 2>&1 | tail -5
fi

printf '\n\033[2mThis is a report, not a score. Red is information, not a verdict on you.\033[0m\n'
