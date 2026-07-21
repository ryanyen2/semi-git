// In-situ semantic blame: a whole-span gutter/border decoration per `blame_view` span, colored by
// the owning feature's identity (color.ts's OKLCH generator) so a feature reads the same color
// here as it does in the feature tree. Hovering a span shows its label and feature id — the
// "detail on demand" layer that used to live in hover.ts/codelens.ts, folded into the decoration
// itself now that there's no per-node webview to link out to. Toggled by `sgt.blame.enabled`.

import * as vscode from "vscode";
import { colorForNode, colorWithAlpha } from "./color";
import { Store } from "./store";
import { BlameView } from "./types";

export class BlameController implements vscode.Disposable {
  private types = new Map<string, vscode.TextEditorDecorationType>();
  private disposables: vscode.Disposable[] = [];
  private debounce: NodeJS.Timeout | undefined;

  constructor(private store: Store) {
    this.disposables.push(
      vscode.window.onDidChangeActiveTextEditor(() => this.schedule()),
      this.store.onDidChange(() => this.schedule()),
      vscode.workspace.onDidChangeConfiguration((e) => {
        if (e.affectsConfiguration("sgt.blame")) {
          this.schedule();
        }
      }),
      // Identity colors are theme-aware (OKLCH lightness shifts light<->dark), so the cached
      // per-color decoration types are stale after a theme switch: drop them and re-render.
      vscode.window.onDidChangeActiveColorTheme(() => {
        const editor = vscode.window.activeTextEditor;
        if (editor) {
          this.clearAll(editor);
        }
        this.types.forEach((t) => t.dispose());
        this.types.clear();
        this.schedule();
      })
    );
  }

  private enabled(): boolean {
    return vscode.workspace.getConfiguration("sgt").get<boolean>("blame.enabled", true);
  }

  private typeFor(featureId: string): vscode.TextEditorDecorationType {
    let t = this.types.get(featureId);
    if (!t) {
      const color = colorForNode(featureId);
      t = vscode.window.createTextEditorDecorationType({
        isWholeLine: true,
        backgroundColor: colorWithAlpha(featureId, 0.07),
        borderWidth: "0 0 0 2px",
        borderStyle: "solid",
        borderColor: color,
        overviewRulerColor: color,
        overviewRulerLane: vscode.OverviewRulerLane.Full,
      });
      this.types.set(featureId, t);
    }
    return t;
  }

  private schedule(): void {
    clearTimeout(this.debounce);
    this.debounce = setTimeout(() => void this.render(), 120);
  }

  private clearAll(editor: vscode.TextEditor): void {
    for (const t of this.types.values()) {
      editor.setDecorations(t, []);
    }
  }

  async render(): Promise<void> {
    const editor = vscode.window.activeTextEditor;
    if (!editor) {
      return;
    }
    // Only real workspace files have a semantic blame. Focusing the "semi-git" Output panel,
    // a diff view, a git:// gutter, or any other virtual document makes it the active editor;
    // running `sgt blame` on that URI fails and appends to the output channel, which re-reveals
    // it and re-fires this handler -- a self-sustaining loop. Gate to file scheme (matches the
    // `{ scheme: "file" }` selector every other read surface registers with).
    if (editor.document.uri.scheme !== "file") {
      return;
    }
    const rel = vscode.workspace.asRelativePath(editor.document.uri, false);
    let blame: BlameView;
    try {
      blame = await this.store.blame(rel);
    } catch {
      return;
    }
    this.clearAll(editor);
    if (blame.error || !this.enabled()) {
      return;
    }
    const byType = new Map<vscode.TextEditorDecorationType, vscode.DecorationOptions[]>();
    for (const s of blame.spans) {
      const t = this.typeFor(s.feature_id);
      const range = new vscode.Range(s.start_line - 1, 0, s.end_line - 1, 0);
      const hoverMessage = new vscode.MarkdownString(`${escape(s.label)} (\`${s.feature_id}\`)`);
      const opts = byType.get(t) ?? [];
      opts.push({ range, hoverMessage });
      byType.set(t, opts);
    }
    for (const [t, opts] of byType) {
      editor.setDecorations(t, opts);
    }
  }

  dispose(): void {
    this.disposables.forEach((d) => d.dispose());
    this.types.forEach((t) => t.dispose());
  }
}

function escape(s: string): string {
  return s.replace(/[<>]/g, (c) => (c === "<" ? "&lt;" : "&gt;"));
}
