// Drive a page in headless Chrome over the DevTools protocol.
//
//   node scripts/demo/drive-page.mjs <url> <plan.json>
//
// Written for the Variolite demo, where every feature is an interaction: a tab
// that switches which code runs cannot be checked by looking at a screenshot,
// only by running the file twice and reading two different numbers. It is also
// what `render-variolite-frontiers.sh` replays at each frontier, because the
// default view of an editor does not show what the editor can do.
//
// The plan is a list of steps run in order:
//   { "eval": "<js>", "as": "name" }   evaluate in the page, await it, keep the value
//   { "mouse": "click|down|up|move", "x": 0, "y": 0, "button": "left|right", "clicks": 1 }
//   { "key": "a", "modifiers": 4 }     dispatch a key press, 1 alt 2 ctrl 4 meta 8 shift
//   { "insert": "text" }               insert text at the caret, replacing the selection
//   { "type": "text" }                 type text
//   { "wait": 300 }                    milliseconds
//   { "shot": "/path/out.png" }        screenshot
//
// Coordinates may be written as "$name.x", which reads from a value an earlier
// eval step returned, so a step can select code whose position it looked up
// rather than one hardcoded in pixels.

import { spawn } from 'node:child_process'
import { readFileSync, writeFileSync } from 'node:fs'

const [url, planPath] = process.argv.slice(2)
const plan = JSON.parse(readFileSync(planPath, 'utf8'))

// Overridable because two drives at once would otherwise fight over one debugging
// port and one profile directory, and a frontier sweep runs this in a loop for
// several minutes. Running anything else against a page in that window hung.
const PORT = Number(process.env.DRIVE_PORT || 9333)
const PROFILE = process.env.DRIVE_PROFILE || '/private/tmp/claude-501/chrome-drive-profile'
const CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
const chrome = spawn(CHROME, [
  '--headless',
  '--disable-gpu',
  '--hide-scrollbars',
  `--remote-debugging-port=${PORT}`,
  `--user-data-dir=${PROFILE}`,
  '--window-size=1280,820',
  'about:blank',
], { stdio: 'ignore' })

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

async function endpoint() {
  for (let i = 0; i < 60; i++) {
    try {
      const res = await fetch(`http://127.0.0.1:${PORT}/json/version`)
      return (await res.json()).webSocketDebuggerUrl
    } catch {
      await sleep(200)
    }
  }
  throw new Error('chrome never opened its debugging port')
}

const ws = new WebSocket(await endpoint())
await new Promise((r) => (ws.onopen = r))

let nextId = 1
const pending = new Map()
ws.onmessage = (event) => {
  const msg = JSON.parse(event.data)
  if (msg.id && pending.has(msg.id)) {
    pending.get(msg.id)(msg)
    pending.delete(msg.id)
  }
}

function send(method, params = {}, sessionId) {
  const id = nextId++
  return new Promise((resolve) => {
    pending.set(id, resolve)
    ws.send(JSON.stringify({ id, method, params, sessionId }))
  })
}

const { result: target } = await send('Target.createTarget', { url: 'about:blank' })
const { result: attached } = await send('Target.attachToTarget', {
  targetId: target.targetId,
  flatten: true,
})
const session = attached.sessionId
await send('Page.enable', {}, session)
await send('Runtime.enable', {}, session)
await send('Page.navigate', { url }, session)
await sleep(2200)

const bag = {}

function resolveValue(value) {
  if (typeof value !== 'string' || !value.startsWith('$')) return value
  const path = value.slice(1).split('.')
  let at = bag
  for (const key of path) at = at?.[key]
  return at
}

async function mouse(kind, x, y, button = 'left', clicks = 1) {
  const common = { x, y, button, clickCount: clicks }
  if (kind === 'move') return send('Input.dispatchMouseEvent', { type: 'mouseMoved', ...common }, session)
  if (kind === 'down') return send('Input.dispatchMouseEvent', { type: 'mousePressed', ...common }, session)
  if (kind === 'up') return send('Input.dispatchMouseEvent', { type: 'mouseReleased', ...common }, session)
  await send('Input.dispatchMouseEvent', { type: 'mousePressed', ...common }, session)
  return send('Input.dispatchMouseEvent', { type: 'mouseReleased', ...common }, session)
}

for (const step of plan) {
  if (step.eval !== undefined) {
    const res = await send(
      'Runtime.evaluate',
      { expression: step.eval, awaitPromise: true, returnByValue: true },
      session,
    )
    const thrown = res.result?.exceptionDetails
    if (thrown) {
      console.error('threw:', JSON.stringify(thrown.exception?.description ?? thrown))
      process.exitCode = 1
    }
    const value = res.result?.result?.value
    if (step.as) bag[step.as] = value
    console.log(`eval ${step.as ?? ''}`, JSON.stringify(value))
  } else if (step.mouse !== undefined) {
    const x = resolveValue(step.x)
    const y = resolveValue(step.y)
    // A step can name a coordinate an earlier eval could not find, because the
    // control it points at does not exist at this frontier. Skipping is the
    // whole point: the same plan runs everywhere and simply does less.
    if (typeof x !== 'number' || typeof y !== 'number') {
      console.log(`skip mouse ${step.mouse}: no coordinate`)
    } else {
      await mouse(step.mouse, x, y, step.button ?? 'left', step.clicks ?? 1)
    }
  } else if (step.key !== undefined) {
    const mods = step.modifiers ?? 0
    await send('Input.dispatchKeyEvent', { type: 'keyDown', key: step.key, modifiers: mods }, session)
    await send('Input.dispatchKeyEvent', { type: 'keyUp', key: step.key, modifiers: mods }, session)
  } else if (step.insert !== undefined) {
    await send('Input.insertText', { text: step.insert }, session)
  } else if (step.type !== undefined) {
    for (const ch of step.type) {
      await send('Input.dispatchKeyEvent', { type: 'char', text: ch }, session)
    }
  } else if (step.wait !== undefined) {
    await sleep(step.wait)
  } else if (step.shot !== undefined) {
    const shot = await send('Page.captureScreenshot', { format: 'png' }, session)
    writeFileSync(step.shot, Buffer.from(shot.result.data, 'base64'))
    console.log('wrote', step.shot)
  }
}

ws.close()
chrome.kill()
