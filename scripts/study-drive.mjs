#!/usr/bin/env node
// A small browser you can drive from the shell, one command at a time.
//
//   node scripts/study-drive.mjs <session> open <url>
//   node scripts/study-drive.mjs <session> text
//   node scripts/study-drive.mjs <session> buttons
//   node scripts/study-drive.mjs <session> click "<visible text>" [nth]
//   node scripts/study-drive.mjs <session> type "<label contains>" "<value>"
//   node scripts/study-drive.mjs <session> pick "<question contains>" "<option>"
//   node scripts/study-drive.mjs <session> scale "<label contains>" <value>
//   node scripts/study-drive.mjs <session> check "<label contains>"
//   node scripts/study-drive.mjs <session> shot <path>
//   node scripts/study-drive.mjs <session> js "<expression>"
//   node scripts/study-drive.mjs <session> stop
//
// The browser stays open between commands, keyed by <session>, so a sign-in or
// a half-filled form survives from one shell command to the next. That is the
// whole point: a session that resets between steps cannot be walked through the
// way a person walks through it.
//
// Errors are returned as text rather than thrown, because the caller is usually
// a person or an agent reading stdout, not a program catching exceptions.

import { spawn } from 'node:child_process'
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const CHROME =
  process.env.CHROME_PATH ?? '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
const STATE_DIR = join(tmpdir(), 'study-drive')
mkdirSync(STATE_DIR, { recursive: true })

const [session, verb, ...args] = process.argv.slice(2)
if (!session || !verb) {
  console.log(readFileSync(new URL(import.meta.url)).toString().split('\n').slice(1, 22).join('\n'))
  process.exit(2)
}

const statePath = join(STATE_DIR, `${session}.json`)
const profileDir = join(STATE_DIR, `${session}-profile`)
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

function readState() {
  try {
    return JSON.parse(readFileSync(statePath, 'utf8'))
  } catch {
    return null
  }
}

async function portAlive(port) {
  try {
    const r = await fetch(`http://127.0.0.1:${port}/json/version`, { signal: AbortSignal.timeout(1500) })
    return r.ok
  } catch {
    return false
  }
}

async function ensureBrowser() {
  const state = readState()
  if (state && (await portAlive(state.port))) return state.port

  // Ports are derived from the session name so two agents never collide.
  let hash = 0
  for (const ch of session) hash = (hash * 31 + ch.charCodeAt(0)) % 4000
  const port = 9400 + hash

  mkdirSync(profileDir, { recursive: true })
  const chrome = spawn(
    CHROME,
    [
      '--headless=new',
      '--disable-gpu',
      '--hide-scrollbars',
      '--no-first-run',
      '--no-default-browser-check',
      `--remote-debugging-port=${port}`,
      `--user-data-dir=${profileDir}`,
      '--window-size=1440,1000',
      'about:blank',
    ],
    { stdio: 'ignore', detached: true },
  )
  chrome.unref()

  for (let i = 0; i < 80; i++) {
    if (await portAlive(port)) break
    await sleep(250)
  }
  if (!(await portAlive(port))) {
    console.log('ERROR: the browser did not start')
    process.exit(1)
  }
  writeState({ port, pid: chrome.pid })
  return port
}

function writeState(s) {
  writeFileSync(statePath, JSON.stringify(s))
}

async function connect(port) {
  const tabs = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json()
  const page = tabs.find((t) => t.type === 'page')
  const ws = new WebSocket(page.webSocketDebuggerUrl)
  await new Promise((r) => (ws.onopen = r))
  let id = 1
  const pending = new Map()
  ws.onmessage = (m) => {
    const msg = JSON.parse(m.data)
    if (msg.id && pending.has(msg.id)) {
      const { res, rej } = pending.get(msg.id)
      pending.delete(msg.id)
      msg.error ? rej(new Error(JSON.stringify(msg.error))) : res(msg.result)
    }
  }
  const send = (method, params = {}) => {
    const i = id++
    ws.send(JSON.stringify({ id: i, method, params }))
    return new Promise((res, rej) => pending.set(i, { res, rej }))
  }
  await send('Page.enable')
  await send('Runtime.enable')
  return { send, close: () => ws.close() }
}

const evaluate = async (send, expression) => {
  const r = await send('Runtime.evaluate', {
    expression,
    awaitPromise: true,
    returnByValue: true,
  })
  if (r.exceptionDetails) {
    return `ERROR: ${r.exceptionDetails.exception?.description ?? r.exceptionDetails.text}`
  }
  return r.result.value
}

// Setting a value on a React-controlled input needs the native setter, or React
// never sees the change and silently reverts it on the next render.
const SET_VALUE = `
function __setValue(el, value) {
  const proto = el instanceof window.HTMLTextAreaElement
    ? window.HTMLTextAreaElement.prototype
    : window.HTMLInputElement.prototype;
  Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, String(value));
  el.dispatchEvent(new Event('input', { bubbles: true }));
  el.dispatchEvent(new Event('change', { bubbles: true }));
}
`

