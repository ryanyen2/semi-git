import { Badge } from './Badge'
import { Mark } from './Mark'
import { SowDots } from './SowDots'
import type { Variety } from './varieties'

export function Card({ variety, onOpen }: { variety: Variety; onOpen: (id: string) => void }) {
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
      <SowDots id={variety.id} />
      <p className="card-open">details ›</p>
    </article>
  )
}
