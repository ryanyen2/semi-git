// What the confirm dialogs and result messages say, from the planner's answer and from
// `ugh undo --emit`. Pure, so `words.test.ts` pins the wording under `node --test`.

import type { CheckVerdict, Plan, UndoPreview } from "./types.ts";

const steps = (n: number) => `${n} step${n === 1 ? "" : "s"}`;
const saves = (n: number) => `${n} save${n === 1 ? "" : "s"}`;
const list = (items: string[], max = 6) => items.slice(0, max).join(", ") + (items.length > max ? " …" : "");

const REASON: Record<string, string> = {
  uses: "used",
  changed_later: "changed later",
  same_lines: "same lines",
  check: "check",
};

export function checkWords(check: "not_run" | CheckVerdict): string {
  if (check === "not_run") return "check not run";
  return `${check.judge === "check" ? "check" : "parser"} ${check.passed ? "passes" : "fails"}`;
}

export interface PreviewWords {
  message: string;
  detail: string;
  /** Something would change. */
  apply: boolean;
  /** `--whole` takes more than the target alone. */
  whole: boolean;
}

/** The confirm dialog for a preview. */
export function previewWords(plan: Plan): PreviewWords {
  const back = plan.verb === "restore";
  const message = `${back ? "Bring back" : "Take out"} ${plan.target}`;
  const lines: string[] = [];
  if (plan.message) lines.push(plan.message);
  if (plan.undone.length) lines.push(`${steps(plan.undone.length)} ${back ? "come back" : "go"}`);
  if (plan.held.length) {
    const why = new Map<string, number>();
    for (const h of plan.held) why.set(h.reason, (why.get(h.reason) ?? 0) + 1);
    lines.push(`${plan.held.length} held: ${[...why].map(([r, n]) => `${n} ${REASON[r] ?? r}`).join(", ")}`);
  }
  const rs = plan.removal_set;
  const whole = !back && !!rs && rs.steps > plan.undone.length;
  if (whole && rs) lines.push(`Whole takes ${steps(rs.steps)} in ${saves(rs.saves.length)}`);
  const files = Object.keys(plan.files);
  if (files.length) lines.push(list(files));
  const never = [...plan.undone, ...plan.held].filter((s) => s.ran === false).length;
  if (never) lines.push(`The check never ran ${steps(never)}`);
  if (plan.observed && plan.observed.head === null) lines.push("HEAD not observed");
  return { message, detail: lines.join("\n"), apply: files.length > 0, whole };
}

/** One line for an applied (or refused) plan. */
export function resultWords(plan: Plan): string {
  const back = plan.verb === "restore";
  if (!plan.applied) {
    if (plan.message) return plan.message.charAt(0).toUpperCase() + plan.message.slice(1);
    if (plan.check !== "not_run" && !plan.check.passed) return `Nothing applied: ${checkWords(plan.check)}`;
    return `Nothing applied${plan.held.length ? `: ${plan.held.length} held` : ""}`;
  }
  const parts = [`${back ? "Brought back" : "Took out"} ${steps(plan.undone.length)}`];
  if (plan.held.length) parts.push(`${plan.held.length} held`);
  parts.push(checkWords(plan.check));
  const seen = plan.observed;
  if (seen?.delta) {
    let out = `output ${seen.delta.added} added, ${seen.delta.removed} removed`;
    if (seen.reverses) out += `, ${back ? "back" : "gone"} as the check sees it`;
    parts.push(out);
  }
  return parts.join(" · ");
}

export interface UndoConfirm {
  /** `confirm` draws the modal; `refused` states a verdict already reached; `nothing` is a toast. */
  kind: "confirm" | "refused" | "nothing";
  message: string;
  detail: string;
}

export function undoWords(pv: UndoPreview): UndoConfirm {
  if (pv.kind === null) return { kind: "nothing", message: "Nothing to undo", detail: "" };
  if (!pv.ok) return { kind: "refused", message: `Cannot undo: ${pv.message}`, detail: "" };
  const detail = [
    pv.message.charAt(0).toUpperCase() + pv.message.slice(1),
    pv.undone || pv.held ? `${steps(pv.undone)} go${pv.held ? `, ${pv.held} held` : ""}` : "",
    pv.steps.length ? list(pv.steps) : "",
  ].filter(Boolean).join("\n");
  return { kind: "confirm", message: "Undo", detail };
}
