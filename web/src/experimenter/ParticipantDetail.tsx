import { useEffect, useMemo, useState } from 'react'
import { doc, setDoc, addDoc, collection, orderBy } from 'firebase/firestore'
import { db } from '../lib/firebase'
import {
  clearDraft,
  draftKey,
  isPilot,
  patchParticipant,
  readDraft,
  useLiveCollection,
  useLiveDoc,
  writeDraft,
} from '../lib/db'
import type {
  EventDoc,
  GroundTruth,
  Half,
  Participant,
  RequestDoc,
  ResponseDoc,
  ScoringDoc,
} from '../lib/types'
import { REQUESTS, requestById } from '../study/tasks'
import { HLAC, instrumentById } from '../study/instruments'
import { gitExpertise, tlxScore, umuxLiteScore } from '../lib/stats'
import { analyzeParticipant, keysFrom, locateMatches } from '../analysis/pipeline'
import { Callout, Empty, Tabs, fmtAgo, fmtDuration } from '../ui/bits'
import { CATEGORY_COLOR } from '../charts/theme'
import { CATEGORY_LABEL } from '../study/taxonomy'
import { downloadCsv, downloadJson } from '../lib/svgExport'

type DetailTab = 'overview' | 'requests' | 'answers' | 'interview' | 'telemetry'

const PROBES = [
  { id: 'wish', label: 'What did you wish you could ask the history?', note: 'Ask this BEFORE they compare the setups.' },
  { id: 'trust', label: 'What did you trust, and what did you check?' },
  { id: 'lost', label: 'Where were you lost?' },
  { id: 'hidden', label: 'What did the history hide, and what did it show?' },
  { id: 'delegate', label: 'How did you decide what to hand to the assistant?' },
]

export function ParticipantDetail({
  pid,
  onClose,
  adminEmail,
}: {
  pid: string
  onClose: () => void
  adminEmail: string
}) {
  const [tab, setTab] = useState<DetailTab>('overview')
  const { data: participant } = useLiveDoc<Participant>(['participants', pid])
  const { data: responses } = useLiveCollection<ResponseDoc & { id: string }>([
    'participants',
    pid,
    'responses',
  ])
  const { data: requests } = useLiveCollection<RequestDoc & { id: string }>([
    'participants',
    pid,
    'requests',
  ])
  const { data: scoring } = useLiveCollection<Record<string, unknown> & { id: string }>([
    'participants',
    pid,
    'scoring',
  ])
  const { data: truth } = useLiveDoc<GroundTruth>(['study', 'groundTruth'])

  if (!participant) return <Empty>Loading</Empty>

  return (
    <div className="stack loose">
      <div className="row" style={{ justifyContent: 'space-between' }}>
        <div>
          <button className="btn sm ghost" onClick={onClose}>
            ← All participants
          </button>
          <h1 style={{ margin: '0.35rem 0 0' }}>
            {participant.label}
            {isPilot(participant) && (
              <span className="badge warn" style={{ marginLeft: '0.5rem', verticalAlign: 'middle' }}>
                pilot
              </span>
            )}
          </h1>
          <div className="small muted">
            group {participant.group} · {participant.blocks[0].condition}/
            {participant.blocks[0].project} then {participant.blocks[1].condition}/
            {participant.blocks[1].project}
            {participant.email ? ` · ${participant.email}` : ''}
          </div>
        </div>
        <div className="row tight">
          {participant.claimedUid && (
            <button
              className="btn sm"
              title="Let them open the link again from a different browser."
              onClick={() => {
                // A cleared cache, a second laptop, or a private window all
                // look identical to a second person taking the link. The guard
                // has to stay strict, so the facilitator gets the release
                // valve instead: it is one click here, versus a lost session.
                if (confirm(`Release ${participant.label}'s link so it can be opened again?`)) {
                  void patchParticipant(pid, { claimedUid: null })
                }
              }}
            >
              Release link
            </button>
          )}
          {/* This sits where the eye goes looking for a status badge, and it
              used to write the moment you touched it. A facilitator in the
              pilot clicked it expecting a read-only history display and
              silently marked a participant excluded — one click, no
              confirmation, no label saying it was editable. Dropping someone
              from a twelve-person study by accident is not a recoverable
              mistake if nobody notices. */}
          <label className="row tight small muted" style={{ gap: '0.35rem' }}>
            status
            <select
              value={participant.status}
              onChange={(e) => {
                const next = e.target.value as Participant['status']
                const drops = next === 'withdrawn' || next === 'excluded'
                if (
                  drops &&
                  !confirm(
                    `Mark ${participant.label} as ${next}? They are removed from the analysis. ` +
                      'Their data is kept and you can change this back.',
                  )
                ) {
                  return
                }
                void patchParticipant(pid, { status: next })
              }}
              style={{ width: 'auto' }}
            >
              {['created', 'claimed', 'consented', 'in-progress', 'completed', 'withdrawn', 'excluded'].map(
                (s) => (
                  <option key={s} value={s}>
                    {s}
                  </option>
                ),
              )}
            </select>
          </label>
        </div>
      </div>

      <Tabs<DetailTab>
        value={tab}
        onChange={setTab}
        tabs={[
          { id: 'overview', label: 'Overview' },
          { id: 'requests', label: 'Requests & scoring' },
          { id: 'answers', label: 'Questionnaires' },
          { id: 'interview', label: 'Interview' },
          { id: 'telemetry', label: 'Telemetry' },
        ]}
      />

      {tab === 'overview' && (
        <Overview participant={participant} responses={responses ?? []} requests={requests ?? []} />
      )}
      {tab === 'requests' && (
        <RequestScoring
          pid={pid}
          requests={requests ?? []}
          scoring={scoring ?? []}
          truth={truth}
          adminEmail={adminEmail}
        />
      )}
      {tab === 'answers' && <Answers responses={responses ?? []} participant={participant} />}
      {tab === 'interview' && <Interview pid={pid} adminEmail={adminEmail} />}
      {tab === 'telemetry' && (
        <Telemetry pid={pid} participant={participant} requests={requests ?? []} responses={responses ?? []} scoring={scoring ?? []} />
      )}
    </div>
  )
}

