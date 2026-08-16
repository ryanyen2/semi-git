import { forwardRef, useMemo } from 'react'
import { scaleLinear } from 'd3'
import { pairedBootstrapDistribution, pairedEstimate, quantile, type Pair } from '../lib/stats'
import type { Condition } from '../lib/types'
import { CONDITION_COLOR, CONDITION_LABEL, FONT, INK, MUTED, RULE, TYPE } from './theme'

export interface PairedPanel {
  id: string
  title: string
  subtitle?: string
  /** Per participant, the value under each condition. */
  values: Array<{ pid: string; label: string; git: number; sgt: number }>
  /** Draws the axis inverted so that up is always the better direction. */
  higherIsBetter: boolean
  unit?: string
  /** Fix the axis, e.g. a rubric out of 2. */
  domain?: [number, number]
}

interface Props {
  panels: PairedPanel[]
  width?: number
  panelHeight?: number
  order?: [Condition, Condition]
}

/**
 * Figure 2. Paired within-subject estimation plot, one panel per outcome.
 *
 * Every participant is drawn: two points joined by a line. Twelve slopes shown
 * individually is the honest way to plot twelve people, because a bar chart of
 * two means cannot tell you whether one person moved a lot or twelve moved a
 * little, and on this sample size that is the entire question.
 *
 * Underneath each panel is the paired mean difference with its bootstrap
 * distribution and 95% interval, on its own axis anchored at zero.
 */
export const PairedEstimation = forwardRef<SVGSVGElement, Props>(function PairedEstimation(
  { panels, width = 980, panelHeight = 330, order = ['git', 'sgt'] },
  ref,
) {
  const cols = Math.min(panels.length, 4)
  const panelW = width / cols
  const rows = Math.ceil(panels.length / cols)
  const height = rows * panelHeight + 26

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
      {panels.map((panel, i) => (
        <g
          key={panel.id}
          transform={`translate(${(i % cols) * panelW},${Math.floor(i / cols) * panelHeight})`}
        >
          <Panel panel={panel} width={panelW} height={panelHeight} order={order} />
        </g>
      ))}
      <text x={8} y={height - 10} fontSize={TYPE.caption} fill={MUTED}>
        Each line is one participant. Lower panel: paired mean difference ({CONDITION_LABEL[order[1]]}{' '}
        − {CONDITION_LABEL[order[0]]}) with its bootstrap distribution and 95% studentized interval.
      </text>
    </svg>
  )
})


/**
 * One outcome, drawn twice: the raw paired values on top, the difference below.
 *
 * Rubric scores are small integers, so twelve participants collapse onto three
 * or four y positions. Without a horizontal offset the figure shows four dots
 * and quietly contradicts its own caption, which says every participant is
 * drawn. The offset is deterministic, derived from the participant's position
 * in the cohort, so the figure is identical every time it is exported.
 */
