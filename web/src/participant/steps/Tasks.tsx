import { useCallback, useEffect, useMemo, useState } from 'react'
import { useParticipant } from '../ParticipantApp'
import {
  markRequestBoundary,
  openRequest,
  patchRequest,
  requestDocId,
  useLiveCollection,
} from '../../lib/db'
import type { PauseInterval, RequestDoc, RequestId } from '../../lib/types'
import { blockFor, type Step } from '../../study/flow'
import { BLOCK_CAP_MIN, SCENARIO, taskCards } from '../../study/tasks'
import { TASK_PREAMBLE } from '../../study/content'
import { Callout, Countdown, fmtClock, useCountdown } from '../../ui/bits'
import { Markdown } from '../../ui/Markdown'

const PAUSE_REASONS: Array<[PauseInterval['reason'], string]> = [
  ['break', 'Taking a break'],
  ['facilitator', 'Talking to the facilitator'],
  ['tool-failure', 'Something is broken'],
  ['other', 'Something else'],
]

function pausedMsOf(doc: RequestDoc | undefined, now: number): number {
  if (!doc) return 0
  return (doc.pauses ?? []).reduce((n, p) => n + ((p.to ?? now) - p.from), 0)
}

export function TasksStep({ step }: { step: Step }) {
  const { pid, participant, goNext } = useParticipant()
  const half = step.half!
  const block = blockFor(participant.blocks, half)
  const project = block.project
  const cards = useMemo(() => taskCards(project), [project])
  const scenario = SCENARIO[project]

  const { data: docs } = useLiveCollection<RequestDoc & { id: string }>([
    'participants',
    pid,
    'requests',
  ])

  const byId = useMemo(() => {
    const m = new Map<string, RequestDoc>()
    for (const d of docs ?? []) m.set(d.id, d)
    return m
  }, [docs])

  const docFor = useCallback(
    (rid: RequestId) => byId.get(requestDocId(rid, half)),
    [byId, half],
  )

  const cardDone = useCallback(
    (cardId: string) => {
      const card = cards.find((c) => c.id === cardId)!
      return card.requests.every((r) => docFor(r.id)?.submittedAt != null)
    },
    [cards, docFor],
  )

  const activeIndex = cards.findIndex((c) => !cardDone(c.id))
  const allDone = activeIndex === -1

  // Block clock. Advisory: the facilitator calls the real time, and a hard stop
  // mid-sentence would cost more data than it saves.
  const [now, setNow] = useState(Date.now())
  useEffect(() => {
    const t = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(t)
  }, [])
  const firstOpen = (docs ?? []).filter((d) => d.half === half && d.openedAt).sort(
    (a, b) => (a.openedAt ?? 0) - (b.openedAt ?? 0),
  )[0]
  const blockElapsed = firstOpen?.openedAt ? now - firstOpen.openedAt : 0

  return (
    <div className="stack loose">
      <div>
        <div className="eyebrow">
          {block.label} · {scenario.app}
        </div>
        <h1>The requests</h1>
      </div>

      <div className="card soft">
        <Markdown>{TASK_PREAMBLE(scenario.app, scenario.maintainer, scenario.blurb)}</Markdown>
        <div className="row small muted" style={{ justifyContent: 'space-between' }}>
          <span>
            The project is in <code>work/</code>. Keep the session shell open.
          </span>
          <span className="tabular">
            {fmtClock(blockElapsed)} of about {BLOCK_CAP_MIN}m in this half
          </span>
        </div>
      </div>

      {cards.map((card, i) => (
        <TaskCardView
          key={card.id}
          state={cardDone(card.id) ? 'done' : i === activeIndex ? 'open' : 'locked'}
          card={card}
          pid={pid}
          half={half}
          block={block}
          docFor={docFor}
        />
      ))}

      {allDone ? (
        <Callout kind="accent" title="That is the whole set">
          Close the project and your editor before the next page, because the next questions ask
          what you remember.
        </Callout>
      ) : (
        <Callout kind="soft" title="Running out of time is a normal result">
          If the facilitator calls time, use <strong>Stop here</strong> on the open request. It
          records where you got to, which is data we want.
        </Callout>
      )}

      <div className="sticky-actions">
        <button className="btn primary lg" onClick={goNext}>
          {allDone ? 'Continue' : 'Finish this half and continue'}
        </button>
      </div>
    </div>
  )
}

