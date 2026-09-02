// Render a REAL study bundle through the real webview code, and check what a participant would
// look at.
//
//   node dev/render-bundle.js <bundle-work-dir> <path-to-sgt> [path-to-shipped-workbench.js]
//
// `smoke.js` renders this repository's own history from a checked-in fixture, which is the right
// thing for the layout contracts and the wrong thing for the study: the record a participant is
// handed is a different shape (13 saves, two subsystems, a cross-feature theme the stages name),
// and the panel that failed them was one the fixture never exercised. This drives the same
// `workbench.js` off `sgt advanced compose`/`sgt log`/`sgt revert --emit` run against the unpacked
// bundle, so the thing under test is the artefact.
//
// Called by `scripts/study/verify-bundles.sh` for each sgt-arm bundle.
const fs = require("fs");
const path = require("path");
const { execFileSync } = require("child_process");

const WORK = process.argv[2];
const SGT = process.argv[3];
// The webview the BUNDLE ships, when the caller can name it (unpacked from its own .vsix). This
// repo's copy is the same file until somebody edits it after a build, and that is precisely the
// state where a gate reading the repo says the artefact is fine and it is not.
const SHIPPED = process.argv[4];
if (!WORK || !SGT) {
  console.error("usage: node dev/render-bundle.js <bundle-work-dir> <path-to-sgt> [workbench.js]");
  process.exit(2);
}

const sgt = (args) => JSON.parse(execFileSync(SGT, args, { cwd: WORK, maxBuffer: 1 << 28 }));

// The shim smoke.js already has. Sliced at a declared boundary rather than guessed at.
const smokeSrc = fs.readFileSync(path.join(__dirname, "smoke.js"), "utf8");
let shim = smokeSrc.slice(0, smokeSrc.indexOf("// ---- end-domshim"))
  .replace(/__dirname/g, JSON.stringify(__dirname));
if (SHIPPED) {
  shim = shim.replace(/const jsPath = [^;]+;/, `const jsPath = ${JSON.stringify(SHIPPED)};`);
}

const compose = sgt(["advanced", "compose", "--json", "--full"]);
compose.grid = sgt(["log", "--json"]);
// The host gives every node an identity hue before posting (colorForNode); fed raw, every lane is
// grey and a regression that loses hue could not be seen.
compose.map = {
  ...compose.map,
  nodes: (compose.map.nodes || []).map((n, i) => ({
    ...n, color: `#${(0x334455 + i * 0x010203).toString(16).padStart(6, "0")}`,
  })),
};
const feature = (compose.map.nodes || []).find((n) => n.kind === "feature");
const emit = feature ? sgt(["revert", feature.id, "--emit", "--json"]) : { files: {} };

let fails = 0;
const chk = (name, cond, detail) => {
  if (cond) { console.log("  ✓", name); }
  else { console.log("  ✗", name, detail != null ? `(${detail})` : ""); fails++; }
};