function Overview({
  participant,
  responses,
  requests,
}: {
  participant: Participant
  responses: Array<ResponseDoc & { id: string }>
  requests: Array<RequestDoc & { id: string }>
}) {
  const background = responses.find((r) => r.id === 'background')?.values
  const consent = responses.find((r) => r.id === 'consent')?.values
  return (
    <div className="grid-2">
      <div className="card">
        <h2>Session</h2>
        <dl className="kv">
          <dt>Started</dt>
          <dd>{participant.startedAt ? new Date(participant.startedAt).toLocaleString() : '—'}</dd>
          <dt>Consented</dt>
          <dd>{participant.consentAt ? new Date(participant.consentAt).toLocaleString() : '—'}</dd>
          <dt>Finished</dt>
          <dd>{participant.completedAt ? new Date(participant.completedAt).toLocaleString() : '—'}</dd>
          <dt>Last seen</dt>
          <dd>{fmtAgo(participant.lastSeenAt)}</dd>
          <dt>Current step</dt>
          <dd>{participant.currentStep}</dd>
          <dt>Requests opened</dt>
          <dd>
            {/* Counted apart rather than summed. A pilot ran six requests a half, so a single
                total against the current set of three reads "12 of 6" -- a fraction that is
                wrong rather than merely surprising. */}
            {requests.filter((r) => r.openedAt && requestById(r.requestId)).length} of{' '}
            {REQUESTS.length * 2}
            {requests.filter((r) => r.openedAt && !requestById(r.requestId)).length > 0 && (
              <span className="muted small">
                {' '}
                · plus {requests.filter((r) => r.openedAt && !requestById(r.requestId)).length}{' '}
                from a retired design
              </span>
            )}
          </dd>
          <dt>Quotes allowed</dt>
          <dd>{consent?.quotes === true ? 'yes' : 'no'}</dd>
        </dl>
      </div>
      <div className="card">
        <h2>Background</h2>
        {background ? (
          <dl className="kv">
            <dt>Years coding</dt>
            <dd>{String(background.yearsCoding ?? '—')}</dd>
            <dt>Years git</dt>
            <dd>{String(background.yearsGit ?? '—')}</dd>
            <dt>Git expertise</dt>
            <dd>{gitExpertise(background) ?? '—'} / 24</dd>
            <dt>Agent tools</dt>
            <dd>{Array.isArray(background.agentTools) ? background.agentTools.join(', ') : '—'}</dd>
            <dt>Assistant use</dt>
            <dd>{String(background.agentFrequency ?? '—')}</dd>
            <dt>AI share of shipped code</dt>
            <dd>{String(background.aiShare ?? '—')}%</dd>
            <dt>Languages</dt>
            <dd>{String(background.languages ?? '—')}</dd>
            <dt>Prior sgt</dt>
            <dd>{String(background.priorSgt ?? '—')}</dd>
          </dl>
        ) : (
          <Empty>Not answered yet</Empty>
        )}
      </div>
    </div>
  )
}

