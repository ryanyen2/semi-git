import { forwardRef, useMemo } from 'react'
import { scaleLinear } from 'd3-scale'
import { pairedEstimate, type Pair } from '../lib/stats'
import type { Condition } from '../lib/types'
import { CONDITION_LABEL, FONT, INK, MUTED, RULE, SOFT, TYPE, likertColors, onColor } from './theme'

export interface LikertItemSpec {
  id: string
  label: string
  reverse?: boolean
}

export interface LikertResponse {
  pid: string
  condition: Condition
  values: Record<string, number>
}

interface Props {
  items: LikertItemSpec[]
  responses: LikertResponse[]
  points?: number
  width?: number
  /** Left panel condition, then right panel condition. */
  order?: [Condition, Condition]
  showDifference?: boolean
}

/**
 * Figure 1. Perception battery as diverging stacked bars, one panel per
 * condition, with paired mean differences and 95% studentized-bootstrap
 * intervals on the right.
 *
 * Reverse-coded items are recoded before plotting so that agreement always
 * means "better" and the whole figure reads in one direction. The recoded items
 * are marked, because a silently flipped item is indistinguishable from a
 * mislabelled one.
 */
export const LikertDiverging = forwardRef<SVGSVGElement, Props>(function LikertDiverging(
  { items, responses, points = 7, width = 980, order = ['git', 'sgt'], showDifference = true },
  ref,
) {
  const colors = useMemo(() => likertColors(points), [points])

  const model = useMemo(() => {
    const recode = (v: number, reverse?: boolean) => (reverse ? points + 1 - v : v)

    const rows = items.map((item) => {
      const counts: Record<Condition, number[]> = {
        git: new Array(points).fill(0),
        sgt: new Array(points).fill(0),
      }
      const pairsByPid = new Map<string, Partial<Record<Condition, number>>>()

      for (const r of responses) {
        const raw = r.values[item.id]
        if (typeof raw !== 'number') continue
        const v = recode(raw, item.reverse)
        if (v < 1 || v > points) continue
        counts[r.condition][v - 1]++
        const slot = pairsByPid.get(r.pid) ?? {}
        slot[r.condition] = v
        pairsByPid.set(r.pid, slot)
      }

      const pairs: Pair[] = [...pairsByPid.entries()]
        .filter(([, v]) => v[order[0]] != null && v[order[1]] != null)
        .map(([pid, v]) => ({ id: pid, a: v[order[0]]!, b: v[order[1]]! }))

      return { item, counts, estimate: pairedEstimate(pairs) }
    })

    // Zero line sits where nothing clips: the widest left extent across every
    // row and both panels.
    const mid = (points - 1) / 2
    let maxLeft = 0
    let maxRight = 0
    for (const row of rows) {
      for (const c of order) {
        const arr = row.counts[c]
        let left = 0
        let right = 0
        arr.forEach((n, i) => {
          if (i < mid) left += n
          else if (i > mid) right += n
          else {
            left += n / 2
            right += n / 2
          }
        })
        maxLeft = Math.max(maxLeft, left)
        maxRight = Math.max(maxRight, right)
      }
    }
    return { rows, maxLeft: maxLeft || 1, maxRight: maxRight || 1 }
  }, [items, responses, points, order])

  const labelW = 208
  const gap = 16
  const diffW = showDifference ? 168 : 0
  const panelW = (width - labelW - diffW - gap * (showDifference ? 3 : 2)) / 2
  const rowH = 26
  const headerH = 22
  const legendH = 46
  const height = headerH + model.rows.length * rowH + legendH + 26

  const x = scaleLinear()
    .domain([-model.maxLeft, model.maxRight])
    .range([0, panelW])

  const allEst = model.rows.map((r) => r.estimate)
  const diffDomain: [number, number] = [
    Math.min(0, ...allEst.map((e) => (Number.isFinite(e.lo) ? e.lo : 0))),
    Math.max(0, ...allEst.map((e) => (Number.isFinite(e.hi) ? e.hi : 0))),
  ]
  const pad = Math.max(0.25, (diffDomain[1] - diffDomain[0]) * 0.08)
  const dx = scaleLinear()
    .domain([diffDomain[0] - pad, diffDomain[1] + pad])
    .range([0, diffW - 24])

  const panelX = (i: number) => labelW + gap + i * (panelW + gap)
  const diffX = labelW + gap + 2 * (panelW + gap) + 4

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
      {/* Panel headings */}
      {order.map((c, i) => (
        <text
          key={c}
          x={panelX(i) + panelW / 2}
          y={13}
          textAnchor="middle"
          fontSize={TYPE.title}
          fontStyle="italic"
          fill={INK}
        >
          {CONDITION_LABEL[c]}
        </text>
      ))}
      {showDifference && (
        <text x={diffX + (diffW - 24) / 2} y={13} textAnchor="middle" fontSize={TYPE.title} fill={INK}>
          Mean difference &amp; 95% CI
        </text>
      )}

      {model.rows.map((row, ri) => {
        const y = headerH + ri * rowH
        const cy = y + rowH / 2
        return (
          <g key={row.item.id}>
            {ri % 2 === 1 && (
              <rect x={0} y={y} width={width} height={rowH} fill={SOFT} />
            )}
            <text x={labelW - 8} y={cy + 3.5} textAnchor="end" fontSize={TYPE.label} fill={INK}>
              {row.item.label}
              {row.item.reverse ? ' ↺' : ''}
            </text>

            {order.map((cond, pi) => (
              <g key={cond} transform={`translate(${panelX(pi)},0)`}>
                <StackedRow
                  counts={row.counts[cond]}
                  points={points}
                  colors={colors}
                  x={x}
                  y={y + 4}
                  h={rowH - 8}
                />
              </g>
            ))}

            {showDifference && (
              <g transform={`translate(${diffX},0)`}>
                {Number.isFinite(row.estimate.estimate) && (
                  <>
                    <line
                      x1={dx(row.estimate.lo)}
                      x2={dx(row.estimate.hi)}
                      y1={cy}
                      y2={cy}
                      stroke={INK}
                      strokeWidth={1.2}
                    />
                    <line
                      x1={dx(row.estimate.lo)}
                      x2={dx(row.estimate.lo)}
                      y1={cy - 3.5}
                      y2={cy + 3.5}
                      stroke={INK}
                      strokeWidth={1.2}
                    />
                    <line
                      x1={dx(row.estimate.hi)}
                      x2={dx(row.estimate.hi)}
                      y1={cy - 3.5}
                      y2={cy + 3.5}
                      stroke={INK}
                      strokeWidth={1.2}
                    />
                    <circle cx={dx(row.estimate.estimate)} cy={cy} r={3.1} fill={INK} />
                  </>
                )}
              </g>
            )}
          </g>
        )
      })}

      {/* Difference axis */}
      {showDifference && (
        <g transform={`translate(${diffX},${headerH + model.rows.length * rowH})`}>
          <line x1={0} x2={diffW - 24} y1={0} y2={0} stroke={RULE} />
          {dx.ticks(4).map((t) => (
            <g key={t} transform={`translate(${dx(t)},0)`}>
              <line y1={0} y2={4} stroke={RULE} />
              <text y={14} textAnchor="middle" fontSize={TYPE.tick} fill={MUTED}>
                {t}
              </text>
            </g>
          ))}
          {diffDomain[0] < 0 && diffDomain[1] > 0 && (
            <line
              x1={dx(0)}
              x2={dx(0)}
              y1={-(model.rows.length * rowH)}
              y2={0}
              stroke={RULE}
              strokeDasharray="2 2"
            />
          )}
        </g>
      )}

      {/* Legend */}
      <g transform={`translate(${labelW + gap},${headerH + model.rows.length * rowH + 24})`}>
        <text x={0} y={10} fontSize={TYPE.label} fill={MUTED}>
          strongly disagree
        </text>
        {colors.map((c, i) => (
          <rect key={i} x={104 + i * 17} y={0} width={15} height={13} fill={c} />
        ))}
        <text x={104 + points * 17 + 6} y={10} fontSize={TYPE.label} fill={MUTED}>
          strongly agree
        </text>
      </g>
    </svg>
  )
})