eval(shim + `
global.window.dispatchEvent(new global.MessageEvent("message", { data: { type: "state", compose } }));

const lanes = byId.rail.querySelectorAll(".glane");
chk("lanes drawn", lanes.length > 0, lanes.length);
chk("checkpoint cars drawn", byId.rail.querySelectorAll(".gcar-wrap").length > 0);

// The titlebar controls this study does not use must not be reachable at all.
for (const id of ["oracleChip", "plansChip", "plansPopover", "driftChip", "compositionBtn", "viewSeg"]) {
  chk("no " + id, !byId[id]);
}

// Search, on the record a participant is actually handed.
const box = byId.findBox;
box.value = "average";
box._listeners.input.forEach((fn) => fn({}));
const hits = byId.findResults.querySelectorAll(".find-hit");
chk("typing answers without a round trip", hits.length > 0, hits.length);
const kinds = [...byId.findResults.querySelectorAll(".find-kind")].map((e) => e.textContent);
chk("a result's kind is a glyph, not a word in the same ink as the name",
    kinds.length > 0 && kinds.every((k) => k.length <= 2), kinds.join(","));
const goes = [...byId.findResults.querySelectorAll(".find-go")].map((e) => e.textContent);
chk("every result says where a click lands", goes.length === hits.length && goes.every(Boolean));
box.value = "";
box._listeners.input.forEach((fn) => fn({}));

// Select a feature and answer its change request with the real projection.
const lane = [...lanes].find((l) => (l.getAttribute("data-id") || "").startsWith("f-"));
const clickable = [lane, lane && lane.querySelector(".glane-hit"), lane && lane.querySelector(".glane-label-btn")]
  .find((e) => e && e._listeners && e._listeners.click);
chk("a lane row takes a click", !!clickable);
posted = [];
if (clickable) clickable._listeners.click.forEach((fn) => fn({ stopPropagation() {}, preventDefault() {} }));
const req = posted.filter((m) => m.type === "requestChange").pop();
chk("selecting a lane asks the host what it changed", !!req, JSON.stringify(posted.map((m) => m.type)));
if (req) {
  global.window.dispatchEvent(new global.MessageEvent("message", { data: {
    type: "changeResult", seq: req.seq, ok: true, files: emit.files || {} } }));
  const insp = byId.inspector;
  const clines = insp.querySelectorAll(".cline");
  // The defect this exists for: the panel answered "what did this change" with counts alone.
  chk("the change panel shows the changed lines, not just counts", clines.length > 0, clines.length);
  const texts = [...insp.querySelectorAll(".cline-text")].map((e) => e.textContent || "");
  chk("a changed line carries real code", texts.some((t) => t.trim().length), texts.slice(0, 2).join(" | "));
  const words = [...insp.querySelectorAll("*")].map((e) => e._text || "").join(" ");
  chk("nothing on the panel says 'chapter'", !/chapter/i.test(words));
  chk("no raw feature id leads the panel",
      !/f-[0-9a-f]{40}/.test([...insp.querySelectorAll(".detail-meta")].map((e) => e._text || "").join(" ")));
  chk("the checkpoint list carries no maintenance instruction", !/sgt intent build/.test(words));

  // The words. This arm's claim is that its history says what each piece of work was asked for,
  // and the testbeds are replayed with no prompt hook running -- so an empty capture store is a
  // failure that every other check passes over: the panel still renders, the verbs still work,
  // and the one attribute the arm is judged on is simply not there.
  const rows = [...insp.querySelectorAll(".checkpoint")];
  chk("checkpoint rows drawn", rows.length > 0, rows.length);
  const row = rows.find((r) => r._listeners && r._listeners.click);
  if (row) {
    row._listeners.click.forEach((fn) => fn({ stopPropagation() {}, preventDefault() {} }));
    const quote = byId.inspector.querySelector(".asked-quote");
    chk("selecting a checkpoint shows what it was asked for", !!quote,
        quote ? "" : "no .asked-quote — the bundle's capture stores may be empty");
    if (quote) {
      const text = quote.textContent || "";
      // An excerpt, not the prompt. Real prompts in these testbeds run 400-900 characters, and
      // the whole point of the attribute is that it shows the request rather than the paragraph
      // around it.
      chk("the ask reads as an excerpt", text.length > 2 && text.length <= 130, text.length);
      chk("the ask is quoted as somebody's words", /^“/.test(text.trim()), text.slice(0, 12));
      chk("it says whose words they are",
          /(you|assistant|recorded)/i.test(byId.inspector.querySelector(".asked-meta")._text || ""));
    }
    // ...and the whole prompt is reachable, or the excerpt is a summary with nothing behind it.
    const more = byId.inspector.querySelector(".asked-more");
    if (more) {
      posted = [];
      more._listeners.click.forEach((fn) => fn({ stopPropagation() {} }));
      const ask = posted.filter((m) => m.type === "requestAsked").pop();
      chk("opening it asks the host for the whole prompt", !!ask,
          JSON.stringify(posted.map((m) => m.type)));
      if (ask) {
        global.window.dispatchEvent(new global.MessageEvent("message", { data: {
          type: "askedResult", seq: ask.seq, ref: ask.ref, ok: true,
          asks: [{ gist: "g", text: "the whole prompt, verbatim", source: "you, in a Claude Code chat",
                   ts: null, claimed: 1, chars: 25, trimmed: true, resumable: false,
                   claude_session_id: null, channel: "hook", actor: "human", scope: "stint" }] } }));
        const full = byId.inspector.querySelector(".asked-text");
        chk("the whole prompt reads back in the panel",
            !!full && /the whole prompt, verbatim/.test(full.textContent || ""));
      }
    }
  }
}
`);

if (fails) {
  console.log(`\nWORKBENCH RENDER FAILED (${fails})`);
  process.exit(1);
}
console.log("\nWORKBENCH RENDER OK");