function RequestScoring({
  pid,
  requests,
  scoring,
  truth,
  adminEmail,
}: {
  pid: string
  requests: Array<RequestDoc & { id: string }>
  scoring: Array<Record<string, unknown> & { id: string }>
  truth: GroundTruth | null
  adminEmail: string
}) {
  const opened = requests.filter((r) => r.openedAt).sort((a, b) => (a.openedAt ?? 0) - (b.openedAt ?? 0))
  const { data: events } = useLiveCollection<EventDoc>(['participants', pid, 'events'], orderBy('ts'))
  if (opened.length === 0) return <Empty>No requests opened yet</Empty>

  return (
    <div className="stack">
      {!truth && (
        <Callout kind="warn" title="No answer key loaded">
          Load the ground-truth file under <strong>Setup</strong> and the right answer appears beside
          each request instead of having to be looked up in the build log.
        </Callout>
      )}
      <RepoOutcome pid={pid} />
      {opened.map((r) => (
        <RequestCard
          key={r.id}
          pid={pid}
          req={r}
          events={events ?? []}
          existing={scoring.find((s) => s.id === r.id) as unknown as ScoringDoc | undefined}
          truth={truth}
          adminEmail={adminEmail}
        />
      ))}
    </div>
  )
}

/**
 * How the request was actually carried out.
 *
 * Several requests can be finished in more than one way -- by rewriting the
 * code, or by operating on the history -- and the finished repository often
 * cannot tell you which happened. "Did they use the history tool or just edit
 * files and commit" is a question about the participant, not the repository. It
 * is answerable only from what ran during the request, so it belongs next to
 * the rubric.
 */
function HowItWasDone({ events, req }: { events: EventDoc[]; req: RequestDoc }) {
  const from = req.openedAt ?? 0
  const to = req.submittedAt ?? Date.now()

  const mine = useMemo(
    () =>
      events.filter(
        (e) =>
          e.ts >= from &&
          e.ts <= to &&
          e.extra?.auto !== true &&
          (e.kind === 'command' || e.kind === 'prompt' || e.kind === 'tool'),
      ),
    [events, from, to],
  )

  if (mine.length === 0) return null

  const commands = mine.filter((e) => e.kind === 'command')
  const tally = {
    git: commands.filter((e) => e.name === 'git').length,
    sgt: commands.filter((e) => e.name === 'sgt').length,
    editor: commands.filter((e) => e.extra?.surface === 'editor').length,
    assistant: mine.filter((e) => e.kind === 'tool' || e.extra?.surface === 'agent').length,
    prompts: mine.filter((e) => e.kind === 'prompt').length,
  }

  return (
    <details style={{ marginTop: '0.75rem' }}>
      <summary className="small muted" style={{ cursor: 'pointer' }}>
        How it was done — {tally.git} git, {tally.sgt} sgt, {tally.editor} in the editor,{' '}
        {tally.assistant} by the assistant, {tally.prompts} prompt(s)
      </summary>
      <div className="stack tight" style={{ marginTop: '0.5rem', maxHeight: '18rem', overflowY: 'auto' }}>
        {mine.map((e) => (
          <div key={e.id} className="row tight" style={{ flexWrap: 'nowrap', alignItems: 'baseline' }}>
            <span className="badge outline tiny">
              {e.kind === 'prompt'
                ? 'prompt'
                : e.kind === 'tool'
                  ? 'assistant'
                  : String(e.extra?.surface ?? 'terminal')}
            </span>
            <span className="tiny mono" style={{ minWidth: 0, wordBreak: 'break-word' }}>
              {(e.text ?? e.name ?? '').slice(0, 200)}
            </span>
            {e.ok === false && <span className="badge bad tiny">failed</span>}
          </div>
        ))}
      </div>
    </details>
  )
}

/**
 * What the participant actually did to the code.
 *
 * Requests 2 and 3 are judged by the state of the repository, and until this
 * existed the scoring screen offered only an empty box asking for a script's
 * output — with no way to obtain a copy of their repository at all. The
 * facilitator in the pilot could not score most of the requests, and only
 * found the script's name by reading source. This is the thing they said they
 * most expected to be here.
 */
