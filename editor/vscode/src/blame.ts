// A quiet tint at the left edge of each named definition, in its name's hue (the workbench's hue
// for that name), and the same colour in the overview ruler. Unnamed definitions are left
// untinted. Toggled by `ugh.blame.enabled`.

import * as vscode from "vscode";
import { nameColor } from "./color";
import { Documents } from "./documents";
import { Store } from "./store";
import { headLine, nameOf } from "./units";

export class Blame implements vscode.Disposable {
  private types = new Map<string, vscode.TextEditorDecorationType>();
  private subs: { dispose(): void }[];
  private timer: NodeJS.Timeout | undefined;

  constructor(private readonly docs: Documents, store: Store) {
    this.subs = [
      store.onChange(() => this.schedule()),
      vscode.window.onDidChangeVisibleTextEditors(() => this.schedule()),
      vscode.workspace.onDidChangeTextDocument((e) => {
        if (vscode.window.visibleTextEditors.some((ed) => ed.document === e.document)) this.schedule(400);
      }),
      vscode.workspace.onDidChangeConfiguration((e) => {
        if (e.affectsConfiguration("ugh.blame")) this.schedule();
      }),
      vscode.window.onDidChangeActiveColorTheme(() => {
        this.clear();
        this.schedule();
      }),
    ];
  }

  private dark(): boolean {
    const kind = vscode.window.activeColorTheme.kind;
    return kind === vscode.ColorThemeKind.Dark || kind === vscode.ColorThemeKind.HighContrast;
  }

  private typeFor(color: string): vscode.TextEditorDecorationType {
    let t = this.types.get(color);
    if (!t) {
      t = vscode.window.createTextEditorDecorationType({
        isWholeLine: true,
        borderWidth: "0 0 0 2px",
        borderStyle: "solid",
        borderColor: color,
        overviewRulerColor: color,
        overviewRulerLane: vscode.OverviewRulerLane.Left,
      });
      this.types.set(color, t);
    }
    return t;
  }

  private schedule(delay = 120): void {
    clearTimeout(this.timer);
    this.timer = setTimeout(() => void this.render(), delay);
  }

  async render(): Promise<void> {
    const on = vscode.workspace.getConfiguration("ugh").get<boolean>("blame.enabled", true);
    for (const editor of vscode.window.visibleTextEditors) {
      const ranges = new Map<string, vscode.Range[]>();
      const all = on ? await this.docs.units(editor.document) : undefined;
      const lines = all ? editor.document.getText().split("\n") : [];
      for (const unit of all?.units ?? []) {
        if (unit.kind === "head") continue;
        const name = nameOf(unit.steps);
        if (!name) continue;
        const color = nameColor(name, this.dark());
        const list = ranges.get(color) ?? [];
        // From the line the name is on, so the blank lines between definitions stay untinted.
        list.push(new vscode.Range(headLine(unit, lines) - 1, 0, unit.last - 1, 0));
        ranges.set(color, list);
      }
      for (const [color, t] of this.types) editor.setDecorations(t, ranges.get(color) ?? []);
      for (const [color, list] of ranges) if (!this.types.has(color)) editor.setDecorations(this.typeFor(color), list);
    }
  }

  private clear(): void {
    this.types.forEach((t) => t.dispose());
    this.types.clear();
  }

  dispose(): void {
    clearTimeout(this.timer);
    this.subs.forEach((s) => s.dispose());
    this.clear();
  }
}
