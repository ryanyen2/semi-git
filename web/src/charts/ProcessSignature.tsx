import { forwardRef, useMemo } from 'react'
import { scaleLinear } from 'd3'
import type { Condition } from '../lib/types'
import { CATEGORY_LABEL, CATEGORY_ORDER, type Category } from '../study/taxonomy'
import type { NgramComparison, Strip, TimeBin } from '../analysis/ngram'
import { prettyNgram } from '../analysis/ngram'
import {
  CATEGORY_COLOR,
  CONDITION_COLOR,
  CONDITION_LABEL,
  FONT,
  INK,
  MUTED,
  RULE,
  TYPE,
} from './theme'

interface Props {
  profiles: Record<Condition, TimeBin[]>
  strips: Record<Condition, Strip[]>
  ngrams: NgramComparison
  topN?: number
  width?: number
  order?: [Condition, Condition]
}

/**
 * Figure 3. How the work was done.
 *
 * (a) Where the time went: a stacked share of action categories across
 * normalized request time, with one strip per participant underneath so the
 * distribution behind the aggregate stays visible.
 *
 * (b) What the sequences looked like: the action bigrams that most distinguish
 * the two conditions, by weighted log-odds with an informative Dirichlet prior.
 *
 * This is the figure that answers the question which sinks tool papers, which
 * is whether the difference is just that one group leaned on the assistant more.
 */
