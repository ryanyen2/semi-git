// `ughNames`: each name with its step and save counts, in the order of its first save, and the
// unnamed steps last. A row reveals the name in the workbench; its actions preview a take-out (or
// a bring-back, when steps of it are out) and rename it.

import * as vscode from "vscode";
import { Store } from "../store";
import { LogView } from "../types";

interface Row {
  address: string | null;
  steps: number;
  saves: number;
  out: number;
}

export function nameRows(log: LogView): Row[] {
  const rows = new Map<string | null, Row & { shas: Set<string> }>();
  for (const save of [...log.saves].reverse()) {
    for (const st of save.steps) {
      let row = rows.get(st.name);
      if (!row) rows.set(st.name, (row = { address: st.name, steps: 0, saves: 0, out: 0, shas: new Set() }));
      row.steps++;
      if (st.out) row.out++;
      row.shas.add(save.sha);
    }
  }
  const named = [...rows.values()].filter((r) => r.address !== null);
  const unnamed = rows.get(null);
  return [...named, ...(unnamed ? [unnamed] : [])].map(({ shas, ...r }) => ({ ...r, saves: shas.size }));
}

const count = (n: number, noun: string) => `${n} ${noun}${n === 1 ? "" : "s"}`;

export class NamesTree implements vscode.TreeDataProvider<Row>, vscode.Disposable {
  private changed = new vscode.EventEmitter<void>();
  readonly onDidChangeTreeData = this.changed.event;
  private sub: { dispose(): void };

  constructor(private readonly store: Store) {
    this.sub = store.onChange(() => this.changed.fire());
  }

  getChildren(row?: Row): Row[] {
    const log = this.store.snapshot.log;
    return row || !log ? [] : nameRows(log);
  }

  getTreeItem(row: Row): vscode.TreeItem {
    const item = new vscode.TreeItem(row.address ?? "Unnamed");
    item.description = [count(row.steps, "step"), count(row.saves, "save"), ...(row.out ? [`${row.out} out`] : [])].join(" · ");
    item.tooltip = `${row.address ?? "Unnamed"}\n${item.description}`;
    if (row.address === null) {
      item.command = { command: "ugh.openWorkbench", title: "Open workbench" };
      return item;
    }
    item.contextValue = "ughName" + (row.out < row.steps ? ".in" : "") + (row.out ? ".out" : "");
    item.command = { command: "ugh.revealInWorkbench", title: "Reveal in workbench", arguments: [row.address] };
    return item;
  }

  dispose(): void {
    this.sub.dispose();
    this.changed.dispose();
  }
}