function Panel({
  panel,
  width,
  height,
  order,
}: {
  panel: PairedPanel
  width: number
  height: number
  order: [Condition, Condition]
}) {
  const m = { top: 34, right: 16, bottom: 46, left: 46 }
  const topH = (height - m.top - m.bottom) * 0.52
  const condLabelY = m.top + topH + 15
  const diffTop = m.top + topH + 44
  const diffBottom = height - m.bottom

  const model = useMemo(() => {
    const pairs: Pair[] = panel.values
      .filter((v) => Number.isFinite(v[order[0]]) && Number.isFinite(v[order[1]]))
      .map((v) => ({ id: v.pid, a: v[order[0]], b: v[order[1]] }))
    return {
      pairs,
      est: pairedEstimate(pairs),
      dist: pairedBootstrapDistribution(pairs),
    }
  }, [panel.values, order])

  const allVals = panel.values.flatMap((v) => [v.git, v.sgt]).filter(Number.isFinite)
  const vMin = panel.domain ? panel.domain[0] : Math.min(0, ...allVals)
  const vMax = panel.domain ? panel.domain[1] : Math.max(...allVals, 1)
  const padV = (vMax - vMin) * 0.09 || 0.5

  // Inverted when lower is better, so "up" means "better" in every panel and a
  // reader does not have to check the axis direction one panel at a time.
  const y = scaleLinear()
    .domain(panel.higherIsBetter ? [vMin - padV, vMax + padV] : [vMax + padV, vMin - padV])
    .range([m.top + topH, m.top])

  const plotW = width - m.left - m.right
  const xOf = (c: Condition) => m.left + (c === order[0] ? 0.3 : 0.7) * plotW

  // Spread ties sideways. Five lanes is enough for twelve people on a
  // three-point rubric and narrow enough that the pairing still reads.
  const jitterOf = (i: number) => ((i % 5) - 2) * 4

  const meanOf = (c: Condition) => {
    const xs = panel.values.map((v) => v[c]).filter(Number.isFinite)
    return xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : NaN
  }

  const dAll = [
    ...model.dist,
    Number.isFinite(model.est.lo) ? model.est.lo : 0,
    Number.isFinite(model.est.hi) ? model.est.hi : 0,
    0,
  ]
  const dMin = Math.min(...dAll)
  const dMax = Math.max(...dAll)
  const dPad = (dMax - dMin) * 0.18 || 0.5
  const dy = scaleLinear()
    .domain(panel.higherIsBetter ? [dMin - dPad, dMax + dPad] : [dMax + dPad, dMin - dPad])
    .range([diffBottom, diffTop])

  const diffCx = m.left + plotW * 0.7
  const axisX = diffCx - 62

  // A histogram outline rather than a smoothed density, because the smoothing
  // bandwidth would be one more undocumented choice inside a figure.
  const violin = useMemo(() => {
    if (model.dist.length === 0) return ''
    const bins = 28
    const lo = quantile(model.dist, 0.001)
    const hi = quantile(model.dist, 0.999)
    const step = (hi - lo) / bins || 1
    const hist = new Array(bins).fill(0)
    for (const v of model.dist) {
      const i = Math.max(0, Math.min(bins - 1, Math.floor((v - lo) / step)))
      hist[i]++
    }
    const peak = Math.max(...hist, 1)
    const maxW = 24
    const left: string[] = []
    const right: string[] = []
    hist.forEach((n, i) => {
      const v = lo + (i + 0.5) * step
      const w = (n / peak) * maxW
      left.push(`${diffCx - w},${dy(v)}`)
      right.unshift(`${diffCx + w},${dy(v)}`)
    })
    return `M${left.join('L')}L${right.join('L')}Z`
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [model.dist, width, height, panel.higherIsBetter])

  return (
    <g>
      <text x={m.left - 40} y={14} fontSize={TYPE.title} fill={INK} fontWeight={600}>
        {panel.title}
      </text>
      {panel.subtitle && (
        <text x={m.left - 40} y={26} fontSize={TYPE.caption} fill={MUTED}>
          {panel.subtitle}
        </text>
      )}

      {/* Value axis */}
      <line x1={m.left} x2={m.left} y1={m.top} y2={m.top + topH} stroke={RULE} />
      {y.ticks(5).map((t) => (
        <g key={t} transform={`translate(${m.left},${y(t)})`}>
          <line x1={-4} x2={0} stroke={RULE} />
          <text x={-7} y={3} textAnchor="end" fontSize={TYPE.tick} fill={MUTED}>
            {t}
          </text>
        </g>
      ))}
      {panel.unit && (
        <text
          transform={`translate(11,${m.top + topH / 2}) rotate(-90)`}
          textAnchor="middle"
          fontSize={TYPE.caption}
          fill={MUTED}
        >
          {panel.unit}
        </text>
      )}

      {/* One line and two dots per participant */}
      {panel.values.map((v, i) => {
        if (!Number.isFinite(v.git) || !Number.isFinite(v.sgt)) return null
        const j = jitterOf(i)
        return (
          <line
            key={v.pid}
            x1={xOf(order[0]) + j}
            x2={xOf(order[1]) + j}
            y1={y(v[order[0]])}
            y2={y(v[order[1]])}
            stroke={INK}
            strokeOpacity={0.26}
            strokeWidth={0.9}
          />
        )
      })}
      {order.map((c) =>
        panel.values.map((v, i) =>
          Number.isFinite(v[c]) ? (
            <circle
              key={`${c}-${v.pid}`}
              cx={xOf(c) + jitterOf(i)}
              cy={y(v[c])}
              r={2.8}
              fill={CONDITION_COLOR[c]}
              fillOpacity={0.9}
            >
              <title>{`${v.label}: ${v[c]}`}</title>
            </circle>
          ) : null,
        ),
      )}

      {/* Condition means and labels */}
      {order.map((c) => {
        const mu = meanOf(c)
        return (
          <g key={`mu-${c}`}>
            {Number.isFinite(mu) && (
              <line
                x1={xOf(c) - 17}
                x2={xOf(c) + 17}
                y1={y(mu)}
                y2={y(mu)}
                stroke={CONDITION_COLOR[c]}
                strokeWidth={2.2}
              />
            )}
            <text
              x={xOf(c)}
              y={condLabelY}
              textAnchor="middle"
              fontSize={TYPE.label}
              fill={CONDITION_COLOR[c]}
              fontWeight={600}
            >
              {CONDITION_LABEL[c]}
            </text>
          </g>
        )
      })}

      {/* Difference */}
      <text x={m.left - 40} y={diffTop - 12} fontSize={TYPE.caption} fill={MUTED}>
        difference · n={model.est.n} · dz=
        {Number.isFinite(model.est.dz) ? model.est.dz.toFixed(2) : '–'}
        {model.est.fellBackToPercentile ? ' · percentile CI' : ''}
      </text>

      <line
        x1={m.left}
        x2={width - m.right}
        y1={dy(0)}
        y2={dy(0)}
        stroke={RULE}
        strokeDasharray="3 2"
      />
      <line x1={axisX} x2={axisX} y1={diffTop} y2={diffBottom} stroke={RULE} />
      {dy.ticks(4).map((t) => (
        <g key={t} transform={`translate(${axisX},${dy(t)})`}>
          <line x1={-4} x2={0} stroke={RULE} />
          <text x={-7} y={3} textAnchor="end" fontSize={TYPE.tick} fill={MUTED}>
            {t}
          </text>
        </g>
      ))}
      {violin && <path d={violin} fill={CONDITION_COLOR[order[1]]} fillOpacity={0.2} />}
      {Number.isFinite(model.est.estimate) && (
        <>
          <line
            x1={diffCx}
            x2={diffCx}
            y1={dy(model.est.lo)}
            y2={dy(model.est.hi)}
            stroke={INK}
            strokeWidth={1.5}
          />
          <circle cx={diffCx} cy={dy(model.est.estimate)} r={3.6} fill={INK} />
          {/* Centred under the axis, so a wide interval can never be clipped by
              the panel edge. */}
          <text
            x={m.left + plotW / 2}
            y={diffBottom + 16}
            textAnchor="middle"
            fontSize={TYPE.label}
            fill={INK}
          >
            {model.est.estimate > 0 ? '+' : ''}
            {model.est.estimate.toFixed(2)}
          </text>
          <text
            x={m.left + plotW / 2}
            y={diffBottom + 28}
            textAnchor="middle"
            fontSize={TYPE.caption}
            fill={MUTED}
          >
            [{model.est.lo.toFixed(2)}, {model.est.hi.toFixed(2)}]
          </text>
        </>
      )}
    </g>
  )
}
