import { Card } from './Card'
import type { Variety } from './varieties'

export function Grid({ varieties }: { varieties: Variety[] }) {
  return (
    <div className="grid">
      {varieties.map((v) => (
        <Card key={v.id} variety={v} />
      ))}
    </div>
  )
}
