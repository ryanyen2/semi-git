import { register } from './registry'

// Appendix A, code 34, letter M: distance from first to second is some ratio of the
// distance from third to fourth. Equal length is this constraint with the ratio
// left at one, and Chapter I's hexagon is held regular by five of these, each side
// tied to the next.
register(
  'M',
  ([a, b, c, d], constraint) => [
    Math.hypot(b.x - a.x, b.y - a.y) -
      (constraint.ratio ?? 1) * Math.hypot(d.x - c.x, d.y - c.y),
  ],
  [0, 1, 2, 3],
)
