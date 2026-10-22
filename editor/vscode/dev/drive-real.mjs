// Drive the timeline in a REAL Chrome over its debugging protocol: real input events, real
// frames, wall-clock time. No virtual-time budget and no rAF shim, which is what the headless
// screenshot path cannot give (it starves animation frames). Usage:
//
//   cd editor/vscode && python3 -m http.server 8777 &
//   "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless=new \
//     --remote-debugging-port=9333 --user-data-dir=/tmp/ugh-real-profile --window-size=1400,420 about:blank &
//   node dev/drive-real.mjs <out dir> 'http://localhost:8777/dev/preview.html?fx=fixture-actors.json&hues=1'
//
// It grabs the playhead at the now edge, drags it left over ~200 ms and lets go with speed,
// samples the line's x and the readout every 40 ms, presses Esc, follows a session from its
// column header, presses Esc again, and focuses a theme; it writes numbered PNG frames and
// log.json to the out dir. Not shipped (under dev/, .vscodeignore'd).
import fs from "node:fs";
const S = process.argv[2], url = process.argv[3];
const list = await (await fetch("http://127.0.0.1:9333/json")).json();
const page = list.find((t) => t.type === "page");
const ws = new WebSocket(page.webSocketDebuggerUrl);
await new Promise((r) => ws.addEventListener("open", r));
let id = 0; const waiting = new Map();
ws.addEventListener("message", (m) => { const d = JSON.parse(m.data); if (d.id && waiting.has(d.id)) { waiting.get(d.id)(d); waiting.delete(d.id); } });
const send = (method, params = {}) => new Promise((res) => { const i = ++id; waiting.set(i, res); ws.send(JSON.stringify({ id: i, method, params })); });
const evaluate = async (expr) => (await send("Runtime.evaluate", { expression: expr, returnByValue: true })).result.result.value;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const log = [];
const t0 = Date.now();
const sample = async (tag) => log.push([tag, Date.now() - t0, await evaluate(`(() => { const l = document.querySelector('.frontier-line'); const ro = document.querySelector('.scrub-readout'); return [l && Number(l.getAttribute('x1')).toFixed(1), ro && ro.style.display !== 'none' ? ro.textContent : '', document.getElementById('presence').textContent]; })()`)]);
let shot = 0;
const snap = async (name) => { const r = await send("Page.captureScreenshot", { format: "png" }); fs.writeFileSync(`${S}/real/${String(shot++).padStart(2, "0")}-${name}.png`, Buffer.from(r.result.data, "base64")); };
await send("Page.enable"); await send("Runtime.enable");
await send("Emulation.setDeviceMetricsOverride", { width: 1400, height: 420, deviceScaleFactor: 1, mobile: false });
await send("Page.navigate", { url });
await sleep(2500);
await sample("loaded"); await snap("idle");
// 1. grab the playhead at the now edge and drag left over 200 ms, then let go with speed
const band = await evaluate(`(() => { const r = document.querySelector('.frontier-band').getBoundingClientRect(); return [r.left + 6, r.top + 20]; })()`);
let [x, y] = band;
await send("Input.dispatchMouseEvent", { type: "mouseMoved", x, y });
await send("Input.dispatchMouseEvent", { type: "mousePressed", x, y, button: "left", clickCount: 1 });
await sample("pressed");
for (let i = 0; i < 12; i++) { x -= 18; await send("Input.dispatchMouseEvent", { type: "mouseMoved", x, y, button: "left" }); await sleep(16); if (i === 5) { await sample("mid-drag"); await snap("drag"); } }
await sample("released-at");
await send("Input.dispatchMouseEvent", { type: "mouseReleased", x, y, button: "left", clickCount: 1 });
for (let i = 0; i < 10; i++) { await sleep(40); await sample("coast"); if (i === 1) await snap("coast"); }
await sleep(400); await sample("settled"); await snap("landed");
await sleep(1500); await sample("after-hold"); await snap("readout-faded");
// 2. Esc glides home
await send("Input.dispatchKeyEvent", { type: "keyDown", key: "Escape", code: "Escape", windowsVirtualKeyCode: 27 });
await send("Input.dispatchKeyEvent", { type: "keyUp", key: "Escape", code: "Escape", windowsVirtualKeyCode: 27 });
for (let i = 0; i < 6; i++) { await sleep(40); await sample("home"); if (i === 1) await snap("gliding-home"); }
await sleep(300); await sample("at-now"); await snap("at-now");
// 3. follow a session from its column header, then Esc
const head = await evaluate(`(() => { const r = document.querySelector('.actor-head.session').getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; })()`);
await send("Input.dispatchMouseEvent", { type: "mouseMoved", x: head[0], y: head[1] });
await send("Input.dispatchMouseEvent", { type: "mousePressed", x: head[0], y: head[1], button: "left", clickCount: 1 });
await send("Input.dispatchMouseEvent", { type: "mouseReleased", x: head[0], y: head[1], button: "left", clickCount: 1 });
await sleep(500); await sample("following"); await snap("following");
await send("Input.dispatchKeyEvent", { type: "keyDown", key: "Escape", code: "Escape", windowsVirtualKeyCode: 27 });
await send("Input.dispatchKeyEvent", { type: "keyUp", key: "Escape", code: "Escape", windowsVirtualKeyCode: 27 });
await sleep(500); await sample("unfollowed"); await snap("unfollowed");
// 4. a theme focus puts its statement in the strip
const row = await evaluate(`(() => { const r = document.querySelector('.theme-row').getBoundingClientRect(); return [r.left + 20, r.top + r.height / 2]; })()`);
await send("Input.dispatchMouseEvent", { type: "mouseMoved", x: row[0], y: row[1] });
await send("Input.dispatchMouseEvent", { type: "mousePressed", x: row[0], y: row[1], button: "left", clickCount: 1 });
await send("Input.dispatchMouseEvent", { type: "mouseReleased", x: row[0], y: row[1], button: "left", clickCount: 1 });
await sleep(600); await sample("theme"); await snap("theme-strip");
fs.writeFileSync(`${S}/real/log.json`, JSON.stringify(log, null, 1));
console.log(log.map((r) => r.join(" | ")).join("\n"));
ws.close();
