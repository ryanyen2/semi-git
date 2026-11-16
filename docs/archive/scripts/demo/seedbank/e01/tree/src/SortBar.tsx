import type { Variety } from './varieties'

export type SortKey = 'name' | 'species' | 'days'

const OPTIONS: { key: SortKey; label: string }[] = [
  { key: 'name', label: 'name' },
  { key: 'species', label: 'species' },
  { key: 'days', label: 'days to harvest' },
]

export function sortVarieties(varieties: Variety[], key: SortKey): Variety[] {
  const sorted = [...varieties]
  if (key === 'days') sorted.sort((a, b) => a.daysToHarvest - b.daysToHarvest)
  else sorted.sort((a, b) => a[key].localeCompare(b[key]))
  return sorted
}

export function SortBar({ sort, onSort }: { sort: SortKey; onSort: (key: SortKey) => void }) {
  return (
    <div className="sortbar">
      <span className="bar-label">sort by</span>
      {OPTIONS.map((o) => (
        <button
          key={o.key}
          className={o.key === sort ? 'opt is-on' : 'opt'}
          onClick={() => onSort(o.key)}
        >
          {o.label}
        </button>
      ))}
    </div>
  )
}
