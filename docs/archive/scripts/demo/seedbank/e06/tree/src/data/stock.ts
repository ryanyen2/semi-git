// What the library actually has on the shelf this season.
export type Stock = 'in' | 'low' | 'out'

const STOCK: Record<string, Stock> = {
  'cherokee-purple-tomato': 'in',
  'dragon-tongue-bean': 'low',
  'black-beauty-zucchini': 'in',
  'chioggia-beet': 'out',
  'lacinato-kale': 'in',
  'moon-and-stars-watermelon': 'out',
  'golden-bantam-corn': 'in',
  'scarlet-runner-bean': 'in',
  'purple-top-turnip': 'in',
  'bloomsdale-spinach': 'low',
  'rouge-dhiver-lettuce': 'in',
  'marketmore-cucumber': 'low',
  'sugar-pie-pumpkin': 'out',
  'danvers-carrot': 'in',
  'detroit-dark-red-beet': 'in',
  'genovese-basil': 'out',
  'amish-paste-tomato': 'in',
  'jimmy-nardello-pepper': 'in',
  'red-russian-kale': 'low',
  'painted-mountain-corn': 'in',
  'yellow-pear-tomato': 'in',
  'delicata-squash': 'low',
  'french-breakfast-radish': 'in',
  'tromboncino-squash': 'low',
}

export function stockOf(id: string): Stock {
  return STOCK[id] ?? 'out'
}
