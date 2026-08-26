import { useCallback, useEffect, useMemo, useState } from 'react'
import { useParticipant } from '../ParticipantApp'
import {
  draftKey,
  markRequestBoundary,
  openRequest,
  patchRequest,
  readDraft,
  requestDocId,
  useFlushOnHide,
  useLiveCollection,
  writeDraft,
} from '../../lib/db'
import type { PauseInterval, Project, RequestDoc, RequestId } from '../../lib/types'
import { blockFor, type Step } from '../../study/flow'
import {
  BEHAVIOURS,
  BLOCK_CAP_MIN,
  REQUESTS,
  SCENARIO,
  type PrescribedRun as PrescribedRunSpec,
  type QuizItem,
  type RequestSpec,
} from '../../study/tasks'
import { TASK_PREAMBLE } from '../../study/content'
import { Callout, Countdown, fmtClock, useCountdown } from '../../ui/bits'
import { Markdown, MarkdownLine } from '../../ui/Markdown'

const PAUSE_REASONS: Array<[PauseInterval['reason'], string]> = [
  ['break', 'Taking a break'],
  ['facilitator', 'Talking to the facilitator'],
  ['tool-failure', 'Something is broken'],
  ['other', 'Something else'],
]

/**
 * Copying the stage text is refused unless the selection sits inside code.
 *
 * `user-select: none` already stops a mouse selection; this catches select-all
 * and the keyboard. It is not a security control and does not need to be: the
 * point is that pasting the stage text somewhere should not be the easiest
 * thing to do, not that it should be impossible.
 */
function blockProseCopy(e: React.ClipboardEvent) {
  const node = window.getSelection()?.anchorNode
  const el = node instanceof Element ? node : node?.parentElement
  if (!el?.closest('code, pre')) e.preventDefault()
}

function pausedMsOf(doc: RequestDoc | undefined, now: number): number {
  if (!doc) return 0
  return (doc.pauses ?? []).reduce((n, p) => n + ((p.to ?? now) - p.from), 0)
}

