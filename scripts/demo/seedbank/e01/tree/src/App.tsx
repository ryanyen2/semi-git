import { useState } from 'react'
import { Grid } from './Grid'
import { SortBar, sortVarieties, type SortKey } from './SortBar'
import { VARIETIES } from './varieties'

export function App() {
  const [sort, setSort] = useState<SortKey>('name')
  const shown = sortVarieties(VARIETIES, sort)

  return (
    <div className="page">
      <header className="head">
        <h1>seedbank</h1>
        <p className="tag">community seed library · {VARIETIES.length} varieties</p>
        <SortBar sort={sort} onSort={setSort} />
      </header>
      <Grid varieties={shown} />
    </div>
  )
}
