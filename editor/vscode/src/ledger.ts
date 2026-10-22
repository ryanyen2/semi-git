// The ledger's page: the history as a document of what was asked, newest first, with the feature
// lanes in the gutter (docs/design/2026-10-18-workbench-redesign.md §3, §7.2). The workbench panel
// shows it by default (`WorkbenchProvider`, mode "ledger") and answers its messages with the same
// handlers the timeline uses, so the two are two readings of one state on one host.
//
// Message protocol (media/ledger.js): `ready` -> `state`; `requestChange {verb, ref, seq}` ->
// `changeResult` (carrying `view`, the whole dry run: files, the subtraction report, `focus`,
// `so_what`); `applyStaged {verb, ref}` -> `applyProgress` phases; `openFile`, `openChangeDiff`,
// `dailyLoop`, `resolveFork`; `openWorkbench` swaps the panel to the timeline.

import * as vscode from "vscode";

export function ledgerHtml(webview: vscode.Webview, extensionUri: vscode.Uri, root: string): string {
  const uri = (name: string) => webview.asWebviewUri(vscode.Uri.joinPath(extensionUri, "media", name));
  const nonce = `${Date.now().toString(36)}${Math.random().toString(36).slice(2)}`;
  const repo = root.split("/").slice(-1)[0];
  return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src ${webview.cspSource}; script-src 'nonce-${nonce}';" />
<link rel="stylesheet" href="${uri("ledger.css")}" />
<title>ugh Ledger</title>
</head>
<body>
<div id="wb">
  <header class="top">
    <div class="brand">ugh<span class="repo">${escapeHtml(repo)}</span></div>
    <div class="find-wrap">
      <input id="find" class="find" type="search" spellcheck="false" autocomplete="off" placeholder="find a function, a feature, or the words you asked for" />
      <div id="findResults" class="find-results" hidden></div>
    </div>
    <div class="actions">
      <button id="workbenchBtn" class="tbtn" title="show the same history as a timeline">Timeline</button>
      <button id="undoBtn" class="tbtn" title="undo the last thing ugh did">Undo</button>
      <button id="saveBtn" class="tbtn primary" title="record your edits as a save">Save</button>
    </div>
  </header>
  <div id="main" class="main">
    <div class="page">
      <div id="themesLine" class="themes-line"></div>
      <div id="strip" class="strip" hidden></div>
      <div id="rows" class="rows"></div>
      <div id="foot" class="foot"></div>
    </div>
    <aside id="note" class="note" hidden></aside>
  </div>
  <footer class="status"><span id="statusLeft" class="s-left"></span><span id="statusRight" class="s-right"></span></footer>
</div>
<script nonce="${nonce}" src="${uri("motion.js")}"></script>
<script nonce="${nonce}" src="${uri("ledger.js")}"></script>
</body>
</html>`;
}

function escapeHtml(s: string): string {
  return s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c] as string));
}
