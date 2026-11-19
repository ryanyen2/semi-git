// The running app: a dev server over a scratch tree, showing the code with some steps out.
//
// `ugh revert <address> --emit --out <dir>` writes the tree that take-out would leave onto <dir>,
// without touching the repo. The write is a sync, not a wipe: files that left are deleted, and
// what the tree does not own (a `node_modules` link, the dev server's cache) stays, so the server
// hot-replaces instead of restarting. The workbench's playhead drives it: at save s the address is
// `<next save>..`. Nothing renders while the panel is closed.
//
// The scratch tree lives in the extension's storage, never in the repo, and running it executes
// that version of the code.

import { ChildProcess, spawn } from "node:child_process";
import * as fs from "node:fs";
import * as http from "node:http";
import * as net from "node:net";
import * as path from "node:path";
import * as vscode from "vscode";
import { StaleRequestError } from "./cliSeam";
import { bootHtml, devCommand, errorHtml, frameHtml, nowAnchor } from "./renderSeam";
import { Store } from "./store";
import { Ugh } from "./ugh";

const READY_TIMEOUT_MS = 30_000;
/** A drag of the playhead fires per column; this is the quiet period before a write is spent on it. */
const DEBOUNCE_MS = 120;

function freePort(): Promise<number> {
  return new Promise((resolve, reject) => {
    const srv = net.createServer();
    srv.once("error", reject);
    srv.listen(0, "127.0.0.1", () => {
      const addr = srv.address();
      const port = typeof addr === "object" && addr ? addr.port : 0;
      srv.close(() => (port ? resolve(port) : reject(new Error("no free port"))));
    });
  });
}

function waitForServer(port: number, deadline: number): Promise<void> {
  return new Promise((resolve, reject) => {
    const tick = () => {
      if (Date.now() > deadline) return reject(new Error("the dev server did not answer"));
      const req = http.get({ host: "127.0.0.1", port, path: "/", timeout: 1000 }, (res) => {
        res.resume();
        resolve();
      });
      req.on("error", () => setTimeout(tick, 250));
      req.on("timeout", () => {
        req.destroy();
        setTimeout(tick, 250);
      });
    };
    tick();
  });
}

export class RenderPanel implements vscode.Disposable {
  private panel?: vscode.WebviewPanel;
  private server?: ChildProcess;
  private dir?: string;
  private seq = 0;
  private debounce?: NodeJS.Timeout;
  private starting?: Promise<void>;
  private label = "now";

  constructor(
    private readonly context: vscode.ExtensionContext,
    private readonly root: string,
    private readonly ugh: Ugh,
    private readonly store: Store,
  ) {}

  isOpen(): boolean {
    return !!this.panel && !!this.dir;
  }

  /** Open (or reveal) the panel on `address`, null being now. */
  async open(address: string | null): Promise<void> {
    if (this.panel) {
      this.panel.reveal(vscode.ViewColumn.Beside, true);
      return this.show(address);
    }
    this.panel = vscode.window.createWebviewPanel(
      "ughRender", "Running app", { viewColumn: vscode.ViewColumn.Beside, preserveFocus: true },
      { enableScripts: true, retainContextWhenHidden: true },
    );
    this.panel.onDidDispose(() => this.teardown(), null, this.context.subscriptions);
    this.panel.webview.html = bootHtml(this.panel.webview.cspSource, "Starting");
    try {
      await (this.starting ??= this.start(address));
    } catch (e: any) {
      if (!this.panel) return;
      this.panel.webview.html = errorHtml(this.panel.webview.cspSource, String(e?.message ?? e));
      this.starting = undefined;
      this.dir = undefined;
      this.server?.kill();
      this.server = undefined;
    }
  }

  /** Point the panel at `address`, debounced. */
  show(address: string | null): Promise<void> {
    if (!this.panel || !this.dir) return Promise.resolve();
    clearTimeout(this.debounce);
    return new Promise((resolve) => {
      this.debounce = setTimeout(() => void this.write(address).then(() => resolve()), DEBOUNCE_MS);
    });
  }

  private async start(address: string | null): Promise<void> {
    const dir = path.join(this.context.globalStorageUri.fsPath, "render", path.basename(this.root));
    await fs.promises.mkdir(dir, { recursive: true });
    this.dir = dir;
    const error = await this.write(address, true);
    if (error) throw new Error(error);

    // `node_modules` is ignored, so never in the tree. Link it rather than copy: a second copy of
    // a framework makes its runtime checks fail.
    const link = path.join(dir, "node_modules");
    const real = path.join(this.root, "node_modules");
    if (fs.existsSync(real) && !fs.existsSync(link)) {
      await fs.promises.symlink(real, link, "dir").catch(() => undefined);
    }

    const port = await freePort();
    const command = devCommand(
      vscode.workspace.getConfiguration("ugh").get<string>(
        "render.devCommand", "npx vite --host 127.0.0.1 --port ${port} --strictPort",
      ),
      port,
    );
    this.server = spawn(command, { cwd: dir, shell: true, env: { ...process.env, BROWSER: "none" } });
    this.server.stdout?.resume();
    this.server.stderr?.resume();
    await waitForServer(port, Date.now() + READY_TIMEOUT_MS);
    // `asExternalUri`, so it also works over Remote, where the host's 127.0.0.1 is not the webview's.
    const external = await vscode.env.asExternalUri(vscode.Uri.parse(`http://127.0.0.1:${port}`));
    if (this.panel) this.panel.webview.html = frameHtml(this.panel.webview.cspSource, external.toString(), this.label);
  }

  /** Write the tree for `address`; returns the refusal, if any. */
  private async write(address: string | null, silent = false): Promise<string | undefined> {
    if (!this.dir) return;
    const seq = ++this.seq;
    const label = address ?? "now";
    if (!silent) this.post({ type: "writing", label });
    // "Now" has no take-out of its own: bringing back a save that has nothing out plans no change,
    // and the preview of no change is the tree as it is.
    const anchor = address ? undefined : nowAnchor(this.store.snapshot.log);
    if (!address && !anchor) {
      this.post({ type: "error", label, message: "no save to write the tree from" });
      return "no save to write the tree from";
    }
    try {
      const plan = address
        ? await this.ugh.emitTo("revert", address, this.dir, () => this.seq === seq)
        : await this.ugh.emitTo("restore", anchor!, this.dir, () => this.seq === seq);
      if (this.seq !== seq) return;
      if (!plan.out) {
        this.post({ type: "error", label, message: plan.message || "nothing written" });
        return plan.message || "nothing written";
      }
      this.label = label;
      this.post({ type: "written", label, files: plan.out.written });
    } catch (e: any) {
      if (e instanceof StaleRequestError || this.seq !== seq) return;
      this.post({ type: "error", label, message: String(e?.message ?? e) });
      return String(e?.message ?? e);
    }
  }

  private post(msg: unknown): void {
    void this.panel?.webview.postMessage(msg);
  }

  private teardown(): void {
    clearTimeout(this.debounce);
    this.server?.kill();
    this.server = undefined;
    this.panel = undefined;
    this.starting = undefined;
    this.dir = undefined;
  }

  dispose(): void {
    this.teardown();
  }
}
