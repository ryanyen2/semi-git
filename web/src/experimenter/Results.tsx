import { useMemo, useRef, useState } from 'react'
import { orderBy } from 'firebase/firestore'
import { fetchParticipantBundle, isPilot, useLiveCollection, useLiveDoc } from '../lib/db'
import type { Condition, GroundTruth, Participant } from '../lib/types'
import { buildDataset, conditionValue, halfOf, keysFrom, type Dataset } from '../analysis/pipeline'
import { compareNgrams, conditionTotals, strips, timeProfile } from '../analysis/ngram'
import { demoDataset } from '../analysis/demo'
import { figure2Panels } from '../analysis/figures'
import { HLAC } from '../study/instruments'
import { LikertDiverging, type LikertResponse } from '../charts/LikertDiverging'
import { PairedEstimation } from '../charts/PairedEstimation'
import { ProcessSignature } from '../charts/ProcessSignature'
import { downloadCsv, downloadPng, downloadSvg } from '../lib/svgExport'
import { pairedEstimate } from '../lib/stats'
import { Callout, Empty, Spinner, fmtAgo } from '../ui/bits'

const ORDER: [Condition, Condition] = ['git', 'sgt']

export function Results() {
  const { data: participants } = useLiveCollection<Participant & { id: string }>(
    ['participants'],
    orderBy('ordinal'),
  )
  // Request 1's questions are closed, so the right answer is a lookup rather
  // than a judgement -- but the key lives in Firestore and not in the bundle,
  // so it has to be fetched here before anything can be scored against it.
  const { data: truth } = useLiveDoc<GroundTruth>(['study', 'groundTruth'])
  const [dataset, setDataset] = useState<Dataset | null>(null)
  const [demo, setDemo] = useState(false)
  const [loading, setLoading] = useState(false)
  const [ngramN, setNgramN] = useState(2)
  const [minTotal, setMinTotal] = useState(5)
  // Off by default, and deliberately not remembered between visits. Including
  // rehearsals is a legitimate thing to want -- it is the only way to exercise
  // this pipeline before real data exists -- but it must be a thing you switch
  // on for one look, not a setting that quietly persists into the figure you
  // paste into the paper.
  const [includePilots, setIncludePilots] = useState(false)

  const pilotCount = (participants ?? []).filter(isPilot).length
  const active = demo ? demoDataset() : dataset

  async function recompute() {
    setLoading(true)
    setDemo(false)
    try {
      const list = (participants ?? []).filter((p) => includePilots || !isPilot(p))
      const bundles = await Promise.all(
        list.map(async (p) => {
          const b = await fetchParticipantBundle(p.code)
          return {
            participant: b.participant ?? p,
            responses: b.responses,
            requests: b.requests,
            events: b.events,
            scoring: b.scoring as Array<Record<string, unknown> & { id: string }>,
          }
        }),
      )
      setDataset(buildDataset(bundles.filter((b) => b.participant), keysFrom(truth)))
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="stack loose">
      <div className="row" style={{ justifyContent: 'space-between' }}>
        <div>
          <h1 style={{ marginBottom: '0.15rem' }}>Results</h1>
          <p className="muted small" style={{ margin: 0 }}>
            {active
              ? `${active.participants.length} participants · built ${fmtAgo(active.builtAt)}${
                  active.unassignedEvents ? ` · ${active.unassignedEvents} events outside any request` : ''
                }${!demo && includePilots ? ' · includes pilots' : ''}`
              : 'Nothing computed yet'}
          </p>
        </div>
        <div className="row tight">
          <button className="btn primary" onClick={() => void recompute()} disabled={loading}>
            {loading ? 'Reading' : dataset ? 'Recompute' : 'Compute from data'}
          </button>
          <button className={`btn${demo ? ' primary' : ''}`} onClick={() => setDemo((d) => !d)}>
            {demo ? 'Showing example data' : 'Show example data'}
          </button>
        </div>
      </div>

      {pilotCount > 0 && (
        <label className="check" style={{ border: 0, background: 'none', padding: 0 }}>
          <input
            type="checkbox"
            checked={includePilots}
            onChange={(e) => {
              setIncludePilots(e.target.checked)
              setDataset(null) // never leave a stale dataset labelled with the new switch
            }}
          />
          <span className="small">
            Include the {pilotCount} pilot record{pilotCount === 1 ? '' : 's'} — for testing this
            page only. Recompute after changing this.
          </span>
        </label>
      )}

      {includePilots && (
        <Callout kind="warn" title="Not the study dataset">
          Pilot records are in this computation. Everything below — every figure, every export — is
          a rehearsal of the analysis, not a result. Switch this off and recompute before anything
          here goes near the paper.
        </Callout>
      )}

      {!demo && !truth && (
        <Callout kind="warn" title="No answer key loaded">
          Request 1 is scored from its three closed questions against{' '}
          <code>docs/study/answer-key.json</code>. Without it those questions stay unscored — which
          looks like an empty panel, not like an error. Load it under <strong>Setup</strong> and
          recompute.
        </Callout>
      )}

      {demo && (
        <Callout kind="warn" title="These are made-up numbers">
          A synthetic cohort of twelve, so the figures and the exports can be checked before the
          first session. Nothing here came from a person and none of it is stored.
        </Callout>
      )}

      {loading && <Spinner label="Reading every participant's raw events" />}

      {!active && !loading && (
        <div className="card">
          <Empty>
            <p>Press compute to build the analysis from the raw events.</p>
            <p className="small">
              Nothing is precomputed on purpose. The raw stream is the record, and every number here
              is a pure function of it, so changing how a measure is defined is a code change and a
              recompute rather than a lost measurement.
            </p>
          </Empty>
        </div>
      )}

      {active && (
        <>
          <Headline dataset={active} />
          <Figure1 dataset={active} />
          <Figure2 dataset={active} />
          <Figure3 dataset={active} ngramN={ngramN} setNgramN={setNgramN} minTotal={minTotal} setMinTotal={setMinTotal} />
          <Exports dataset={active} />
        </>
      )}
    </div>
  )
}

function Headline({ dataset }: { dataset: Dataset }) {
  const rows = useMemo(() => {
    const measure = (
      label: string,
      pick: (p: Dataset['participants'][number], c: Condition) => number,
      unit = '',
      betterHigher = true,
    ) => {
      const pairs = dataset.participants.map((p) => ({
        id: p.pid,
        a: pick(p, 'git'),
        b: pick(p, 'sgt'),
      }))
      const est = pairedEstimate(pairs)
      const meanOf = (c: Condition) => {
        const xs = dataset.participants.map((p) => pick(p, c)).filter(Number.isFinite)
        return xs.length ? xs.reduce((x, y) => x + y, 0) / xs.length : NaN
      }
      return { label, unit, betterHigher, git: meanOf('git'), sgt: meanOf('sgt'), est }
    }

    return [
      measure('Task score, r2 and r3', (p, c) =>
        conditionValue(p, c, (m) => m.score, 'sum', ['r2', 'r3']),
      ),
      measure(
        'Collateral damage',
        (p, c) => conditionValue(p, c, (m) => m.collateralDamage, 'sum'),
        'tests',
        false,
      ),
      measure('Request 1, of 3', (p, c) =>
        conditionValue(p, c, (m) => m.choiceScore, 'sum', ['r1']),
      ),
      // Signed, not absolute: the interesting failure is being sure of a wrong
      // answer, and an absolute error would score that the same as hedging a
      // right one. Positive is overconfident, so lower is better here -- but a
      // large negative number is not a success either, and the sign has to be
      // read rather than taken from the badge.
      measure(
        'Overconfidence on request 1',
        (p, c) => conditionValue(p, c, (m) => m.calibration, 'mean', ['r1']),
        '',
        false,
      ),
      measure('NASA-TLX', (p, c) => halfOf(p, c)?.tlx ?? NaN, '', false),
      measure('UMUX-Lite', (p, c) => halfOf(p, c)?.umux ?? NaN),
      measure('Verification ratio', (p, c) =>
        conditionValue(p, c, (m) => m.verificationRatio, 'mean'),
      ),
      measure('Prompt specificity, 0-3', (p, c) =>
        conditionValue(p, c, (m) => m.meanSpecificity, 'mean'),
      ),
      measure('Wrong turns', (p, c) => conditionValue(p, c, (m) => m.wrongTurns, 'sum'), '', false),
    ]
  }, [dataset])

  const totals = {
    git: conditionTotals(dataset, 'git'),
    sgt: conditionTotals(dataset, 'sgt'),
  }

  return (
    <div className="card flush">
      <div className="card-head">
        <h2 style={{ margin: 0, fontSize: '1rem' }}>Every measure, paired</h2>
        <div className="spacer" />
        <span className="tiny muted">
          differences are sgt − git, 95% studentized bootstrap, 10,000 resamples over participants
        </span>
      </div>
      <div className="scroll-x">
        <table className="table">
          <thead>
            <tr>
              <th>Measure</th>
              <th>Git</th>
              <th>sgt</th>
              <th>Difference</th>
              <th>95% CI</th>
              <th>dz</th>
              <th>n</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => {
              const helps = r.betterHigher ? r.est.estimate > 0 : r.est.estimate < 0
              const crossesZero = r.est.lo <= 0 && r.est.hi >= 0
              return (
                <tr key={r.label}>
                  <td>{r.label}</td>
                  <td className="tabular">{fmt(r.git)}</td>
                  <td className="tabular">{fmt(r.sgt)}</td>
                  <td className="tabular">
                    <span className={`badge ${crossesZero ? 'outline' : helps ? 'good' : 'bad'}`}>
                      {r.est.estimate > 0 ? '+' : ''}
                      {fmt(r.est.estimate)}
                    </span>
                  </td>
                  <td className="tabular small muted">
                    [{fmt(r.est.lo)}, {fmt(r.est.hi)}]
                    {r.est.fellBackToPercentile && <span className="tiny"> pct</span>}
                  </td>
                  <td className="tabular">{fmt(r.est.dz)}</td>
                  <td className="tabular">{r.est.n}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
      <div className="card-body small muted">
        Process totals — git: {totals.git.prompts} prompts over {totals.git.requests} requests,{' '}
        {totals.git.hitCap} hit the cap. sgt: {totals.sgt.prompts} prompts over{' '}
        {totals.sgt.requests} requests, {totals.sgt.hitCap} hit the cap.
      </div>
    </div>
  )
}

function fmt(v: number): string {
  if (!Number.isFinite(v)) return '—'
  return Math.abs(v) >= 100 ? v.toFixed(0) : v.toFixed(2)
}

function FigureFrame({
  id,
  title,
  caption,
  children,
  svgRef,
}: {
  id: string
  title: string
  caption: string
  children: React.ReactNode
  svgRef: React.RefObject<SVGSVGElement | null>
}) {
  return (
    <div className="stack tight">
      <div className="row" style={{ justifyContent: 'space-between' }}>
        <h2 style={{ margin: 0 }}>{title}</h2>
        <div className="row tight">
          <button className="btn sm" onClick={() => downloadSvg(svgRef.current, `${id}.svg`)}>
            SVG
          </button>
          <button className="btn sm" onClick={() => void downloadPng(svgRef.current, `${id}.png`)}>
            PNG
          </button>
        </div>
      </div>
      <div className="chart-wrap">{children}</div>
      <p className="small muted">{caption}</p>
    </div>
  )
}

function Figure1({ dataset }: { dataset: Dataset }) {
  const ref = useRef<SVGSVGElement>(null)
  const responses: LikertResponse[] = useMemo(
    () =>
      dataset.participants.flatMap((p) =>
        p.halves
          .filter((h) => Object.keys(h.hlac).length > 0)
          .map((h) => ({ pid: p.pid, condition: h.condition, values: h.hlac })),
      ),
    [dataset],
  )

  if (responses.length === 0)
    return <Callout kind="soft">No perception responses yet, so Figure 1 has nothing to draw.</Callout>

  return (
    <FigureFrame
      id="fig1-perception"
      title="Figure 1 · What the two setups felt like"
      svgRef={ref}
      caption="Perceived experience on fourteen 7-point items, one panel per condition, counts inside the segments. Likert-type items grouped into ad-hoc blocks, not a validated scale: read them item by item. Reverse-coded items (marked ↺) are recoded so agreement always means better. Dots are paired mean differences, sgt minus git; bars are 95% bootstrap intervals over participants."
    >
      <LikertDiverging
        ref={ref}
        // Section headings are layout, not questions: they carry no answer and
        // would render as empty rows. Manipulation checks are excluded for a
        // different reason -- they are not what this block measures, and the
        // realism check is a five-point item that would be drawn on this
        // figure's seven-point axis, landing its neutral answer a bucket left
        // of the midpoint and reading as systematically negative.
        items={HLAC.items.filter((i) => i.type === 'likert' && !i.check).map((i) => ({
          id: i.id,
          label: `${i.id.toUpperCase()}: ${i.shortLabel}`,
          reverse: i.reverse,
        }))}
        responses={responses}
        points={7}
        order={ORDER}
      />
    </FigureFrame>
  )
}

function Figure2({ dataset }: { dataset: Dataset }) {
  const ref = useRef<SVGSVGElement>(null)
  const panels = useMemo(() => figure2Panels(dataset), [dataset])

  return (
    <FigureFrame
      id="fig2-outcomes"
      title="Figure 2 · What people managed to do"
      svgRef={ref}
      caption="Every participant appears twice, joined by a line. Thick bars are condition means. Below each panel, the paired mean difference with its bootstrap distribution and 95% studentized interval on its own axis, anchored at zero. The two prediction panels average the pair of trials, whose answer sets were built to differ in size so that ticking more boxes cannot raise both. Collateral damage is plotted with the axis inverted, so up is better in every panel."
    >
      <PairedEstimation ref={ref} panels={panels} order={ORDER} />
    </FigureFrame>
  )
}

function Figure3({
  dataset,
  ngramN,
  setNgramN,
  minTotal,
  setMinTotal,
}: {
  dataset: Dataset
  ngramN: number
  setNgramN: (n: number) => void
  minTotal: number
  setMinTotal: (n: number) => void
}) {
  const ref = useRef<SVGSVGElement>(null)
  const model = useMemo(
    () => ({
      profiles: { git: timeProfile(dataset, 'git'), sgt: timeProfile(dataset, 'sgt') },
      strips: { git: strips(dataset, 'git'), sgt: strips(dataset, 'sgt') },
      ngrams: compareNgrams(dataset, ngramN, minTotal),
    }),
    [dataset, ngramN, minTotal],
  )

  return (
    <div className="stack tight">
      <div className="row" style={{ justifyContent: 'space-between' }}>
        <h2 style={{ margin: 0 }}>Figure 3 · How the work was done</h2>
        <div className="row tight">
          <div className="btn-group">
            {[2, 3].map((n) => (
              <button key={n} className={`btn sm${ngramN === n ? ' on' : ''}`} onClick={() => setNgramN(n)}>
                {n}-grams
              </button>
            ))}
          </div>
          <label className="small muted row tight">
            min count
            <input
              type="number"
              min={1}
              max={50}
              value={minTotal}
              onChange={(e) => setMinTotal(Math.max(1, Number(e.target.value)))}
              style={{ width: '4.5rem' }}
            />
          </label>
          <button className="btn sm" onClick={() => downloadSvg(ref.current, 'fig3-process.svg')}>
            SVG
          </button>
          <button className="btn sm" onClick={() => void downloadPng(ref.current, 'fig3-process.png')}>
            PNG
          </button>
        </div>
      </div>
      <div className="chart-wrap">
        <ProcessSignature
          ref={ref}
          profiles={model.profiles}
          strips={model.strips}
          ngrams={model.ngrams}
          order={ORDER}
        />
      </div>
      <p className="small muted">
        (a) Share of action categories across normalized request time, pooled per condition, with one
        strip per participant beneath. (b) Action {ngramN === 2 ? 'bigrams' : `${ngramN}-grams`} that
        most distinguish the conditions, by weighted log-odds with an informative Dirichlet prior
        (Monroe et al. 2008); positive leans towards sgt. Sequences seen fewer than {minTotal} times
        across the corpus are excluded.
      </p>
    </div>
  )
}

function Exports({ dataset }: { dataset: Dataset }) {
  return (
    <div className="card">
      <h2>Export</h2>
      <p className="small muted">
        One row per participant per condition for the mixed models, one row per request for anything
        finer, and the coded action stream for the qualitative pass.
      </p>
      <div className="row tight">
        <button
          className="btn"
          onClick={() =>
            downloadCsv(
              dataset.participants.flatMap((p) =>
                (['git', 'sgt'] as Condition[]).map((c) => {
                  const h = halfOf(p, c)
                  return {
                    participant: p.label,
                    group: p.group,
                    condition: c,
                    order: p.firstCondition === c ? 'first' : 'second',
                    project: h?.project ?? '',
                    gitExpertise: p.gitExpertise ?? '',
                    score: conditionValue(p, c, (m) => m.score, 'sum', ['r2', 'r3']),
                    r1Correct: conditionValue(p, c, (m) => m.choiceScore, 'sum', ['r1']),
                    r1Calibration: conditionValue(p, c, (m) => m.calibration, 'mean', ['r1']),
                    collateralDamage: conditionValue(p, c, (m) => m.collateralDamage, 'sum'),
                    activeMs: conditionValue(p, c, (m) => m.activeMs, 'sum'),
                    prompts: conditionValue(p, c, (m) => m.prompts, 'sum'),
                    specificity: conditionValue(p, c, (m) => m.meanSpecificity, 'mean'),
                    verificationRatio: conditionValue(p, c, (m) => m.verificationRatio, 'mean'),
                    wrongTurns: conditionValue(p, c, (m) => m.wrongTurns, 'sum'),
                    tlx: h?.tlx ?? '',
                    umux: h?.umux ?? '',
                    ...Object.fromEntries(
                      Object.entries(h?.hlac ?? {}).map(([k, v]) => [`hlac_${k}`, v]),
                    ),
                    // The two manipulation checks. Prefixed apart from `hlac_`
                    // because they are not that block's construct, and exported
                    // rather than only plotted: whether the requests read as
                    // realistic and whether the cap bound the same way in both
                    // arms are answers to questions a reader will ask, not
                    // figures.
                    ...Object.fromEntries(
                      Object.entries(h?.checks ?? {}).map(([k, v]) => [`check_${k}`, v]),
                    ),
                  }
                }),
              ),
              'study-by-condition.csv',
            )
          }
        >
          Per participant × condition
        </button>
        <button
          className="btn"
          onClick={() =>
            downloadCsv(
              dataset.participants.flatMap((p) =>
                p.requests.map((m) => ({
                  participant: p.label,
                  group: p.group,
                  half: m.half,
                  condition: m.condition,
                  project: m.project,
                  request: m.requestId,
                  activeMs: m.activeMs,
                  hitCap: m.hitCap,
                  selfReport: m.selfReport ?? '',
                  confidence: m.confidence ?? '',
                  score: m.score ?? '',
                  outOf: m.outOf ?? '',
                  choiceScore: m.choiceScore ?? '',
                  choiceOutOf: m.choiceOutOf ?? '',
                  calibration: m.calibration ?? '',
                  collateralDamage: m.collateralDamage ?? '',
                  prompts: m.prompts,
                  meanPromptChars: Math.round(m.meanPromptChars),
                  meanSpecificity: m.meanSpecificity ?? '',
                  verificationRatio: m.verificationRatio ?? '',
                  timeToFirstHistoryOpMs: m.timeToFirstHistoryOpMs ?? '',
                  wrongTurns: m.wrongTurns,
                  ...m.counts,
                })),
              ),
              'study-by-request.csv',
            )
          }
        >
          Per request
        </button>
        <button
          className="btn"
          onClick={() =>
            downloadCsv(
              dataset.participants.flatMap((p) =>
                p.events.map((e) => ({
                  participant: p.label,
                  ts: new Date(e.ts).toISOString(),
                  request: e.requestId ?? '',
                  tRel: e.tRel.toFixed(4),
                  category: e.category,
                  kind: e.kind,
                  name: e.name ?? '',
                  inferred: e.inferred ? 1 : 0,
                  text: (e.text ?? '').slice(0, 400),
                })),
              ),
              'study-actions.csv',
            )
          }
        >
          Coded action stream
        </button>
      </div>
    </div>
  )
}
