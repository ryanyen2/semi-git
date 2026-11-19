// The units of an open document, for the hover, the inlay hints, the blame tint, the diagnostics
// and the commands that act on the definition at the cursor. One `ugh show <path>` per file, from
// the store's cache; the placement is recomputed only when the text or that read changes.

import * as path from "node:path";
import * as vscode from "vscode";
import { Store } from "./store";
import { ShowView } from "./types";
import { locate, Unit, unitAt } from "./units";

export interface DocUnits {
  show: Extract<ShowView, { ok: true }>;
  units: Unit[];
}

export class Documents {
  private placed = new WeakMap<ShowView, Map<string, { version: number; units: Unit[] }>>();

  constructor(private readonly root: string, private readonly store: Store) {}

  /** The repo-relative path of a file in this repo, or undefined for anything else. */
  relPath(uri: vscode.Uri): string | undefined {
    if (uri.scheme !== "file") return undefined;
    const rel = path.relative(this.root, uri.fsPath);
    if (!rel || rel.startsWith("..") || path.isAbsolute(rel)) return undefined;
    const posix = rel.split(path.sep).join("/");
    return posix.startsWith(".git/") || posix.startsWith(".sgt/") ? undefined : posix;
  }

  async units(doc: vscode.TextDocument): Promise<DocUnits | undefined> {
    const rel = this.relPath(doc.uri);
    if (!rel || !this.store.snapshot.log) return undefined;
    let show: ShowView;
    try {
      show = await this.store.show(rel);
    } catch {
      return undefined;
    }
    if (!show.ok) return undefined;
    let byDoc = this.placed.get(show);
    if (!byDoc) this.placed.set(show, (byDoc = new Map()));
    const key = doc.uri.toString();
    let hit = byDoc.get(key);
    if (!hit || hit.version !== doc.version) {
      hit = { version: doc.version, units: locate(show, doc.getText()) };
      byDoc.set(key, hit);
    }
    return { show, units: hit.units };
  }

  /** The unit at a 0-based line. */
  async at(doc: vscode.TextDocument, line: number): Promise<(DocUnits & { unit: Unit }) | undefined> {
    const all = await this.units(doc);
    const unit = all && unitAt(all.units, line + 1);
    return all && unit ? { ...all, unit } : undefined;
  }

  /** The address of the definition (or whole file) at the active editor's cursor. */
  async cursorAddress(): Promise<string | undefined> {
    const editor = vscode.window.activeTextEditor;
    if (!editor) return undefined;
    const hit = await this.at(editor.document, editor.selection.active.line);
    return hit && hit.unit.kind !== "head" ? hit.unit.unit : undefined;
  }
}
