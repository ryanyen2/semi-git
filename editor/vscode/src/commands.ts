// The palette verbs, and the flows the trees, the hover links and the workbench share: take out
// and bring back with a preview, name and rename, resolve a two-version choice in VS Code's own
// diff editor, save, undo, sync, push, init, and the diff editors for a step or an unsaved file.

import { execFile } from "node:child_process";
import * as path from "node:path";
import { promisify } from "node:util";
import * as vscode from "vscode";
import { Keep, Verb } from "./cliSeam";
import { Documents } from "./documents";
import { Host } from "./protocol";
import { RenderPanel } from "./renderPanel";
import { Store } from "./store";
import { Texts } from "./texts";
import { Ugh } from "./ugh";
import { WorkbenchView } from "./workbench";
import { previewWords, resultWords, undoWords } from "./words";

const pExecFile = promisify(execFile);

export interface Deps {
  root: string;
  ugh: Ugh;
  store: Store;
  docs: Documents;
  texts: Texts;
  render: RenderPanel;
  workbench: () => WorkbenchView;
}

const fail = (e: unknown) => void vscode.window.showErrorMessage(String((e as Error)?.message ?? e));

/** An address from a command argument: a string, a list of them, or a tree row carrying one. */
function addressesOf(arg: unknown): string[] | undefined {
  if (typeof arg === "string") return [arg];
  if (Array.isArray(arg) && arg.every((a) => typeof a === "string")) return arg.length ? arg : undefined;
  const address = (arg as { address?: unknown })?.address;
  return typeof address === "string" ? [address] : undefined;
}

function labels(store: Store): string[] {
  const seen = new Set<string>();
  for (const save of [...(store.snapshot.log?.saves ?? [])].reverse()) {
    for (const st of save.steps) if (st.name) seen.add(st.name);
  }
  return [...seen];
}

/** An address typed, or picked from `ugh find` as you type; the definition at the cursor first. */
function pickAddress(ugh: Ugh, title: string, initial: string[]): Promise<string | undefined> {
  const qp = vscode.window.createQuickPick();
  qp.title = title;
  qp.placeholder = "Address or words";
  const base: vscode.QuickPickItem[] = initial.map((a) => ({ label: a, description: "at cursor" }));
  qp.items = base;
  let seq = 0;
  let timer: NodeJS.Timeout | undefined;
  qp.onDidChangeValue((value) => {
    clearTimeout(timer);
    const typed = value.trim();
    const head: vscode.QuickPickItem[] = typed ? [{ label: typed, description: "address", alwaysShow: true }] : [];
    qp.items = [...head, ...base];
    if (!typed) return;
    const mine = ++seq;
    timer = setTimeout(async () => {
      qp.busy = true;
      try {
        const view = await ugh.find(typed, () => mine === seq);
        if (mine !== seq) return;
        qp.items = [
          ...head,
          ...view.hits
            .filter((h) => h.address !== typed)
            .map((h) => ({
              label: h.address,
              description: h.matched,
              detail: h.matched === "subject" || h.matched === "ask" ? h.text : undefined,
              alwaysShow: true,
            })),
        ];
      } catch {
        // the typed address stays offered
      } finally {
        if (mine === seq) qp.busy = false;
      }
    }, 200);
  });
  return new Promise((resolve) => {
    qp.onDidAccept(() => {
      resolve(qp.selectedItems[0]?.label ?? (qp.value.trim() || undefined));
      qp.hide();
    });
    qp.onDidHide(() => {
      clearTimeout(timer);
      resolve(undefined);
      qp.dispose();
    });
    qp.show();
  });
}

/** A label from those in use, or a new one typed. `none` adds a first row that picks no label. */
function pickLabel(title: string, known: string[], none?: string): Promise<string | null | undefined> {
  const qp = vscode.window.createQuickPick();
  qp.title = title;
  qp.placeholder = "Name";
  const NONE = none ? [{ label: none }] : [];
  const base = [...NONE, ...known.map((label) => ({ label }))];
  qp.items = base;
  qp.onDidChangeValue((value) => {
    const typed = value.trim();
    qp.items = typed && !known.includes(typed) ? [{ label: typed, description: "new" }, ...base] : base;
  });
  return new Promise((resolve) => {
    qp.onDidAccept(() => {
      const picked = qp.selectedItems[0]?.label ?? qp.value.trim();
      resolve(none && picked === none ? null : picked || undefined);
      qp.hide();
    });
    qp.onDidHide(() => {
      resolve(undefined);
      qp.dispose();
    });
    qp.show();
  });
}

/** `remote branch` from one input; empty means the upstream. */
async function askWhere(title: string): Promise<[string?, string?] | undefined> {
  const value = await vscode.window.showInputBox({ title, placeHolder: "remote branch, or empty for the upstream" });
  if (value === undefined) return undefined;
  const [remote, branch] = value.trim().split(/\s+/).filter(Boolean);
  return [remote, branch];
}

