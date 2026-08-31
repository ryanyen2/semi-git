import { register } from './registry'

// Appendix A, code 22, letter C: "Distance from first to second is equal to
// distance from first to third. (First is circle center.)" Generated automatically
// when points are created on circles.
//
// Chapter VIII: the "for reference only" variables. The circle is the reference and
// the point is what moves. Without that, six corners pulling on one circle can drag
// the circle to them, and relaxation settles into an equilateral hexagon that
// crosses itself: six equal chords, in the wrong order round the rim.
register(
  'C',
  ([center, onRim, subject]) => [
    Math.hypot(subject.x - center.x, subject.y - center.y) -
      Math.hypot(onRim.x - center.x, onRim.y - center.y),
  ],
  [2],
)
