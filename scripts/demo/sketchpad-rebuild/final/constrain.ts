import { pointOf, type Constraint, type Drawing, type Point } from './drawing'
import { ERROR, MOVABLE } from './kinds/registry'
import './kinds/C'
import './kinds/M'
import './kinds/T'
import './kinds/F'
import './kinds/H'
import './kinds/E'

// Constraint satisfaction, Chapter VIII.
//
// "The major feature which distinguishes a Sketchpad drawing from a paper and
// pencil drawing is the user's ability to specify to Sketchpad mathematical
// conditions on already drawn parts of his drawing which will be automatically
// satisfied by the computer to make the drawing take the exact shape desired."
//
// A constraint type is not a special case in the program. Each one lives in its own
// file under kinds/, registering an error subroutine and its movable variables, and
// nothing here refers to a particular type -- which is why adding one is a small
// change and removing one is too. "After the first stumblings of trying to define a
// constraint type in terms of the equations of lines along which the constrained
// variables should lie to satisfy the constraint, the numerical definition of
// constraints directly in terms of an error was devised." So a type is one file
// returning errors.

function varsOf(drawing: Drawing, constraint: Constraint): Point[] | null {
  const points = constraint.vars.map((id) => pointOf(drawing, id))
  return points.every((p): p is Point => p !== null) ? points : null
}

export function errorsOf(drawing: Drawing, constraint: Constraint): number[] {
  const vars = varsOf(drawing, constraint)
  const compute = ERROR[constraint.kind]
  // A constraint of a type the program does not have is not an error, it is a
  // condition nobody here can satisfy, so it contributes nothing and the drawing
  // relaxes without it. That is what makes a constraint type removable: take the
  // table entry away and the drawings that used it simply stop being held.
  return vars && compute ? compute(vars, constraint, drawing) : []
}

// How much the error changes as one point moves. Sutherland linearizes the
// constraint around the current values; this does it numerically, which costs two
// evaluations and works for any error function anyone later writes.
const NUDGE = 0.05

// A variable is one point, or an instance's origin and handle together, so this
// reports one gradient component per unknown: two for each point of the variable.
function sensitivity(
  drawing: Drawing,
  constraint: Constraint,
  ids: string[],
): Array<{ g: number[]; e: number }> {
  const here = errorsOf(drawing, constraint)
  const nudged = (unknown: number, step: number) => {
    const id = ids[unknown >> 1]
    const dx = unknown % 2 === 0 ? step : 0
    const dy = unknown % 2 === 0 ? 0 : step
    const moved = {
      ...drawing,
      points: drawing.points.map((p) => (p.id === id ? { ...p, x: p.x + dx, y: p.y + dy } : p)),
    }
    return errorsOf(moved, constraint)
  }
  const slope: number[][] = []
  for (let k = 0; k < ids.length * 2; k++) {
    const up = nudged(k, NUDGE)
    const down = nudged(k, -NUDGE)
    slope.push(here.map((_, i) => (up[i] - down[i]) / (2 * NUDGE)))
  }
  // Chapter VIII: "In order to make the constraints work well together, it is
  // necessary that they be balanced, that is that the partial derivative of error
  // with respect to displacement be nearly equal for all constraint types... many of
  // the existing constraint computation subroutines make the partial derivative
  // about unity."
  //
  // Every subroutine in the ERROR table returns a distance or a difference of
  // coordinates, so each already has a derivative of about one, which is where
  // Sutherland says the balancing belongs: in the writing of the subroutine, not at
  // run time. A step here used to divide each equation by its own gradient to force
  // exactly one. It went in when six rosettes fastened to a seventh collapsed
  // towards nothing, and it was the wrong fix for that: they collapsed because an
  // instance was being moved one point at a time, and forcing unity only hid it, by
  // flattening the difference between a constraint with an opinion and one with
  // barely any. It cost the hexagon of Figure 1.5. Five equal length statements
  // share their middle corner, so that corner's gradient is the sum of two unit
  // vectors and falls to zero as its two sides straighten, and dividing by it turned
  // the quietest equation in the drawing into the loudest. All six corners slid
  // round the rim into one point, which satisfies every condition asked of them and
  // is not a hexagon.
  return here.map((e, i) => ({ e, g: slope.map((row) => row[i]) }))
}

// SOLVE, from the section "Least mean squares fit to linearized constraints".
// Every constraint on this point gives one linear equation in the two unknowns of
// where to move it. There may be too few equations, exactly enough, or too many.
// SOLVE "converts the given equations into an independent set of equations whose
// solution will be a point of minimum mean squared error for the original set",
// and where that is not unique it "finds that solution which results in the minimum
// change from the existing value". The lambda below is what produces the second
// half of that: with too few equations it pulls the answer toward moving nothing.
const LAMBDA = 1e-4

