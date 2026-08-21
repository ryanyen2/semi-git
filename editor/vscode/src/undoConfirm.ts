// What the undo confirm dialog says, derived from `sgt undo --emit`.
//
// "Undo the last operation?" was the whole of the dialog: it asked for consent to reverse an
// operation without naming it, so the answer was a guess about what you last did. Undo is what you
// reach for when something has already gone wrong, which is the worst moment to be asked blind --
// and none of it needs guessing, because `oplog.preview` knows the kind of operation, the exact
// op-set coming back, the op-set going away, and the guard's verdict before anything is touched.
//
// Pure, and in its own module rather than inline in `gitBridge.ts`, for the reason `mapFilter.ts`
// gives: a module that imports `vscode` cannot be loaded under `node --test`, and the wording of
// the one dialog standing between a click and a re-materialized ideal is worth pinning.

import type { UndoPreview } from "./types";

export interface UndoConfirm {
  /** `confirm` draws the modal; `refused` states a verdict already reached; `nothing` is a toast. */
  kind: "confirm" | "refused" | "nothing";
  message: string;
  detail: string;
}

export function undoConfirmText(pv: UndoPreview): UndoConfirm {
  if (pv.kind === null) {
    return { kind: "nothing", message: pv.message || "nothing to undo", detail: "" };
  }
  if (!pv.ok) {
    // The F3 guard's refusal, surfaced instead of a button that would hit it. Getting past it
    // destroys the work it names, and the deliberate act that opts into that is typing
    // `sgt undo --force` -- not a second click on a dialog you were already unsure about.
    return { kind: "refused", message: `Cannot undo: ${pv.message}`, detail: "" };
  }
  const detail = [
    pv.restored.length ? `Brings back ${count(pv.restored.length)}.` : "",
    pv.dropped.length ? `Drops ${count(pv.dropped.length)} made since.` : "",
    // `symbols` arrives capped upstream, so the tail is "…" rather than a remainder count this
    // payload has no way to be right about -- the same tail `sgt undo` prints, for the same reason.
    pv.symbols.length
      ? `Touches ${pv.symbols.slice(0, 6).join(", ")}${pv.symbols.length > 6 ? " …" : ""}`
      : "",
  ].filter(Boolean).join("\n");
  return { kind: "confirm", message: `This undo ${pv.message}.`, detail };
}

const count = (n: number) => `${n} edit${n === 1 ? "" : "s"}`;
