import type { Field, Hit } from './search/match'

export function tokensFor(hits: Hit[], field: Field): string[] {
  return hits.filter((h) => h.field === field).map((h) => h.token)
}

// The tokens come out of the matcher, so a synonym highlights the word that was
// actually matched: searching "courgette" marks "Zucchini".
export function Highlight({ text, tokens }: { text: string; tokens: string[] }) {
  if (tokens.length === 0) return <>{text}</>

  const flat = text.toLowerCase()
  const spans: [number, number][] = []
  for (const token of tokens) {
    for (let at = flat.indexOf(token); at !== -1; at = flat.indexOf(token, at + token.length)) {
      spans.push([at, at + token.length])
    }
  }
  spans.sort((a, b) => a[0] - b[0])

  const parts: { text: string; mark: boolean }[] = []
  let at = 0
  for (const [start, end] of spans) {
    if (end <= at) continue
    const from = Math.max(start, at)
    if (from > at) parts.push({ text: text.slice(at, from), mark: false })
    parts.push({ text: text.slice(from, end), mark: true })
    at = end
  }
  if (at < text.length) parts.push({ text: text.slice(at), mark: false })

  return (
    <>
      {parts.map((part, i) =>
        part.mark ? (
          <mark key={i} className="hl">
            {part.text}
          </mark>
        ) : (
          <span key={i}>{part.text}</span>
        ),
      )}
    </>
  )
}
