// A season is a window on the sowing calendar. Nothing is hidden by it: a
// variety you cannot sow now is still in the catalog, it is just not for today.
import { sowMonths } from '../data/sowing'

export type Season = 'spring' | 'summer' | 'autumn' | 'winter'

export const SEASONS: Season[] = ['spring', 'summer', 'autumn', 'winter']

const MONTHS: Record<Season, number[]> = {
  spring: [3, 4, 5],
  summer: [6, 7, 8],
  autumn: [9, 10, 11],
  winter: [12, 1, 2],
}

export function seasonMonths(season: Season): number[] {
  return MONTHS[season]
}

export function inSeason(id: string, season: Season): boolean {
  const months = seasonMonths(season)
  return sowMonths(id).some((m) => months.includes(m))
}