export function flows(d: Deps) {
  const { root, ugh, store } = d;
  const refresh = () => void store.refresh();

  async function openFile(rel: string, line?: number): Promise<void> {
    const doc = await vscode.workspace.openTextDocument(vscode.Uri.file(path.join(root, rel)));
    const editor = await vscode.window.showTextDocument(doc, { preview: true });
    if (line) {
      const pos = new vscode.Position(Math.max(0, line - 1), 0);
      editor.selection = new vscode.Selection(pos, pos);
      editor.revealRange(new vscode.Range(pos, pos), vscode.TextEditorRevealType.InCenterIfOutsideViewport);
    }
  }

  /** The file at HEAD against the file as it is. */
  async function openUnsaved(rel: string): Promise<void> {
    let head = "";
    try {
      head = (await pExecFile("git", ["show", `HEAD:${rel}`], { cwd: root, maxBuffer: 64 * 1024 * 1024 })).stdout;
    } catch {
      // a file HEAD does not have: everything in it is new
    }
    await vscode.commands.executeCommand(
      "vscode.diff",
      d.texts.put("HEAD", rel, head),
      vscode.Uri.file(path.join(root, rel)),
      `${path.basename(rel)} (HEAD ↔ file)`,
    );
  }

  /** A step's unit before and after its save. */
  async function openStep(address: string): Promise<void> {
    try {
      const show = await ugh.show(address);
      if (!show.ok) return void vscode.window.showWarningMessage(show.message);
      const rel = show.steps[0]?.unit.split("::")[0] ?? address.split("::")[0].split("@")[0];
      const join = (side: "before" | "after") => show.steps.map((s) => s[side] ?? "").join("");
      await vscode.commands.executeCommand(
        "vscode.diff",
        d.texts.put(`before ${address}`, rel, join("before")),
        d.texts.put(`after ${address}`, rel, join("after")),
        address,
      );
    } catch (e) {
      fail(e);
    }
  }

  /** Preview, confirm, apply. A refusal says why in one line. */
  async function change(verb: Verb, addresses: string[]): Promise<void> {
    let plan;
    try {
      plan = await vscode.window.withProgress({ location: vscode.ProgressLocation.Window, title: "ugh" }, () =>
        ugh.preview(verb, addresses));
    } catch (e) {
      return fail(e);
    }
    if (!plan.ok) return void vscode.window.showWarningMessage(plan.message || "Refused");
    const say = previewWords(plan);
    if (!say.apply && !say.whole) return void vscode.window.showInformationMessage(say.detail || say.message);
    const go = verb === "revert" ? "Take out" : "Bring back";
    const buttons = [...(say.apply ? [go] : []), ...(say.whole ? ["Whole"] : []), "Diff"];
    let choice = await vscode.window.showWarningMessage(say.message, { modal: true, detail: say.detail }, ...buttons);
    if (choice === "Diff") {
      for (const [file, { before, after }] of Object.entries(plan.files)) {
        await vscode.commands.executeCommand(
          "vscode.diff",
          d.texts.put("now", file, before ?? ""),
          d.texts.put(`${verb} ${plan.target}`, file, after ?? ""),
          `${path.basename(file)} (${go.toLowerCase()} ${plan.target})`,
          { preview: false },
        );
      }
      choice = await vscode.window.showInformationMessage(say.message, ...buttons.filter((b) => b !== "Diff"));
    }
    if (choice !== go && choice !== "Whole") return;
    try {
      const done = await vscode.window.withProgress(
        { location: vscode.ProgressLocation.Notification, title: say.message },
        () => ugh.apply(verb, addresses, choice === "Whole"),
      );
      const line = resultWords(done);
      if (done.applied) void vscode.window.showInformationMessage(line);
      else void vscode.window.showWarningMessage(line);
    } catch (e) {
      fail(e);
    }
    refresh();
  }

  async function name(addresses?: string[]): Promise<void> {
    const cursor = await d.docs.cursorAddress();
    const picked = addresses ?? [await pickAddress(ugh, "Name", cursor ? [cursor] : [])];
    const targets = picked.filter((a): a is string => !!a);
    if (!targets.length) return;
    const label = await pickLabel(`Name ${targets.join(", ")}`, labels(store));
    if (!label) return;
    try {
      for (const address of targets) await ugh.name(address, label);
    } catch (e) {
      fail(e);
    }
    refresh();
  }

  async function rename(label?: string): Promise<void> {
    const old = label ?? (await vscode.window.showQuickPick(labels(store), { title: "Rename" }));
    if (!old) return;
    const next = await vscode.window.showInputBox({ title: `Rename ${old}`, value: old, valueSelection: [0, old.length] });
    if (!next?.trim() || next.trim() === old) return;
    try {
      await ugh.rename(old, next.trim());
    } catch (e) {
      fail(e);
    }
    refresh();
  }

  async function keep(address: string, how: Keep): Promise<void> {
    const doc = vscode.workspace.textDocuments.find(
      (t) => t.uri.scheme === "file" && t.uri.fsPath === path.join(root, address.split("::")[0]),
    );
    if (doc?.isDirty && how === "apply") await doc.save();
    try {
      await ugh.resolve(address, how);
      void vscode.window.showInformationMessage(`Resolved ${address}`);
    } catch (e) {
      fail(e);
    }
    refresh();
  }

  /** Theirs on the left, the file on the right, then the three ways to settle it. */
  async function resolve(address?: string): Promise<void> {
    const choices = store.snapshot.status?.choices ?? [];
    const target =
      address ??
      (choices.length === 1
        ? choices[0].address
        : await vscode.window.showQuickPick(choices.map((c) => c.address), { title: "Resolve" }));
    if (!target) return;
    let versions;
    try {
      versions = await ugh.versions(target);
    } catch (e) {
      return fail(e);
    }
    for (const v of versions.choices) {
      const rel = v.address.split("::")[0];
      const file = vscode.Uri.file(path.join(root, rel));
      const now = (await vscode.workspace.openTextDocument(file)).getText();
      const at = v.ours ? now.indexOf(v.ours) : -1;
      const theirs = at >= 0 ? now.slice(0, at) + (v.theirs ?? "") + now.slice(at + v.ours!.length) : v.theirs ?? "";
      await vscode.commands.executeCommand(
        "vscode.diff",
        d.texts.put(`theirs ${v.merge}`, rel, theirs),
        file,
        `${v.address.split("::").pop()} (theirs ↔ file)`,
      );
    }
    const how = await vscode.window.showInformationMessage(target, "Take theirs", "Keep ours", "Use file as edited");
    if (how) await keep(target, how === "Take theirs" ? "theirs" : how === "Keep ours" ? "ours" : "apply");
  }

  async function save(): Promise<void> {
    const message = await vscode.window.showInputBox({ title: "Save", placeHolder: "Message" });
    if (message === undefined) return;
    const label = await pickLabel("Name this save", labels(store), "No name");
    if (label === undefined) return;
    await vscode.workspace.saveAll(false);
    try {
      const out = await vscode.window.withProgress({ location: vscode.ProgressLocation.Notification, title: "Saving" },
        () => ugh.save(message.trim() || undefined, label ?? undefined));
      if (!out.ok) void vscode.window.showErrorMessage(out.error ?? "Save refused");
      else if (!out.saved) void vscode.window.showInformationMessage(out.message ?? "Nothing to save");
      else {
        const seen = out.observed && "verdict" in out.observed ? ` · check ${out.observed.verdict === "pass" ? "passes" : "fails"}` : "";
        const n = out.steps?.length ?? 0;
        void vscode.window.showInformationMessage(`Saved ${out.commit?.slice(0, 7)} · ${n} step${n === 1 ? "" : "s"}${seen}`);
      }
    } catch (e) {
      fail(e);
    }
    refresh();
  }

  async function undo(): Promise<void> {
    let say;
    try {
      say = undoWords(await ugh.undoPreview());
    } catch (e) {
      return fail(e);
    }
    if (say.kind === "nothing") return void vscode.window.showInformationMessage(say.message);
    if (say.kind === "refused") return void vscode.window.showWarningMessage(say.message);
    if ((await vscode.window.showWarningMessage(say.message, { modal: true, detail: say.detail }, "Undo")) !== "Undo") return;
    try {
      const out = await ugh.undo();
      if (out.ok === false) void vscode.window.showWarningMessage(out.message || out.error || "Undo refused");
      else void vscode.window.showInformationMessage(out.message ? `Undone: ${out.message}` : "Undone");
    } catch (e) {
      fail(e);
    }
    refresh();
  }

  async function sync(): Promise<void> {
    const where = await askWhere("Sync");
    if (!where) return;
    try {
      const out = await vscode.window.withProgress({ location: vscode.ProgressLocation.Notification, title: "Syncing" },
        () => ugh.sync(...where));
      const names = out.name_conflicts.length ? ` · ${out.name_conflicts.length} names differ, ours kept` : "";
      const line = `Synced ${out.remote}/${out.branch}: ${out.message}${names}`;
      if (!out.choices.length) void vscode.window.showInformationMessage(line);
      else {
        const n = out.choices.length;
        const pick = await vscode.window.showWarningMessage(`${line} · ${n} two-version choice${n === 1 ? "" : "s"}`, "Resolve");
        if (pick) {
          await store.refresh();
          await resolve(out.choices[0]);
        }
      }
    } catch (e) {
      fail(e);
    }
    refresh();
  }

  async function push(): Promise<void> {
    const where = await askWhere("Push");
    if (!where) return;
    const to = where[0] ? where.filter(Boolean).join(" ") : "the upstream";
    if ((await vscode.window.showWarningMessage(`Push to ${to}?`, { modal: true }, "Push")) !== "Push") return;
    try {
      const out = await vscode.window.withProgress({ location: vscode.ProgressLocation.Notification, title: "Pushing" },
        () => ugh.push(...where));
      if (out.ok) void vscode.window.showInformationMessage(`Pushed ${out.remote}/${out.branch} ${out.pushed_sha?.slice(0, 7) ?? ""}`);
      else if (out.rejected) {
        if (await vscode.window.showWarningMessage(out.error ?? "Push rejected", "Sync")) await sync();
      } else void vscode.window.showErrorMessage(out.error ?? "Push failed");
    } catch (e) {
      fail(e);
    }
    refresh();
  }

  async function init(): Promise<void> {
    try {
      await vscode.window.withProgress({ location: vscode.ProgressLocation.Notification, title: "Reading the history" },
        () => ugh.init());
    } catch (e) {
      fail(e);
    }
    refresh();
  }

  const act: Host["act"] = async (msg) => {
    switch (msg.type) {
      case "name": return name(msg.addresses);
      case "rename": return rename(msg.label);
      case "resolve": return resolve(msg.address);
      case "openStep": return openStep(msg.address);
      case "openFile": return openFile(msg.path, msg.line).catch(fail);
      case "render": if (d.render.isOpen()) d.render.show(msg.address); return;
      case "copy": return void vscode.env.clipboard.writeText(msg.text);
      case "init": return init();
    }
  };

  return { change, name, rename, resolve, keep, save, undo, sync, push, init, openFile, openStep, openUnsaved, act };
}