function solve(rows: Array<{ g: number[]; e: number }>, unknowns: number): number[] {
  const a = Array.from({ length: unknowns }, (_, i) =>
    Array.from({ length: unknowns }, (_, j) => (i === j ? LAMBDA : 0)),
  )
  const b = new Array(unknowns).fill(0)
  for (const { g, e } of rows) {
    for (let i = 0; i < unknowns; i++) {
      for (let j = 0; j < unknowns; j++) a[i][j] += g[i] * g[j]
      b[i] -= g[i] * e
    }
  }
  return eliminate(a, b) ?? new Array(unknowns).fill(0)
}

// Gaussian elimination with partial pivoting, which is all the normal equations
// above need: two unknowns for a point, four for an instance.
function eliminate(a: number[][], b: number[]): number[] | null {
  const n = b.length
  for (let col = 0; col < n; col++) {
    let pivot = col
    for (let r = col + 1; r < n; r++) {
      if (Math.abs(a[r][col]) > Math.abs(a[pivot][col])) pivot = r
    }
    if (Math.abs(a[pivot][col]) < 1e-12) return null
    ;[a[col], a[pivot]] = [a[pivot], a[col]]
    ;[b[col], b[pivot]] = [b[pivot], b[col]]
    for (let r = col + 1; r < n; r++) {
      const factor = a[r][col] / a[col][col]
      for (let c = col; c < n; c++) a[r][c] -= factor * a[col][c]
      b[r] -= factor * b[col]
    }
  }
  const x = new Array(n).fill(0)
  for (let r = n - 1; r >= 0; r--) {
    let sum = b[r]
    for (let c = r + 1; c < n; c++) sum -= a[r][c] * x[c]
    x[r] = sum / a[r][r]
  }
  return x
}

// "Choose a variable. Re-evaluate it to reduce the total error introduced by all
// constraints in the system. Choose another variable and repeat."
//
// The latest value of every variable is used at every step, which Sutherland says
// is important and is the reason only one value per variable is ever stored:
// "Former values not only may, but must be discarded."
// How far one point may move in one pass.
//
// A distance constraint linearizes badly over a long correction, so the exact
// least-squares answer can throw a point clear across the figure. Six equal chords
// on a circle are satisfied by a regular hexagon and also by a zigzag that walks
// back and forth between two positions, and a jump lands in whichever basin it
// happens to reach. Stepping instead of jumping keeps relaxation in the basin it
// started in, which is the one the reader drew.
const STEP = 12

export function relax(drawing: Drawing, passes = 400): Drawing {
  if (drawing.constraints.length === 0) return drawing
  // Instances move as a unit here for the same reason they do in the one pass
  // method below. Nudging an instance's origin without its handle turns and resizes
  // the instance as a side effect, which moves every point attached to it, which
  // makes more error than it removed. Six rosettes fastened to a seventh flew off
  // the sheet that way, and no number of passes brought them back.
  const variables = variablesOf(drawing)
  let current = drawing
  let used = 0
  for (let pass = 0; pass < passes; pass++) {
    used = pass + 1
    let worst = 0
    for (const variable of variables) {
      const rows = current.constraints
        .filter((c) => movable(c, variable.points))
        .flatMap((c) => sensitivity(current, c, variable.points))
      if (rows.length === 0) continue
      const unknowns = variable.points.length * 2
      const raw = solve(rows, unknowns)
      if (raw.some((v) => !Number.isFinite(v))) continue
      const reach = Math.hypot(...raw)
      const scale = reach > STEP ? STEP / reach : 1
      const move = raw.map((v) => v * scale)
      worst = Math.max(worst, ...move.map(Math.abs))
      current = {
        ...current,
        points: current.points.map((p) => {
          const at = variable.points.indexOf(p.id)
          return at < 0 ? p : { ...p, x: p.x + move[at * 2], y: p.y + move[at * 2 + 1] }
        }),
      }
    }
    // "Since each step makes some net reduction of total error, there will be
    // monotonic decrease of error and thus stability is assured." So stopping when
    // nothing moved any more is safe.
    if (worst < 0.01) break
  }
  return { ...current, settled: { method: 'relaxation', passes: used, ordered: 0 } }
}

// ONE PASS METHOD, Chapter VIII.
//
// "Sketchpad can often find an order in which the variables of a drawing may be
// re-evaluated to completely satisfy all the conditions on them in just one pass.
// For the cases in which the one pass method works, it is far better than
// relaxation: it gives correct answers at once; relaxation may not give a correct
// solution in any finite time."
//
// The picture this produces is the picture relaxation was already producing. What
// changes is how it gets there, and whether it arrives at all.

// What counts as one variable.
//
// A loose point is one variable with two degrees of freedom. An instance is one
// variable with four, because its position, its angle and its size move together
// and no two of them can be settled apart from the third. Chapter VII treats
// instance constraint satisfaction "as a four dimensional problem" for that reason,
// and counting an instance as two independent points instead gets a different and
// wrong answer: the origin is solved against a handle that has not moved yet.
//
// The unknowns of a variable are the x and y of each of its points, in order.
type Variable = { id: string; points: string[]; constraints: Constraint[] }