export function TasksStep({ step }: { step: Step }) {
  const { pid, participant, goNext } = useParticipant()
  const half = step.half!
  const block = blockFor(participant.blocks, half)
  const project = block.project
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

  const stageDone = useCallback(
    (rid: RequestId) => docFor(rid)?.submittedAt != null,
    [docFor],
  )

  const activeIndex = REQUESTS.findIndex((r) => !stageDone(r.id))
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
        <h1>The stages</h1>
      </div>

      <div className="card soft no-copy" onCopy={blockProseCopy}>
        <Markdown>{TASK_PREAMBLE(scenario.app, scenario.maintainer, scenario.blurb)}</Markdown>
        <div className="row small muted" style={{ justifyContent: 'space-between' }}>
          <span>
            The project is in <code>work/</code>. Keep the session shell open.
          </span>
          <span className="tabular">
            {fmtClock(blockElapsed)} of about {BLOCK_CAP_MIN}m of timed work in this half
          </span>
        </div>
      </div>

      {REQUESTS.map((spec, i) => (
        <StageCardView
          key={spec.id}
          state={stageDone(spec.id) ? 'done' : i === activeIndex ? 'open' : 'locked'}
          spec={spec}
          pid={pid}
          half={half}
          block={block}
          doc={docFor(spec.id)}
        />
      ))}

      {allDone ? (
        <Callout kind="accent" title="That is the whole set">
          Close the project and your editor before the next page.
        </Callout>
      ) : (
        <Callout kind="soft" title="Running out of time is a normal result">
          If the facilitator calls time, use <strong>Stop here</strong> on the open stage. It
          records where you got to, which is data we want, and the questions still follow.
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

/**
 * One stage, in one of three phases.
 *
 * The work phase runs the countdown. The answer phase (the quiz and the three
 * rating statements) runs no clock at all: it is a measurement of what the
 * person took away, not more work to race through, and the phase boundary is
 * stored (`workEndedAt`) so the timing analysis covers the work alone. The
 * phase is derived from the document rather than held in state, so a reload
 * lands exactly where the participant was.
 */
function StageCardView({
  state,
  spec,
  pid,
  half,
  block,
  doc,
}: {
  state: 'locked' | 'open' | 'done'
  spec: RequestSpec
  pid: string
  half: 1 | 2
  block: ReturnType<typeof blockFor>
  doc: RequestDoc | undefined
}) {
  const project = block.project
  const phase: 'work' | 'answers' =
    state === 'open' && doc?.workEndedAt ? 'answers' : 'work'
  const capMs = spec.capMin * 60_000
  const [now, setNow] = useState(Date.now())
  const [pauseOpen, setPauseOpen] = useState(false)

  useEffect(() => {
    if (state !== 'open' || phase !== 'work') return
    const t = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(t)
  }, [state, phase])

  const activePause = (doc?.pauses ?? []).find((p) => p.to == null) ?? null
  const pausedMs = pausedMsOf(doc, now)
  const { remaining, expired } = useCountdown({
    openedAt: doc?.openedAt ?? null,
    capMs,
    pausedMs,
    running: state === 'open' && phase === 'work' && !activePause,
  })

  // Opening the stage starts its clock, and drops a marker into the same event
  // stream the machine writes to, so telemetry can be sliced by stage without
  // the machine having to know what a stage is.
  useEffect(() => {
    if (state !== 'open') return
    if (doc?.openedAt) return
    void (async () => {
      await openRequest(pid, spec.id, block, capMs)
      await markRequestBoundary(pid, spec.id, block, 'open')
    })()
  }, [state, doc?.openedAt, pid, spec.id, block, capMs])

  // Record that the cap was reached, once.
  useEffect(() => {
    if (state === 'open' && phase === 'work' && expired && doc && !doc.hitCap) {
      void patchRequest(pid, spec.id, half, { hitCap: true })
    }
  }, [state, phase, expired, doc, pid, spec.id, half])

  async function togglePause(reason?: PauseInterval['reason']) {
    if (!doc) return
    const pauses = [...(doc.pauses ?? [])]
    const open = pauses.findIndex((p) => p.to == null)
    if (open >= 0) {
      pauses[open] = { ...pauses[open], to: Date.now() }
    } else {
      pauses.push({ from: Date.now(), to: null, reason: reason ?? 'break' })
    }
    await patchRequest(pid, spec.id, half, { pauses })
    setPauseOpen(false)
  }

  /**
   * End of the work phase. The clock stops here and the timing fields are
   * written here, so `elapsedMs`/`activeMs` mean the work alone and nothing
   * the answer phase does can move them. The telemetry boundary closes here
   * too, for the same reason.
   */
  async function endWork(selfReport: RequestDoc['selfReport']) {
    const t = Date.now()
    const openedAt = doc?.openedAt ?? t
    await patchRequest(pid, spec.id, half, {
      workEndedAt: t,
      elapsedMs: t - openedAt,
      activeMs: Math.max(0, t - openedAt - pausedMsOf(doc, t)),
      pauses: (doc?.pauses ?? []).map((p) => (p.to == null ? { ...p, to: t } : p)),
      selfReport: doc?.selfReport ?? selfReport,
    })
    await markRequestBoundary(pid, spec.id, block, 'close')
  }

  if (state === 'locked') {
    return (
      <div className="card soft" style={{ opacity: 0.6 }}>
        <div className="row" style={{ justifyContent: 'space-between' }}>
          <div>
            <div className="eyebrow">{spec.heading}</div>
            <div className="strong">{spec.title[project]}</div>
          </div>
          <span className="badge outline">{spec.capMin} min + questions</span>
        </div>
      </div>
    )
  }

  if (state === 'done') {
    const quiz = doc?.quiz ?? {}
    const picks = Array.isArray(quiz['behaviours']) ? (quiz['behaviours'] as string[]) : null
    return (
      <div className="card">
        <div className="row" style={{ justifyContent: 'space-between' }}>
          <div>
            <div className="eyebrow">{spec.heading} · finished</div>
            <div className="strong">{spec.title[project]}</div>
          </div>
          <span className="badge good">
            {doc?.selfReport === 'done'
              ? 'Marked done'
              : doc?.selfReport === 'partial'
                ? 'Partly done'
                : doc?.selfReport === 'blocked'
                  ? 'Blocked'
                  : 'Stopped'}
          </span>
        </div>
        <div className="small muted" style={{ marginTop: '0.75rem' }}>
          {picks != null && (
            <span>
              {picks.length} of {BEHAVIOURS.length} ticked.{' '}
            </span>
          )}
          {spec.identify && (doc?.locate ? `Named: ${doc.locate}` : 'Nothing named.')}
        </div>
      </div>
    )
  }

  if (phase === 'answers') {
    return (
      <div className="card" style={{ borderColor: 'var(--accent-line)' }}>
        <div className="row" style={{ justifyContent: 'space-between', alignItems: 'flex-start' }}>
          <div>
            <div className="eyebrow">{spec.heading} · questions</div>
            <h2 style={{ margin: 0 }}>{spec.title[project]}</h2>
          </div>
          <span className="badge outline">not timed</span>
        </div>
        <StageAnswers pid={pid} half={half} spec={spec} doc={doc} project={project} />
      </div>
    )
  }

  return (
    <div className="card" style={{ borderColor: 'var(--accent-line)' }}>
      <div className="row" style={{ justifyContent: 'space-between', alignItems: 'flex-start' }}>
        <div>
          <div className="eyebrow">{spec.heading}</div>
          <h2 style={{ margin: 0 }}>{spec.title[project]}</h2>
        </div>
        <Countdown remaining={remaining} capMs={capMs} />
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
          Wrap up where you are and go to the questions. Running out is expected on some stages
          and it is recorded as a normal outcome, not a failure.
        </Callout>
      )}

      <div style={{ marginTop: '1rem' }}>
        <div className="no-copy" onCopy={blockProseCopy}>
          <Markdown>{spec.body[project]}</Markdown>
        </div>
        <PrescribedRun run={spec.run} project={project} />
        <Tips tips={spec.tips[block.condition]} />
        {spec.identify && (
          <TextAnswer
            pid={pid}
            half={half}
            request={spec.id}
            doc={doc}
            field="locate"
            label={spec.identify[project]}
            placeholder="a commit hash, a name, an id, or what you have and how sure you are"
          />
        )}
      </div>

      <hr />

      <div className="row" style={{ justifyContent: 'space-between' }}>
        <div className="row tight">
          <button className="btn primary" onClick={() => endWork('done')}>
            I am done — go to the questions
          </button>
          <button className="btn" onClick={() => endWork('partial')}>
            Stop here
          </button>
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

/**
 * The untimed answer phase of one stage: the quiz items in order, the
 * confidence slider where the quiz has a right answer, and the three rating
 * statements.
 *
 * All answers live in one local object mirrored to localStorage on every
 * change and debounced to the server, so a crash costs at most the debounce
 * window. Submitting writes the local object directly, so nothing can sit in
 * the debounce and be lost -- the failure mode the old design's radio-button
 * rescue existed for.
 */
function StageAnswers({
  pid,
  half,
  spec,
  doc,
  project,
}: {
  pid: string
  half: 1 | 2
  spec: RequestSpec
  doc: RequestDoc | undefined
  project: Project
}) {
  type Answers = {
    quiz: Record<string, string | string[] | null>
    ratings: Record<string, number>
    confidence: number | null
    confidenceScale?: 7
  }
  const key = draftKey(pid, 'answers', `${spec.id}-h${half}`)
  const [a, setA] = useState<Answers>(() => {
    const local = readDraft<Answers>(key)
    if (local && local.at > (doc?.submittedAt ?? 0)) return local.value
    return {
      quiz: doc?.quiz ?? {},
      ratings: doc?.ratings ?? {},
      confidence: doc?.confidence ?? null,
      confidenceScale: doc?.confidenceScale,
    }
  })
  const [dirty, setDirty] = useState(false)

  const update = (patch: Partial<Answers>) => {
    setA((prev) => ({ ...prev, ...patch }))
    setDirty(true)
  }

  useEffect(() => {
    if (!dirty) return
    writeDraft(key, a)
    const t = window.setTimeout(() => {
      void patchRequest(pid, spec.id, half, a)
    }, 600)
    return () => window.clearTimeout(t)
  }, [a, dirty, key, pid, spec.id, half])

  useFlushOnHide(() => {
    if (dirty) void patchRequest(pid, spec.id, half, a)
  })

  async function submit() {
    await patchRequest(pid, spec.id, half, { ...a, submittedAt: Date.now() })
  }

  // What still has to be answered before submitting. Behaviour checklists are
  // deliberately NOT required: ticking nothing is a real answer ("none of
  // these"), and forcing a tick would manufacture data. Free text is optional
  // for the same reason it is never scored.
  const missing: string[] = []
  for (const q of spec.quiz) {
    if (q.kind === 'choice' && !a.quiz[q.id]) missing.push('the multiple choice')
  }
  if (spec.quizConfidence && a.confidence == null) missing.push('how sure you are')
  if (spec.ratings.some((r) => a.ratings[r.id] == null)) missing.push('the statements')

  return (
    <div className="stack tight" style={{ marginTop: '0.75rem' }}>
      <Callout kind="soft" title="The clock has stopped">
        Answer from what you saw and did. You do not need to go back to the project.
      </Callout>

      {spec.quiz.map((q) => (
        <QuizItemView key={q.id} q={q} project={project} answers={a.quiz} update={(quiz) => update({ quiz })} />
      ))}

      {spec.quizConfidence && (
        <LikertRow
          name={`${spec.id}-confidence`}
          label="How sure are you of those answers?"
          anchors={['Not at all sure', 'Completely sure']}
          value={a.confidence}
          onChange={(confidence) => update({ confidence, confidenceScale: 7 })}
        />
      )}

      <div className="field-label" style={{ marginTop: '0.75rem' }}>
        {spec.ratings.length === 2
          ? 'Two statements about this stage. Rate how much you agree with each.'
          : `${spec.ratings.length} statements about this stage. Rate how much you agree with each.`}
      </div>
      {spec.ratings.map((r) => (
        <LikertRow
          key={r.id}
          name={`${spec.id}-${r.id}`}
          label={r.label}
          anchors={['Strongly disagree', 'Strongly agree']}
          value={a.ratings[r.id] ?? null}
          onChange={(v) => update({ ratings: { ...a.ratings, [r.id]: v } })}
        />
      ))}

      <div className="row" style={{ justifyContent: 'space-between', alignItems: 'center' }}>
        <button className="btn primary" disabled={missing.length > 0} onClick={() => void submit()}>
          Submit and continue
        </button>
        {missing.length > 0 && (
          <span className="small muted">Still to answer: {[...new Set(missing)].join(', ')}.</span>
        )}
      </div>
    </div>
  )
}

function QuizItemView({
  q,
  project,
  answers,
  update,
}: {
  q: QuizItem
  project: Project
  answers: Record<string, string | string[] | null>
  update: (quiz: Record<string, string | string[] | null>) => void
}) {
  if (q.kind === 'behaviours') {
    const picks = Array.isArray(answers[q.id]) ? (answers[q.id] as string[]) : []
    const toggle = (id: string) =>
      update({
        ...answers,
        [q.id]: picks.includes(id) ? picks.filter((x) => x !== id) : [...picks, id],
      })
    return (
      <div>
        <div className="field-label">{q.prompt}</div>
        <div className="grid-2" style={{ marginTop: '0.5rem' }}>
          {BEHAVIOURS.map((b) => {
            const on = picks.includes(b.id)
            return (
              <label key={b.id} className={`check${on ? ' on' : ''}`}>
                <input type="checkbox" checked={on} onChange={() => toggle(b.id)} />
                <span>
                  {b.label[project]}
                  <br />
                  <code className="tiny">{b.command[project]}</code>
                </span>
              </label>
            )
          })}
        </div>
        <div className="small muted tabular" style={{ marginTop: '0.25rem' }}>
          {picks.length} of {BEHAVIOURS.length} ticked.
        </div>
      </div>
    )
  }

  if (q.kind === 'choice') {
    const value = typeof answers[q.id] === 'string' ? (answers[q.id] as string) : null
    return (
      <div>
        <div className="field-label">{q.prompt}</div>
        <div className="stack tight" style={{ marginTop: '0.25rem' }}>
          {q.options.map((o) => (
            <label key={o.value} className={`check${value === o.value ? ' on' : ''}`}>
              <input
                type="radio"
                name={q.id}
                checked={value === o.value}
                onChange={() => update({ ...answers, [q.id]: o.value })}
              />
              <span>{o.label}</span>
            </label>
          ))}
        </div>
      </div>
    )
  }

  const text = typeof answers[q.id] === 'string' ? (answers[q.id] as string) : ''
  return (
    <div>
      <label className="field-label" htmlFor={`quiz-${q.id}`}>
        {q.prompt}
      </label>
      <textarea
        id={`quiz-${q.id}`}
        rows={2}
        value={text}
        onChange={(e) => update({ ...answers, [q.id]: e.target.value })}
      />
    </div>
  )
}

/**
 * One free-text box, debounced to one field of the request document, shown
 * during the WORK phase because typing the identifier is the work. What
 * somebody typed while reading code cannot be reconstructed by asking again.
 */
function TextAnswer({
  pid,
  half,
  request,
  doc,
  field,
  label,
  placeholder,
}: {
  pid: string
  half: 1 | 2
  request: RequestId
  doc: RequestDoc | undefined
  field: 'locate' | 'notes'
  label: string
  placeholder?: string
}) {
  // A crash-safe mirror, keyed to this request and field.
  const key = draftKey(pid, field, `${request}-h${half}`)
  const stored = (doc?.[field] ?? '') as string
  const [text, setText] = useState<string>(stored)
  const [dirty, setDirty] = useState(false)

  // Recover a draft the server never received -- the browser died between a
  // keystroke and the debounce. Only when it is strictly newer than what came
  // back, so a stale draft from a previous attempt cannot resurrect itself
  // over a real submitted answer.
  useEffect(() => {
    const local = readDraft<{ text: string }>(key)
    if (!local) return
    if (local.at <= (doc?.submittedAt ?? 0) || !local.value.text) return
    if (local.value.text === stored) return
    setText(local.value.text)
    setDirty(true)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key])

  // Adopt the stored value only while the box is untouched, so a slow snapshot
  // cannot overwrite what the participant is in the middle of typing.
  useEffect(() => {
    if (!dirty && stored && stored !== text) setText(stored)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stored])

  useEffect(() => {
    if (!dirty) return
    writeDraft(key, { text })
    const t = window.setTimeout(() => {
      void patchRequest(pid, request, half, { [field]: text })
    }, 600)
    return () => window.clearTimeout(t)
  }, [text, dirty, pid, request, half, key, field])

  useFlushOnHide(() => {
    if (dirty) void patchRequest(pid, request, half, { [field]: text })
  })

  return (
    <div className="stack tight" style={{ marginTop: '0.75rem' }}>
      <label className="field-label" htmlFor={`${request}-h${half}-${field}`}>
        {label}
      </label>
      <textarea
        id={`${request}-h${half}-${field}`}
        rows={2}
        value={text}
        placeholder={placeholder}
        onChange={(e) => {
          setText(e.target.value)
          setDirty(true)
        }}
      />
    </div>
  )
}

/**
 * What the stage's prescribed command does, printed under the card body.
 *
 * The command itself already appears inline in the body, where the story
 * needs it; repeating it here as a second block made the card read like two
 * different instructions. What stays is the explanation, so a prescribed step
 * is never a black box: a participant who wants to know what they just ran
 * can read it without leaving the card.
 */
function PrescribedRun({ run, project }: { run: PrescribedRunSpec; project: Project }) {
  return (
    <div className="small muted" style={{ marginTop: '0.75rem' }}>
      What <code>{run.script[project]}</code> does:
      <ul style={{ margin: '0.25rem 0 0' }}>
        {run.does[project].map((d, i) => (
          <li key={i}>{d}</li>
        ))}
      </ul>
    </div>
  )
}

/**
 * The command reminders for this stage, in this arm.
 *
 * Shown for the whole working phase, not tucked behind a disclosure. A
 * participant four minutes into a stage who cannot remember a flag does not go
 * looking for a collapsed panel; they guess, or they lose the stage. Both arms
 * get the same number of lines about their own tool.
 */
function Tips({ tips }: { tips: string[] }) {
  if (!tips.length) return null
  return (
    <div className="card soft" style={{ marginTop: '0.75rem' }}>
      <div className="field-label">Commands you may want</div>
      <ul className="small" style={{ margin: '0.35rem 0 0', paddingLeft: '1.1rem' }}>
        {tips.map((t, i) => (
          <li key={i} style={{ marginBottom: '0.25rem' }}>
            <MarkdownLine>{t}</MarkdownLine>
          </li>
        ))}
      </ul>
    </div>
  )
}

/**
 * One seven-point agreement row: the statement, seven circles, an anchor at
 * each end.
 *
 * Confidence used to be a 0-100 slider here. Two problems. A slider draws its
 * thumb somewhere from the moment it renders, so an untouched one looks
 * answered -- which is why the old component carried a whole apparatus of
 * dimming and synthetic commit events. And it put a hundred-point judgement
 * next to seven-point judgements on the same screen, which is two scales to
 * hold in your head for no gain. Seven discrete targets have no default
 * position, so an unanswered row is simply empty.
 *
 * Stored 1-7. `RequestDoc.confidenceScale` says so, because the pilots' stored
 * confidences are on the old 0-100 scale and the two are not comparable.
 */
function LikertRow({
  name,
  label,
  anchors,
  value,
  onChange,
}: {
  name: string
  label: string
  anchors: [string, string]
  value: number | null
  onChange: (v: number) => void
}) {
  return (
    <div style={{ marginBottom: '0.65rem' }}>
      <div className="small" style={{ marginBottom: '0.25rem' }}>
        {label}
      </div>
      <div className="likert">
        <div className="likert-opts" role="radiogroup" aria-label={label}>
          <span className="likert-anchor">{anchors[0]}</span>
          {[1, 2, 3, 4, 5, 6, 7].map((p) => (
            <label key={p} className={`likert-opt${value === p ? ' on' : ''}`}>
              <input
                type="radio"
                name={name}
                checked={value === p}
                onChange={() => onChange(p)}
              />
              {p}
            </label>
          ))}
          <span className="likert-anchor">{anchors[1]}</span>
        </div>
      </div>
    </div>
  )
}
