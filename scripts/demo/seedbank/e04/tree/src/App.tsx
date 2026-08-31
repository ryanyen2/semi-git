import { useState } from 'react'
import { Grid } from './Grid'
import { SearchBox } from './SearchBox'
import { SortBar, sortVarieties, type SortKey } from './SortBar'
import { search } from './search/rank'
import { VARIETIES } from './varieties'

export function App() {
  const [sort, setSort] = useState<SortKey>('name')
  const [query, setQuery] = useState('')

  const results = search(VARIETIES, query)
  // A query is an ordering of its own, so it wins over the sort bar while it
  // is there; the sort bar takes back over as soon as the box is empty.
  const shown =
    query.trim() === ''
      ? sortVarieties(
          results.map((r) => r.variety),
          sort,
        )
      : results.map((r) => r.variety)

  return (
    <div className="page">
      <header className="head">
        <h1>seedbank</h1>
        <p className="tag">community seed library · {VARIETIES.length} varieties</p>
        <SearchBox
          query={query}
          onQuery={setQuery}
          found={shown.length}
          total={VARIETIES.length}
        />
        <SortBar sort={sort} onSort={setSort} />
      </header>
      <Grid varieties={shown} />
    </div>
  )
}