function TaskCardView({
  state,
  card,
  pid,
  half,
  block,
  docFor,
}: {
  state: 'locked' | 'open' | 'done'
  card: ReturnType<typeof taskCards>[number]
  pid: string
  half: 1 | 2
  block: ReturnType<typeof blockFor>
  docFor: (r: RequestId) => RequestDoc | undefined
}) {
  const project = block.project
  const lead = card.requests[0]
  const leadDoc = docFor(lead.id)
  const capMs = (card.capMin ?? 0) * 60_000
  const [now, setNow] = useState(Date.now())
  const [pauseOpen, setPauseOpen] = useState(false)

  useEffect(() => {
    if (state !== 'open') return
    const t = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(t)
  }, [state])

  const activePause = (leadDoc?.pauses ?? []).find((p) => p.to == null) ?? null
  const pausedMs = pausedMsOf(leadDoc, now)
  const { remaining, expired } = useCountdown({
    openedAt: leadDoc?.openedAt ?? null,
    capMs,
    pausedMs,
    running: state === 'open' && !activePause,
  })

  // Opening the card starts its clock, and drops a marker into the same event
  // stream the machine writes to, so telemetry can be sliced by request without
  // the machine having to know what a request is.
  useEffect(() => {
    if (state !== 'open') return
    if (leadDoc?.openedAt) return
    void (async () => {
      for (const r of card.requests) {
        await openRequest(pid, r.id, block, r.id === lead.id ? capMs : 0)
      }
      await markRequestBoundary(pid, lead.id, block, 'open')
    })()
  }, [state, leadDoc?.openedAt, card.requests, pid, block, capMs, lead.id])

  // Record that the cap was reached, once.
  useEffect(() => {
    if (state === 'open' && expired && leadDoc && !leadDoc.hitCap) {
      void patchRequest(pid, lead.id, half, { hitCap: true })
    }
  }, [state, expired, leadDoc, pid, lead.id, half])

  async function togglePause(reason?: PauseInterval['reason']) {
    const doc = docFor(lead.id)
    if (!doc) return
    const pauses = [...(doc.pauses ?? [])]
    const open = pauses.findIndex((p) => p.to == null)
    if (open >= 0) {
      pauses[open] = { ...pauses[open], to: Date.now() }
    } else {
      pauses.push({ from: Date.now(), to: null, reason: reason ?? 'break' })
    }
    await patchRequest(pid, lead.id, half, { pauses })
    setPauseOpen(false)
  }

  async function finishCard(selfReport: RequestDoc['selfReport']) {
    const t = Date.now()
    for (const r of card.requests) {
      const doc = docFor(r.id)
      const openedAt = doc?.openedAt ?? leadDoc?.openedAt ?? t
      await patchRequest(pid, r.id, half, {
        submittedAt: t,
        elapsedMs: t - openedAt,
        activeMs: Math.max(0, t - openedAt - pausedMsOf(leadDoc, t)),
        pauses: (leadDoc?.pauses ?? []).map((p) => (p.to == null ? { ...p, to: t } : p)),
        selfReport: doc?.selfReport ?? selfReport,
      })
    }
    await markRequestBoundary(pid, lead.id, block, 'close')
  }

  if (state === 'locked') {
    return (
      <div className="card soft" style={{ opacity: 0.6 }}>
        <div className="row" style={{ justifyContent: 'space-between' }}>
          <div>
            <div className="eyebrow">{card.heading}</div>
            <div className="strong">{card.title}</div>
          </div>
          <span className="badge outline">
            {card.capMin ? `${card.capMin} min` : 'no limit'}
            {lead.optional ? ' · optional' : ''}
          </span>
        </div>
      </div>
    )
  }

  if (state === 'done') {
    return (
      <div className="card">
        <div className="row" style={{ justifyContent: 'space-between' }}>
          <div>
            <div className="eyebrow">{card.heading} · finished</div>
            <div className="strong">{card.title}</div>
          </div>
          <span className="badge good">
            {leadDoc?.selfReport === 'done'
              ? 'Marked done'
              : leadDoc?.selfReport === 'partial'
                ? 'Partly done'
                : leadDoc?.selfReport === 'blocked'
                  ? 'Blocked'
                  : 'Stopped'}
          </span>
        </div>
        {card.requests.map((r) => {
          const d = docFor(r.id)
          if (!r.wantsAnswer || !d?.answer) return null
          return (
            <div key={r.id} className="small" style={{ marginTop: '0.75rem' }}>
              <div className="muted tiny">{r.title[project]}</div>
              <div style={{ whiteSpace: 'pre-wrap' }}>{d.answer}</div>
            </div>
          )
        })}
      </div>
    )
  }

  return (
    <div className="card" style={{ borderColor: 'var(--accent-line)' }}>
      <div className="row" style={{ justifyContent: 'space-between', alignItems: 'flex-start' }}>
        <div>
          <div className="eyebrow">{card.heading}</div>
          <h2 style={{ margin: 0 }}>{card.title}</h2>
          {lead.optional && <span className="badge outline">optional</span>}
        </div>
        {capMs > 0 && <Countdown remaining={remaining} capMs={capMs} />}
      </div>

      {activePause && (
        <Callout kind="warn" title="Paused">
          The clock is stopped. Nothing is being lost.{' '}
          <button className="btn sm" onClick={() => togglePause()} style={{ marginLeft: '0.5rem' }}>
            Resume
          </button>
        </Callout>
      )}

      {expired && !activePause && (
        <Callout kind="warn" title="That is time">
          Wrap up where you are. Running out is expected on some of these and it is recorded as a
          normal outcome, not a failure.
        </Callout>
      )}

      <div style={{ marginTop: '1rem' }}>
        {card.requests.map((r, ri) => (
          <div key={r.id} style={{ marginTop: ri ? '1.5rem' : 0 }}>
            {card.requests.length > 1 && <h3>{r.title[project]}</h3>}
            <Markdown>{r.body[project]}</Markdown>
            {r.wantsAnswer && (
              <AnswerBox pid={pid} half={half} request={r.id} doc={docFor(r.id)} wantsConfidence={r.wantsConfidence} />
            )}
          </div>
        ))}
      </div>

      <hr />

      <div className="row" style={{ justifyContent: 'space-between' }}>
        <div className="row tight">
          <button className="btn primary" onClick={() => finishCard('done')}>
            Mark done
          </button>
          <button className="btn" onClick={() => finishCard('partial')}>
            Stop here
          </button>
          {lead.optional && (
            <button className="btn ghost" onClick={() => finishCard('gave-up')}>
              Skip this one
            </button>
          )}
        </div>
        <div className="row tight">
          {pauseOpen ? (
            <>
              {PAUSE_REASONS.map(([reason, label]) => (
                <button key={reason} className="btn sm" onClick={() => togglePause(reason)}>
                  {label}
                </button>
              ))}
              <button className="btn sm ghost" onClick={() => setPauseOpen(false)}>
                Cancel
              </button>
            </>
          ) : (
            !activePause && (
              <button className="btn ghost sm" onClick={() => setPauseOpen(true)}>
                Pause the clock
              </button>
            )
          )}
        </div>
      </div>
    </div>
  )
}

