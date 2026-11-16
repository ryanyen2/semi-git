import { Card } from './Card'
import type { Variety } from './varieties'

export function Grid({
  varieties,
  onOpen,
}: {
  varieties: Variety[]
  onOpen: (id: string) => void
}) {
  return (
    <div className="grid">
      {varieties.map((v) => (
        <Card key={v.id} variety={v} onOpen={onOpen} />
      ))}
    </div>
  )
}
