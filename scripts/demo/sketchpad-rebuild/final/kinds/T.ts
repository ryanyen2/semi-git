import { placed } from '../drawing'
import { register } from './registry'

// Appendix A, code 43, letter T: "Point bears same relation to instance that
// (point) bears to its picture. GENERATED AUTOMATICALLY WITH INSTANCES." This is
// what keeps the end terminal on a resistor at the end of the resistor, and it is
// what fastens one hexagon's corner to another's. Two errors, because holding a
// point at a place removes both of its degrees of freedom.
//
// The attached point and the instance's origin give. The handle does not, so
// fastening a picture somewhere can move it and can never turn or resize it. Size
// and angle belong to their own constraints, F and E, which is how Appendix A
// divides the work.
register(
  'T',
  ([subject], constraint, drawing) => {
    const via = constraint.via
    if (!via) return [0, 0]
    const instance = drawing.instances.find(
      (i) => i.master === via.master && i.origin === constraint.vars[1],
    )
    const master = drawing.masters.find((m) => m.id === via.master)
    const source = master?.points.find((p) => p.id === via.point)
    if (!instance || !source) return [0, 0]
    const there = placed(drawing, instance, source)
    return there ? [subject.x - there.x, subject.y - there.y] : [0, 0]
  },
  [0, 1],
)