function AnswerBox({
  pid,
  half,
  request,
  doc,
  wantsConfidence,
}: {
  pid: string
  half: 1 | 2
  request: RequestId
  doc: RequestDoc | undefined
  wantsConfidence: boolean
}) {
  const [answer, setAnswer] = useState(doc?.answer ?? '')
  const [confidence, setConfidence] = useState<number | null>(doc?.confidence ?? null)
  const [dirty, setDirty] = useState(false)

  // Adopt the stored value only while the box is untouched, so a slow snapshot
  // cannot overwrite something the participant is in the middle of typing.
  useEffect(() => {
    if (!dirty && doc?.answer != null && doc.answer !== answer) setAnswer(doc.answer)
    if (!dirty && doc?.confidence != null && doc.confidence !== confidence) setConfidence(doc.confidence)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [doc?.answer, doc?.confidence])

  useEffect(() => {
    if (!dirty) return
    const t = window.setTimeout(() => {
      void patchRequest(pid, request, half, { answer, confidence })
    }, 600)
    return () => window.clearTimeout(t)
  }, [answer, confidence, dirty, pid, request, half])

  useEffect(() => {
    const flush = () => {
      if (dirty) void patchRequest(pid, request, half, { answer, confidence })
    }
    window.addEventListener('pagehide', flush)
    return () => window.removeEventListener('pagehide', flush)
  }, [dirty, answer, confidence, pid, request, half])

  return (
    <div className="stack tight" style={{ marginTop: '0.75rem' }}>
      <div className="field-label">Your answer</div>
      <textarea
        value={answer}
        placeholder="Two or three sentences."
        onChange={(e) => {
          setAnswer(e.target.value)
          setDirty(true)
        }}
      />
      {wantsConfidence && (
        <div style={{ marginTop: '0.5rem' }}>
          <div className="field-label">How sure are you?</div>
          <div className="row" style={{ flexWrap: 'nowrap', gap: '0.75rem' }}>
            <input
              type="range"
              min={0}
              max={100}
              step={5}
              value={confidence ?? 50}
              aria-label="Confidence"
              aria-valuetext={confidence == null ? 'not answered yet' : String(confidence)}
              style={confidence == null ? { opacity: 0.72 } : undefined}
              onChange={(e) => {
                setConfidence(Number(e.target.value))
                setDirty(true)
              }}
              // Touching the thumb where it already sits fires no change event,
              // so a participant who means exactly 50 would otherwise leave no
              // answer at all. Confidence is half of the calibration measure;
              // losing it quietly would be worse than losing it loudly.
              onPointerDown={() => {
                if (confidence == null) {
                  setConfidence(50)
                  setDirty(true)
                }
              }}
            />
            <span className="tlx-value" style={confidence == null ? { color: 'var(--faint)' } : undefined}>
              {confidence == null ? '–' : confidence}
            </span>
          </div>
          <div className="anchors">
            <span>Guessing</span>
            <span>Certain</span>
          </div>
          {confidence == null && (
            <div className="tiny faint">Not answered yet — click anywhere on the line.</div>
          )}
        </div>
      )}
    </div>
  )
}
