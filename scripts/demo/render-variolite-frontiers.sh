#!/usr/bin/env bash
# Render every frontier of the Variolite build, driving the same interaction at
# each one, and say which frontiers changed pixels.
#
#   scripts/demo/render-variolite-frontiers.sh [<repo-dir>] [<out-dir>] [<from>] [<to>]
#
# WHY THIS EXISTS ALONGSIDE render-frontiers.sh
#
# `render-frontiers.sh` photographs each frontier as it loads. That works for the
# seedbank, whose features are all on screen the moment the page opens: a sort
# bar, a stock badge, a star on every card. Run it against the Variolite build
# and eight consecutive frontiers come back IDENTICAL, while the DOM grows from
# 600 lines to 830. The code is landing and the picture is not moving, because
# every feature after the first two is an interaction. A version tab is invisible
# until a box exists. A box is invisible until someone wraps one.
#
# So this script replays a short user story at every frontier before it takes the
# photograph: wrap the matcher in a variant box, add a version to it, run the
# file. Each step is written to do nothing when the frontier has no such control,
# which is the point -- the difference between two frontiers is exactly how much
# of the story the tool could still perform.
#
# The story is deliberately the paper's own: Ellen wraps matchString(), branches
# it, names the branch, nests a box inside it, runs to compare, and then searches
# her own history for the run that printed 7/8. If a future frontier breaks it,
# the demo is broken.
#
# The search is last because it is the only step whose result hides an earlier
# one. A live query replaces the run list with its matches, so the second frame,
# which opens a past run by clicking a row, clears the query before it looks.
#
# It ends on a run, and that ordering is load-bearing. The clock shows a box's
# last *recorded* state, so opening it after an edit that has not been run yet
# displays the box as it was before that edit -- correct behaviour that makes the
# nested box vanish from the frame it was created for.
set -euo pipefail

repo="${1:-$HOME/repos/sgt-demo/variolite}"
out="${2:-/tmp/variolite-frontiers}"
repo="$(cd "$repo" && pwd)"
here="$(cd "$(dirname "$0")" && pwd)"
SGT="${SGT:-sgt}"
PORT="${PORT:-5312}"

[ -d "$repo/node_modules" ] || { echo "$repo has no node_modules; npm install there first" >&2; exit 1; }

rm -rf "$out"; mkdir -p "$out"
cd "$repo"
last="$(git rev-list --count HEAD)"
from="${3:-0}"
to="${4:-$((last - 1))}"

