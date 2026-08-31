const EXAMPLES = ['courgette', 'kale', 'purple']

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
    <div className="search">
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
      <div className="search-examples">
        <span className="bar-label">try</span>
        {EXAMPLES.map((example) => (
          <button key={example} className="opt" onClick={() => onQuery(example)}>
            {example}
          </button>
        ))}
      </div>
    </div>
  )
}
