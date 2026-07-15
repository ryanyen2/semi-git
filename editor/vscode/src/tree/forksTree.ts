// `sgtForks`: the open-fork inbox from `forks_view` (C4). A same-symbol chain fork is the one true
// conflict in this kernel -- durable in committed `.sgt/forks.json` -- and blocks `land`. The
// container badge (wired in extension.ts) surfaces the open count even when the view is collapsed.

import * as vscode from "vscode";
import { Store } from "../store";
import { ForkRecord } from "../types";

export class ForkItem extends vscode.TreeItem {
  constructor(public readonly record: ForkRecord) {
    super(record.symbol, vscode.TreeItemCollapsibleState.None);
    this.description = record.file;
    this.tooltip = record.remedy;
    this.iconPath = new vscode.ThemeIcon(
      "warning",
      new vscode.ThemeColor("problemsWarningIcon.foreground")
    );
    this.contextValue = "sgtFork";
    this.command = { command: "sgt.resolveFork", title: "Resolve Fork", arguments: [record.symbol] };
  }
}

export class ForksTreeProvider implements vscode.TreeDataProvider<ForkRecord>, vscode.Disposable {
  private _onDidChangeTreeData = new vscode.EventEmitter<void>();
  readonly onDidChangeTreeData = this._onDidChangeTreeData.event;
  private disposable: vscode.Disposable;

  constructor(private store: Store) {
    this.disposable = store.onDidChange(() => this._onDidChangeTreeData.fire());
  }

  getTreeItem(record: ForkRecord): vscode.TreeItem {
    return new ForkItem(record);
  }

  async getChildren(): Promise<ForkRecord[]> {
    try {
      return (await this.store.forksView()).forks;
    } catch {
      return [];
    }
  }

  dispose(): void {
    this.disposable.dispose();
  }
}