# The story, as a plan the driver understands. Every step guards itself, so a
# frontier that has no variant boxes yet runs the same plan and simply produces
# less.
plan="$out/story.json"
cat > "$plan" <<'JSON'
[
 {
  "eval": "(() => { const lines=[...document.querySelectorAll('.cm-line')]; const s=lines.findIndex(l=>l.textContent.startsWith('function matchString')); if (s<0) return null; const e=lines.findIndex((l,i)=>i>s && l.textContent.trim()==='}'); if (e<0) return null; const a=lines[s].getBoundingClientRect(); const b=lines[e].getBoundingClientRect(); return {sx:a.left+4, sy:a.top+a.height/2, ex:b.right-2, ey:b.top+b.height/2}; })()",
  "as": "sel"
 },
 {
  "mouse": "down",
  "x": "$sel.sx",
  "y": "$sel.sy"
 },
 {
  "mouse": "move",
  "x": "$sel.ex",
  "y": "$sel.ey"
 },
 {
  "mouse": "up",
  "x": "$sel.ex",
  "y": "$sel.ey"
 },
 {
  "mouse": "click",
  "x": "$sel.ex",
  "y": "$sel.ey",
  "button": "right"
 },
 {
  "wait": 250
 },
 {
  "eval": "(() => { const el=document.querySelector('.menu-item'); if (!el) return 'absent'; el.click(); return 'clicked' })()",
  "as": "wrapped_sel"
 },
 {
  "wait": 350
 },
 {
  "eval": "(() => { const el=document.querySelector('.tab-add'); if (!el) return 'absent'; el.click(); return 'clicked' })()",
  "as": "added"
 },
 {
  "wait": 350
 },
 {
  "eval": "(() => { const t=[...document.querySelectorAll('.box .tab')]; if (t.length<2) return null; const r=t[1].getBoundingClientRect(); return {x:r.left+r.width/2, y:r.top+r.height/2}; })()",
  "as": "newtab"
 },
 {
  "mouse": "click",
  "x": "$newtab.x",
  "y": "$newtab.y",
  "clicks": 2
 },
 {
  "wait": 250
 },
 {
  "eval": "(() => { const i=document.querySelector('.tab-input'); if (!i) return 'absent'; const set=Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value').set; set.call(i, 'fuzzy match'); i.dispatchEvent(new Event('input', {bubbles:true})); i.blur(); return 'renamed' })()",
  "as": "renamed"
 },
 {
  "wait": 300
 },
 {
  "eval": "(() => { const lines=[...document.querySelectorAll('.box .cm-line')]; const s=lines.findIndex(l=>l.textContent.includes('const left')); const e=lines.findIndex(l=>l.textContent.includes('const right')); if (s<0||e<0) return null; const a=lines[s].getBoundingClientRect(); const b=lines[e].getBoundingClientRect(); return {sx:a.left+4, sy:a.top+a.height/2, ex:b.right-2, ey:b.top+b.height/2}; })()",
  "as": "inner"
 },
 {
  "mouse": "down",
  "x": "$inner.sx",
  "y": "$inner.sy"
 },
 {
  "mouse": "move",
  "x": "$inner.ex",
  "y": "$inner.ey"
 },
 {
  "mouse": "up",
  "x": "$inner.ex",
  "y": "$inner.ey"
 },
 {
  "mouse": "click",
  "x": "$inner.ex",
  "y": "$inner.ey",
  "button": "right"
 },
 {
  "wait": 250
 },
 {
  "eval": "(() => { const el=document.querySelector('.menu-item'); if (!el) return 'absent'; el.click(); return 'clicked' })()",
  "as": "wrapped_inner"
 },
 {
  "wait": 350
 },
 {
  "eval": "(async () => { const b=document.querySelector('.run-btn'); if (!b) return 'absent'; b.click(); for (let i=0;i<60;i++){ await new Promise(r=>setTimeout(r,100)); const l=document.querySelector('.output .line'); if (l) return l.textContent } return 'no output' })()",
  "as": "ran"
 },
 {
  "wait": 200
 },
 {
  "eval": "(() => { const t=[...document.querySelectorAll('.box .tab')]; if (t.length<1) return 'absent'; t[0].click(); return t[0].textContent })()",
  "as": "tab_first"
 },
 {
  "wait": 300
 },
 {
  "eval": "(async () => { const b=document.querySelector('.run-btn'); if (!b) return 'absent'; b.click(); for (let i=0;i<60;i++){ await new Promise(r=>setTimeout(r,100)); const l=document.querySelector('.output .line'); if (l) return l.textContent } return 'no output' })()",
  "as": "ran_other"
 },
 {
  "wait": 200
 },
 {
  "eval": "(() => { const t=[...document.querySelectorAll('.box .tab')]; if (t.length<2) return 'absent'; t[1].click(); return t[1].textContent })()",
  "as": "tab_back"
 },
 {
  "wait": 300
 },
 {
  "eval": "(async () => { const b=document.querySelector('.run-btn'); if (!b) return 'absent'; b.click(); for (let i=0;i<60;i++){ await new Promise(r=>setTimeout(r,100)); const l=document.querySelector('.output .line'); if (l) return l.textContent } return 'no output' })()",
  "as": "ran_last"
 },
 {
  "wait": 200
 },
 {
  "eval": "(() => { const el=document.querySelector('.box-clock'); if (!el) return 'absent'; el.click(); return 'clicked' })()",
  "as": "clock"
 },
 {
  "wait": 300
 },
 {
  "mouse": "click",
  "x": 640,
  "y": 790
 },
 {
  "wait": 250
 },
 {
  "eval": "(() => { const i = document.querySelector('.find'); if (!i) return 'no search pane'; const set = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set; set.call(i, '7/8'); i.dispatchEvent(new Event('input', {bubbles: true})); return 'searched 7/8' })()",
  "as": "searched"
 },
 {
  "wait": 350
 },
 {
  "eval": "(() => { let n=0; for (const el of document.querySelectorAll('.elapsed')) { el.textContent='-- ms'; n++ } for (const el of document.querySelectorAll('.run-when, .box-when, .past-what, .hit-where')) { el.textContent='--:--:--'; n++ } return n })()",
  "as": "masked"
 }
]
JSON

