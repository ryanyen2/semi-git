// `sgtNow`: the state-of-actions surface from `now_view` -- one sectioned list answering "what's
// happening, what needs me, what did I just do, what's next". Unsaved (dirty ops that would land
// on the next save), Needs you (open forks / stalled plans / the review pile), Recently done (the
// last few saves), and a single structured Next action whose row runs the recommended command.
// Sourced from one `sgt now --json` call; refreshes live off the same `.sgt/**/*.json` watcher (the
// PostToolUse activity hook and every save write under `.sgt/`), like every other tree.

import * as vscode from "vscode";
import { Store } from "../store";
import {
  ForkRecord,
  HistoryCommit,
  NextAction,
  NowInFlightRow,
  NowReview,
  NowStalledPlan,
  NowView,
} from "../types";

type SectionId = "in_flight" | "needs_you" | "recently_done" | "next";

export type NowNode =
  | { kind: "section"; sectionId: SectionId; label: string; count: number | null }
  | { kind: "inflight"; row: NowInFlightRow }
  | { kind: "fork"; record: ForkRecord }
  | { kind: "review"; review: NowReview }
  | { kind: "stalled"; plan: NowStalledPlan }
  | { kind: "commit"; commit: HistoryCommit }
  | { kind: "next"; action: NextAction };

const NEXT_ICON: Record<NextAction["kind"], string> = {
  resolve_fork: "warning",
  resume_plan: "debug-continue",
  save: "save",
  review: "checklist",
  clean: "check",
};

export class NowTreeProvider implements vscode.TreeDataProvider<NowNode>, vscode.Disposable {
  private _onDidChangeTreeData = new vscode.EventEmitter<void>();
  readonly onDidChangeTreeData = this._onDidChangeTreeData.event;
  private disposable: vscode.Disposable;
  private now: NowView | undefined;

  constructor(private store: Store) {
    this.disposable = store.onDidChange(() => {
      this.now = undefined;
      this._onDidChangeTreeData.fire();
    });
  }

  getTreeItem(node: NowNode): vscode.TreeItem {
    if (node.kind === "section") {
      // count === null marks the always-present Next action section (no "(n)" suffix, but it still
      // expands to show its single row); a 0-count section would have been filtered out already.
      const label = node.count === null ? node.label : `${node.label} (${node.count})`;
      const item = new vscode.TreeItem(label, vscode.TreeItemCollapsibleState.Expanded);
      item.contextValue = "sgtNowSection";
      return item;
    }
    if (node.kind === "inflight") {
      const label = this.store.node(node.row.feature_id)?.label ?? node.row.feature_id;
      const item = new vscode.TreeItem(label, vscode.TreeItemCollapsibleState.None);
      item.description = `${node.row.op_count} op(s)`;
      item.iconPath = new vscode.ThemeIcon("git-commit");
      item.tooltip = "Pending ops already attributed to this feature -- they land on the next save.";
      item.contextValue = "sgtNowInFlight";
      return item;
    }
    if (node.kind === "fork") {
      const item = new vscode.TreeItem(node.record.symbol, vscode.TreeItemCollapsibleState.None);
      item.description = node.record.file;
      item.tooltip = node.record.remedy;
      item.iconPath = new vscode.ThemeIcon(
        "warning",
        new vscode.ThemeColor("problemsWarningIcon.foreground")
      );
      item.contextValue = "sgtNowFork";
      item.command = { command: "sgt.resolveFork", title: "Resolve Fork", arguments: [node.record.symbol] };
      return item;
    }
    if (node.kind === "review") {
      const item = new vscode.TreeItem(node.review.subject, vscode.TreeItemCollapsibleState.None);
      item.description = node.review.reason;
      item.iconPath = new vscode.ThemeIcon("checklist");
      item.tooltip = "In the review pile -- run `sgt intent review` to accept or discard.";
      item.contextValue = "sgtNowReview";
      return item;
    }
    if (node.kind === "stalled") {
      const first = node.plan.remaining_titles[0] ?? `${node.plan.pending_count} step(s) left`;
      const item = new vscode.TreeItem(first, vscode.TreeItemCollapsibleState.None);
      item.description = `${node.plan.pending_count} pending`;
      item.iconPath = new vscode.ThemeIcon("debug-pause");
      item.tooltip = node.plan.remaining_titles.join("\n") || "Stalled plan.";
      item.contextValue = "sgtNowStalled";
      item.command = {
        command: "sgt.resolveStalledPlan",
        title: "Resolve Stalled Plan",
        arguments: [node.plan.session_id],
      };
      return item;
    }
    if (node.kind === "commit") {
      const item = new vscode.TreeItem(node.commit.subject, vscode.TreeItemCollapsibleState.None);
      item.description = (node.commit.sha || "").slice(0, 7);
      item.iconPath = new vscode.ThemeIcon("git-commit");
      item.contextValue = "sgtNowCommit";
      return item;
    }
    // next action
    const item = new vscode.TreeItem(node.action.label, vscode.TreeItemCollapsibleState.None);
    item.iconPath = new vscode.ThemeIcon(NEXT_ICON[node.action.kind]);
    if (node.action.command) {
      item.description = node.action.command;
      item.tooltip = `Run: ${node.action.command}`;
    }
    item.contextValue = "sgtNowNext";
    // A fork routes to its resolution wizard; anything else with a command runs it in a terminal.
    if (node.action.kind === "resolve_fork" && node.action.target) {
      item.command = { command: "sgt.resolveFork", title: "Resolve Fork", arguments: [node.action.target] };
    } else if (node.action.command) {
      item.command = { command: "sgt.runNextAction", title: "Run", arguments: [node.action] };
    }
    return item;
  }

