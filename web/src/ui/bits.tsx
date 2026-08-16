import { useEffect, useRef, useState, type ReactNode } from 'react'
import type { SaveState } from '../lib/db'
import { PHASES, STEPS, stepIndex } from '../study/flow'

export function SaveChip({ state }: { state: SaveState }) {
  if (state === 'idle') return null
  if (state === 'saving')
    return (
      <span className="savechip">
        <span className="spin" /> saving
      </span>
    )
  if (state === 'saved')
    return (
      <span className="savechip">
        <span className="dot good" /> saved
      </span>
    )
  return (
    <span className="savechip" style={{ color: 'var(--bad)' }}>
      <span className="dot bad" /> not saved yet, still trying
    </span>
  )
}

export function fmtClock(ms: number): string {
  const neg = ms < 0
  const t = Math.abs(Math.floor(ms / 1000))
  const m = Math.floor(t / 60)
  const s = t % 60
  return `${neg ? '−' : ''}${m}:${String(s).padStart(2, '0')}`
}

export function fmtDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`
  const s = Math.round(ms / 1000)
  if (s < 60) return `${s}s`
  const m = Math.floor(s / 60)
  return `${m}m ${String(s % 60).padStart(2, '0')}s`
}

export function fmtAgo(ts: number | null | undefined): string {
  if (!ts) return 'never'
  const d = Date.now() - ts
  if (d < 15_000) return 'just now'
  if (d < 60_000) return `${Math.round(d / 1000)}s ago`
  if (d < 3_600_000) return `${Math.round(d / 60_000)}m ago`
  if (d < 86_400_000) return `${Math.round(d / 3_600_000)}h ago`
  return new Date(ts).toLocaleDateString()
}

/**
 * Remaining time on a request, ticking once a second.
 *
 * The countdown is visible to the participant on purpose. The caps come from
 * the design's task budget, and a hidden cap turns "ran out of time" into "gave
 * up", which are different data and get analyzed differently.
 */
export function useCountdown(opts: {
  openedAt: number | null
  capMs: number
  pausedMs: number
  running: boolean
}) {
  const { openedAt, capMs, pausedMs, running } = opts
  const [now, setNow] = useState(Date.now())
  useEffect(() => {
    if (!running || !openedAt) return
    const t = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(t)
  }, [running, openedAt])
  const elapsed = openedAt ? Math.max(0, now - openedAt - pausedMs) : 0
  const remaining = capMs - elapsed
  return { elapsed, remaining, expired: capMs > 0 && remaining <= 0 }
}

export function Countdown({
  remaining,
  capMs,
}: {
  remaining: number
  capMs: number
}) {
  const frac = capMs > 0 ? Math.max(0, Math.min(1, remaining / capMs)) : 1
  const level = remaining <= 0 ? 'over' : frac < 0.2 ? 'warn' : ''
  return (
    <div style={{ minWidth: '7rem' }}>
      <div className={`timer ${level}`}>{fmtClock(remaining)}</div>
      <div className={`timer-bar ${level}`}>
        <i style={{ width: `${frac * 100}%` }} />
      </div>
    </div>
  )
}

export function Rail({ currentStep }: { currentStep: string }) {
  const currentIdx = stepIndex(currentStep)
  return (
    <nav className="rail" aria-label="Progress">
      {PHASES.map((phase) => (
        <div key={phase}>
          <div className="rail-phase-title">{phase}</div>
          {STEPS.filter((s) => s.phase === phase).map((s) => {
            const idx = stepIndex(s.id)
            const cls = idx < currentIdx ? 'done' : idx === currentIdx ? 'now' : ''
            return (
              <div key={s.id} className={`rail-step ${cls}`}>
                <span className="pip" />
                <span>{s.title}</span>
              </div>
            )
          })}
        </div>
      ))}
    </nav>
  )
}

export function Copyable({ text, label }: { text: string; label?: string }) {
  const [done, setDone] = useState(false)
  const timer = useRef<number | null>(null)
  useEffect(() => () => { if (timer.current) window.clearTimeout(timer.current) }, [])
  return (
    <div className="copyline">
      <span title={text}>{label ?? text}</span>
      <button
        className="btn sm"
        onClick={async () => {
          try {
            await navigator.clipboard.writeText(text)
          } catch {
            // Clipboard is blocked in some embedded browsers; select-and-copy
            // still works, so say nothing and let the label do the work.
          }
          setDone(true)
          timer.current = window.setTimeout(() => setDone(false), 1600)
        }}
      >
        {done ? 'Copied' : 'Copy'}
      </button>
    </div>
  )
}

export function Callout({
  kind = 'accent',
  title,
  children,
}: {
  kind?: 'accent' | 'warn' | 'bad' | 'soft'
  title?: string
  children: ReactNode
}) {
  return (
    <div className={`card ${kind}`} style={{ padding: '1rem 1.15rem' }}>
      {title && <div className="strong" style={{ marginBottom: '0.3rem' }}>{title}</div>}
      <div className="small" style={{ color: 'var(--ink-2)' }}>
        {children}
      </div>
    </div>
  )
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>
}

export function Spinner({ label }: { label?: string }) {
  return (
    <div className="row" style={{ padding: '2rem', justifyContent: 'center' }}>
      <span className="spin" />
      <span className="muted small">{label ?? 'Loading'}</span>
    </div>
  )
}

export function Tabs<T extends string>({
  tabs,
  value,
  onChange,
}: {
  tabs: Array<{ id: T; label: string; badge?: ReactNode }>
  value: T
  onChange: (t: T) => void
}) {
  return (
    <div className="tabs" role="tablist">
      {tabs.map((t) => (
        <button
          key={t.id}
          role="tab"
          aria-selected={value === t.id}
          className={`tab${value === t.id ? ' on' : ''}`}
          onClick={() => onChange(t.id)}
        >
          {t.label}
          {t.badge != null && <> {t.badge}</>}
        </button>
      ))}
    </div>
  )
}
