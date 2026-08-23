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
import type { PauseInterval, Project, ReachStage, RequestDoc, RequestId } from '../../lib/types'
import { blockFor, type Step } from '../../study/flow'
import {
  BEHAVIOURS,
  BLOCK_CAP_MIN,
  SCENARIO,
  requestHeading,
  taskCards,
  type PrescribedRun as PrescribedRunSpec,
  type ReachTrial,
} from '../../study/tasks'
import { TASK_PREAMBLE } from '../../study/content'
import { Callout, Countdown, fmtClock, useCountdown } from '../../ui/bits'
import { Markdown } from '../../ui/Markdown'

const PAUSE_REASONS: Array<[PauseInterval['reason'], string]> = [
  ['break', 'Taking a break'],
  ['facilitator', 'Talking to the facilitator'],
  ['tool-failure', 'Something is broken'],
  ['other', 'Something else'],
]

/**
 * Copying the request text is refused unless the selection sits inside code.
 *
 * `user-select: none` already stops a mouse selection; this catches select-all
 * and the keyboard. It is not a security control and does not need to be: the
 * point is that pasting the request into the assistant should not be the
 * easiest thing to do, not that it should be impossible.
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

      <div className="card soft no-copy" onCopy={blockProseCopy}>
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
  const reach = lead.reach
  // A reach card runs two stage clocks of its own, and the card cap is their sum.
  // Showing both would put two countdowns on one card disagreeing about how long
  // is left, so the card's is suppressed and the stage's is the only one. `hitCap`
  // follows the same rule: with no card clock there is no card cap to hit, and
  // whether a stage ran out is recorded in the stage.
  const capMs = reach ? 0 : (card.capMin ?? 0) * 60_000
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
      // The last answer, taken from the draft rather than from `doc`.
      //
      // Answering is debounced by 600ms, and submitting unmounts the card that
      // owns the debounce, so a pick made in that window is cleared before it is
      // written and then refused on recovery for being older than `submittedAt`.
      // It sat in localStorage, unreachable. That was survivable when the last
      // act before submitting was finishing a typed sentence; with radio buttons
      // the last act IS a click, and "choose the last option, press Mark done"
      // is the normal rhythm. Reading the draft here closes the window without
      // touching the debounce machinery, and writes nothing when there is
      // nothing newer to write.
      const draft = readDraft<{ picks: Record<string, number>; confidence: number | null }>(
        draftKey(pid, 'choices', `${r.id}-h${half}`),
      )
      // `?? {}` because Firestore is initialised without `ignoreUndefinedProperties`, so a single
      // undefined field rejects the entire `setDoc` -- `submittedAt` with it. A truncated or
      // hand-edited draft would then make "Mark done" do nothing at all, silently, which is a
      // worse failure than the one this rescue exists to prevent. The sibling recovery path
      // defends the same way.
      const rescued =
        draft && draft.at > (doc?.submittedAt ?? 0)
          ? { choices: draft.value.picks ?? {}, confidence: draft.value.confidence ?? null }
          : {}
      await patchRequest(pid, r.id, half, {
        submittedAt: t,
        elapsedMs: t - openedAt,
        activeMs: Math.max(0, t - openedAt - pausedMsOf(leadDoc, t)),
        pauses: (leadDoc?.pauses ?? []).map((p) => (p.to == null ? { ...p, to: t } : p)),
        selfReport: doc?.selfReport ?? selfReport,
        ...rescued,
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
          if (r.reach && d?.stages) {
            const n = (s?: { picks: string[] }) => s?.picks.length ?? 0
            return (
              <div key={r.id} className="small muted" style={{ marginTop: '0.75rem' }}>
                First answer {n(d.stages.blind)} of {BEHAVIOURS.length}, final answer{' '}
                {n(d.stages.checked)} of {BEHAVIOURS.length}.
              </div>
            )
          }
          const written = r.identify ? d?.locate : r.note ? d?.notes : undefined
          if (!r.identify && !r.note) return null
          return (
            <div key={r.id} className="small" style={{ marginTop: '0.75rem' }}>
              <div className="muted tiny">{r.title[project]}</div>
              {/* A dash here left the participant's own recap reading as a table with a
                  hole in it. The word says which line they left blank. */}
              <div className={written ? undefined : 'muted'}>{written || 'Not answered'}</div>
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
            {card.requests.length > 1 && (
              <h3>
                {requestHeading(r)}: {r.title[project]}
              </h3>
            )}
            <div className="no-copy" onCopy={blockProseCopy}>
              <Markdown>{r.body[project]}</Markdown>
            </div>
            {r.run && <PrescribedRun run={r.run} project={project} />}
            {r.reach && (
              <ReachAnswers
                pid={pid}
                half={half}
                request={r.id}
                doc={docFor(r.id)}
                trial={r.reach}
                project={project}
              />
            )}
            {r.identify && (
              <TextAnswer
                pid={pid}
                half={half}
                request={r.id}
                doc={docFor(r.id)}
                field="locate"
                label={r.identify[project]}
                placeholder="a commit hash, a feature name, an id — or what you have and how sure you are"
              />
            )}
            {r.note && (
              <TextAnswer
                pid={pid}
                half={half}
                request={r.id}
                doc={docFor(r.id)}
                field="notes"
                label={r.note[project]}
              />
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
        {/* The blind minute inside the card runs its own clock and cannot be
            paused -- it is the control that makes `blind` mean "at a glance", and
            a pause button beside it would be a way to take ten minutes over it.
            The card's clock, which covers the actual work, can be. */}
        {(
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
        )}
      </div>
    </div>
  )
}

