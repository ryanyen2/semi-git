#!/usr/bin/env bash
# Turn a frontier sweep into one self-contained web page with a time slider.
#
#   scripts/demo/build-timeline-page.sh [<frames-dir>] [<out.html>] [<repo-dir>]
#
# `render-variolite-frontiers.sh` folds every frontier, replays the same story
# at each one, and leaves two photographs per frontier. This turns that pile of
# PNGs into the thing the demo is actually for: a slider you drag backward
# through the build while the tool loses its features on screen.
#
# WHY PRE-RENDERED FRAMES AND NOT TWELVE LIVE SERVERS
#
# Each frontier is a separate application that needs its own dev server, and a
# scrubber that boots one per drag step is a scrubber nobody can drag. The frames
# are real: every one is a fold of that commit, served and photographed after the
# same interaction. What the page gives up is interactivity inside the frame, and
# what it buys is a scrub that keeps up with a hand.
#
# The page carries its images inline, so it is one file that opens from disk with
# no server and nothing to install.
set -euo pipefail

frames="${1:-/tmp/variolite-story}"
out="${2:-/tmp/variolite-timeline.html}"
repo="${3:-$HOME/repos/sgt-demo/variolite}"
# The two lines of prose at the top of the page. They say what the reader is
# looking at, so they belong to the subject and not to this script: a page built
# from sketchpad frames that calls itself variolite is worse than no heading.
title="${TIMELINE_TITLE:-$(basename "$repo"), built one feature at a time}"
sub="${TIMELINE_SUB:-every frame is a fold of that commit, served and photographed}"
# Only the story renderer has two views per frame. A plain sweep has one, and
# the buttons are hidden when the second one is missing.
view_a="${TIMELINE_VIEW_A:-after the story}"
view_b="${TIMELINE_VIEW_B:-with a past run opened}"

[ -d "$frames" ] || { echo "no frames in $frames; run render-variolite-frontiers.sh first" >&2; exit 1; }

python3 - "$frames" "$out" "$repo" "$title" "$sub" "$view_a" "$view_b" <<'PY'
import base64, json, pathlib, subprocess, sys
from html import escape as html_escape

frames, out, repo = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2]), sys.argv[3]
title, sub, view_a, view_b = sys.argv[4], sys.argv[5], sys.argv[6], sys.argv[7]

subjects = subprocess.run(
    ["git", "-C", repo, "log", "--format=%s", "--reverse"],
    capture_output=True, text=True, check=True,
).stdout.splitlines()

def data_uri(path):
    return "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode()

steps = []
for idx, subject in enumerate(subjects):
    # Two renderers feed this page. `render-variolite-frontiers.sh` replays a story
    # and leaves two frames per frontier, shot-a and shot-b. `render-frontiers.sh`
    # just loads the page and leaves one, shot-<idx>. An app whose frontiers differ
    # on their own needs no story, so accept either.
    a = frames / f"shot-a-{idx}.png"
    if not a.is_file():
        a = frames / f"shot-{idx}.png"
    if not a.is_file():
        continue
    b = frames / f"shot-b-{idx}.png"
    log = frames / f"log-{idx}.txt"
    ran = ""
    if log.is_file():
        for line in log.read_text().splitlines():
            if line.startswith("eval ran "):
                ran = line[len("eval ran "):].strip('"')
    steps.append({
        "idx": idx,
        "subject": subject,
        "ran": ran,
        "a": data_uri(a),
        "b": data_uri(b) if b.is_file() else None,
    })

# Which steps moved the picture, so the slider can mark them.
for i, step in enumerate(steps):
    step["changed"] = i > 0 and (
        step["a"] != steps[i - 1]["a"] or step["b"] != steps[i - 1]["b"]
    )

html = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>__TITLE__</title>
<style>
  :root {
    --paper: #ffffff; --chrome: #f4f7f9; --ink: #14181c; --muted: #5d6b78;
    --line: #dde4ea; --blue: #1c6aa8; --orange: #c2650f;
    --sans: ui-sans-serif, -apple-system, "Segoe UI", Roboto, sans-serif;
    --mono: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace;
  }
  * { box-sizing: border-box; }
  body { margin: 0; background: var(--paper); color: var(--ink); font: 400 13px/1.5 var(--sans); }
  .wrap { max-width: 1320px; margin: 0 auto; padding: 22px 24px 48px; }
  h1 { margin: 0 0 2px; font-size: 17px; font-weight: 600; }
  .sub { margin: 0 0 20px; font-family: var(--mono); font-size: 11.5px; color: var(--muted); }
  .bar { display: flex; align-items: center; gap: 16px; padding: 12px 14px;
         background: var(--chrome); border: 1px solid var(--line); border-radius: 3px; }
  input[type=range] { flex: 1; accent-color: var(--blue); }
  .pos { font-family: var(--mono); font-size: 11px; color: var(--muted); white-space: nowrap; }
  .subject { margin: 14px 0 4px; font-size: 14px; font-weight: 600; }
  .meta { margin: 0 0 12px; font-family: var(--mono); font-size: 11px; color: var(--muted); }
  .meta .moved { color: var(--orange); }
  .meta .same { color: var(--muted); }
  .shot { width: 100%; display: block; border: 1px solid var(--line); border-radius: 3px; }
  .views { display: flex; gap: 8px; margin: 0 0 12px; }
  .views button { font: inherit; font-size: 11.5px; color: var(--muted); background: transparent;
                  border: 1px solid var(--line); border-radius: 3px; padding: 3px 10px; cursor: pointer; }
  .views button.on { color: var(--blue); border-color: #b9d8ef; background: #eaf4fb; }
  .ticks { display: flex; gap: 2px; margin-top: 10px; }
  .tick { height: 4px; flex: 1; border-radius: 1px; background: var(--line); }
  .tick.changed { background: var(--blue); }
  .tick.here { background: var(--orange); }
</style>
</head>
<body>
<div class="wrap">
  <h1>__TITLE__</h1>
  <p class="sub">__SUB__</p>
  <div class="bar">
    <span class="pos" id="pos"></span>
    <input type="range" id="slider" min="0" max="0" step="1">
    <span class="pos">drag to scrub</span>
  </div>
  <div class="ticks" id="ticks"></div>
  <p class="subject" id="subject"></p>
  <p class="meta" id="meta"></p>
  <div class="views" id="views">
    <button id="viewA" class="on">__VIEW_A__</button>
    <button id="viewB">__VIEW_B__</button>
  </div>
  <img class="shot" id="shot" alt="">
</div>
<script>
const STEPS = __STEPS__;
// A plain sweep photographs each frontier once, so there is nothing to switch
// between and the pair of buttons would be two dead controls on the page.
const views = document.getElementById('views');
if (views && !STEPS.some((step) => step.b)) views.style.display = 'none';
const slider = document.getElementById('slider');
const shot = document.getElementById('shot');
const subject = document.getElementById('subject');
const meta = document.getElementById('meta');
const pos = document.getElementById('pos');
const ticks = document.getElementById('ticks');
const viewA = document.getElementById('viewA');
const viewB = document.getElementById('viewB');
let which = 'a';

slider.max = String(STEPS.length - 1);
slider.value = String(STEPS.length - 1);

STEPS.forEach(() => {
  const t = document.createElement('div');
  t.className = 'tick';
  ticks.appendChild(t);
});

function draw() {
  const step = STEPS[Number(slider.value)];
  shot.src = which === 'b' && step.b ? step.b : step.a;
  shot.alt = __TITLE_JSON__ + ', save ' + step.idx;
  subject.textContent = step.subject;
  const moved = step.changed
    ? '<span class="moved">the picture moved here</span>'
    : '<span class="same">same picture as the save before it</span>';
  meta.innerHTML = 'save ' + step.idx + (step.ran ? ' &nbsp; ran: ' + step.ran : '') + ' &nbsp; ' + moved;
  pos.textContent = step.idx + ' of ' + (STEPS.length - 1);
  [...ticks.children].forEach((t, i) => {
    t.className = 'tick' + (STEPS[i].changed ? ' changed' : '') +
      (i === Number(slider.value) ? ' here' : '');
  });
  viewA.className = which === 'a' ? 'on' : '';
  viewB.className = which === 'b' ? 'on' : '';
}

slider.addEventListener('input', draw);
viewA.addEventListener('click', () => { which = 'a'; draw(); });
viewB.addEventListener('click', () => { which = 'b'; draw(); });
addEventListener('keydown', (e) => {
  if (e.key === 'ArrowLeft') slider.value = String(Math.max(0, Number(slider.value) - 1));
  if (e.key === 'ArrowRight') slider.value = String(Math.min(STEPS.length - 1, Number(slider.value) + 1));
  if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') draw();
});
draw();
</script>
</body>
</html>
"""

html = (html
        .replace("__TITLE_JSON__", json.dumps(title))
        .replace("__TITLE__", html_escape(title))
        .replace("__SUB__", html_escape(sub))
        .replace("__VIEW_A__", html_escape(view_a))
        .replace("__VIEW_B__", html_escape(view_b)))
out.write_text(html.replace("__STEPS__", json.dumps(steps)))
size = out.stat().st_size / 1_000_000
print(f"{len(steps)} frontiers -> {out} ({size:.1f} MB)")
PY
