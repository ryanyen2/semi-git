#!/usr/bin/env bash
# Render every frontier of the seedbank demo and say which ones changed pixels.
#
#   scripts/demo/render-frontiers.sh [<repo-dir>] [<out-dir>] [<from>] [<to>]
#
# With no range it sweeps every frontier. A range renders just those commit
# indices, which is what `build-seedbank.sh` uses to check the silent gap
# without paying for the whole sweep on every build.
#
# This is the measurement behind the demo's headline claim, and it exists as a
# script because the claim is not one you can make by looking. A feature's code
# lands, and whether it shows up on the page the demo opens by default is a
# question about the *data*, not about the diff -- the spike that started this
# work found a feature that was live and invisible for eleven positions because
# the default slice happened not to exercise it
# (docs/design/2026-08-26-live-render-timeline.md §7).
#
# So: fold each commit-index onto disk, run it, photograph it, and compare
# consecutive photographs byte for byte.
#
# WHY A FOLD DIRECTORY IS NOT A RUNNABLE DIRECTORY
#
# `node_modules` is gitignored, therefore `ignored` tier, therefore correctly
# absent from every fold (gap G2 in the plan). Each fold directory gets a
# symlink to the source repo's install rather than its own copy, which is what
# makes a thirteen-frontier sweep take a minute instead of ten.
#
# WHY HEADLESS CHROME AND NOT AN SSR RENDER
#
# An SSR render would miss anything that only exists once the CSS is applied,
# and the interesting silent episodes are exactly the ones where the DOM is the
# same and the question is whether anything moved. Chrome's screenshots are
# byte-deterministic on the same machine, so "identical pixels" is a real
# comparison and not a threshold to tune.
set -euo pipefail

repo="${1:-$HOME/repos/sgt-demo/seedbank}"
out="${2:-/tmp/seedbank-frontiers}"
repo="$(cd "$repo" && pwd)"
SGT="${SGT:-sgt}"
CHROME="${CHROME:-/Applications/Google Chrome.app/Contents/MacOS/Google Chrome}"
PORT="${PORT:-5311}"

[ -x "$CHROME" ] || { echo "no headless Chrome at $CHROME; set CHROME=<path>" >&2; exit 1; }
[ -d "$repo/node_modules" ] || { echo "$repo has no node_modules; npm ci there first" >&2; exit 1; }

rm -rf "$out"; mkdir -p "$out"
cd "$repo"
last="$(git rev-list --count HEAD)"
from="${3:-0}"
to="${4:-$((last - 1))}"

for idx in $(seq "$from" "$to"); do
    dir="$out/$idx"
    mkdir -p "$dir"
    "$SGT" advanced fold --at "$idx" --json > "$out/$idx.json" 2>/dev/null

    # Deletion is half of materializing: scrubbing backward has to remove the
    # files that left the fold, not leave them behind for the next render to
    # pick up. The directories are fresh here, but the same loop drives a
    # scrubber over a warm overlay, where it is the whole game.
    python3 - "$out/$idx.json" "$dir" <<'PY'
import json, pathlib, sys
blob = json.loads(pathlib.Path(sys.argv[1]).read_text())
root = pathlib.Path(sys.argv[2])
want = set()
for path, text in blob["files"].items():
    p = root / path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    want.add(p.resolve())
for p in root.rglob("*"):
    if p.is_file() and p.resolve() not in want and "node_modules" not in p.parts:
        p.unlink()
print(f"{len(blob['files'])} files, {blob['op_count']} ops", end="")
PY
    ln -sfn "$repo/node_modules" "$dir/node_modules"

    if [ -f "$dir/src/main.tsx" ]; then
        (cd "$dir" && ./node_modules/.bin/vite --port "$PORT" --strictPort >/dev/null 2>&1 &)
        # Probed on [::1], not localhost. Vite 8 binds IPv6-only by default, so a
        # curl to localhost resolves to 127.0.0.1 and never answers -- the loop then
        # spins out its forty tries and the screenshot races an unready server.
        for _ in $(seq 1 40); do curl -sf -o /dev/null "http://[::1]:$PORT/" && break; sleep 0.25; done
        # The dev server stamps its own absolute path into a `data-vite-dev-id`
        # attribute, so two identical apps served from two fold
        # directories differ by one line. Stripped, or every frontier reads as changed.
        "$CHROME" --headless=new --disable-gpu --hide-scrollbars --force-device-scale-factor=1 \
            --window-size=1280,1000 --virtual-time-budget=6000 \
            --screenshot="$out/shot-$idx.png" --dump-dom "http://localhost:$PORT/" 2>/dev/null \
            | sed 's/></>\n</g' | sed -E "s/\?t=[0-9]+//g; s#$out/[0-9]+/#FOLD/#g" > "$out/dom-$idx.html"
        pkill -f "vite --port $PORT" >/dev/null 2>&1 || true
        sleep 1
        printf ", %s dom lines" "$(wc -l < "$out/dom-$idx.html" | tr -d ' ')"
    else
        printf ", nothing to run yet"
    fi
    echo "   [$idx]"
done

echo
printf '%-5s %-52s %-10s %s\n' idx save dom-lines pixels
prev=""
for idx in $(seq "$from" "$to"); do
    subject="$(git log --format='%s' --reverse | sed -n "$((idx + 1))p")"
    if [ ! -f "$out/dom-$idx.html" ]; then
        printf '%-5s %-52s %-10s %s\n' "$idx" "${subject:0:50}" "-" "(no app)"
        continue
    fi
    lines="$(wc -l < "$out/dom-$idx.html" | tr -d ' ')"
    if [ -z "$prev" ]; then
        px="-"
    elif cmp -s "$out/shot-$prev.png" "$out/shot-$idx.png"; then
        px="IDENTICAL"
    else
        px="changed"
    fi
    printf '%-5s %-52s %-10s %s\n' "$idx" "${subject:0:50}" "$lines" "$px"
    prev="$idx"
done
echo
echo "frames in $out"
