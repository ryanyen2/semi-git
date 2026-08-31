// What people type instead of what is printed on the packet. Substitution is
// one-way: the packet name already matches itself.
const SYNONYMS: Record<string, string> = {
  courgette: 'zucchini',
  beetroot: 'beet',
  capsicum: 'pepper',
  sweetcorn: 'corn',
  marrow: 'squash',
  cuke: 'cucumber',
  aubergine: 'eggplant',
  mangetout: 'pea',
}

export function substitute(tokens: string[]): string[] {
  return tokens.map((t) => SYNONYMS[t] ?? t)
}
