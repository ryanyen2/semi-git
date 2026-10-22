// Motion primitives for the ugh ledger. Every animation stands for a real event in the history;
// nothing flies in, bounces or springs. The choreography that maps ugh events onto these lives in
// ledger.js (`choreo`). Tokens mirror the motion brief: ease-out for everything that moves, linear
// only for things that keep going (spinner, shimmer); fast 140ms for fades and swaps, base 220ms for
// layout, decode 650ms for text whose identity changes, highlight = in, hold ~1s, out 400ms.
(function () {
  "use strict";
  const reduced = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const T = { fast: 140, base: 220, decode: 650, stagger: 65, siblings: 75, wave: 90, hold: 1000, out: 400 };
  const EASE = "cubic-bezier(0.22, 1, 0.36, 1)";
  const GLYPHS = "abcdefghijklmnopqrstuvwxyz0123456789";
  const rnd = (n) => { let s = ""; for (let i = 0; i < n; i++) s += GLYPHS[(Math.random() * GLYPHS.length) | 0]; return s; };

  // DecodeText: the new string resolves left to right; a muted tail of random glyphs runs ahead of
  // the resolved prefix and changes every frame. The old text goes the moment decoding starts.
  // Returns a promise; a second call on the same element cancels the first.
  const decoding = new WeakMap();
  function decodeText(el, text, ms) {
    text = String(text);
    if (reduced()) { el.textContent = text; return Promise.resolve(); }
    const token = {}; decoding.set(el, token);
    const dur = ms || T.decode; const t0 = performance.now();
    el.textContent = "";
    const head = document.createElement("span"); const tail = document.createElement("span"); tail.className = "m-tail";
    el.append(head, tail);
    return new Promise((resolve) => {
      const step = (now) => {
        if (decoding.get(el) !== token) return resolve();
        const p = Math.min(1, (now - t0) / dur); const eased = 1 - Math.pow(1 - p, 3);
        const n = Math.round(eased * text.length);
        head.textContent = text.slice(0, n);
        const tl = Math.min(8, Math.max(0, text.length - n)); tail.textContent = n < text.length ? rnd(Math.max(6, tl)).slice(0, Math.max(0, Math.min(8, text.length - n))) : "";
        if (p < 1) requestAnimationFrame(step); else { el.textContent = text; decoding.delete(el); resolve(); }
      };
      requestAnimationFrame(step);
    });
  }

  // BlurFadeIn: opacity 0→1 with blur 4→0, fast. Used for content arriving where it belongs.
  function blurFadeIn(el, delay) {
    if (reduced()) { el.style.opacity = ""; return Promise.resolve(); }
    el.style.opacity = "0"; el.style.filter = "blur(4px)";
    el.style.transition = "none";
    void el.offsetWidth; // flush
    el.style.transition = `opacity ${T.fast}ms ${EASE} ${delay || 0}ms, filter ${T.fast}ms ${EASE} ${delay || 0}ms`;
    el.style.opacity = ""; el.style.filter = "";
    return wait(T.fast + (delay || 0)).then(() => { el.style.transition = ""; });
  }
  function fadeOut(el) {
    if (reduced()) { el.style.opacity = "0"; return Promise.resolve(); }
    el.style.transition = `opacity ${T.fast}ms ${EASE}, filter ${T.fast}ms ${EASE}`;
    el.style.opacity = "0"; el.style.filter = "blur(4px)";
    return wait(T.fast);
  }

  // swapContent: fade the old content out, let the container resize smoothly, fade the new in.
  // `render(container)` fills the container with the new content.
  async function swapContent(el, render) {
    const w0 = el.offsetWidth, h0 = el.offsetHeight;
    await fadeOut(el);
    el.replaceChildren(); render(el);
    const w1 = el.offsetWidth, h1 = el.offsetHeight;
    if (!reduced() && (w0 !== w1 || h0 !== h1)) {
      el.style.transition = "none"; el.style.width = w0 + "px"; el.style.height = h0 + "px"; el.style.overflow = "hidden";
      void el.offsetWidth;
      el.style.transition = `width ${T.base}ms ${EASE}, height ${T.base}ms ${EASE}`;
      el.style.width = w1 + "px"; el.style.height = h1 + "px";
      await wait(T.base);
      el.style.width = ""; el.style.height = ""; el.style.overflow = "";
    }
    await blurFadeIn(el);
  }

  // Open a gap: an element that will hold new rows grows from 0 to its natural height, then the
  // caller fills it. Returns when the gap is open.
  async function openGap(el) {
    if (reduced()) return;
    el.style.height = "0px"; el.style.overflow = "hidden"; el.style.transition = "none";
    void el.offsetWidth;
    const h = el.scrollHeight;
    el.style.transition = `height ${T.base}ms ${EASE}`; el.style.height = h + "px";
    await wait(T.base);
    el.style.height = ""; el.style.overflow = ""; el.style.transition = "";
  }
  async function closeGap(el) {
    if (reduced()) { el.remove(); return; }
    const h = el.offsetHeight;
    el.style.height = h + "px"; el.style.overflow = "hidden"; void el.offsetWidth;
    el.style.transition = `height ${T.base}ms ${EASE}, opacity ${T.fast}ms ${EASE}`;
    el.style.height = "0px"; el.style.opacity = "0";
    await wait(T.base); el.remove();
  }

  // Highlight: a tint fades in, holds, fades out. `kind` picks the tint (yours | incoming | affected | warn).
  function highlight(el, kind, hold) {
    el.classList.remove("hl-yours", "hl-incoming", "hl-affected", "hl-warn");
    el.classList.add("hl", "hl-" + (kind || "yours"));
    void el.offsetWidth;
    el.classList.add("hl-on");
    const token = {}; el._hlToken = token;
    return wait(T.fast + (hold == null ? T.hold : hold)).then(() => {
      if (el._hlToken !== token) return;
      el.classList.remove("hl-on");
      return wait(T.out).then(() => { if (el._hlToken === token) el.classList.remove("hl", "hl-" + (kind || "yours")); });
    });
  }

  // LaneDraw: an SVG path draws itself from one end (stroke-dashoffset). Pending lanes are dashed
  // and the change to solid is drawn, not swapped.
  function laneDraw(path, ms) {
    const len = typeof path.getTotalLength === "function" ? path.getTotalLength() : 0;
    if (reduced() || !len) return Promise.resolve();
    path.style.transition = "none"; path.style.strokeDasharray = `${len} ${len}`; path.style.strokeDashoffset = String(len);
    void path.getBoundingClientRect();
    path.style.transition = `stroke-dashoffset ${ms || T.base}ms ${EASE}`; path.style.strokeDashoffset = "0";
    return wait(ms || T.base).then(() => { path.style.strokeDasharray = ""; path.style.strokeDashoffset = ""; path.style.transition = ""; });
  }

  // NodeState: a graph node tweens between states by class; the halo on a terminal state grows ~2x and fades.
  const NODE_STATES = ["hollow", "pending", "working", "solid", "warn", "check", "terminal", "gone"];
  function nodeState(node, state, opts) {
    NODE_STATES.forEach((s) => node.classList.remove("n-" + s));
    node.classList.add("n-" + state);
    if (opts && opts.halo && !reduced()) {
      const halo = node.querySelector(".n-halo"); if (halo) { halo.classList.remove("n-halo-go"); void halo.getBoundingClientRect(); halo.classList.add("n-halo-go"); }
    }
  }

  // CheckDraw: the check's stroke draws in; in a group, one after another.
  function checkDraw(svgPath, delay) { return wait(delay || 0).then(() => laneDraw(svgPath, 200)); }

  // Counter swap: a number changes with a quick fade swap, never a rolling odometer.
  async function counterSwap(el, text) { if (el.textContent === String(text)) return; await fadeOut(el); el.textContent = text; await blurFadeIn(el); }

  // StreamIn: prose appears word by word (~25 words/s), the last few words still fading in.
  async function streamIn(el, text) {
    el.textContent = "";
    if (reduced()) { el.textContent = text; return; }
    const words = String(text).split(/(\s+)/);
    for (const w of words) {
      if (!w) continue;
      const s = document.createElement("span"); s.textContent = w; s.className = "m-word"; el.appendChild(s);
      void s.offsetWidth; s.classList.add("m-word-in");
      if (/\S/.test(w)) await wait(40);
    }
  }

  // Shimmer and spinner are pure CSS classes; these just toggle them so callers read as prose.
  const shimmer = (el, on) => el.classList.toggle("m-shimmer", !!on);
  const progress = (el, frac) => { el.style.setProperty("--p", String(Math.max(0, Math.min(1, frac)))); el.classList.toggle("m-progress-on", frac > 0 && frac < 1); };

  // A phase queue: phases run in order and a newer sequence cancels the rest of an older one, so a
  // state that arrives mid-animation skips cleanly to the latest picture.
  let seq = 0;
  async function run(phases) {
    const mine = ++seq;
    for (const ph of phases) { if (seq !== mine) return false; await ph(); }
    return seq === mine;
  }
  const wait = (ms) => new Promise((r) => setTimeout(r, reduced() ? 0 : ms));

  window.Motion = { T, EASE, reduced, decodeText, blurFadeIn, fadeOut, swapContent, openGap, closeGap, highlight, laneDraw, nodeState, checkDraw, counterSwap, streamIn, shimmer, progress, run, wait };
})();
