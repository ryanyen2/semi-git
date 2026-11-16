import { Card } from './Card'
import type { Season } from './filters/season'
import type { Variety } from './varieties'

export function Grid({
  varieties,
  onOpen,
  activeTraits,
  onToggleTrait,
  season,
}: {
  varieties: Variety[]
  onOpen: (id: string) => void
  activeTraits: string[]
  onToggleTrait: (trait: string) => void
  season: Season
}) {
  return (
    <div className="grid">
      {varieties.map((v) => (
        <Card
          key={v.id}
          variety={v}
          onOpen={onOpen}
          activeTraits={activeTraits}
          onToggleTrait={onToggleTrait}
          season={season}
        />
      ))}
    </div>
  )
}
