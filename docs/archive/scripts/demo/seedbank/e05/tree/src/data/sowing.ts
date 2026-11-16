// When each variety goes in the ground, as month numbers. Direct sow or under
// cover -- the catalog does not distinguish, and neither does the calendar.
const SOW_MONTHS: Record<string, number[]> = {
  'cherokee-purple-tomato': [4, 5, 6],
  'dragon-tongue-bean': [5, 6, 7],
  'black-beauty-zucchini': [5, 6, 7],
  'chioggia-beet': [3, 4, 5, 9],
  'lacinato-kale': [4, 5, 6, 7],
  'moon-and-stars-watermelon': [5, 6],
  'golden-bantam-corn': [5, 6, 7],
  'scarlet-runner-bean': [5, 6, 7],
  'purple-top-turnip': [3, 4, 9, 10],
  'bloomsdale-spinach': [3, 4, 9, 10],
  'rouge-dhiver-lettuce': [3, 4, 5, 9, 10],
  'marketmore-cucumber': [5, 6, 7],
  'sugar-pie-pumpkin': [5, 6],
  'danvers-carrot': [3, 4, 5],
  'detroit-dark-red-beet': [4, 5, 9],
  'genovese-basil': [4, 5, 6, 7, 8],
  'amish-paste-tomato': [4, 5, 6],
  'jimmy-nardello-pepper': [3, 4, 5, 6],
  'red-russian-kale': [3, 4, 5, 9],
  'painted-mountain-corn': [5, 6, 7],
  'yellow-pear-tomato': [4, 5, 6],
  'delicata-squash': [5, 6, 7],
  'french-breakfast-radish': [3, 4, 5, 9, 10],
  'tromboncino-squash': [5, 6, 7],
}

export function sowMonths(id: string): number[] {
  return SOW_MONTHS[id] ?? []
}
