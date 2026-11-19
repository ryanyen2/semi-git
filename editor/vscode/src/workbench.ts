// The workbench: a webview in the bottom panel serving `media/workbench.js` and `workbench.css`,
// speaking the protocol in `protocol.ts`. It receives the store's state after every refresh and
// the client's busy verb around every mutating call.

import { randomBytes } from "node:crypto";
import * as vscode from "vscode";
import { FromWebview, Host, Protocol, ToWebview, workbenchHtml } from "./protocol";
import { Store } from "./store";
import { Ugh } from "./ugh";

export class WorkbenchView implements vscode.WebviewViewProvider, vscode.Disposable {
  private view: vscode.WebviewView | undefined;
  private ready = false;
  private pendingFocus: string | undefined;
  private protocol: Protocol;
  private disposables: vscode.Disposable[] = [];

  constructor(
    private readonly extensionUri: vscode.Uri,
    store: Store,
    ugh: Ugh,
    act: Host["act"],
  ) {
    this.protocol = new Protocol({
      snapshot: () => store.snapshot,
      preview: (verb, addresses, whole, wanted) => ugh.preview(verb, addresses, whole, wanted),
      apply: (verb, addresses, whole) => ugh.apply(verb, addresses, whole),
      show: (address, wanted) => ugh.show(address, wanted),
      find: (query, wanted) => ugh.find(query, wanted),
      refresh: () => store.refresh(),
      post: (msg) => this.post(msg),
      act,
    });
    const sub = store.onChange((s) => this.post({ type: "state", ...s }));
    this.disposables.push(
      { dispose: () => sub.dispose() },
      ugh.onBusy((verb) => this.post({ type: "busy", verb })),
    );
  }

  resolveWebviewView(view: vscode.WebviewView): void {
    this.view = view;
    this.ready = false;
    const media = vscode.Uri.joinPath(this.extensionUri, "media");
    view.webview.options = { enableScripts: true, localResourceRoots: [media] };
    view.webview.html = workbenchHtml(
      view.webview.cspSource,
      view.webview.asWebviewUri(vscode.Uri.joinPath(media, "workbench.js")).toString(),
      view.webview.asWebviewUri(vscode.Uri.joinPath(media, "workbench.css")).toString(),
      randomBytes(16).toString("base64"),
    );
    this.disposables.push(
      view.webview.onDidReceiveMessage((msg: FromWebview) => {
        if (msg?.type === "ready") {
          this.ready = true;
          void this.protocol.receive(msg).then(() => this.flushFocus());
          return;
        }
        void this.protocol.receive(msg);
      }),
      view.onDidDispose(() => {
        this.view = undefined;
        this.ready = false;
      }),
    );
  }

  /** Open the workbench and select `address` in it. */
  async focus(address?: string): Promise<void> {
    this.pendingFocus = address;
    await vscode.commands.executeCommand("ughWorkbench.focus");
    this.flushFocus();
  }

  private flushFocus(): void {
    if (this.pendingFocus !== undefined && this.ready) {
      this.post({ type: "focus", address: this.pendingFocus });
      this.pendingFocus = undefined;
    }
  }

  private post(msg: ToWebview): void {
    if (this.view && this.ready) void this.view.webview.postMessage(msg);
  }

  dispose(): void {
    this.disposables.forEach((d) => d.dispose());
  }
}
