import { traitsOf } from '../data/traits'
import type { Variety } from '../varieties'

export function toggleTrait(active: string[], trait: string): string[] {
  return active.includes(trait) ? active.filter((t) => t !== trait) : [...active, trait]
}

// Selecting two traits narrows rather than widens: a variety has to carry both.
export function keepByTraits(varieties: Variety[], active: string[]): Variety[] {
  if (active.length === 0) return varieties
  return varieties.filter((v) => {
    const traits = traitsOf(v.id)
    return active.every((t) => traits.includes(t))
  })
}
