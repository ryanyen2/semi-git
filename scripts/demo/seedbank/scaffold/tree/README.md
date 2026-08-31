# seedbank

A community seed library catalog: a grid of 24 seed varieties, one card each.

This repo is a **demo fixture for [sgt](https://github.com/ryanyen2/semi-git)**, not a product.
Its history was written on purpose, one `sgt save` per episode, so that manipulating that
history produces changes you can see in a browser rather than read in a diff.

## Run it

```
npm install
npm run dev
```

There are no binary assets. Every visual is generated SVG or CSS, so the whole repo is text and
sgt's fold can be checked byte-for-byte.

## Provenance in the rendered page

`tools/vite-plugin-sgt-loc.mjs` stamps every host element with `data-sgt-loc="<file>:<line>"`.
Open the page and inspect a card:

```html
<article data-sgt-loc="src/Card.tsx:28" class="card" role="button">
```

That line number lands inside the span `sgt advanced blame src/Card.tsx` reports for
`src/Card.tsx::Card`, which names the feature that owns it. So a rendered region resolves to a
symbol, and the symbol to a stripe of history -- which is the whole point of the demo. It is a
development-time mechanism: a production build carries no such attribute, and does not need one.

## What it is built to show

1. **A silent gap.** The search engine (episodes 2 and 3) lands and renders nothing at all.
   Two consecutive saves change zero pixels on the landing page. Episode 4 wires the same code
   to a search box and it appears. Scrubbing into that gap shows an app that knows how to search
   and offers no way to.
2. **A cross-cutting feature.** The season filter (episode 10) is one idea spread over six
   component files. One revert changes the whole page.
3. **Subtraction.** Any feature can be taken out of *today's* app, producing a state that was
   never committed and that no `git checkout` can reach.

## The episodes

| # | save | what changes on screen |
|---|---|---|
| 0 | the catalog | 24 variety cards, each with a generated botanical mark |
| 1 | sort control | a sort bar appears; the grid reorders to alphabetical |
| 2 | search matching | **nothing** — `src/search/match.ts` is imported by no one |
| 3 | scoring + synonyms | **nothing** — `rank.ts` and `synonyms.ts` are still unused |
| 4 | the search box | a search field and a result count appear; typing filters the grid |
| 5 | sowing calendar | twelve month dots on every card, filled for its sowing months |
| 6 | availability | an in stock / low / sold out badge on every card |
| 7 | detail panel | every card gains a "Details" affordance; clicking opens a panel |
| 8 | trait chips | two or three trait chips on every card and in the detail panel |
| 9 | filter by trait | a trait filter bar in the header; card chips become toggles |
| 10 | season filter | out-of-season cards dim, the count changes, dots highlight, detail warns |
| 11 | match highlighting | example queries under the search box; matches highlight in titles |

Episodes 2 and 3 are the silent ones, and they are silent on purpose.

## Layout

```
src/
  varieties.ts      the 24 varieties (name, species, family, days to harvest)
  data/             per-feature data: sowing months, stock levels, traits
  search/           match, rank, synonyms — the engine behind the search box
  filters/          trait and season predicates
  *.tsx             components
```
