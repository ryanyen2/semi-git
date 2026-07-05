"""Render the feature-lane timeline as a self-contained, interactive HTML page.

The ASCII grid is hard to read; this makes the same data *inspectable* so you can judge
whether the clustering is good and decide where to push next. It reads ``out/timeline.json``
(produced by ``timeline.py``) and writes ``out/timeline.html`` — one file, no dependencies,
opens with ``file://``.

What it surfaces on purpose:
  - matrix: rows = commits (oldest -> HEAD), columns = feature lanes; a filled cell means
    that commit advanced that lane. A god-lane reads as a near-solid vertical stripe.
  - columns ordered by (dominant dir, birth) and colored by dir, so over-split siblings sit
    adjacent in the same hue — the "Code Map" / "Code Map Panel" defect pops out.
  - badges computed client-side: ⚠hub (touched in a large share of commits) and ⧉split
    (two adjacent lanes with near-identical commit sets).

    .venv/bin/python experiments/patch_clustering/render_html.py
"""

from __future__ import annotations

import json
from pathlib import Path

_OUT = Path(__file__).resolve().parent / "out"

_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>sgt — feature lanes over version history</title>
<style>
  :root { --bg:#0f1115; --panel:#171a21; --line:#262b36; --ink:#e6e9ef; --dim:#8b93a3; --accent:#7aa2f7; }
  * { box-sizing: border-box; }
  body { margin:0; background:var(--bg); color:var(--ink);
         font:13px/1.45 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; }
  header { padding:12px 16px; border-bottom:1px solid var(--line); position:sticky; top:0;
           background:var(--bg); z-index:20; }
  header h1 { margin:0 0 4px; font-size:15px; font-weight:600; }
  header .stats { color:var(--dim); font-size:12px; }
  header .stats b { color:var(--ink); font-weight:600; }
  .howto { color:var(--dim); font-size:11.5px; margin-top:6px; max-width:900px; }
  .howto code { color:var(--accent); }
  .controls { margin-top:8px; display:flex; gap:16px; align-items:center; font-size:12px; color:var(--dim); }
  .controls input[type=range] { vertical-align:middle; }
  .wrap { display:flex; align-items:flex-start; }
  .grid-scroll { overflow:auto; max-height:calc(100vh - 150px); flex:1; }
  table { border-collapse:collapse; }
  thead th { position:sticky; top:0; background:var(--bg); z-index:10; }
  .colhead { writing-mode:vertical-rl; transform:rotate(180deg); white-space:nowrap;
             font-size:10.5px; font-weight:500; padding:6px 0; height:150px; cursor:pointer;
             color:var(--dim); border-left:1px solid var(--line); }
  .colhead .sw { display:inline-block; width:8px; height:8px; border-radius:2px; margin-bottom:4px; }
  .colhead.sel { color:var(--ink); background:#1e2430; }
  .colhead .badge { font-size:9px; }
  th.corner { position:sticky; left:0; z-index:15; background:var(--bg); text-align:left;
              padding:0 8px; vertical-align:bottom; color:var(--dim); font-weight:500; }
  tbody td.meta { position:sticky; left:0; background:var(--bg); white-space:nowrap; padding:2px 8px;
                  border-right:1px solid var(--line); cursor:default; z-index:5; }
  tbody td.meta .sha { color:var(--dim); }
  tbody td.meta .subj { color:var(--ink); }
  tbody tr.dead td.meta .subj { color:#5b6272; font-style:italic; }
  tbody tr:hover td.meta { background:#1e2430; }
  td.cell { width:15px; height:15px; border-left:1px solid #1b1f28; border-top:1px solid #1b1f28;
            padding:0; }
  tr.hl td.cell { outline:0; }
  tr:hover td { background-color:rgba(122,162,247,.06); }
  .dot { width:100%; height:100%; border-radius:2px; }
  /* detail panel */
  aside { width:330px; flex:none; border-left:1px solid var(--line); padding:14px 14px 40px;
          height:calc(100vh - 150px); overflow:auto; background:var(--panel); }
  aside h2 { font-size:13px; margin:0 0 2px; }
  aside .why { color:var(--dim); font-style:italic; margin:0 0 10px; }
  aside .kv { color:var(--dim); font-size:12px; margin:2px 0; }
  aside .kv b { color:var(--ink); }
  aside .sec { margin-top:12px; border-top:1px solid var(--line); padding-top:10px; }
  aside .members { font-size:11px; color:var(--dim); white-space:pre-wrap; word-break:break-all; }
  aside .members .fn { color:var(--ink); }
  .flag { display:inline-block; font-size:10px; padding:1px 5px; border-radius:3px; margin-right:4px; }
  .flag.hub { background:#3a2a12; color:#f7c66b; }
  .flag.split { background:#2a1230; color:#e58bff; }
  .empty { color:var(--dim); padding:20px; }
  .legend-hint { font-size:11px; color:var(--dim); margin-bottom:6px; }
</style>
</head>
<body>
<header>
  <h1>sgt — feature lanes over version history</h1>
  <div class="stats" id="stats"></div>
  <div class="howto">
    Each <b>row</b> is a commit (oldest at top, HEAD at bottom). Each <b>column</b> is a feature
    lane — a cluster of code entities the pipeline decided belong together. A filled cell means
    <b>that commit advanced that lane</b>. Columns are grouped by folder and colored by it, so
    over-split siblings sit adjacent. Hover a row to read the commit; click a column header (or any
    cell) to inspect the lane's members in the panel. Watch for: a <span class="flag hub">⚠ hub</span>
    stripe filled in most rows (a god-lane that should be split/demoted) and
    <span class="flag split">⧉ split</span> pairs (two lanes that are really one feature).
  </div>
  <div class="controls">
    <label>min lane size: <input type="range" id="sizeSlider" min="4" max="40" value="4">
      <span id="sizeVal">4</span></label>
    <label>sort lanes:
      <select id="sortSel">
        <option value="dir">by folder (siblings adjacent)</option>
        <option value="size">by size</option>
        <option value="birth">by birth</option>
        <option value="activity">by activity</option>
      </select>
    </label>
    <span id="shownCount"></span>
  </div>
</header>
<div class="wrap">
  <div class="grid-scroll"><table id="grid"></table></div>
  <aside id="panel"><div class="empty">Hover a commit, or click a lane header / cell to inspect.</div></aside>
</div>
<script id="data" type="application/json">__DATA__</script>
<script>
const D = JSON.parse(document.getElementById('data').textContent);
const commits = D.commits;
const laneMap = D.lanes;
const commitLanes = {};
for (const [o, ls] of Object.entries(D.commit_lanes)) commitLanes[+o] = ls;

const allLanes = Object.keys(laneMap).filter(l => (laneMap[l].commits || []).length);
const nCommits = commits.length;
const HUB_FRAC = 0.40;
const SPLIT_JAC = 0.80;

function hueFor(s){ let h=0; for(const c of s) h=(h*31 + c.charCodeAt(0)) % 360; return h; }
const dirs = [...new Set(allLanes.map(l => laneMap[l].dir))];
const dirHue = Object.fromEntries(dirs.map(d => [d, hueFor(d)]));
function laneColor(l){ return `hsl(${dirHue[laneMap[l].dir]} 60% 58%)`; }

const laneCommits = {};
for (const l of allLanes) laneCommits[l] = new Set(laneMap[l].commits);
function jaccard(a, b){ let i=0; for (const x of a) if (b.has(x)) i++; return i / (a.size + b.size - i); }
function isHub(l){ return laneCommits[l].size >= HUB_FRAC * nCommits; }

const stats = document.getElementById('stats');
const covered = commits.filter(c => (commitLanes[c.order] || []).length).length;
stats.innerHTML = `<b>${allLanes.length}</b> feature lanes · <b>${nCommits}</b> commits · `
  + `coverage <b>${covered}/${nCommits}</b> · γ=${D.gamma} · ${D.cost}`;

let selLane = null;

function orderedLanes(){
  const min = +document.getElementById('sizeSlider').value;
  const sort = document.getElementById('sortSel').value;
  let ls = allLanes.filter(l => laneMap[l].size >= min);
  const cmp = {
    dir:      (a,b)=> laneMap[a].dir<laneMap[b].dir?-1:laneMap[a].dir>laneMap[b].dir?1:(laneMap[a].birth-laneMap[b].birth),
    size:     (a,b)=> laneMap[b].size - laneMap[a].size,
    birth:    (a,b)=> laneMap[a].birth - laneMap[b].birth,
    activity: (a,b)=> laneCommits[b].size - laneCommits[a].size,
  }[sort];
  return ls.sort(cmp);
}

function splitPartners(lanes){
  // mark lanes whose commit-set is near-identical to an adjacent (same-dir) lane
  const flag = new Set();
  for (let i=1;i<lanes.length;i++){
    const a=lanes[i-1], b=lanes[i];
    if (laneMap[a].dir===laneMap[b].dir && jaccard(laneCommits[a],laneCommits[b])>=SPLIT_JAC){
      flag.add(a); flag.add(b);
    }
  }
  return flag;
}

function render(){
  const lanes = orderedLanes();
  const splits = splitPartners(lanes);
  document.getElementById('shownCount').textContent = `showing ${lanes.length} lanes`;
  const laneIdx = Object.fromEntries(lanes.map((l,i)=>[l,i]));

  const tbl = document.getElementById('grid');
  tbl.innerHTML = '';

  // header
  const thead = document.createElement('thead');
  const hr = document.createElement('tr');
  const corner = document.createElement('th');
  corner.className = 'corner'; corner.textContent = 'commit ↓  /  lane →';
  hr.appendChild(corner);
  lanes.forEach(l => {
    const th = document.createElement('th');
    th.className = 'colhead' + (l===selLane?' sel':'');
    const badge = (isHub(l)?' ⚠':'') + (splits.has(l)?' ⧉':'');
    th.innerHTML = `<span class="sw" style="background:${laneColor(l)}"></span>`
      + `${laneMap[l].label}<span class="badge">${badge}</span>`;
    th.title = `${laneMap[l].label} — ${laneMap[l].dir} · ${laneMap[l].size} entities`;
    th.onclick = ()=>{ selLane = (selLane===l?null:l); showLane(l); render(); };
    hr.appendChild(th);
  });
  thead.appendChild(hr); tbl.appendChild(thead);

  // body
  const tb = document.createElement('tbody');
  commits.forEach(c => {
    const touched = new Set(commitLanes[c.order] || []);
    const tr = document.createElement('tr');
    if (!touched.size) tr.className = 'dead';
    const meta = document.createElement('td');
    meta.className = 'meta';
    const headTag = c.order===nCommits-1 ? ' <b style="color:#7aa2f7">HEAD</b>' : '';
    meta.innerHTML = `<span class="sha">${String(c.order).padStart(2,' ')} ${c.short}</span> `
      + `<span class="subj">${esc(c.subject).slice(0,52)}</span>${headTag}`;
    meta.onmouseenter = ()=> showCommit(c);
    tr.appendChild(meta);
    lanes.forEach(l => {
      const td = document.createElement('td');
      td.className = 'cell';
      if (touched.has(l)){
        const dot = document.createElement('div');
        dot.className='dot'; dot.style.background = laneColor(l);
        if (l===selLane) dot.style.boxShadow='0 0 0 2px #fff inset';
        td.appendChild(dot);
      }
      td.onclick = ()=>{ selLane=l; showCell(c,l); render(); };
      tr.appendChild(td);
    });
    tb.appendChild(tr);
  });
  tbl.appendChild(tb);
}

function esc(s){ return s.replace(/[&<>]/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[m])); }

function laneFlags(l){
  let s='';
  if (isHub(l)) s+=`<span class="flag hub">⚠ hub — touched in ${laneCommits[l].size}/${nCommits} commits</span>`;
  return s;
}

function showLane(l){
  const L = laneMap[l];
  const members = (L.members||[]).map(m=>{
    const p = m.split('::'); const fn = p.length>1?p[1]:m;
    return `<span class="fn">${esc(fn)}</span>  ${esc(p[0])}`;
  }).join('\\n');
  document.getElementById('panel').innerHTML =
    `<h2><span class="sw" style="display:inline-block;width:9px;height:9px;border-radius:2px;background:${laneColor(l)}"></span> ${esc(L.label)}</h2>`
    + `<p class="why">${esc(L.why||'')}</p>`
    + laneFlags(l)
    + `<div class="kv">folder: <b>${esc(L.dir)}</b></div>`
    + `<div class="kv">entities: <b>${L.size}</b></div>`
    + `<div class="kv">active: commits <b>${L.birth}</b> → <b>${L.last}</b> (${laneCommits[l].size} touches)</div>`
    + `<div class="sec"><div class="legend-hint">members (${(L.members||[]).length}) — judge coherence here:</div>`
    + `<div class="members">${members}</div></div>`;
}

function showCommit(c){
  const ls = (commitLanes[c.order]||[]);
  const list = ls.length
    ? ls.map(l=>`<div class="kv"><span class="sw" style="display:inline-block;width:8px;height:8px;border-radius:2px;background:${laneColor(l)}"></span> ${esc(laneMap[l].label)}</div>`).join('')
    : '<div class="kv">— no significant lane (churn that didn\\'t survive to HEAD, or docs)</div>';
  document.getElementById('panel').innerHTML =
    `<h2>commit ${c.order} · ${c.short}</h2>`
    + `<p class="why">${esc(c.subject)}</p>`
    + `<div class="sec"><div class="legend-hint">advances ${ls.length} lane(s):</div>${list}</div>`;
}

function showCell(c,l){ showLane(l); }

document.getElementById('sizeSlider').oninput = e => {
  document.getElementById('sizeVal').textContent = e.target.value; render();
};
document.getElementById('sortSel').onchange = render;
render();
</script>
</body>
</html>
"""


def main() -> None:
    data = json.loads((_OUT / "timeline.json").read_text(encoding="utf-8"))
    blob = json.dumps(data, separators=(",", ":")).replace("</", "<\\/")
    html = _TEMPLATE.replace("__DATA__", blob)
    dest = _OUT / "timeline.html"
    dest.write_text(html, encoding="utf-8")
    lanes = sum(1 for L in data["lanes"].values() if L.get("commits"))
    print(f"wrote {dest}  ({lanes} lanes × {len(data['commits'])} commits)")


if __name__ == "__main__":
    main()
