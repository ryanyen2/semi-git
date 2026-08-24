#!/usr/bin/env bash
# Turn the harvested repo into the plain git repo the git half of the study uses.
#
#   render_git_arm.sh <harvested-repo> <out-repo>
#
# Two things have to come out, both recorded as finding 40 in sgt-findings.md.
# Landing a session writes a merge commit subjected "sgt land: main", and every
# commit carries one `Sgt-Op:` trailer per op, dozens of lines of them. sgt's own
# views hide both, so leaving them in would only ever cost the git arm, and a
# study that hands one side a worse version of its own tool is not measuring what
# it says it is.
#
# The content is untouched. Each piece of work becomes one commit holding exactly
# the tree that piece of work produced, with its own message and its own author
# date. Shas differ from the harvested repo, so regenerate the answer key against
# whichever repo the participant is actually given.
set -euo pipefail
SRC="$(cd "$1" && pwd)"; OUT="$2"

rm -rf "$OUT"
mkdir -p "$OUT"
cd "$OUT"
git init -q

# Oldest first, skipping sgt's own plumbing commits. A merge subjected "sgt land"
# carries no work of its own: the work is on the commit it merged in. Written as a
# plain word list rather than `mapfile` so this runs on the bash macOS ships.
COMMITS=$(git -C "$SRC" log --format="%H %s" --reverse --no-merges \
  | grep -v " sgt land: " | grep -v " sgt save$" | cut -d' ' -f1)

echo "$(echo "$COMMITS" | wc -l | tr -d ' ') work commit(s) to replay"

for sha in $COMMITS; do
  # Take that commit's tree wholesale, minus sgt's own state directory.
  rm -rf "${OUT:?}"/* 2>/dev/null || true
  git -C "$SRC" archive "$sha" | tar -x -C "$OUT"
  rm -rf "$OUT/.sgt"

  subject=$(git -C "$SRC" log -1 --format=%s "$sha")
  body=$(git -C "$SRC" log -1 --format=%b "$sha" | grep -v "^Sgt-Op: " | grep -v "^Sgt-Node-Id: " | sed '/^$/{ N; /^\n$/D; }')
  name=$(git -C "$SRC" log -1 --format=%an "$sha")
  email=$(git -C "$SRC" log -1 --format=%ae "$sha")
  when=$(git -C "$SRC" log -1 --format=%aI "$sha")

  git add -A
  GIT_AUTHOR_DATE="$when" GIT_COMMITTER_DATE="$when" \
    git -c user.name="$name" -c user.email="$email" \
    commit -q --allow-empty -m "$subject" -m "$body"
done

echo
git log --oneline
echo
echo "trailers left behind: $(git log --format=%b | grep -c '^Sgt-' || true)"
echo "sgt state left behind: $(git ls-files | grep -c '^\.sgt/' || true)"
