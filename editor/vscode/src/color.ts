// Deterministic, stable color per node id. The hue comes from golden-angle stepping (Ankerl) so
// the same id always lands on the same hue and distinct features fall in the largest remaining
// gap. Crucially the color is generated in **OKLCH**, not HSL/HSV: OKLCH is perceptually uniform,
// so a fixed lightness/chroma gives every hue the *same* perceived brightness — which keeps
// contrast roughly constant across all features and lets us hit a WCAG floor against the editor
// background. Lightness is theme-aware (lighter on dark themes, darker on light ones).
//
// This same math is mirrored byte-for-byte in media/decision.js (the webview can't import this
// module across the bundle boundary) and in sgt/tui/color.py. Keep the three in sync —
// tests/test_color_parity.py drives all three and fails on drift.

import * as vscode from "vscode";

const GOLDEN = 0.618033988749895;

// Fixed lightness (L) and chroma (C) per theme. L is chosen so the color clears ~3:1 non-text
// contrast against a typical editor background on each theme (WCAG 1.4.11); C is muted so color
// reads as a quiet ownership channel, never a shout.
function lc(): { L: number; C: number } {
  const kind = vscode.window.activeColorTheme.kind;
  switch (kind) {
    case vscode.ColorThemeKind.Light:
      return { L: 0.55, C: 0.14 };
    case vscode.ColorThemeKind.HighContrastLight:
      return { L: 0.48, C: 0.15 };
    case vscode.ColorThemeKind.HighContrast:
      return { L: 0.8, C: 0.15 };
    default: // Dark
      return { L: 0.72, C: 0.13 };
  }
}

function hashId(id: string): number {
  let h = 2166136261;
  for (let i = 0; i < id.length; i++) {
    h ^= id.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return h >>> 0;
}

/** OKLCH (L 0..1, C, hue in degrees) -> sRGB [r,g,b] 0..255, gamut-clamped (Ottosson). */
function oklchToRgb(L: number, C: number, hDeg: number): [number, number, number] {
  const h = (hDeg * Math.PI) / 180;
  const a = C * Math.cos(h);
  const b = C * Math.sin(h);

  const l_ = L + 0.3963377774 * a + 0.2158037573 * b;
  const m_ = L - 0.1055613458 * a - 0.0638541728 * b;
  const s_ = L - 0.0894841775 * a - 1.291485548 * b;
  const l = l_ * l_ * l_;
  const m = m_ * m_ * m_;
  const s = s_ * s_ * s_;

  const lr = 4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s;
  const lg = -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s;
  const lb = -0.0041960863 * l - 0.7034186147 * m + 1.707614701 * s;

  const g = (x: number) => {
    const c = x <= 0.0031308 ? 12.92 * x : 1.055 * Math.pow(x, 1 / 2.4) - 0.055;
    return Math.round(Math.max(0, Math.min(1, c)) * 255);
  };
  return [g(lr), g(lg), g(lb)];
}

function hueForId(id: string): number {
  return ((hashId(id) * GOLDEN) % 1) * 360;
}

/** Stable `#rrggbb` for a node id (or a neutral gray for the unattributed case). */
export function colorForNode(id: string | null): string {
  if (!id) {
    return "#888888";
  }
  const { L, C } = lc();
  const [r, g, b] = oklchToRgb(L, C, hueForId(id));
  return `#${[r, g, b].map((c) => c.toString(16).padStart(2, "0")).join("")}`;
}

/** `#rrggbbaa` — the same color at a given 0..1 alpha, for low-opacity whole-line tints. */
export function colorWithAlpha(id: string | null, alpha: number): string {
  const a = Math.round(Math.max(0, Math.min(1, alpha)) * 255)
    .toString(16)
    .padStart(2, "0");
  return colorForNode(id) + a;
}
