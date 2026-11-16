import { stockOf, type Stock } from './data/stock'

const LABEL: Record<Stock, string> = {
  in: 'in stock',
  low: 'low',
  out: 'sold out',
}

export function Badge({ id }: { id: string }) {
  const stock = stockOf(id)
  return <span className={`badge is-${stock}`}>{LABEL[stock]}</span>
}
