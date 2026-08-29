// The render panel: the running product, beside the graph, showing the frontier the playhead is on.
//
// Dragging the playhead already folds an arbitrary frontier (`scrubPlayhead` -> `store.foldAt`);
// this makes that fold *run*. The mechanism is one CLI flag and one dev server:
//
//   sgt advanced fold --at <spec> --out <dir>     materialize the frontier onto a scratch tree
//   vite <dir>                                    serve it, and hot-replace what changed
//
// `--out` is a sync rather than a wipe, so the `node_modules` symlink and the dev server's own
// caches survive every scrub, and a file that left the frontier is deleted rather than left behind
// to be imported by code that no longer exists. That combination is what makes scrubbing feel like
// scrubbing instead of like thirteen rebuilds.
//
// WHY A SCRATCH TREE AND NOT THE WORKSPACE
//
// The obvious implementation folds onto the user's own working tree. It is also destructive and
// unrecoverable: a scrub to episode 4 would delete nine of their files, and a scrub is a *drag*,
// firing continuously. The scratch tree means the playhead can be dragged across the whole history
// without touching a single file the user owns.
//
// WHAT THIS CANNOT DO
//
// Rendering a past frontier executes that frontier's code. That is acceptable pointed at your own
// repo and is not acceptable as a general feature pointed at someone else's history without a
// sandbox. It is also not free: a fold is ~0.3s at demo scale and 15s on a repo the size of
// semi-git, so the panel debounces and always shows which frontier it is actually displaying
// rather than pretending to be live.

import { spawn, ChildProcess } from "node:child_process";
import * as fs from "node:fs";
import * as http from "node:http";
import * as net from "node:net";
import * as path from "node:path";
import * as vscode from "vscode";
import { bootHtml, devCommand, errorHtml, frameHtml } from "./renderSeam";
import { FoldFrontier, foldAtSpec, StaleRequestError } from "./sgt";
import { Store } from "./store";

/** How long to wait for the dev server to answer before giving up and saying so. */
const READY_TIMEOUT_MS = 30_000;
/** A scrub fires per pointer-move; this is the quiet period before we spend a fold on it. */
const SCRUB_DEBOUNCE_MS = 120;

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
      if (Date.now() > deadline) return reject(new Error("dev server did not become ready"));
      const req = http.get({ host: "127.0.0.1", port, path: "/", timeout: 1000 }, (res) => {
        res.resume();
        // Any answer at all means the server is listening; a 404 on `/` is still a live server.
        resolve();
      });
      req.on("error", () => setTimeout(tick, 250));
      req.on("timeout", () => { req.destroy(); setTimeout(tick, 250); });
    };
    tick();
  });
}

export class RenderPanel implements vscode.Disposable {
  private panel?: vscode.WebviewPanel;
  private server?: ChildProcess;
  private port?: number;
  private foldDir?: string;
  private seq = 0;
  private debounce?: NodeJS.Timeout;
  private starting?: Promise<void>;
  private lastLabel = "";

  constructor(
    private readonly context: vscode.ExtensionContext,
    private readonly store: Store,
    private readonly root: () => string | undefined,
  ) {}

  /** Whether a panel is up. The workbench asks before spending a fold on a scrub. */
  isOpen(): boolean {
    return !!this.panel && !!this.foldDir;
  }

  /** Open (or reveal) the panel and show `frontier`. */
  async open(frontier: FoldFrontier = { current: true }): Promise<void> {
    if (this.panel) {
      this.panel.reveal(vscode.ViewColumn.Beside, true);
      await this.show(frontier);
      return;
    }
    const workspace = this.root();
    if (!workspace) {
      void vscode.window.showWarningMessage("sgt: open a folder with an sgt repo first.");
      return;
    }

    this.panel = vscode.window.createWebviewPanel(
      "sgtRender", "sgt — running app", { viewColumn: vscode.ViewColumn.Beside, preserveFocus: true },
      { enableScripts: true, retainContextWhenHidden: true },
    );
    this.panel.onDidDispose(() => this.teardown(), null, this.context.subscriptions);
    this.panel.webview.onDidReceiveMessage((msg) => {
      if (msg?.type === "reload") void this.reload();
    });
    this.panel.webview.html = bootHtml(this.panel.webview.cspSource, "starting the dev server\u2026");

    try {
      await (this.starting ??= this.start(workspace, frontier));
    } catch (e: any) {
      // A designed failure state, not a stack trace in a blank panel: say what broke and offer the
      // one action that might fix it.
      this.panel.webview.html = errorHtml(this.panel.webview.cspSource, String(e?.message ?? e));
      this.starting = undefined;
      // Stop answering `isOpen()`. The panel is still up (showing why it failed), but nothing is
      // serving the directory, so every later scrub would spend a fold writing into a tree no one
      // reads -- work that looks like the feature functioning.
      this.foldDir = undefined;
      this.server?.kill();
      this.server = undefined;
    }
  }

