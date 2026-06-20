// Small shared helpers used across surfaces. Kept here so blame, hover, and codelens share one
// definition rather than three copies that drift.

import { BlameView } from "./types";

/** The node that owns 1-based `line1` in a blame view, or null if unattributed. */
export function ownerAt(blame: BlameView, line1: number): string | null {
  for (const s of blame.spans) {
    if (line1 >= s.start && line1 <= s.end) {
      return s.node_id;
    }
  }
  return null;
}

/** Truncate to `n` chars with an ellipsis. */
export function truncate(s: string, n: number): string {
  return s.length > n ? s.slice(0, n - 1) + "…" : s;
}
