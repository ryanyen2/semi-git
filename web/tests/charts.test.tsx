// Render each paper figure and check the output is a real, self-contained SVG.
//
// The export path is the point: these files go straight into a LaTeX document,
// so nothing may depend on a stylesheet that will not travel with them, and
// text must stay text rather than being flattened into paths.
//
// Set FIGURE_OUT to a directory to also write the rendered figures there and
// look at them.

import { mkdirSync, writeFileSync } from 'node:fs'
import { createRef } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { demoDataset } from '../src/analysis/demo'
import { compareNgrams, strips, timeProfile } from '../src/analysis/ngram'
import { conditionValue } from '../src/analysis/pipeline'
import { HLAC } from '../src/study/instruments'
import { LikertDiverging, type LikertResponse } from '../src/charts/LikertDiverging'
import { PairedEstimation, type PairedPanel } from '../src/charts/PairedEstimation'
import { figure2Panels } from '../src/analysis/figures'
import { ProcessSignature } from '../src/charts/ProcessSignature'
import type { Condition } from '../src/lib/types'

const dataset = demoDataset()
const ORDER: [Condition, Condition] = ['git', 'sgt']
const OUT = process.env.FIGURE_OUT

function save(name: string, svg: string) {
  if (!OUT) return
  mkdirSync(OUT, { recursive: true })
  writeFileSync(`${OUT}/${name}.svg`, `<?xml version="1.0" encoding="UTF-8"?>\n${svg}`)
}

function expectPublishable(svg: string) {
  expect(svg.startsWith('<svg')).toBe(true)
  expect(svg).toContain("viewBox=")
  expect(svg).toContain("xmlns=\"http://www.w3.org/2000/svg\"")
  // A figure that leans on a class would arrive in the paper unstyled.
  expect(svg).not.toContain('class=')
  // Text has to stay text, or a reviewer cannot select it and we cannot
  // re-typeset it when the caption font changes.
  expect(svg).toContain('<text')
  expect(svg).toContain('font-family')
  // Nothing may be fetched at render time.
  expect(svg).not.toMatch(/<image|xlink:href="http/)
}

describe('Figure 1, the perception battery', () => {
  const responses: LikertResponse[] = dataset.participants.flatMap((p) =>
    p.halves.map((h) => ({ pid: p.pid, condition: h.condition, values: h.hlac })),
  )

  it('renders a publishable SVG', () => {
    const svg = renderToStaticMarkup(
      <LikertDiverging
        ref={createRef()}
        items={HLAC.items.map((i) => ({
          id: i.id,
          label: `${i.id.toUpperCase()}: ${i.shortLabel}`,
          reverse: i.reverse,
        }))}
        responses={responses}
        points={7}
        order={ORDER}
      />,
    )
    expectPublishable(svg)
    save('fig1-perception', svg)
  })

  it('accounts for every response in the stacked counts', () => {
    const svg = renderToStaticMarkup(
      <LikertDiverging
        ref={createRef()}
        items={[{ id: 'q1', label: 'Q1' }]}
        responses={[
          { pid: 'a', condition: 'git', values: { q1: 1 } },
          { pid: 'a', condition: 'sgt', values: { q1: 7 } },
          { pid: 'b', condition: 'git', values: { q1: 2 } },
          { pid: 'b', condition: 'sgt', values: { q1: 7 } },
        ]}
        points={7}
        order={ORDER}
      />,
    )
    // Two people agreed strongly under sgt, so a segment labelled 2 must exist.
    expect(svg).toMatch(/>2</)
  })

  it('draws an empty row as a dash rather than as nothing', () => {
    const svg = renderToStaticMarkup(
      <LikertDiverging
        ref={createRef()}
        items={[{ id: 'q1', label: 'Q1' }]}
        responses={[{ pid: 'a', condition: 'git', values: { q1: 4 } }]}
        points={7}
        order={ORDER}
      />,
    )
    expect(svg).toContain('–')
  })
})

describe('Figure 2, the outcomes', () => {
  // The real panel list, imported rather than restated: a copy here would go on
  // rendering a publishable figure out of its own older set of outcomes, which is
  // the one thing this file exists to rule out.
  const panels = figure2Panels(dataset)

  it('renders a publishable SVG', () => {
    const svg = renderToStaticMarkup(<PairedEstimation ref={createRef()} panels={panels} order={ORDER} />)
    expectPublishable(svg)
    save('fig2-outcomes', svg)
  })

  it('draws one line per participant, so nothing is hidden behind a mean', () => {
    const svg = renderToStaticMarkup(
      <PairedEstimation ref={createRef()} panels={[panels[0]]} order={ORDER} />,
    )
    const lines = svg.match(/<line /g)?.length ?? 0
    expect(lines).toBeGreaterThanOrEqual(12)
    const dots = svg.match(/<circle /g)?.length ?? 0
    expect(dots).toBeGreaterThanOrEqual(24)
  })

  it('prints the interval next to the estimate', () => {
    const svg = renderToStaticMarkup(
      <PairedEstimation ref={createRef()} panels={[panels[0]]} order={ORDER} />,
    )
    expect(svg).toMatch(/\[-?\d+\.\d\d, -?\d+\.\d\d\]/)
    expect(svg).toContain('dz=')
  })

  it('survives a panel where nobody has a score yet', () => {
    const empty: PairedPanel = {
      id: 'x',
      title: 'Nothing yet',
      higherIsBetter: true,
      values: dataset.participants.map((p) => ({ pid: p.pid, label: p.label, git: NaN, sgt: NaN })),
    }
    const svg = renderToStaticMarkup(<PairedEstimation ref={createRef()} panels={[empty]} order={ORDER} />)
    expect(svg.startsWith('<svg')).toBe(true)
  })
})

describe('Figure 3, the process signature', () => {
  const model = {
    profiles: { git: timeProfile(dataset, 'git'), sgt: timeProfile(dataset, 'sgt') },
    strips: { git: strips(dataset, 'git'), sgt: strips(dataset, 'sgt') },
    ngrams: compareNgrams(dataset, 2, 5),
  }

  it('renders a publishable SVG', () => {
    const svg = renderToStaticMarkup(
      <ProcessSignature
        ref={createRef()}
        profiles={model.profiles}
        strips={model.strips}
        ngrams={model.ngrams}
        order={ORDER}
      />,
    )
    expectPublishable(svg)
    save('fig3-process', svg)
  })

  it('says how many sequences it dropped rather than leaving it implicit', () => {
    const svg = renderToStaticMarkup(
      <ProcessSignature
        ref={createRef()}
        profiles={model.profiles}
        strips={model.strips}
        ngrams={model.ngrams}
        order={ORDER}
      />,
    )
    expect(svg).toMatch(/distinct sequences seen fewer than \d+ times were dropped/)
  })

  it('shows every participant in both conditions', () => {
    expect(model.strips.git).toHaveLength(12)
    expect(model.strips.sgt).toHaveLength(12)
  })

  it('renders with no data at all', () => {
    const svg = renderToStaticMarkup(
      <ProcessSignature
        ref={createRef()}
        profiles={{ git: [], sgt: [] }}
        strips={{ git: [], sgt: [] }}
        ngrams={{ n: 2, rows: [], dropped: 0, totalA: 0, totalB: 0, minTotal: 5, sequencesA: 0, sequencesB: 0 }}
        order={ORDER}
      />,
    )
    expect(svg.startsWith('<svg')).toBe(true)
  })
})