  /** Point the panel at `frontier`. Debounced: a drag produces far more calls than folds. */
  show(frontier: FoldFrontier): Promise<void> {
    if (!this.panel || !this.foldDir) return Promise.resolve();
    if (this.debounce) clearTimeout(this.debounce);
    return new Promise((resolve) => {
      this.debounce = setTimeout(() => void this.fold(frontier).then(resolve), SCRUB_DEBOUNCE_MS);
    });
  }

  private async start(workspace: string, frontier: FoldFrontier): Promise<void> {
    // Scratch tree under the extension's own storage -- never inside the user's repo, where it
    // would be mined, gitignored-or-not, and would show up in their own feature graph.
    const dir = path.join(this.context.globalStorageUri.fsPath, "render", path.basename(workspace));
    await fs.promises.mkdir(dir, { recursive: true });
    this.foldDir = dir;

    await this.fold(frontier, /* silent */ true);

    // `node_modules` is gitignored, therefore `ignored` tier, therefore correctly absent from every
    // fold. Link rather than copy: a copy is hundreds of megabytes and, worse, a second React would
    // make every hook call throw.
    const link = path.join(dir, "node_modules");
    const real = path.join(workspace, "node_modules");
    if (fs.existsSync(real) && !fs.existsSync(link)) {
      await fs.promises.symlink(real, link, "dir").catch(() => undefined);
    }

    this.port = await freePort();
    const command = devCommand(
      vscode.workspace.getConfiguration("sgt").get<string>(
        "render.devCommand", "npx vite --host 127.0.0.1 --port ${port} --strictPort",
      ),
      this.port,
    );

    this.server = spawn(command, { cwd: dir, shell: true, env: { ...process.env, BROWSER: "none" } });
    this.server.stdout?.on("data", () => undefined);
    this.server.stderr?.on("data", () => undefined);

    await waitForServer(this.port, Date.now() + READY_TIMEOUT_MS);
    // `asExternalUri` so the panel also works over Remote/Codespaces, where 127.0.0.1 in the
    // extension host is not 127.0.0.1 in the webview.
    const external = await vscode.env.asExternalUri(vscode.Uri.parse(`http://127.0.0.1:${this.port}`));
    this.panel!.webview.html = frameHtml(this.panel!.webview.cspSource, external.toString(), this.lastLabel);
  }

  private async fold(frontier: FoldFrontier, silent = false): Promise<void> {
    if (!this.foldDir) return;
    const seq = ++this.seq;
    const label = foldAtSpec(frontier);
    if (!silent) this.post({ type: "folding", label });
    try {
      const view = await this.store.foldTo(frontier, this.foldDir, () => this.seq === seq);
      if (this.seq !== seq) return; // the drag moved on; the newer frontier answers
      this.lastLabel = label;
      const files = view.written ?? Object.keys(view.files ?? {}).length;
      this.post({ type: "folded", label, files, ops: view.op_count });
    } catch (e: any) {
      if (e instanceof StaleRequestError) return;
      if (this.seq !== seq) return;
      this.post({ type: "foldError", label, message: String(e?.message ?? e) });
    }
  }

  private async reload(): Promise<void> {
    this.post({ type: "reload" });
  }

  private post(msg: unknown): void {
    void this.panel?.webview.postMessage(msg);
  }

  private teardown(): void {
    if (this.debounce) clearTimeout(this.debounce);
    this.server?.kill();
    this.server = undefined;
    this.panel = undefined;
    this.starting = undefined;
    this.foldDir = undefined;
    this.port = undefined;
  }

  dispose(): void {
    this.teardown();
  }

}