/**
 * One free-text box, debounced to one field of the request document.
 *
 * Two steps use it and they want different things written down, so the field is
 * a parameter: a locate step writes `locate` (compared against the key after the
 * session) and an observation step writes `notes` (never scored). Everything
 * else is identical, and the hazard they share is the reason this is one
 * component rather than two -- what somebody typed while reading code cannot be
 * reconstructed by asking them again.
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
  // A crash-safe mirror, keyed to this request and field. The questionnaires
  // have had one since the start; these answers did not.
  const key = draftKey(pid, field, `${request}-h${half}`)
  const stored = (doc?.[field] ?? '') as string
  const [text, setText] = useState<string>(stored)
  const [dirty, setDirty] = useState(false)

  // Recover a draft the server never received -- the browser died between a
  // keystroke and the debounce. Only when it is strictly newer than what came
  // back, so a stale draft from a previous attempt cannot resurrect itself over
  // a real submitted answer.
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
        rows={field === 'locate' ? 2 : 4}
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
 * A prescribed step: the command, and what running it does.
 *
 * The command is prescribed so both arms see byte-identical output and nobody's
 * result turns on their typing. What it does is printed underneath so that
 * prescribing it does not make it a black box -- a participant who wants to know
 * what they just ran can read it without leaving the card.
 */
function PrescribedRun({ run, project }: { run: PrescribedRunSpec; project: Project }) {
  return (
    <div style={{ marginTop: '0.75rem' }}>
      <pre className="cmd">
        <code>{run.script[project]}</code>
      </pre>
      <div className="small muted" style={{ marginTop: '0.4rem' }}>
        It:
        <ul style={{ margin: '0.25rem 0 0' }}>
          {run.does[project].map((d, i) => (
            <li key={i}>{d}</li>
          ))}
        </ul>
      </div>
    </div>
  )
}

/**
 * Its own component because of one hazard: touching the thumb where it already
 * sits fires no change event, so a participant who means exactly 50 leaves no
 * answer at all. Confidence is half of the calibration measure, and losing it
 * quietly is worse than losing it loudly, so `onPointerDown` commits the
 * midpoint and an unanswered slider says so in words.
 */
