import { interpolateBrBG } from 'd3-scale-chromatic'
import type { Category } from '../study/taxonomy'
import type { Condition } from '../lib/types'

// Colours and type are set as SVG attributes, never through CSS classes, so the
// exported file stands on its own in a LaTeX document with no stylesheet to
// carry along.

export const FONT = 'Helvetica, Arial, sans-serif'

export const TYPE = {
  axis: 9,
  tick: 9,
  label: 10,
  title: 11,
  inBar: 8.5,
  caption: 8.5,
}

export const INK = '#1a1a1a'
export const RULE = '#cfd4da'
export const SOFT = '#f2f4f6'
export const MUTED = '#6b7280'

/** ACM column widths, in points at 72dpi. */
export const WIDTH = {
  single: 3.33 * 72,
  double: 7.0 * 72,
}

export const CATEGORY_COLOR: Record<Category, string> = {
  orient: '#AEC7E8',
  inspect: '#1F77B4',
  search: '#9EDAE5',
  prompt: '#9467BD',
  agent_edit: '#FFBB78',
  manual_edit: '#FF7F0E',
  history_op: '#D62728',
  verify: '#2CA02C',
  recover: '#8C564B',
}

/**
 * Conditions are named by what they are, not by which is ours. The figure the
 * paper ships uses these labels, and the dashboard uses the same ones so nobody
 * has to remember a mapping while reading a chart.
 */
export const CONDITION_LABEL: Record<Condition, string> = {
  git: 'Git',
  sgt: 'sgt',
}

export const CONDITION_COLOR: Record<Condition, string> = {
  git: '#8c6d31',
  sgt: '#2b7a8c',
}

/**
 * Seven-step diverging ramp for a 7-point Likert scale: brown at disagree,
 * teal at agree, pale in the middle. Sampled from BrBG rather than picked by
 * hand so the steps are perceptually even, and the two ends stay separable in
 * greyscale, which is how half of reviewers will print it.
 */
export function likertColors(points: number): string[] {
  return Array.from({ length: points }, (_, i) => {
    const t = points === 1 ? 0.5 : i / (points - 1)
    // Pull the ends in slightly: the extremes of BrBG are dark enough that
    // white numerals inside the bar stop being readable.
    return interpolateBrBG(0.08 + t * 0.84)
  })
}

/** Readable text colour on a given fill. */
export function onColor(fill: string): string {
  const m = /^#?([a-f\d]{2})([a-f\d]{2})([a-f\d]{2})$/i.exec(fill)
  let r = 128
  let g = 128
  let b = 128
  if (m) {
    r = parseInt(m[1], 16)
    g = parseInt(m[2], 16)
    b = parseInt(m[3], 16)
  } else {
    const rgb = /rgb\((\d+),\s*(\d+),\s*(\d+)\)/.exec(fill)
    if (rgb) {
      r = +rgb[1]
      g = +rgb[2]
      b = +rgb[3]
    }
  }
  const lum = (0.299 * r + 0.587 * g + 0.114 * b) / 255
  return lum > 0.6 ? INK : '#ffffff'
}