function RepoOutcome({ pid }: { pid: string }) {
  const { data: events } = useLiveCollection<EventDoc>(
    ['participants', pid, 'events'],
    orderBy('ts', 'desc'),
  )
  const snapshots = (events ?? []).filter((e) => e.kind === 'repo' && e.name === 'outcome')
  const latest = snapshots[0]

  if (!latest) {
    return (
      <Callout kind="warn" title="No picture of their code yet">
        Their machine sends a summary of what has changed in the project every time it syncs. If
        nothing appears once they are working, the upload is not running — check the Live tab.
      </Callout>
    )
  }

  const extra = (latest.extra ?? {}) as Record<string, unknown>
  const tests = typeof extra.tests === 'string' ? extra.tests : null
  const files = typeof extra.files === 'string' ? extra.files : ''
  const testsBad = tests ? /fail|error/i.test(tests) : false

  return (
    <div className="card">
      <div className="row" style={{ justifyContent: 'space-between' }}>
        <h2 style={{ margin: 0 }}>What they did to the code</h2>
        <span className="badge outline">
          as of {fmtAgo(latest.ts)}
          {typeof extra.head === 'string' ? ` · at ${extra.head}` : ''}
        </span>
      </div>
      <p className="small muted">
        Changes against <code>{String(extra.baseline ?? 'the starting point')}</code>. This is the
        evidence for requests scored on the state of the code.
      </p>

      {tests && (
        <div className={`card ${testsBad ? 'bad' : ''}`} style={{ padding: '0.6rem 0.9rem' }}>
          <span className="tiny muted">Test suite</span>
          <div className="mono small">{tests}</div>
        </div>
      )}

      <pre style={{ marginTop: '0.75rem', maxHeight: '18rem' }}>{latest.text || 'no changes'}</pre>

      {files && (
        <details>
          <summary className="small muted" style={{ cursor: 'pointer' }}>
            Which files, and how
          </summary>
          <pre style={{ marginTop: '0.5rem', maxHeight: '16rem' }}>{files}</pre>
        </details>
      )}

      {typeof extra.uncommitted === 'number' && extra.uncommitted > 0 && (
        <p className="small" style={{ color: 'var(--warn)' }}>
          {String(extra.uncommitted)} file(s) changed but not committed at that moment.
        </p>
      )}

      {snapshots.length > 1 && (
        <p className="tiny faint">
          {snapshots.length} snapshots recorded; showing the most recent. Earlier ones are in the
          Telemetry tab.
        </p>
      )}
    </div>
  )
}

