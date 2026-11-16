import { Mark } from './Mark'
import type { Variety } from './varieties'

export function Card({ variety }: { variety: Variety }) {
  return (
    <article className="card">
      <Mark name={variety.name} />
      <h2 className="card-name">{variety.name}</h2>
      <p className="card-species">{variety.species}</p>
      <p className="card-meta">
        {variety.family} · {variety.daysToHarvest} days
      </p>
    </article>
  )
}
