// The render panel's pure decisions: its documents, the dev-command substitution, and which save
// stands in for "now". Apart from `renderPanel.ts` (which imports `vscode`) so the Content Security
// Policy can be tested: a policy missing `frame-src` throws nothing, it only leaves an empty box.

import type { LogView } from "./types.ts";

/** A `${port}` template resolved against the port actually bound. */
export function devCommand(template: string, port: number): string {
  return template.replace(/\$\{port\}/g, String(port));
}

/** Strip anything that could close a tag out of text that lands in the document. */
export function plain(text: string): string {
  return String(text).replace(/[<>&]/g, "");
}

/** The newest save with steps and none of them out: restoring it plans no change. */
export function nowAnchor(log: LogView | null): string | undefined {
  return log?.saves.find((s) => s.steps.length && s.steps.every((st) => !st.out))?.sha;
}

const CSS = `
  :root { color-scheme: light dark; }
  body { margin: 0; height: 100vh; display: flex; flex-direction: column;
         font: 12px/1.5 var(--vscode-font-family); color: var(--vscode-foreground);
         background: var(--vscode-editor-background); }
  #bar { flex: 0 0 auto; display: flex; align-items: center; gap: 12px; height: 22px; padding: 0 10px;
         position: relative; background: var(--vscode-sideBar-background); }
  #label { font-family: var(--vscode-editor-font-family); }
  #detail { color: var(--vscode-descriptionForeground); }
  body.err #detail { color: var(--vscode-errorForeground); }
  #spacer { margin-left: auto; }
  button { font: inherit; color: var(--vscode-foreground); background: none; border: 0; padding: 0 4px;
           cursor: pointer; }
  button:hover { color: var(--vscode-textLink-activeForeground); }
  button:focus-visible { outline: 1px solid var(--vscode-focusBorder); }
  #prog { position: absolute; left: 0; bottom: 0; height: 1px; width: 0;
          background: var(--vscode-progressBar-background); transition: width 200ms ease-out, opacity 200ms ease-out; }
  body.busy #prog { width: 70%; }
  body.settled #prog { width: 100%; opacity: 0; }
  #frame { flex: 1 1 auto; border: 0; width: 100%; background: #fff; transition: opacity 160ms ease-out; }
  body.busy #frame { opacity: .55; }
  #msg { padding: 12px 10px; }
  code { font-family: var(--vscode-editor-font-family); }
  @media (prefers-reduced-motion: reduce) { #prog, #frame { transition: none; } }`;

/** The document shell. `extraCsp` is appended verbatim inside the policy. */
export function shellHtml(cspSource: string, body: string, extraCsp = ""): string {
  return `<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8" />
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src ${cspSource} 'unsafe-inline'; script-src 'unsafe-inline'; ${extraCsp}" />
<style>${CSS}</style></head>
<body>${body}</body></html>`;
}

export function bootHtml(cspSource: string, message: string): string {
  return shellHtml(cspSource, `<div id="bar"><span id="detail">${plain(message)}</span></div>`);
}

export function errorHtml(cspSource: string, message: string): string {
  return shellHtml(cspSource, `
<div id="bar"><span id="detail">Not running</span></div>
<div id="msg"><code>${plain(message)}</code><br><code>ugh.render.devCommand</code></div>`);
}

/**
 * The live panel. `url` must already be external-facing (`asExternalUri`): its origin is what
 * `frame-src` names, so the policy is derived from the address actually loaded.
 */
export function frameHtml(cspSource: string, url: string, label: string): string {
  const origin = new URL(url).origin;
  return shellHtml(cspSource, `
<div id="bar">
  <span id="label">${plain(label) || "now"}</span>
  <span id="detail"></span>
  <span id="spacer"></span>
  <button id="rl">Reload</button>
  <div id="prog"></div>
</div>
<iframe id="frame" src="${url}" sandbox="allow-scripts allow-same-origin allow-forms"></iframe>
<script>
  const body = document.body, frame = document.getElementById("frame");
  const label = document.getElementById("label"), detail = document.getElementById("detail");
  let settle;
  document.getElementById("rl").addEventListener("click", () => frame.contentWindow.location.reload());
  addEventListener("message", (e) => {
    const m = e.data || {};
    if (m.type === "writing") {
      body.classList.remove("settled", "err"); body.classList.add("busy");
      label.textContent = m.label; detail.textContent = "";
    } else if (m.type === "written") {
      body.classList.remove("busy", "err"); body.classList.add("settled");
      label.textContent = m.label; detail.textContent = m.files + " files";
      clearTimeout(settle);
      settle = setTimeout(() => body.classList.remove("settled"), 420);
    } else if (m.type === "error") {
      body.classList.remove("busy", "settled"); body.classList.add("err");
      detail.textContent = m.message;
    }
  });
</script>`, `frame-src ${origin};`);
}
