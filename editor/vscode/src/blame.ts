// In-situ semantic blame. Three layers, mirroring GitLens's progressive disclosure:
//   * always-on: a quiet end-of-line annotation on the *current* line + a status-bar owner,
//   * opt-in heatmap: a per-feature gutter band + overview-ruler color across the whole file,
//   * detail on demand: the rich hover (hover.ts) and CodeLens (codelens.ts).
// All color comes from the deterministic per-id hash, so a feature reads the same everywhere.

import * as vscode from "vscode";
import { colorForNode, colorWithAlpha } from "./color";
import { Store } from "./store";
import { BlameView } from "./types";
import { ownerAt, truncate } from "./util";

export class BlameController implements vscode.Disposable {
  private currentLineType: vscode.TextEditorDecorationType;
  private heatTypes = new Map<string, vscode.TextEditorDecorationType>();
  private status: vscode.StatusBarItem;
  private disposables: vscode.Disposable[] = [];
  private debounce: NodeJS.Timeout | undefined;

  constructor(private store: Store) {
    this.currentLineType = vscode.window.createTextEditorDecorationType({
      rangeBehavior: vscode.DecorationRangeBehavior.ClosedOpen,
      after: { margin: "0 0 0 2em", color: new vscode.ThemeColor("editorCodeLens.foreground") },
    });
    this.status = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 100);
    this.status.command = "sgt.showGraph";
    this.disposables.push(
      this.currentLineType,
      this.status,
      vscode.window.onDidChangeActiveTextEditor(() => this.schedule()),
      vscode.window.onDidChangeTextEditorSelection(() => this.schedule()),
      this.store.onDidChange(() => this.schedule()),
      vscode.workspace.onDidChangeConfiguration((e) => {
        if (e.affectsConfiguration("sgt")) {
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
        this.heatTypes.forEach((t) => t.dispose());
        this.heatTypes.clear();
        this.schedule();
      })
    );
  }

  private cfg() {
    const c = vscode.workspace.getConfiguration("sgt");
    return {
      blame: c.get<boolean>("blame.enabled", true),
      heatmap: c.get<boolean>("heatmap.enabled", false),
    };
  }

  private heatType(nodeId: string | null): vscode.TextEditorDecorationType {
    const color = colorForNode(nodeId);
    let t = this.heatTypes.get(color);
    if (!t) {
      t = vscode.window.createTextEditorDecorationType({
        isWholeLine: true,
        backgroundColor: colorWithAlpha(nodeId, 0.07),
        borderWidth: "0 0 0 2px",
        borderStyle: "solid",
        borderColor: color,
        overviewRulerColor: color,
        overviewRulerLane: vscode.OverviewRulerLane.Full,
      });
      this.heatTypes.set(color, t);
    }
    return t;
  }

  private schedule(): void {
    clearTimeout(this.debounce);
    this.debounce = setTimeout(() => void this.render(), 120);
  }

  private clearAll(editor: vscode.TextEditor): void {
    editor.setDecorations(this.currentLineType, []);
    for (const t of this.heatTypes.values()) {
      editor.setDecorations(t, []);
    }
  }

  async render(): Promise<void> {
    const editor = vscode.window.activeTextEditor;
    if (!editor || editor.document.languageId !== "python") {
      this.status.hide();
      return;
    }
    const rel = vscode.workspace.asRelativePath(editor.document.uri, false);
    let blame: BlameView;
    try {
      blame = await this.store.blame(rel);
    } catch {
      this.status.hide();
      return;
    }
    if (blame.error) {
      this.clearAll(editor);
      this.status.hide();
      return;
    }
    const { blame: showBlame, heatmap } = this.cfg();
    this.renderHeatmap(editor, blame, heatmap);
    this.renderCurrentLine(editor, blame, showBlame);
  }

  private renderHeatmap(editor: vscode.TextEditor, blame: BlameView, on: boolean): void {
    // Clear every known type first, then re-apply: avoids stale bands when ownership shifts.
    for (const t of this.heatTypes.values()) {
      editor.setDecorations(t, []);
    }
    if (!on) {
      return;
    }
    const byType = new Map<vscode.TextEditorDecorationType, vscode.Range[]>();
    for (const s of blame.spans) {
      if (!s.node_id) {
        continue;
      }
      const t = this.heatType(s.node_id);
      const range = new vscode.Range(s.start - 1, 0, s.end - 1, 0);
      if (!byType.has(t)) {
        byType.set(t, []);
      }
      byType.get(t)!.push(range);
    }
    for (const [t, ranges] of byType) {
      editor.setDecorations(t, ranges);
    }
  }

  private renderCurrentLine(editor: vscode.TextEditor, blame: BlameView, on: boolean): void {
    if (!on) {
      editor.setDecorations(this.currentLineType, []);
      this.status.hide();
      return;
    }
    const line1 = editor.selection.active.line + 1;
    const owner = ownerAt(blame, line1);
    if (!owner) {
      editor.setDecorations(this.currentLineType, []);
      this.status.hide();
      return;
    }
    const meta = blame.nodes[owner];
    const label = meta ? meta.intent : owner;
    const drift = blame.drift ? "  ⚠ drifted" : "";
    const lineRange = editor.document.lineAt(line1 - 1).range;
    // Tint the annotation with the feature's identity color so the diamond by the cursor reads
    // as the same feature shown in the gutter and the graph. OKLCH keeps it contrast-safe.
    editor.setDecorations(this.currentLineType, [
      {
        range: lineRange,
        renderOptions: { after: { contentText: `  ◆ ${label}${drift}`, color: colorForNode(owner) } },
      },
    ]);
    this.status.text = `$(git-commit) ${truncate(label, 40)}`;
    this.status.tooltip = new vscode.MarkdownString(
      `Feature \`${owner}\`${meta ? ` — ${meta.kind} / ${meta.status}` : ""}`
    );
    this.status.color = new vscode.ThemeColor("statusBar.foreground");
    this.status.show();
  }

  dispose(): void {
    this.disposables.forEach((d) => d.dispose());
    this.heatTypes.forEach((t) => t.dispose());
  }
}
