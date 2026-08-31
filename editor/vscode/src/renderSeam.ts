// The render panel's pure decisions: the webview documents, and the dev-command substitution.
//
// Same reason `cliSeam.ts` exists. `renderPanel.ts` imports `vscode` and so cannot be loaded
// outside the extension host, which would leave the riskiest part of the panel -- its Content
// Security Policy -- untestable. A CSP that forgets `frame-src` does not throw: it renders a
// panel-shaped hole where the app should be, and the failure looks like "the dev server is slow".

/** A `${port}` template resolved against the port actually bound. */
export function devCommand(template: string, port: number): string {
  return template.replace(/\$\{port\}/g, String(port));
}

/** Strip anything that could close a tag out of text that lands in the document. */
export function plain(text: string): string {
  return String(text).replace(/[<>&]/g, "");
}

const CSS = `
  :root { color-scheme: light dark; }
  body { margin: 0; height: 100vh; display: flex; flex-direction: column;
         font: 12px/1.5 var(--vscode-font-family); color: var(--vscode-foreground);
         background: var(--vscode-editor-background); }
  #bar { flex: 0 0 auto; display: flex; align-items: center; gap: 8px; padding: 5px 10px;
         border-bottom: 1px solid var(--vscode-panel-border); position: relative; }
  #dot { width: 7px; height: 7px; border-radius: 50%; background: var(--vscode-charts-green);
         transition: background 160ms ease; flex: 0 0 auto; }
  body.busy #dot { background: var(--vscode-charts-yellow); }
  body.err #dot { background: var(--vscode-errorForeground); }
  #what { font-weight: 600; }
  #detail { opacity: .65; }
  #spacer { margin-left: auto; }
  button { font: inherit; color: var(--vscode-button-secondaryForeground);
           background: var(--vscode-button-secondaryBackground); border: 0; border-radius: 3px;
           padding: 2px 8px; cursor: pointer; }
  /* Intermediate progress: a hairline that only exists while a fold is in flight. */
  #prog { position: absolute; left: 0; bottom: -1px; height: 2px; width: 0;
          background: var(--vscode-progressBar-background);
          transition: width 200ms ease, opacity 200ms ease; }
  body.busy #prog { width: 70%; }
  body.settled #prog { width: 100%; opacity: 0; }
  #frame { flex: 1 1 auto; border: 0; width: 100%; background: #fff;
           transition: opacity 160ms ease, filter 160ms ease; }
  /* Feedforward: while the next frontier is being folded the picture is visibly *stale*, so a
     slow fold never reads as "this is what that frontier looks like". */
  body.busy #frame { opacity: .55; filter: saturate(.6); }
  #msg { padding: 18px 20px; max-width: 62ch; }
  #msg h2 { font-size: 13px; margin: 0 0 6px; }
  #msg p { opacity: .8; }
  code { font-family: var(--vscode-editor-font-family); }`;

/** The document shell. `extraCsp` is appended verbatim inside the policy. */
export function shellHtml(cspSource: string, body: string, extraCsp = ""): string {
  return `<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8" />
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src ${cspSource} 'unsafe-inline'; script-src 'unsafe-inline'; ${extraCsp}" />
<style>${CSS}</style></head>
<body>${body}</body></html>`;
}

export function bootHtml(cspSource: string, message: string): string {
  return shellHtml(cspSource, `<div id="bar"><span id="dot"></span><span id="what">${plain(message)}</span></div>`);
}

export function errorHtml(cspSource: string, message: string): string {
  return shellHtml(cspSource, `
<div id="bar"><span id="dot"></span><span id="what">the app is not running</span></div>
<div id="msg">
  <h2>Could not start the dev server</h2>
  <p><code>${plain(message)}</code></p>
  <p>The panel runs one command in a scratch copy of the folded tree. If this project does not
     use Vite, set <code>sgt.render.devCommand</code> &mdash; <code>\${port}</code> is substituted.</p>
</div>`);
}

/**
 * The live panel. `url` must already be an external-facing URL (`asExternalUri`), because its
 * origin is what `frame-src` has to name -- deriving the policy from the address we are actually
 * going to load is the only way the two cannot drift apart.
 */
export function frameHtml(cspSource: string, url: string, label: string): string {
  const origin = new URL(url).origin;
  return shellHtml(cspSource, `
<div id="bar">
  <span id="dot"></span>
  <span id="what">frontier <span id="label">${plain(label) || "now"}</span></span>
  <span id="detail"></span>
  <span id="spacer"></span>
  <button id="rl">reload</button>
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
    if (m.type === "folding") {
      body.classList.remove("settled", "err"); body.classList.add("busy");
      label.textContent = m.label; detail.textContent = "folding\\u2026";
    } else if (m.type === "folded") {
      body.classList.remove("busy", "err"); body.classList.add("settled");
      label.textContent = m.label;
      detail.textContent = m.files + " files \\u00b7 " + m.ops + " ops";
      // The dev server hot-replaces what changed on its own. Settle the bar after the same beat
      // so the "after effect" lines up with the picture rather than preceding it.
      clearTimeout(settle);
      settle = setTimeout(() => body.classList.remove("settled"), 420);
    } else if (m.type === "foldError") {
      body.classList.remove("busy", "settled"); body.classList.add("err");
      detail.textContent = m.message;
    } else if (m.type === "reload") {
      frame.contentWindow.location.reload();
    }
  });
</script>`, `frame-src ${origin};`);
}