function ConfidenceSlider({
  value,
  onChange,
  label = 'How sure are you?',
}: {
  value: number | null
  onChange: (v: number) => void
  label?: string
}) {
  return (
    <div style={{ marginTop: '0.5rem' }}>
      <div className="field-label">{label}</div>
      <div className="row" style={{ flexWrap: 'nowrap', gap: '0.75rem' }}>
        <input
          type="range"
          min={0}
          max={100}
          step={5}
          value={value ?? 50}
          aria-label="Confidence"
          aria-valuetext={value == null ? 'not answered yet' : String(value)}
          style={value == null ? { opacity: 0.72 } : undefined}
          onChange={(e) => onChange(Number(e.target.value))}
          onPointerDown={() => {
            if (value == null) onChange(50)
          }}
        />
        <span className="tlx-value" style={value == null ? { color: 'var(--faint)' } : undefined}>
          {value == null ? '–' : value}
        </span>
      </div>
      <div className="anchors">
        <span>Guessing</span>
        <span>Certain</span>
      </div>
      {value == null && (
        <div className="tiny faint">Not answered yet. Click anywhere on the line.</div>
      )}
    </div>
  )
}

/** The five states a reach trial passes through, in order. */
type ReachPhase = 'intro' | 'blind' | 'rateBlind' | 'work' | 'checked' | 'rateChecked'

/**
 * The two-stage reach trial: tick the behaviours this piece of work reaches, once
 * from the representation alone and once after checking properly.
 *
 * Why five states rather than one form with two columns. The measurement is the
 * difference between the two answers, and that only means anything if the first
 * one was committed before the second was possible -- a single form lets a
 * participant fill in the "blind" column after looking, in good faith, and there
 * is nothing in the data afterwards that shows they did. So the blind answer is
 * written to the server before the checked stage opens, and the blind grid is
 * read-only from that point on.
 *
 * Why confidence is rated after each stage's clock has stopped. Rating inside the
 * minute would spend the minute, and the minute is there to bound reading, not to
 * price a slider.
 *
 * Why the checked stage starts from the blind picks rather than empty. They are
 * revising a prediction, not making an unrelated second one, and re-ticking twelve
 * boxes from scratch would spend the stage on data entry. It anchors them, which
 * makes `gain` harder to earn rather than easier -- the conservative direction for
 * the claim it supports.
 */
