import { useEffect, useMemo, useState } from 'react'
import { limit, orderBy, query, collection, onSnapshot } from 'firebase/firestore'
import { db } from '../lib/firebase'
import { isPilot, useLiveCollection } from '../lib/db'
import type { DeviceDoc, EventDoc, Participant, RequestDoc } from '../lib/types'
import { STEPS, stepById, stepIndex } from '../study/flow'
import { Callout, fmtAgo, fmtClock, fmtDuration } from '../ui/bits'
import { CATEGORY_COLOR } from '../charts/theme'
import { classify } from '../study/taxonomy'

const LIVE_MS = 120_000
// A request is open and the clock is running, but nothing new has been
// delivered for this long. Generous, because a participant reading code is
// legitimately quiet for a couple of minutes.
const STALE_UPLOAD_MS = 240_000
// How far back a failed command still counts as "happening now".
const FAILURE_WINDOW_MS = 300_000

export function Monitor({ onOpen }: { onOpen: (pid: string) => void }) {
  const { data: participants } = useLiveCollection<Participant & { id: string }>(
    ['participants'],
    orderBy('ordinal'),
  )
  const [now, setNow] = useState(Date.now())
  useEffect(() => {
    const t = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(t)
  }, [])

  const rows = participants ?? []
  const active = rows.filter((p) => (p.lastSeenAt ?? 0) > now - LIVE_MS)
  const started = rows.filter(
    (p) => p.status !== 'created' && !active.some((a) => a.code === p.code),
  )

  return (
    <div className="stack loose">
      <div>
        <h1 style={{ marginBottom: '0.15rem' }}>Live</h1>
        <p className="muted small" style={{ margin: 0 }}>
          {active.length} in session now · {rows.filter((p) => p.status === 'completed').length}{' '}
          finished of {rows.length}
        </p>
      </div>

      {active.length === 0 && (
        <Callout kind="soft" title="Nobody is in a session right now">
          This page updates by itself. Anyone who opens their link appears here within a few
          seconds.
        </Callout>
      )}

      {active.map((p) => (
        <ActiveCard key={p.code} p={p} now={now} onOpen={onOpen} />
      ))}

      {started.length > 0 && (
        <div className="card flush">
          <div className="card-head">
            <h2 style={{ margin: 0, fontSize: '1rem' }}>Started earlier</h2>
          </div>
          <table className="table">
            <tbody>
              {started.map((p) => (
                <tr key={p.code} className="clickable" onClick={() => onOpen(p.code)}>
                  <td>
                    <strong>{p.label}</strong>
                  </td>
                  <td className="small muted">{stepById(p.currentStep)?.title ?? p.currentStep}</td>
                  <td className="small muted">{fmtAgo(p.lastSeenAt)}</td>
                  <td>
                    <span className="badge outline">{p.status}</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

function ActiveCard({
  p,
  now,
  onOpen,
}: {
  p: Participant & { id: string }
  now: number
  onOpen: (pid: string) => void
}) {
  const step = stepById(p.currentStep)
  const progress = (stepIndex(p.currentStep) + 1) / STEPS.length
  const { data: devices } = useLiveCollection<DeviceDoc & { id: string }>([
    'participants',
    p.code,
    'devices',
  ])
  const { data: requests } = useLiveCollection<RequestDoc & { id: string }>([
    'participants',
    p.code,
    'requests',
  ])
  const [tail, setTail] = useState<EventDoc[]>([])

  useEffect(() => {
    const q = query(
      collection(db, 'participants', p.code, 'events'),
      orderBy('ts', 'desc'),
      limit(24),
    )
    return onSnapshot(q, (snap) => setTail(snap.docs.map((d) => d.data() as EventDoc)))
  }, [p.code])

  const openRequest = useMemo(
    () => (requests ?? []).find((r) => r.openedAt && !r.submittedAt),
    [requests],
  )

  const device = (devices ?? []).sort((a, b) => (b.lastSeenAt ?? 0) - (a.lastSeenAt ?? 0))[0]
  const machineLive = device && (device.lastSeenAt ?? 0) > now - LIVE_MS
  // Counts what their machine delivered. Markers the study page writes are not
  // "records received from them" and must not pad this number -- that padding is
  // what made a dead uploader look like a working one.
  const uploaded = (devices ?? []).reduce((n, d) => n + (d.eventsUploaded ?? 0), 0)

  // "Working" means a request is open with the clock running. If someone is
  // mid-request and the delivered count has not moved for minutes, the
  // interesting number is the one standing still.
  const working = !!openRequest && !(openRequest.pauses ?? []).some((x) => x.to == null)
  const lastEventAt = tail.length ? Math.max(...tail.map((e) => e.ts)) : 0
  const stalled = working && lastEventAt > 0 && now - lastEventAt > STALE_UPLOAD_MS

  // Anything the participant ran that came back non-zero, recently enough to
  // still be worth asking about.
  const recentFailures = tail.filter(
    (e) => e.ok === false && e.ts > now - FAILURE_WINDOW_MS,
  )
  const pausedMs = (openRequest?.pauses ?? []).reduce((n, x) => n + ((x.to ?? now) - x.from), 0)
  const remaining = openRequest
    ? openRequest.capMs - (now - (openRequest.openedAt ?? now) - pausedMs)
    : 0
  const isPaused = (openRequest?.pauses ?? []).some((x) => x.to == null)

  return (
    <div className="card">
      <div className="row" style={{ justifyContent: 'space-between', alignItems: 'flex-start' }}>
        <div>
          <div className="row tight">
            <span className="dot live" />
            <h2 style={{ margin: 0 }}>{p.label}</h2>
            <span className="badge outline">group {p.group}</span>
            {isPilot(p) && <span className="badge warn">pilot</span>}
            {p.email && <span className="small muted">{p.email}</span>}
          </div>
          <div className="small muted">
            {step?.phase} · {step?.title}
            {step?.half ? ` · ${p.blocks[step.half - 1].condition}/${p.blocks[step.half - 1].project}` : ''}
          </div>
        </div>
        <div className="row tight">
          {openRequest && openRequest.capMs > 0 && (
            <span
              className={`timer${remaining < 0 ? ' over' : remaining < 60_000 ? ' warn' : ''}`}
              style={{ fontSize: '1.2rem' }}
              title={`${openRequest.requestId} time remaining`}
            >
              {isPaused ? 'paused' : fmtClock(remaining)}
            </span>
          )}
          <button className="btn sm" onClick={() => onOpen(p.code)}>
            Open
          </button>
        </div>
      </div>

      <div className="timer-bar" style={{ margin: '0.75rem 0' }}>
        <i style={{ width: `${progress * 100}%` }} />
      </div>

      <div className="grid-3">
        <Stat
          label="Browser"
          value={fmtAgo(p.lastSeenAt)}
          tone={(p.lastSeenAt ?? 0) > now - LIVE_MS ? 'good' : 'warn'}
        />
        <Stat
          label="Their machine"
          value={device ? fmtAgo(device.lastSeenAt) : 'not reporting'}
          tone={machineLive ? 'good' : 'bad'}
        />
        <Stat
          label="Records received"
          value={uploaded.toLocaleString()}
          tone={working && stalled ? 'bad' : 'none'}
        />
      </div>

      {!machineLive && p.status !== 'created' && stepIndex(p.currentStep) > stepIndex('setup-1') && (
        <Callout kind="bad" title="Nothing is arriving from their machine">
          Ask them to check the session shell is open and to run <code>study-sync</code>. Their local
          log is safe either way, but you are flying blind until it reconnects.
        </Callout>
      )}

      {/* The facilitator's only job during a task block is noticing that
          somebody is stuck. In the pilot a participant lost most of their
          practice window to a crash and nothing on any screen showed it -- the
          facilitator found out because that particular participant chose to
          type it to them, which is not something a study can rely on. The
          recorder already captures exit codes; they just never reached here. */}
      {recentFailures.length > 0 && (
        <Callout kind="bad" title={`${recentFailures.length} command(s) failed in the last few minutes`}>
          <div className="stack tight" style={{ marginTop: '0.35rem' }}>
            {recentFailures.slice(0, 4).map((e) => (
              <div key={e.id} className="mono tiny" style={{ wordBreak: 'break-word' }}>
                {new Date(e.ts).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}{' '}
                {(e.text ?? e.name ?? '').slice(0, 120)}
                {e.exitCode != null && <span className="faint"> → exit {e.exitCode}</span>}
              </div>
            ))}
          </div>
          <div className="small" style={{ marginTop: '0.5rem' }}>
            Failing commands are normal while somebody explores. Several in a row on the same thing
            usually is not — that is the moment to ask how it is going.
          </div>
        </Callout>
      )}

      {/* A live heartbeat is not evidence that anything is being recorded. In
          the pilot the machine kept reporting while 325 events sat undelivered
          on disk, and the only number on screen -- five -- looked like a quiet
          participant rather than a dead uploader. Watch the thing that should
          be growing, not the thing that is merely alive. */}
      {machineLive && working && stalled && (
        <Callout kind="warn" title="They are working, but nothing new has arrived">
          Their machine is still reporting, so this is the upload rather than the connection.
          Nothing is lost — their own copy keeps everything — but you are not seeing what they are
          doing. Ask them to run <code>study-sync</code> in the session shell.
        </Callout>
      )}

      {openRequest && (
        <div className="small muted" style={{ marginTop: '0.75rem' }}>
          On <strong>{openRequest.requestId}</strong> for{' '}
          {fmtDuration(now - (openRequest.openedAt ?? now) - pausedMs)}
          {openRequest.hitCap && <span className="badge warn" style={{ marginLeft: '0.5rem' }}>over cap</span>}
        </div>
      )}

      <EventTail events={tail} />
    </div>
  )
}

function Stat({
  label,
  value,
  tone,
}: {
  label: string
  value: string
  tone: 'good' | 'warn' | 'bad' | 'none'
}) {
  return (
    <div className="card soft" style={{ padding: '0.7rem 0.9rem' }}>
      <div className="tiny muted">{label}</div>
      <div className="row tight">
        {tone !== 'none' && <span className={`dot ${tone}`} />}
        <span className="strong tabular">{value}</span>
      </div>
    </div>
  )
}

/**
 * Two things write to this stream and they mean opposite things.
 *
 * The study page writes a marker every time the participant opens or closes a
 * request. Their machine writes what they actually run. In the pilot the
 * facilitator watched markers arrive with fresh timestamps, concluded the
 * machine was fine, and decided the red "nothing is arriving from their
 * machine" warning was a false alarm. It was not: the machine had genuinely
 * gone silent and every arriving event was the browser. They said they would
 * have dismissed it.
 *
 * A correct alarm that another part of the same screen quietly contradicts is
 * worse than no alarm, so the two origins are now labelled and counted apart.
 */
function EventTail({ events }: { events: EventDoc[] }) {
  if (events.length === 0) return null
  const fromMachine = events.filter((e) => e.deviceId && e.deviceId !== 'web').length
  const fromPage = events.length - fromMachine
  return (
    <div style={{ marginTop: '1rem' }}>
      <div className="tiny muted" style={{ marginBottom: '0.35rem' }}>
        Latest activity — {fromMachine} from their machine, {fromPage} from the study page
        {fromMachine === 0 && (
          <span style={{ color: 'var(--bad)' }}>
            {' '}· nothing here is their actual work
          </span>
        )}
      </div>
      <div className="stack" style={{ gap: '0.15rem', maxHeight: '13rem', overflowY: 'auto' }}>
        {events.map((e) => {
          const cat = classify(e, { dirtySinceCheck: false, lastOpFailed: false })
          const fromPage = !e.deviceId || e.deviceId === 'web'
          return (
            <div
              key={e.id}
              className="row tight tiny"
              style={{ flexWrap: 'nowrap', opacity: fromPage ? 0.55 : 1 }}
              title={fromPage ? 'written by the study page, not their machine' : 'from their machine'}
            >
              <span className="tiny faint nowrap" style={{ width: '3.4rem', flex: 'none' }}>
                {fromPage ? 'page' : 'machine'}
              </span>
              <span
                style={{
                  width: 8,
                  height: 8,
                  borderRadius: 2,
                  flex: 'none',
                  background: cat ? CATEGORY_COLOR[cat] : 'var(--line)',
                }}
              />
              <span className="faint tabular nowrap">
                {new Date(e.ts).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })}
              </span>
              <span className="mono nowrap" style={{ color: 'var(--muted)' }}>
                {e.kind === 'prompt' ? 'prompt' : (e.name ?? e.kind)}
              </span>
              <span
                className="mono"
                style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
              >
                {(e.text ?? '').slice(0, 140)}
              </span>
              {e.ok === false && <span className="badge bad">failed</span>}
            </div>
          )
        })}
      </div>
    </div>
  )
}
