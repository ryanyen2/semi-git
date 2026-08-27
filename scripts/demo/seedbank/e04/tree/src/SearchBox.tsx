export function SearchBox({
  query,
  onQuery,
  found,
  total,
}: {
  query: string
  onQuery: (query: string) => void
  found: number
  total: number
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
        {found} of {total}
      </span>
    </div>
  )
}
