// Capture the dashboard screenshots the stage cards and the tutorials show.
//
//   node scripts/study/capture-page-shots.mjs <repo> <out-dir> <prefix>
//
// The cards claim to show the participant's own dashboard. They do, because the
// shots come from the same built repository the bundle ships -- point this at
// `~/repos/sgt-study/footfall` (or at an unpacked bundle's `work/`) and it
// writes `<prefix>-<name>.png` for every crop the study uses.
//
// It used to be an ad-hoc pass with a browser open by hand, which is why the
// four shots that shipped had no way to be regenerated after a testbed rebuild:
// a rebuild moves the numbers on the pages, and a card showing last month's
// numbers next to a `./stage` script printing this month's is the kind of
// mismatch a participant reads as "the study is broken".
//
// Headless Chrome over the DevTools protocol, at deviceScaleFactor 2 so the
// crops are legible on a retina screen. Each crop names the region it wants as
// a JS expression evaluated in the page, so a crop is defined by the element it
// is about rather than by pixel counts that go stale.

import { spawn, spawnSync } from 'node:child_process'
import { existsSync, readdirSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { createServer } from 'node:net'

const [repo, outDir, prefix] = process.argv.slice(2)
if (!repo || !outDir || !prefix) {
  console.error('usage: node capture-page-shots.mjs <repo> <out-dir> <prefix>')
  process.exit(2)
}

const CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
const PROFILE = process.env.SHOT_PROFILE || '/private/tmp/claude-501/chrome-shot-profile'
const WINDOW = 'start=2018-01-01&end=2018-12-31'
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

// The crops the study shows, and what each one is for. `clip` runs in the page
// and returns a DOMRect-ish; `prepare` runs first, for the one shot that marks a
// row -- the by-year table's 2018 row is the number stages 2 and 4 are entirely
// about, and marking it is what the screenshot exists to do that prose ("the
// 2018 row") cannot. `paths` is tried in order, because the two harvested
// projects route the same page differently.
const CROPS = [
  {
    name: 'window',
    paths: ['/'],
    what: 'the nav and the date window, the one control on every page',
    clip: `(() => { const h = document.querySelector('header').getBoundingClientRect();
             return { x: 0, y: 0, width: h.width, height: h.bottom + 1 } })()`,
  },
  {
    name: 'hourly',
    paths: ['/hourly'],
    what: 'the busiest hour and the hour-of-day chart under it',
    clip: `(() => { const c = document.querySelectorAll('main svg')[0].getBoundingClientRect();
             return { x: 0, y: 0, width: document.documentElement.clientWidth,
                      height: c.bottom + 28 } })()`,
  },
  {
    name: 'hourly-split',
    paths: ['/hourly'],
    what: 'the weekday and weekend charts, side by side',
    clip: `(() => { const svgs = document.querySelectorAll('main svg');
             const first = svgs[1].getBoundingClientRect();
             const last = svgs[svgs.length - 1].getBoundingClientRect();
             const label = [...document.querySelectorAll('main .label')].pop()
                             .getBoundingClientRect();
             return { x: 0, y: label.top - 12, width: document.documentElement.clientWidth,
                      height: Math.max(first.bottom, last.bottom) - label.top + 40 } })()`,
  },
  {
    name: 'monthly',
    paths: ['/monthly'],
    what: 'the month-by-month chart, whose coloured bars flag months with an event day',
    clip: `(() => { const c = document.querySelector('main svg').getBoundingClientRect();
             return { x: 0, y: 0, width: document.documentElement.clientWidth,
                      height: Math.min(c.bottom + 20, 515) } })()`,
  },
  {
    name: 'sides',
    paths: ['/sides'],
    what: 'the two-sensor comparison: the two totals and their share',
    // Through the row holding the two big numbers, and the note under it when
    // there is one. Not "down to the paragraph about the sensor": the two
    // projects' comparison pages are genuinely different shapes -- bikecount's
    // carries two more charts and a long caveat -- and a rule written against
    // one of them cropped 1908 pixels of the other.
    clip: `(() => {
      const big = [...document.querySelectorAll('main .big')].pop()
      let row = big
      while (row.parentElement && row.parentElement.tagName !== 'MAIN') row = row.parentElement
      let bottom = row.getBoundingClientRect().bottom
      const next = row.nextElementSibling
      if (next && next.tagName === 'P' && !next.classList.contains('label')) {
        bottom = next.getBoundingClientRect().bottom
      }
      return { x: 0, y: 0, width: document.documentElement.clientWidth,
               height: bottom + 24 } })()`,
  },
  {
    // The by-year page as it actually looks, for stage 1's map. The marked
    // variant below is an annotation we add, and on a card that opens "nothing
    // is wrong with it" a red outline round the 2018 row says the opposite.
    name: 'yearly-plain',
    paths: ['/yearly', '/years'],
    what: 'the by-year table, unmarked',
    clip: `(() => { const t = document.querySelector('main table').getBoundingClientRect();
             const note = [...document.querySelectorAll('main p')]
               .map((p) => p.getBoundingClientRect())
               .filter((r) => r.top >= t.bottom - 1).pop();
             return { x: 0, y: 0, width: document.documentElement.clientWidth,
                      height: (note ? note.bottom : t.bottom) + 20 } })()`,
  },
  {
    name: 'yearly',
    // The harvest let each project's agent choose its own routes, and they chose
    // differently. Tried in order; the first that has a by-year table wins.
    paths: ['/yearly', '/years'],
    what: 'the by-year table with the 2018 row marked',
    prepare: `(() => {
      const row = [...document.querySelectorAll('main table tr')]
        .find((tr) => tr.textContent.trim().startsWith('2018'))
      if (row) {
        row.style.outline = '2px solid #b3261e'
        row.style.outlineOffset = '2px'
        row.style.background = '#fdecea'
      }
      return !!row })()`,
    // Down to the bottom of the note under the table, not the table's own
    // bottom: cutting at the table sliced the partial-year caveat in half, and a
    // screenshot ending mid-sentence reads as a broken image.
    clip: `(() => { const t = document.querySelector('main table').getBoundingClientRect();
             const note = [...document.querySelectorAll('main p')]
               .map((p) => p.getBoundingClientRect())
               .filter((r) => r.top >= t.bottom - 1).pop();
             return { x: 0, y: 0, width: document.documentElement.clientWidth,
                      height: (note ? note.bottom : t.bottom) + 20 } })()`,
  },
]

function freePort() {
  const s = createServer()
  s.listen(0)
  const port = s.address().port
  s.close()
  return port
}

function packageOf(dir) {
  for (const name of readdirSync(dir)) {
    if (existsSync(join(dir, name, 'server.py'))) return name
  }
  throw new Error(`no package with a server.py under ${dir}`)
}

// The interpreter the repo itself would use. A bundle ships its own venv; a
// source testbed does not, and the system python3 is right there.
function python(dir) {
  for (const candidate of [join(dir, '.venv/bin/python'), '../toolenv/bin/python']) {
    const p = candidate.startsWith('..') ? join(dir, candidate) : candidate
    if (existsSync(p)) return p
  }
  return spawnSync('command', ['-v', 'python3'], { shell: true }).stdout.toString().trim() || 'python3'
}

const pkg = packageOf(repo)
const port = freePort()
const py = python(repo)
const app = spawn(py, ['-c', `from ${pkg} import server; server.serve(${port})`], {
  cwd: repo,
  stdio: 'ignore',
})

const chromePort = freePort()
const chrome = spawn(CHROME, [
  '--headless',
  '--disable-gpu',
  '--hide-scrollbars',
  `--remote-debugging-port=${chromePort}`,
  `--user-data-dir=${PROFILE}`,
  '--window-size=900,1200',
  'about:blank',
], { stdio: 'ignore' })

async function endpoint() {
  for (let i = 0; i < 80; i++) {
    try {
      const res = await fetch(`http://127.0.0.1:${chromePort}/json/version`)
      return (await res.json()).webSocketDebuggerUrl
    } catch {
      await sleep(200)
    }
  }
  throw new Error('chrome never opened its debugging port')
}

async function appUp() {
  for (let i = 0; i < 80; i++) {
    try {
      await fetch(`http://localhost:${port}/`)
      return
    } catch {
      await sleep(200)
    }
  }
  throw new Error('the dashboard never came up')
}

let failed = false
try {
  await appUp()
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
  const send = (method, params = {}, sessionId) =>
    new Promise((resolve) => {
      const id = nextId++
      pending.set(id, resolve)
      ws.send(JSON.stringify({ id, method, params, sessionId }))
    })

  const { result: target } = await send('Target.createTarget', { url: 'about:blank' })
  const { result: attached } = await send('Target.attachToTarget', {
    targetId: target.targetId,
    flatten: true,
  })
  const s = attached.sessionId
  await send('Page.enable', {}, s)
  await send('Runtime.enable', {}, s)
  // 810 wide is the width the shipped shots were taken at, so a regenerated
  // crop lines up with the one it replaces. Scale 2 for a legible retina image.
  await send('Emulation.setDeviceMetricsOverride',
             { width: 810, height: 1100, deviceScaleFactor: 2, mobile: false }, s)

  const evaluate = async (expr) => {
    const res = await send('Runtime.evaluate',
                           { expression: expr, returnByValue: true, awaitPromise: true }, s)
    if (res.result?.exceptionDetails) {
      throw new Error(res.result.exceptionDetails.exception?.description ?? 'evaluate threw')
    }
    return res.result?.result?.value
  }

  for (const crop of CROPS) {
    let rect = null
    let why = ''
    for (const path of crop.paths) {
      await send('Page.navigate', { url: `http://localhost:${port}${path}?${WINDOW}` }, s)
      await sleep(700)
      if (crop.prepare && (await evaluate(crop.prepare)) === false) {
        why = `nothing to mark on ${crop.paths.join(' or ')} — the page has changed shape`
        continue
      }
      try {
        rect = await evaluate(crop.clip)
        break
      } catch (e) {
        why = e.message
      }
    }
    if (rect == null) {
      console.error(`  ${crop.name}: ${why}`)
      failed = true
      continue
    }
    const clip = {
      x: Math.max(0, Math.round(rect.x)),
      y: Math.max(0, Math.round(rect.y)),
      width: Math.round(rect.width),
      height: Math.round(rect.height),
      scale: 1,
    }
    if (!(clip.width > 40 && clip.height > 40)) {
      console.error(`  ${crop.name}: clip came back ${clip.width}x${clip.height}`)
      failed = true
      continue
    }
    const shot = await send('Page.captureScreenshot',
                            { format: 'png', clip, captureBeyondViewport: true }, s)
    const out = join(outDir, `${prefix}-${crop.name}.png`)
    writeFileSync(out, Buffer.from(shot.result.data, 'base64'))
    console.log(`  ${prefix}-${crop.name}.png  ${clip.width}x${clip.height}  — ${crop.what}`)
  }
  ws.close()
} finally {
  chrome.kill()
  app.kill()
}
process.exit(failed ? 1 : 0)