const q = (s) => JSON.stringify(s)

async function main() {
  if (verb === 'stop') {
    const state = readState()
    if (state?.pid) {
      try {
        process.kill(-state.pid)
      } catch {
        try {
          process.kill(state.pid)
        } catch {}
      }
    }
    try {
      writeFileSync(statePath, '{}')
    } catch {}
    console.log('stopped')
    return
  }

  const port = await ensureBrowser()
  const { send, close } = await connect(port)

  try {
    switch (verb) {
      case 'open': {
        await send('Page.navigate', { url: args[0] })
        // Wait for the app to paint something rather than a fixed sleep.
        for (let i = 0; i < 40; i++) {
          await sleep(400)
          const ready = await evaluate(send, `document.body.innerText.trim().length > 40`)
          if (ready === true) break
        }
        await sleep(1200)
        console.log(await evaluate(send, 'document.body.innerText'))
        break
      }

      case 'text': {
        await sleep(Number(args[0] ?? 0))
        console.log(await evaluate(send, 'document.body.innerText'))
        break
      }

      case 'buttons': {
        console.log(
          await evaluate(
            send,
            `[...document.querySelectorAll('button, a.btn, summary')]
               .map(e => (e.innerText||'').trim().split('\\n')[0])
               .filter(Boolean).join('\\n')`,
          ),
        )
        break
      }

      case 'fields': {
        console.log(
          await evaluate(
            send,
            `[...document.querySelectorAll('.field')].map(f => {
               const label = (f.querySelector('.field-label')?.innerText || '').trim()
                          || (f.querySelector('label.check')?.innerText || '').trim().split('\\n')[0];
               const kinds = new Set([...f.querySelectorAll('input, textarea, select')].map(i => i.type || i.tagName.toLowerCase()));
               // A single-choice question renders as a row of buttons with the
               // chosen one highlighted, not as a checked input. Reading only
               // inputs marked every one of them unanswered forever, which
               // trains you to ignore the warning you most need to see.
               const buttons = [...f.querySelectorAll('button.btn')];
               if (buttons.length) kinds.add('choice');
               const chosen = buttons.some(b => b.classList.contains('primary'));
               const filled = [...f.querySelectorAll('input, textarea')].some(i =>
                 (i.type === 'radio' || i.type === 'checkbox') ? i.checked : String(i.value || '').length > 0);
               const untouched = f.innerText.includes('Not answered yet');
               const answered = (chosen || filled) && !untouched;
               return label + '  [' + [...kinds].join(',') + ']' + (answered ? '' : '  UNANSWERED');
             }).join('\\n')`,
          ),
        )
        break
      }

      case 'click': {
        // Exact label first, substring only as a fallback.
        //
        // A plain substring match is a data-corruption bug waiting to happen:
        // clicking "No" on one question matched "None of these" on a different
        // question further down the page and silently ticked it. The answer
        // that lands is then one the participant never gave, and nothing on
        // screen says so. Ambiguous substring matches are refused outright
        // rather than resolved by document order.
        const nth = Number(args[1] ?? 0)
        const r = await evaluate(
          send,
          `(() => {
             const all = [...document.querySelectorAll('button, a, label.check, summary, .tab, .likert-opt')];
             const want = ${q(args[0])};
             const norm = e => (e.innerText || '').trim().replace(/\\s+/g, ' ');
             const exact = all.filter(e => norm(e) === want.trim());
             let pool = exact;
             if (!pool.length) {
               const partial = all.filter(e => norm(e).includes(want));
               if (partial.length > 1 && ${nth} === 0) {
                 const names = partial.slice(0, 6).map(e => JSON.stringify(norm(e).slice(0, 60)));
                 return 'AMBIGUOUS: ' + partial.length + ' things contain ' + JSON.stringify(want) +
                        ' -> ' + names.join(', ') +
                        '. Use the exact label, or pass an index as the next argument.';
               }
               pool = partial;
             }
             if (!pool[${nth}]) return 'ERROR: nothing clickable matching ' + JSON.stringify(want);
             pool[${nth}].scrollIntoView({block:'center'});
             pool[${nth}].click();
             return 'clicked: ' + norm(pool[${nth}]).slice(0, 80);
           })()`,
        )
        await sleep(1400)
        console.log(r)
        break
      }

      case 'type': {
        const r = await evaluate(
          send,
          `(() => { ${SET_VALUE}
             const fields = [...document.querySelectorAll('.field')];
             const f = fields.find(x => (x.querySelector('.field-label')?.innerText || '').includes(${q(args[0])}));
             const el = (f || document).querySelector('input[type=text], input[type=email], input[type=number], textarea');
             if (!el) return 'ERROR: no text field matching ' + ${q(args[0])};
             __setValue(el, ${q(args[1])});
             return 'typed into: ' + (f?.querySelector('.field-label')?.innerText || el.name || 'field');
           })()`,
        )
        await sleep(500)
        console.log(r)
        break
      }

      case 'scale': {
        const r = await evaluate(
          send,
          `(() => { ${SET_VALUE}
             const fields = [...document.querySelectorAll('.field')];
             const f = fields.find(x => (x.querySelector('.field-label')?.innerText || '').includes(${q(args[0])}));
             if (!f) return 'ERROR: no field matching ' + ${q(args[0])};
             const range = f.querySelector('input[type=range]');
             if (range) { range.dispatchEvent(new PointerEvent('pointerdown', {bubbles:true})); __setValue(range, ${q(args[1])}); return 'set slider to ' + ${q(args[1])}; }
             const opts = [...f.querySelectorAll('.likert-opt')];
             const hit = opts.find(o => o.innerText.trim() === String(${q(args[1])}));
             if (hit) { hit.click(); return 'picked ' + ${q(args[1])}; }
             return 'ERROR: no slider or scale in ' + ${q(args[0])};
           })()`,
        )
        await sleep(400)
        console.log(r)
        break
      }

      case 'pick': {
        const r = await evaluate(
          send,
          `(() => {
             const fields = [...document.querySelectorAll('.field')];
             const f = fields.find(x => (x.querySelector('.field-label')?.innerText || '').includes(${q(args[0])}));
             if (!f) return 'ERROR: no field matching ' + ${q(args[0])};
             const btn = [...f.querySelectorAll('button')].find(b => b.innerText.trim().includes(${q(args[1])}));
             if (btn) { btn.click(); return 'picked ' + btn.innerText.trim(); }
             const lab = [...f.querySelectorAll('label')].find(l => l.innerText.includes(${q(args[1])}));
             if (lab) { lab.click(); return 'ticked ' + lab.innerText.trim().split('\\n')[0]; }
             return 'ERROR: no option ' + ${q(args[1])};
           })()`,
        )
        await sleep(400)
        console.log(r)
        break
      }

      case 'grid': {
        // grid "<row label>" "<column label>"
        const r = await evaluate(
          send,
          `(() => {
             const rows = [...document.querySelectorAll('.matrix tbody tr')];
             const row = rows.find(t => t.cells[0].innerText.includes(${q(args[0])}));
             if (!row) return 'ERROR: no row ' + ${q(args[0])};
             const heads = [...document.querySelectorAll('.matrix thead th')].map(h => h.innerText.trim());
             const idx = heads.findIndex(h => h.includes(${q(args[1])}));
             if (idx < 1) return 'ERROR: no column ' + ${q(args[1])};
             const input = row.cells[idx].querySelector('input');
             if (!input) return 'ERROR: no control at that cell';
             input.click();
             return 'set ' + row.cells[0].innerText.trim() + ' = ' + heads[idx];
           })()`,
        )
        await sleep(300)
        console.log(r)
        break
      }

      case 'upload': {
        // upload "<button text that opens the picker>" <path>
        // A native file dialog cannot be driven from page JavaScript, so the
        // file is handed to the input over the debugging protocol instead.
        await send('DOM.enable')
        const { root } = await send('DOM.getDocument', { depth: -1 })
        const { nodeIds } = await send('DOM.querySelectorAll', {
          nodeId: root.nodeId,
          selector: 'input[type=file]',
        })
        if (!nodeIds.length) {
          console.log('ERROR: no file input on this page')
          break
        }
        await send('DOM.setFileInputFiles', {
          nodeId: nodeIds[nodeIds.length - 1],
          files: [args[1]],
        })
        await sleep(1500)
        console.log(`uploaded ${args[1]}`)
        break
      }

      case 'shot': {
        const { cssContentSize } = await send('Page.getLayoutMetrics')
        await send('Emulation.setDeviceMetricsOverride', {
          width: 1440,
          height: Math.min(Math.ceil(cssContentSize.height) + 20, 6000),
          deviceScaleFactor: 1,
          mobile: false,
        })
        const { data } = await send('Page.captureScreenshot', { format: 'png' })
        const out = args[0] ?? join(STATE_DIR, `${session}.png`)
        writeFileSync(out, Buffer.from(data, 'base64'))
        await send('Emulation.clearDeviceMetricsOverride')
        console.log(out)
        break
      }

      case 'js': {
        const v = await evaluate(send, args[0])
        console.log(typeof v === 'string' ? v : JSON.stringify(v, null, 2))
        break
      }

      default:
        console.log(`ERROR: unknown command "${verb}"`)
    }
  } finally {
    close()
  }
}

main().catch((e) => {
  console.log('ERROR: ' + e.message)
  process.exit(1)
})