plan_past="$out/story-past.json"
cat > "$plan_past" <<'JSON'
[
 {
  "eval": "(() => { const i = document.querySelector('.find'); if (!i || !i.value) return 'nothing to clear'; const set = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set; set.call(i, ''); i.dispatchEvent(new Event('input', {bubbles: true})); return 'cleared' })()",
  "as": "cleared"
 },
 {
  "wait": 250
 },
 {
  "eval": "(() => { const rows=[...document.querySelectorAll('.run-row')]; if (rows.length<2) return null; const r=rows[rows.length-1].getBoundingClientRect(); return {x:r.left+r.width/2, y:r.top+r.height/2}; })()",
  "as": "oldrow"
 },
 {
  "mouse": "click",
  "x": "$oldrow.x",
  "y": "$oldrow.y",
  "clicks": 2
 },
 {
  "wait": 450
 },
 {
  "eval": "(() => { let n=0; for (const el of document.querySelectorAll('.elapsed')) { el.textContent='-- ms'; n++ } for (const el of document.querySelectorAll('.run-when, .box-when, .past-what')) { el.textContent='--:--:--'; n++ } return n })()",
  "as": "masked2"
 }
]
JSON

# The last step blanks the elapsed time and the wall clock before the shot. Both
# are in the frame and both change between two runs of identical code, so without
# it a one-millisecond difference reads as a changed frontier and the IDENTICAL
# column stops meaning anything. Nothing else in the page is nondeterministic:
# the fold is byte-identical, and Chrome's rendering is stable on one machine.

for idx in $(seq "$from" "$to"); do
    dir="$out/$idx"
    mkdir -p "$dir"
    "$SGT" advanced fold --at "$idx" --json > "$out/$idx.json" 2>/dev/null

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
        # Vite 8 binds IPv6-only by default, so probe [::1] rather than localhost.
        for _ in $(seq 1 40); do curl -sf -o /dev/null "http://[::1]:$PORT/" && break; sleep 0.25; done
        # One plan step per line of the story, then the shot. The driver appends
        # its own screenshot step so the plan itself stays about the story.
        # Two frames per frontier. The first is the tool after the story has been
        # played as far as it goes. The second is the same tool with an older run
        # reopened, because putting the code back into a past state is a feature
        # whose whole visible effect is a state the first frame cannot be in.
        python3 - "$plan" "$plan_past" "$out/shot-a-$idx.png" "$out/shot-b-$idx.png" \
            > "$out/plan-$idx.json" <<'PY'
import json, pathlib, sys
story = json.loads(pathlib.Path(sys.argv[1]).read_text())
past = json.loads(pathlib.Path(sys.argv[2]).read_text())
print(json.dumps(story + [{"shot": sys.argv[3]}] + past + [{"shot": sys.argv[4]}]))
PY
        node "$here/drive-page.mjs" "http://localhost:$PORT/" "$out/plan-$idx.json" \
            > "$out/log-$idx.txt" 2>&1 || true
        pkill -f "vite --port $PORT" >/dev/null 2>&1 || true
        sleep 1
        printf ", ran: %s" "$(sed -n 's/^eval ran //p' "$out/log-$idx.txt" | tail -1)"
    else
        printf ", nothing to run yet"
    fi
    echo "   [$idx]"
done

echo
printf '%-5s %-52s %-28s %s\n' idx save "story got to" pixels
prev=""
for idx in $(seq "$from" "$to"); do
    subject="$(git log --format='%s' --reverse | sed -n "$((idx + 1))p")"
    if [ ! -f "$out/shot-a-$idx.png" ]; then
        printf '%-5s %-52s %-28s %s\n' "$idx" "${subject:0:50}" "-" "(no app)"
        continue
    fi
    ran="$(sed -n 's/^eval ran //p' "$out/log-$idx.txt" | tail -1 | tr -d '"')"
    if [ -z "$prev" ]; then px="-"
    elif cmp -s "$out/shot-a-$prev.png" "$out/shot-a-$idx.png" \
      && cmp -s "$out/shot-b-$prev.png" "$out/shot-b-$idx.png"; then px="IDENTICAL"
    else px="changed"; fi
    printf '%-5s %-52s %-28s %s\n' "$idx" "${subject:0:50}" "${ran:0:26}" "$px"
    prev="$idx"
done

echo
echo "frames in $out"