function RequestCard({
  pid,
  req,
  events,
  existing,
  truth,
  adminEmail,
}: {
  pid: string
  req: RequestDoc & { id: string }
  events: EventDoc[]
  existing: ScoringDoc | undefined
  truth: GroundTruth | null
  adminEmail: string
}) {
  const spec = requestById(req.requestId)
  const rubric = truth?.rubrics?.[req.requestId] ?? []
  // A pilot's stored requests include ones this study no longer asks. Say so and
  // move on: the alternative was resolving them anyway, which threw mid-render
  // and took every other request on the page down with it.
  const outOf = rubric.reduce((n, x) => n + x.points, 0) || 2
  const [checks, setChecks] = useState<Record<string, boolean>>(existing?.rubric ?? {})
  const [damage, setDamage] = useState<string>(
    existing?.collateralDamage != null ? String(existing.collateralDamage) : '',
  )
  const [outcome, setOutcome] = useState(existing?.outcome ?? '')
  const [scorer, setScorer] = useState(existing?.scorerOutput ?? '')
  const [note, setNote] = useState(existing?.note ?? '')
  const [saved, setSaved] = useState(false)

  useEffect(() => {
    setChecks(existing?.rubric ?? {})
    setDamage(existing?.collateralDamage != null ? String(existing.collateralDamage) : '')
    setOutcome(existing?.outcome ?? '')
    setScorer(existing?.scorerOutput ?? '')
    setNote(existing?.note ?? '')
  }, [existing?.scoredAt])

  // Unsaved scoring is kept locally, not written through.
  //
  // A score is a judgement, and half a judgement must not enter the record --
  // auto-saving would put a partial rubric in the data and make "scored" mean
  // two different things. But losing a pasted scorer output because a tab
  // closed is pure waste, so the in-progress form is mirrored to this browser
  // and offered back, while Save stays the only thing that records a score.
  const draft = draftKey(pid, 'scoring', req.id)
  const [recovered, setRecovered] = useState<null | Record<string, unknown>>(null)
  useEffect(() => {
    const local = readDraft<Record<string, unknown>>(draft)
    if (local && local.at > (existing?.scoredAt ?? 0)) setRecovered(local.value)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [draft, existing?.scoredAt])
  useEffect(() => {
    writeDraft(draft, { checks, damage, outcome, scorer, note })
  }, [draft, checks, damage, outcome, scorer, note])

  const score = rubric.reduce((n, x) => n + (checks[x.id] ? x.points : 0), 0)
  const key = truth?.requestKeys?.[req.requestId]?.[req.project]

  // The locate answer is matched provisionally by the analysis and shown here in
  // full, because the match is lenient on purpose and the experimenter is the
  // authority. A tick next to a lookup would invite agreeing with the lookup;
  // the answer next to the key invites reading both.
  const accepted = keysFrom(truth).locate[req.requestId]?.[req.project] ?? null
  const typed = (req.locate ?? '').trim()
  const provisional =
    accepted && typed ? accepted.some((a) => locateMatches(typed, a)) : null

  async function save() {
    await setDoc(doc(db, 'participants', pid, 'scoring', req.id), {
      requestId: req.requestId,
      half: req.half,
      score: rubric.length ? score : null,
      outOf: rubric.length ? outOf : null,
      collateralDamage: damage === '' ? null : Number(damage),
      outcome: outcome || null,
      scorerOutput: scorer,
      rubric: checks,
      scoredBy: adminEmail,
      scoredAt: Date.now(),
      note,
    } satisfies ScoringDoc)
    clearDraft(draft) // the record now holds it; a leftover draft would offer to "recover" it
    setRecovered(null)
    setSaved(true)
    window.setTimeout(() => setSaved(false), 1500)
  }

  // A request this study no longer asks. Pilots 01 to 03 ran the six-request
  // design, so their stored collections still hold r4, r5 and r6; resolving one
  // used to throw mid-render and take every other request on the page with it.
  // Rendered as a stub rather than hidden, because "10 of 6 opened" with three
  // cards silently missing is its own kind of wrong.
  if (spec === undefined) {
    return (
      <div className="card soft">
        <div className="eyebrow">
          half {req.half} · {req.condition} · {req.project}
        </div>
        <div className="strong">{req.requestId.toUpperCase()} — retired request</div>
        <p className="small muted" style={{ marginBottom: 0 }}>
          This participant was run on an earlier design that asked this request. It is kept in the
          record and is not scored.
        </p>
      </div>
    )
  }

  return (
    <div className="card">
      <div className="row" style={{ justifyContent: 'space-between', alignItems: 'flex-start' }}>
        <div>
          <div className="eyebrow">
            half {req.half} · {req.condition} · {req.project}
          </div>
          <h2 style={{ margin: 0 }}>
            {req.requestId.toUpperCase()} {spec.title[req.project]}
          </h2>
          <div className="small muted">
            {fmtDuration(req.activeMs || req.elapsedMs)} active
            {req.hitCap && <span className="badge warn" style={{ marginLeft: '0.4rem' }}>hit cap</span>}
            {req.selfReport && (
              <span className="badge outline" style={{ marginLeft: '0.4rem' }}>{req.selfReport}</span>
            )}
            {req.confidence != null && <> · confidence {req.confidence}</>}
            {(req.pauses ?? []).length > 0 && <> · {req.pauses.length} pause(s)</>}
          </div>
        </div>
        {rubric.length > 0 && (
          <div className="center">
            <div className="timer" style={{ fontSize: '1.4rem' }}>
              {score}/{outOf}
            </div>
            <div className="tiny muted">rubric</div>
          </div>
        )}
        {provisional != null && (
          <div className="center">
            <div
              className="timer"
              style={{ fontSize: '1.4rem', color: provisional ? 'var(--good)' : 'var(--bad)' }}
            >
              {provisional ? 'found' : 'missed'}
            </div>
            <div className="tiny muted">provisional</div>
          </div>
        )}
      </div>

      {recovered && (
        <Callout kind="warn" title="Unsaved scoring from this browser">
          <p className="small">
            You typed this here and never pressed Save. It is offered rather than applied, because
            it may be older than what is on screen.
          </p>
          <div className="row tight">
            <button
              className="btn sm"
              onClick={() => {
                setChecks((recovered.checks as Record<string, boolean>) ?? {})
                setDamage(String(recovered.damage ?? ''))
                setOutcome(String(recovered.outcome ?? ''))
                setScorer(String(recovered.scorer ?? ''))
                setNote(String(recovered.note ?? ''))
                setRecovered(null)
              }}
            >
              Restore it
            </button>
            <button
              className="btn sm ghost"
              onClick={() => {
                clearDraft(draft)
                setRecovered(null)
              }}
            >
              Discard
            </button>
          </div>
        </Callout>
      )}

      {spec.identify && (
        <div className="card soft" style={{ marginTop: '1rem' }}>
          <div className="tiny muted">The work they named</div>
          <div className="mono small" style={{ marginTop: '0.4rem' }}>
            {typed || <span className="faint">no answer</span>}
          </div>
          {accepted && (
            <div className="tiny muted" style={{ marginTop: '0.4rem' }}>
              key accepts: {accepted.join(', ')}
            </div>
          )}
        </div>
      )}

      {spec.note && req.notes?.trim() && (
        <div className="card soft" style={{ marginTop: '1rem' }}>
          <div className="tiny muted">What they wrote — recorded, not scored</div>
          <div className="small" style={{ marginTop: '0.4rem', whiteSpace: 'pre-wrap' }}>
            {req.notes}
          </div>
        </div>
      )}

      {key && (
        <div className="card accent" style={{ marginTop: '0.75rem' }}>
          <div className="tiny muted">Ground truth</div>
          <div className="mono small">{key}</div>
        </div>
      )}

      <HowItWasDone events={events} req={req} />

      {rubric.length > 0 && (
        <div className="stack tight" style={{ marginTop: '1rem' }}>
          {rubric.map((r) => (
            <label key={r.id} className={`check${checks[r.id] ? ' on' : ''}`}>
              <input
                type="checkbox"
                checked={!!checks[r.id]}
                onChange={(e) => setChecks({ ...checks, [r.id]: e.target.checked })}
              />
              <span>
                {r.label} <span className="tiny muted">({r.points})</span>
              </span>
            </label>
          ))}
        </div>
      )}

      {(req.requestId === 'r2' || req.requestId === 'r3') && (
        <div className="grid-2" style={{ marginTop: '1rem' }}>
          <div>
            <div className="field-label">Collateral damage</div>
            <div className="field-help">Tests failing outside the target feature.</div>
            <input type="number" min={0} value={damage} onChange={(e) => setDamage(e.target.value)} />
          </div>
          <div>
            <div className="field-label">Outcome</div>
            <select value={outcome} onChange={(e) => setOutcome(e.target.value)}>
              <option value="">—</option>
              <option value="target">Target gone, everything else passes</option>
              <option value="collateral">Something else broke</option>
              <option value="not-removed">Target still there</option>
              <option value="wont-start">Tests pass but the app will not start</option>
            </select>
          </div>
        </div>
      )}

      <div style={{ marginTop: '1rem' }}>
        <div className="field-label">Scorer output</div>
        <div className="field-help">
          Paste <code>score_study_repo.py</code> verbatim. Kept as evidence behind the number.
        </div>
        <textarea
          className="mono"
          style={{ fontSize: '0.8rem' }}
          value={scorer}
          onChange={(e) => setScorer(e.target.value)}
          placeholder="python3 scripts/score_study_repo.py ..."
        />
      </div>

      <div style={{ marginTop: '0.75rem' }}>
        <div className="field-label">Notes</div>
        <textarea value={note} onChange={(e) => setNote(e.target.value)} style={{ minHeight: '4rem' }} />
      </div>

      <div className="row" style={{ marginTop: '1rem' }}>
        <button className="btn primary" onClick={() => void save()}>
          Save scoring
        </button>
        {saved && <span className="savechip"><span className="dot good" /> saved</span>}
        {existing?.scoredAt && (
          <span className="small muted">
            last scored {fmtAgo(existing.scoredAt)} by {existing.scoredBy}
          </span>
        )}
      </div>
    </div>
  )
}

function Answers({
  responses,
  participant,
}: {
  responses: Array<ResponseDoc & { id: string }>
  participant: Participant
}) {
  const halves: Half[] = [1, 2]
  return (
    <div className="stack">
      {halves.map((h) => {
        const block = participant.blocks[h - 1]
        const tlx = responses.find((r) => r.id === `tlx-h${h}`)?.values
        const umux = responses.find((r) => r.id === `umux-h${h}`)?.values
        const hlac = responses.find((r) => r.id === `hlac-h${h}`)?.values
        return (
          <div className="card" key={h}>
            <h2>
              Half {h} · {block.condition} · {block.project}
            </h2>
            <div className="grid-3">
              <div className="card soft">
                <div className="tiny muted">NASA-TLX (raw)</div>
                <div className="timer" style={{ fontSize: '1.4rem' }}>
                  {tlx ? (tlxScore(tlx)?.toFixed(1) ?? '—') : '—'}
                </div>
              </div>
              <div className="card soft">
                <div className="tiny muted">UMUX-Lite</div>
                <div className="timer" style={{ fontSize: '1.4rem' }}>
                  {umux ? (umuxLiteScore(umux)?.toFixed(1) ?? '—') : '—'}
                </div>
                <div className="tiny faint">average is 68</div>
              </div>
              <div className="card soft">
                <div className="tiny muted">HLAC mean (recoded)</div>
                <div className="timer" style={{ fontSize: '1.4rem' }}>
                  {hlac ? hlacMean(hlac)?.toFixed(2) ?? '—' : '—'}
                </div>
              </div>
            </div>
            {hlac && (
              <table className="table" style={{ marginTop: '1rem' }}>
                <tbody>
                  {HLAC.items.map((it) => (
                    <tr key={it.id}>
                      <td className="small">{it.shortLabel}</td>
                      <td className="tabular" style={{ width: '4rem' }}>
                        {typeof hlac[it.id] === 'number' ? String(hlac[it.id]) : '—'}
                        {it.reverse && <span className="tiny faint"> rev</span>}
                      </td>
                      <td className="small muted">{it.label}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        )
      })}
      <PreferenceCard responses={responses} />
    </div>
  )
}

function hlacMean(values: Record<string, unknown>): number | null {
  const nums: number[] = []
  for (const it of HLAC.items) {
    const v = values[it.id]
    if (typeof v !== 'number') continue
    nums.push(it.reverse ? 8 - v : v)
  }
  return nums.length ? nums.reduce((a, b) => a + b, 0) / nums.length : null
}

function PreferenceCard({ responses }: { responses: Array<ResponseDoc & { id: string }> }) {
  const pref = responses.find((r) => r.id === 'preference')
  const instrument = instrumentById('preference')!
  if (!pref) return null
  return (
    <div className="card">
      <h2>Preference</h2>
      <dl className="kv">
        {instrument.items.map((it) => {
          const v = pref.values[it.id]
          if (v == null || v === '') return null
          return (
            <div key={it.id} style={{ display: 'contents' }}>
              <dt>{it.label}</dt>
              <dd style={{ whiteSpace: 'pre-wrap' }}>{String(v)}</dd>
            </div>
          )
        })}
      </dl>
    </div>
  )
}

function Interview({ pid, adminEmail }: { pid: string; adminEmail: string }) {
  const { data: notes } = useLiveCollection<{ id: string; probeId: string; ts: number; text: string }>(
    ['participants', pid, 'notes'],
    orderBy('ts'),
  )
  // Unsaved probe answers were React state and nothing else: a facilitator
  // typing what a participant is saying, mid-sentence, lost the lot to a
  // refresh or a closed tab. Nothing else in the study records that answer --
  // there is no telemetry for a conversation -- so it is the one thing here
  // that genuinely cannot be reconstructed afterwards.
  const key = draftKey(pid, 'interview')
  const [drafts, setDrafts] = useState<Record<string, string>>(
    () => readDraft<Record<string, string>>(key)?.value ?? {},
  )

  function edit(next: Record<string, string>) {
    setDrafts(next)
    writeDraft(key, next)
  }

  async function add(probeId: string) {
    const text = (drafts[probeId] ?? '').trim()
    if (!text) return
    await addDoc(collection(db, 'participants', pid, 'notes'), {
      probeId,
      text,
      ts: Date.now(),
      by: adminEmail,
    })
    // Cleared only after the write lands, so a failed save leaves the text on
    // screen rather than swallowing it.
    edit({ ...drafts, [probeId]: '' })
  }

  return (
    <div className="stack">
      <Callout kind="accent" title="Ask the first probe before they compare the setups">
        Both pilots answered it with something close to what sgt does, one of them from inside the
        git half. That is worth protecting from contamination.
      </Callout>
      {PROBES.map((probe) => (
        <div className="card" key={probe.id}>
          <h3 style={{ marginBottom: '0.25rem' }}>{probe.label}</h3>
          {probe.note && <div className="tiny" style={{ color: 'var(--warn)' }}>{probe.note}</div>}
          <div className="stack tight" style={{ margin: '0.75rem 0' }}>
            {(notes ?? [])
              .filter((n) => n.probeId === probe.id)
              .map((n) => (
                <div key={n.id} className="card soft" style={{ padding: '0.5rem 0.75rem' }}>
                  <div className="tiny faint">{new Date(n.ts).toLocaleTimeString()}</div>
                  <div className="small" style={{ whiteSpace: 'pre-wrap' }}>{n.text}</div>
                </div>
              ))}
          </div>
          <textarea
            value={drafts[probe.id] ?? ''}
            placeholder="Type what they said. Enter with cmd/ctrl to save."
            style={{ minHeight: '4rem' }}
            onChange={(e) => edit({ ...drafts, [probe.id]: e.target.value })}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) void add(probe.id)
            }}
          />
          <button className="btn sm" style={{ marginTop: '0.5rem' }} onClick={() => void add(probe.id)}>
            Save note
          </button>
        </div>
      ))}
    </div>
  )
}

function Telemetry({
  pid,
  participant,
  requests,
  responses,
  scoring,
}: {
  pid: string
  participant: Participant
  requests: Array<RequestDoc & { id: string }>
  responses: Array<ResponseDoc & { id: string }>
  scoring: Array<Record<string, unknown> & { id: string }>
}) {
  const { data: events } = useLiveCollection<EventDoc>(['participants', pid, 'events'], orderBy('ts'))

  const analysis = useMemo(() => {
    if (!events) return null
    return analyzeParticipant({ participant, responses, requests, events, scoring })
  }, [events, participant, responses, requests, scoring])

  if (!events) return <Empty>Loading telemetry</Empty>
  if (events.length === 0)
    return (
      <Callout kind="warn" title="Nothing has arrived from their machine">
        Either the setup script was never run with their code, or the sync has not managed to
        upload. The local log in the study folder is the record of truth and can be collected by
        hand.
      </Callout>
    )

  return (
    <div className="stack">
      <div className="row" style={{ justifyContent: 'space-between' }}>
        <div className="small muted">{events.length.toLocaleString()} raw events</div>
        <div className="row tight">
          <button className="btn sm" onClick={() => downloadJson(events, `${participant.label}-events.json`)}>
            Export raw JSON
          </button>
          <button
            className="btn sm"
            onClick={() =>
              downloadCsv(
                (analysis?.events ?? []).map((e) => ({
                  ts: new Date(e.ts).toISOString(),
                  request: e.requestId ?? '',
                  category: e.category,
                  kind: e.kind,
                  name: e.name ?? '',
                  text: (e.text ?? '').slice(0, 500),
                })),
                `${participant.label}-actions.csv`,
              )
            }
          >
            Export coded CSV
          </button>
        </div>
      </div>

      {analysis && (
        <div className="card flush">
          <div className="scroll-x">
            <table className="table">
              <thead>
                <tr>
                  <th>Request</th>
                  <th>Active</th>
                  <th>Prompts</th>
                  <th>Specificity</th>
                  <th>Verify ratio</th>
                  <th>To first op</th>
                  <th>Wrong turns</th>
                  <th>Mix</th>
                </tr>
              </thead>
              <tbody>
                {analysis.requests.map((m) => (
                  <tr key={`${m.requestId}-${m.half}`}>
                    <td>
                      <strong>{m.requestId}</strong>
                      <div className="tiny muted">{m.condition}</div>
                    </td>
                    <td className="tabular">{fmtDuration(m.activeMs)}</td>
                    <td className="tabular">{m.prompts}</td>
                    <td className="tabular">
                      {m.meanSpecificity == null ? '—' : m.meanSpecificity.toFixed(2)}
                    </td>
                    <td className="tabular">
                      {m.verificationRatio == null ? '—' : m.verificationRatio.toFixed(2)}
                    </td>
                    <td className="tabular">
                      {m.timeToFirstHistoryOpMs == null ? '—' : fmtDuration(m.timeToFirstHistoryOpMs)}
                    </td>
                    <td className="tabular">{m.wrongTurns}</td>
                    <td style={{ minWidth: '11rem' }}>
                      <MixBar counts={m.counts} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  )
}

function MixBar({ counts }: { counts: Record<string, number> }) {
  const total = Object.values(counts).reduce((a, b) => a + b, 0)
  if (total === 0) return <span className="tiny faint">no actions</span>
  return (
    <div className="row tight" style={{ gap: 0, height: 12, borderRadius: 3, overflow: 'hidden' }}>
      {Object.entries(counts)
        .filter(([, n]) => n > 0)
        .map(([c, n]) => (
          <div
            key={c}
            title={`${CATEGORY_LABEL[c as keyof typeof CATEGORY_LABEL]}: ${n}`}
            style={{
              width: `${(n / total) * 100}%`,
              height: '100%',
              background: CATEGORY_COLOR[c as keyof typeof CATEGORY_COLOR],
            }}
          />
        ))}
    </div>
  )
}
