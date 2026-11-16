import { Grid } from './Grid'
import { VARIETIES } from './varieties'

export function App() {
  return (
    <div className="page">
      <header className="head">
        <h1>seedbank</h1>
        <p className="tag">community seed library · {VARIETIES.length} varieties</p>
      </header>
      <Grid varieties={VARIETIES} />
    </div>
  )
}
