import type { Constraint, Drawing, Kind, Point } from '../drawing'

// The generic blocks of Chapter VIII: a constraint type "tells how many variables
// are constrained, which of these variables may be changed in order to satisfy the
// constraint, how many degrees of freedom are removed from the constrained
// variables, and a code letter for human reference to this constraint type."
// Nothing else in the program refers to a particular type. Each type is one file in
// this directory, registering its error subroutine and its movable variables here;
// the solver reads the tables and never names a letter.
//
// A constraint type returns one error per degree of freedom it removes, not one
// error. Chapter VIII is explicit and it is not a detail: constraints "must have as
// many error computation subroutines as there are degrees of freedom lost since each
// subroutine results in a single linear equation. A subroutine which computes the
// distance from a variable to its correct location without regard to the number of
// degrees of freedom being removed will cause erratic results."

export type ErrorSubroutine = (vars: Point[], constraint: Constraint, drawing: Drawing) => number[]

// Indexed by the code letter. Not a Record over Kind: a drawing may carry a
// condition of a type this program does not have, and the tables answer for what
// the program has, not for what drawings may say.
export const ERROR: { [kind: string]: ErrorSubroutine } = {}
export const MOVABLE: { [kind: string]: number[] } = {}

export function register(kind: Kind, error: ErrorSubroutine, movable: number[]) {
  ERROR[kind] = error
  MOVABLE[kind] = movable
}