function movable(constraint: Constraint, ids: string[]): boolean {
  return (MOVABLE[constraint.kind] ?? []).some((i) => ids.includes(constraint.vars[i]))
}

// A fixed point is not a variable, so an instance pinned at both ends is not one
// either, and an instance pinned at one end is only the end that is loose.
function variablesOf(drawing: Drawing): Array<{ id: string; points: string[] }> {
  const fixed = new Set(drawing.points.filter((p) => p.fixed).map((p) => p.id))
  const spoken = new Set<string>()
  const out: Array<{ id: string; points: string[] }> = []
  for (const instance of drawing.instances) {
    const loose = [instance.origin, instance.handle].filter((id) => !fixed.has(id))
    loose.forEach((id) => spoken.add(id))
    if (loose.length === 2) out.push({ id: instance.id, points: loose })
    else loose.forEach((id) => out.push({ id, points: [id] }))
  }
  for (const point of drawing.points) {
    if (fixed.has(point.id) || spoken.has(point.id)) continue
    out.push({ id: point.id, points: [point.id] })
  }
  return out
}

// "Suppose that some variable can be found which has so few constraints applying to
// it that it can be re-evaluated to completely satisfy all of them. Such a variable
// we shall call a 'free' variable. As soon as a variable is recognized as free, the
// constraints which apply to it are removed from further consideration, because the
// free variable can be used to satisfy them. Removing these constraints, however,
// may make adjacent variables free... and so on throughout the maze of constraints.
// The manner in which freedom spreads is much like the method used in Moore's
// algorithm to find the shortest path through a maze."
//
// Every constraint states as many equations as it removes degrees of freedom, which
// is the rule the ERROR table already follows, and that is what makes this
// countable at all. Null means the walk stalled with constraints still unaccounted
// for, which is the honest answer rather than a bad one: Sutherland says ordering
// "cannot be found for the bridge truss problems illustrated in the last chapter."
export function freedoms(drawing: Drawing): Variable[] | null {
  const active = new Map(drawing.constraints.map((c) => [c.id, c]))
  const waiting = variablesOf(drawing)
  const found: Variable[] = []

  for (;;) {
    let spread = false
    for (let i = waiting.length - 1; i >= 0; i--) {
      const { id, points } = waiting[i]
      const on = [...active.values()].filter((c) => movable(c, points))
      const cost = on.reduce((n, c) => n + errorsOf(drawing, c).length, 0)
      if (cost > points.length * 2) continue
      for (const c of on) active.delete(c.id)
      waiting.splice(i, 1)
      found.push({ id, points, constraints: on })
      spread = true
    }
    if (!spread) break
  }
  // Every constraint has to have found somebody to satisfy it. One left over means
  // no ordering exists and the drawing needs relaxation.
  return active.size === 0 ? found : null
}

// A free variable is solved outright rather than nudged, so the loop below is
// Newton's method on its own equations and not another relaxation. It runs to a
// tolerance far tighter than relaxation's, because it can afford to.
const SETTLED = 1e-9
const NEWTON = 40

// "Having found that a collection of variables is free, Sketchpad will re-evaluate
// them in the reverse order, saving the first-found free variable until last. In
// re-evaluating any particular free variable Sketchpad uses only those constraints
// which were present when that variable was found to be free."
//
// Reverse is the whole trick. A constraint handed to the variable found at step k
// cannot touch any variable found before k, because those took their constraints
// with them when they were found. So its other variables were all found after k,
// and going backwards means they are already final by the time k is solved.
function evaluate(drawing: Drawing, order: Variable[]): Drawing {
  let current = drawing
  for (let i = order.length - 1; i >= 0; i--) {
    const { points, constraints } = order[i]
    if (constraints.length === 0) continue
    const unknowns = points.length * 2
    for (let step = 0; step < NEWTON; step++) {
      const rows = constraints.flatMap((c) => sensitivity(current, c, points))
      if (rows.every((r) => Math.abs(r.e) < SETTLED)) break
      const move = solve(rows, unknowns)
      if (move.some((v) => !Number.isFinite(v))) break
      current = {
        ...current,
        points: current.points.map((p) => {
          const at = points.indexOf(p.id)
          return at < 0 ? p : { ...p, x: p.x + move[at * 2], y: p.y + move[at * 2 + 1] }
        }),
      }
    }
  }
  return current
}

// "When the one pass method of satisfying constraints to be described later on
// fails, the Sketchpad system falls back on the reliable but slow method of
// relaxation."
//
// So this is the entry point and the two methods are its two answers. The drawing
// carries back which one ran, because on the scope they are indistinguishable, and
// that is the point: the conditions are what the reader asked for, and how they
// were met is the program's own business.
export function satisfy(drawing: Drawing): Drawing {
  if (drawing.constraints.length === 0) return drawing
  const order = freedoms(drawing)
  if (!order) return relax(drawing)
  const solved = evaluate(drawing, order)
  return { ...solved, settled: { method: 'one pass', passes: 1, ordered: order.length } }
}
