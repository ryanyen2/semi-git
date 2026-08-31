import { register } from './registry'

// Appendix A, code 24, letter E: "Thing is erect or on its side." One error,
// because standing something up removes its angle and leaves its position alone.
// The handle turns; the origin is where the reader put the thing.
register('E', ([origin, handle]) => [handle.y - origin.y], [1])
