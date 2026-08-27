import { SEASONS, type Season } from './filters/season'

export function SeasonBar({
  season,
  onSeason,
}: {
  season: Season
  onSeason: (season: Season) => void
}) {
  return (
    <div className="seasonbar">
      <span className="bar-label">sowing now</span>
      {SEASONS.map((s) => (
        <button
          key={s}
          className={s === season ? 'opt is-on' : 'opt'}
          onClick={() => onSeason(s)}
        >
          {s}
        </button>
      ))}
    </div>
  )
}
