// A name's identity hue, the same function `media/workbench.js` uses, so a name's blame tint in the
// editor matches its row in the workbench: FNV-1a over the label, stepped by the golden ratio,
// drawn in OKLCH at a fixed, muted lightness and chroma per theme.

const GOLDEN = 0.618033988749895;

export function hue(label: string): number {
  let h = 2166136261;
  for (let i = 0; i < label.length; i++) {
    h ^= label.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return (((h >>> 0) * GOLDEN) % 1) * 360;
}

/** OKLCH to sRGB `#rrggbb`, gamut-clamped (Ottosson). */
function oklch(L: number, C: number, deg: number): string {
  const h = (deg * Math.PI) / 180;
  const a = C * Math.cos(h);
  const b = C * Math.sin(h);
  const l = (L + 0.3963377774 * a + 0.2158037573 * b) ** 3;
  const m = (L - 0.1055613458 * a - 0.0638541728 * b) ** 3;
  const s = (L - 0.0894841775 * a - 1.291485548 * b) ** 3;
  const rgb = [
    4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
    -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
    -0.0041960863 * l - 0.7034186147 * m + 1.707614701 * s,
  ];
  return (
    "#" +
    rgb
      .map((x) => {
        const c = x <= 0.0031308 ? 12.92 * x : 1.055 * Math.pow(x, 1 / 2.4) - 0.055;
        return Math.round(Math.max(0, Math.min(1, c)) * 255).toString(16).padStart(2, "0");
      })
      .join("")
  );
}

/** `oklch(0.70 0.09 h)` on dark themes, `oklch(0.55 0.09 h)` on light ones. */
export function nameColor(label: string, dark: boolean): string {
  return oklch(dark ? 0.7 : 0.55, 0.09, hue(label));
}
