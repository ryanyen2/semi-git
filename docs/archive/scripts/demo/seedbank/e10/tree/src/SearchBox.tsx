export function SearchBox({
  query,
  onQuery,
  found,
  total,
  sowable,
  season,
}: {
  query: string
  onQuery: (query: string) => void
  found: number
  total: number
  sowable: number
  season: string
}) {
  return (
    <div className="searchbox">
      <input
        className="search-input"
        type="search"
        value={query}
        placeholder="search varieties"
        aria-label="search varieties"
        onChange={(e) => onQuery(e.target.value)}
      />
      <span className="search-count">
        {found} of {total} · {sowable} to sow in {season}
      </span>
    </div>
  )
}