export const ProcessSignature = forwardRef<SVGSVGElement, Props>(function ProcessSignature(
  { profiles, strips, ngrams, topN = 10, width = 980, order = ['git', 'sgt'] },
  ref,
) {
  const m = { top: 30, left: 54, right: 18, gap: 26 }
  const colW = (width - m.left - m.right - m.gap) / 2
  const areaH = 120
  const stripH = 11
  const stripGap = 2.5
  const maxStrips = Math.max(strips[order[0]].length, strips[order[1]].length)
  const stripsH = maxStrips * (stripH + stripGap) + 18

  const rows = useMemo(
    () => ngrams.rows.slice(0, topN).sort((a, b) => a.z - b.z),
    [ngrams.rows, topN],
  )
  const ngramRowH = 19
  const legendTop = m.top + areaH + stripsH + 24
  const legendRows = Math.ceil(CATEGORY_ORDER.length / 5)
  // Clear the legend before panel (b) starts. Computed rather than nudged by
  // hand, so adding a tenth action category cannot silently push the legend
  // through the heading underneath it.
  const ngramTop = legendTop + legendRows * 14 + 46
  const ngramLabelW = 250
  const ngramPlotW = width - m.left - m.right - ngramLabelW - 90
  const height = ngramTop + rows.length * ngramRowH + 62

  const x = scaleLinear().domain([0, 1]).range([0, colW])
  const y = scaleLinear().domain([0, 1]).range([m.top + areaH, m.top])

  const zMax = Math.max(1, ...rows.map((r) => Math.abs(r.z)))
  const zx = scaleLinear()
    .domain([-zMax * 1.1, zMax * 1.1])
    .range([0, ngramPlotW])

  return (
    <svg
      ref={ref}
      xmlns="http://www.w3.org/2000/svg"
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      fontFamily={FONT}
      style={{ background: '#fff' }}
    >
      <text x={8} y={14} fontSize={TYPE.title} fontWeight={600} fill={INK}>
        (a) Where the time went
      </text>
      <text x={200} y={14} fontSize={TYPE.caption} fill={MUTED}>
        share of actions across normalized request time; one strip per participant
      </text>

      {order.map((cond, ci) => {
        const ox = m.left + ci * (colW + m.gap)
        const bins = profiles[cond] ?? []
        return (
          <g key={cond} transform={`translate(${ox},0)`}>
            <text
              x={colW / 2}
              y={m.top - 8}
              textAnchor="middle"
              fontSize={TYPE.label}
              fill={CONDITION_COLOR[cond]}
              fontWeight={600}
            >
              {CONDITION_LABEL[cond]}
            </text>

            {/* Stacked share */}
            <StackedArea bins={bins} x={x} y={y} />
            <line x1={0} x2={colW} y1={y(0)} y2={y(0)} stroke={RULE} />
            {[0, 0.5, 1].map((t) => (
              <g key={t}>
                <line x1={x(t)} x2={x(t)} y1={y(0)} y2={y(0) + 4} stroke={RULE} />
                <text x={x(t)} y={y(0) + 14} textAnchor="middle" fontSize={TYPE.tick} fill={MUTED}>
                  {t === 0 ? 'start' : t === 1 ? 'end' : 'half'}
                </text>
              </g>
            ))}
            {ci === 0 &&
              [0, 0.5, 1].map((t) => (
                <text
                  key={t}
                  x={-8}
                  y={y(t) + 3}
                  textAnchor="end"
                  fontSize={TYPE.tick}
                  fill={MUTED}
                >
                  {Math.round(t * 100)}%
                </text>
              ))}

            {/* Individual strips */}
            <g transform={`translate(0,${m.top + areaH + 26})`}>
              {(strips[cond] ?? []).map((s, si) => (
                <g key={s.pid} transform={`translate(0,${si * (stripH + stripGap)})`}>
                  {/* Labelled once, on the left column. The same twelve people
                      appear in both, and repeating the labels only crowds the
                      gap between the panels. */}
                  {ci === 0 && (
                    <text x={-8} y={stripH - 2} textAnchor="end" fontSize={7.5} fill={MUTED}>
                      {s.label}
                    </text>
                  )}
                  {s.segments.map((seg, i) => (
                    <rect
                      key={i}
                      x={x(seg.t0)}
                      y={0}
                      width={Math.max(x(seg.t1) - x(seg.t0), 0.6)}
                      height={stripH}
                      fill={CATEGORY_COLOR[seg.category]}
                    />
                  ))}
                  {s.segments.length === 0 && (
                    <rect x={0} y={0} width={colW} height={stripH} fill="#f0f0f0" />
                  )}
                </g>
              ))}
            </g>
          </g>
        )
      })}

      {/* Legend */}
      <g transform={`translate(${m.left},${legendTop})`}>
        {CATEGORY_ORDER.map((c, i) => {
          const perRow = 5
          const cx = (i % perRow) * ((width - m.left - m.right) / perRow)
          const cy = Math.floor(i / perRow) * 14
          return (
            <g key={c} transform={`translate(${cx},${cy})`}>
              <rect width={10} height={10} fill={CATEGORY_COLOR[c]} />
              <text x={14} y={9} fontSize={TYPE.caption} fill={INK}>
                {CATEGORY_LABEL[c]}
              </text>
            </g>
          )
        })}
      </g>

      {/* Panel b */}
      <text x={8} y={ngramTop - 24} fontSize={TYPE.title} fontWeight={600} fill={INK}>
        (b) What the sequences looked like
      </text>
      <text x={278} y={ngramTop - 24} fontSize={TYPE.caption} fill={MUTED}>
        action {ngrams.n === 2 ? 'bigrams' : `${ngrams.n}-grams`} by weighted log-odds z
      </text>

      <g transform={`translate(${m.left + ngramLabelW},${ngramTop})`}>
        <line
          x1={zx(0)}
          x2={zx(0)}
          y1={-8}
          y2={rows.length * ngramRowH}
          stroke={RULE}
          strokeDasharray="2 2"
        />
        {rows.map((r, i) => {
          const cy = i * ngramRowH + ngramRowH / 2
          const lean = r.z >= 0 ? order[1] : order[0]
          return (
            <g key={r.key}>
              <text
                x={-12}
                y={cy + 3}
                textAnchor="end"
                fontSize={TYPE.label}
                fill={INK}
              >
                {prettyNgram(r.key)}
              </text>
              <line
                x1={zx(0)}
                x2={zx(r.z)}
                y1={cy}
                y2={cy}
                stroke={CONDITION_COLOR[lean]}
                strokeWidth={1.4}
              />
              <circle cx={zx(r.z)} cy={cy} r={3.4} fill={CONDITION_COLOR[lean]} />
              <text
                x={ngramPlotW + 10}
                y={cy + 3}
                fontSize={TYPE.caption}
                fill={MUTED}
              >
                {r.countA} / {r.countB}
              </text>
            </g>
          )
        })}
        <line
          x1={0}
          x2={ngramPlotW}
          y1={rows.length * ngramRowH}
          y2={rows.length * ngramRowH}
          stroke={RULE}
        />
        {zx.ticks(5).map((t) => (
          <g key={t} transform={`translate(${zx(t)},${rows.length * ngramRowH})`}>
            <line y1={0} y2={4} stroke={RULE} />
            <text y={14} textAnchor="middle" fontSize={TYPE.tick} fill={MUTED}>
              {t}
            </text>
          </g>
        ))}
        <text
          x={zx(0) - 12}
          y={rows.length * ngramRowH + 28}
          textAnchor="end"
          fontSize={TYPE.caption}
          fill={CONDITION_COLOR[order[0]]}
        >
          ← more like {CONDITION_LABEL[order[0]]}
        </text>
        <text
          x={zx(0) + 12}
          y={rows.length * ngramRowH + 28}
          fontSize={TYPE.caption}
          fill={CONDITION_COLOR[order[1]]}
        >
          more like {CONDITION_LABEL[order[1]]} →
        </text>
        <text x={ngramPlotW + 10} y={-8} fontSize={TYPE.caption} fill={MUTED}>
          counts
        </text>
      </g>

      <text x={8} y={height - 10} fontSize={TYPE.caption} fill={MUTED}>
        {ngrams.totalA.toLocaleString()} / {ngrams.totalB.toLocaleString()} sequences;{' '}
        {ngrams.dropped} distinct sequences seen fewer than {ngrams.minTotal} times were dropped
        before scoring.
      </text>
    </svg>
  )
})

function StackedArea({
  bins,
  x,
  y,
}: {
  bins: TimeBin[]
  x: (v: number) => number
  y: (v: number) => number
}) {
  if (bins.length === 0) return null
  const cum = new Array(bins.length).fill(0)
  const paths: Array<{ c: Category; d: string }> = []

  for (const cat of CATEGORY_ORDER) {
    const top = bins.map((b, i) => cum[i] + (b.shares[cat] ?? 0))
    const forward = bins.map((b, i) => `${x(b.t)},${y(top[i])}`)
    const back = bins.map((b, i) => `${x(b.t)},${y(cum[i])}`).reverse()
    paths.push({ c: cat, d: `M${forward.join('L')}L${back.join('L')}Z` })
    for (let i = 0; i < cum.length; i++) cum[i] = top[i]
  }

  return (
    <g>
      {paths.map((p) => (
        <path key={p.c} d={p.d} fill={CATEGORY_COLOR[p.c]} />
      ))}
    </g>
  )
}
