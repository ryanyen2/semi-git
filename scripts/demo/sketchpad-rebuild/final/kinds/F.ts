import { FULL_SIZE } from '../drawing'
import { register } from './registry'

// Appendix A, code 25, letter F: "Instance is full size, i.e. the same size as its
// master picture." The ratio is Appendix A's S constraint folded in, "First thing
// is 1/3, 1/2, 1, 2, 3 times size of second thing", so a group placed small stays
// small.
//
// Only the handle gives. The origin is where the reader put the instance, and an
// instance that slid sideways every time its size was corrected would be worse than
// one that was the wrong size.
register(
  'F',
  ([origin, handle], constraint) => [
    Math.hypot(handle.x - origin.x, handle.y - origin.y) - FULL_SIZE * (constraint.ratio ?? 1),
  ],
  [1],
)
