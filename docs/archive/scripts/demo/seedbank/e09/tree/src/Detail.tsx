import { Badge } from './Badge'
import { Chips } from './Chips'
import { Mark } from './Mark'
import { SowDots } from './SowDots'
import { traitsOf } from './data/traits'
import type { Variety } from './varieties'

export function Detail({
  variety,
  onClose,
  activeTraits,
  onToggleTrait,
}: {
  variety: Variety
  onClose: () => void
  activeTraits: string[]
  onToggleTrait: (trait: string) => void
}) {
  return (
    <aside className="detail" aria-label={variety.name}>
      <button className="detail-close" onClick={onClose} aria-label="close">
        ×
      </button>
      <Mark name={variety.name} />
      <h2 className="detail-name">{variety.name}</h2>
      <p className="card-species">{variety.species}</p>
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
      <p className="detail-label">traits</p>
      <Chips traits={traitsOf(variety.id)} active={activeTraits} onToggle={onToggleTrait} />
      <p className="detail-label">sow</p>
      <SowDots id={variety.id} />
    </aside>
  )
}
