#!/usr/bin/env bash
# Build everything a participant downloads, then publish it.
#
#   scripts/publish-study.sh            build the four bundles, the site, deploy
#   scripts/publish-study.sh --site     the site only (no bundle rebuild)
#   scripts/publish-study.sh --dry-run  build everything, publish nothing
#
# This exists because `npm run build && firebase deploy` looks like it publishes
# the study and does not. `npm run build` builds the *website*; the four bundles
# are separate artefacts that the deploy merely copies out of
# `web/public/bundles/` because they happen to sit in the static directory. So
# changing sgt and deploying gets you an updated site serving the old tool, with
# nothing on screen to say so -- and the participant who downloads it runs a
# build nobody can name afterwards.
#
# One command that does both is the fix. `--site` is still there for a
# copy-change, but you have to ask for it.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SITE_ONLY=0
DRY_RUN=0
for arg in "$@"; do
    case "$arg" in
        --site) SITE_ONLY=1 ;;
        --dry-run) DRY_RUN=1 ;;
        -h|--help) sed -n '2,18p' "${BASH_SOURCE[0]}"; exit 0 ;;
        *) echo "unknown option: $arg" >&2; exit 2 ;;
    esac
done

cd "$ROOT"

if [ "$SITE_ONLY" -eq 0 ]; then
    # The wheel is built from the working tree, so a dirty tree ships code that
    # is not any commit. The bundle records it as `-dirty`, which is honest but
    # useless: "which build did participant 7 run" then has no answer.
    if [ -n "$(git status --porcelain -- sgt/ pyproject.toml)" ]; then
        echo "Refusing: sgt has uncommitted changes, so the wheel would not be any commit."
        echo "Commit first, or pass --site if you only meant to publish the website."
        exit 1
    fi

    echo "==> Building the four bundles."
    for spec in "git coursecraft" "sgt coursecraft" "git confplan" "sgt confplan"; do
        # Split explicitly: zsh does not word-split an unquoted expansion, so
        # `set -- $spec` passes one argument there and silently builds nothing.
        condition="${spec%% *}"
        project="${spec##* }"
        scripts/make-study-bundle.sh "$condition" "$project" | sed 's/^/    /'
    done
fi

echo "==> Building the site."
(cd web && npm run build)

echo
echo "Bundles now in web/public/bundles:"
ls -la web/public/bundles/*.tgz | awk '{printf "    %-42s %s\n", $NF, $5}'

if [ "$DRY_RUN" -eq 1 ]; then
    echo
    echo "--dry-run: nothing published."
    exit 0
fi

echo
echo "==> Publishing."
(cd web && firebase deploy --only hosting)

echo
echo "==> Checking what is actually being served."
ok=1
for name in study-coursecraft-a study-coursecraft-b study-confplan-a study-confplan-b; do
    local_size=$(wc -c < "web/public/bundles/$name.tgz" | tr -d ' ')
    served=$(curl -s -o /dev/null -w "%{http_code} %{size_download}" \
        "https://sem-git.web.app/bundles/$name.tgz")
    code="${served%% *}"; size="${served##* }"
    if [ "$code" = "200" ] && [ "$size" = "$local_size" ]; then
        printf "    %-24s ok (%s bytes)\n" "$name" "$size"
    else
        printf "    %-24s MISMATCH: served %s/%s bytes, local %s\n" \
            "$name" "$code" "$size" "$local_size"
        ok=0
    fi
done

[ "$ok" -eq 1 ] || { echo; echo "A bundle on the site is not the one just built."; exit 1; }
echo
echo "Published. Participants downloading now get exactly what was just built."
