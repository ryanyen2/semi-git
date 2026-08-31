// Matching a query against one variety. Fields are matched independently and
// every token has to land somewhere, so "purple tomato" finds the Cherokee
// Purple but not the Purple Top Turnip.
import type { Variety } from '../varieties'

export type Field = 'name' | 'species' | 'family'

export const FIELDS: Field[] = ['name', 'species', 'family']

export type Hit = {
  field: Field
  token: string
  start: number
  end: number
  prefix: boolean
}

export function normalize(text: string): string {
  return text
    .toLowerCase()
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .replace(/[^a-z0-9]+/g, ' ')
    .trim()
}

export function tokenize(query: string): string[] {
  const flat = normalize(query)
  return flat === '' ? [] : flat.split(' ')
}

// The first place `token` appears in `text`, preferring a word start.
export function matchField(text: string, field: Field, token: string): Hit | null {
  const flat = normalize(text)
  if (flat === '') return null

  for (const word of flat.matchAll(/\S+/g)) {
    const at = word.index
    if (flat.startsWith(token, at)) {
      return { field, token, start: at, end: at + token.length, prefix: true }
    }
  }

  const at = flat.indexOf(token)
  if (at === -1) return null
  return { field, token, start: at, end: at + token.length, prefix: false }
}

// Every token must hit at least one field. The hits come back so a caller can
// score them or paint them.
export function matchVariety(variety: Variety, tokens: string[]): Hit[] | null {
  const hits: Hit[] = []
  for (const token of tokens) {
    const found = FIELDS.map((f) => matchField(variety[f], f, token)).filter(
      (h): h is Hit => h !== null,
    )
    if (found.length === 0) return null
    hits.push(...found)
  }
  return hits
}
