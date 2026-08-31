// The overlay itself. Injected by sgt-overlay.mjs; never part of the demo's own source.
//
// Two directions, one join:
//   forward  — hover a feature in the rail, its regions light up in that feature's identity hue
//   backward — hover the page, a chip names the symbol and the feature that last changed it
//
// The design brief was "let the developer stay in the flow": no dialog, no dashboard, nothing
// that has to be dismissed. So the rail is a column of hairlines that only becomes words when
// the pointer is near it, and the page is never modified -- only outlined and dimmed, both of
// which are removed on mouseout. Press ` to hide the whole thing for a clean take.

const data = JSON.parse(document.getElementById("sgt-blame")?.textContent || '{"features":{},"files":{}}');
const FEATURES = data.features;
const FILES = data.files;

function boot() {
  // ---- the join ---------------------------------------------------------------------------
  // Innermost span wins: spans nest (a method inside a class), and the tightest one is the
  // truthful owner. Same rule `_innermost_owner` uses in the entity graph.
  const ownerCache = new Map();
  function ownerOf(loc) {
    if (ownerCache.has(loc)) return ownerCache.get(loc);
    const cut = loc.lastIndexOf(":");
    const file = loc.slice(0, cut), line = Number(loc.slice(cut + 1));
    let best = null;
    for (const [a, b, fid, symbol] of FILES[file] || []) {
      if (a <= line && line <= b && (best === null || a > best[0])) best = [a, fid, symbol];
    }
    const out = best ? { fid: best[1], symbol: best[2] } : null;
    ownerCache.set(loc, out);
    return out;
  }

  // Only the OUTERMOST match is lit. Lighting every descendant stacked a translucent tint on
  // itself a dozen levels deep and turned the page into one flat wash.
  function elementsFor(symbol) {
    const all = [];
    for (const el of document.querySelectorAll("[data-sgt-loc]")) {
      const o = ownerOf(el.getAttribute("data-sgt-loc"));
      if (o && o.symbol === symbol) all.push(el);
    }
    return all.filter((el) => !all.some((other) => other !== el && other.contains(el)));
  }

  // ---- styles -----------------------------------------------------------------------------
  const style = document.createElement("style");
  style.textContent = `
  .sgt-rail { position: fixed; left: 0; top: 0; bottom: 0; width: 10px; overflow-y: auto; z-index: 2147483000;
    display: flex; flex-direction: column; gap: 2px; padding: 10px 0;
    transition: width 160ms cubic-bezier(.2,.7,.3,1), background 160ms ease; }
  .sgt-rail:hover, .sgt-rail.sgt-open { width: 216px; background: #ffffff;
    box-shadow: 0 0 0 1px rgba(20,24,28,.08), 6px 0 24px rgba(20,24,28,.07); }
  /* Push the page over instead of covering it: a lit region must never sit under the rail. */
  body { transition: padding-left 160ms cubic-bezier(.2,.7,.3,1); }
  body.sgt-shift { padding-left: 216px; }
  .sgt-row { display: flex; align-items: center; gap: 8px; height: 28px; cursor: default;
    padding-left: 0; transition: padding-left 160ms cubic-bezier(.2,.7,.3,1); flex: 0 0 auto; }
  .sgt-rail:hover .sgt-row, .sgt-rail.sgt-open .sgt-row { padding-left: 10px; }
  .sgt-swatch { width: 4px; height: 100%; border-radius: 2px; flex: 0 0 auto;
    transition: width 160ms ease, transform 160ms ease; }
  .sgt-rail:hover .sgt-swatch, .sgt-rail.sgt-open .sgt-swatch { width: 6px; }
  .sgt-row:hover .sgt-swatch { transform: scaleX(1.6); }
  .sgt-label { display: flex; flex-direction: column; gap: 1px; min-width: 0;
    font: 500 11px/1.15 "Avenir Next",Avenir,ui-sans-serif,-apple-system,sans-serif; color: #14181c;
    white-space: nowrap; overflow: hidden; opacity: 0; transition: opacity 130ms ease 40ms; }
  .sgt-label b { font-weight: 600; overflow: hidden; text-overflow: ellipsis; }
  .sgt-feat { font-weight: 400; font-size: 9.5px; color: #5d6b78;
    overflow: hidden; text-overflow: ellipsis; }
  .sgt-rail:hover .sgt-label, .sgt-rail.sgt-open .sgt-label { opacity: 1; }
  .sgt-count { font: 400 10px/1 ui-monospace,SFMono-Regular,monospace; color: #5d6b78;
    margin-left: auto; padding-right: 12px; opacity: 0; transition: opacity 130ms ease 40ms; }
  .sgt-rail:hover .sgt-count, .sgt-rail.sgt-open .sgt-count { opacity: 1; }

  /* Focus: the page is never edited, only outlined and dimmed -- both removed on mouseout. */
  .sgt-dim [data-sgt-loc] { transition: opacity 140ms ease; }
  .sgt-dim .sgt-faded { opacity: .3; }
  .sgt-lit { outline: 2px solid var(--sgt-hue); outline-offset: 2px; border-radius: 4px;
    box-shadow: 0 0 0 6px color-mix(in srgb, var(--sgt-hue) 14%, transparent);
    transition: outline-color 140ms ease, box-shadow 140ms ease; }

  .sgt-chip { position: fixed; z-index: 2147483001; pointer-events: none;
    font: 500 11px/1.35 "Avenir Next",Avenir,ui-sans-serif,-apple-system,sans-serif;
    background: rgba(20,24,28,.94); color: #f2f6f9; padding: 6px 9px; border-radius: 6px;
    box-shadow: 0 4px 14px rgba(0,0,0,.22); max-width: 320px;
    opacity: 0; transform: translateY(3px); transition: opacity 110ms ease, transform 110ms ease; }
  .sgt-chip.on { opacity: 1; transform: translateY(0); }
  .sgt-chip .k { color: #a6b3bf; font-weight: 400; }
  .sgt-chip .s { font-family: ui-monospace,SFMono-Regular,monospace; font-size: 10.5px; }
  .sgt-hidden { display: none !important; }`;
  document.head.appendChild(style);

  // ---- the rail ---------------------------------------------------------------------------
  const rail = document.createElement("div");
  rail.className = "sgt-rail";
  rail.addEventListener("mouseenter", () => document.body.classList.add("sgt-shift"));
  rail.addEventListener("mouseleave", () => {
    if (!rail.classList.contains("sgt-open")) document.body.classList.remove("sgt-shift");
  });

  // The rail lists SYMBOLS, coloured by the feature that owns them -- not features directly.
  //
  // Listing features was the obvious thing and it was wrong. sgt attributes a symbol, and blame
  // reports that symbol's tip op; `Card` is one 68-line symbol the seed tray touched last, so a
  // feature-level highlight lit every card, the header and the title, and read as "the seed tray
  // built this whole app". A symbol is the unit the data actually supports: hovering `TrayButton`
  // lights exactly the twenty-four stars, and hovering `Card` lights the cards, which is true.
  const counts = new Map();  // symbol -> {n, fid}
  for (const el of document.querySelectorAll("[data-sgt-loc]")) {
    const o = ownerOf(el.getAttribute("data-sgt-loc"));
    if (!o) continue;
    const key = o.symbol || "?";
    const cur = counts.get(key) || { n: 0, fid: o.fid };
    cur.n += 1;
    counts.set(key, cur);
  }
  const ordered = [...counts.entries()].sort((x, y) => y[1].n - x[1].n);

  let pinned = null;
  function light(symbol) {
    const rec = counts.get(symbol);
    if (!rec) return;
    const els = elementsFor(symbol);
    document.body.classList.add("sgt-dim");
    for (const el of els) {
      el.style.setProperty("--sgt-hue", FEATURES[rec.fid].color);
      el.classList.add("sgt-lit");
    }
    // An ANCESTOR of a lit element must never be faded. `opacity` composites down the tree, so a
    // faded `Card` dims the very star inside it that is supposed to be glowing -- the first
    // version of this lit all 24 buttons correctly and they were invisible anyway. Skip anything
    // that is lit, inside something lit, or CONTAINS something lit; everything else fades.
    for (const el of document.querySelectorAll("[data-sgt-loc]")) {
      const touches = els.some((lit) => lit === el || lit.contains(el) || el.contains(lit));
      if (!touches) el.classList.add("sgt-faded");
    }
  }
  function unlight() {
    document.body.classList.remove("sgt-dim");
    for (const el of document.querySelectorAll("[data-sgt-loc]")) {
      el.classList.remove("sgt-lit", "sgt-faded");
      el.style.removeProperty("--sgt-hue");
    }
  }

  const esc = (t) => String(t).replace(/[<>&]/g, "");
  for (const [symbol, rec] of ordered) {
    const f = FEATURES[rec.fid];
    const leaf = symbol.includes("::") ? symbol.split("::").pop() : symbol;
    const row = document.createElement("div");
    row.className = "sgt-row";
    row.title = `${symbol} - ${f.label}`;
    row.innerHTML =
      `<span class="sgt-swatch" style="background:${f.color}"></span>` +
      `<span class="sgt-label"><b>${esc(leaf)}</b><span class="sgt-feat">${esc(f.label)}</span></span>` +
      `<span class="sgt-count">${rec.n}</span>`;
    row.addEventListener("mouseenter", () => { if (!pinned) light(symbol); });
    row.addEventListener("mouseleave", () => { if (!pinned) unlight(); });
    row.addEventListener("click", () => {
      if (pinned === symbol) { pinned = null; unlight(); }
      else { pinned = symbol; unlight(); light(symbol); }
    });
    rail.appendChild(row);
  }
  document.body.appendChild(rail);

  // ---- the chip: pixels -> symbol -> feature ----------------------------------------------
  const chip = document.createElement("div");
  chip.className = "sgt-chip";
  document.body.appendChild(chip);

  let raf = 0;
  document.addEventListener("mousemove", (e) => {
    if (raf) return;
    raf = requestAnimationFrame(() => {
      raf = 0;
      if (rail.contains(e.target)) { chip.classList.remove("on"); return; }
      const host = e.target.closest?.("[data-sgt-loc]");
      if (!host) { chip.classList.remove("on"); return; }
      const loc = host.getAttribute("data-sgt-loc");
      const o = ownerOf(loc);
      if (!o) { chip.classList.remove("on"); return; }
      const f = FEATURES[o.fid];
      // "last changed by", not "owned by" -- see the note in sgt-overlay.mjs.
      chip.innerHTML =
        `<div><span class="k">last changed by</span> ${f.label.replace(/[<>&]/g, "")}</div>` +
        `<div class="s"><span class="k">symbol</span> ${(o.symbol || loc).replace(/[<>&]/g, "")}</div>`;
      chip.style.borderLeft = `3px solid ${f.color}`;
      const x = Math.min(e.clientX + 14, innerWidth - 340);
      const y = Math.min(e.clientY + 16, innerHeight - 70);
      chip.style.left = x + "px";
      chip.style.top = y + "px";
      chip.classList.add("on");
    });
  });

  // `#sgt=<text>` opens the rail and lights the first SYMBOL whose name contains <text>, which
  // is what the rail lists. `#sgt=TrayButton` lights the 24 stars; `#sgt=Chips` lights 25 regions,
  // the 24 on the cards and the filter row in the header.
  // A figure for the paper has to be reproducible without a hand on the mouse, and a recording
  // can deep-link straight to the state it wants to show.
  function applyHash() {
    const m = /^#sgt=(.*)$/.exec(decodeURIComponent(location.hash));
    if (!m) { pinned = null; rail.classList.remove("sgt-open"); document.body.classList.remove("sgt-shift"); unlight(); return; }
    const want = m[1].toLowerCase();
    const found = ordered.find(([symbol]) => symbol.toLowerCase().includes(want));
    rail.classList.add("sgt-open");
    document.body.classList.add("sgt-shift");
    unlight();
    if (found) { pinned = found[0]; light(found[0]); }
  }
  addEventListener("hashchange", applyHash);
  if (location.hash) applyHash();

  // A clean take needs a way to get the instrument out of frame.
  addEventListener("keydown", (e) => {
    if (e.key === "`") { rail.classList.toggle("sgt-hidden"); chip.classList.toggle("sgt-hidden"); }
  });
}

// The React entry is a module script, so at end-of-body the tree is still empty and every
// `[data-sgt-loc]` query returns nothing -- the rail came up with no rows and the highlight lit
// nothing at all. Wait for the first stamped element, then boot once.
if (Object.keys(FEATURES).length) {
  if (document.querySelector("[data-sgt-loc]")) {
    boot();
  } else {
    const obs = new MutationObserver(() => {
      if (!document.querySelector("[data-sgt-loc]")) return;
      obs.disconnect();
      boot();
    });
    obs.observe(document.documentElement, { childList: true, subtree: true });
  }
}
