import { Badge } from './Badge'
import { Mark } from './Mark'
import { SowDots } from './SowDots'
import type { Variety } from './varieties'

export function Card({ variety }: { variety: Variety }) {
  return (
    <article className="card">
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
    </article>
  )
}
