// The things a grower asks about before anything else on the packet.
export const ALL_TRAITS = [
  'heirloom',
  'quick',
  'cold-hardy',
  'shade-ok',
  'container',
  'drought-tolerant',
  'pollinator',
  'seed-saver',
]

const TRAITS: Record<string, string[]> = {
  'cherokee-purple-tomato': ['heirloom', 'seed-saver'],
  'dragon-tongue-bean': ['heirloom', 'container', 'quick'],
  'black-beauty-zucchini': ['quick', 'container'],
  'chioggia-beet': ['heirloom', 'cold-hardy'],
  'lacinato-kale': ['cold-hardy', 'shade-ok', 'heirloom'],
  'moon-and-stars-watermelon': ['heirloom', 'drought-tolerant'],
  'golden-bantam-corn': ['heirloom', 'pollinator'],
  'scarlet-runner-bean': ['pollinator', 'heirloom', 'cold-hardy'],
  'purple-top-turnip': ['cold-hardy', 'quick'],
  'bloomsdale-spinach': ['shade-ok', 'quick', 'cold-hardy'],
  'rouge-dhiver-lettuce': ['shade-ok', 'cold-hardy', 'heirloom'],
  'marketmore-cucumber': ['container', 'quick'],
  'sugar-pie-pumpkin': ['heirloom', 'drought-tolerant'],
  'danvers-carrot': ['heirloom', 'cold-hardy'],
  'detroit-dark-red-beet': ['heirloom', 'quick', 'cold-hardy'],
  'genovese-basil': ['container', 'shade-ok'],
  'amish-paste-tomato': ['heirloom', 'seed-saver', 'drought-tolerant'],
  'jimmy-nardello-pepper': ['heirloom', 'container'],
  'red-russian-kale': ['cold-hardy', 'shade-ok'],
  'painted-mountain-corn': ['drought-tolerant', 'cold-hardy', 'seed-saver'],
  'yellow-pear-tomato': ['heirloom', 'container', 'seed-saver'],
  'delicata-squash': ['heirloom', 'seed-saver'],
  'french-breakfast-radish': ['quick', 'container', 'cold-hardy'],
  'tromboncino-squash': ['drought-tolerant', 'heirloom', 'pollinator'],
}

export function traitsOf(id: string): string[] {
  return TRAITS[id] ?? []
}
