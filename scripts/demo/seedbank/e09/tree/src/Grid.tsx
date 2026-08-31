import { Card } from './Card'
import type { Variety } from './varieties'

export function Grid({
  varieties,
  onOpen,
  activeTraits,
  onToggleTrait,
}: {
  varieties: Variety[]
  onOpen: (id: string) => void
  activeTraits: string[]
  onToggleTrait: (trait: string) => void
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
        />
      ))}
    </div>
  )
}
