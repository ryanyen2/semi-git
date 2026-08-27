import { Badge } from './Badge'
import { Chips } from './Chips'
import { Highlight, tokensFor } from './Highlight'
import { Mark } from './Mark'
import { SowDots } from './SowDots'
import { traitsOf } from './data/traits'
import { inSeason, type Season } from './filters/season'
import type { Hit } from './search/match'
import type { Variety } from './varieties'

export function Detail({
  variety,
  onClose,
  activeTraits,
  onToggleTrait,
  season,
  hits,
}: {
  variety: Variety
  onClose: () => void
  activeTraits: string[]
  onToggleTrait: (trait: string) => void
  season: Season
  hits: Hit[]
}) {
  return (
    <aside className="detail" aria-label={variety.name}>
      <button className="detail-close" onClick={onClose} aria-label="close">
        ×
      </button>
      <Mark name={variety.name} />
      <h2 className="detail-name">
        <Highlight text={variety.name} tokens={tokensFor(hits, 'name')} />
      </h2>
      <p className="card-species">
        <Highlight text={variety.species} tokens={tokensFor(hits, 'species')} />
      </p>
      <dl className="detail-facts">
        <dt>family</dt>
        <dd>{variety.family}</dd>
        <dt>days to harvest</dt>
        <dd>{variety.daysToHarvest}</dd>
        <dt>availability</dt>
        <dd>
          <Badge id={variety.id} />
        </dd>
      </dl>
      {!inSeason(variety.id, season) && (
        <p className="detail-note">not one to sow in {season}</p>
      )}
      <p className="detail-label">traits</p>
      <Chips traits={traitsOf(variety.id)} active={activeTraits} onToggle={onToggleTrait} />
      <p className="detail-label">sow</p>
      <SowDots id={variety.id} season={season} />
    </aside>
  )
}
