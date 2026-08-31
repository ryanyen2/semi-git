import { sowMonths } from './data/sowing'

const MONTHS = [
  'January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December',
]

export function SowDots({ id }: { id: string }) {
  const months = sowMonths(id)
  return (
    <div className="sowdots" aria-label="sowing months">
      {MONTHS.map((month, i) => (
        <span
          key={month}
          className={months.includes(i + 1) ? 'dot is-sow' : 'dot'}
          title={month}
        />
      ))}
    </div>
  )
}