function StackedRow({
  counts,
  points,
  colors,
  x,
  y,
  h,
}: {
  counts: number[]
  points: number
  colors: string[]
  x: (v: number) => number
  y: number
  h: number
}) {
  const mid = (points - 1) / 2
  let left = 0
  counts.forEach((n, i) => {
    if (i < mid) left += n
    else if (i === mid) left += n / 2
  })

  const total = counts.reduce((a, b) => a + b, 0)
  if (total === 0) {
    return (
      <text x={x(0) - 6} y={y + h / 2 + 3} textAnchor="end" fontSize={TYPE.inBar} fill={MUTED}>
        –
      </text>
    )
  }

  let cursor = -left
  const segments = counts.map((n, i) => {
    const from = cursor
    cursor += n
    return { i, n, from, to: cursor }
  })

  return (
    <g>
      {segments.map(({ i, n, from, to }) => {
        if (n === 0) return null
        const x0 = x(from)
        const w = x(to) - x0
        const fill = colors[i]
        return (
          <g key={i}>
            <rect x={x0} y={y} width={Math.max(w, 0.5)} height={h} fill={fill} />
            {w >= 11 && (
              <text
                x={x0 + w / 2}
                y={y + h / 2 + 3}
                textAnchor="middle"
                fontSize={TYPE.inBar}
                fontStyle="italic"
                fill={onColor(fill)}
              >
                {n}
              </text>
            )}
          </g>
        )
      })}
    </g>
  )
}
