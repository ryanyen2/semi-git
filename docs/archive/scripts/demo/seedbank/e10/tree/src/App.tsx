import { useState } from 'react'
import { Chips } from './Chips'
import { Detail } from './Detail'
import { Grid } from './Grid'
import { SearchBox } from './SearchBox'
import { SeasonBar } from './SeasonBar'
import { SortBar, sortVarieties, type SortKey } from './SortBar'
import { ALL_TRAITS } from './data/traits'
import { inSeason, type Season } from './filters/season'
import { keepByTraits, toggleTrait } from './filters/traits'
import { search } from './search/rank'
import { VARIETIES } from './varieties'

export function App() {
  const [sort, setSort] = useState<SortKey>('name')
  const [query, setQuery] = useState('')
  const [openId, setOpenId] = useState<string | null>(null)
  const [traits, setTraits] = useState<string[]>([])
  const [season, setSeason] = useState<Season>('summer')

  const onToggleTrait = (trait: string) => setTraits((cur) => toggleTrait(cur, trait))

  const results = search(keepByTraits(VARIETIES, traits), query)
  // A query is an ordering of its own, so it wins over the sort bar while it
  // is there; the sort bar takes back over as soon as the box is empty.
  const shown =
    query.trim() === ''
      ? sortVarieties(
          results.map((r) => r.variety),
          sort,
        )
      : results.map((r) => r.variety)
  const open = VARIETIES.find((v) => v.id === openId) ?? null

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
          sowable={shown.filter((v) => inSeason(v.id, season)).length}
          season={season}
        />
        <SortBar sort={sort} onSort={setSort} />
        <div className="traitbar">
          <span className="bar-label">traits</span>
          <Chips traits={ALL_TRAITS} active={traits} onToggle={onToggleTrait} />
          {traits.length > 0 && (
            <button className="opt" onClick={() => setTraits([])}>
              clear
            </button>
          )}
        </div>
        <SeasonBar season={season} onSeason={setSeason} />
      </header>
      <Grid
        varieties={shown}
        onOpen={setOpenId}
        activeTraits={traits}
        onToggleTrait={onToggleTrait}
        season={season}
      />
      {open && (
        <Detail
          variety={open}
          onClose={() => setOpenId(null)}
          activeTraits={traits}
          onToggleTrait={onToggleTrait}
          season={season}
        />
      )}
    </div>
  )
}
