import { sowMonths } from './data/sowing'
import { seasonMonths, type Season } from './filters/season'

const MONTHS = [
  'January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December',
]

export function SowDots({ id, season }: { id: string; season: Season }) {
  const months = sowMonths(id)
  const window = seasonMonths(season)
  return (
    <div className="sowdots" aria-label="sowing months">
      {MONTHS.map((month, i) => {
        const classes = ['dot']
        if (months.includes(i + 1)) classes.push('is-sow')
        if (window.includes(i + 1)) classes.push('is-season')
        return <span key={month} className={classes.join(' ')} title={month} />
      })}
    </div>
  )
}