function ReachAnswers({
  pid,
  half,
  request,
  doc,
  trial,
  project,
}: {
  pid: string
  half: 1 | 2
  request: RequestId
  doc: RequestDoc | undefined
  trial: ReachTrial
  project: Project
}) {
  // The draft carries what the server does not yet have: the picks in progress and
  // the blind stage's deadline. A reload inside the minute would otherwise have no
  // origin to count from and would silently restart the clock.
  const key = draftKey(pid, 'reach', `${request}-h${half}`)
  type Draft = { phase: ReachPhase; picks: string[]; endsAt: number | null }

  // Recovery order matters, and one order is wrong in a way nothing would report:
  // resuming to `checked` because a blind stage exists skips the rating in between,
  // so the blind confidence is lost for good and the calibration measure quietly
  // has a hole in it. An unrated blind stage therefore resumes at its rating.
  const [phase, setPhase] = useState<ReachPhase>(() => {
    if (doc?.stages?.checked) return 'rateChecked'
    if (doc?.stages?.blind) return doc.stages.blind.confidence == null ? 'rateBlind' : 'work'
    return readDraft<Draft>(key)?.value.phase ?? 'intro'
  })
  // Draft first: it is written on every change, so it is never older than the
  // server's copy, and during the checked stage it is the only copy of the edits.
  const [picks, setPicks] = useState<string[]>(
    () => readDraft<Draft>(key)?.value.picks ?? doc?.stages?.blind?.picks ?? [],
  )
  const [confidence, setConfidence] = useState<number | null>(null)
  const [endsAt, setEndsAt] = useState<number | null>(
    () => readDraft<Draft>(key)?.value.endsAt ?? null,
  )

  useEffect(() => {
    writeDraft(key, { phase, picks, endsAt } satisfies Draft)
  }, [key, phase, picks, endsAt])

  // One ticking clock, owned by whichever stage is running.
  const [now, setNow] = useState(Date.now())
  const running = phase === 'blind' || phase === 'checked'
  useEffect(() => {
    if (!running) return
    const t = window.setInterval(() => setNow(Date.now()), 250)
    return () => window.clearInterval(t)
  }, [running])
  const capMs = (phase === 'blind' ? trial.blindSec : trial.checkedSec) * 1000
  const remaining = endsAt ? endsAt - now : capMs

  /**
   * Time in the stage is derived from the deadline rather than from a start held in
   * state, so a reload mid-stage resumes with the elapsed time intact instead of
   * counting again from zero. `merge: true` merges nested maps, so writing one
   * stage leaves the other alone and no stale local copy has to be spread in.
   */
  const buildStage = useCallback(
    (stage: 'blind' | 'checked', at: number, deadline: number | null, conf: number | null) => {
      const spanMs = (stage === 'blind' ? trial.blindSec : trial.checkedSec) * 1000
      return {
        picks,
        confidence: conf,
        submittedAt: at,
        activeMs: deadline ? Math.max(0, Math.min(spanMs, spanMs - (deadline - at))) : 0,
      } satisfies ReachStage
    },
    [picks, trial.blindSec, trial.checkedSec],
  )

  const write = useCallback(
    (stage: 'blind' | 'checked', value: ReachStage) =>
      patchRequest(pid, request, half, { stages: { [stage]: value } }),
    [pid, request, half],
  )

  /**
   * The blind stage as written, kept so the rating that follows can be added to it
   * without recomputing the timing. Recomputing is what introduced the bug this
   * replaces: it folded the seconds spent moving the confidence slider into
   * `activeMs`, and `activeMs` is what says whether the blind answer was read off
   * the representation or reasoned out from general knowledge.
   */
  const [blindStage, setBlindStage] = useState<ReachStage | null>(doc?.stages?.blind ?? null)

  const submitBlind = useCallback(
    (at: number) => {
      const value = buildStage('blind', at, endsAt, null)
      setBlindStage(value)
      void write('blind', value)
    },
    [buildStage, endsAt, write],
  )

  // The minute is hard. Whatever is ticked when it runs out is the blind answer,
  // which is the point: an answer improved after the deadline is not a blind one.
  useEffect(() => {
    if (phase !== 'blind' || !endsAt || now < endsAt) return
    setPhase('rateBlind')
    submitBlind(endsAt)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [phase, endsAt, now])

  useFlushOnHide(() => {
    writeDraft(key, { phase, picks, endsAt } satisfies Draft)
  })

  function toggle(id: string) {
    setPicks((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]))
  }

  const grid = (editable: boolean) => (
    <div className="grid-2" style={{ marginTop: '0.75rem' }}>
      {BEHAVIOURS.map((b) => {
        const on = picks.includes(b.id)
        return (
          <label
            key={b.id}
            className={`check${on ? ' on' : ''}`}
            style={editable ? undefined : { cursor: 'default', opacity: on ? 1 : 0.55 }}
          >
            <input
              type="checkbox"
              checked={on}
              disabled={!editable}
              onChange={() => toggle(b.id)}
            />
            <span>
              {b.label[project]}
              <br />
              <code className="tiny">{b.command[project]}</code>
            </span>
          </label>
        )
      })}
    </div>
  )

  const counted = (
    <div className="small muted tabular">
      {picks.length} of {BEHAVIOURS.length} ticked
    </div>
  )

  if (phase === 'intro') {
    return (
      <div className="stack tight" style={{ marginTop: '1rem' }}>
        <Callout kind="accent" title={`First answer: ${trial.blindSec} seconds on the clock`}>
          Tick what you think the change you are about to make will affect. The clock starts when
          you press the button, runs for <strong>{trial.blindSec} seconds</strong>, and submits
          whatever is ticked when it ends. Then you go and do it, and afterwards you answer once
          more knowing what actually happened. Getting the first one wrong is expected and is not
          held against you — the difference between the two answers is the whole measurement.
        </Callout>
        {grid(false)}
        <div>
          <button
            className="btn primary"
            onClick={() => {
              const t = Date.now()
              setEndsAt(t + trial.blindSec * 1000)
              setNow(t)
              setPhase('blind')
            }}
          >
            Start the {trial.blindSec} seconds
          </button>
        </div>
      </div>
    )
  }

  if (phase === 'blind') {
    return (
      <div className="stack tight" style={{ marginTop: '1rem' }}>
        <div className="row" style={{ justifyContent: 'space-between', alignItems: 'center' }}>
          <div className="field-label" style={{ margin: 0 }}>
            First answer, from what you can see
          </div>
          <Countdown remaining={remaining} capMs={capMs} />
        </div>
        {grid(true)}
        <div className="row" style={{ justifyContent: 'space-between' }}>
          <button
            className="btn primary"
            onClick={() => {
              setPhase('rateBlind')
              submitBlind(Date.now())
            }}
          >
            Lock this in
          </button>
          {counted}
        </div>
      </div>
    )
  }

  if (phase === 'rateBlind') {
    return (
      <div className="stack tight" style={{ marginTop: '1rem' }}>
        <div className="field-label">Your first answer is in, {picks.length} ticked</div>
        {grid(false)}
        <ConfidenceSlider
          label="How sure are you of that first answer?"
          value={confidence}
          onChange={setConfidence}
        />
        <div>
          <button
            className="btn primary"
            disabled={confidence == null}
            onClick={() => {
              if (blindStage) void write('blind', { ...blindStage, confidence })
              setConfidence(null)
              setEndsAt(null)
              setPhase('work')
            }}
          >
            Saved — now go and do it
          </button>
          {confidence == null && (
            <span className="small muted" style={{ marginLeft: '0.75rem' }}>
              Rate it first.
            </span>
          )}
        </div>
      </div>
    )
  }

  // The gap the second answer is grounded in. The old design's second answer was
  // another read of the same screen, three minutes later; this one is answered
  // after the operation has run, so `checked - blind` is what doing it taught
  // them rather than what looking twice did. The clock does not run here: the
  // work has the card's own clock and a second one beside it would be two
  // deadlines for one activity.
  if (phase === 'work') {
    return (
      <div className="stack tight" style={{ marginTop: '1rem' }}>
        <Callout kind="accent" title="Your first answer is saved">
          Now do the rest of this step. When you have run it and checked where you ended up, come
          back here and answer once more.
        </Callout>
        <div>
          <button
            className="btn primary"
            onClick={() => {
              const t = Date.now()
              setEndsAt(t + trial.checkedSec * 1000)
              setNow(t)
              setPhase('checked')
            }}
          >
            I have done it — answer again
          </button>
        </div>
      </div>
    )
  }

  if (phase === 'checked') {
    return (
      <div className="stack tight" style={{ marginTop: '1rem' }}>
        <div className="row" style={{ justifyContent: 'space-between', alignItems: 'center' }}>
          <div className="field-label" style={{ margin: 0 }}>
            Now check properly, and change whatever you got wrong
          </div>
          <Countdown remaining={remaining} capMs={capMs} />
        </div>
        <Callout kind="soft" title="What you may use">
          Anything that does not change the project: read the history, read the code, run the app,
          ask the assistant. Changing nothing is fine, and so is changing all twelve.
        </Callout>
        {grid(true)}
        <div className="row" style={{ justifyContent: 'space-between' }}>
          <button className="btn primary" onClick={() => setPhase('rateChecked')}>
            That is my answer
          </button>
          {counted}
        </div>
      </div>
    )
  }

  return (
    <div className="stack tight" style={{ marginTop: '1rem' }}>
      <div className="field-label">Final answer, {picks.length} ticked</div>
      {grid(false)}
      <ConfidenceSlider
        label="How sure are you now?"
        value={confidence}
        onChange={setConfidence}
      />
      <div>
        <button
          className="btn primary"
          disabled={confidence == null}
          onClick={() => {
            void write('checked', buildStage('checked', Date.now(), endsAt, confidence))
          }}
        >
          Save this answer
        </button>
        {confidence == null && (
          <span className="small muted" style={{ marginLeft: '0.75rem' }}>
            Rate it first.
          </span>
        )}
      </div>
    </div>
  )
}
