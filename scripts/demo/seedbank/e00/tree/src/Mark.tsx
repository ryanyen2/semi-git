// A botanical mark generated from the variety's name. Every visual in this repo
// is generated, so there are no binary assets to carry through a fold.

// The page uses three hues and nothing else, so the marks draw from the same
// three. The name still decides everything about the shape, which is what makes
// 24 marks look different from one another.
const HUES = [205, 145, 28]

function seedOf(name: string): number {
  let h = 2166136261
  for (let i = 0; i < name.length; i++) {
    h ^= name.charCodeAt(i)
    h = Math.imul(h, 16777619)
  }
  return h >>> 0
}

export function Mark({ name }: { name: string }) {
  const seed = seedOf(name)
  const hue = HUES[seed % HUES.length] + (((seed >> 5) % 9) - 4)
  const petals = 4 + ((seed >> 9) % 4)
  const spread = 12 + ((seed >> 13) % 9)
  const lift = ((seed >> 19) % 14) - 7

  return (
    <svg className="mark" viewBox="0 0 64 64" role="presentation">
      <circle cx="32" cy="32" r="30" fill={`hsl(${hue} 62% 96%)`} />
      {Array.from({ length: petals }, (_, i) => (
        <ellipse
          key={i}
          cx="32"
          cy="19"
          rx={spread * 0.42}
          ry="13"
          fill={`hsl(${hue} 55% ${48 + i * 3}%)`}
          transform={`rotate(${(360 / petals) * i + lift} 32 32)`}
        />
      ))}
      <circle cx="32" cy="32" r="6" fill={`hsl(${hue} 60% 30%)`} />
    </svg>
  )
}
