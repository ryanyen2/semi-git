#!/usr/bin/env bash
# Walk every packed bundle the way a participant does, and fail on what a
# participant would hit.
#
#   scripts/study/verify-bundles.sh [bundle-dir]
#
# The bundles are the artefact. Everything else in this repo can be green while
# the thing somebody downloads cannot complete a stage -- that is exactly what
# happened on 2026-09-01 (ledger F134-F137), and the pre-ship rehearsal missed it
# because it ran against a working tree rather than against the tarball.
#
# So this unpacks each `.tgz`, builds the environment `install/setup.sh` would,
# and walks stages 0-4 asserting the ABSENCE of each known symptom. It does not
# talk to the study server and needs no key: the record ships mined and frozen in
# `.study/sgt-pristine.tar`, so every read and both kernel verbs are deterministic.
#
# Run it after any bundle rebuild, before `scripts/publish-study.sh`.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BUNDLES="${1:-$ROOT/web/public/bundles}"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/verify-bundles.XXXXXX")"
trap 'rm -rf "$WORK"' EXIT

fails=0
ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
bad()  { printf '  \033[31m✗\033[0m %s\n' "$*"; fails=$((fails + 1)); }
check() { if [ "$1" = 0 ]; then ok "$2"; else bad "$2${3:+ — $3}"; fi; }

# The name this project gives the work stages 3 and 4 are about, read out of the
# shipped record exactly as `./stage 3` reads it -- never written here, because a
# name written in two places is a name that can disagree with itself.
theme_label() {
    python3 - "$1" <<'PY' 2>/dev/null
import json, pathlib, sys
squash = lambda t: "".join(c for c in t.casefold() if c.isalnum())
p = pathlib.Path(sys.argv[1]) / ".sgt/intent/themes.json"
if p.exists():
    for v in json.loads(p.read_text()).get("data", {}).values():
        if isinstance(v, dict) and "eventday" in squash(v.get("label", "")):
            print(v["label"])
            break
PY
}

# `./check N`'s verdict, without its colours.
checks_match() {
    ( cd "$1" && ./check "$2" 2>&1 ) | sed 's/\x1b\[[0-9;]*m//g' | grep -q "those match"
}

for tgz in "$BUNDLES"/study-*.tgz; do
    name="$(basename "$tgz" .tgz)"
    echo
    echo "=== $name"
    dest="$WORK/$name"
    mkdir -p "$dest"
    tar xzf "$tgz" -C "$dest" --strip-components=1 || { bad "could not unpack"; continue; }
    w="$dest/work"

    # What setup.sh builds, minus the parts that need the study server.
    ( cd "$w" && uv venv -q --clear -p 3.12 && uv pip install -q -p .venv/bin/python pytest ) \
        >/dev/null 2>&1 || { bad "could not build the project environment"; continue; }
    ( cd "$dest" && uv venv -q --clear -p 3.12 toolenv \
        && uv pip install -q -p toolenv/bin/python install/semi_git-*.whl ) >/dev/null 2>&1
    sgt_bin="$dest/toolenv/bin/sgt"
    arm=git; [ -x "$sgt_bin" ] && [ -d "$w/.sgt" ] && arm=sgt

    # F134: the key setup writes here, and the interpreter every command runs
    # through, must both survive a reset -- and every stage begins with one.
    printf 'OPENAI_API_KEY=verify-bundles-placeholder\nSGT_MODEL=gpt-5.6-luna\n' > "$w/.env"
    export PATH="$dest/toolenv/bin:$PATH"
    for n in 0 1 2 3 4; do
        ( cd "$w" && ./stage "$n" ) >/dev/null 2>&1
        check $? "stage $n runs"
        [ -f "$w/.env" ] || { bad "stage $n deleted .env"; break; }
        [ -d "$w/.venv" ] || { bad "stage $n deleted .venv"; break; }
    done
    [ -f "$w/.env" ] && ok ".env survives every stage"
    [ -d "$w/.venv" ] && ok ".venv survives every stage"

    if [ "$arm" != sgt ]; then
        ( cd "$w" && ./stage 3 ) >/dev/null 2>&1
        ( cd "$w" && ./check 3 ) >/dev/null 2>&1
        check $? "check 3 runs"
        continue
    fi

    label="$(theme_label "$w")"
    [ -n "$label" ] && ok "the work stages 3 and 4 name reads back: $label" \
                    || bad "no Event Day theme in the shipped record"
    [ -n "$label" ] || continue

    # F135: the wrong verb for this stage is where a tool says what it is for.
    ( cd "$w" && ./stage 3 ) >/dev/null 2>&1
    out="$( cd "$w" && "$sgt_bin" restore "$label" 2>&1 )"
    rc=$?
    case "$out" in
        *"nothing to restore"*) [ "$rc" = 0 ] && ok "restore before a removal says there is nothing to restore" \
                                              || bad "restore says the right thing but exits $rc" ;;
        *"two live versions"*)  bad "restore refuses with the kernel's fork message (F135)" ;;
        *)                      bad "restore before a removal said: ${out%%$'\n'*}" ;;
    esac

    # The stage-3 task itself, and its verdict.
    ( cd "$w" && "$sgt_bin" revert "$label" --yes ) >/dev/null 2>&1
    check $? "revert '$label' applies"
    checks_match "$w" 3 && ok "check 3: the averages count every day again" \
                        || bad "check 3 does not match after the revert"

    # Stage 4 sets its own removed state up; the task is to reverse it.
    ( cd "$w" && ./stage 4 ) >/dev/null 2>&1
    check $? "stage 4 sets up the removed state"
    ( cd "$w" && "$sgt_bin" restore "$label" --yes ) >/dev/null 2>&1
    check $? "restore '$label' applies"
    checks_match "$w" 4 && ok "check 4: the page reads what it read before" \
                        || bad "check 4 does not match after the restore"

    # The half of this arm that is not the CLI. `render-bundle.js` drives the shipped
    # `workbench.js` off THIS bundle's own views -- the panel that failed a participant was one
    # the extension's own fixture never exercised, so the fixture could not have caught it.
    ( cd "$w" && ./stage 1 ) >/dev/null 2>&1
    # The webview THIS bundle ships, out of its own .vsix -- not the repo's copy, which is the same
    # file right up until somebody edits it after a build, and that is exactly the state where a
    # gate reading the repo would call the artefact fine.
    shipped=""
    if [ -f "$dest/install/semi-git.vsix" ] && command -v unzip >/dev/null 2>&1; then
        unzip -o -q "$dest/install/semi-git.vsix" "extension/media/workbench.js" -d "$dest/vsx" \
            && shipped="$dest/vsx/extension/media/workbench.js"
    fi
    [ -n "$shipped" ] && ok "the bundle ships its own workbench.js" \
                      || bad "no workbench.js in the bundle's .vsix — rendering the repo's copy"
    if command -v node >/dev/null 2>&1; then
        node "$ROOT/editor/vscode/dev/render-bundle.js" "$w" "$sgt_bin" "$shipped" 2>&1 | sed 's/^/  /'
        [ "${PIPESTATUS[0]}" = 0 ] || fails=$((fails + 1))
    else
        bad "node is not available, so the workbench was not rendered"
    fi
done

echo
if [ "$fails" = 0 ]; then
    echo "All bundles walk stages 0-4."
else
    echo "$fails check(s) failed — do not publish."
fi
exit $((fails > 0))
