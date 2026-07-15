// Opt-in inlay hints: `‹feature-label ·N ops›` appended to each symbol's definition line, from
// the same blame/map data `blame.ts`/`hover.ts` already fetch. Off by default
// (`sgt.inlayHints.enabled`) -- a hint per definition is a lot of visual noise for a whole-file
// view, unlike the on-hover/gutter surfaces that only show on demand.

import * as vscode from "vscode";
import { Store } from "./store";

export class FeatureInlayHintsProvider implements vscode.InlayHintsProvider {
  private readonly emitter = new vscode.EventEmitter<void>();
  readonly onDidChangeInlayHints = this.emitter.event;
  private readonly sub: vscode.Disposable;

  constructor(private store: Store) {
    this.sub = this.store.onDidChange(() => this.emitter.fire());
  }

  async provideInlayHints(document: vscode.TextDocument, range: vscode.Range): Promise<vscode.InlayHint[]> {
    if (!vscode.workspace.getConfiguration("sgt").get<boolean>("inlayHints.enabled", false)) {
      return [];
    }
    const rel = vscode.workspace.asRelativePath(document.uri, false);
    let blame;
    let map;
    try {
      [blame, map] = await Promise.all([this.store.blame(rel), this.store.map()]);
    } catch {
      return [];
    }
    const hints: vscode.InlayHint[] = [];
    for (const span of blame.spans) {
      const line = span.start_line - 1;
      if (line < range.start.line || line > range.end.line) {
        continue;
      }
      const opCount = map.nodes.find((n) => n.id === span.feature_id)?.op_count;
      const label = `‹${span.label}${opCount !== undefined ? ` ·${opCount} ops` : ""}›`;
      const pos = new vscode.Position(line, document.lineAt(line).text.length);
      const hint = new vscode.InlayHint(pos, label, vscode.InlayHintKind.Type);
      hint.paddingLeft = true;
      hints.push(hint);
    }
    return hints;
  }

  dispose(): void {
    this.sub.dispose();
    this.emitter.dispose();
  }
}
