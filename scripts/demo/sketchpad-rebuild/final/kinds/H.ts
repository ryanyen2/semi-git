import { register } from './registry'

// Appendix A, code 27, letter H. One error, because being level removes one degree
// of freedom and leaves the line free to slide along itself.
register('H', ([a, b], constraint) => [constraint.upright ? a.x - b.x : a.y - b.y], [0, 1])
