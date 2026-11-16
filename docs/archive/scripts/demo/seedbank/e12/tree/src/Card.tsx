import { Badge } from './Badge'
import { Chips } from './Chips'
import { Highlight, tokensFor } from './Highlight'
import { Mark } from './Mark'
import { SowDots } from './SowDots'
import { TrayButton } from './Tray'
import { traitsOf } from './data/traits'
import { inSeason, type Season } from './filters/season'
import type { Hit } from './search/match'
import type { Variety } from './varieties'

export function Card({
  variety,
  onOpen,
  activeTraits,
  onToggleTrait,
  season,
  hits,
}: {
  variety: Variety
  onOpen: (id: string) => void
  activeTraits: string[]
  onToggleTrait: (trait: string) => void
  season: Season
  hits: Hit[]
}) {
  const sowable = inSeason(variety.id, season)
  return (
    <article
      className={sowable ? 'card' : 'card is-off-season'}
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
        <TrayButton id={variety.id} />
      </div>
      <h2 className="card-name">
        <Highlight text={variety.name} tokens={tokensFor(hits, 'name')} />
      </h2>
      <p className="card-species">{variety.species}</p>
      <p className="card-meta">
        {variety.family} · {variety.daysToHarvest} days
      </p>
      <Chips traits={traitsOf(variety.id)} active={activeTraits} onToggle={onToggleTrait} />
      <SowDots id={variety.id} season={season} />
      <p className="card-open">details ›</p>
    </article>
  )
}