export function registerCommands(context: vscode.ExtensionContext, d: Deps, f: ReturnType<typeof flows>): void {
  const reg = (id: string, fn: (...a: any[]) => unknown) =>
    context.subscriptions.push(vscode.commands.registerCommand(id, fn));
  const cursor = async () => [await d.docs.cursorAddress()].filter((a): a is string => !!a);
  const ask = async (verb: Verb, title: string, arg: unknown) => {
    const given = addressesOf(arg);
    const picked = given ?? [await pickAddress(d.ugh, title, verb === "revert" ? await cursor() : [])];
    if (picked[0]) await f.change(verb, picked as string[]);
  };

  reg("ugh.openWorkbench", () => d.workbench().focus());
  reg("ugh.revealInWorkbench", async (arg?: unknown) => d.workbench().focus(addressesOf(arg)?.[0] ?? (await cursor())[0]));
  reg("ugh.refresh", () => d.store.refresh());
  reg("ugh.takeOut", (arg?: unknown) => ask("revert", "Take out", arg));
  reg("ugh.bringBack", (arg?: unknown) => ask("restore", "Bring back", arg));
  reg("ugh.takeOutDefinition", async () => {
    const at = await cursor();
    if (at.length) await f.change("revert", at);
  });
  reg("ugh.name", (arg?: unknown) => f.name(addressesOf(arg)));
  reg("ugh.nameDefinition", async () => {
    const at = await cursor();
    if (at.length) await f.name(at);
  });
  reg("ugh.rename", (arg?: unknown) => f.rename(addressesOf(arg)?.[0]));
  reg("ugh.resolve", (arg?: unknown) => f.resolve(addressesOf(arg)?.[0]));
  for (const [id, how] of [["ugh.resolveTheirs", "theirs"], ["ugh.resolveOurs", "ours"], ["ugh.resolveApply", "apply"]] as const) {
    reg(id, (arg?: unknown) => {
      const address = addressesOf(arg)?.[0];
      if (address) return f.keep(address, how);
    });
  }
  reg("ugh.save", () => f.save());
  reg("ugh.undo", () => f.undo());
  reg("ugh.sync", () => f.sync());
  reg("ugh.push", () => f.push());
  reg("ugh.init", () => f.init());
  reg("ugh.openRenderPanel", () => d.render.open(null));
  reg("ugh.toggleBlame", () => {
    const config = vscode.workspace.getConfiguration("ugh");
    return config.update("blame.enabled", !config.get<boolean>("blame.enabled", true), vscode.ConfigurationTarget.Global);
  });
  reg("ugh.openUnsaved", (arg?: unknown) => {
    const rel = typeof arg === "string" ? arg : (arg as { path?: string })?.path;
    if (rel) return f.openUnsaved(rel);
  });
  reg("ugh.openStep", (arg?: unknown) => {
    const address = addressesOf(arg)?.[0];
    if (address) return f.openStep(address);
  });
  reg("ugh.jumpToLocation", (rel: string, line?: number) => f.openFile(rel, line).catch(fail));
}
