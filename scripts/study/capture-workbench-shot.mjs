// Capture the workbench screenshot the sgt practice sheet shows.
//
//   node scripts/study/capture-workbench-shot.mjs <bundle-work-dir> <path-to-sgt> [out.png]
//
// The sheet's picture is the first thing a participant sees of the tool, so it has to be the
// tool. The previous one was taken by hand and went stale silently: it still showed a Timeline /
// Rail toggle, a composition selector, an oracle chip and a Plans counter, none of which the
// shipped workbench has any more, so the sheet and the screen disagreed on the first page.
//
// Headless Chrome over the DevTools protocol, against `editor/vscode/dev/preview.html`, fed the
// views of a real unpacked bundle. Identity hues come from `sgt.tui.color.color_for`, the same
// function the extension mirrors, so the lanes are the colours the participant will see.
import { spawn, execFileSync } from 'node:child_process'
import { writeFileSync, mkdtempSync, unlinkSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '../..')
const DEV = join(ROOT, 'editor/vscode/dev')

const [WORK, SGT, OUT = join(ROOT, 'web/public/materials/sgt_workbench.png')] = process.argv.slice(2)
if (!WORK || !SGT) {
  console.error('usage: capture-workbench-shot.mjs <bundle-work-dir> <path-to-sgt> [out.png]')
  process.exit(2)
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
const sgt = (args) => JSON.parse(execFileSync(SGT, args, { cwd: WORK, maxBuffer: 1 << 28 }))

const compose = sgt(['advanced', 'compose', '--json', '--full'])
compose.grid = sgt(['log', '--json'])
// The real identity hue per node, read out of sgt itself rather than invented here.
const ids = compose.map.nodes.map((n) => n.id)
const python = join(dirname(SGT), 'python')
const hues = JSON.parse(execFileSync(python, ['-c',
  'import json,sys;from sgt.tui.color import color_for;print(json.dumps([color_for(i) for i in json.load(sys.stdin)]))',
], { input: JSON.stringify(ids) }))
compose.map = { ...compose.map, nodes: compose.map.nodes.map((n, i) => ({ ...n, color: hues[i] })) }

const feed = join(DEV, '.live.json')
writeFileSync(feed, JSON.stringify(compose))

const PROFILE = mkdtempSync(join(tmpdir(), 'wb-shot-'))
const port = 9223 + Math.floor(process.pid % 400)
const chrome = spawn(CHROME, ['--headless', '--disable-gpu', '--hide-scrollbars',
  `--remote-debugging-port=${port}`, `--user-data-dir=${PROFILE}`,
  '--allow-file-access-from-files', '--window-size=1500,450', 'about:blank'], { stdio: 'ignore' })

try {
  let ws
  for (let i = 0; i < 80 && !ws; i++) {
    try {
      const v = await (await fetch(`http://127.0.0.1:${port}/json/version`)).json()
      ws = new WebSocket(v.webSocketDebuggerUrl)
    } catch { await sleep(200) }
  }
  if (!ws) throw new Error('chrome never opened its debugging port')
  await new Promise((r) => (ws.onopen = r))

  let nextId = 1
  const pending = new Map()
  ws.onmessage = (e) => {
    const m = JSON.parse(e.data)
    if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id) }
  }
  const send = (method, params = {}, sessionId) => new Promise((res) => {
    const id = nextId++
    pending.set(id, res)
    ws.send(JSON.stringify({ id, method, params, sessionId }))
  })

  const { result: target } = await send('Target.createTarget', { url: 'about:blank' })
  const { result: attached } = await send('Target.attachToTarget', { targetId: target.targetId, flatten: true })
  const s = attached.sessionId
  await send('Page.enable', {}, s)
  await send('Runtime.enable', {}, s)
  await send('Emulation.setDeviceMetricsOverride',
    { width: 1500, height: 450, deviceScaleFactor: 2, mobile: false }, s)
  await send('Page.navigate', { url: `file://${join(DEV, 'preview.html')}` }, s)
  await sleep(2000)
  await send('Runtime.evaluate', {
    expression: `(async () => {
      const raw = await (await fetch("./.live.json")).json();
      window.dispatchEvent(new MessageEvent("message", {data: {type: "state", compose: raw}}));
      await new Promise(r => setTimeout(r, 500));
    })()`, awaitPromise: true, returnByValue: true }, s)
  const shot = await send('Page.captureScreenshot', { format: 'png' }, s)
  writeFileSync(OUT, Buffer.from(shot.result.data, 'base64'))
  console.log('wrote', OUT)
} finally {
  chrome.kill()
  try { unlinkSync(feed) } catch {}
}
