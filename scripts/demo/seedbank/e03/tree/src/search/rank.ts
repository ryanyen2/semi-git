// Scoring and ordering for a query. A hit in the variety's own name counts for
// more than one in its family, and a hit at the start of a word counts for more
// than one in the middle -- so "bean" puts Dragon Tongue Bean above the Legumes.
import { matchVariety, tokenize, type Field, type Hit } from './match'
import { substitute } from './synonyms'
import type { Variety } from '../varieties'

export type Result = { variety: Variety; hits: Hit[]; score: number }

const WEIGHT: Record<Field, number> = { name: 3, species: 2, family: 1 }

export function score(hits: Hit[]): number {
  return hits.reduce((total, h) => total + WEIGHT[h.field] * (h.prefix ? 2 : 1), 0)
}

export function search(varieties: Variety[], query: string): Result[] {
  const tokens = substitute(tokenize(query))
  if (tokens.length === 0) return varieties.map((variety) => ({ variety, hits: [], score: 0 }))

  const results: Result[] = []
  for (const variety of varieties) {
    const hits = matchVariety(variety, tokens)
    if (hits) results.push({ variety, hits, score: score(hits) })
  }
  return results.sort((a, b) => b.score - a.score)
}