  private async load(): Promise<NowView> {
    if (!this.now) {
      // Warm the map cache in parallel so in-flight rows can resolve feature labels synchronously
      // via `store.node()`; both share the `.sgt/**/*.json` invalidation, so this stays coherent.
      const [now] = await Promise.all([this.store.nowView(), this.store.map().catch(() => undefined)]);
      this.now = now;
    }
    return this.now;
  }

  async getChildren(node?: NowNode): Promise<NowNode[]> {
    let now: NowView;
    try {
      now = await this.load();
    } catch {
      return [];
    }

    if (!node) {
      const needsYou =
        now.needs_you.forks.length + now.needs_you.reviews.length + now.needs_you.stalled_plans.length;
      const sections: NowNode[] = [
        { kind: "section", sectionId: "in_flight", label: "Unsaved", count: now.in_flight.total_op_count },
        { kind: "section", sectionId: "needs_you", label: "Needs you", count: needsYou },
        { kind: "section", sectionId: "recently_done", label: "Recently done", count: now.recently_done.length },
      ];
      // Drop empty sections; the Next action always shows -- it's the point of the view (and states
      // "nothing pending" when the tree is otherwise clean).
      return [
        ...sections.filter((s) => s.kind === "section" && (s.count ?? 0) > 0),
        { kind: "section", sectionId: "next", label: "Next action", count: null },
      ];
    }

    if (node.kind !== "section") return [];
    if (node.sectionId === "in_flight") {
      return now.in_flight.affected.map((row) => ({ kind: "inflight", row }));
    }
    if (node.sectionId === "needs_you") {
      return [
        ...now.needs_you.forks.map((record): NowNode => ({ kind: "fork", record })),
        ...now.needs_you.stalled_plans.map((plan): NowNode => ({ kind: "stalled", plan })),
        ...now.needs_you.reviews.map((review): NowNode => ({ kind: "review", review })),
      ];
    }
    if (node.sectionId === "recently_done") {
      return now.recently_done.map((commit) => ({ kind: "commit", commit }));
    }
    if (node.sectionId === "next") {
      return [{ kind: "next", action: now.next_action }];
    }
    return [];
  }

  dispose(): void {
    this.disposable.dispose();
  }
}
