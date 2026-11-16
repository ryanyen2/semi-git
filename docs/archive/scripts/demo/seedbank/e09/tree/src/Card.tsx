import { Badge } from './Badge'
import { Chips } from './Chips'
import { Mark } from './Mark'
import { SowDots } from './SowDots'
import { traitsOf } from './data/traits'
import type { Variety } from './varieties'

export function Card({
  variety,
  onOpen,
  activeTraits,
  onToggleTrait,
}: {
  variety: Variety
  onOpen: (id: string) => void
  activeTraits: string[]
  onToggleTrait: (trait: string) => void
}) {
  return (
    <article
      className="card"
      role="button"
      tabIndex={0}
      onClick={() => onOpen(variety.id)}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault()
          onOpen(variety.id)
        }
      }}
    >
      <div className="card-top">
        <Mark name={variety.name} />
        <Badge id={variety.id} />
      </div>
      <h2 className="card-name">{variety.name}</h2>
      <p className="card-species">{variety.species}</p>
      <p className="card-meta">
        {variety.family} · {variety.daysToHarvest} days
      </p>
      <Chips traits={traitsOf(variety.id)} active={activeTraits} onToggle={onToggleTrait} />
      <SowDots id={variety.id} />
      <p className="card-open">details ›</p>
    </article>
  )
}
